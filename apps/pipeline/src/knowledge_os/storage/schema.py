"""SQLite connection and schema lifecycle."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SCHEMA_VERSION = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        connection.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        # Some in-memory/read-only SQLite contexts cannot switch journal modes.
        pass
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    metadata_exists = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='metadata'
        """
    ).fetchone()
    if metadata_exists is not None:
        version_row = connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()
        if version_row is None:
            raise RuntimeError(
                "existing database has no schema_version; refusing implicit migration"
            )
        try:
            existing_version = int(version_row[0])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("database schema_version is invalid") from exc
        if existing_version == 1:
            raise RuntimeError(
                "database schema v1 requires an explicit verified migration to v2"
            )
        if existing_version != SCHEMA_VERSION:
            raise RuntimeError(
                "unsupported database schema_version {} (expected {})".format(
                    existing_version, SCHEMA_VERSION
                )
            )
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS nodes (
            id TEXT PRIMARY KEY,
            parent_id TEXT REFERENCES nodes(id),
            name TEXT NOT NULL,
            level INTEGER NOT NULL CHECK (level >= 0),
            path_json TEXT NOT NULL,
            locked INTEGER NOT NULL DEFAULT 0 CHECK (locked IN (0, 1)),
            sort_order INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
            UNIQUE(parent_id, name)
        );

        CREATE INDEX IF NOT EXISTS nodes_parent_idx ON nodes(parent_id, sort_order);

        CREATE TABLE IF NOT EXISTS sources (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            origin TEXT NOT NULL,
            original_name TEXT NOT NULL,
            raw_path TEXT NOT NULL,
            sha256 TEXT NOT NULL UNIQUE,
            mime_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
            imported_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            last_error TEXT
        );

        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            section_index INTEGER NOT NULL DEFAULT 0 CHECK (section_index >= 0),
            heading_path_json TEXT NOT NULL DEFAULT '[]',
            source_line_start INTEGER CHECK (
                source_line_start IS NULL OR source_line_start > 0
            ),
            source_line_end INTEGER CHECK (
                source_line_end IS NULL OR source_line_end >= source_line_start
            ),
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
        );

        CREATE INDEX IF NOT EXISTS documents_source_idx
        ON documents(source_id, section_index);

        CREATE TABLE IF NOT EXISTS placements (
            document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
            node_id TEXT NOT NULL REFERENCES nodes(id),
            confidence REAL NOT NULL DEFAULT 0,
            method TEXT NOT NULL,
            classified_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS relations (
            id TEXT PRIMARY KEY,
            from_node_id TEXT NOT NULL REFERENCES nodes(id),
            to_node_id TEXT NOT NULL REFERENCES nodes(id),
            relation_type TEXT NOT NULL,
            label TEXT NOT NULL,
            document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
            confidence REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            stage TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'running', 'retry', 'done', 'failed')),
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            available_at TEXT NOT NULL,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(source_id, stage)
        );

        CREATE INDEX IF NOT EXISTS jobs_ready_idx
        ON jobs(status, available_at, id);

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            happened_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            source_id TEXT,
            details_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    # Trigram supports substring-style Chinese search.  It is part of FTS5 on
    # the target macOS SQLite; fail loudly if FTS5 itself is unavailable.
    try:
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                document_id UNINDEXED,
                title,
                body,
                tags,
                taxonomy_path,
                tokenize='trigram'
            )
            """
        )
        tokenizer = "trigram"
    except sqlite3.OperationalError as exc:
        if "tokenize" not in str(exc).lower():
            raise RuntimeError("SQLite must be compiled with FTS5 support") from exc
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                document_id UNINDEXED,
                title,
                body,
                tags,
                taxonomy_path,
                tokenize='unicode61'
            )
            """
        )
        tokenizer = "unicode61"
    connection.execute(
        "INSERT OR IGNORE INTO metadata(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES('fts_tokenizer', ?)",
        (tokenizer,),
    )
    connection.commit()
