"""Atomic, local-only correction of one document's primary classification."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import ProjectPaths
from ..storage.schema import connect, utc_now


class ClassificationCorrectionError(RuntimeError):
    """Raised when a manual correction cannot be applied safely."""


@dataclass(frozen=True)
class ClassificationCorrection:
    document_id: str
    previous_node_id: str
    target_node_id: str
    previous_path: list[str]
    target_path: list[str]
    changed: bool
    dry_run: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def correct_document_classification(
    project_root: Path,
    document_id: str,
    target_node_id: str,
    *,
    expected_node_id: Optional[str] = None,
    dry_run: bool = False,
) -> ClassificationCorrection:
    root = project_root.expanduser().resolve()
    paths = ProjectPaths.from_root(root)
    if not paths.database_file.is_file() or paths.database_file.is_symlink():
        raise ClassificationCorrectionError("knowledge database is unavailable")
    document_id = document_id.strip()
    target_node_id = target_node_id.strip()
    if not document_id or not target_node_id:
        raise ClassificationCorrectionError("document_id and target_node_id are required")

    connection = connect(paths.database_file)
    try:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            """
            SELECT d.id, d.source_id, d.title, d.body, d.tags_json,
                   p.node_id, n.path_json
            FROM documents d
            JOIN placements p ON p.document_id=d.id
            JOIN nodes n ON n.id=p.node_id
            WHERE d.id=?
            """,
            (document_id,),
        ).fetchone()
        if current is None:
            raise ClassificationCorrectionError("document is not classified or does not exist")
        target = connection.execute(
            """
            SELECT id, parent_id, path_json FROM nodes
            WHERE id=? AND active=1
            """,
            (target_node_id,),
        ).fetchone()
        if target is None:
            raise ClassificationCorrectionError("target taxonomy node is missing or inactive")
        if target["parent_id"] is None:
            raise ClassificationCorrectionError("the taxonomy root cannot hold documents")
        if expected_node_id is not None and current["node_id"] != expected_node_id:
            raise ClassificationCorrectionError("classification changed since it was loaded")

        result = ClassificationCorrection(
            document_id=document_id,
            previous_node_id=str(current["node_id"]),
            target_node_id=target_node_id,
            previous_path=list(json.loads(current["path_json"])),
            target_path=list(json.loads(target["path_json"])),
            changed=current["node_id"] != target_node_id,
            dry_run=bool(dry_run),
        )
        if dry_run or not result.changed:
            connection.rollback()
            return result

        changed_at = utc_now()
        connection.execute(
            """
            UPDATE placements
            SET node_id=?, confidence=1.0, method='manual-v1', classified_at=?
            WHERE document_id=?
            """,
            (target_node_id, changed_at, document_id),
        )
        connection.execute(
            "UPDATE documents SET updated_at=? WHERE id=?",
            (changed_at, document_id),
        )
        connection.execute("DELETE FROM documents_fts WHERE document_id=?", (document_id,))
        connection.execute(
            """
            INSERT INTO documents_fts(document_id, title, body, tags, taxonomy_path)
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                document_id,
                current["title"],
                current["body"],
                " ".join(json.loads(current["tags_json"])),
                " / ".join(result.target_path),
            ),
        )
        connection.execute(
            """
            INSERT INTO events(happened_at, event_type, source_id, details_json)
            VALUES(?, 'classification_corrected', ?, ?)
            """,
            (
                changed_at,
                current["source_id"],
                json.dumps(
                    {
                        "document_id": document_id,
                        "previous_node_id": result.previous_node_id,
                        "target_node_id": target_node_id,
                        "method": "manual-v1",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        )
        connection.commit()
        return result
    except ClassificationCorrectionError:
        connection.rollback()
        raise
    except (sqlite3.Error, ValueError, TypeError) as exc:
        connection.rollback()
        raise ClassificationCorrectionError("classification correction failed") from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
