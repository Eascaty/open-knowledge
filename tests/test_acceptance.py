from __future__ import annotations

import unittest
from pathlib import Path

from knowledge_os.acceptance import run_acceptance_suite


class AcceptanceSuiteTests(unittest.TestCase):
    def test_batch_acceptance_is_private_repeatable_and_failure_isolated(self):
        repository = Path(__file__).resolve().parents[1]
        report = run_acceptance_suite(repository)
        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(report.fixture_files, 5)
        self.assertEqual(report.unique_sources, 4)
        self.assertEqual(report.documents, 6)
        self.assertEqual(report.split_cards, 3)
        self.assertGreater(report.first_run_pending, 0)
        self.assertEqual(report.duplicate_second_run, 5)
        self.assertEqual(report.isolated_failure_jobs, 1)
        self.assertEqual(report.isolated_success_documents, 1)
        self.assertEqual(report.network_requests, 0)


if __name__ == "__main__":
    unittest.main()
