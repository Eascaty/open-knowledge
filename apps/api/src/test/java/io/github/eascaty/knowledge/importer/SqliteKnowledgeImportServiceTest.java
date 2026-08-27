package io.github.eascaty.knowledge.importer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class SqliteKnowledgeImportServiceTest {

    @TempDir
    private Path tempDir;

    private Path projectRoot;
    private Path database;
    private Path rawRoot;
    private SqliteKnowledgeImportService service;

    @BeforeEach
    void setUp() throws Exception {
        projectRoot = tempDir.resolve("knowledge");
        database = projectRoot.resolve("workspace/data/state/knowledge.sqlite3");
        rawRoot = projectRoot.resolve("workspace/data/raw");
        Files.createDirectories(database.getParent());
        createDatabase(false);
        service = new SqliteKnowledgeImportService(
                projectRoot, database, rawRoot, 1, 3);
    }

    @Test
    void importsImmutableRawSourceJobAndEventInOneTransaction() throws Exception {
        Path input = tempDir.resolve("并发 编程:笔记.md");
        Files.writeString(input, "# 并发编程\n\n线程池与锁。", StandardCharsets.UTF_8);

        KnowledgeImportResult result = service.importFile(input);

        assertThat(result.duplicate()).isFalse();
        assertThat(result.sourceId()).isEqualTo("sha256-" + result.sha256());
        assertThat(result.sha256()).hasSize(64);
        assertThat(result.rawPath()).startsWith("workspace/data/raw/")
                .endsWith("/并发 编程-笔记.md");
        assertThat(Files.readString(projectRoot.resolve(result.rawPath())))
                .isEqualTo("# 并发编程\n\n线程池与锁。");
        assertThat(Files.readString(input)).isEqualTo("# 并发编程\n\n线程池与锁。");

        try (Connection connection = connect(); Statement statement = connection.createStatement()) {
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM sources")).isEqualTo(1);
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM jobs")).isEqualTo(1);
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM events")).isEqualTo(1);
            try (ResultSet job = statement.executeQuery(
                    "SELECT stage, status, attempts, max_attempts FROM jobs")) {
                assertThat(job.next()).isTrue();
                assertThat(job.getString("stage")).isEqualTo("extract");
                assertThat(job.getString("status")).isEqualTo("queued");
                assertThat(job.getInt("attempts")).isZero();
                assertThat(job.getInt("max_attempts")).isEqualTo(3);
            }
        }
    }

    @Test
    void importsIntoSchemaV2WithoutChangingSchemaVersion() throws Exception {
        try (Connection connection = connect(); Statement statement = connection.createStatement()) {
            statement.executeUpdate("UPDATE metadata SET value='2' WHERE key='schema_version'");
        }
        Path input = tempDir.resolve("schema-v2.md");
        Files.writeString(input, "schema v2", StandardCharsets.UTF_8);

        KnowledgeImportResult result = service.importFile(input);

        assertThat(result.duplicate()).isFalse();
        try (Connection connection = connect(); Statement statement = connection.createStatement()) {
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM sources")).isEqualTo(1);
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM jobs")).isEqualTo(1);
            try (ResultSet schema = statement.executeQuery(
                    "SELECT value FROM metadata WHERE key='schema_version'")) {
                assertThat(schema.next()).isTrue();
                assertThat(schema.getInt(1)).isEqualTo(2);
            }
        }
    }

    @Test
    void repeatedContentIsIdempotentAndRepairsMissingRawWithoutNewRows() throws Exception {
        Path firstInput = tempDir.resolve("first.md");
        Path renamedInput = tempDir.resolve("renamed.md");
        Files.writeString(firstInput, "相同内容", StandardCharsets.UTF_8);
        Files.writeString(renamedInput, "相同内容", StandardCharsets.UTF_8);

        KnowledgeImportResult first = service.importFile(firstInput);
        KnowledgeImportResult duplicate = service.importFile(renamedInput);
        Files.delete(projectRoot.resolve(first.rawPath()));
        KnowledgeImportResult repaired = service.importFile(renamedInput);

        assertThat(duplicate.duplicate()).isTrue();
        assertThat(repaired.duplicate()).isTrue();
        assertThat(duplicate.sourceId()).isEqualTo(first.sourceId());
        assertThat(repaired.rawPath()).isEqualTo(first.rawPath());
        assertThat(Files.readString(projectRoot.resolve(first.rawPath())))
                .isEqualTo("相同内容");
        assertThat(Files.exists(rawRoot.resolve(first.sha256().substring(0, 2))
                .resolve(first.sha256()).resolve("renamed.md"))).isFalse();

        try (Connection connection = connect(); Statement statement = connection.createStatement()) {
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM sources")).isEqualTo(1);
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM jobs")).isEqualTo(1);
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM events")).isEqualTo(1);
        }
    }

    @Test
    void rejectsSymlinksAndOversizedFilesWithoutWritingState() throws Exception {
        Path input = tempDir.resolve("source.md");
        Files.writeString(input, "安全内容", StandardCharsets.UTF_8);
        Path link = tempDir.resolve("linked.md");
        Files.createSymbolicLink(link, input);
        Path oversized = tempDir.resolve("oversized.bin");
        Files.write(oversized, new byte[1024 * 1024 + 1]);

        assertThatThrownBy(() -> service.importFile(link))
                .isInstanceOf(KnowledgeImportException.class)
                .hasMessageContaining("符号链接");
        assertThatThrownBy(() -> service.importFile(oversized))
                .isInstanceOf(KnowledgeImportException.class)
                .hasMessageContaining("大小上限");

        try (Connection connection = connect(); Statement statement = connection.createStatement()) {
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM sources")).isZero();
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM jobs")).isZero();
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM events")).isZero();
        }
        assertThat(Files.exists(rawRoot)).isFalse();
    }

    @Test
    void rollsBackDatabaseAndDeletesNewRawWhenInitialJobFails() throws Exception {
        Files.delete(database);
        createDatabase(true);
        Path input = tempDir.resolve("rollback.md");
        Files.writeString(input, "事务回滚", StandardCharsets.UTF_8);

        assertThatThrownBy(() -> service.importFile(input))
                .isInstanceOf(KnowledgeImportException.class)
                .hasMessageContaining("事务失败");

        try (Connection connection = connect(); Statement statement = connection.createStatement()) {
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM sources")).isZero();
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM jobs")).isZero();
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM events")).isZero();
        }
        if (Files.exists(rawRoot)) {
            try (var files = Files.walk(rawRoot)) {
                assertThat(files.filter(Files::isRegularFile)).isEmpty();
            }
        }
    }

    @Test
    void refusesTamperedExistingRawAndStoredPathsOutsideRawBoundary() throws Exception {
        Path input = tempDir.resolve("source.md");
        Files.writeString(input, "不可变内容", StandardCharsets.UTF_8);
        KnowledgeImportResult first = service.importFile(input);
        Files.writeString(projectRoot.resolve(first.rawPath()), "被篡改", StandardCharsets.UTF_8);

        assertThatThrownBy(() -> service.importFile(input))
                .isInstanceOf(KnowledgeImportException.class)
                .hasMessageContaining("哈希不一致");

        Files.writeString(projectRoot.resolve(first.rawPath()), "不可变内容", StandardCharsets.UTF_8);
        try (Connection connection = connect(); Statement statement = connection.createStatement()) {
            statement.executeUpdate("UPDATE sources SET raw_path='../../outside.md'");
        }
        assertThatThrownBy(() -> service.importFile(input))
                .isInstanceOf(KnowledgeImportException.class)
                .hasMessageContaining("项目边界");
    }

    @Test
    void validatesConfigurationAndRejectsUnsupportedSchemaWithoutImplicitMigration()
            throws Exception {
        assertThatThrownBy(() -> new SqliteKnowledgeImportService(
                projectRoot, tempDir.resolve("outside.sqlite3"), rawRoot, 1, 3))
                .isInstanceOf(KnowledgeImportException.class)
                .hasMessageContaining("数据库");
        assertThatThrownBy(() -> new SqliteKnowledgeImportService(
                projectRoot, database, tempDir.resolve("outside"), 1, 3))
                .isInstanceOf(KnowledgeImportException.class)
                .hasMessageContaining("raw 目录");
        assertThatThrownBy(() -> new SqliteKnowledgeImportService(
                projectRoot, database, rawRoot, 0, 3))
                .isInstanceOf(KnowledgeImportException.class)
                .hasMessageContaining("大小上限");
        assertThatThrownBy(() -> new SqliteKnowledgeImportService(
                projectRoot, database, rawRoot, 1, 0))
                .isInstanceOf(KnowledgeImportException.class)
                .hasMessageContaining("尝试次数");

        try (Connection connection = connect(); Statement statement = connection.createStatement()) {
            statement.executeUpdate("UPDATE metadata SET value='3' WHERE key='schema_version'");
        }
        Path input = tempDir.resolve("schema.md");
        Files.writeString(input, "schema", StandardCharsets.UTF_8);
        assertThatThrownBy(() -> service.importFile(input))
                .isInstanceOf(KnowledgeImportException.class)
                .hasMessageContaining("schema v1 和 v2");
        assertThat(Files.exists(rawRoot)).isFalse();
    }

    @Test
    void rejectsMissingAndNonNumericSchemaVersionsWithoutWritingRaw() throws Exception {
        Path input = tempDir.resolve("invalid-schema.md");
        Files.writeString(input, "schema", StandardCharsets.UTF_8);
        try (Connection connection = connect(); Statement statement = connection.createStatement()) {
            statement.executeUpdate("DELETE FROM metadata WHERE key='schema_version'");
        }

        assertThatThrownBy(() -> service.importFile(input))
                .isInstanceOf(KnowledgeImportException.class)
                .hasMessageContaining("schema v1 和 v2");

        try (Connection connection = connect(); Statement statement = connection.createStatement()) {
            statement.executeUpdate("INSERT INTO metadata VALUES ('schema_version', 'invalid')");
        }
        assertThatThrownBy(() -> service.importFile(input))
                .isInstanceOf(KnowledgeImportException.class)
                .hasMessageContaining("schema v1 和 v2");
        assertThat(Files.exists(rawRoot)).isFalse();
    }

    @Test
    void rejectsRawDirectorySymlinkBeforeWritingOutsideProject() throws Exception {
        Path external = tempDir.resolve("external-raw");
        Files.createDirectories(external);
        Files.createDirectories(rawRoot.getParent());
        Files.createSymbolicLink(rawRoot, external);
        Path input = tempDir.resolve("symlink-raw.md");
        Files.writeString(input, "不能越界", StandardCharsets.UTF_8);

        assertThatThrownBy(() -> service.importFile(input))
                .isInstanceOf(KnowledgeImportException.class)
                .hasMessageContaining("符号链接");
        try (var files = Files.list(external)) {
            assertThat(files).isEmpty();
        }
    }

    @Test
    void refusesImportWhileAnotherWriterOwnsTheProjectLock() throws Exception {
        Path input = tempDir.resolve("locked.md");
        Files.writeString(input, "并发写入", StandardCharsets.UTF_8);
        Path lockPath = projectRoot.resolve(
                "workspace/data/state/knowledge-os.lock");
        Files.createDirectories(lockPath.getParent());

        try (FileChannel channel = FileChannel.open(
                     lockPath,
                     StandardOpenOption.CREATE,
                     StandardOpenOption.READ,
                     StandardOpenOption.WRITE);
             FileLock ignored = channel.lock()) {
            assertThatThrownBy(() -> service.importFile(input))
                    .isInstanceOf(KnowledgeImportException.class)
                    .hasMessageContaining("占用");
        }

        try (Connection connection = connect(); Statement statement = connection.createStatement()) {
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM sources")).isZero();
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM jobs")).isZero();
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM events")).isZero();
        }
    }

    @Test
    void sharesTheProjectLockWithThePythonPipeline() throws Exception {
        Path input = tempDir.resolve("python-locked.md");
        Files.writeString(input, "跨运行时互斥", StandardCharsets.UTF_8);
        Path pipelineSource = Path.of("../pipeline/src").toAbsolutePath().normalize();
        String python = """
                import sys, time
                from pathlib import Path
                from knowledge_os.operations.lock import ProjectLock
                with ProjectLock(Path(sys.argv[1]), timeout=0, purpose='cross-runtime-test'):
                    print('LOCKED', flush=True)
                    time.sleep(10)
                """;
        ProcessBuilder processBuilder = new ProcessBuilder(
                "python3", "-c", python, projectRoot.toString())
                .redirectErrorStream(true);
        processBuilder.environment().put("PYTHONPATH", pipelineSource.toString());
        Process process = processBuilder.start();
        try (BufferedReader output = new BufferedReader(
                new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8))) {
            assertThat(output.readLine()).isEqualTo("LOCKED");
            assertThatThrownBy(() -> service.importFile(input))
                    .isInstanceOf(KnowledgeImportException.class)
                    .hasMessageContaining("占用");
        } finally {
            process.destroyForcibly();
            process.waitFor();
        }

        try (Connection connection = connect(); Statement statement = connection.createStatement()) {
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM sources")).isZero();
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM jobs")).isZero();
            assertThat(singleLong(statement, "SELECT COUNT(*) FROM events")).isZero();
        }
    }

    private Connection connect() throws Exception {
        Connection connection = DriverManager.getConnection("jdbc:sqlite:" + database);
        connection.createStatement().execute("PRAGMA foreign_keys = ON");
        return connection;
    }

    private long singleLong(Statement statement, String sql) throws Exception {
        try (ResultSet result = statement.executeQuery(sql)) {
            assertThat(result.next()).isTrue();
            return result.getLong(1);
        }
    }

    private void createDatabase(boolean rejectJobs) throws Exception {
        try (Connection connection = connect(); Statement statement = connection.createStatement()) {
            statement.executeUpdate(
                    "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)");
            statement.executeUpdate("INSERT INTO metadata VALUES ('schema_version', '1')");
            statement.executeUpdate("""
                    CREATE TABLE sources (
                        id TEXT PRIMARY KEY, kind TEXT NOT NULL, origin TEXT NOT NULL,
                        original_name TEXT NOT NULL, raw_path TEXT NOT NULL,
                        sha256 TEXT NOT NULL UNIQUE, mime_type TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL, imported_at TEXT NOT NULL,
                        status TEXT NOT NULL, last_error TEXT)
                    """);
            statement.executeUpdate("""
                    CREATE TABLE jobs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                        stage TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL,
                        max_attempts INTEGER NOT NULL, available_at TEXT NOT NULL,
                        last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                        CHECK (%s), UNIQUE(source_id, stage))
                    """.formatted(rejectJobs ? "stage <> 'extract'" : "1"));
            statement.executeUpdate("""
                    CREATE TABLE events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, happened_at TEXT NOT NULL,
                        event_type TEXT NOT NULL, source_id TEXT, details_json TEXT NOT NULL)
                    """);
        }
    }
}
