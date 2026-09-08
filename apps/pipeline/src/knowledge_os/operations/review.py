"""Atomic, local-only correction of one document's review status."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict

from ..config import ProjectPaths
from ..storage.reviews import (
    DEFAULT_REVIEW_STATUS,
    MAX_REVIEW_NOTE_LENGTH,
    REVIEW_EVENT_TYPE,
    REVIEW_STATUSES,
    review_metadata_by_document,
)
from ..storage.schema import connect, utc_now


class DocumentReviewError(RuntimeError):
    """Raised when a review status cannot be changed safely."""


@dataclass(frozen=True)
class DocumentReviewChange:
    document_id: str
    previous_status: str
    target_status: str
    previous_note: str
    note: str
    changed: bool
    dry_run: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def change_document_review(
    project_root: Path,
    document_id: str,
    target_status: str,
    *,
    expected_status: str = DEFAULT_REVIEW_STATUS,
    note: str = "",
    expected_note: str = "",
    dry_run: bool = False,
) -> DocumentReviewChange:
    root = project_root.expanduser().resolve()
    paths = ProjectPaths.from_root(root)
    if not paths.database_file.is_file() or paths.database_file.is_symlink():
        raise DocumentReviewError("knowledge database is unavailable")
    document_id = document_id.strip()
    target_status = target_status.strip()
    expected_status = expected_status.strip()
    note = note.strip()
    expected_note = expected_note.strip()
    if not document_id:
        raise DocumentReviewError("document_id is required")
    if target_status not in REVIEW_STATUSES or expected_status not in REVIEW_STATUSES:
        raise DocumentReviewError("review status is invalid")
    if len(note) > MAX_REVIEW_NOTE_LENGTH or len(expected_note) > MAX_REVIEW_NOTE_LENGTH:
        raise DocumentReviewError("review note is too long")
    if any(ord(char) < 32 and char not in "\n\t" for char in note):
        raise DocumentReviewError("review note contains invalid characters")
    if any(ord(char) < 32 and char not in "\n\t" for char in expected_note):
        raise DocumentReviewError("review note contains invalid characters")

    connection = connect(paths.database_file)
    try:
        connection.execute("BEGIN IMMEDIATE")
        document = connection.execute(
            "SELECT id, source_id FROM documents WHERE id=?",
            (document_id,),
        ).fetchone()
        if document is None:
            raise DocumentReviewError("document does not exist")
        current_review = review_metadata_by_document(connection).get(
            document_id,
            {"status": DEFAULT_REVIEW_STATUS, "note": ""},
        )
        current_status = current_review["status"]
        current_note = current_review.get("note", "")
        if current_status != expected_status or current_note != expected_note:
            raise DocumentReviewError("review status changed since it was loaded")
        result = DocumentReviewChange(
            document_id=document_id,
            previous_status=current_status,
            target_status=target_status,
            previous_note=current_note,
            note=note,
            changed=current_status != target_status or current_note != note,
            dry_run=bool(dry_run),
        )
        if dry_run or not result.changed:
            connection.rollback()
            return result

        changed_at = utc_now()
        connection.execute(
            """
            INSERT INTO events(happened_at, event_type, source_id, details_json)
            VALUES(?, ?, ?, ?)
            """,
            (
                changed_at,
                REVIEW_EVENT_TYPE,
                document["source_id"],
                json.dumps(
                    {
                        "document_id": document_id,
                        "previous_status": current_status,
                        "target_status": target_status,
                        "previous_note": current_note,
                        "note": note,
                        "method": "manual-review-v1",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        )
        connection.execute(
            "UPDATE documents SET updated_at=? WHERE id=?",
            (changed_at, document_id),
        )
        connection.commit()
        return result
    except DocumentReviewError:
        connection.rollback()
        raise
    except (sqlite3.Error, ValueError, TypeError) as exc:
        connection.rollback()
        raise DocumentReviewError("document review failed") from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
