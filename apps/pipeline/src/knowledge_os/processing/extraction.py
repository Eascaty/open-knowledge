"""Local document extraction and normalization."""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional, Tuple
from xml.etree import ElementTree

class ExtractionError(RuntimeError):
    pass


class NeedsExternalTool(ExtractionError):
    pass



class _ReadableHTMLParser(HTMLParser):
    BLOCKS = {
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "p",
        "section",
        "tr",
    }
    SKIP = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]]
    ) -> None:
        lowered = tag.casefold()
        if lowered in self.SKIP:
            self.skip_depth += 1
        if lowered == "title":
            self._in_title = True
        if lowered in self.BLOCKS and not self.skip_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered == "title":
            self._in_title = False
        if lowered in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        if lowered in self.BLOCKS and not self.skip_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        value = data.strip()
        if not value:
            return
        if self._in_title:
            self.title = (self.title + " " + value).strip()
        self.parts.append(value)
        self.parts.append(" ")

    def text(self) -> str:
        value = "".join(self.parts)
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n[ \t]+", "\n", value)
        value = re.sub(r"[ \t]{2,}", " ", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


TEXT_EXTENSIONS = {
    ".c",
    ".cc",
    ".conf",
    ".cpp",
    ".css",
    ".csv",
    ".go",
    ".h",
    ".html",
    ".htm",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".log",
    ".md",
    ".properties",
    ".py",
    ".rb",
    ".rst",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
MAX_DOCX_XML_BYTES = 32 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 200


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    decoded = data.decode("utf-8", errors="replace")
    replacement_ratio = decoded.count("\ufffd") / max(1, len(decoded))
    if replacement_ratio > 0.02:
        raise ExtractionError("file does not look like supported text")
    return decoded


def _title_from_text(text: str, fallback: str) -> str:
    for line in text.splitlines():
        candidate = re.sub(r"^#{1,6}\s*", "", line).strip()
        if candidate:
            return candidate[:200]
    return Path(fallback).stem[:200] or "未命名资料"


def _extract_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(str(path)) as archive:
            document = archive.getinfo("word/document.xml")
            if document.flag_bits & 0x1:
                raise ExtractionError("encrypted DOCX is not supported")
            if document.file_size > MAX_DOCX_XML_BYTES:
                raise ExtractionError("DOCX document.xml exceeds the safe limit")
            compression_ratio = document.file_size / max(1, document.compress_size)
            if compression_ratio > MAX_DOCX_COMPRESSION_RATIO:
                raise ExtractionError("DOCX compression ratio exceeds the safe limit")
            with archive.open(document) as source:
                xml_data = source.read(MAX_DOCX_XML_BYTES + 1)
            if len(xml_data) > MAX_DOCX_XML_BYTES:
                raise ExtractionError("DOCX document.xml exceeds the safe limit")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ExtractionError(f"invalid DOCX: {exc}") from exc
    root = ElementTree.fromstring(xml_data)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: List[str] = []
    for paragraph in root.iter(namespace + "p"):
        pieces = [
            element.text or ""
            for element in paragraph.iter(namespace + "t")
            if element.text
        ]
        if pieces:
            paragraphs.append("".join(pieces))
    return "\n\n".join(paragraphs).strip()


def _extract_pdf(path: Path) -> str:
    executable = shutil.which("pdftotext")
    if not executable:
        raise NeedsExternalTool(
            "PDF extraction requires the free local 'pdftotext' command"
        )
    try:
        result = subprocess.run(
            [executable, "-layout", str(path), "-"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise ExtractionError(f"pdftotext failed: {exc}") from exc
    text = result.stdout.decode("utf-8", errors="replace").strip()
    if not text:
        raise NeedsExternalTool(
            "PDF contains no text; OCRmyPDF/Tesseract preprocessing is required"
        )
    return text


def extract_source(path: Path, original_name: str) -> Tuple[str, str]:
    suffix = Path(original_name).suffix.casefold()
    if suffix == ".docx":
        body = _extract_docx(path)
        return _title_from_text(body, original_name), body
    if suffix == ".pdf":
        body = _extract_pdf(path)
        return _title_from_text(body, original_name), body
    data = path.read_bytes()
    if suffix in {".html", ".htm"}:
        parser = _ReadableHTMLParser()
        parser.feed(_decode_text(data))
        body = parser.text()
        return parser.title[:200] or _title_from_text(body, original_name), body
    if suffix in TEXT_EXTENSIONS or not suffix:
        body = _decode_text(data).replace("\x00", "").strip()
        return _title_from_text(body, original_name), body

    # Accept an unknown extension only when it is convincingly plain text.
    sample = data[:8192]
    if sample and sum(byte == 0 for byte in sample) == 0:
        body = _decode_text(data).replace("\x00", "").strip()
        return _title_from_text(body, original_name), body
    raise ExtractionError(f"unsupported local format: {suffix or '(none)'}")


def _normalized_markdown(
    *,
    document_id: str,
    source_id: str,
    sha256: str,
    title: str,
    original_name: str,
    imported_at: str,
    body: str,
    section_index: int = 0,
    heading_path: Tuple[str, ...] = (),
    source_line_start: Optional[int] = None,
    source_line_end: Optional[int] = None,
    body_sha256: str = "",
    splitter_version: str = "single-v1",
) -> str:
    frontmatter = {
        "id": document_id,
        "source_id": source_id,
        "source_sha256": sha256,
        "title": title,
        "original_name": original_name,
        "imported_at": imported_at,
        "visibility": "private",
        "generated_by": "knowledge-os/extract-v1",
        "section_index": section_index,
        "heading_path": list(heading_path),
        "source_line_start": source_line_start,
        "source_line_end": source_line_end,
        "body_sha256": body_sha256,
        "splitter_version": splitter_version,
    }
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(
            f"{key}: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"
        )
    lines.extend(["---", "", f"# {title}", "", body.strip(), ""])
    return "\n".join(lines)
