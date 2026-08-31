"""Project-local background manager for automatic ingestion and site preview."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import secrets
import signal
import socket
import threading
import time
import traceback
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, Mapping, Optional, Sequence

from .automation import AutomationResult, run_full_pipeline
from .config import ProjectPaths, atomic_write_json, initialize_layout
from .local_http import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    STATUS_PATH,
    STOP_PATH,
    ManagerHttpServer,
    create_manager_server,
)
from .local_inbox import (
    HISTORY_LIMIT,
    InboxUploadStore,
    database_work_pending,
    limited_upload_history,
    max_upload_bytes,
    normalized_upload_history,
    public_upload,
    source_known,
    upload_outcomes,
)


SERVICE_NAME = "personal-knowledge-manager"


class ManagerError(RuntimeError):
    """Raised when the local manager cannot be safely controlled."""


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _state_path(paths: ProjectPaths) -> Path:
    return paths.state_dir / "local-manager.json"


def _log_path(paths: ProjectPaths) -> Path:
    return paths.runtime_logs_dir / "local-manager.log"


def _read_state(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, dict(state))
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _safe_error(exception: BaseException) -> str:
    return "{}: 本地自动处理失败，详情只保存在私密日志中".format(
        type(exception).__name__
    )


def _public_state(state: Mapping[str, Any]) -> Dict[str, Any]:
    allowed = (
        "service",
        "instance_id",
        "pid",
        "status",
        "host",
        "port",
        "url",
        "started_at",
        "stopped_at",
        "last_attempt_at",
        "last_success_at",
        "last_error",
        "last_result",
        "successful_inbox_fingerprint",
    )
    result = {key: state.get(key) for key in allowed if key in state}
    last_result = result.get("last_result")
    if isinstance(last_result, Mapping):
        public_result_keys = (
            "ok",
            "ingested",
            "duplicates",
            "ingest_failed",
            "jobs_claimed",
            "jobs_completed",
            "jobs_retried",
            "jobs_pending",
            "jobs_failed",
            "documents",
            "gate_allowed",
            "health_status",
            "network_requests",
        )
        result["last_result"] = {
            key: last_result.get(key)
            for key in public_result_keys
            if key in last_result
        }
    result["ok"] = state.get("status") in {"running", "updating"}
    return result


def inbox_fingerprint(paths: ProjectPaths) -> str:
    """Return a cheap, deterministic fingerprint after ignoring control files."""

    digest = hashlib.sha256()
    if not paths.inbox_dir.exists():
        return digest.hexdigest()
    ignored_root_files = {"readme.md", "urls.txt"}
    rows = []
    for path in paths.inbox_dir.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if (
            path.parent == paths.inbox_dir
            and path.name.casefold() in ignored_root_files
        ):
            continue
        try:
            stat = path.stat()
            relative = path.relative_to(paths.inbox_dir).as_posix()
        except (OSError, RuntimeError, ValueError):
            continue
        rows.append((relative, stat.st_size, stat.st_mtime_ns))
    for relative, size, modified in sorted(rows):
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(modified).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _request_json(
    host: str,
    port: int,
    path: str,
    *,
    method: str = "GET",
    token: Optional[str] = None,
    timeout: float = 1.0,
) -> Optional[Dict[str, Any]]:
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["X-Knowledge-Token"] = token
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        payload = response.read(1024 * 1024)
        if response.status >= 400:
            return None
        value = json.loads(payload.decode("utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    finally:
        connection.close()


def _probe_state(state: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    instance_id = state.get("instance_id")
    port = state.get("port")
    if not isinstance(instance_id, str) or not isinstance(port, int) or port <= 0:
        return None
    result = _request_json(DEFAULT_HOST, port, STATUS_PATH)
    if result is None or result.get("instance_id") != instance_id:
        return None
    return result


def _port_is_open(host: str, port: int) -> bool:
    if port <= 0:
        return False
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


class LocalKnowledgeManager:
    """Serve the last successful site while watching the private inbox."""

    def __init__(
        self,
        root: Path,
        *,
        instance_id: str,
        control_token: str,
        port: int = DEFAULT_PORT,
        poll_seconds: float = 2.0,
        settle_seconds: float = 3.0,
        retry_seconds: float = 30.0,
        pipeline_runner: Callable[..., AutomationResult] = run_full_pipeline,
    ) -> None:
        self.paths = ProjectPaths.from_root(root)
        self.instance_id = instance_id
        self.control_token = control_token
        self.port = port
        self.poll_seconds = max(0.05, poll_seconds)
        self.settle_seconds = max(0.0, settle_seconds)
        self.retry_seconds = max(0.1, retry_seconds)
        self.pipeline_runner = pipeline_runner
        self.stop_event = threading.Event()
        self.state_lock = threading.Lock()
        self.server: Optional[ManagerHttpServer] = None
        self.browser_token = secrets.token_urlsafe(32)
        self.inbox_store: Optional[InboxUploadStore] = None
        previous = _read_state(_state_path(self.paths))
        self.state: Dict[str, Any] = {
            "service": SERVICE_NAME,
            "instance_id": instance_id,
            "control_token": control_token,
            "pid": os.getpid(),
            "status": "starting",
            "host": DEFAULT_HOST,
            "port": port,
            "url": "http://{}:{}/".format(DEFAULT_HOST, port),
            "started_at": _utc_now(),
            "last_success_at": previous.get("last_success_at"),
            "last_result": previous.get("last_result"),
            "successful_inbox_fingerprint": previous.get(
                "successful_inbox_fingerprint"
            ),
            "recent_uploads": normalized_upload_history(
                previous.get("recent_uploads")
            ),
        }

    def _save(self, **updates: Any) -> None:
        with self.state_lock:
            self.state.update(updates)
            _write_state(_state_path(self.paths), self.state)

    def public_state(self) -> Dict[str, Any]:
        with self.state_lock:
            return _public_state(self.state)

    def authorized(self, supplied: str) -> bool:
        return bool(supplied) and secrets.compare_digest(
            supplied, self.control_token
        )

    def authorized_browser(self, supplied: str) -> bool:
        return bool(supplied) and secrets.compare_digest(
            supplied, self.browser_token
        )

    def browser_session(self) -> Dict[str, Any]:
        store = self.inbox_store
        if store is None:
            raise ManagerError("browser inbox is not initialized")
        return {
            "ok": True,
            "schema_version": 1,
            "api_version": "browser-v1",
            "service": SERVICE_NAME,
            "session_token": self.browser_token,
            "capabilities": {
                "file_upload": True,
                "maximum_file_bytes": store.maximum_bytes,
                "accepted_extensions": list(store.accepted_extensions),
                "history_limit": HISTORY_LIMIT,
            },
        }

    def inbox_status(self) -> Dict[str, Any]:
        with self.state_lock:
            uploads = [
                public_upload(item)
                for item in self.state.get("recent_uploads", [])
                if isinstance(item, Mapping)
            ]
            manager = {
                "status": str(self.state.get("status", "unknown")),
                "last_success_at": self.state.get("last_success_at"),
                "last_error": self.state.get("last_error"),
            }
        return {
            "ok": True,
            "schema_version": 1,
            "service": SERVICE_NAME,
            "manager_status": manager["status"],
            "manager": manager,
            "uploads": uploads,
            "poll_after_ms": max(400, min(2000, int(self.poll_seconds * 1000))),
        }

    def receive_browser_upload(
        self,
        stream: BinaryIO,
        *,
        encoded_filename: str,
        content_length: str,
    ) -> Dict[str, Any]:
        store = self.inbox_store
        if store is None:
            raise ManagerError("browser inbox is not initialized")
        received_at = _utc_now()
        receipt = store.receive(
            stream,
            encoded_filename=encoded_filename,
            content_length=content_length,
            upload_id=secrets.token_hex(16),
            received_at=received_at,
        )
        upload = receipt.to_dict()
        known_before = source_known(self.paths, receipt.sha256)
        with self.state_lock:
            history = list(self.state.get("recent_uploads", []))
            known_before = known_before or any(
                isinstance(item, Mapping) and item.get("sha256") == receipt.sha256
                for item in history
            )
            upload.update(
                {
                    "known_before": known_before,
                    "revision": 1,
                    "updated_at": received_at,
                }
            )
            self.state["recent_uploads"] = limited_upload_history(
                [upload] + history
            )
            _write_state(_state_path(self.paths), self.state)
        return public_upload(upload)

    def _begin_upload_attempt(self, attempt_id: str) -> list[str]:
        started_at = _utc_now()
        with self.state_lock:
            history = list(self.state.get("recent_uploads", []))
            selected = []
            for upload in history:
                if not isinstance(upload, dict):
                    continue
                status = upload.get("status")
                error = upload.get("error")
                retryable_failure = (
                    status == "failed"
                    and isinstance(error, Mapping)
                    and error.get("retryable") is True
                )
                if status != "queued" and not retryable_failure:
                    continue
                upload_id = upload.get("upload_id")
                if not isinstance(upload_id, str):
                    continue
                selected.append(upload_id)
                upload.update(
                    {
                        "status": "processing",
                        "attempt_id": attempt_id,
                        "started_at": started_at,
                        "updated_at": started_at,
                        "revision": int(upload.get("revision", 0)) + 1,
                    }
                )
                upload.pop("completed_at", None)
                upload.pop("error", None)
                upload.pop("result", None)
            self.state["recent_uploads"] = history
            _write_state(_state_path(self.paths), self.state)
        return selected

    def _settle_upload_attempt(self, upload_ids: Sequence[str]) -> Dict[str, int]:
        selected = set(upload_ids)
        with self.state_lock:
            candidates = {
                str(upload.get("upload_id")): str(upload.get("sha256"))
                for upload in self.state.get("recent_uploads", [])
                if isinstance(upload, Mapping)
                and upload.get("upload_id") in selected
                and isinstance(upload.get("upload_id"), str)
                and isinstance(upload.get("sha256"), str)
            }
        outcomes = upload_outcomes(self.paths, candidates)
        counts = {"completed": 0, "permanent": 0, "retryable": 0}
        settled_at = _utc_now()
        with self.state_lock:
            history = list(self.state.get("recent_uploads", []))
            for upload in history:
                if (
                    not isinstance(upload, dict)
                    or upload.get("upload_id") not in selected
                ):
                    continue
                outcome = outcomes.get(
                    upload["upload_id"],
                    {"status": "failed", "retryable": True},
                )
                upload["updated_at"] = settled_at
                upload["revision"] = int(upload.get("revision", 0)) + 1
                upload.pop("attempt_id", None)
                if outcome["status"] == "completed":
                    upload.update(
                        {
                            "status": "completed",
                            "completed_at": settled_at,
                            "result": {
                                "outcome": (
                                    "duplicate"
                                    if upload.get("known_before")
                                    else "created"
                                ),
                                "site_revision": outcome["site_revision"],
                                "document": outcome["document"],
                            },
                        }
                    )
                    upload.pop("error", None)
                    counts["completed"] += 1
                else:
                    retryable = bool(outcome.get("retryable", True))
                    upload.update(
                        {
                            "status": "failed",
                            "error": {
                                "code": "processing_failed",
                                "retryable": retryable,
                            },
                        }
                    )
                    upload.pop("completed_at", None)
                    upload.pop("result", None)
                    counts["retryable" if retryable else "permanent"] += 1
            self.state["recent_uploads"] = limited_upload_history(history)
            _write_state(_state_path(self.paths), self.state)
        return counts

    def request_stop(self) -> None:
        self._save(status="stopping")
        self.stop_event.set()
        if self.server is not None:
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _run_pipeline(self, fingerprint: str) -> bool:
        attempt_id = secrets.token_hex(12)
        upload_ids = self._begin_upload_attempt(attempt_id)
        self._save(status="updating", last_attempt_at=_utc_now(), last_error=None)
        settled = False
        try:
            result = self.pipeline_runner(self.paths.root, visibility="private")
            settlement = self._settle_upload_attempt(upload_ids)
            settled = True
            jobs_pending = int(
                getattr(result, "jobs_pending", result.jobs_retried)
            )
            raw_ingest_failed = getattr(result, "ingest_failed", 0)
            ingest_failed = (
                int(raw_ingest_failed)
                if isinstance(raw_ingest_failed, int)
                and not isinstance(raw_ingest_failed, bool)
                else 0
            )
            terminal_failures = int(result.jobs_failed) + ingest_failed
            checks_passed = result.gate_allowed and result.health_status != "FAIL"
            if (
                settlement["retryable"]
                or jobs_pending
                or not checks_passed
            ):
                raise ManagerError("pipeline has unfinished work or failed checks")
            if (
                not result.ok
                and terminal_failures == 0
                and not settlement["permanent"]
            ):
                raise ManagerError("pipeline checks did not pass")
        except Exception as exc:
            traceback.print_exc()
            if not settled:
                self._settle_upload_attempt(upload_ids)
            self._save(
                status=(
                    "running"
                    if (self.paths.site_dir / "dist" / "index.html").is_file()
                    else "degraded"
                ),
                last_error=_safe_error(exc),
            )
            return False
        isolated = terminal_failures + int(settlement["permanent"])
        self._save(
            status="degraded" if isolated else "running",
            last_success_at=_utc_now(),
            last_error=(
                "有 {} 项资料已隔离；其他成功资料仍可使用。".format(isolated)
                if isolated
                else None
            ),
            last_result=result.to_dict(),
            successful_inbox_fingerprint=fingerprint,
        )
        return True

    def _watch_loop(self) -> None:
        candidate: Optional[str] = None
        candidate_since = 0.0
        next_retry_at = 0.0
        while not self.stop_event.is_set():
            current = inbox_fingerprint(self.paths)
            with self.state_lock:
                successful = self.state.get("successful_inbox_fingerprint")
            site_exists = (self.paths.site_dir / "dist" / "index.html").is_file()
            pending = not site_exists or current != successful
            if not pending:
                pending = database_work_pending(self.paths)
            now = time.monotonic()
            if pending:
                if current != candidate:
                    candidate = current
                    candidate_since = now
                elif now - candidate_since >= self.settle_seconds and now >= next_retry_at:
                    if self._run_pipeline(current):
                        candidate = None
                        next_retry_at = 0.0
                    else:
                        next_retry_at = now + self.retry_seconds
            else:
                candidate = None
                next_retry_at = 0.0
            self.stop_event.wait(self.poll_seconds)

    def serve(self) -> None:
        initialize_layout(self.paths)
        self.inbox_store = InboxUploadStore(
            self.paths, max_upload_bytes(self.paths)
        )
        server = create_manager_server(
            self, self.port, self.paths.site_dir / "dist"
        )
        self.server = server
        actual_port = int(server.server_address[1])
        self.port = actual_port
        self._save(
            status="running",
            port=actual_port,
            url="http://{}:{}/".format(DEFAULT_HOST, actual_port),
        )
        watcher = threading.Thread(
            target=self._watch_loop,
            name="knowledge-inbox-watcher",
            daemon=False,
        )
        watcher.start()

        def stop_from_signal(_signum: int, _frame: Any) -> None:
            self.request_stop()

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, stop_from_signal)
            signal.signal(signal.SIGINT, stop_from_signal)
        try:
            server.serve_forever(poll_interval=0.25)
        finally:
            self.stop_event.set()
            watcher.join()
            server.server_close()
            self._save(status="stopped", stopped_at=_utc_now())
