from __future__ import annotations

import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from knowledge_os import db
from knowledge_os.automation import AutomationResult, run_full_pipeline
from knowledge_os.cli import main as cli_main
from knowledge_os.config import (
    ProjectPaths,
    atomic_write_json,
    initialize_layout,
    load_runtime,
)
from knowledge_os.ingest import ingest_text
from knowledge_os.local_manager import LocalKnowledgeManager, inbox_fingerprint
from knowledge_os.operations import CheckResult, CheckStatus, HealthReport


def _wait_for(check: object, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():  # type: ignore[operator]
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for queue state")


def _manager_result(
    root: Path, *, pending: int, retried: int, failed: int = 0
) -> AutomationResult:
    return AutomationResult(
        project_root=str(root),
        ingested=1,
        duplicates=0,
        ingest_failed=0,
        jobs_claimed=1,
        jobs_completed=0 if pending else 1,
        jobs_retried=retried,
        jobs_failed=failed,
        jobs_pending=pending,
        documents=0 if pending else 1,
        site_output=str(root / "workspace" / "site" / "dist"),
        gate_allowed=True,
        health_status="PASS",
        health_report=str(root / "workspace" / "exports" / "private" / "health.md"),
    )


class QueueCompletionSemanticsTests(unittest.TestCase):
    def test_single_ingest_failure_does_not_block_later_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            initialize_layout(paths)
            runtime = load_runtime(paths)
            runtime["pipeline"]["max_file_mb"] = 1
            atomic_write_json(paths.runtime_file, runtime)

            inbox = paths.inbox_dir / "files"
            (inbox / "00-too-large.md").write_bytes(b"x" * (1024 * 1024 + 1))
            (inbox / "99-good.md").write_text(
                "# Java G1\n\nJava JVM G1 垃圾回收排障记录。",
                encoding="utf-8",
            )

            result = run_full_pipeline(root, max_jobs=10)

            self.assertFalse(result.ok)
            self.assertEqual(result.ingest_failed, 1)
            self.assertEqual(result.ingested, 1)
            self.assertEqual(result.jobs_completed, 3)
            self.assertEqual(result.jobs_pending, 0)
            self.assertEqual(result.documents, 1)
            reports = list(paths.quarantine_dir.glob("ingest-*.json"))
            self.assertEqual(len(reports), 1)
            report = json.loads(reports[0].read_text(encoding="utf-8"))
            self.assertEqual(report["source_name"], "00-too-large.md")
            self.assertNotIn(str(root), json.dumps(report, ensure_ascii=False))

    def test_max_jobs_limit_cannot_report_success_and_next_run_drains(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            initialize_layout(paths)
            source = paths.inbox_dir / "files" / "limited.md"
            source.write_text("# Java G1\n\nG1 Mixed GC。", encoding="utf-8")
            old_index = paths.site_dir / "dist" / "index.html"
            old_index.parent.mkdir(parents=True, exist_ok=True)
            old_index.write_text("last known good", encoding="utf-8")

            limited = run_full_pipeline(root, max_jobs=1)
            self.assertFalse(limited.ok)
            self.assertEqual(limited.jobs_claimed, 1)
            self.assertGreater(limited.jobs_pending, 0)
            self.assertEqual(old_index.read_text(encoding="utf-8"), "last known good")
            self.assertEqual(
                list(paths.site_dir.glob(".dist-candidate-*")), []
            )

            drained = run_full_pipeline(root, max_jobs=10)
            self.assertTrue(drained.ok, drained.to_dict())
            self.assertEqual(drained.jobs_pending, 0)
            self.assertEqual(drained.duplicates, 1)
            self.assertNotEqual(
                old_index.read_text(encoding="utf-8"), "last known good"
            )

    def test_existing_failed_job_keeps_later_pipeline_run_unhealthy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            initialize_layout(paths)
            runtime = load_runtime(paths)
            runtime["pipeline"].update(
                {"max_attempts": 1, "retry_base_seconds": 0}
            )
            atomic_write_json(paths.runtime_file, runtime)
            (paths.inbox_dir / "files" / "broken.bin").write_bytes(b"\x00\x01")
            (paths.inbox_dir / "files" / "good.md").write_text(
                "# Java G1\n\nJava JVM G1 正常资料。", encoding="utf-8"
            )

            first = run_full_pipeline(root, max_jobs=10)
            second = run_full_pipeline(root, max_jobs=10)

            self.assertFalse(first.ok)
            self.assertFalse(second.ok)
            self.assertEqual(first.jobs_failed, 1)
            self.assertEqual(second.jobs_failed, 1)
            self.assertEqual(second.jobs_pending, 0)
            promoted = json.loads(
                (paths.site_dir / "dist" / "data" / "site-data.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(promoted["documents"]), 1)

    def test_failed_candidate_health_keeps_last_known_good_site(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            initialize_layout(paths)
            (paths.inbox_dir / "files" / "healthy.md").write_text(
                "# Java G1\n\nG1 正常资料。", encoding="utf-8"
            )
            old_index = paths.site_dir / "dist" / "index.html"
            old_index.parent.mkdir(parents=True, exist_ok=True)
            old_index.write_text("last known good", encoding="utf-8")
            failed_health = HealthReport(
                project_root=paths.root,
                generated_at="2026-08-23T00:00:00+00:00",
                status=CheckStatus.FAIL,
                checks=(
                    CheckResult(
                        "candidate-test",
                        CheckStatus.FAIL,
                        "候选健康检查失败",
                    ),
                ),
            )

            with mock.patch(
                "knowledge_os.automation.run_health_checks",
                return_value=failed_health,
            ):
                result = run_full_pipeline(root, max_jobs=10)

            self.assertFalse(result.ok)
            self.assertEqual(result.jobs_pending, 0)
            self.assertTrue(result.gate_allowed)
            self.assertEqual(result.health_status, "FAIL")
            self.assertEqual(old_index.read_text(encoding="utf-8"), "last known good")
            self.assertEqual(list(paths.site_dir.glob(".dist-candidate-*")), [])

    def test_cli_status_and_run_json_reject_pending_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            initialize_layout(paths)
            connection = db.connect(paths.database_file)
            try:
                db.initialize_database(connection)
                ingest_text(
                    connection,
                    paths,
                    "Java G1 待处理任务",
                    load_runtime(paths),
                    title="队列状态",
                )
            finally:
                connection.close()

            status_output = io.StringIO()
            with contextlib.redirect_stdout(status_output):
                status_code = cli_main(
                    ["--root", str(root), "status", "--json"]
                )
            status = json.loads(status_output.getvalue())
            self.assertEqual(status_code, 2)
            self.assertEqual(status["jobs_pending"], 1)
            self.assertEqual(status["job_status"]["queued"], 1)

            run_output = io.StringIO()
            with contextlib.redirect_stdout(run_output):
                run_code = cli_main(
                    [
                        "--root",
                        str(root),
                        "run",
                        "--max-jobs",
                        "0",
                        "--no-build-data",
                    ]
                )
            run_result = json.loads(run_output.getvalue())
            self.assertEqual(run_code, 2)
            self.assertFalse(run_result["ok"])
            self.assertEqual(run_result["jobs"]["pending"], 1)

    def test_manager_retries_pending_result_before_recording_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            initialize_layout(paths)
            first_returned = threading.Event()
            second_started = threading.Event()
            allow_success = threading.Event()
            calls = []

            def runner(runner_root: Path, *, visibility: str) -> AutomationResult:
                self.assertEqual(runner_root, paths.root)
                self.assertEqual(visibility, "private")
                calls.append(len(calls) + 1)
                if len(calls) == 1:
                    first_returned.set()
                    return _manager_result(root, pending=1, retried=1)
                second_started.set()
                if not allow_success.wait(2.0):
                    raise RuntimeError("test did not allow queue continuation")
                index = paths.site_dir / "dist" / "index.html"
                index.parent.mkdir(parents=True, exist_ok=True)
                index.write_text("ready", encoding="utf-8")
                return _manager_result(root, pending=0, retried=0)

            manager = LocalKnowledgeManager(
                root,
                instance_id="queue-retry-test",
                control_token="q" * 32,
                poll_seconds=0.01,
                settle_seconds=0.0,
                retry_seconds=0.05,
                pipeline_runner=runner,
            )
            worker = threading.Thread(target=manager._watch_loop)
            with mock.patch("knowledge_os.local_manager.traceback.print_exc"):
                worker.start()
                try:
                    self.assertTrue(first_returned.wait(1.0))
                    _wait_for(lambda: manager.public_state().get("last_error"))
                    self.assertIsNone(
                        manager.public_state().get("successful_inbox_fingerprint")
                    )
                    self.assertTrue(second_started.wait(1.0))
                    self.assertIsNone(
                        manager.public_state().get("successful_inbox_fingerprint")
                    )
                    allow_success.set()
                    expected = inbox_fingerprint(paths)
                    _wait_for(
                        lambda: manager.public_state().get(
                            "successful_inbox_fingerprint"
                        )
                        == expected
                    )
                    state = manager.public_state()
                    self.assertEqual(state["last_result"]["jobs_pending"], 0)
                    self.assertIsNone(state.get("last_error"))
                    self.assertEqual(calls, [1, 2])
                finally:
                    allow_success.set()
                    manager.stop_event.set()
                    worker.join(timeout=2.0)
            self.assertFalse(worker.is_alive())

    def test_manager_records_terminal_isolation_without_empty_retry_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            initialize_layout(paths)
            called = threading.Event()
            calls = []

            def runner(runner_root: Path, *, visibility: str) -> AutomationResult:
                self.assertEqual(runner_root, paths.root)
                self.assertEqual(visibility, "private")
                calls.append(len(calls) + 1)
                index = paths.site_dir / "dist" / "index.html"
                index.parent.mkdir(parents=True, exist_ok=True)
                index.write_text("successful documents", encoding="utf-8")
                called.set()
                return _manager_result(
                    paths.root, pending=0, retried=1, failed=1
                )

            manager = LocalKnowledgeManager(
                root,
                instance_id="terminal-isolation-test",
                control_token="i" * 32,
                poll_seconds=0.01,
                settle_seconds=0.0,
                retry_seconds=0.05,
                pipeline_runner=runner,
            )
            worker = threading.Thread(target=manager._watch_loop)
            worker.start()
            try:
                self.assertTrue(called.wait(1.0))
                expected = inbox_fingerprint(paths)
                _wait_for(
                    lambda: manager.public_state().get(
                        "successful_inbox_fingerprint"
                    )
                    == expected
                )
                state = manager.public_state()
                self.assertEqual(state["status"], "degraded")
                self.assertEqual(state["last_result"]["jobs_failed"], 1)
                self.assertIn("已隔离", state["last_error"])
                self.assertEqual(
                    (paths.site_dir / "dist" / "index.html").read_text(
                        encoding="utf-8"
                    ),
                    "successful documents",
                )
                time.sleep(0.15)
                self.assertEqual(calls, [1])
            finally:
                manager.stop_event.set()
                worker.join(timeout=2.0)
            self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
