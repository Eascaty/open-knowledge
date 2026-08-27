"""Synchronize the strict taxonomy with persisted placements."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .schema import utc_now
from .records import add_event

def sync_taxonomy(
    connection: sqlite3.Connection, taxonomy: Mapping[str, Any]
) -> None:
    connection.execute("UPDATE nodes SET active = 0")

    def visit(
        node: Mapping[str, Any],
        parent_id: Optional[str],
        level: int,
        path: List[str],
        order: int,
    ) -> None:
        current_path = path + [str(node["name"])]
        connection.execute(
            """
            INSERT INTO nodes(
                id, parent_id, name, level, path_json, locked, sort_order, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(id) DO UPDATE SET
                parent_id=excluded.parent_id,
                name=excluded.name,
                level=excluded.level,
                path_json=excluded.path_json,
                locked=excluded.locked,
                sort_order=excluded.sort_order,
                active=1
            """,
            (
                node["id"],
                parent_id,
                node["name"],
                level,
                json.dumps(current_path, ensure_ascii=False),
                1 if node.get("locked", False) else 0,
                order,
            ),
        )
        for child_order, child in enumerate(node.get("children", [])):
            visit(child, str(node["id"]), level + 1, current_path, child_order)

    visit(taxonomy["root"], None, 0, [], 0)
    # A taxonomy edit may retire a node that already owns documents.  Never
    # leave those placements pointing at an inactive node: move them to the
    # configured review bucket and queue enrichment/indexing again.
    uncertain_id = str(taxonomy["rules"]["uncertain_destination"])
    orphaned = connection.execute(
        """
        SELECT p.document_id, d.source_id
        FROM placements p
        JOIN documents d ON d.id=p.document_id
        JOIN nodes n ON n.id=p.node_id
        WHERE n.active=0
        ORDER BY p.document_id
        """
    ).fetchall()
    now = utc_now()
    affected_sources = set()
    for row in orphaned:
        document_id = str(row["document_id"])
        source_id = str(row["source_id"])
        affected_sources.add(source_id)
        connection.execute(
            """
            UPDATE placements
            SET node_id=?, confidence=0, method='taxonomy-node-retired',
                classified_at=?
            WHERE document_id=?
            """,
            (uncertain_id, now, document_id),
        )
        connection.execute(
            "DELETE FROM documents_fts WHERE document_id=?", (document_id,)
        )
        add_event(
            connection,
            "placement_retired",
            source_id=source_id,
            details={"document_id": document_id, "destination": uncertain_id},
        )
    for source_id in sorted(affected_sources):
        connection.execute(
            "UPDATE sources SET status='queued', last_error=NULL WHERE id=?",
            (source_id,),
        )
        for stage in ("enrich", "index"):
            connection.execute(
                """
                INSERT INTO jobs(
                    source_id, stage, status, attempts, max_attempts,
                    available_at, created_at, updated_at
                ) VALUES (?, ?, 'queued', 0, 3, ?, ?, ?)
                ON CONFLICT(source_id, stage) DO UPDATE SET
                    status='queued', attempts=0, available_at=excluded.available_at,
                    last_error=NULL, updated_at=excluded.updated_at
                """,
                (source_id, stage, now, now, now),
            )
    connection.commit()
