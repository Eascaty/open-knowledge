"""Static-site build contracts and constants."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Union

SCHEMA_VERSION = 1
VALID_VISIBILITIES = {"private", "public"}
ASSET_DIR = Path(__file__).resolve().parents[5] / "web" / "src"
REQUIRED_ASSETS = (
    "index.html",
    "data-source.js",
    "related-documents.js",
    "app.js",
    "styles.css",
    "local-ingest.js",
    "local-ingest.css",
    "local-classification.js",
    "local-classification.css",
    "local-review.js",
    "local-review.css",
    "local-review-queue.js",
    "local-review-queue.css",
)


class SiteDataError(ValueError):
    """Raised when canonical site data violates the integration contract."""


@dataclass(frozen=True)
class BuildResult:
    """A compact description of a successful site build."""

    output_dir: Path
    visibility: str
    allow_indexing: bool
    node_count: int
    document_count: int
    relation_count: int
    content_digest: str
