"""Restore a verified bundle into a new private data root, without ingestion."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional

from ..config import ProjectPaths, atomic_write_json, initialize_layout, load_runtime, load_taxonomy
from ..knowledge import build_site_data
from ..site import build_site
from .gate import run_prebuild_gate
from .health import run_health_checks
from .project_backup import (
    DATABASE_MEMBER, MANIFEST_NAME, MAX_PACKAGE_BYTES, ProjectBackupError,
    _SOURCE_ROOTS, _sha256, verify_project_backup,
)


@dataclass(frozen=True)
class ProjectRestoreResult:
    destination: Path
    sha256: str
    sources: int
    documents: int
    schema_version: int


def _allowed_member(name: str) -> bool:
    # Reject aliases before touching the filesystem, including Windows paths.
    if (not name or "\\" in name or ":" in name or "\x00" in name
            or str(PurePosixPath(name)) != name
            or any(part in {".", ".."} for part in name.split("/"))):
        return False
    return name == DATABASE_MEMBER or any(
        name == prefix if prefix.endswith(".json") else name.startswith(prefix + "/")
        for prefix, _kind in _SOURCE_ROOTS
    )


def _extract(archive_path: Path, root: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read(MANIFEST_NAME))
        for item in manifest["files"]:
            name = item["path"]
            info = archive.getinfo(name)
            kind = (info.external_attr >> 16) & 0o170000
            if not _allowed_member(name) or info.is_dir() or kind not in {0, 0o100000}:
                raise ProjectBackupError("restore rejects unsupported archive member")
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            digest = hashlib.sha256()
            size = 0
            # Exclusive creation also rejects case-folding/normalization collisions.
            with archive.open(info) as source, target.open("xb") as destination:
                os.chmod(target, 0o600)
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > item["size_bytes"]:
                        raise ProjectBackupError("restore member exceeds declared size")
                    digest.update(block)
                    destination.write(block)
            if size != item["size_bytes"] or digest.hexdigest() != item["sha256"].lower():
                raise ProjectBackupError("restore member digest mismatch")


def _reference(root: Path, value: str, prefix: str) -> Path:
    if not _allowed_member(value) or not value.startswith(prefix + "/"):
        raise ProjectBackupError("database contains an unsafe file reference")
    path = root / value
    if not path.is_file():
        raise ProjectBackupError("database references a missing backup file")
    return path


def _rebuild(root: Path) -> tuple[int, int]:
    paths = ProjectPaths.from_root(root)
    initialize_layout(paths)
    before = _sha256(paths.database_file)
    with closing(sqlite3.connect(paths.database_file.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        sources = connection.execute("SELECT raw_path, sha256 FROM sources").fetchall()
        for source in sources:
            raw = _reference(root, source["raw_path"], "workspace/data/raw")
            if _sha256(raw) != source["sha256"]:
                raise ProjectBackupError("raw source digest differs from database")
        documents = connection.execute("SELECT id, normalized_path FROM documents").fetchall()
        for document in documents:
            _reference(root, document["normalized_path"], "workspace/data/normalized")
        indexed = [row[0] for row in connection.execute("SELECT document_id FROM documents_fts")]
        if len(indexed) != len(documents) or set(indexed) != {row["id"] for row in documents}:
            raise ProjectBackupError("search index does not match knowledge cards")
        canonical = build_site_data(connection, paths, load_taxonomy(paths), load_runtime(paths), visibility="private")
        if {item["id"] for item in canonical["documents"]} != {row["id"] for row in documents}:
            raise ProjectBackupError("rebuilt site omits knowledge cards")
    # Discard old executable website assets, then build only from current code.
    shutil.rmtree(paths.site_dir / "dist", ignore_errors=True)
    build_site(canonical, paths.site_dir / "dist", visibility="private")
    # A copied WAL-mode database has no sidecars. Let SQLite initialize its
    # candidate-only runtime state before the ordinary read-only health checks.
    with closing(sqlite3.connect(paths.database_file)) as connection:
        connection.execute("SELECT COUNT(*) FROM documents").fetchone()
    gate = run_prebuild_gate(root)
    health = run_health_checks(root)
    if not gate.allowed or not health.passed:
        raise ProjectBackupError("restored candidate failed site gate or health checks: {}".format(
            "; ".join(check.name + ": " + check.summary + " " + str(check.details) for check in (*gate.checks, *health.checks) if check.status.value == "FAIL")
        ))
    if _sha256(paths.database_file) != before:
        raise ProjectBackupError("restore unexpectedly modified the database")
    return len(sources), len(documents)


def restore_project_backup(
    package_path: Path, destination: Path, *, expected_sha256: Optional[str] = None
) -> ProjectRestoreResult:
    """Verify, extract and rebuild privately; never overwrite an existing root."""
    requested = destination.expanduser()
    if requested.exists() or requested.is_symlink():
        raise ProjectBackupError("restore destination must not exist")
    parent = requested.parent.resolve(strict=True)
    target = parent / requested.name
    package = package_path.expanduser()
    if package.is_symlink() or not package.is_file():
        raise ProjectBackupError("restore requires a regular backup file")
    reserved = False
    completed = False
    try:
        with tempfile.TemporaryDirectory(prefix=".knowledge-restore-", dir=parent) as temporary:
            staging = Path(temporary)
            frozen = staging / "backup.zip"
            # Verify the exact private copy later extracted, avoiding reopen races.
            size = 0
            with package.open("rb") as source, frozen.open("xb") as output:
                os.chmod(frozen, 0o600)
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > MAX_PACKAGE_BYTES:
                        raise ProjectBackupError("backup package exceeds the 4 GiB limit")
                    output.write(block)
            verified = verify_project_backup(frozen, expected_sha256=expected_sha256)
            if verified.schema_version != 2:
                raise ProjectBackupError("full restore requires schema v2; migrate explicitly first")
            candidate = staging / "candidate"
            candidate.mkdir(mode=0o700)
            _extract(frozen, candidate)
            sources, documents = _rebuild(candidate)
            for path in candidate.rglob("*"):
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
            # Reserve exclusively after validation: a concurrent creator wins safely.
            target.mkdir(mode=0o700)
            reserved = True
            for child in candidate.iterdir():
                os.rename(child, target / child.name)
            atomic_write_json(target / "restore-result.json", {
                "schema_version": 1, "status": "verified",
                "backup_sha256": verified.sha256, "sources": sources,
                "documents": documents, "database_schema_version": verified.schema_version,
                "site_visibility": "private", "generator": "knowledge-os/restore-v1",
            })
            completed = True
            return ProjectRestoreResult(target, verified.sha256, sources, documents, verified.schema_version)
    except ProjectBackupError:
        raise
    except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as exc:
        raise ProjectBackupError("project restore failed: {}".format(type(exc).__name__)) from exc
    finally:
        if reserved and not completed:
            shutil.rmtree(target)
