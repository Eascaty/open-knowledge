"""Explicit, transactional SQLite schema migrations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class SchemaMigration:
    from_version: int
    to_version: int
    requeued_sources: int = 0


def read_schema_version(connection: sqlite3.Connection) -> Optional[int]:
    metadata_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
    ).fetchone()
    if metadata_exists is None:
        return None
    row = connection.execute(
        "SELECT value FROM metadata WHERE key='schema_version'"
    ).fetchone()
    if row is None:
        raise RuntimeError("existing database has no schema_version")
    try:
        return int(row[0])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("database schema_version is invalid") from exc


def migrate_v1_to_v2(connection: sqlite3.Connection) -> SchemaMigration:
    """Replace the 1:1 source/document constraint with source 1:N cards.

    The caller must hold the project write lock and create a verified snapshot
    before calling this function. SQLite DDL remains inside one transaction;
    failures restore the complete v1 layout.
    """

    if connection.in_transaction:
        raise RuntimeError(
            "schema migration requires a connection with no active transaction"
        )
    if read_schema_version(connection) != 1:
        raise RuntimeError("schema v1 migration requires a v1 database")
    connection.execute("PRAGMA foreign_keys = OFF")
    if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 0:
        raise RuntimeError("could not disable foreign keys for schema migration")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("ALTER TABLE placements RENAME TO placements_v1")
        connection.execute("ALTER TABLE relations RENAME TO relations_v1")
        connection.execute("ALTER TABLE documents RENAME TO documents_v1")
        connection.execute(
            """
            CREATE TABLE documents (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                section_index INTEGER NOT NULL DEFAULT 0 CHECK (section_index >= 0),
                heading_path_json TEXT NOT NULL DEFAULT '[]',
                source_line_start INTEGER CHECK (source_line_start IS NULL OR source_line_start > 0),
                source_line_end INTEGER CHECK (source_line_end IS NULL OR source_line_end >= source_line_start),
                body_sha256 TEXT NOT NULL DEFAULT '',
                splitter_version TEXT NOT NULL DEFAULT 'single-v1',
                title TEXT NOT NULL,
                normalized_path TEXT NOT NULL,
                body TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                key_points_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                visibility TEXT NOT NULL DEFAULT 'private'
                    CHECK (visibility IN ('private', 'public')),
                model_name TEXT NOT NULL DEFAULT 'rules-v1',
                prompt_version TEXT NOT NULL DEFAULT 'extract-v1',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source_id, section_index)
            )
            """
        )
        connection.execute(
            "CREATE INDEX documents_source_idx ON documents(source_id, section_index)"
        )
        connection.execute(
            """
            INSERT INTO documents(
                id, source_id, section_index, heading_path_json,
                source_line_start, source_line_end, body_sha256,
                splitter_version, title, normalized_path, body, summary,
                key_points_json, tags_json, visibility, model_name,
                prompt_version, created_at, updated_at
            )
            SELECT id, source_id, 0, '[]', NULL, NULL, '',
                   'legacy-single-v1', title, normalized_path, body, summary,
                   key_points_json, tags_json, visibility, model_name,
                   prompt_version, created_at, updated_at
            FROM documents_v1
            """
        )
        legacy_bodies = connection.execute(
            "SELECT id, body FROM documents ORDER BY id"
        ).fetchall()
        for row in legacy_bodies:
            body = str(row[1])
            connection.execute(
                """
                UPDATE documents
                SET source_line_start=1,
                    source_line_end=?,
                    body_sha256=?
                WHERE id=?
                """,
                (
                    max(1, len(body.splitlines())),
                    hashlib.sha256(body.encode("utf-8")).hexdigest(),
                    str(row[0]),
                ),
            )
        connection.execute(
            """
            CREATE TABLE placements (
                document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                node_id TEXT NOT NULL REFERENCES nodes(id),
                confidence REAL NOT NULL DEFAULT 0,
                method TEXT NOT NULL,
                classified_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO placements(document_id, node_id, confidence, method, classified_at)
            SELECT document_id, node_id, confidence, method, classified_at
            FROM placements_v1
            """
        )
        connection.execute(
            """
            CREATE TABLE relations (
                id TEXT PRIMARY KEY,
                from_node_id TEXT NOT NULL REFERENCES nodes(id),
                to_node_id TEXT NOT NULL REFERENCES nodes(id),
                relation_type TEXT NOT NULL,
                label TEXT NOT NULL,
                document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
                confidence REAL NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            """
            INSERT INTO relations(
                id, from_node_id, to_node_id, relation_type,
                label, document_id, confidence
            )
            SELECT id, from_node_id, to_node_id, relation_type,
                   label, document_id, confidence
            FROM relations_v1
            """
        )
        connection.execute("DROP TABLE placements_v1")
        connection.execute("DROP TABLE relations_v1")
        connection.execute("DROP TABLE documents_v1")
        connection.execute(
            "UPDATE metadata SET value='2' WHERE key='schema_version'"
        )
        markdown_sources = connection.execute(
            """
            SELECT id FROM sources
            WHERE lower(original_name) LIKE '%.md'
               OR lower(original_name) LIKE '%.markdown'
               OR lower(mime_type) IN ('text/markdown', 'text/x-markdown')
            ORDER BY id
            """
        ).fetchall()
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        for source_row in markdown_sources:
            source_id = str(source_row[0])
            attempt_row = connection.execute(
                "SELECT MAX(max_attempts) FROM jobs WHERE source_id=?",
                (source_id,),
            ).fetchone()
            max_attempts = max(1, int(attempt_row[0] or 3))
            # Downstream jobs are disposable queue state. Removing them makes
            # enrichment/indexing depend on the newly successful extraction
            # instead of racing ahead with the legacy single document.
            connection.execute(
                "DELETE FROM jobs WHERE source_id=? AND stage IN ('enrich', 'index')",
                (source_id,),
            )
            connection.execute(
                """
                INSERT INTO jobs(
                    source_id, stage, status, attempts, max_attempts,
                    available_at, last_error, created_at, updated_at
                ) VALUES(?, 'extract', 'queued', 0, ?, ?, NULL, ?, ?)
                ON CONFLICT(source_id, stage) DO UPDATE SET
                    status='queued', attempts=0,
                    max_attempts=excluded.max_attempts,
                    available_at=excluded.available_at,
                    last_error=NULL, updated_at=excluded.updated_at
                """,
                (source_id, max_attempts, now, now, now),
            )
            connection.execute(
                "UPDATE sources SET status='queued', last_error=NULL WHERE id=?",
                (source_id,),
            )
            connection.execute(
                """
                INSERT INTO events(
                    happened_at, event_type, source_id, details_json
                ) VALUES(?, 'source_requeued', ?, ?)
                """,
                (
                    now,
                    source_id,
                    json.dumps(
                        {
                            "reason": "schema-v2-markdown-card-splitting",
                            "next_stage": "extract",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                "schema v2 migration introduced foreign-key violations"
            )
        integrity = [
            str(row[0])
            for row in connection.execute("PRAGMA integrity_check").fetchall()
        ]
        if integrity != ["ok"]:
            raise RuntimeError("schema v2 migration failed integrity_check")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
    return SchemaMigration(
        from_version=1,
        to_version=2,
        requeued_sources=len(markdown_sources),
    )
