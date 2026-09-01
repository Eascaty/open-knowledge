"""Auditable review status derived from append-only project events."""

from __future__ import annotations

import json
from typing import Any, Dict


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


def review_statuses_by_document(connection: Any) -> Dict[str, str]:
    """Return the latest valid review status for each document."""

    statuses: Dict[str, str] = {}
    rows = connection.execute(
        """
        SELECT details_json FROM events
        WHERE event_type=?
        ORDER BY id
        """,
        (REVIEW_EVENT_TYPE,),
    ).fetchall()
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
            isinstance(document_id, str)
            and document_id
            and isinstance(status, str)
            and status in REVIEW_STATUSES
        ):
            statuses[document_id] = status
    return statuses


def review_status_for_document(connection: Any, document_id: str) -> str:
    return review_statuses_by_document(connection).get(
        document_id, DEFAULT_REVIEW_STATUS
    )
