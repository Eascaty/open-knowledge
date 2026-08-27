"""Safe project-local file receipt for the browser inbox."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import unicodedata
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO, Dict, Mapping, Optional, Sequence, Tuple

from .config import ProjectPaths, load_runtime
from .processing.extraction import TEXT_EXTENSIONS
from .storage.sqlite import connect


SESSION_PATH = "/__knowledge/session"
INBOX_STATUS_PATH = "/__knowledge/inbox"
UPLOAD_PATH = "/__knowledge/upload"
UPLOAD_HEADER = "X-Knowledge-Session"
FILENAME_HEADER = "X-Knowledge-Filename"
HISTORY_LIMIT = 24
UPLOAD_CHUNK_BYTES = 1024 * 1024
MAX_BROWSER_UPLOAD_BYTES = 64 * 1024 * 1024
SUPPORTED_UPLOAD_EXTENSIONS = frozenset(TEXT_EXTENSIONS | {".docx", ".pdf"})


class InboxUploadError(RuntimeError):
    """A safe, user-visible upload rejection."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class UploadReceipt:
    upload_id: str
    filename: str
    size_bytes: int
    sha256: str
    received_at: str
    status: str = "queued"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def max_upload_bytes(paths: ProjectPaths) -> int:
    runtime = load_runtime(paths)
    configured_mb = int(runtime.get("pipeline", {}).get("max_file_mb", 512))
    return min(MAX_BROWSER_UPLOAD_BYTES, max(1, configured_mb) * 1024 * 1024)


def accepted_extensions() -> Tuple[str, ...]:
    extensions = set(SUPPORTED_UPLOAD_EXTENSIONS)
    if shutil.which("pdftotext") is None:
        extensions.discard(".pdf")
    return tuple(sorted(extensions))


def parse_content_length(value: str, maximum: int) -> int:
    if not value:
        raise InboxUploadError(411, "length_required", "无法确认文件大小")
    if not value.isascii() or not value.isdigit():
        raise InboxUploadError(400, "invalid_length", "文件大小格式无效")
    try:
        length = int(value)
    except (TypeError, ValueError) as exc:
        raise InboxUploadError(400, "invalid_length", "文件大小格式无效") from exc
    if length <= 0:
        raise InboxUploadError(400, "empty_file", "不能投入空文件")
    if length > maximum:
        raise InboxUploadError(
            413,
            "file_too_large",
            "文件超过本知识库允许的大小",
        )
    return length


def _decode_filename(encoded: str) -> str:
    if not encoded or len(encoded) > 2048:
        raise InboxUploadError(400, "invalid_filename", "文件名无效")
    if re.search(r"%(?![0-9a-fA-F]{2})", encoded):
        raise InboxUploadError(400, "invalid_filename", "文件名编码无效")
    try:
        decoded = urllib.parse.unquote_to_bytes(encoded).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InboxUploadError(400, "invalid_filename", "文件名编码无效") from exc
    return unicodedata.normalize("NFC", decoded)


def _trim_utf8_stem(stem: str, suffix: str, limit: int = 180) -> str:
    while stem and len((stem + suffix).encode("utf-8")) > limit:
        stem = stem[:-1]
    if not stem:
        raise InboxUploadError(400, "invalid_filename", "文件名过长或无效")
    return stem + suffix


def safe_upload_filename(
    encoded: str,
    *,
    allowed_extensions: Optional[Sequence[str]] = None,
) -> str:
    decoded = _decode_filename(encoded)
    if decoded in {".", ".."} or "/" in decoded or "\\" in decoded:
        raise InboxUploadError(400, "invalid_filename", "文件名不能包含路径")
    if any(ord(character) < 32 or ord(character) == 127 for character in decoded):
        raise InboxUploadError(400, "invalid_filename", "文件名包含无效字符")
    cleaned = decoded.replace(":", "-").strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned or cleaned.startswith("."):
        raise InboxUploadError(400, "invalid_filename", "隐藏文件或空文件名不能投入")
    suffix = Path(cleaned).suffix.casefold()
    allowed = (
        SUPPORTED_UPLOAD_EXTENSIONS
        if allowed_extensions is None
        else frozenset(allowed_extensions)
    )
    if (allowed_extensions is not None and suffix not in allowed) or (
        allowed_extensions is None and suffix and suffix not in allowed
    ):
        raise InboxUploadError(
            415,
            "unsupported_type",
            "暂不支持这种文件类型",
        )
    original_suffix = Path(cleaned).suffix
    stem = cleaned[: -len(original_suffix)] if original_suffix else cleaned
    return _trim_utf8_stem(stem, original_suffix)


def public_upload(upload: Mapping[str, Any]) -> Dict[str, Any]:
    fields = (
        "upload_id",
        "filename",
        "size_bytes",
        "received_at",
        "status",
        "started_at",
        "completed_at",
        "updated_at",
        "revision",
    )
    result = {field: upload.get(field) for field in fields if field in upload}
    error = upload.get("error")
    if isinstance(error, Mapping):
        code = error.get("code")
        if code == "processing_failed":
            result["error"] = {
                "code": code,
                "retryable": bool(error.get("retryable", False)),
            }
    outcome = upload.get("result")
    if isinstance(outcome, Mapping):
        document = outcome.get("document")
        revision = outcome.get("site_revision")
        kind = outcome.get("outcome")
        if (
            isinstance(document, Mapping)
            and isinstance(document.get("id"), str)
            and isinstance(revision, str)
            and kind in {"created", "duplicate"}
        ):
            path = document.get("path")
            result["result"] = {
                "outcome": kind,
                "site_revision": revision,
                "document": {
                    "id": document["id"],
                    "title": document.get("title", "")
                    if isinstance(document.get("title", ""), str)
                    else "",
                    "node_id": document.get("node_id")
                    if isinstance(document.get("node_id"), str)
                    else None,
                    "path": [part for part in path if isinstance(part, str)]
                    if isinstance(path, list)
                    else [],
                },
            }
    return result


def normalized_upload_history(value: Any) -> list[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        upload = dict(item)
        if upload.get("status") == "processing":
            upload["status"] = "queued"
            upload.pop("attempt_id", None)
            upload.pop("started_at", None)
        normalized.append(upload)
    return limited_upload_history(normalized)


def limited_upload_history(value: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Keep every actionable receipt plus a bounded terminal history."""

    terminal = 0
    result = []
    for upload in value:
        error = upload.get("error")
        is_terminal = upload.get("status") == "completed" or (
            upload.get("status") == "failed"
            and isinstance(error, Mapping)
            and error.get("retryable") is False
        )
        if is_terminal:
            if terminal >= HISTORY_LIMIT:
                continue
            terminal += 1
        result.append(upload)
    return result


def source_known(paths: ProjectPaths, digest: str) -> bool:
    if not paths.database_file.is_file():
        return False
    connection = None
    try:
        connection = connect(paths.database_file)
        row = connection.execute(
            "SELECT 1 FROM sources WHERE sha256 = ?", (digest,)
        ).fetchone()
        return row is not None
    except Exception:
        return False
    finally:
        if connection is not None:
            connection.close()


def site_revision(paths: ProjectPaths) -> Optional[str]:
    metadata_path = paths.site_dir / "dist" / "build-meta.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    revision = metadata.get("content_digest") if isinstance(metadata, dict) else None
    return revision if isinstance(revision, str) and revision else None


def upload_outcomes(
    paths: ProjectPaths, uploads: Mapping[str, str]
) -> Dict[str, Dict[str, Any]]:
    """Classify uploads from the published site and durable job states."""

    outcomes: Dict[str, Dict[str, Any]] = {}
    revision = site_revision(paths)
    published: Dict[str, Dict[str, Any]] = {}
    if revision is not None:
        data_path = paths.site_dir / "dist" / "data" / "site-data.json"
        try:
            site_data = json.loads(data_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            site_data = {}
        documents = site_data.get("documents") if isinstance(site_data, dict) else []
        if isinstance(documents, list):
            for document in documents:
                if not isinstance(document, Mapping):
                    continue
                source = document.get("source")
                digest = source.get("sha256") if isinstance(source, Mapping) else None
                document_id = document.get("id")
                if not isinstance(digest, str) or not isinstance(document_id, str):
                    continue
                path = document.get("path")
                candidate = {
                    "id": document_id,
                    "title": document.get("title", "")
                    if isinstance(document.get("title", ""), str)
                    else "",
                    "node_id": document.get("node_id")
                    if isinstance(document.get("node_id"), str)
                    else None,
                    "path": [part for part in path if isinstance(part, str)]
                    if isinstance(path, list)
                    else [],
                }
                source_id = document.get("source_id")
                if digest not in published or (
                    isinstance(source_id, str) and document_id == source_id
                ):
                    # A long source may create several cards. The first card
                    # keeps the source ID and is the stable landing page.
                    published[digest] = candidate

    terminal_digests = set()
    if paths.database_file.is_file():
        connection = None
        try:
            connection = connect(paths.database_file)
            for digest in set(uploads.values()):
                row = connection.execute(
                    """
                    SELECT 1 FROM sources AS s
                    JOIN jobs AS j ON j.source_id = s.id
                    WHERE s.sha256 = ? AND j.status = 'failed'
                    LIMIT 1
                    """,
                    (digest,),
                ).fetchone()
                if row is not None:
                    terminal_digests.add(digest)
        except Exception:
            terminal_digests.clear()
        finally:
            if connection is not None:
                connection.close()

    for upload_id, digest in uploads.items():
        if digest in published and revision is not None:
            outcomes[upload_id] = {
                "status": "completed",
                "site_revision": revision,
                "document": published[digest],
            }
        else:
            outcomes[upload_id] = {
                "status": "failed",
                "retryable": digest not in terminal_digests,
            }
    return outcomes


class InboxUploadStore:
    """Stream browser uploads into the private inbox without overwriting."""

    def __init__(
        self,
        paths: ProjectPaths,
        maximum_bytes: int,
        allowed_extensions: Optional[Sequence[str]] = None,
    ) -> None:
        self.paths = paths
        self.maximum_bytes = maximum_bytes
        configured = (
            accepted_extensions()
            if allowed_extensions is None
            else tuple(allowed_extensions)
        )
        if not set(configured).issubset(SUPPORTED_UPLOAD_EXTENSIONS):
            raise ValueError("unsupported browser upload extension")
        self.accepted_extensions = tuple(sorted(set(configured)))
        self.slots = threading.BoundedSemaphore(2)

    def _private_directory(self, path: Path, boundary: Path) -> Path:
        try:
            project = self.paths.root.resolve()
            resolved_boundary = boundary.resolve()
            resolved_boundary.relative_to(project)
            path.parent.resolve(strict=False).relative_to(resolved_boundary)
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.is_symlink():
                raise ValueError("symbolic link")
            path.resolve().relative_to(resolved_boundary)
            path.chmod(0o700)
        except (OSError, RuntimeError, ValueError) as exc:
            raise InboxUploadError(
                500, "upload_failed", "无法准备私密投放目录"
            ) from exc
        return path

    def _staging_directory(self, upload_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", upload_id):
            raise InboxUploadError(400, "upload_conflict", "投放标识无效")
        root = self._private_directory(
            self.paths.data_dir / "uploads" / "staging",
            self.paths.data_dir,
        )
        candidate = root / upload_id
        candidate.mkdir(mode=0o700)
        return candidate

    def receive(
        self,
        stream: BinaryIO,
        *,
        encoded_filename: str,
        content_length: str,
        upload_id: str,
        received_at: str,
    ) -> UploadReceipt:
        if not self.slots.acquire(blocking=False):
            raise InboxUploadError(429, "upload_busy", "正在接收其他文件，请稍后重试")
        staging = None
        try:
            filename = safe_upload_filename(
                encoded_filename,
                allowed_extensions=self.accepted_extensions,
            )
            length = parse_content_length(content_length, self.maximum_bytes)
            staging = self._staging_directory(upload_id)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix="content-", suffix=".part", dir=str(staging)
            )
            temporary = Path(temporary_name)
            digest = hashlib.sha256()
            try:
                with os.fdopen(descriptor, "wb") as target:
                    remaining = length
                    while remaining:
                        chunk = stream.read(min(UPLOAD_CHUNK_BYTES, remaining))
                        if not chunk:
                            raise InboxUploadError(
                                400,
                                "incomplete_upload",
                                "文件传输未完成，请重新投入",
                            )
                        if len(chunk) > remaining:
                            raise InboxUploadError(
                                400,
                                "invalid_length",
                                "文件传输长度与声明不一致",
                            )
                        target.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, staging / filename)
            finally:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

            inbox_root = self._private_directory(
                self.paths.inbox_dir / "files",
                self.paths.inbox_dir,
            )
            destination = inbox_root / upload_id
            if destination.exists() or destination.is_symlink():
                raise InboxUploadError(
                    409, "upload_conflict", "投放标识发生冲突，请重试"
                )
            os.replace(staging, destination)
            staging = None
            return UploadReceipt(
                upload_id=upload_id,
                filename=filename,
                size_bytes=length,
                sha256=digest.hexdigest(),
                received_at=received_at,
            )
        finally:
            if staging is not None and staging.exists():
                for child in staging.iterdir():
                    child.unlink()
                staging.rmdir()
            self.slots.release()
