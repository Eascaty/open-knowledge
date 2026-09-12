"""Capability contract for the authenticated local browser session."""
from typing import Any, Dict

from .local_inbox import HISTORY_LIMIT, InboxUploadStore


def browser_session_payload(service: str, token: str, store: InboxUploadStore) -> Dict[str, Any]:
    return {
        "ok": True,
        "schema_version": 1,
        "api_version": "browser-v1",
        "service": service,
        "session_token": token,
        "capabilities": {
            "file_upload": True,
            "manual_classification": True,
            "document_review": True,
            "source_preview": True,
            "maximum_file_bytes": store.maximum_bytes,
            "accepted_extensions": list(store.accepted_extensions),
            "history_limit": HISTORY_LIMIT,
        },
    }
