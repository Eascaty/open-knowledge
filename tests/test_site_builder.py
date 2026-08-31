from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from knowledge_os.site import SiteDataError, build_site
from knowledge_os.site.build import builder as site_builder_impl


def sample_data():
    return {
        "schema_version": 1,
        "generated_at": "2026-07-27T00:00:00Z",
        "root": "root",
        "site": {"title": "测试知识库"},
        "nodes": [
            {
                "id": "root",
                "parent_id": None,
                "name": "我的知识体系",
                "path": [],
            },
            {
                "id": "tech",
                "parent_id": "root",
                "name": "技术",
                "path": ["技术"],
            },
        ],
        "documents": [
            {
                "id": "public",
                "source_id": "source-shared",
                "title": "公开文档",
                "summary": "公开摘要",
                "content": "PUBLIC_ONLY",
                "path": ["技术"],
                "node_id": "tech",
                "source": {
                    "origin": "https://example.com/a?X-Amz-Signature=secret",
                    "original_name": "public.md",
                },
                "evidence": [],
                "tags": ["公开"],
                "visibility": "public",
                "section_index": 1,
                "heading_path": ["公开资料", "第一节"],
                "source_line_start": 10,
                "source_line_end": 20,
                "body_sha256": hashlib.sha256(b"PUBLIC_ONLY").hexdigest(),
                "splitter_version": "markdown-h2-v1",
            },
            {
                "id": "private",
                "title": "私密文档",
                "summary": "私密摘要",
                "content": "PRIVATE_SENTINEL",
                "path": ["技术"],
                "node_id": "tech",
                "source": {"origin": "local-file", "original_name": "private.md"},
                "evidence": [],
                "tags": ["私密"],
                "visibility": "private",
            },
        ],
        "relations": [],
    }


class SiteBuilderTests(unittest.TestCase):
    def test_public_filters_private_and_drops_url_query(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "public"
            result = build_site(
                sample_data(),
                output,
                visibility="public",
                allow_indexing=True,
            )
            payload = (output / "data" / "site-data.json").read_text(
                encoding="utf-8"
            )
            self.assertEqual(result.document_count, 1)
            self.assertNotIn("PRIVATE_SENTINEL", payload)
            self.assertNotIn("X-Amz-Signature", payload)
            self.assertNotIn("secret", payload)
            document = json.loads(payload)["documents"][0]
            self.assertEqual(document["section_index"], 1)
            self.assertEqual(document["heading_path"], ["公开资料", "第一节"])
            self.assertEqual(document["source_line_start"], 10)
            self.assertEqual(document["source_line_end"], 20)
            self.assertEqual(document["splitter_version"], "markdown-h2-v1")
            self.assertRegex(document["body_sha256"], r"^[0-9a-f]{64}$")

    def test_private_bundle_is_noindex_and_does_not_cache_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "private"
            build_site(sample_data(), output, visibility="private")
            headers = (output / "_headers").read_text(encoding="utf-8")
            worker = (output / "service-worker.js").read_text(encoding="utf-8")
            index = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("X-Robots-Tag: noindex", headers)
            self.assertIn("private, no-store", headers)
            self.assertIn("/build-meta.json", headers)
            self.assertIn("/__knowledge/*", headers)
            self.assertNotIn("./data/site-data.json", worker)
            self.assertIn('cache: "no-store"', worker)
            self.assertIn("./assets/data-source.js", worker)
            self.assertIn("./assets/related-documents.js", worker)
            self.assertIn("./assets/local-ingest.js", worker)
            self.assertIn("./assets/local-ingest.css", worker)
            self.assertIn('pathname.includes("/__knowledge/")', worker)
            self.assertIn('pathname.endsWith("/build-meta.json")', worker)
            self.assertLess(
                index.index("data-source.js"), index.index("related-documents.js")
            )
            self.assertLess(
                index.index("related-documents.js"), index.index("local-ingest.js")
            )
            self.assertLess(index.index("local-ingest.js"), index.index("app.js"))
            self.assertTrue((output / "assets" / "data-source.js").is_file())
            self.assertTrue(
                (output / "assets" / "related-documents.js").is_file()
            )
            self.assertTrue((output / "assets" / "local-ingest.js").is_file())
            self.assertTrue((output / "assets" / "local-ingest.css").is_file())

    def test_private_worker_only_uses_install_time_shell_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "private"
            build_site(sample_data(), output, visibility="private")
            worker = (output / "service-worker.js").read_text(encoding="utf-8")

            self.assertIn("knowledge-os-private-shell-v3-", worker)
            self.assertIn("cache.addAll(CACHE_FILES)", worker)
            self.assertNotIn("cache.put(", worker)
            self.assertIn("decodeURIComponent(url.pathname)", worker)
            self.assertIn(
                "decodedPathname(url).toLocaleLowerCase()", worker
            )
            self.assertIn('pathname.includes("/data/")', worker)
            self.assertIn('cache.match(event.request)', worker)
            self.assertIn('cache.match("./offline.html")', worker)

            public_output = Path(temporary) / "public"
            build_site(sample_data(), public_output, visibility="public")
            public_worker = (public_output / "service-worker.js").read_text(
                encoding="utf-8"
            )
            self.assertIn("cache.put(", public_worker)

    def test_cache_version_changes_when_browser_asset_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = root / "web"
            shutil.copytree(site_builder_impl.ASSET_DIR, assets)
            first_output = root / "first"
            second_output = root / "second"

            with mock.patch.object(site_builder_impl, "ASSET_DIR", assets):
                first = build_site(sample_data(), first_output, visibility="private")
                first_worker = (first_output / "service-worker.js").read_text(
                    encoding="utf-8"
                )
                (assets / "local-ingest.js").write_text(
                    (assets / "local-ingest.js").read_text(encoding="utf-8")
                    + "\n// cache-version-test\n",
                    encoding="utf-8",
                )
                second = build_site(sample_data(), second_output, visibility="private")
                second_worker = (second_output / "service-worker.js").read_text(
                    encoding="utf-8"
                )

            cache_pattern = re.compile(r'const CACHE_NAME = "([^"]+)";')
            first_cache = cache_pattern.search(first_worker)
            second_cache = cache_pattern.search(second_worker)
            self.assertIsNotNone(first_cache)
            self.assertIsNotNone(second_cache)
            self.assertEqual(first.content_digest, second.content_digest)
            self.assertNotEqual(first_cache.group(1), second_cache.group(1))
            first_meta = json.loads(
                (first_output / "build-meta.json").read_text(encoding="utf-8")
            )
            second_meta = json.loads(
                (second_output / "build-meta.json").read_text(encoding="utf-8")
            )
            self.assertNotEqual(
                first_meta["cache_version"], second_meta["cache_version"]
            )

    def test_cache_version_changes_when_knowledge_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_output = root / "first"
            second_output = root / "second"
            changed = sample_data()
            changed["documents"][0]["summary"] = "修改后的摘要"

            first = build_site(sample_data(), first_output, visibility="private")
            second = build_site(changed, second_output, visibility="private")
            first_meta = json.loads(
                (first_output / "build-meta.json").read_text(encoding="utf-8")
            )
            second_meta = json.loads(
                (second_output / "build-meta.json").read_text(encoding="utf-8")
            )

            self.assertNotEqual(first.content_digest, second.content_digest)
            self.assertNotEqual(
                first_meta["cache_version"], second_meta["cache_version"]
            )

    def test_bad_canonical_does_not_replace_old_site(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "dist"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("old", encoding="utf-8")
            with self.assertRaises((SiteDataError, ValueError)):
                build_site(
                    {"schema_version": 1, "nodes": [], "documents": []},
                    output,
                )
            self.assertEqual(marker.read_text(encoding="utf-8"), "old")


if __name__ == "__main__":
    unittest.main()
