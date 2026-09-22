"""Verify benchmark failures are actionable, not just timing output."""
import json
import runpy
import tempfile
import unittest
from pathlib import Path

BENCHMARK = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/benchmark-site"))


class SiteBenchmarkTests(unittest.TestCase):
    def test_small_fixture_preserves_private_and_filters_public(self):
        for visibility, expected in (("private", 20), ("public", 16)):
            with self.subTest(visibility=visibility), tempfile.TemporaryDirectory() as temporary:
                data = BENCHMARK["fixture"](20, visibility)
                output = Path(temporary) / "site"
                BENCHMARK["build_site"](data, output, visibility=visibility)
                result = BENCHMARK["verify"](output, data, visibility)
                self.assertEqual(result["output_cards"], expected)

    def test_missing_search_card_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = BENCHMARK["fixture"](20, "private")
            output = Path(temporary) / "site"
            BENCHMARK["build_site"](data, output)
            path = output / "data/search-index.json"
            search = json.loads(path.read_text())
            search["items"] = [item for item in search["items"] if item["id"] != "card-00001"]
            path.write_text(json.dumps(search))
            with self.assertRaisesRegex(RuntimeError, "Search card IDs differ"):
                BENCHMARK["verify"](output, data, "private")

    def test_private_marker_in_public_shell_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = BENCHMARK["fixture"](20, "public")
            output = Path(temporary) / "site"
            BENCHMARK["build_site"](data, output, visibility="public")
            path = output / "index.html"
            path.write_text(path.read_text() + "<!-- FICTION_REVIEW_ONLY -->")
            with self.assertRaisesRegex(RuntimeError, "private marker leaked"):
                BENCHMARK["verify"](output, data, "public")
