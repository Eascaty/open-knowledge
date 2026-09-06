"""Focused health and release checks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union
from urllib.parse import unquote, urlsplit

from .model import CheckResult, CheckStatus, _duration_ms, _label, _path_within
from .privacy import _iter_public_files

def check_json_document(
    path: PathLike,
    name: str = "canonical-data",
    max_bytes: int = 5 * 1024**2,
) -> CheckResult:
    """Validate that a canonical build input is present and parseable JSON."""

    started = time.monotonic()
    json_path = Path(path).expanduser().resolve()
    if not json_path.exists():
        return CheckResult(
            name,
            CheckStatus.FAIL,
            "规范化构建数据不存在",
            (json_path.name,),
            duration_ms=_duration_ms(started),
        )
    if not json_path.is_file():
        return CheckResult(
            name,
            CheckStatus.FAIL,
            "规范化构建数据不是普通文件",
            (json_path.name,),
            duration_ms=_duration_ms(started),
        )
    size = json_path.stat().st_size
    if size > max_bytes:
        return CheckResult(
            name,
            CheckStatus.FAIL,
            "规范化构建数据超过单包上限",
            ("{} bytes > {} bytes".format(size, max_bytes),),
            metrics={"bytes": size, "max_bytes": max_bytes},
            duration_ms=_duration_ms(started),
        )
    try:
        value = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return CheckResult(
            name,
            CheckStatus.FAIL,
            "规范化构建数据不是有效 JSON",
            ("{}: {}".format(type(exc).__name__, str(exc)[:160]),),
            metrics={"bytes": size},
            duration_ms=_duration_ms(started),
        )
    if not isinstance(value, (dict, list)):
        return CheckResult(
            name,
            CheckStatus.FAIL,
            "规范化构建数据顶层必须是对象或数组",
            metrics={"bytes": size, "top_level_type": type(value).__name__},
            duration_ms=_duration_ms(started),
        )
    item_count = len(value)
    return CheckResult(
        name,
        CheckStatus.PASS,
        "规范化构建数据可读取",
        metrics={
            "bytes": size,
            "top_level_type": type(value).__name__,
            "top_level_items": item_count,
        },
        duration_ms=_duration_ms(started),
    )


_SITE_BUNDLE_FILES = {
    "_headers",
    "assets/app.js",
    "assets/data-source.js",
    "assets/related-documents.js",
    "assets/local-ingest.css",
    "assets/local-ingest.js",
    "assets/local-classification.css",
    "assets/local-classification.js",
    "assets/local-review.css",
    "assets/local-review.js",
    "assets/local-review-queue.css",
    "assets/local-review-queue.js",
    "assets/styles.css",
    "assets/markdown-reader.js",
    "assets/markdown-reader.css",
    "assets/reading-history.js",
    "assets/reading-actions.js",
    "build-meta.json",
    "data/graph.json",
    "data/search-index.json",
    "data/site-data.json",
    "data/taxonomy.json",
    "icons/icon-192.png",
    "icons/icon-512.png",
    "index.html",
    "manifest.webmanifest",
    "offline.html",
    "robots.txt",
    "service-worker.js",
}


def check_site_bundle(
    root: PathLike,
    *,
    expected_visibility: str,
) -> CheckResult:
    """Verify that a candidate is one complete, unmodified site build."""

    started = time.monotonic()
    bundle = Path(root).expanduser().resolve()
    if expected_visibility not in {"private", "public"}:
        return CheckResult(
            "site-bundle",
            CheckStatus.FAIL,
            "期望可见性必须是 private 或 public",
            duration_ms=_duration_ms(started),
        )
    if not bundle.is_dir():
        return CheckResult(
            "site-bundle",
            CheckStatus.FAIL,
            "候选站点目录不存在",
            (bundle.name,),
            duration_ms=_duration_ms(started),
        )
    issues: List[str] = []
    actual_files = {
        path.relative_to(bundle).as_posix()
        for path, is_symlink in _iter_public_files(bundle)
        if path.is_file() and not is_symlink
    }
    missing = sorted(_SITE_BUNDLE_FILES - actual_files)
    unexpected = sorted(actual_files - _SITE_BUNDLE_FILES)
    if missing:
        issues.append("缺少构建文件: {}".format(", ".join(missing)))
    if unexpected:
        issues.append("存在非构建器文件: {}".format(", ".join(unexpected[:20])))

    meta_path = bundle / "build-meta.json"
    data_path = bundle / "data" / "site-data.json"
    metadata: Any = {}
    site_data: Any = {}
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        site_data = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        issues.append("无法读取构建清单: {}: {}".format(type(exc).__name__, str(exc)[:120]))

    if isinstance(metadata, dict) and isinstance(site_data, dict):
        if metadata.get("visibility") != expected_visibility:
            issues.append(
                "build-meta visibility={}，期望 {}".format(
                    metadata.get("visibility"), expected_visibility
                )
            )
        site_visibility = site_data.get("site", {}).get("visibility")
        if site_visibility != expected_visibility:
            issues.append(
                "site-data visibility={}，期望 {}".format(
                    site_visibility, expected_visibility
                )
            )
        documents = site_data.get("documents", [])
        if not isinstance(documents, list):
            issues.append("site-data documents 必须是数组")
            documents = []
        invalid_documents = [
            str(item.get("id", "<unknown>"))
            for item in documents
            if not isinstance(item, dict)
            or item.get("visibility") != expected_visibility
        ]
        if invalid_documents:
            issues.append(
                "文档可见性与候选包不一致: {}".format(
                    ", ".join(invalid_documents[:20])
                )
            )
        canonical_bytes = (
            json.dumps(site_data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        actual_digest = hashlib.sha256(canonical_bytes).hexdigest()
        if metadata.get("content_digest") != actual_digest:
            issues.append("site-data 内容摘要与 build-meta 不一致")
        expected_counts = {
            "document_count": len(documents),
            "node_count": len(site_data.get("nodes", []))
            if isinstance(site_data.get("nodes", []), list)
            else -1,
            "relation_count": len(site_data.get("relations", []))
            if isinstance(site_data.get("relations", []), list)
            else -1,
        }
        for field, count in expected_counts.items():
            if metadata.get(field) != count:
                issues.append(
                    "{}={}，实际 {}".format(field, metadata.get(field), count)
                )
    elif not issues:
        issues.append("构建清单顶层必须是对象")

    metrics = {
        "expected_visibility": expected_visibility,
        "files": len(actual_files),
        "missing_files": len(missing),
        "unexpected_files": len(unexpected),
        "issues": len(issues),
    }
    return CheckResult(
        "site-bundle",
        CheckStatus.FAIL if issues else CheckStatus.PASS,
        "候选站点构建清单无效" if issues else "候选站点构建清单与内容一致",
        tuple(issues),
        metrics,
        _duration_ms(started),
    )
