"""Normalize private review metadata without changing public facts."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence


def normalize_review(review: Any, as_text: Callable[[Any], str]) -> dict[str, Any]:
    if not isinstance(review, Mapping):
        return {"note": "", "reviewed_at": "", "history": []}
    history: list[dict[str, str]] = []
    raw_history = review.get("history", [])
    if isinstance(raw_history, Sequence) and not isinstance(raw_history, (str, bytes, bytearray)):
        for item in raw_history[:100]:
            if isinstance(item, Mapping):
                history.append({
                    "previous_status": as_text(item.get("previous_status")),
                    "status": as_text(item.get("status")),
                    "note": as_text(item.get("note"))[:1000],
                    "reviewed_at": as_text(item.get("reviewed_at")),
                })
    return {
        "note": as_text(review.get("note"))[:1000],
        "reviewed_at": as_text(review.get("reviewed_at")),
        "history": history,
    }
