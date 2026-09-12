"""Bounded, read-only previews of authenticated local text sources."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from .config import ProjectPaths
from .processing.extraction import TEXT_EXTENSIONS, _decode_text

SOURCE_PATH = '/__knowledge/source'
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_PREVIEW_CHARS = 12000
MAX_PREVIEW_LINES = 120


class SourcePreviewError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def source_preview(root: Path, body: bytes) -> dict:
    try:
        request = json.loads(body)
    except (ValueError, UnicodeError) as exc:
        raise SourcePreviewError(400, '原文请求格式无效') from exc
    if (not isinstance(request, dict) or set(request) != {'document_id'}
            or not isinstance(request['document_id'], str)
            or not 1 <= len(request['document_id']) <= 300):
        raise SourcePreviewError(400, '请指定有效的知识卡')
    paths = ProjectPaths.from_root(root)
    with closing(sqlite3.connect(paths.database_file.as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute('''SELECT s.raw_path, s.original_name, s.sha256,
            d.source_line_start, d.source_line_end FROM documents d
            JOIN sources s ON s.id=d.source_id WHERE d.id=?''', (request['document_id'],)).fetchone()
    if row is None:
        raise SourcePreviewError(404, '这张知识卡已不存在，请刷新页面')
    if Path(row['original_name']).suffix.lower() not in TEXT_EXTENSIONS - {'.html', '.htm'}:
        raise SourcePreviewError(415, '此格式暂不支持原件预览，请在本机原始资料中核对')
    raw = paths.root / row['raw_path']
    try:
        raw.resolve().relative_to(paths.raw_dir)
        if any(item.is_symlink() for item in (raw, *raw.parents)):
            raise ValueError('symlink')
    except (OSError, ValueError, RuntimeError) as exc:
        raise SourcePreviewError(409, '原件引用无效，请检查本地备份') from exc
    try:
        descriptor = os.open(raw, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            import stat
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise SourcePreviewError(409, '原件不是普通文件')
            data = stream.read(MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise SourcePreviewError(404, '原件暂不可用，请检查本地备份') from exc
    if len(data) > MAX_SOURCE_BYTES:
        raise SourcePreviewError(413, '原件超过2 MiB预览上限，请在本机文件中核对')
    if hashlib.sha256(data).hexdigest() != row['sha256']:
        raise SourcePreviewError(409, '原件摘要已改变，未显示可能不匹配的内容')
    # Match the pipeline's text extraction; locators refer to this decoded text.
    lines = _decode_text(data).replace('\x00', '').strip().splitlines()
    start = row['source_line_start'] or 1
    end = row['source_line_end'] or len(lines)
    if not lines or not 1 <= start <= end <= len(lines):
        raise SourcePreviewError(409, '原文行号与原件不一致，请重新核对来源')
    stop = min(end, start + MAX_PREVIEW_LINES - 1)
    text = '\n'.join(lines[start - 1:stop])
    truncated = stop < end or len(text) > MAX_PREVIEW_CHARS
    text = text[:MAX_PREVIEW_CHARS]
    shown_end = start + len(text.splitlines()) - 1
    return {'ok': True, 'document_id': request['document_id'],
            'original_name': row['original_name'], 'text': text,
            'line_start': start, 'line_end': shown_end,
            'truncated': truncated, 'locator_basis': 'decoded-source-text'}
