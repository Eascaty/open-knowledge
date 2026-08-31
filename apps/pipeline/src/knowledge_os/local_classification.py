"""Validated browser requests for local-only classification correction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .operations.classification import (
    ClassificationCorrection,
    ClassificationCorrectionError,
    correct_document_classification,
)
from .operations.lock import LockUnavailable, ProjectLock


CLASSIFICATION_PATH = "/__knowledge/classification"
MAX_CLASSIFICATION_BODY_BYTES = 8 * 1024
_ALLOWED_FIELDS = {
    "action",
    "document_id",
    "target_node_id",
    "expected_node_id",
}


class BrowserClassificationError(RuntimeError):
    """A safe error that may be returned to the local browser."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class BrowserClassificationRequest:
    action: str
    document_id: str
    target_node_id: str
    expected_node_id: str

    @property
    def dry_run(self) -> bool:
        return self.action == "preview"


def _identifier(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise BrowserClassificationError(400, "invalid_request", "归类参数不完整")
    normalized = value.strip()
    if not normalized or len(normalized) > 300 or any(ord(char) < 32 for char in normalized):
        raise BrowserClassificationError(400, "invalid_request", "归类参数格式无效")
    return normalized


def parse_classification_request(body: bytes) -> BrowserClassificationRequest:
    if not body or len(body) > MAX_CLASSIFICATION_BODY_BYTES:
        raise BrowserClassificationError(413, "invalid_size", "归类请求大小无效")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrowserClassificationError(400, "invalid_json", "归类请求格式无效") from exc
    if not isinstance(payload, dict) or set(payload) - _ALLOWED_FIELDS:
        raise BrowserClassificationError(400, "invalid_request", "归类请求字段无效")
    action = payload.get("action")
    if action not in {"preview", "apply"}:
        raise BrowserClassificationError(400, "invalid_action", "归类操作无效")
    return BrowserClassificationRequest(
        action=str(action),
        document_id=_identifier(payload, "document_id"),
        target_node_id=_identifier(payload, "target_node_id"),
        expected_node_id=_identifier(payload, "expected_node_id"),
    )


def correct_browser_classification(
    project_root: Path,
    request: BrowserClassificationRequest,
) -> ClassificationCorrection:
    try:
        with ProjectLock(project_root, purpose="browser-classification"):
            return correct_document_classification(
                project_root,
                request.document_id,
                request.target_node_id,
                expected_node_id=request.expected_node_id,
                dry_run=request.dry_run,
            )
    except LockUnavailable as exc:
        raise BrowserClassificationError(
            409,
            "manager_busy",
            "知识管家正在整理资料，请稍后再试",
        ) from exc
    except ClassificationCorrectionError as exc:
        messages = {
            "document is not classified or does not exist": (
                "document_missing",
                "这张知识卡已不存在或尚未归类",
            ),
            "target taxonomy node is missing or inactive": (
                "target_unavailable",
                "目标分类已不存在或不可用",
            ),
            "the taxonomy root cannot hold documents": (
                "target_unavailable",
                "不能把知识卡直接放在根目录",
            ),
            "classification changed since it was loaded": (
                "classification_changed",
                "归类已在其他位置发生变化，请刷新后重试",
            ),
        }
        code, message = messages.get(
            str(exc),
            ("classification_failed", "归类纠正失败，请稍后重试"),
        )
        raise BrowserClassificationError(409, code, message) from exc
