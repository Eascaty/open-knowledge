from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from knowledge_os.automation import (
    _publish_candidate,
    _recover_publication,
    run_full_pipeline,
)
from knowledge_os.config import ProjectPaths
from knowledge_os.operations import CheckStatus, GateResult, HealthReport
from knowledge_os.processing import RunSummary


class AutomationPublicationTests(unittest.TestCase):
    def test_failed_gate_keeps_live_site_and_cleans_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = ProjectPaths.from_root(root)
            live = paths.site_dir / "dist"
            live.mkdir(parents=True)
            marker = live / "old-site.txt"
            marker.write_text("keep me", encoding="utf-8")

            with mock.patch(
                "knowledge_os.automation.run_prebuild_gate",
                return_value=GateResult(False, (), "blocked for test"),
            ):
                result = run_full_pipeline(root)

            self.assertFalse(result.ok)
            self.assertFalse(result.gate_allowed)
            self.assertEqual(result.site_output, str(live))
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep me")
            self.assertEqual(list(live.parent.glob(".dist-candidate-*")), [])

    def test_publish_failure_rolls_back_previous_live_site(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            live = parent / "dist"
            candidate = parent / ".dist-candidate-test"
            live.mkdir()
            candidate.mkdir()
            (live / "index.html").write_text("old", encoding="utf-8")
            (candidate / "index.html").write_text("new", encoding="utf-8")
            real_replace = os.replace

            def fail_candidate_replace(source: str, destination: str) -> None:
                if (
                    Path(source).resolve() == candidate.resolve()
                    and Path(destination).resolve() == live.resolve()
                ):
                    raise OSError("simulated publication failure")
                real_replace(source, destination)

            with mock.patch(
                "knowledge_os.automation.os.replace",
                side_effect=fail_candidate_replace,
            ):
                with self.assertRaisesRegex(OSError, "simulated publication"):
                    _publish_candidate(candidate, live)

            self.assertEqual(
                (live / "index.html").read_text(encoding="utf-8"), "old"
            )
            self.assertTrue(candidate.is_dir())
            self.assertFalse((parent / ".dist-rollback").exists())
            self.assertEqual(list(parent.glob(".dist-rollback-*")), [])

    def test_interrupted_swap_restores_unique_rollback_and_stale_candidate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            live = parent / "dist"
            backup = parent / ".dist-rollback"
            stale_candidate = parent / ".dist-candidate-abandoned"
            backup.mkdir()
            stale_candidate.mkdir()
            (backup / "index.html").write_text("old", encoding="utf-8")
            (stale_candidate / "partial.txt").write_text(
                "partial", encoding="utf-8"
            )

            recovered = _recover_publication(live)

            self.assertEqual(recovered, live)
            self.assertEqual(
                (live / "index.html").read_text(encoding="utf-8"), "old"
            )
            self.assertFalse(backup.exists())
            self.assertFalse(stale_candidate.exists())

    def test_committed_swap_removes_stale_unique_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            live = parent / "dist"
            backup = parent / ".dist-rollback"
            live.mkdir()
            backup.mkdir()
            (live / "index.html").write_text("new", encoding="utf-8")
            (backup / "index.html").write_text("old", encoding="utf-8")

            _recover_publication(live)

            self.assertEqual(
                (live / "index.html").read_text(encoding="utf-8"), "new"
            )
            self.assertFalse(backup.exists())

    def test_failed_candidate_health_keeps_previous_live_site(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = ProjectPaths.from_root(root)
            live = paths.site_dir / "dist"
            live.mkdir(parents=True)
            marker = live / "old-site.txt"
            marker.write_text("healthy old site", encoding="utf-8")
            failed_health = HealthReport(
                project_root=root,
                generated_at="2026-08-23T00:00:00+00:00",
                status=CheckStatus.FAIL,
                checks=(),
            )

            def inspect_candidate(
                project_root: Path, **options: object
            ) -> HealthReport:
                self.assertEqual(Path(project_root), root)
                roots = options["candidate_roots"]
                self.assertIsInstance(roots, tuple)
                candidate = roots[0]  # type: ignore[index]
                self.assertEqual(candidate.parent, live.parent)
                self.assertNotEqual(candidate, live)
                self.assertEqual(
                    options["canonical_path"], candidate / "data" / "site-data.json"
                )
                return failed_health

            with mock.patch(
                "knowledge_os.automation.run_health_checks",
                side_effect=inspect_candidate,
            ):
                result = run_full_pipeline(root)

            self.assertFalse(result.ok)
            self.assertTrue(result.gate_allowed)
            self.assertEqual(result.health_status, "FAIL")
            self.assertEqual(
                marker.read_text(encoding="utf-8"), "healthy old site"
            )
            self.assertEqual(list(live.parent.glob(".dist-candidate-*")), [])

    def test_retrying_jobs_do_not_replace_previous_live_site(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = ProjectPaths.from_root(root)
            live = paths.site_dir / "dist"
            live.mkdir(parents=True)
            marker = live / "old-site.txt"
            marker.write_text("wait for retry", encoding="utf-8")

            with mock.patch(
                "knowledge_os.automation.process_jobs",
                return_value=RunSummary(
                    claimed=1,
                    completed=0,
                    retried=1,
                    failed=0,
                    pending=1,
                ),
            ):
                result = run_full_pipeline(root)

            self.assertEqual(result.jobs_retried, 1)
            self.assertEqual(result.jobs_failed, 0)
            self.assertEqual(result.jobs_pending, 1)
            self.assertFalse(result.ok)
            self.assertTrue(result.gate_allowed)
            self.assertNotEqual(result.health_status, "FAIL")
            self.assertEqual(
                marker.read_text(encoding="utf-8"), "wait for retry"
            )
            self.assertEqual(list(live.parent.glob(".dist-candidate-*")), [])

    def test_zero_job_limit_reports_pending_and_keeps_previous_site(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = ProjectPaths.from_root(root)
            live = paths.site_dir / "dist"
            live.mkdir(parents=True)
            marker = live / "old-site.txt"
            marker.write_text("keep until queue drains", encoding="utf-8")
            inbox = paths.inbox_dir / "files"
            inbox.mkdir(parents=True)
            shutil.copyfile(
                Path(__file__).parent / "fixtures" / "agent_memory.md",
                inbox / "agent_memory.md",
            )

            result = run_full_pipeline(root, max_jobs=0)

            self.assertEqual(result.jobs_claimed, 0)
            self.assertEqual(result.jobs_pending, 1)
            self.assertFalse(result.ok)
            self.assertEqual(
                marker.read_text(encoding="utf-8"), "keep until queue drains"
            )

    def test_positive_job_limit_does_not_publish_truncated_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = ProjectPaths.from_root(root)
            live = paths.site_dir / "dist"
            live.mkdir(parents=True)
            marker = live / "old-site.txt"
            marker.write_text("keep truncated queue", encoding="utf-8")
            inbox = paths.inbox_dir / "files"
            inbox.mkdir(parents=True)
            shutil.copyfile(
                Path(__file__).parent / "fixtures" / "agent_memory.md",
                inbox / "agent_memory.md",
            )

            result = run_full_pipeline(root, max_jobs=1)

            self.assertEqual(result.jobs_claimed, 1)
            self.assertGreater(result.jobs_pending, 0)
            self.assertFalse(result.ok)
            self.assertEqual(
                marker.read_text(encoding="utf-8"), "keep truncated queue"
            )

    def test_future_retry_is_pending_even_when_nothing_is_claimed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = ProjectPaths.from_root(root)
            live = paths.site_dir / "dist"
            live.mkdir(parents=True)
            marker = live / "old-site.txt"
            marker.write_text("keep future retry", encoding="utf-8")
            inbox = paths.inbox_dir / "files"
            inbox.mkdir(parents=True)
            shutil.copyfile(
                Path(__file__).parent / "fixtures" / "agent_memory.md",
                inbox / "agent_memory.md",
            )
            first = run_full_pipeline(root, max_jobs=0)
            self.assertEqual(first.jobs_pending, 1)
            connection = sqlite3.connect(str(paths.database_file))
            try:
                connection.execute(
                    """
                    UPDATE jobs
                    SET status='retry', available_at='2999-01-01T00:00:00+00:00'
                    """
                )
                connection.commit()
            finally:
                connection.close()

            result = run_full_pipeline(root)

            self.assertEqual(result.jobs_claimed, 0)
            self.assertEqual(result.jobs_retried, 0)
            self.assertEqual(result.jobs_pending, 1)
            self.assertFalse(result.ok)
            self.assertEqual(
                marker.read_text(encoding="utf-8"), "keep future retry"
            )

    def test_permanent_failure_does_not_block_checked_site_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = ProjectPaths.from_root(root)
            live = paths.site_dir / "dist"
            live.mkdir(parents=True)
            marker = live / "old-site.txt"
            marker.write_text("replace me", encoding="utf-8")

            with mock.patch(
                "knowledge_os.automation.process_jobs",
                return_value=RunSummary(
                    claimed=1,
                    completed=0,
                    retried=0,
                    failed=1,
                    pending=0,
                ),
            ):
                result = run_full_pipeline(root)

            self.assertEqual(result.jobs_failed, 1)
            self.assertEqual(result.jobs_pending, 0)
            self.assertFalse(result.ok)
            self.assertFalse(marker.exists())
            self.assertTrue((live / "index.html").is_file())
            self.assertFalse((live.parent / ".dist-rollback").exists())


if __name__ == "__main__":
    unittest.main()
