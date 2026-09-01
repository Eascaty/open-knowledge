"""Validated browser requests for local-only document review."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .operations.lock import LockUnavailable, ProjectLock
from .operations.review import (
    DocumentReviewChange,
    DocumentReviewError,
    change_document_review,
)


REVIEW_PATH = "/__knowledge/review"
MAX_REVIEW_BODY_BYTES = 4 * 1024
_ALLOWED_FIELDS = {
    "action",
    "document_id",
    "target_status",
    "expected_status",
}


class BrowserReviewError(RuntimeError):
    """A safe review error that may be returned to the local browser."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class BrowserReviewRequest:
    action: str
    document_id: str
    target_status: str
    expected_status: str

    @property
    def dry_run(self) -> bool:
        return self.action == "preview"


def _text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise BrowserReviewError(400, "invalid_request", "审核参数不完整")
    normalized = value.strip()
    if not normalized or len(normalized) > 300 or any(
        ord(char) < 32 for char in normalized
    ):
        raise BrowserReviewError(400, "invalid_request", "审核参数格式无效")
    return normalized


def parse_review_request(body: bytes) -> BrowserReviewRequest:
    if not body or len(body) > MAX_REVIEW_BODY_BYTES:
        raise BrowserReviewError(413, "invalid_size", "审核请求大小无效")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrowserReviewError(400, "invalid_json", "审核请求格式无效") from exc
    if not isinstance(payload, dict) or set(payload) - _ALLOWED_FIELDS:
        raise BrowserReviewError(400, "invalid_request", "审核请求字段无效")
    action = payload.get("action")
    if action not in {"preview", "apply"}:
        raise BrowserReviewError(400, "invalid_action", "审核操作无效")
    return BrowserReviewRequest(
        action=str(action),
        document_id=_text(payload, "document_id"),
        target_status=_text(payload, "target_status"),
        expected_status=_text(payload, "expected_status"),
    )


def change_browser_review(
    project_root: Path,
    request: BrowserReviewRequest,
) -> DocumentReviewChange:
    try:
        with ProjectLock(project_root, purpose="browser-document-review"):
            return change_document_review(
                project_root,
                request.document_id,
                request.target_status,
                expected_status=request.expected_status,
                dry_run=request.dry_run,
            )
    except LockUnavailable as exc:
        raise BrowserReviewError(
            409, "manager_busy", "知识管家正在整理资料，请稍后再试"
        ) from exc
    except DocumentReviewError as exc:
        messages = {
            "document does not exist": (
                "document_missing",
                "这张知识卡已不存在",
            ),
            "review status is invalid": (
                "status_invalid",
                "目标可信状态不可用",
            ),
            "review status changed since it was loaded": (
                "review_changed",
                "可信状态已在其他位置变化，请刷新后重试",
            ),
        }
        code, message = messages.get(
            str(exc),
            ("review_failed", "知识审核失败，请稍后重试"),
        )
        raise BrowserReviewError(409, code, message) from exc
