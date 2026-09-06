"""Auditable review status derived from append-only project events."""

from __future__ import annotations

import json
from typing import Any, Dict, List


DEFAULT_REVIEW_STATUS = "unverified"
REVIEW_STATUSES = frozenset(
    {
        "unverified",
        "personal",
        "supported",
        "verified-by-practice",
        "contradicted",
        "deprecated",
    }
)
REVIEW_EVENT_TYPE = "document_review_status_changed"
MAX_REVIEW_NOTE_LENGTH = 1000


def _review_event_rows(connection: Any) -> List[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT happened_at, details_json FROM events
        WHERE event_type=?
        ORDER BY id
        """,
        (REVIEW_EVENT_TYPE,),
    ).fetchall()
    events: List[dict[str, Any]] = []
    for row in rows:
        try:
            details = json.loads(row["details_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(details, dict):
            continue
        document_id = details.get("document_id")
        status = details.get("target_status")
        if (
            not isinstance(document_id, str)
            or not document_id
            or not isinstance(status, str)
            or status not in REVIEW_STATUSES
        ):
            continue
        note = details.get("note", "")
        if not isinstance(note, str):
            note = ""
        events.append(
            {
                "document_id": document_id,
                "previous_status": (
                    details.get("previous_status")
                    if details.get("previous_status") in REVIEW_STATUSES
                    else DEFAULT_REVIEW_STATUS
                ),
                "status": status,
                "note": note[:MAX_REVIEW_NOTE_LENGTH],
                "reviewed_at": str(row["happened_at"] or ""),
            }
        )
    return events


def review_statuses_by_document(connection: Any) -> Dict[str, str]:
    """Return the latest valid review status for each document."""

    return {
        document_id: item["status"]
        for document_id, item in review_metadata_by_document(connection).items()
    }


def review_metadata_by_document(connection: Any) -> Dict[str, dict[str, Any]]:
    """Return the latest status, note and private audit history per document."""

    metadata: Dict[str, dict[str, Any]] = {}
    for event in _review_event_rows(connection):
        document_id = event["document_id"]
        current = metadata.setdefault(
            document_id,
            {
                "status": DEFAULT_REVIEW_STATUS,
                "note": "",
                "reviewed_at": "",
                "history": [],
            },
        )
        current["status"] = event["status"]
        current["note"] = event["note"]
        current["reviewed_at"] = event["reviewed_at"]
        current["history"].append(
            {
                "previous_status": event["previous_status"],
                "status": event["status"],
                "note": event["note"],
                "reviewed_at": event["reviewed_at"],
            }
        )
    return metadata


def review_status_for_document(connection: Any, document_id: str) -> str:
    return review_metadata_by_document(connection).get(document_id, {}).get(
        "status", DEFAULT_REVIEW_STATUS
    )
