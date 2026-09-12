"""Atomically assemble and validate a complete static-site bundle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Union

from knowledge_os.contracts import validate_canonical_contract

from .model import (
    ASSET_DIR,
    REQUIRED_ASSETS,
    SCHEMA_VERSION,
    BuildResult,
    SiteDataError,
)
from .normalize import DataInput, normalize_site_data
from .payloads import _graph_payload, _search_payload, _taxonomy_payload
from .render import (
    _headers,
    _json_bytes,
    _make_icon,
    _manifest,
    _render_index,
    _service_worker,
    _write_json,
)


_CACHE_POLICY_VERSION = b"knowledge-os-shell-v2\0"


def _cache_version(canonical_bytes: bytes) -> str:
    """Fingerprint knowledge plus every checked-in browser shell source."""

    digest = hashlib.sha256(_CACHE_POLICY_VERSION)
    digest.update(canonical_bytes)
    for name in (*REQUIRED_ASSETS, "offline.html"):
        payload = (ASSET_DIR / name).read_bytes()
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()[:16]


def _validate_build(directory: Path) -> None:
    required = [
        "index.html",
        "offline.html",
        "assets/data-source.js",
        "assets/search-utils.js",
        "assets/related-documents.js",
        "assets/app.js",
        "assets/styles.css",
        "assets/markdown-reader.js",
        "assets/markdown-reader.css",
        "assets/reading-history.js",
        "assets/reading-actions.js",
        "assets/first-use.js",
        "assets/local-ingest.js",
        "assets/local-ingest.css",
        "assets/local-classification.js",
        "assets/local-classification.css",
        "assets/local-review.js",
        "assets/local-review.css",
        "assets/local-review-queue.js",
        "assets/local-review-queue.css",
        "manifest.webmanifest",
        "service-worker.js",
        "robots.txt",
        "_headers",
        "data/site-data.json",
        "data/taxonomy.json",
        "data/search-index.json",
        "data/graph.json",
        "icons/icon-192.png",
        "icons/icon-512.png",
    ]
    missing = [name for name in required if not (directory / name).is_file()]
    if missing:
        raise SiteDataError(f"网站构建缺少文件：{', '.join(missing)}")
    index = (directory / "index.html").read_text(encoding="utf-8")
    if re.search(r"\{\{[A-Z_]+\}\}", index):
        raise SiteDataError("index.html 中仍有未替换的模板变量")
    for relative in (
        "data/site-data.json",
        "data/taxonomy.json",
        "data/search-index.json",
        "data/graph.json",
        "manifest.webmanifest",
    ):
        json.loads((directory / relative).read_text(encoding="utf-8"))
    for size in (192, 512):
        signature = (directory / f"icons/icon-{size}.png").read_bytes()[:8]
        if signature != b"\x89PNG\r\n\x1a\n":
            raise SiteDataError(f"icon-{size}.png 不是有效 PNG")


def _safe_output_directory(output_dir: Union[str, os.PathLike]) -> Path:
    requested = Path(output_dir).expanduser()
    if requested.is_symlink():
        raise ValueError("网站输出目录不能是符号链接")
    # Resolve existing parent aliases (macOS /var -> /private/var included)
    # before any replace/delete operation, so the real target is unambiguous.
    output = requested.resolve(strict=False)
    if output in {Path("/"), Path.home(), Path(Path.cwd().anchor)}:
        raise ValueError("拒绝把宽泛目录作为网站输出目录")
    return output


def build_site(
    data_or_path: DataInput,
    output_dir: Union[str, os.PathLike] = "workspace/site/dist",
    *,
    visibility: str = "private",
    allow_indexing: bool = False,
) -> BuildResult:
    """Build and atomically replace a complete static site.

    ``allow_indexing`` is deliberately opt-in and is rejected for private
    builds.  Private PWA builds cache only the application shell, never
    knowledge JSON.
    """

    if visibility == "private" and allow_indexing:
        raise ValueError("private 构建不能允许搜索引擎索引")
    data = normalize_site_data(data_or_path, visibility=visibility)
    validate_canonical_contract(data)
    output = _safe_output_directory(output_dir)
    output.parent.mkdir(parents=True, exist_ok=True)

    for asset in REQUIRED_ASSETS:
        if not (ASSET_DIR / asset).is_file():
            raise SiteDataError(f"缺少网站源资源：{ASSET_DIR / asset}")

    canonical_bytes = _json_bytes(data)
    content_digest = hashlib.sha256(canonical_bytes).hexdigest()
    cache_version = _cache_version(canonical_bytes)
    temp = Path(
        tempfile.mkdtemp(prefix=f".{output.name}-build-", dir=output.parent)
    )
    backup = output.with_name(
        f".{output.name}-previous-{uuid.uuid4().hex}"
    )
    try:
        (temp / "assets").mkdir(parents=True)
        (temp / "icons").mkdir(parents=True)
        (temp / "data").mkdir(parents=True)
        template = (ASSET_DIR / "index.html").read_text(encoding="utf-8")
        noindex = visibility == "private" or not allow_indexing
        (temp / "index.html").write_text(
            _render_index(template, data, noindex), encoding="utf-8"
        )
        shutil.copyfile(
            ASSET_DIR / "data-source.js", temp / "assets" / "data-source.js"
        )
        shutil.copyfile(
            ASSET_DIR / "search-utils.js", temp / "assets" / "search-utils.js"
        )
        shutil.copyfile(
            ASSET_DIR / "related-documents.js",
            temp / "assets" / "related-documents.js",
        )
        shutil.copyfile(ASSET_DIR / "app.js", temp / "assets" / "app.js")
        shutil.copyfile(ASSET_DIR / "styles.css", temp / "assets" / "styles.css")
        for name in ("markdown-reader.js", "markdown-reader.css"):
            shutil.copyfile(ASSET_DIR / name, temp / "assets" / name)
        for name in ("reading-history.js", "reading-actions.js", "first-use.js"):
            shutil.copyfile(ASSET_DIR / name, temp / "assets" / name)
        shutil.copyfile(
            ASSET_DIR / "local-ingest.js", temp / "assets" / "local-ingest.js"
        )
        shutil.copyfile(
            ASSET_DIR / "local-ingest.css", temp / "assets" / "local-ingest.css"
        )
        shutil.copyfile(
            ASSET_DIR / "local-classification.js",
            temp / "assets" / "local-classification.js",
        )
        shutil.copyfile(
            ASSET_DIR / "local-classification.css",
            temp / "assets" / "local-classification.css",
        )
        shutil.copyfile(
            ASSET_DIR / "local-review.js",
            temp / "assets" / "local-review.js",
        )
        shutil.copyfile(
            ASSET_DIR / "local-review.css",
            temp / "assets" / "local-review.css",
        )
        shutil.copyfile(
            ASSET_DIR / "local-review-queue.js",
            temp / "assets" / "local-review-queue.js",
        )
        shutil.copyfile(
            ASSET_DIR / "local-review-queue.css",
            temp / "assets" / "local-review-queue.css",
        )
        (temp / "offline.html").write_text(
            _render_index(
                (ASSET_DIR / "offline.html").read_text(encoding="utf-8"),
                data,
                noindex,
            ),
            encoding="utf-8",
        )
        (temp / "service-worker.js").write_text(
            _service_worker(cache_version, visibility == "private"),
            encoding="utf-8",
        )
        _write_json(temp / "manifest.webmanifest", _manifest(data))
        _write_json(temp / "data" / "site-data.json", data)
        _write_json(temp / "data" / "taxonomy.json", _taxonomy_payload(data))
        _write_json(temp / "data" / "search-index.json", _search_payload(data))
        _write_json(temp / "data" / "graph.json", _graph_payload(data))
        (temp / "icons" / "icon-192.png").write_bytes(_make_icon(192))
        (temp / "icons" / "icon-512.png").write_bytes(_make_icon(512))
        (temp / "_headers").write_text(
            _headers(visibility == "private", noindex), encoding="utf-8"
        )
        (temp / "robots.txt").write_text(
            (
                "User-agent: *\nDisallow: /\n"
                if noindex
                else "User-agent: *\nAllow: /\n"
            ),
            encoding="utf-8",
        )
        _write_json(
            temp / "build-meta.json",
            {
                "schema_version": SCHEMA_VERSION,
                "content_digest": content_digest,
                "cache_version": cache_version,
                "generated_at": data["generated_at"],
                "visibility": visibility,
                "allow_indexing": allow_indexing,
                "node_count": len(data["nodes"]),
                "document_count": len(data["documents"]),
                "relation_count": len(data["relations"]),
                "private_data_cached_by_service_worker": False
                if visibility == "private"
                else None,
            },
        )
        _validate_build(temp)

        if backup.exists():
            raise ValueError(f"随机备份路径意外存在：{backup}")
        if output.exists():
            if not output.is_dir():
                raise ValueError(f"输出路径已存在且不是目录：{output}")
            os.replace(output, backup)
        try:
            os.replace(temp, output)
        except BaseException:
            if backup.exists() and not output.exists():
                os.replace(backup, output)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if temp.exists():
            shutil.rmtree(temp)

    return BuildResult(
        output_dir=output,
        visibility=visibility,
        allow_indexing=allow_indexing,
        node_count=len(data["nodes"]),
        document_count=len(data["documents"]),
        relation_count=len(data["relations"]),
        content_digest=content_digest,
    )
