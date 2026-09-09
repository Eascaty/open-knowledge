from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGER = runpy.run_path(str(ROOT / 'scripts/package-local'))


class LocalPackageTests(unittest.TestCase):
    def _extract(self, package, destination):
        # This archive was built locally from the explicit source allowlist.
        with tarfile.open(package) as archive:
            for member in archive.getmembers():
                self.assertTrue(member.isfile())
                self.assertNotIn('..', Path(member.name).parts)
                target = destination / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
                target.chmod(member.mode)
        return next(destination.iterdir())

    def test_reproducible_archive_manifest_permissions_and_private_exclusion(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            package = PACKAGER['build'](ROOT, output)
            first = package.read_bytes()
            self.assertEqual(PACKAGER['build'](ROOT, output).read_bytes(), first)
            checksum = package.with_name(package.name + '.sha256').read_text().split()[0]
            self.assertEqual(checksum, hashlib.sha256(first).hexdigest())
            install = self._extract(package, output / 'install')
            manifest = json.loads((install / 'local-package.json').read_text())
            self.assertFalse(manifest['data_included'])
            for entry in manifest['files']:
                data = (install / entry['path']).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'])
                self.assertEqual(len(data), entry['size_bytes'])
            self.assertEqual((install / 'scripts/knowledge-manager').stat().st_mode & 0o777, 0o755)
            for forbidden in ['workspace', '.git', 'config', 'apps/api', 'tests']:
                self.assertFalse((install / forbidden).exists())
            self.assertTrue((install / 'examples/java_g1.md').is_file())

    def test_detached_package_import_search_backup_and_restore(self):
        with tempfile.TemporaryDirectory(prefix='knowledge-local-') as temporary:
            parent = Path(temporary)
            package = PACKAGER['build'](ROOT, parent / 'packages')
            install = self._extract(package, parent / '安装包 with spaces')
            environment = dict(os.environ, PYTHONPATH='', PYTHONDONTWRITEBYTECODE='1')
            def command(*args):
                result = subprocess.run(args, cwd=install, env=environment, text=True,
                                        capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout
            command('sh', 'scripts/kb', 'init')
            inbox = install / 'workspace/inbox/files'
            (inbox / 'sample.md').write_bytes((install / 'examples/java_g1.md').read_bytes())
            first = json.loads(command('sh', 'scripts/run-pipeline'))
            self.assertTrue(first['ok'])
            self.assertGreater(first['documents'], 0)
            second = json.loads(command('sh', 'scripts/run-pipeline'))
            self.assertEqual(first['documents'], second['documents'])
            self.assertEqual(second['ingested'], 0)
            command('sh', 'scripts/doctor')
            bundle = json.loads(command('sh', 'scripts/backup-bundle'))
            restored = parent / '恢复的资料'
            result = json.loads(command('sh', 'scripts/restore-backup-bundle', bundle['package'], str(restored), '--sha256', bundle['sha256']))
            self.assertEqual(result['documents'], first['documents'])
            smoke = """
import json
from pathlib import Path
from knowledge_os import db
from knowledge_os.contracts import CANONICAL_SCHEMA
from knowledge_os.site.build.model import ASSET_DIR
root=Path.cwd()
assert ASSET_DIR.is_relative_to(root)
assert CANONICAL_SCHEMA.is_relative_to(root)
c=db.connect(root/'workspace/data/state/knowledge.sqlite3')
assert db.query_documents(c,'Java')
c.close()
print('isolated resources and search passed')
"""
            environment['PYTHONPATH'] = str(install / 'apps/pipeline/src')
            command(sys.executable, '-B', '-c', smoke)
            import socket
            import urllib.request
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            try:
                status = json.loads(command('sh', 'scripts/knowledge-manager', 'start',
                                            '--port', str(port), '--no-open', '--json'))
                with urllib.request.urlopen(status['url'], timeout=10) as response:
                    self.assertEqual(response.status, 200)
                command('sh', 'scripts/knowledge-manager', 'status', '--json')
            finally:
                command('sh', 'scripts/knowledge-manager', 'stop')

    def test_rejects_symlink_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / 'pyproject.toml').write_text('version = "0.5.0"\n')
            source = root / 'LICENSE'
            source.parent.mkdir(parents=True, exist_ok=True)
            source.symlink_to(root / 'pyproject.toml')
            with patch('subprocess.check_output', side_effect=[str(source.relative_to(root)) + '\0', 'a' * 40]):
                with self.assertRaisesRegex(ValueError, 'symbolic'):
                    PACKAGER['build'](root, root / 'out')


if __name__ == '__main__':
    unittest.main()
