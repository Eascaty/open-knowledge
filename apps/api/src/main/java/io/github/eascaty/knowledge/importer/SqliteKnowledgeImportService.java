package io.github.eascaty.knowledge.importer;

import java.io.IOException;
import java.io.InputStream;
import java.nio.channels.FileChannel;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.HexFormat;
import java.util.Properties;
import java.util.Set;
import java.util.UUID;

public class SqliteKnowledgeImportService {

    private static final int COPY_BUFFER_BYTES = 1024 * 1024;
    private static final Set<Integer> SUPPORTED_SCHEMA_VERSIONS = Set.of(1, 2);

    private final Path projectRoot;
    private final Path databasePath;
    private final Path rawRoot;
    private final long maximumFileBytes;
    private final int maximumAttempts;

    public SqliteKnowledgeImportService(
            Path projectRoot,
            Path databasePath,
            Path rawRoot,
            int maximumFileMegabytes,
            int maximumAttempts) {
        this.projectRoot = absolute(projectRoot);
        this.databasePath = absolute(databasePath);
        this.rawRoot = absolute(rawRoot);
        if (!this.databasePath.startsWith(this.projectRoot)) {
            throw new KnowledgeImportException("数据库必须位于项目目录内");
        }
        if (!this.rawRoot.startsWith(this.projectRoot)) {
            throw new KnowledgeImportException("raw 目录必须位于项目目录内");
        }
        if (maximumFileMegabytes < 1) {
            throw new KnowledgeImportException("文件大小上限必须大于 0 MiB");
        }
        if (maximumAttempts < 1) {
            throw new KnowledgeImportException("最大尝试次数必须大于 0");
        }
        try {
            this.maximumFileBytes = Math.multiplyExact(
                    (long) maximumFileMegabytes, 1024L * 1024L);
        } catch (ArithmeticException exception) {
            throw new KnowledgeImportException("文件大小上限超出范围", exception);
        }
        this.maximumAttempts = maximumAttempts;
    }

    public KnowledgeImportResult importFile(Path source) {
        try (ProjectWriteLock ignored = ProjectWriteLock.acquire(projectRoot)) {
            return importFileLocked(source);
        }
    }

    private KnowledgeImportResult importFileLocked(Path source) {
        Path input = validateSource(source);
        long size = fileSize(input);
        if (size > maximumFileBytes) {
            throw new KnowledgeImportException("文件超过配置的大小上限: " + input);
        }
        String digest = sha256(input);
        String sourceId = "sha256-" + digest;
        String safeName = safeFilename(input.getFileName().toString());
        Path candidateRaw = rawRoot.resolve(digest.substring(0, 2))
                .resolve(digest)
                .resolve(safeName)
                .normalize();
        boolean candidateCreated = false;
        boolean committed = false;

        try (Connection connection = openWritable()) {
            verifySchema(connection);
            connection.setAutoCommit(false);
            try {
                ExistingSource existing = findByHash(connection, digest);
                if (existing != null) {
                    Path existingRaw = resolveRaw(existing.rawPath());
                    copyImmutable(input, existingRaw, digest);
                    connection.commit();
                    committed = true;
                    cleanupUnreferencedCandidate(candidateRaw, existingRaw, candidateCreated);
                    return new KnowledgeImportResult(
                            existing.id(), digest, existing.originalName(),
                            existing.rawPath(), true);
                }

                candidateCreated = copyImmutable(input, candidateRaw, digest);
                String rawPath = projectRoot.relativize(candidateRaw).toString()
                        .replace(candidateRaw.getFileSystem().getSeparator(), "/");
                String now = Instant.now().truncatedTo(ChronoUnit.SECONDS).toString();
                insertSource(connection, sourceId, input, rawPath, digest, size, now);
                insertInitialJob(connection, sourceId, now);
                insertEvent(connection, sourceId, digest, now);
                connection.commit();
                committed = true;
                return new KnowledgeImportResult(
                        sourceId, digest, input.getFileName().toString(), rawPath, false);
            } catch (Exception exception) {
                rollback(connection, exception);
                cleanupAfterRollback(candidateRaw, candidateCreated);
                if (exception instanceof KnowledgeImportException importException) {
                    throw importException;
                }
                throw new KnowledgeImportException("Java 导入事务失败", exception);
            }
        } catch (SQLException exception) {
            if (!committed) {
                cleanupAfterRollback(candidateRaw, candidateCreated);
            }
            throw new KnowledgeImportException("无法写入知识库 SQLite", exception);
        }
    }

    private Path validateSource(Path source) {
        if (source == null) {
            throw new KnowledgeImportException("必须提供待导入文件");
        }
        Path unresolved = source.toAbsolutePath().normalize();
        if (Files.isSymbolicLink(unresolved)) {
            throw new KnowledgeImportException("不接受符号链接输入: " + unresolved);
        }
        if (!Files.isRegularFile(unresolved, LinkOption.NOFOLLOW_LINKS)) {
            throw new KnowledgeImportException("不是普通文件: " + unresolved);
        }
        return unresolved;
    }

    private long fileSize(Path input) {
        try {
            return Files.size(input);
        } catch (IOException exception) {
            throw new KnowledgeImportException("无法读取文件大小: " + input, exception);
        }
    }

    private Connection openWritable() throws SQLException {
        if (!Files.isRegularFile(databasePath)) {
            throw new SQLException("knowledge database does not exist: " + databasePath);
        }
        Properties properties = new Properties();
        properties.setProperty("foreign_keys", "true");
        properties.setProperty("busy_timeout", "30000");
        properties.setProperty("transaction_mode", "IMMEDIATE");
        return DriverManager.getConnection("jdbc:sqlite:" + databasePath, properties);
    }

    private void verifySchema(Connection connection) throws SQLException {
        try (PreparedStatement statement = connection.prepareStatement(
                "SELECT value FROM metadata WHERE key='schema_version'");
             ResultSet result = statement.executeQuery()) {
            Integer schemaVersion = null;
            if (result.next()) {
                try {
                    schemaVersion = Integer.valueOf(result.getString(1));
                } catch (NumberFormatException ignored) {
                    // Fall through to the same stable rejection used for unknown versions.
                }
            }
            if (schemaVersion == null || !SUPPORTED_SCHEMA_VERSIONS.contains(schemaVersion)) {
                throw new KnowledgeImportException(
                        "仅支持 SQLite schema v1 和 v2，拒绝隐式迁移");
            }
        }
    }

    private ExistingSource findByHash(Connection connection, String digest) throws SQLException {
        try (PreparedStatement statement = connection.prepareStatement("""
                SELECT id, original_name, raw_path FROM sources WHERE sha256 = ?
                """)) {
            statement.setString(1, digest);
            try (ResultSet result = statement.executeQuery()) {
                if (!result.next()) {
                    return null;
                }
                return new ExistingSource(
                        result.getString("id"),
                        result.getString("original_name"),
                        result.getString("raw_path"));
            }
        }
    }

    private void insertSource(
            Connection connection,
            String sourceId,
            Path input,
            String rawPath,
            String digest,
            long size,
            String now) throws SQLException {
        try (PreparedStatement statement = connection.prepareStatement("""
                INSERT INTO sources(
                    id, kind, origin, original_name, raw_path, sha256, mime_type,
                    size_bytes, imported_at, status, last_error)
                VALUES (?, 'file', ?, ?, ?, ?, ?, ?, ?, 'queued', NULL)
                """)) {
            statement.setString(1, sourceId);
            statement.setString(2, input.toString());
            statement.setString(3, input.getFileName().toString());
            statement.setString(4, rawPath);
            statement.setString(5, digest);
            statement.setString(6, mimeType(input));
            statement.setLong(7, size);
            statement.setString(8, now);
            statement.executeUpdate();
        }
    }

    private void insertInitialJob(Connection connection, String sourceId, String now)
            throws SQLException {
        try (PreparedStatement statement = connection.prepareStatement("""
                INSERT INTO jobs(
                    source_id, stage, status, attempts, max_attempts,
                    available_at, last_error, created_at, updated_at)
                VALUES (?, 'extract', 'queued', 0, ?, ?, NULL, ?, ?)
                """)) {
            statement.setString(1, sourceId);
            statement.setInt(2, maximumAttempts);
            statement.setString(3, now);
            statement.setString(4, now);
            statement.setString(5, now);
            statement.executeUpdate();
        }
    }

    private void insertEvent(
            Connection connection, String sourceId, String digest, String now)
            throws SQLException {
        try (PreparedStatement statement = connection.prepareStatement("""
                INSERT INTO events(happened_at, event_type, source_id, details_json)
                VALUES (?, 'source_ingested', ?, ?)
                """)) {
            statement.setString(1, now);
            statement.setString(2, sourceId);
            statement.setString(3, "{\"sha256\":\"" + digest + "\"}");
            statement.executeUpdate();
        }
    }

    private boolean copyImmutable(Path source, Path target, String expectedHash) {
        ensureInsideRawRoot(target);
        try {
            Files.createDirectories(target.getParent());
            if (Files.exists(target, LinkOption.NOFOLLOW_LINKS)) {
                verifyExistingRaw(target, expectedHash);
                return false;
            }
            Path temporary = target.getParent().resolve(
                    ".incoming-" + UUID.randomUUID() + ".tmp");
            try {
                try (InputStream input = Files.newInputStream(source);
                     FileChannel output = FileChannel.open(
                             temporary, StandardOpenOption.CREATE_NEW,
                             StandardOpenOption.WRITE)) {
                    byte[] buffer = new byte[COPY_BUFFER_BYTES];
                    int count;
                    while ((count = input.read(buffer)) >= 0) {
                        if (count > 0) {
                            java.nio.ByteBuffer bytes = java.nio.ByteBuffer.wrap(buffer, 0, count);
                            while (bytes.hasRemaining()) {
                                output.write(bytes);
                            }
                        }
                    }
                    output.force(true);
                }
                if (!expectedHash.equals(sha256(temporary))) {
                    throw new KnowledgeImportException("复制期间源文件发生变化: " + source);
                }
                try {
                    Files.createLink(target, temporary);
                    return true;
                } catch (FileAlreadyExistsException exception) {
                    verifyExistingRaw(target, expectedHash);
                    return false;
                }
            } finally {
                Files.deleteIfExists(temporary);
            }
        } catch (IOException exception) {
            throw new KnowledgeImportException("无法写入不可变 raw: " + target, exception);
        }
    }

    private void verifyExistingRaw(Path target, String expectedHash) {
        if (Files.isSymbolicLink(target) || !Files.isRegularFile(target, LinkOption.NOFOLLOW_LINKS)) {
            throw new KnowledgeImportException("raw 目标不是普通文件: " + target);
        }
        if (!expectedHash.equals(sha256(target))) {
            throw new KnowledgeImportException("不可变 raw 内容与哈希不一致: " + target);
        }
    }

    private Path resolveRaw(String storedPath) {
        Path resolved = projectRoot.resolve(storedPath).normalize();
        ensureInsideRawRoot(resolved);
        return resolved;
    }

    private void ensureInsideRawRoot(Path target) {
        if (!absolute(target).startsWith(rawRoot)) {
            throw new KnowledgeImportException("raw 路径越过项目边界: " + target);
        }
        Path current = projectRoot;
        Path relative = projectRoot.relativize(absolute(target));
        for (Path part : relative) {
            current = current.resolve(part);
            if (Files.exists(current, LinkOption.NOFOLLOW_LINKS)
                    && Files.isSymbolicLink(current)) {
                throw new KnowledgeImportException("raw 路径包含符号链接: " + current);
            }
        }
    }

    private void cleanupUnreferencedCandidate(
            Path candidate, Path existingRaw, boolean candidateCreated) {
        if (candidateCreated && !candidate.equals(existingRaw)) {
            deleteCandidate(candidate);
        }
    }

    private void cleanupAfterRollback(Path candidate, boolean candidateCreated) {
        if (candidateCreated) {
            deleteCandidate(candidate);
        }
    }

    private void deleteCandidate(Path candidate) {
        try {
            Files.deleteIfExists(candidate);
        } catch (IOException exception) {
            throw new KnowledgeImportException("回滚后无法清理孤立 raw: " + candidate, exception);
        }
    }

    private void rollback(Connection connection, Exception original) {
        try {
            connection.rollback();
        } catch (SQLException rollbackFailure) {
            original.addSuppressed(rollbackFailure);
        }
    }

    private String sha256(Path path) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            try (InputStream input = new DigestInputStream(
                    Files.newInputStream(path), digest)) {
                input.transferTo(java.io.OutputStream.nullOutputStream());
            }
            return HexFormat.of().formatHex(digest.digest());
        } catch (IOException | NoSuchAlgorithmException exception) {
            throw new KnowledgeImportException("无法计算 SHA-256: " + path, exception);
        }
    }

    private String mimeType(Path input) {
        try {
            String detected = Files.probeContentType(input);
            return detected == null ? "application/octet-stream" : detected;
        } catch (IOException exception) {
            return "application/octet-stream";
        }
    }

    private String safeFilename(String original) {
        String cleaned = original.replaceAll("[\\x00-\\x1f/\\\\:]+", "-")
                .replaceAll("\\s+", " ")
                .replaceAll("^[ .]+|[ .]+$", "");
        if (cleaned.isBlank()) {
            return "source.bin";
        }
        int codePoints = cleaned.codePointCount(0, cleaned.length());
        int end = cleaned.offsetByCodePoints(0, Math.min(180, codePoints));
        return cleaned.substring(0, end);
    }

    private static Path absolute(Path path) {
        return path.toAbsolutePath().normalize();
    }

    private record ExistingSource(String id, String originalName, String rawPath) {
    }
}
