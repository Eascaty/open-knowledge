from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from knowledge_os import db
from knowledge_os.config import ProjectPaths
from knowledge_os.operations.migration import migrate_project_database
from knowledge_os.operations.restore import RestoreDrillError, run_restore_drill
from knowledge_os.operations.snapshot import SnapshotError, create_sqlite_snapshot
from knowledge_os.storage.migrations import migrate_v1_to_v2


LEGACY_BODY = "# 原始标题\n\n保留正文\n第二行"
SOURCE_ID = "sha256-legacy-source"
FIXED_TIME = "2026-08-23T00:00:00+00:00"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _create_v1_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE nodes (
                id TEXT PRIMARY KEY,
                parent_id TEXT REFERENCES nodes(id),
                name TEXT NOT NULL,
                level INTEGER NOT NULL,
                path_json TEXT NOT NULL,
                locked INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                UNIQUE(parent_id, name)
            );
            CREATE TABLE sources (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                origin TEXT NOT NULL,
                original_name TEXT NOT NULL,
                raw_path TEXT NOT NULL,
                sha256 TEXT NOT NULL UNIQUE,
                mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                imported_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                last_error TEXT
            );
            CREATE TABLE documents (
                id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
                source_id TEXT NOT NULL UNIQUE REFERENCES sources(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                normalized_path TEXT NOT NULL,
                body TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                key_points_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                visibility TEXT NOT NULL DEFAULT 'private',
                model_name TEXT NOT NULL DEFAULT 'rules-v1',
                prompt_version TEXT NOT NULL DEFAULT 'extract-v1',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE placements (
                document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                node_id TEXT NOT NULL REFERENCES nodes(id),
                confidence REAL NOT NULL DEFAULT 0,
                method TEXT NOT NULL,
                classified_at TEXT NOT NULL
            );
            CREATE TABLE relations (
                id TEXT PRIMARY KEY,
                from_node_id TEXT NOT NULL REFERENCES nodes(id),
                to_node_id TEXT NOT NULL REFERENCES nodes(id),
                relation_type TEXT NOT NULL,
                label TEXT NOT NULL,
                document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
                confidence REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                stage TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                available_at TEXT NOT NULL,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source_id, stage)
            );
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                happened_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                source_id TEXT,
                details_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE VIRTUAL TABLE documents_fts USING fts5(
                document_id UNINDEXED,
                title,
                body,
                tags,
                taxonomy_path,
                tokenize='unicode61'
            );
            """
        )
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES(?, ?)",
            (("schema_version", "1"), ("fts_tokenizer", "unicode61")),
        )
        connection.execute(
            """
            INSERT INTO nodes(
                id, parent_id, name, level, path_json,
                locked, sort_order, active
            ) VALUES('root', NULL, '知识', 0, '["知识"]', 1, 0, 1)
            """
        )
        connection.execute(
            """
            INSERT INTO nodes(
                id, parent_id, name, level, path_json,
                locked, sort_order, active
            ) VALUES('topic', 'root', '主题', 1, '["知识","主题"]', 1, 0, 1)
            """
        )
        connection.execute(
            """
            INSERT INTO sources(
                id, kind, origin, original_name, raw_path, sha256,
                mime_type, size_bytes, imported_at, status, last_error
            ) VALUES(?, 'file', '/private/input.md', 'input.md',
                     'workspace/data/raw/aa/input.md', ?, 'text/markdown',
                     ?, ?, 'completed', NULL)
            """,
            (SOURCE_ID, "a" * 64, len(LEGACY_BODY.encode("utf-8")), FIXED_TIME),
        )
        connection.execute(
            """
            INSERT INTO documents(
                id, source_id, title, normalized_path, body, summary,
                key_points_json, tags_json, visibility, model_name,
                prompt_version, created_at, updated_at
            ) VALUES(?, ?, '旧知识', 'workspace/data/normalized/legacy.md', ?,
                     '旧摘要', '["要点"]', '["标签"]', 'private',
                     'rules-v1', 'knowledge-extract-v1', ?, ?)
            """,
            (SOURCE_ID, SOURCE_ID, LEGACY_BODY, FIXED_TIME, FIXED_TIME),
        )
        connection.execute(
            """
            INSERT INTO placements(
                document_id, node_id, confidence, method, classified_at
            ) VALUES(?, 'topic', 0.75, 'rules-stepwise', ?)
            """,
            (SOURCE_ID, FIXED_TIME),
        )
        connection.execute(
            """
            INSERT INTO relations(
                id, from_node_id, to_node_id, relation_type,
                label, document_id, confidence
            ) VALUES('relation-1', 'root', 'topic', 'supports',
                     '支持', ?, 0.8)
            """,
            (SOURCE_ID,),
        )
        connection.execute(
            """
            INSERT INTO jobs(
                source_id, stage, status, attempts, max_attempts,
                available_at, last_error, created_at, updated_at
            ) VALUES(?, 'index', 'done', 1, 3, ?, NULL, ?, ?)
            """,
            (SOURCE_ID, FIXED_TIME, FIXED_TIME, FIXED_TIME),
        )
        connection.execute(
            """
            INSERT INTO events(
                happened_at, event_type, source_id, details_json
            ) VALUES(?, 'document_indexed', ?, '{"preserved":true}')
            """,
            (FIXED_TIME, SOURCE_ID),
        )
        connection.execute(
            """
            INSERT INTO documents_fts(
                document_id, title, body, tags, taxonomy_path
            ) VALUES(?, '旧知识', ?, '标签', '知识 / 主题')
            """,
            (SOURCE_ID, LEGACY_BODY),
        )
        connection.commit()
    finally:
        connection.close()


class SchemaMigrationAndRestoreTests(unittest.TestCase):
    def test_v1_migration_preserves_data_and_creates_verified_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            _create_v1_database(paths.database_file)

            result = migrate_project_database(root)

            self.assertTrue(result.changed)
            self.assertEqual((result.from_version, result.to_version), (1, 2))
            self.assertEqual(result.requeued_sources, 1)
            self.assertIsNotNone(result.snapshot)
            assert result.snapshot is not None
            self.assertTrue(result.snapshot.snapshot.is_file())
            self.assertEqual(_sha256(result.snapshot.snapshot), result.snapshot.sha256)
            self.assertEqual(len(result.snapshot.sha256), 64)

            pre_migration_drill = run_restore_drill(
                result.snapshot.snapshot,
                expected_sha256=result.snapshot.sha256.upper(),
            )
            self.assertEqual(pre_migration_drill.schema_version, 1)
            self.assertEqual(pre_migration_drill.counts["documents"], 1)
            snapshot_connection = sqlite3.connect(str(result.snapshot.snapshot))
            try:
                self.assertEqual(
                    snapshot_connection.execute(
                        "SELECT value FROM metadata WHERE key='schema_version'"
                    ).fetchone()[0],
                    "1",
                )
                self.assertEqual(
                    snapshot_connection.execute(
                        "SELECT body FROM documents WHERE id=?", (SOURCE_ID,)
                    ).fetchone()[0],
                    LEGACY_BODY,
                )
            finally:
                snapshot_connection.close()

            connection = db.connect(paths.database_file)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT value FROM metadata WHERE key='schema_version'"
                    ).fetchone()[0],
                    "2",
                )
                document = connection.execute(
                    "SELECT * FROM documents WHERE id=?", (SOURCE_ID,)
                ).fetchone()
                self.assertEqual(document["source_id"], SOURCE_ID)
                self.assertEqual(document["section_index"], 0)
                self.assertEqual(document["body"], LEGACY_BODY)
                self.assertEqual(document["summary"], "旧摘要")
                self.assertEqual(document["source_line_start"], 1)
                self.assertEqual(
                    document["source_line_end"], len(LEGACY_BODY.splitlines())
                )
                self.assertEqual(
                    document["body_sha256"],
                    hashlib.sha256(LEGACY_BODY.encode("utf-8")).hexdigest(),
                )
                self.assertEqual(document["splitter_version"], "legacy-single-v1")
                self.assertEqual(
                    connection.execute(
                        "SELECT node_id FROM placements WHERE document_id=?",
                        (SOURCE_ID,),
                    ).fetchone()[0],
                    "topic",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT label FROM relations WHERE document_id=?",
                        (SOURCE_ID,),
                    ).fetchone()[0],
                    "支持",
                )
                self.assertEqual(
                    tuple(connection.execute(
                        "SELECT stage, status FROM jobs WHERE source_id=?",
                        (SOURCE_ID,),
                    ).fetchone()),
                    ("extract", "queued"),
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT details_json FROM events
                        WHERE source_id=? AND event_type='document_indexed'
                        """,
                        (SOURCE_ID,),
                    ).fetchone()[0],
                    '{"preserved":true}',
                )
                requeue_event = connection.execute(
                    """
                    SELECT details_json FROM events
                    WHERE source_id=? AND event_type='source_requeued'
                    """,
                    (SOURCE_ID,),
                ).fetchone()
                self.assertEqual(
                    json.loads(requeue_event[0])["reason"],
                    "schema-v2-markdown-card-splitting",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT body FROM documents_fts WHERE document_id=?",
                        (SOURCE_ID,),
                    ).fetchone()[0],
                    LEGACY_BODY,
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(), []
                )
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                )
                placement_foreign_keys = connection.execute(
                    "PRAGMA foreign_key_list(placements)"
                ).fetchall()
                self.assertTrue(
                    any(
                        row["from"] == "document_id"
                        and row["table"] == "documents"
                        for row in placement_foreign_keys
                    )
                )
                connection.execute(
                    """
                    INSERT INTO documents(
                        id, source_id, section_index, title,
                        normalized_path, body, created_at, updated_at
                    ) VALUES('derived-card', ?, 1, '第二张卡',
                             'workspace/data/normalized/card-2.md',
                             '第二张卡正文', ?, ?)
                    """,
                    (SOURCE_ID, FIXED_TIME, FIXED_TIME),
                )
                connection.commit()
            finally:
                connection.close()

            second = migrate_project_database(root)
            self.assertFalse(second.changed)
            self.assertEqual((second.from_version, second.to_version), (2, 2))
            self.assertEqual(second.requeued_sources, 0)
            self.assertIsNone(second.snapshot)
            backups = list(
                (paths.private_exports_dir / "backups").glob("*.sqlite3")
            )
            self.assertEqual(backups, [result.snapshot.snapshot])

            v2_snapshot = create_sqlite_snapshot(
                paths.database_file, root / "v2-backups", prefix="schema-v2"
            )
            post_migration_drill = run_restore_drill(
                v2_snapshot.snapshot, expected_sha256=v2_snapshot.sha256
            )
            self.assertEqual(post_migration_drill.schema_version, 2)
            self.assertEqual(post_migration_drill.counts["documents"], 2)

    def test_failed_migration_rolls_back_complete_v1_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            _create_v1_database(paths.database_file)
            connection = sqlite3.connect(str(paths.database_file))
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    "UPDATE relations SET document_id='missing-document'"
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaisesRegex(RuntimeError, "foreign-key violations"):
                migrate_project_database(root)

            connection = sqlite3.connect(str(paths.database_file))
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT value FROM metadata WHERE key='schema_version'"
                    ).fetchone()[0],
                    "1",
                )
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                self.assertIn("documents", tables)
                self.assertIn("placements", tables)
                self.assertIn("relations", tables)
                self.assertNotIn("documents_v1", tables)
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM placements").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1
                )
            finally:
                connection.close()
            self.assertEqual(
                len(list((paths.private_exports_dir / "backups").glob("*.sqlite3"))),
                1,
            )

    def test_migration_never_commits_callers_active_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "knowledge.sqlite3"
            _create_v1_database(database)
            connection = sqlite3.connect(str(database))
            try:
                connection.execute(
                    "UPDATE documents SET summary='未提交修改' WHERE id=?",
                    (SOURCE_ID,),
                )
                self.assertTrue(connection.in_transaction)
                with self.assertRaisesRegex(RuntimeError, "active transaction"):
                    migrate_v1_to_v2(connection)
                observer = sqlite3.connect(str(database))
                try:
                    self.assertEqual(
                        observer.execute(
                            "SELECT summary FROM documents WHERE id=?", (SOURCE_ID,)
                        ).fetchone()[0],
                        "旧摘要",
                    )
                finally:
                    observer.close()
            finally:
                connection.rollback()
                connection.close()

    def test_restore_rejects_bad_hash_and_corruption_without_touching_live_db(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            paths.state_dir.mkdir(parents=True)
            connection = db.connect(paths.database_file)
            try:
                db.initialize_database(connection)
            finally:
                connection.close()
            snapshot = create_sqlite_snapshot(
                paths.database_file, root / "backups", prefix="live"
            )
            live_before = paths.database_file.read_bytes()

            with self.assertRaisesRegex(RestoreDrillError, "does not match"):
                run_restore_drill(snapshot.snapshot, expected_sha256="0" * 64)

            corrupt = root / "corrupt.sqlite3"
            corrupt.write_bytes(b"this is not a sqlite database")
            with self.assertRaisesRegex(RestoreDrillError, "restore drill failed"):
                run_restore_drill(corrupt)

            self.assertEqual(paths.database_file.read_bytes(), live_before)
            self.assertEqual(_sha256(paths.database_file), hashlib.sha256(live_before).hexdigest())

    def test_first_connection_closes_if_second_connection_cannot_open(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "source.sqlite3"
            source_path.write_bytes(b"placeholder")

            snapshot_source = mock.Mock()
            with mock.patch(
                "knowledge_os.operations.snapshot.sqlite3.connect",
                side_effect=[snapshot_source, sqlite3.OperationalError("open failed")],
            ):
                with self.assertRaises(SnapshotError):
                    create_sqlite_snapshot(source_path, root / "backups")
            snapshot_source.close.assert_called_once_with()
            self.assertEqual(list((root / "backups").glob(".snapshot-*")), [])

            restore_source = mock.Mock()
            with mock.patch(
                "knowledge_os.operations.restore.sqlite3.connect",
                side_effect=[restore_source, sqlite3.OperationalError("open failed")],
            ):
                with self.assertRaises(RestoreDrillError):
                    run_restore_drill(source_path)
            restore_source.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
