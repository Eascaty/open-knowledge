from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from knowledge_os.processing.extraction import ExtractionError, extract_source


def _docx_xml(text: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
        + text
        + "</w:t></w:r></w:p></w:body></w:document>"
    )


class ExtractionSecurityTests(unittest.TestCase):
    def test_normal_docx_is_still_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "note.docx"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("word/document.xml", _docx_xml("Java 安全笔记"))

            title, body = extract_source(path, path.name)

            self.assertEqual(title, "Java 安全笔记")
            self.assertEqual(body, "Java 安全笔记")

    def test_docx_with_extreme_compression_ratio_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "compressed-bomb.docx"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("word/document.xml", _docx_xml("A" * 1_000_000))

            with self.assertRaisesRegex(
                ExtractionError, "compression ratio exceeds the safe limit"
            ):
                extract_source(path, path.name)


if __name__ == "__main__":
    unittest.main()
