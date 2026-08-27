from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from knowledge_os.automation import run_full_pipeline
from knowledge_os.config import ProjectPaths


class FullPipelineTests(unittest.TestCase):
    def test_inbox_to_checked_static_site(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            inbox = paths.inbox_dir / "files"
            inbox.mkdir(parents=True)
            fixtures = Path(__file__).parent.parent / "fixtures"
            for fixture in fixtures.glob("*.md"):
                shutil.copyfile(fixture, inbox / fixture.name)
            result = run_full_pipeline(root)
            self.assertTrue(result.ok)
            self.assertEqual(result.ingested, 3)
            self.assertEqual(result.jobs_completed, 9)
            self.assertEqual(result.documents, 3)
            self.assertTrue(result.gate_allowed)
            self.assertEqual(result.health_status, "PASS")
            self.assertEqual(result.site_output, str(paths.site_dir / "dist"))
            self.assertEqual(
                list(paths.site_dir.glob(".dist-candidate-*")), []
            )
            canonical = json.loads(
                (paths.site_data_dir / "site-data.json").read_text(
                    encoding="utf-8"
                )
            )
            paths = {tuple(document["path"]) for document in canonical["documents"]}
            self.assertIn(("AI", "Agent", "智能体"), paths)
            self.assertIn(("金融", "财经", "信用卡", "美股"), paths)
            self.assertIn(
                (
                    "技术",
                    "程序员",
                    "Java开发",
                    "JVM",
                    "垃圾回收",
                    "G1",
                ),
                paths,
            )
            second = run_full_pipeline(root)
            self.assertTrue(second.ok)
            self.assertEqual(second.ingested, 0)
            self.assertEqual(second.duplicates, 3)
            self.assertEqual(second.jobs_claimed, 0)


if __name__ == "__main__":
    unittest.main()
