from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from knowledge_os.operations.site_package import (
    MANIFEST_NAME,
    SitePackageError,
    package_site,
)


class SitePackageTests(unittest.TestCase):
    def test_package_contains_only_site_files_and_integrity_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "site"
            output = root / "workspace" / "exports" / "private" / "site-packages"
            (source / "data").mkdir(parents=True)
            (source / "index.html").write_text("<h1>private</h1>\n", encoding="utf-8")
            (source / "data" / "site-data.json").write_text(
                json.dumps({"site": {"visibility": "private"}}), encoding="utf-8"
            )
            result = package_site(source, output, visibility="private")

            self.assertTrue(result.package.is_file())
            with zipfile.ZipFile(result.package) as archive:
                names = set(archive.namelist())
                self.assertEqual(names, {"index.html", "data/site-data.json", MANIFEST_NAME})
                manifest = json.loads(archive.read(MANIFEST_NAME))
            self.assertEqual(manifest["visibility"], "private")
            self.assertEqual(manifest["file_count"], 2)
            self.assertEqual(result.file_count, 2)

    def test_rejects_symlinked_site_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "site"
            output = root / "out"
            source.mkdir()
            (source / "index.html").write_text("ok", encoding="utf-8")
            try:
                (source / "secret.txt").symlink_to(source / "index.html")
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links unavailable")
            with self.assertRaisesRegex(SitePackageError, "symbolic"):
                package_site(source, output, visibility="private")

    def test_rejects_visibility_mismatch_from_build_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "site"
            source.mkdir()
            (source / "index.html").write_text("ok", encoding="utf-8")
            (source / "build-meta.json").write_text(
                json.dumps({"visibility": "public"}), encoding="utf-8"
            )
            with self.assertRaisesRegex(SitePackageError, "visibility"):
                package_site(root / "site", root / "out", visibility="private")


if __name__ == "__main__":
    unittest.main()
