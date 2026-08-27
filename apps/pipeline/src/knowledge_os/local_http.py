"""Loopback-only HTTP surface for the local knowledge manager."""

from __future__ import annotations

import functools
import html
import ipaddress
import json
import traceback
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO, Dict, Mapping, Protocol

from .local_inbox import (
    FILENAME_HEADER,
    INBOX_STATUS_PATH,
    SESSION_PATH,
    UPLOAD_HEADER,
    UPLOAD_PATH,
    InboxUploadError,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
STATUS_PATH = "/__knowledge/status"
STOP_PATH = "/__knowledge/stop"
SESSION_REQUEST_HEADER = "X-Knowledge-Client"


class ManagerHttpApi(Protocol):
    port: int
    paths: Any

    def public_state(self) -> Dict[str, Any]: ...

    def browser_session(self) -> Dict[str, Any]: ...

    def inbox_status(self) -> Dict[str, Any]: ...

    def authorized(self, supplied: str) -> bool: ...

    def authorized_browser(self, supplied: str) -> bool: ...

    def receive_browser_upload(
        self,
        stream: BinaryIO,
        *,
        encoded_filename: str,
        content_length: str,
    ) -> Dict[str, Any]: ...

    def request_stop(self) -> None: ...


class ManagerHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    manager: ManagerHttpApi


class ManagerRequestHandler(SimpleHTTPRequestHandler):
    server_version = "PersonalKnowledgeManager/2"

    def send_response(self, code: int, message: Any = None) -> None:
        self._cache_control_sent = False
        super().send_response(code, message)

    @property
    def manager(self) -> ManagerHttpApi:
        return self.server.manager  # type: ignore[attr-defined]

    def _valid_host(self) -> bool:
        try:
            address = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        if not address.is_loopback:
            return False
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1:
            return False
        host = hosts[0].casefold()
        allowed = {
            "{}:{}".format(DEFAULT_HOST, self.manager.port),
            "localhost:{}".format(self.manager.port),
        }
        return host in allowed

    def _valid_browser_context(self) -> bool:
        if not self._valid_host():
            return False
        allowed_origins = {
            "http://{}:{}".format(DEFAULT_HOST, self.manager.port),
            "http://localhost:{}".format(self.manager.port),
        }
        if self.headers.get("Origin") not in allowed_origins:
            return False
        fetch_site = self.headers.get("Sec-Fetch-Site")
        return fetch_site in {None, "same-origin"}

    def translate_path(self, path: str) -> str:
        translated = Path(super().translate_path(path))
        site_root = (self.manager.paths.site_dir / "dist").resolve()
        try:
            translated.resolve().relative_to(site_root)
        except (OSError, RuntimeError, ValueError):
            return str(site_root / ".outside-site-is-not-served")
        return str(translated)

    def _send_json(self, status: int, value: Mapping[str, Any]) -> None:
        payload = (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "private, no-store, max-age=0")
        self._cache_control_sent = True
        self.end_headers()
        self.wfile.write(payload)

    def _reject(self, status: int, code: str, message: str) -> None:
        self.close_connection = True
        self._send_json(
            status,
            {"ok": False, "error": {"code": code, "message": message}},
        )

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
        encoded = payload.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(encoded)

    def _host_or_reject(self) -> bool:
        if self._valid_host():
            return True
        self._reject(421, "bad_host", "请求地址不属于本地知识库")
        return False

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler API
        if self._host_or_reject():
            super().do_HEAD()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if not self._host_or_reject():
            return
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

    def _browser_authorized(self) -> bool:
        supplied = self.headers.get(UPLOAD_HEADER, "")
        return self._valid_browser_context() and self.manager.authorized_browser(
            supplied
        )

    def _receive_upload(self) -> None:
        if not self._browser_authorized():
            self._reject(403, "forbidden", "本地投放授权无效")
            return
        if self.headers.get("Transfer-Encoding"):
            self._reject(400, "invalid_length", "不接受分块文件传输")
            return
        lengths = self.headers.get_all("Content-Length", [])
        if not lengths:
            self._reject(411, "length_required", "无法确认文件大小")
            return
        if len(lengths) != 1 or "," in lengths[0]:
            self._reject(400, "invalid_length", "文件大小格式无效")
            return
        filenames = self.headers.get_all(FILENAME_HEADER, [])
        if len(filenames) != 1:
            self._reject(400, "invalid_filename", "文件名无效")
            return
        if self.headers.get("Content-Encoding"):
            self._reject(415, "unsupported_media", "不接受压缩的传输正文")
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].casefold() != (
            "application/octet-stream"
        ):
            self._reject(415, "unsupported_media", "文件传输格式无效")
            return
        try:
            self.connection.settimeout(30.0)
            receipt = self.manager.receive_browser_upload(
                self.rfile,
                encoded_filename=filenames[0],
                content_length=lengths[0],
            )
        except InboxUploadError as exc:
            self._reject(exc.status, exc.code, exc.message)
            return
        except (OSError, ValueError):
            traceback.print_exc()
            self._reject(500, "upload_failed", "文件投放失败，请稍后重试")
            return
        self._send_json(202, {"ok": True, "upload": receipt})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urllib.parse.urlsplit(self.path).path
        if path == SESSION_PATH:
            if not self._valid_browser_context() or self.headers.get(
                SESSION_REQUEST_HEADER
            ) != "browser-v1":
                self._reject(403, "forbidden", "本地投放会话不可用")
                return
            self._send_json(200, self.manager.browser_session())
            return
        if path == INBOX_STATUS_PATH:
            if not self._browser_authorized():
                self._reject(403, "forbidden", "本地投放授权无效")
                return
            self._send_json(200, self.manager.inbox_status())
            return
        if path == UPLOAD_PATH:
            self._receive_upload()
            return
        if path == STOP_PATH:
            if not self._host_or_reject():
                return
            supplied = self.headers.get("X-Knowledge-Token", "")
            if not self.manager.authorized(supplied):
                self._reject(403, "forbidden", "本地控制授权无效")
                return
            self.manager.request_stop()
            self._send_json(202, {"ok": True, "status": "stopping"})
            return
        self._reject(404, "not_found", "接口不存在")

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
        self._reject(405, "method_not_allowed", "不接受跨来源预检")

    def end_headers(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        decoded_path = urllib.parse.unquote(path).casefold()
        private_path = (
            decoded_path in {"/data", "/build-meta.json", "/__knowledge"}
            or decoded_path.startswith(("/data/", "/__knowledge/"))
        )
        if not getattr(self, "_cache_control_sent", False):
            self.send_header(
                "Cache-Control",
                (
                    "private, no-store, max-age=0"
                    if private_path
                    else "no-cache, must-revalidate"
                ),
            )
        if not (path == "/" and not (
            self.manager.paths.site_dir / "dist" / "index.html"
        ).is_file()):
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
            )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        status = str(args[1]) if len(args) > 1 else ""
        if self.command not in {"GET", "HEAD"} or status.startswith(("4", "5")):
            super().log_message(format, *args)


def create_manager_server(
    manager: ManagerHttpApi, port: int, directory: Path
) -> ManagerHttpServer:
    handler = functools.partial(ManagerRequestHandler, directory=str(directory))
    server = ManagerHttpServer((DEFAULT_HOST, port), handler)
    server.manager = manager
    return server
