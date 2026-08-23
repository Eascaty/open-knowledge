"""Project-local background manager for automatic ingestion and site preview."""

from __future__ import annotations

import functools
import hashlib
import html
import http.client
import json
import os
import secrets
import signal
import socket
import threading
import time
import traceback
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from .automation import AutomationResult, run_full_pipeline
from .config import ProjectPaths, atomic_write_json, initialize_layout


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
STATUS_PATH = "/__knowledge/status"
STOP_PATH = "/__knowledge/stop"
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
            "jobs_claimed",
            "jobs_completed",
            "jobs_retried",
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


class _ManagerHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    manager: "LocalKnowledgeManager"


class _ManagerRequestHandler(SimpleHTTPRequestHandler):
    server_version = "PersonalKnowledgeManager/1"

    @property
    def manager(self) -> "LocalKnowledgeManager":
        return self.server.manager  # type: ignore[attr-defined]

    def translate_path(self, path: str) -> str:
        translated = Path(super().translate_path(path))
        site_root = (self.manager.paths.site_dir / "dist").resolve()
        try:
            translated.resolve().relative_to(site_root)
        except (OSError, ValueError):
            return str(site_root / ".outside-site-is-not-served")
        return str(translated)

    def _send_json(self, status: int, value: Mapping[str, Any]) -> None:
        payload = (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(payload)

    def _send_waiting_page(self) -> None:
        status = self.manager.public_state()
        message = "正在第一次整理资料，完成后本页会自动显示知识库。"
        if status.get("last_error"):
            message = str(status["last_error"])
        payload = (
            "<!doctype html><html lang='zh-CN'><head>"
            "<meta charset='utf-8'><meta name='viewport' "
            "content='width=device-width,initial-scale=1'>"
            "<meta http-equiv='refresh' content='2'>"
            "<title>知识库正在准备</title>"
            "<style>body{margin:0;min-height:100vh;display:grid;place-items:center;"
            "font:16px/1.7 -apple-system,BlinkMacSystemFont,sans-serif;"
            "background:#f5f3ee;color:#202520}.card{max-width:34rem;margin:2rem;"
            "padding:2rem;border-radius:1.2rem;background:#fff;"
            "box-shadow:0 1rem 3rem #20252018}h1{font-size:1.4rem}</style>"
            "</head><body><main class='card'><h1>知识库正在准备</h1><p>%s</p>"
            "<p>你可以保留这个页面，无需重复启动。</p></main></body></html>"
        ) % html.escape(message)
        payload = payload.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'",
        )
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urllib.parse.urlsplit(self.path).path
        if path == STATUS_PATH:
            self._send_json(200, self.manager.public_state())
            return
        if path == "/" and not (
            self.manager.paths.site_dir / "dist" / "index.html"
        ).is_file():
            self._send_waiting_page()
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urllib.parse.urlsplit(self.path).path
        if path != STOP_PATH:
            self._send_json(404, {"ok": False, "error": "not_found"})
            return
        supplied = self.headers.get("X-Knowledge-Token", "")
        if not self.manager.authorized(supplied):
            self._send_json(403, {"ok": False, "error": "forbidden"})
            return
        self.manager.request_stop()
        self._send_json(202, {"ok": True, "status": "stopping"})

    def end_headers(self) -> None:
        if urllib.parse.urlsplit(self.path).path not in {STATUS_PATH, STOP_PATH}:
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        status = str(args[1]) if len(args) > 1 else ""
        if self.command not in {"GET", "HEAD"} or status.startswith(("4", "5")):
            super().log_message(format, *args)


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
        self.server: Optional[_ManagerHttpServer] = None
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

    def request_stop(self) -> None:
        self._save(status="stopping")
        self.stop_event.set()
        if self.server is not None:
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _run_pipeline(self, fingerprint: str) -> bool:
        self._save(status="updating", last_attempt_at=_utc_now(), last_error=None)
        try:
            result = self.pipeline_runner(self.paths.root, visibility="private")
            if not result.ok:
                raise ManagerError("pipeline checks did not pass")
        except Exception as exc:
            traceback.print_exc()
            self._save(
                status=(
                    "running"
                    if (self.paths.site_dir / "dist" / "index.html").is_file()
                    else "degraded"
                ),
                last_error=_safe_error(exc),
            )
            return False
        self._save(
            status="running",
            last_success_at=_utc_now(),
            last_error=None,
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
        handler = functools.partial(
            _ManagerRequestHandler,
            directory=str(self.paths.site_dir / "dist"),
        )
        server = _ManagerHttpServer((DEFAULT_HOST, self.port), handler)
        server.manager = self
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
