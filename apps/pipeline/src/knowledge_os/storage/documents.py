"""Knowledge documents, placements and full-text search."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .schema import SCHEMA_VERSION, utc_now
from .records import add_event


def _upsert_document_row(
    connection: sqlite3.Connection, document: Mapping[str, Any]
) -> None:
    now = utc_now()
    connection.execute(
        """
        INSERT INTO documents(
            id, source_id, section_index, heading_path_json,
            source_line_start, source_line_end, body_sha256,
            splitter_version, title, normalized_path, body, summary,
            key_points_json, tags_json, visibility, model_name,
            prompt_version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source_id=excluded.source_id,
            section_index=excluded.section_index,
            heading_path_json=excluded.heading_path_json,
            source_line_start=excluded.source_line_start,
            source_line_end=excluded.source_line_end,
            body_sha256=excluded.body_sha256,
            splitter_version=excluded.splitter_version,
            title=excluded.title,
            normalized_path=excluded.normalized_path,
            body=excluded.body,
            summary=excluded.summary,
            key_points_json=excluded.key_points_json,
            tags_json=excluded.tags_json,
            visibility=excluded.visibility,
            model_name=excluded.model_name,
            prompt_version=excluded.prompt_version,
            updated_at=excluded.updated_at
        """,
        (
            document["id"],
            document["source_id"],
            int(document.get("section_index", 0)),
            json.dumps(document.get("heading_path", []), ensure_ascii=False),
            document.get("source_line_start"),
            document.get("source_line_end"),
            document.get("body_sha256", ""),
            document.get("splitter_version", "single-v1"),
            document["title"],
            document["normalized_path"],
            document["body"],
            document.get("summary", ""),
            json.dumps(document.get("key_points", []), ensure_ascii=False),
            json.dumps(document.get("tags", []), ensure_ascii=False),
            document.get("visibility", "private"),
            document.get("model_name", "rules-v1"),
            document.get("prompt_version", "extract-v1"),
            document.get("created_at", now),
            now,
        ),
    )


def upsert_document(
    connection: sqlite3.Connection, document: Mapping[str, Any]
) -> None:
    _upsert_document_row(connection, document)
    connection.execute(
        "UPDATE sources SET status='normalized', last_error=NULL WHERE id=?",
        (document["source_id"],),
    )
    connection.commit()


def replace_source_documents(
    connection: sqlite3.Connection,
    source_id: str,
    documents: Sequence[Mapping[str, Any]],
) -> List[Tuple[str, str]]:
    """Atomically reconcile every derived card for one immutable source."""

    if not documents:
        raise ValueError("a source must produce at least one document")
    ids = [str(document["id"]) for document in documents]
    section_indexes = [int(document.get("section_index", 0)) for document in documents]
    if len(ids) != len(set(ids)) or len(section_indexes) != len(set(section_indexes)):
        raise ValueError("document ids and section indexes must be unique per source")
    if any(str(document["source_id"]) != source_id for document in documents):
        raise ValueError("all documents must belong to the reconciled source")
    old_rows = connection.execute(
        "SELECT id, normalized_path FROM documents WHERE source_id=?",
        (source_id,),
    ).fetchall()
    stale = [row for row in old_rows if str(row["id"]) not in set(ids)]
    try:
        connection.execute("BEGIN IMMEDIATE")
        for row in stale:
            connection.execute(
                "DELETE FROM documents_fts WHERE document_id=?", (row["id"],)
            )
            connection.execute("DELETE FROM documents WHERE id=?", (row["id"],))
        for document in documents:
            _upsert_document_row(connection, document)
        connection.execute(
            "UPDATE sources SET status='normalized', last_error=NULL WHERE id=?",
            (source_id,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return [
        (str(row["id"]), str(row["normalized_path"])) for row in stale
    ]


def documents_by_source(
    connection: sqlite3.Connection, source_id: str
) -> List[sqlite3.Row]:
    return connection.execute(
        "SELECT * FROM documents WHERE source_id=? ORDER BY section_index, id",
        (source_id,),
    ).fetchall()


def mark_source_status(
    connection: sqlite3.Connection, source_id: str, status: str
) -> None:
    connection.execute(
        "UPDATE sources SET status=?, last_error=NULL WHERE id=?",
        (status, source_id),
    )
    connection.commit()


def place_document(
    connection: sqlite3.Connection,
    document_id: str,
    node_id: str,
    confidence: float,
    method: str,
) -> None:
    connection.execute(
        """
        INSERT INTO placements(
            document_id, node_id, confidence, method, classified_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(document_id) DO UPDATE SET
            node_id=excluded.node_id,
            confidence=excluded.confidence,
            method=excluded.method,
            classified_at=excluded.classified_at
        """,
        (document_id, node_id, confidence, method, utc_now()),
    )
    connection.commit()


def update_document_enrichment(
    connection: sqlite3.Connection,
    document_id: str,
    *,
    summary: str,
    key_points: Sequence[str],
    tags: Sequence[str],
    model_name: str,
    prompt_version: str,
) -> None:
    connection.execute(
        """
        UPDATE documents
        SET summary=?, key_points_json=?, tags_json=?, model_name=?,
            prompt_version=?, updated_at=?
        WHERE id=?
        """,
        (
            summary,
            json.dumps(list(key_points), ensure_ascii=False),
            json.dumps(list(tags), ensure_ascii=False),
            model_name,
            prompt_version,
            utc_now(),
            document_id,
        ),
    )
    connection.commit()


def index_document(connection: sqlite3.Connection, document_id: str) -> None:
    row = connection.execute(
        """
        SELECT d.id, d.source_id, d.title, d.body, d.tags_json, n.path_json
        FROM documents d
        JOIN placements p ON p.document_id=d.id
        JOIN nodes n ON n.id=p.node_id
        WHERE d.id=?
        """,
        (document_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"document is not ready for indexing: {document_id}")
    connection.execute(
        "DELETE FROM documents_fts WHERE document_id=?", (document_id,)
    )
    connection.execute(
        """
        INSERT INTO documents_fts(
            document_id, title, body, tags, taxonomy_path
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            row["id"],
            row["title"],
            row["body"],
            " ".join(json.loads(row["tags_json"])),
            " / ".join(json.loads(row["path_json"])),
        ),
    )
    add_event(
        connection,
        "document_indexed",
        source_id=row["source_id"],
        details={"document_id": document_id},
    )
    connection.commit()


def query_documents(
    connection: sqlite3.Connection, query: str, limit: int = 20
) -> List[Dict[str, Any]]:
    cleaned = query.strip()
    if not cleaned:
        return []
    safe_limit = max(1, min(int(limit), 100))
    tokenizer_row = connection.execute(
        "SELECT value FROM metadata WHERE key='fts_tokenizer'"
    ).fetchone()
    tokenizer = tokenizer_row["value"] if tokenizer_row else "trigram"
    rows: Iterable[sqlite3.Row]
    if tokenizer == "trigram" and len(cleaned) >= 3:
        expression = '"' + cleaned.replace('"', '""') + '"'
        try:
            rows = connection.execute(
                """
                SELECT d.id, d.title, d.summary, d.tags_json, d.updated_at,
                       n.id AS node_id, n.path_json,
                       bm25(documents_fts) AS rank,
                       snippet(documents_fts, 2, '<mark>', '</mark>', '…', 20)
                           AS snippet
                FROM documents_fts
                JOIN documents d ON d.id=documents_fts.document_id
                JOIN placements p ON p.document_id=d.id
                JOIN nodes n ON n.id=p.node_id
                WHERE documents_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (expression, safe_limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
    else:
        rows = []
    materialized = list(rows)
    if not materialized:
        pattern = f"%{cleaned}%"
        materialized = connection.execute(
            """
            SELECT d.id, d.title, d.summary, d.tags_json, d.updated_at,
                   n.id AS node_id, n.path_json, 0.0 AS rank,
                   substr(d.body, 1, 300) AS snippet
            FROM documents d
            JOIN placements p ON p.document_id=d.id
            JOIN nodes n ON n.id=p.node_id
            WHERE d.title LIKE ? OR d.body LIKE ? OR d.tags_json LIKE ?
            ORDER BY d.updated_at DESC
            LIMIT ?
            """,
            (pattern, pattern, pattern, safe_limit),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "summary": row["summary"],
            "tags": json.loads(row["tags_json"]),
            "node_id": row["node_id"],
            "path": json.loads(row["path_json"]),
            "rank": row["rank"],
            "snippet": row["snippet"],
            "updated_at": row["updated_at"],
        }
        for row in materialized
    ]


def status_summary(connection: sqlite3.Connection) -> Dict[str, Any]:
    def grouped(table: str, column: str) -> Dict[str, int]:
        return {
            str(row[0]): int(row[1])
            for row in connection.execute(
                f"SELECT {column}, COUNT(*) FROM {table} GROUP BY {column}"
            )
        }

    counts = {
        "sources": int(connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]),
        "documents": int(
            connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        ),
        "nodes": int(
            connection.execute("SELECT COUNT(*) FROM nodes WHERE active=1").fetchone()[0]
        ),
        "relations": int(
            connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        ),
    }
    job_status = grouped("jobs", "status")
    return {
        "schema_version": SCHEMA_VERSION,
        "counts": counts,
        "source_status": grouped("sources", "status"),
        "job_status": job_status,
        "jobs_pending": sum(
            int(job_status.get(name, 0))
            for name in ("queued", "retry", "running")
        ),
        "last_event_at": connection.execute(
            "SELECT MAX(happened_at) FROM events"
        ).fetchone()[0],
    }
