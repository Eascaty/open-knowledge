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
from pathlib import Path
from typing import Callable
from unittest import mock

from knowledge_os.automation import AutomationResult
from knowledge_os.config import ProjectPaths, initialize_layout
from knowledge_os.local_manager import (
    DEFAULT_HOST,
    STATUS_PATH,
    LocalKnowledgeManager,
    _request_json,
    _state_path,
    inbox_fingerprint,
)


def _result(root: Path) -> AutomationResult:
    return AutomationResult(
        project_root=str(root),
        ingested=1,
        duplicates=0,
        jobs_claimed=1,
        jobs_completed=1,
        jobs_retried=0,
        jobs_failed=0,
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
