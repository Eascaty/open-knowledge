from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from knowledge_os.automation import run_full_pipeline
from knowledge_os.config import ProjectPaths, initialize_layout
from knowledge_os.operations import project_backup
from knowledge_os.operations.project_backup import (
    DATABASE_MEMBER,
    MANIFEST_NAME,
    ProjectBackupError,
    package_project,
    verify_project_backup,
)


class ProjectBackupTests(unittest.TestCase):
    def _project(self, root: Path) -> ProjectPaths:
        paths = ProjectPaths.from_root(root)
        initialize_layout(paths)
        (paths.inbox_dir / "files" / "source.md").write_text(
            "# Java 备份演练\n\nSQLite 一致性快照用于验证知识库备份。\n",
            encoding="utf-8",
        )
        result = run_full_pipeline(root)
        self.assertTrue(result.ok)
        self.assertEqual(result.documents, 1)
        (paths.inbox_dir / "files" / "pending.md").write_text("待处理\n", encoding="utf-8")
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
            self.assertTrue(any(name.startswith("workspace/data/raw/") for name in names))
            self.assertTrue(any(name.startswith("workspace/vault/") for name in names))
            self.assertIn("workspace/site/dist/index.html", names)
            self.assertIn("workspace/inbox/files/pending.md", names)
            self.assertNotIn("workspace/data/state/knowledge.sqlite3-wal", names)
            self.assertEqual(manifest["bundle_type"], "knowledge-project-backup")
            self.assertEqual(manifest["database_counts"]["sources"], 1)
            self.assertEqual(manifest["database_counts"]["documents"], 1)
            verified = verify_project_backup(result.package, expected_sha256=result.sha256)
            self.assertEqual(verified.schema_version, 2)
            self.assertEqual(before, hashlib.sha256(paths.database_file.read_bytes()).hexdigest())
            self.assertFalse(verified.package == paths.database_file)

    def test_corrupt_candidate_is_rejected_without_replacing_previous_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._project(root)
            output = paths.private_exports_dir / "project-backups"
            previous = package_project(root, output)
            previous_bytes = previous.package.read_bytes()
            database_bytes = paths.database_file.read_bytes()
            raw_path = next(paths.raw_dir.rglob("*.md"))
            raw_bytes = raw_path.read_bytes()
            write_member = project_backup._write_member

            def write_corrupt_member(archive, source, relative):
                if relative.startswith("workspace/data/raw/"):
                    archive.writestr(relative, b"corrupt archive member\n")
                else:
                    write_member(archive, source, relative)

            with patch.object(project_backup, "_write_member", side_effect=write_corrupt_member):
                with self.assertRaisesRegex(ProjectBackupError, "digest"):
                    package_project(root, output)

            self.assertEqual(set(output.iterdir()), {previous.package})
            self.assertEqual(previous.package.read_bytes(), previous_bytes)
            self.assertEqual(paths.database_file.read_bytes(), database_bytes)
            self.assertEqual(raw_path.read_bytes(), raw_bytes)
            verify_project_backup(previous.package, expected_sha256=previous.sha256)

    def test_rejects_symlink_in_project_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._project(root)
            try:
                (paths.raw_dir / "escape.md").symlink_to(next(paths.vault_dir.rglob("*.md")))
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
                    if info.filename.startswith("workspace/data/raw/"):
                        content = b"tampered\n"
                    target.writestr(info, content)
            with self.assertRaisesRegex(ProjectBackupError, "digest"):
                verify_project_backup(tampered)


if __name__ == "__main__":
    unittest.main()
