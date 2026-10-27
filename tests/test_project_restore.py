from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from knowledge_os.automation import run_full_pipeline
from knowledge_os.config import ProjectPaths, initialize_layout
from knowledge_os.operations.classification import correct_document_classification
from knowledge_os.operations.review import change_document_review
from knowledge_os.operations.project_backup import MANIFEST_NAME, ProjectBackupError, package_project
from knowledge_os.operations.project_restore import restore_project_backup
from knowledge_os.storage.documents import query_documents


class ProjectRestoreTests(unittest.TestCase):
    def _bundle(self, root):
        paths = ProjectPaths.from_root(root)
        initialize_layout(paths)
        (paths.inbox_dir / 'files' / 'source.md').write_text('# Java restore\n\nSQLite recovery knowledge.\n')
        self.assertTrue(run_full_pipeline(root).ok)
        with closing(sqlite3.connect(paths.database_file)) as connection:
            document_id = connection.execute('SELECT id FROM documents').fetchone()[0]
            old_node = connection.execute('SELECT node_id FROM placements').fetchone()[0]
            target = connection.execute('SELECT id FROM nodes WHERE active=1 AND parent_id IS NOT NULL AND id!=?', (old_node,)).fetchone()[0]
        correct_document_classification(root, document_id, target)
        change_document_review(root, document_id, 'personal', note='Synthetic recovery review')
        (paths.inbox_dir / 'files' / 'pending.md').write_text('Not ingested during restoration')
        bundle = package_project(root, paths.private_exports_dir / 'project-backups')
        return paths, bundle, document_id, target

    def test_restores_in_new_root_preserving_database_raw_review_and_search(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            paths, bundle, document_id, target = self._bundle(parent / 'original')
            original_db = paths.database_file.read_bytes()
            original_raw = {p.relative_to(paths.root): p.read_bytes() for p in paths.raw_dir.rglob('*') if p.is_file()}
            restored = parent / '恢复目录'
            # A configured model must never be called by restore.
            with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
                result = restore_project_backup(bundle.package, restored, expected_sha256=bundle.sha256)
            self.assertEqual(json.loads((restored / "restore-result.json").read_text())["status"], "verified")
            self.assertEqual(result.documents, 1)
            self.assertEqual(result.sources, 1)
            recovered = ProjectPaths.from_root(restored)
            with zipfile.ZipFile(bundle.package) as archive:
                self.assertEqual(recovered.database_file.read_bytes(), archive.read('workspace/data/state/knowledge.sqlite3'))
            self.assertEqual(paths.database_file.read_bytes(), original_db)
            for name, data in original_raw.items():
                self.assertEqual((restored / name).read_bytes(), data)
                self.assertEqual((paths.root / name).read_bytes(), data)
            with closing(sqlite3.connect(recovered.database_file)) as connection:
                connection.row_factory = sqlite3.Row
                self.assertEqual(query_documents(connection, 'SQLite')[0]['id'], document_id)
            canonical = json.loads((recovered.site_data_dir / 'site-data.json').read_text())
            card = canonical['documents'][0]
            self.assertEqual(card['node_id'], target)
            self.assertEqual(card['status'], 'personal')
            self.assertEqual(card['review']['note'], 'Synthetic recovery review')
            self.assertTrue(card['review']['history'])
            self.assertTrue((recovered.site_dir / 'dist' / 'data' / 'search-index.json').is_file())
            self.assertTrue((recovered.inbox_dir / 'files' / 'pending.md').is_file())
            self.assertFalse((recovered.state_dir / 'manager.json').exists())
            self.assertEqual(restored.stat().st_mode & 0o777, 0o700)
            with self.assertRaisesRegex(ProjectBackupError, 'must not exist'):
                restore_project_backup(bundle.package, restored)

    def test_corrupt_bundle_or_wrong_digest_leaves_no_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            _paths, bundle, _id, _target = self._bundle(parent / 'original')
            destination = parent / 'recovered'
            with self.assertRaises(ProjectBackupError):
                restore_project_backup(bundle.package, destination, expected_sha256='0' * 64)
            self.assertFalse(destination.exists())
            self.assertEqual(list(parent.glob('.knowledge-restore-*')), [])

    def test_missing_source_reference_rejects_otherwise_valid_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            paths, _bundle, _id, _target = self._bundle(parent / 'original')
            with closing(sqlite3.connect(paths.database_file)) as connection:
                connection.execute("UPDATE sources SET raw_path='workspace/data/raw/missing.md'")
                connection.commit()
            bundle = package_project(paths.root, paths.private_exports_dir / 'project-backups')
            with self.assertRaisesRegex(ProjectBackupError, 'missing backup file'):
                restore_project_backup(bundle.package, parent / 'recovered')
            self.assertFalse((parent / 'recovered').exists())
            self.assertEqual(list(parent.glob('.knowledge-restore-*')), [])

    def test_rebuild_failure_and_concurrent_destination_preserve_existing_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            _paths, bundle, _id, _target = self._bundle(parent / 'original')
            destination = parent / 'recovered'
            with patch('knowledge_os.operations.project_restore._rebuild', side_effect=ProjectBackupError('gate failure')):
                with self.assertRaisesRegex(ProjectBackupError, 'gate failure'):
                    restore_project_backup(bundle.package, destination)
            self.assertFalse(destination.exists())
            def concurrent(_root):
                destination.mkdir()
                (destination / 'keep.txt').write_text('keep')
                return 1, 1
            with patch('knowledge_os.operations.project_restore._rebuild', side_effect=concurrent):
                with self.assertRaises(ProjectBackupError):
                    restore_project_backup(bundle.package, destination)
            self.assertEqual((destination / 'keep.txt').read_text(), 'keep')

    def test_self_consistent_archive_cannot_write_outside_allowed_roots(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            _paths, bundle, _id, _target = self._bundle(parent / 'original')
            for name in ['../escape.txt', 'config/untrusted.py', 'workspace/data/raw/../escape.txt']:
                changed = parent / 'changed.zip'
                content = b'forbidden'
                with zipfile.ZipFile(bundle.package) as source, zipfile.ZipFile(changed, 'w') as output:
                    manifest = json.loads(source.read(MANIFEST_NAME))
                    manifest['files'].append({'path': name, 'size_bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest(), 'kind': 'raw'})
                    manifest['file_count'] += 1
                    manifest['total_bytes'] += len(content)
                    for info in source.infolist():
                        if info.filename != MANIFEST_NAME:
                            output.writestr(info, source.read(info))
                    output.writestr(name, content)
                    output.writestr(MANIFEST_NAME, json.dumps(manifest))
                with self.assertRaises(ProjectBackupError):
                    restore_project_backup(changed, parent / 'recovered')
                self.assertFalse((parent / 'recovered').exists())
            self.assertFalse((parent / 'escape.txt').exists())

    def test_publish_failure_cleans_reserved_root_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            paths, bundle, _id, _target = self._bundle(parent / 'original')
            before = paths.database_file.read_bytes()
            import os
            rename = os.rename
            calls = []
            def fail_second(source, destination):
                calls.append(destination)
                if len(calls) == 2:
                    raise OSError('synthetic disk failure')
                return rename(source, destination)
            with patch('knowledge_os.operations.project_restore.os.rename', side_effect=fail_second):
                with self.assertRaises(ProjectBackupError):
                    restore_project_backup(bundle.package, parent / 'recovered')
            self.assertEqual(len(calls), 2)
            self.assertFalse((parent / 'recovered').exists())
            self.assertEqual(list(parent.glob('.knowledge-restore-*')), [])
            self.assertEqual(paths.database_file.read_bytes(), before)

    def test_symlink_destination_and_source_hash_mismatch_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            paths, bundle, _id, _target = self._bundle(parent / 'original')
            link = parent / 'recovered'
            link.symlink_to(parent / 'absent')
            with self.assertRaisesRegex(ProjectBackupError, 'must not exist'):
                restore_project_backup(bundle.package, link)
            link.unlink()
            raw = next(paths.raw_dir.rglob('*.md'))
            raw.write_text('modified source with a valid archive digest')
            changed = package_project(paths.root, paths.private_exports_dir / 'project-backups')
            with self.assertRaisesRegex(ProjectBackupError, 'differs from database'):
                restore_project_backup(changed.package, link)
            self.assertFalse(link.exists())


if __name__ == '__main__':
    unittest.main()
