from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from knowledge_os import db
from knowledge_os.config import ProjectPaths, initialize_layout
from knowledge_os.operations.project_backup import (
    DATABASE_MEMBER,
    MANIFEST_NAME,
    ProjectBackupError,
    package_project,
    verify_project_backup,
)
from knowledge_os.site import build_site


class ProjectBackupTests(unittest.TestCase):
    def _project(self, root: Path) -> ProjectPaths:
        paths = ProjectPaths.from_root(root)
        initialize_layout(paths)
        connection = db.connect(paths.database_file)
        try:
            db.initialize_database(connection)
        finally:
            connection.close()
        (paths.raw_dir / "source.md").write_text("# 原始资料\n", encoding="utf-8")
        (paths.normalized_dir / "source.md").write_text("原始资料\n", encoding="utf-8")
        (paths.vault_dir / "技术").mkdir(parents=True, exist_ok=True)
        (paths.vault_dir / "技术" / "source.md").write_text("知识卡\n", encoding="utf-8")
        (paths.inbox_dir / "files" / "pending.md").write_text("待处理\n", encoding="utf-8")
        build_site(
            {
                "schema_version": 1,
                "generated_at": "2026-09-06T00:00:00Z",
                "root": "root",
                "site": {"title": "Knowledge OS"},
                "nodes": [{"id": "root", "parent_id": None, "name": "知识", "path": []}],
                "documents": [],
                "relations": [],
            },
            paths.site_dir / "dist",
            visibility="private",
        )
        return paths

    def test_bundle_contains_project_state_and_verifies_without_touching_live_db(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._project(root)
            output = paths.private_exports_dir / "project-backups"
            before = hashlib.sha256(paths.database_file.read_bytes()).hexdigest()
            result = package_project(root, output)
            with zipfile.ZipFile(result.package) as archive:
                names = set(archive.namelist())
                manifest = json.loads(archive.read(MANIFEST_NAME))
            self.assertIn(DATABASE_MEMBER, names)
            self.assertIn("workspace/data/raw/source.md", names)
            self.assertIn("workspace/vault/技术/source.md", names)
            self.assertIn("workspace/site/dist/index.html", names)
            self.assertNotIn("workspace/data/state/knowledge.sqlite3-wal", names)
            self.assertEqual(manifest["bundle_type"], "knowledge-project-backup")
            verified = verify_project_backup(result.package, expected_sha256=result.sha256)
            self.assertEqual(verified.schema_version, 2)
            self.assertEqual(before, hashlib.sha256(paths.database_file.read_bytes()).hexdigest())
            self.assertFalse(verified.package == paths.database_file)

    def test_rejects_symlink_in_project_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._project(root)
            try:
                (paths.raw_dir / "escape.md").symlink_to(paths.vault_dir / "技术" / "source.md")
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links unavailable")
            with self.assertRaisesRegex(ProjectBackupError, "symbolic"):
                package_project(root, paths.private_exports_dir / "project-backups")

    def test_verification_rejects_tampered_member(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._project(root)
            result = package_project(root, paths.private_exports_dir / "project-backups")
            tampered = root / "tampered.zip"
            with zipfile.ZipFile(result.package) as source, zipfile.ZipFile(
                tampered, "w", compression=zipfile.ZIP_DEFLATED
            ) as target:
                for info in source.infolist():
                    content = source.read(info.filename)
                    if info.filename == "workspace/vault/技术/source.md":
                        content = b"tampered\n"
                    target.writestr(info, content)
            with self.assertRaisesRegex(ProjectBackupError, "digest"):
                verify_project_backup(tampered)


if __name__ == "__main__":
    unittest.main()
