from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from argparse import Namespace
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, Iterator, Optional
from unittest import mock

from knowledge_os import db
from knowledge_os.automation import AutomationResult
from knowledge_os.config import ProjectPaths, initialize_layout
from knowledge_os.local_http import (
    SESSION_REQUEST_HEADER,
    create_manager_server,
)
from knowledge_os.local_classification import CLASSIFICATION_PATH
from knowledge_os.local_review import REVIEW_PATH
from knowledge_os.local_inbox import (
    FILENAME_HEADER,
    INBOX_STATUS_PATH,
    SESSION_PATH,
    UPLOAD_HEADER,
    UPLOAD_PATH,
    InboxUploadStore,
    database_work_pending,
)
from knowledge_os.local_manager import (
    DEFAULT_HOST,
    STATUS_PATH,
    LocalKnowledgeManager,
    ManagerError,
    _request_json,
    _state_path,
    inbox_fingerprint,
)
from knowledge_os.local_manager_cli import _command_start


class LoopbackBindingTests(unittest.TestCase):
    def test_server_binding_does_not_reverse_resolve_loopback(self) -> None:
        from knowledge_os.local_http import ManagerHttpServer, ManagerRequestHandler
        with mock.patch("socket.getfqdn", side_effect=AssertionError("DNS forbidden")):
            server = ManagerHttpServer(("127.0.0.1", 0), ManagerRequestHandler)
        try:
            self.assertEqual(server.server_name, "127.0.0.1")
            self.assertGreater(server.server_port, 0)
        finally:
            server.server_close()


class _HttpManagerStub:
    def __init__(self, paths: ProjectPaths) -> None:
        self.paths = paths
        self.port = 0
        self.browser_token = "browser-session-secret"
        self.control_token = "control-secret"
        self.received: list[Dict[str, Any]] = []
        self.stopped = False
        self.rebuilds = 0

    def public_state(self) -> Dict[str, Any]:
        return {"ok": True, "status": "running", "instance_id": "http-test"}

    def browser_session(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "schema_version": 1,
            "service": "personal-knowledge-manager",
            "api_version": "browser-v1",
            "session_token": self.browser_token,
            "capabilities": {
                "file_upload": True,
                "manual_classification": True,
                "document_review": True,
                "accepted_extensions": [".md", ".pdf"],
                "maximum_file_bytes": 1024,
                "history_limit": 24,
            },
        }

    def inbox_status(self) -> Dict[str, Any]:
        return {"ok": True, "uploads": []}

    def authorized(self, supplied: str) -> bool:
        return supplied == self.control_token

    def authorized_browser(self, supplied: str) -> bool:
        return supplied == self.browser_token

    def receive_browser_upload(
        self,
        stream: BinaryIO,
        *,
        encoded_filename: str,
        content_length: str,
    ) -> Dict[str, Any]:
        size = int(content_length)
        body = stream.read(size)
        upload = {
            "upload_id": "http-upload",
            "filename": urllib.parse.unquote(encoded_filename),
            "size_bytes": len(body),
            "received_at": "2026-08-23T00:00:00+00:00",
            "status": "queued",
            "body": body,
        }
        self.received.append(upload)
        return {key: value for key, value in upload.items() if key != "body"}

    def request_stop(self) -> None:
        self.stopped = True

    def rebuild_after_classification(self) -> bool:
        self.rebuilds += 1
        return True


@contextmanager
def _running_http_server(
    root: Path,
) -> Iterator[tuple[_HttpManagerStub, int]]:
    paths = ProjectPaths.from_root(root)
    initialize_layout(paths)
    site = paths.site_dir / "dist"
    site.mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_text("PRIVATE_SITE_SENTINEL", encoding="utf-8")
    (site / "data").mkdir()
    (site / "data" / "site-data.json").write_text("{}", encoding="utf-8")
    (site / "build-meta.json").write_text("{}", encoding="utf-8")
    (site / "__knowledge").mkdir()
    (site / "__knowledge" / "private.json").write_text("{}", encoding="utf-8")
    manager = _HttpManagerStub(paths)
    server = create_manager_server(manager, 0, site)
    manager.port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield manager, manager.port
    finally:
        server.shutdown()
        thread.join(timeout=5.0)
        server.server_close()


def _http_request(
    port: int,
    method: str,
    path: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[bytes] = None,
) -> tuple[int, Dict[str, str], bytes]:
    connection = http.client.HTTPConnection(DEFAULT_HOST, port, timeout=2.0)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        response_headers = {
            key.casefold(): value for key, value in response.getheaders()
        }
        return response.status, response_headers, payload
    finally:
        connection.close()


def _result(root: Path) -> AutomationResult:
    return AutomationResult(
        project_root=str(root),
        ingested=1,
        duplicates=0,
        ingest_failed=0,
        jobs_claimed=1,
        jobs_completed=1,
        jobs_retried=0,
        jobs_failed=0,
        jobs_pending=0,
        documents=1,
        site_output=str(root / "workspace" / "site" / "dist"),
        gate_allowed=True,
        health_status="PASS",
        health_report=str(root / "workspace" / "data" / "logs" / "health.json"),
    )


def _wait_for(check: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting for local manager")


class LocalManagerTests(unittest.TestCase):
    def test_classification_rebuild_stays_pending_while_pipeline_is_busy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = LocalKnowledgeManager(
                Path(temporary),
                instance_id="classification-busy",
                control_token="c" * 32,
            )
            manager._save(successful_inbox_fingerprint="previous")
            manager.pipeline_lock.acquire()
            try:
                self.assertFalse(manager.rebuild_after_classification())
            finally:
                manager.pipeline_lock.release()
            self.assertIsNone(manager.state["successful_inbox_fingerprint"])

    def test_http_classification_previews_then_applies_with_same_origin_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            initialize_layout(paths)
            with db.connect(paths.database_file) as connection:
                db.initialize_database(connection)
                connection.executescript(
                    """
                    INSERT INTO nodes(id,parent_id,name,level,path_json,locked,sort_order,active)
                    VALUES('root',NULL,'知识',0,'["知识"]',1,0,1),
                          ('old','root','旧分类',1,'["知识","旧分类"]',1,0,1),
                          ('new','root','新分类',1,'["知识","新分类"]',1,1,1);
                    INSERT INTO sources(id,kind,origin,original_name,raw_path,sha256,mime_type,size_bytes,imported_at,status)
                    VALUES('source','file','note.md','note.md','workspace/data/raw/note.md','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','text/markdown',4,'2026-09-01T00:00:00+00:00','completed');
                    INSERT INTO documents(id,source_id,title,normalized_path,body,tags_json,created_at,updated_at)
                    VALUES('doc','source','测试卡','workspace/data/normalized/doc.md','正文','[]','2026-09-01T00:00:00+00:00','2026-09-01T00:00:00+00:00');
                    INSERT INTO placements(document_id,node_id,confidence,method,classified_at)
                    VALUES('doc','old',0.5,'rules','2026-09-01T00:00:00+00:00');
                    INSERT INTO documents_fts(document_id,title,body,tags,taxonomy_path)
                    VALUES('doc','测试卡','正文','','知识 / 旧分类');
                    """
                )
            with _running_http_server(root) as (manager, port):
                host = "{}:{}".format(DEFAULT_HOST, port)
                headers = {
                    "Host": host,
                    "Origin": "http://{}".format(host),
                    "Sec-Fetch-Site": "same-origin",
                    "Content-Type": "application/json",
                    UPLOAD_HEADER: manager.browser_token,
                }
                base = {
                    "document_id": "doc",
                    "target_node_id": "new",
                    "expected_node_id": "old",
                }
                preview_status, _, preview_body = _http_request(
                    port,
                    "POST",
                    CLASSIFICATION_PATH,
                    headers=headers,
                    body=json.dumps({**base, "action": "preview"}).encode("utf-8"),
                )
                with db.connect(paths.database_file) as connection:
                    self.assertEqual(connection.execute("SELECT node_id FROM placements").fetchone()[0], "old")
                apply_status, response_headers, apply_body = _http_request(
                    port,
                    "POST",
                    CLASSIFICATION_PATH,
                    headers=headers,
                    body=json.dumps({**base, "action": "apply"}).encode("utf-8"),
                )

            preview = json.loads(preview_body.decode("utf-8"))
            applied = json.loads(apply_body.decode("utf-8"))
            self.assertEqual(preview_status, 200)
            self.assertTrue(preview["dry_run"])
            self.assertTrue(preview["changed"])
            self.assertEqual(apply_status, 200)
            self.assertFalse(applied["dry_run"])
            self.assertTrue(applied["site_rebuilt"])
            self.assertEqual(response_headers["cache-control"], "private, no-store, max-age=0")
            self.assertEqual(manager.rebuilds, 1)
            with db.connect(paths.database_file) as connection:
                self.assertEqual(connection.execute("SELECT node_id FROM placements").fetchone()[0], "new")
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_http_classification_rejects_cross_origin_and_extra_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            initialize_layout(paths)
            with db.connect(paths.database_file) as connection:
                db.initialize_database(connection)
            with _running_http_server(root) as (manager, port):
                host = "{}:{}".format(DEFAULT_HOST, port)
                payload = json.dumps(
                    {
                        "action": "apply",
                        "document_id": "doc",
                        "target_node_id": "target",
                        "expected_node_id": "old",
                    }
                ).encode("utf-8")
                forbidden, _, _ = _http_request(
                    port,
                    "POST",
                    CLASSIFICATION_PATH,
                    headers={
                        "Host": host,
                        "Origin": "https://attacker.example",
                        "Content-Type": "application/json",
                        UPLOAD_HEADER: manager.browser_token,
                    },
                    body=payload,
                )
                invalid_status, _, invalid_body = _http_request(
                    port,
                    "POST",
                    CLASSIFICATION_PATH,
                    headers={
                        "Host": host,
                        "Origin": "http://{}".format(host),
                        "Content-Type": "application/json",
                        UPLOAD_HEADER: manager.browser_token,
                    },
                    body=json.dumps({**json.loads(payload), "unexpected": True}).encode("utf-8"),
                )
                wrong_media, _, _ = _http_request(
                    port,
                    "POST",
                    CLASSIFICATION_PATH,
                    headers={
                        "Host": host,
                        "Origin": "http://{}".format(host),
                        "Content-Type": "text/plain",
                        UPLOAD_HEADER: manager.browser_token,
                    },
                    body=payload,
                )
                oversized, _, _ = _http_request(
                    port,
                    "POST",
                    CLASSIFICATION_PATH,
                    headers={
                        "Host": host,
                        "Origin": "http://{}".format(host),
                        "Content-Type": "application/json",
                        UPLOAD_HEADER: manager.browser_token,
                    },
                    body=b" " * (8 * 1024 + 1),
                )

            self.assertEqual(forbidden, 403)
            self.assertEqual(invalid_status, 400)
            self.assertEqual(json.loads(invalid_body)["error"]["code"], "invalid_request")
            self.assertEqual(wrong_media, 415)
            self.assertEqual(oversized, 413)
            self.assertEqual(manager.rebuilds, 0)

    def test_http_review_previews_then_applies_with_same_origin_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            initialize_layout(paths)
            with db.connect(paths.database_file) as connection:
                db.initialize_database(connection)
                connection.executescript(
                    """
                    INSERT INTO sources(id,kind,origin,original_name,raw_path,sha256,mime_type,size_bytes,imported_at,status)
                    VALUES('source','file','note.md','note.md','workspace/data/raw/note.md','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','text/markdown',4,'2026-09-01T00:00:00+00:00','completed');
                    INSERT INTO documents(id,source_id,title,normalized_path,body,tags_json,created_at,updated_at)
                    VALUES('doc','source','测试卡','workspace/data/normalized/doc.md','正文','[]','2026-09-01T00:00:00+00:00','2026-09-01T00:00:00+00:00');
                    """
                )
            with _running_http_server(root) as (manager, port):
                host = "{}:{}".format(DEFAULT_HOST, port)
                headers = {
                    "Host": host,
                    "Origin": "http://{}".format(host),
                    "Sec-Fetch-Site": "same-origin",
                    "Content-Type": "application/json",
                    UPLOAD_HEADER: manager.browser_token,
                }
                base = {
                    "document_id": "doc",
                    "target_status": "supported",
                    "expected_status": "unverified",
                }
                preview_status, _, preview_body = _http_request(
                    port,
                    "POST",
                    REVIEW_PATH,
                    headers=headers,
                    body=json.dumps({**base, "action": "preview"}).encode("utf-8"),
                )
                with db.connect(paths.database_file) as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
                apply_status, response_headers, apply_body = _http_request(
                    port,
                    "POST",
                    REVIEW_PATH,
                    headers=headers,
                    body=json.dumps({**base, "action": "apply"}).encode("utf-8"),
                )

            preview = json.loads(preview_body.decode("utf-8"))
            applied = json.loads(apply_body.decode("utf-8"))
            self.assertEqual(preview_status, 200)
            self.assertTrue(preview["dry_run"])
            self.assertEqual(preview["target_status"], "supported")
            self.assertEqual(apply_status, 200)
            self.assertFalse(applied["dry_run"])
            self.assertTrue(applied["site_rebuilt"])
            self.assertEqual(response_headers["cache-control"], "private, no-store, max-age=0")
            self.assertEqual(manager.rebuilds, 1)
            with db.connect(paths.database_file) as connection:
                event = connection.execute(
                    "SELECT event_type FROM events ORDER BY id DESC LIMIT 1"
                ).fetchone()
                self.assertEqual(event["event_type"], "document_review_status_changed")

    def test_restart_requeues_processing_upload_and_rotates_browser_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialize_layout(ProjectPaths.from_root(root))
            first = LocalKnowledgeManager(
                root,
                instance_id="restart-first",
                control_token="a" * 32,
            )
            first._save(
                recent_uploads=[
                    {
                        "upload_id": "pending-upload",
                        "filename": "pending.md",
                        "size_bytes": 7,
                        "sha256": "f" * 64,
                        "received_at": "2026-08-23T00:00:00+00:00",
                        "updated_at": "2026-08-23T00:00:01+00:00",
                        "revision": 2,
                        "status": "processing",
                        "attempt_id": "interrupted-attempt",
                        "started_at": "2026-08-23T00:00:01+00:00",
                    }
                ]
            )
            state_path = _state_path(ProjectPaths.from_root(root))
            first_token = first.browser_token
            self.assertNotIn(first_token, state_path.read_text(encoding="utf-8"))

            second = LocalKnowledgeManager(
                root,
                instance_id="restart-second",
                control_token="b" * 32,
            )
            self.assertNotEqual(second.browser_token, first_token)
            restored = second.state["recent_uploads"][0]
            self.assertEqual(restored["status"], "queued")
            self.assertNotIn("attempt_id", restored)
            self.assertNotIn("started_at", restored)
            second._save()
            persisted = state_path.read_text(encoding="utf-8")
            self.assertNotIn(first_token, persisted)
            self.assertNotIn(second.browser_token, persisted)

    def test_http_rejects_untrusted_host_without_serving_private_site(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with _running_http_server(Path(temporary)) as (_manager, port):
                status, headers, payload = _http_request(
                    port,
                    "GET",
                    "/",
                    headers={"Host": "rebound.example:{}".format(port)},
                )

            self.assertEqual(status, 421)
            response = json.loads(payload.decode("utf-8"))
            self.assertEqual(response["error"]["code"], "bad_host")
            self.assertNotIn(b"PRIVATE_SITE_SENTINEL", payload)
            self.assertEqual(headers["x-frame-options"], "DENY")
            self.assertIn("frame-ancestors 'none'", headers["content-security-policy"])

    def test_http_session_requires_same_origin_and_client_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with _running_http_server(Path(temporary)) as (manager, port):
                host = "{}:{}".format(DEFAULT_HOST, port)
                origin = "http://{}".format(host)
                wrong_origin, _, _ = _http_request(
                    port,
                    "POST",
                    SESSION_PATH,
                    headers={
                        "Host": host,
                        "Origin": "https://attacker.example",
                        SESSION_REQUEST_HEADER: "browser-v1",
                    },
                )
                wrong_probe, _, _ = _http_request(
                    port,
                    "POST",
                    SESSION_PATH,
                    headers={"Host": host, "Origin": origin},
                )
                status, headers, payload = _http_request(
                    port,
                    "POST",
                    SESSION_PATH,
                    headers={
                        "Host": host,
                        "Origin": origin,
                        "Sec-Fetch-Site": "same-origin",
                        SESSION_REQUEST_HEADER: "browser-v1",
                    },
                )

            self.assertEqual(wrong_origin, 403)
            self.assertEqual(wrong_probe, 403)
            self.assertEqual(status, 200)
            session = json.loads(payload.decode("utf-8"))
            self.assertEqual(session["session_token"], manager.browser_token)
            self.assertEqual(session["service"], "personal-knowledge-manager")
            self.assertEqual(session["api_version"], "browser-v1")
            self.assertEqual(
                headers["cache-control"], "private, no-store, max-age=0"
            )

    def test_http_upload_requires_origin_token_and_binary_media(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with _running_http_server(Path(temporary)) as (manager, port):
                host = "{}:{}".format(DEFAULT_HOST, port)
                origin = "http://{}".format(host)
                base_headers = {
                    "Host": host,
                    "Origin": origin,
                    "Sec-Fetch-Site": "same-origin",
                    FILENAME_HEADER: urllib.parse.quote("技术笔记.md"),
                    "Content-Type": "application/octet-stream",
                }
                wrong_token, _, _ = _http_request(
                    port,
                    "POST",
                    UPLOAD_PATH,
                    headers={**base_headers, UPLOAD_HEADER: "wrong"},
                    body=b"not accepted",
                )
                wrong_origin, _, _ = _http_request(
                    port,
                    "POST",
                    UPLOAD_PATH,
                    headers={
                        **base_headers,
                        "Origin": "https://attacker.example",
                        UPLOAD_HEADER: manager.browser_token,
                    },
                    body=b"not accepted",
                )
                wrong_media, _, _ = _http_request(
                    port,
                    "POST",
                    UPLOAD_PATH,
                    headers={
                        **base_headers,
                        "Content-Type": "multipart/form-data; boundary=x",
                        UPLOAD_HEADER: manager.browser_token,
                    },
                    body=b"not accepted",
                )
                status, _, payload = _http_request(
                    port,
                    "POST",
                    UPLOAD_PATH,
                    headers={**base_headers, UPLOAD_HEADER: manager.browser_token},
                    body=b"accepted note",
                )

            self.assertEqual(wrong_token, 403)
            self.assertEqual(wrong_origin, 403)
            self.assertEqual(wrong_media, 415)
            self.assertEqual(status, 202)
            receipt = json.loads(payload.decode("utf-8"))["upload"]
            self.assertEqual(receipt["filename"], "技术笔记.md")
            self.assertEqual(receipt["size_bytes"], len(b"accepted note"))
            self.assertEqual(len(manager.received), 1)
            self.assertEqual(manager.received[0]["body"], b"accepted note")

    def test_http_inbox_status_requires_browser_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with _running_http_server(Path(temporary)) as (manager, port):
                host = "{}:{}".format(DEFAULT_HOST, port)
                origin = "http://{}".format(host)
                forbidden, _, _ = _http_request(
                    port,
                    "POST",
                    INBOX_STATUS_PATH,
                    headers={"Host": host, "Origin": origin, UPLOAD_HEADER: "wrong"},
                )
                status, _, payload = _http_request(
                    port,
                    "POST",
                    INBOX_STATUS_PATH,
                    headers={
                        "Host": host,
                        "Origin": origin,
                        UPLOAD_HEADER: manager.browser_token,
                    },
                )

            self.assertEqual(forbidden, 403)
            self.assertEqual(status, 200)
            self.assertEqual(
                json.loads(payload.decode("utf-8")),
                {"ok": True, "uploads": []},
            )

    def test_http_options_never_grants_cross_origin_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with _running_http_server(Path(temporary)) as (_manager, port):
                status, headers, payload = _http_request(
                    port,
                    "OPTIONS",
                    UPLOAD_PATH,
                    headers={
                        "Host": "{}:{}".format(DEFAULT_HOST, port),
                        "Origin": "https://attacker.example",
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": UPLOAD_HEADER,
                    },
                )

            self.assertEqual(status, 405)
            self.assertNotIn("access-control-allow-origin", headers)
            self.assertEqual(
                json.loads(payload.decode("utf-8"))["error"]["code"],
                "method_not_allowed",
            )

    def test_http_security_headers_cover_static_and_control_responses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with _running_http_server(Path(temporary)) as (_manager, port):
                expected = {
                    "Host": "{}:{}".format(DEFAULT_HOST, port),
                }
                static_status, static_headers, _ = _http_request(
                    port, "GET", "/", headers=expected
                )
                api_status, api_headers, _ = _http_request(
                    port, "GET", STATUS_PATH, headers=expected
                )

            self.assertEqual(static_status, 200)
            self.assertEqual(api_status, 200)
            for response_headers in (static_headers, api_headers):
                self.assertEqual(response_headers["x-frame-options"], "DENY")
                self.assertEqual(
                    response_headers["x-content-type-options"], "nosniff"
                )
                self.assertEqual(
                    response_headers["cross-origin-resource-policy"],
                    "same-origin",
                )
                self.assertEqual(
                    response_headers["cross-origin-opener-policy"],
                    "same-origin",
                )
                self.assertEqual(response_headers["referrer-policy"], "no-referrer")
                self.assertIn(
                    "frame-ancestors 'none'",
                    response_headers["content-security-policy"],
                )

    def test_http_private_data_cache_policy_covers_encoded_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with _running_http_server(Path(temporary)) as (_manager, port):
                headers = {"Host": "{}:{}".format(DEFAULT_HOST, port)}
                paths = (
                    "/data/site-data.json",
                    "/d%61ta/site-data.json",
                    "/data%2Fsite-data.json",
                    "/build-meta%2Ejson",
                    "/DATA/site-data.json",
                    "/BUILD-META.JSON",
                    "/%5f%5fknowledge/private.json",
                )
                responses = [
                    _http_request(port, "GET", path, headers=headers)
                    for path in paths
                ]

            for path, (status, response_headers, _) in zip(paths, responses):
                with self.subTest(path=path):
                    if path in {"/DATA/site-data.json", "/BUILD-META.JSON"}:
                        self.assertIn(status, {200, 404})
                    else:
                        self.assertEqual(status, 200)
                    self.assertEqual(
                        response_headers["cache-control"],
                        "private, no-store, max-age=0",
                    )

    def test_session_and_store_share_dynamic_pdf_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch(
            "knowledge_os.local_inbox.shutil.which", return_value=None
        ):
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            initialize_layout(paths)
            manager = LocalKnowledgeManager(
                root,
                instance_id="capability-test",
                control_token="c" * 32,
            )
            manager.inbox_store = InboxUploadStore(paths, 1024)
            session = manager.browser_session()
            self.assertNotIn(
                ".pdf", session["capabilities"]["accepted_extensions"]
            )
            self.assertEqual(
                session["capabilities"]["accepted_extensions"],
                list(manager.inbox_store.accepted_extensions),
            )

    def test_begin_only_claims_queued_and_retryable_failed_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = LocalKnowledgeManager(
                Path(temporary),
                instance_id="claim-test",
                control_token="d" * 32,
            )
            manager.state["recent_uploads"] = [
                {"upload_id": "queued", "status": "queued", "revision": 1},
                {
                    "upload_id": "retryable",
                    "status": "failed",
                    "revision": 1,
                    "error": {"retryable": True},
                },
                {
                    "upload_id": "permanent",
                    "status": "failed",
                    "revision": 1,
                    "error": {"retryable": False},
                },
                {"upload_id": "done", "status": "completed", "revision": 1},
            ]

            selected = manager._begin_upload_attempt("attempt")

            self.assertEqual(selected, ["queued", "retryable"])
            statuses = {
                item["upload_id"]: item["status"]
                for item in manager.state["recent_uploads"]
            }
            self.assertEqual(statuses["queued"], "processing")
            self.assertEqual(statuses["retryable"], "processing")
            self.assertEqual(statuses["permanent"], "failed")
            self.assertEqual(statuses["done"], "completed")

    def test_upload_attempt_settles_each_file_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = LocalKnowledgeManager(
                Path(temporary),
                instance_id="settle-test",
                control_token="e" * 32,
            )
            manager.state["recent_uploads"] = [
                {
                    "upload_id": "good",
                    "sha256": "a" * 64,
                    "status": "processing",
                    "revision": 2,
                    "known_before": False,
                },
                {
                    "upload_id": "bad",
                    "sha256": "b" * 64,
                    "status": "processing",
                    "revision": 2,
                },
            ]
            outcomes = {
                "good": {
                    "status": "completed",
                    "site_revision": "published-revision",
                    "document": {
                        "id": "doc-good",
                        "title": "Good",
                        "node_id": "ai-agent",
                        "path": ["AI", "Agent"],
                    },
                },
                "bad": {"status": "failed", "retryable": False},
            }
            with mock.patch(
                "knowledge_os.local_manager.upload_outcomes",
                return_value=outcomes,
            ):
                counts = manager._settle_upload_attempt(["good", "bad"])

            self.assertEqual(
                counts, {"completed": 1, "permanent": 1, "retryable": 0}
            )
            by_id = {
                item["upload_id"]: item
                for item in manager.state["recent_uploads"]
            }
            self.assertEqual(by_id["good"]["status"], "completed")
            self.assertEqual(
                by_id["good"]["result"]["document"]["id"], "doc-good"
            )
            self.assertEqual(by_id["bad"]["status"], "failed")
            self.assertFalse(by_id["bad"]["error"]["retryable"])

    def test_partial_success_with_no_pending_jobs_is_a_finished_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = LocalKnowledgeManager(
                Path(temporary),
                instance_id="partial-test",
                control_token="f" * 32,
                pipeline_runner=mock.Mock(),
            )
            result = mock.Mock(
                ok=False,
                jobs_failed=1,
                jobs_retried=0,
                jobs_pending=0,
                gate_allowed=True,
                health_status="PASS",
            )
            result.to_dict.return_value = {
                "ok": False,
                "jobs_failed": 1,
                "jobs_pending": 0,
                "gate_allowed": True,
                "health_status": "PASS",
            }
            manager.pipeline_runner.return_value = result
            with mock.patch.object(
                manager,
                "_settle_upload_attempt",
                return_value={"completed": 1, "permanent": 1, "retryable": 0},
            ):
                self.assertTrue(manager._run_pipeline("finished-fingerprint"))

            self.assertEqual(
                manager.state["successful_inbox_fingerprint"],
                "finished-fingerprint",
            )
            self.assertEqual(
                manager.public_state()["last_result"]["jobs_pending"], 0
            )

            result.jobs_pending = 1
            result.to_dict.return_value["jobs_pending"] = 1
            with mock.patch.object(
                manager,
                "_settle_upload_attempt",
                return_value={"completed": 0, "permanent": 0, "retryable": 0},
            ), mock.patch("knowledge_os.local_manager.traceback.print_exc"):
                self.assertFalse(manager._run_pipeline("not-published"))
            self.assertNotEqual(
                manager.state["successful_inbox_fingerprint"], "not-published"
            )

    def test_real_manager_upload_completes_and_links_generated_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = LocalKnowledgeManager(
                root,
                instance_id="real-upload-test",
                control_token="z" * 32,
                port=0,
                poll_seconds=0.02,
                settle_seconds=0.05,
                retry_seconds=60.0,
            )
            thread = threading.Thread(target=manager.serve)
            thread.start()
            browser_token = ""
            public_payload = b""
            completed: Optional[Dict[str, Any]] = None
            try:
                _wait_for(lambda: manager.port > 0)
                host = "{}:{}".format(DEFAULT_HOST, manager.port)
                origin = "http://{}".format(host)
                session_status, _, session_payload = _http_request(
                    manager.port,
                    "POST",
                    SESSION_PATH,
                    headers={
                        "Host": host,
                        "Origin": origin,
                        "Sec-Fetch-Site": "same-origin",
                        SESSION_REQUEST_HEADER: "browser-v1",
                    },
                )
                self.assertEqual(session_status, 200)
                session = json.loads(session_payload.decode("utf-8"))
                browser_token = session["session_token"]

                source = (
                    "# Java 智能体安全\n\n"
                    "Java Agent 应当校验输入、限制权限，并保留可审计来源。\n"
                ).encode("utf-8")
                upload_status, _, upload_payload = _http_request(
                    manager.port,
                    "POST",
                    UPLOAD_PATH,
                    headers={
                        "Host": host,
                        "Origin": origin,
                        "Sec-Fetch-Site": "same-origin",
                        UPLOAD_HEADER: browser_token,
                        FILENAME_HEADER: urllib.parse.quote(
                            "Java 智能体安全.md", safe=""
                        ),
                        "Content-Type": "application/octet-stream",
                    },
                    body=source,
                )
                self.assertEqual(upload_status, 202)
                upload_id = json.loads(upload_payload.decode("utf-8"))["upload"][
                    "upload_id"
                ]

                deadline = time.monotonic() + 15.0
                last_status: Dict[str, Any] = {}
                while time.monotonic() < deadline:
                    inbox_code, _, inbox_payload = _http_request(
                        manager.port,
                        "POST",
                        INBOX_STATUS_PATH,
                        headers={
                            "Host": host,
                            "Origin": origin,
                            "Sec-Fetch-Site": "same-origin",
                            UPLOAD_HEADER: browser_token,
                        },
                    )
                    self.assertEqual(inbox_code, 200)
                    last_status = json.loads(inbox_payload.decode("utf-8"))
                    completed = next(
                        (
                            item
                            for item in last_status.get("uploads", [])
                            if item.get("upload_id") == upload_id
                            and item.get("status") == "completed"
                        ),
                        None,
                    )
                    if completed is not None:
                        break
                    time.sleep(0.05)
                self.assertIsNotNone(completed, last_status)
                assert completed is not None

                public_status, _, public_payload = _http_request(
                    manager.port,
                    "GET",
                    STATUS_PATH,
                    headers={"Host": host},
                )
                self.assertEqual(public_status, 200)
            finally:
                manager.request_stop()
                thread.join(timeout=10.0)
            self.assertFalse(thread.is_alive())

            document_id = completed["result"]["document"]["id"]
            site_data_path = (
                ProjectPaths.from_root(root).site_dir
                / "dist"
                / "data"
                / "site-data.json"
            )
            site_data = json.loads(site_data_path.read_text(encoding="utf-8"))
            self.assertIn(
                document_id,
                {document["id"] for document in site_data["documents"]},
            )
            token_bytes = browser_token.encode("utf-8")
            self.assertNotIn(token_bytes, public_payload)
            for path in (ProjectPaths.from_root(root).site_dir / "dist").rglob("*"):
                if path.is_file():
                    self.assertNotIn(token_bytes, path.read_bytes(), path)

    def test_inbox_fingerprint_tracks_sources_but_ignores_control_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = ProjectPaths.from_root(temporary)
            initialize_layout(paths)
            baseline = inbox_fingerprint(paths)

            (paths.inbox_dir / "README.md").write_text(
                "instructions", encoding="utf-8"
            )
            (paths.inbox_dir / "urls.txt").write_text(
                "https://example.invalid", encoding="utf-8"
            )
            self.assertEqual(inbox_fingerprint(paths), baseline)

            source = paths.inbox_dir / "files" / "note.md"
            source.write_text("Java agent", encoding="utf-8")
            with_source = inbox_fingerprint(paths)
            self.assertNotEqual(with_source, baseline)

            source.write_text("Java agent updated", encoding="utf-8")
            self.assertNotEqual(inbox_fingerprint(paths), with_source)

    def test_manager_runs_requeued_database_job_when_inbox_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = ProjectPaths.from_root(root)
            initialize_layout(paths)
            index = paths.site_dir / "dist" / "index.html"
            index.parent.mkdir(parents=True, exist_ok=True)
            index.write_text("site before migration", encoding="utf-8")

            with db.connect(paths.database_file) as connection:
                db.initialize_database(connection)
                connection.execute(
                    """
                    INSERT INTO sources(
                        id, kind, origin, original_name, raw_path, sha256,
                        mime_type, size_bytes, imported_at, status
                    ) VALUES(
                        'migration-source', 'file', 'migration.md',
                        'migration.md', 'workspace/data/raw/migration.md',
                        ?, 'text/markdown', 1, ?, 'queued'
                    )
                    """,
                    ("a" * 64, "2026-08-30T00:00:00+00:00"),
                )
                connection.execute(
                    """
                    INSERT INTO jobs(
                        source_id, stage, status, available_at,
                        created_at, updated_at
                    ) VALUES(
                        'migration-source', 'extract', 'queued', ?, ?, ?
                    )
                    """,
                    ("2026-08-30T00:00:00+00:00",) * 3,
                )
                connection.commit()

            self.assertTrue(database_work_pending(paths))
            completed = threading.Event()

            def migration_runner(
                runner_root: Path, *, visibility: str
            ) -> AutomationResult:
                self.assertEqual(runner_root, root)
                self.assertEqual(visibility, "private")
                with db.connect(paths.database_file) as connection:
                    connection.execute(
                        "UPDATE jobs SET status='done' WHERE source_id=?",
                        ("migration-source",),
                    )
                    connection.execute(
                        "UPDATE sources SET status='completed' WHERE id=?",
                        ("migration-source",),
                    )
                    connection.commit()
                completed.set()
                return _result(runner_root)

            manager = LocalKnowledgeManager(
                root,
                instance_id="migration-requeue-test",
                control_token="m" * 32,
                port=0,
                poll_seconds=0.02,
                settle_seconds=0.0,
                retry_seconds=0.1,
                pipeline_runner=migration_runner,
            )
            manager.state["successful_inbox_fingerprint"] = inbox_fingerprint(paths)
            thread = threading.Thread(target=manager.serve)
            thread.start()
            try:
                self.assertTrue(completed.wait(3.0))
                _wait_for(lambda: not database_work_pending(paths))
            finally:
                manager.request_stop()
                thread.join(timeout=5.0)
            self.assertFalse(thread.is_alive())

    def test_manager_keeps_old_site_and_sanitizes_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            initialize_layout(paths)
            index = paths.site_dir / "dist" / "index.html"
            index.parent.mkdir(parents=True, exist_ok=True)
            index.write_text("old working site", encoding="utf-8")
            attempted = threading.Event()

            def failing_runner(
                runner_root: Path, *, visibility: str
            ) -> AutomationResult:
                self.assertEqual(visibility, "private")
                attempted.set()
                raise RuntimeError("SECRET_SENTINEL {}".format(runner_root))

            manager = LocalKnowledgeManager(
                root,
                instance_id="failure-test",
                control_token="x" * 32,
                port=0,
                poll_seconds=0.02,
                settle_seconds=0.0,
                retry_seconds=60.0,
                pipeline_runner=failing_runner,
            )
            manager.state["successful_inbox_fingerprint"] = inbox_fingerprint(paths)
            thread = threading.Thread(target=manager.serve)
            thread.start()
            try:
                _wait_for(lambda: manager.port > 0)
                with mock.patch(
                    "knowledge_os.local_manager.traceback.print_exc"
                ) as private_trace:
                    (paths.inbox_dir / "files" / "broken.md").write_text(
                        "trigger", encoding="utf-8"
                    )
                    self.assertTrue(attempted.wait(3.0))
                    _wait_for(
                        lambda: bool(manager.public_state().get("last_error"))
                    )
                    private_trace.assert_called_once()
                    status = _request_json(DEFAULT_HOST, manager.port, STATUS_PATH)
                    self.assertIsNotNone(status)
                    encoded = json.dumps(status, ensure_ascii=False)
                    self.assertNotIn("SECRET_SENTINEL", encoded)
                    self.assertNotIn(str(root), encoded)
                    self.assertNotIn("control_token", encoded)
                    self.assertEqual(
                        index.read_text(encoding="utf-8"), "old working site"
                    )
            finally:
                manager.request_stop()
                thread.join(timeout=5.0)
            self.assertFalse(thread.is_alive())

    def test_manager_updates_site_and_exposes_only_safe_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            initialize_layout(paths)
            completed = threading.Event()

            def successful_runner(
                runner_root: Path, *, visibility: str
            ) -> AutomationResult:
                self.assertEqual(visibility, "private")
                index = paths.site_dir / "dist" / "index.html"
                index.parent.mkdir(parents=True, exist_ok=True)
                index.write_text("new site", encoding="utf-8")
                completed.set()
                return _result(runner_root)

            manager = LocalKnowledgeManager(
                root,
                instance_id="success-test",
                control_token="y" * 32,
                port=0,
                poll_seconds=0.02,
                settle_seconds=0.5,
                retry_seconds=0.1,
                pipeline_runner=successful_runner,
            )
            thread = threading.Thread(target=manager.serve)
            thread.start()
            try:
                _wait_for(lambda: manager.port > 0)
                connection = http.client.HTTPConnection(
                    DEFAULT_HOST, manager.port, timeout=1.0
                )
                connection.request("GET", "/")
                response = connection.getresponse()
                waiting_page = response.read().decode("utf-8")
                connection.close()
                self.assertEqual(response.status, 200)
                self.assertIn("知识库正在准备", waiting_page)
                self.assertTrue(completed.wait(3.0))
                _wait_for(
                    lambda: manager.public_state().get("last_success_at") is not None
                )
                status = _request_json(DEFAULT_HOST, manager.port, STATUS_PATH)
                self.assertIsNotNone(status)
                assert status is not None
                self.assertEqual(status["last_result"]["documents"], 1)
                encoded = json.dumps(status, ensure_ascii=False)
                self.assertNotIn(str(root), encoded)
                self.assertNotIn("site_output", encoded)
                self.assertNotIn("health_report", encoded)
                self.assertNotIn("control_token", encoded)

                private_file = root / "private-sentinel.txt"
                private_file.write_text("DO_NOT_SERVE", encoding="utf-8")
                leaked_link = paths.site_dir / "dist" / "leak.txt"
                leaked_link.symlink_to(private_file)
                connection = http.client.HTTPConnection(
                    DEFAULT_HOST, manager.port, timeout=1.0
                )
                connection.request("GET", "/leak.txt")
                response = connection.getresponse()
                leaked_payload = response.read().decode("utf-8")
                connection.close()
                self.assertEqual(response.status, 404)
                self.assertNotIn("DO_NOT_SERVE", leaked_payload)
            finally:
                manager.request_stop()
                thread.join(timeout=5.0)
            self.assertFalse(thread.is_alive())

    def test_cli_failed_start_restores_state_and_stops_detached_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            initialize_layout(paths)
            previous = {
                "service": "personal-knowledge-manager",
                "instance_id": "previous-instance",
                "control_token": "p" * 32,
                "status": "running",
                "host": DEFAULT_HOST,
                "port": 8765,
                "url": "http://127.0.0.1:8765/",
                "last_success_at": "2026-08-30T00:00:00+00:00",
            }
            state_path = _state_path(paths)
            state_path.write_text(json.dumps(previous), encoding="utf-8")
            process = mock.Mock()
            process.poll.return_value = None
            process.wait.return_value = 0
            arguments = Namespace(
                root=root,
                port=8765,
                poll_seconds=2.0,
                settle_seconds=3.0,
                retry_seconds=30.0,
                wait_seconds=0.0,
                no_open=True,
                json=True,
            )

            with mock.patch(
                "knowledge_os.local_manager_cli._probe_state", return_value=None
            ), mock.patch(
                "knowledge_os.local_manager_cli._port_is_open", return_value=False
            ), mock.patch(
                "knowledge_os.local_manager_cli.secrets.token_hex",
                return_value="failed-instance",
            ), mock.patch(
                "knowledge_os.local_manager_cli.subprocess.Popen",
                return_value=process,
            ):
                with self.assertRaises(ManagerError):
                    _command_start(arguments)

            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8")), previous
            )
            process.terminate.assert_called_once_with()
            process.wait.assert_called_once_with(timeout=2.0)

    def test_cli_reuses_one_detached_instance_and_stops_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_source = Path(__file__).parents[1] / "apps" / "pipeline" / "src"
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["PYTHONPATH"] = str(project_source)
            common = [
                sys.executable,
                "-B",
                "-m",
                "knowledge_os.local_manager_cli",
                "--root",
                str(root),
            ]
            start = common + [
                "start",
                "--port",
                "0",
                "--poll-seconds",
                "0.05",
                "--settle-seconds",
                "0.05",
                "--retry-seconds",
                "0.1",
                "--no-open",
                "--json",
            ]
            first = subprocess.run(
                start,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            first_status = json.loads(first.stdout)
            try:
                second = subprocess.run(
                    start,
                    env=environment,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                second_status = json.loads(second.stdout)
                self.assertEqual(
                    second_status["instance_id"], first_status["instance_id"]
                )
                self.assertEqual(second_status["port"], first_status["port"])
                state_path = _state_path(ProjectPaths.from_root(root))
                private_state = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertTrue(private_state["control_token"])
            finally:
                subprocess.run(
                    common + ["stop", "--wait-seconds", "5"],
                    env=environment,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            stopped = subprocess.run(
                common + ["status", "--json"],
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(stopped.returncode, 1)
            self.assertEqual(json.loads(stopped.stdout)["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
