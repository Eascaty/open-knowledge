from __future__ import annotations
import http.client
import io
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
from knowledge_os.local_source import source_preview, SourcePreviewError, MAX_PREVIEW_CHARS
from test_local_manager import _running_http_server

class LocalSourceTests(unittest.TestCase):
    def test_html_and_docx_preview_match_pipeline_without_modifying_sources(self):
        from knowledge_os.processing.extraction import extract_source
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>虚构 Word 来源</w:t></w:r></w:p><w:p><w:r><w:t>核对条件与上下文</w:t></w:r></w:p></w:body></w:document>')
        samples = {
            'example.docx': archive.getvalue(),
            'example.html': '<html><body><h1>虚构网页来源</h1><script>DO_NOT_SHOW</script><p>核对条件与上下文</p></body></html>'.encode(),
        }
        for name, data in samples.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as t:
                paths = ProjectPaths.from_root(Path(t))
                initialize_layout(paths)
                original = paths.inbox_dir / 'files' / name
                original.write_bytes(data)
                expected = extract_source(original, name)[1]
                self.assertTrue(run_full_pipeline(paths.root).ok)
                with closing(sqlite3.connect(paths.database_file)) as c:
                    doc_id, raw_path = c.execute('SELECT d.id,s.raw_path FROM documents d JOIN sources s ON s.id=d.source_id').fetchone()
                database = paths.database_file.read_bytes()
                result = source_preview(paths.root, json.dumps({'document_id': doc_id}).encode())
                self.assertEqual(result['text'], expected)
                self.assertEqual(result['locator_basis'], 'extracted-source-text')
                self.assertNotIn('DO_NOT_SHOW', result['text'])
                self.assertEqual((paths.root / raw_path).read_bytes(), data)
                self.assertEqual(paths.database_file.read_bytes(), database)

    def test_invalid_docx_is_reported_without_parser_details(self):
        with tempfile.TemporaryDirectory() as t:
            paths, body, _ = self._project(Path(t))
            with closing(sqlite3.connect(paths.database_file)) as c:
                c.execute("UPDATE sources SET original_name='broken.docx'")
                c.commit()
            with self.assertRaisesRegex(SourcePreviewError, '提取失败') as error:
                source_preview(paths.root, body)
            self.assertEqual(error.exception.status, 422)

    def _project(self, root, text='# 原件\n\n<script>literal</script>\n\n可核对的原文\n'):
        paths = ProjectPaths.from_root(root)
        initialize_layout(paths)
        (paths.inbox_dir / 'files' / 'example.md').write_text(text)
        self.assertTrue(run_full_pipeline(root).ok)
        with closing(sqlite3.connect(paths.database_file)) as c:
            row = c.execute('SELECT d.id,s.raw_path FROM documents d JOIN sources s ON s.id=d.source_id').fetchone()
        return paths, json.dumps({'document_id':row[0]}).encode(), paths.root/row[1]

    def test_original_text_preview_is_bounded_and_read_only(self):
        with tempfile.TemporaryDirectory() as t:
            paths, body, raw = self._project(Path(t))
            original = raw.read_bytes(); database = paths.database_file.read_bytes()
            result = source_preview(paths.root, body)
            self.assertIn('<script>literal</script>', result['text'])
            self.assertEqual(result['line_start'],1)
            self.assertNotIn('raw_path', result)
            self.assertNotIn(str(paths.root), json.dumps(result))
            self.assertEqual(raw.read_bytes(), original)
            self.assertEqual(paths.database_file.read_bytes(), database)
            with closing(sqlite3.connect(paths.database_file)) as c:
                c.execute('UPDATE documents SET source_line_start=3,source_line_end=3');c.commit()
            fragment = source_preview(paths.root, body)
            self.assertEqual(fragment['text'], '<script>literal</script>')
            self.assertEqual(fragment['line_end'],3)

    def test_changed_missing_and_escaping_sources_are_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            paths, body, raw = self._project(Path(t))
            raw.write_text('changed')
            with self.assertRaisesRegex(SourcePreviewError, '摘要'):
                source_preview(paths.root,body)
            raw.unlink()
            with self.assertRaisesRegex(SourcePreviewError, '不可用'):
                source_preview(paths.root,body)
            with closing(sqlite3.connect(paths.database_file)) as c:
                c.execute("UPDATE sources SET raw_path='../outside.md'");c.commit()
            with self.assertRaisesRegex(SourcePreviewError, '引用无效'):
                source_preview(paths.root,body)

    def test_large_preview_and_unsupported_format_have_explicit_boundaries(self):
        with tempfile.TemporaryDirectory() as t:
            paths,body,raw=self._project(Path(t), '# 长文\n\n'+'文字'*9000)
            result=source_preview(paths.root,body)
            self.assertTrue(result['truncated'])
            self.assertLessEqual(len(result['text']),MAX_PREVIEW_CHARS)
            with closing(sqlite3.connect(paths.database_file)) as c:
                c.execute("UPDATE sources SET original_name='example.pdf'");c.commit()
            with self.assertRaisesRegex(SourcePreviewError,'格式暂不支持'):
                source_preview(paths.root,body)
            with self.assertRaises(SourcePreviewError):
                source_preview(paths.root,b'{"path":"/etc/passwd"}')

    def test_http_requires_session_origin_and_bounded_json_and_never_caches(self):
        with tempfile.TemporaryDirectory() as t, _running_http_server(Path(t)) as (manager,port):
            def request(headers):
                connection=http.client.HTTPConnection('127.0.0.1',port)
                connection.request('POST','/__knowledge/source',body=b'{"document_id":"sample"}',headers=headers)
                response=connection.getresponse(); data=response.read(); status=response.status; cache=response.getheader('Cache-Control');connection.close()
                return status,cache,data
            headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{port}','X-Knowledge-Session':manager.browser_token}
            with patch('knowledge_os.local_http.source_preview',return_value={'ok':True,'text':'original'}) as preview:
                self.assertEqual(request({})[0],403)
                self.assertEqual(request(dict(headers,Origin='https://evil.example'))[0],403)
                self.assertEqual(preview.call_count,0)
                status,cache,data=request(headers)
                self.assertEqual(status,200);self.assertIn('no-store',cache)
                self.assertEqual(json.loads(data)['text'],'original')
                self.assertEqual(request(dict(headers,**{'Content-Encoding':'gzip'}))[0],400)
