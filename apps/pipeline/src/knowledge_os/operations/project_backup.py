"""Create and verify a complete, local-only Knowledge OS backup bundle."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from ..config import ProjectPaths
from .restore import RestoreDrillError, run_restore_drill
from .snapshot import create_sqlite_snapshot


PathLike = Union[str, os.PathLike]
MANIFEST_NAME = "knowledge-backup-manifest.json"
DATABASE_MEMBER = "workspace/data/state/knowledge.sqlite3"
MAX_ENTRIES = 50000
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
MAX_PACKAGE_BYTES = 4 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024

_SOURCE_ROOTS: Tuple[Tuple[str, str], ...] = (
    ("config/taxonomy.json", "configuration"),
    ("config/runtime.json", "configuration"),
    ("workspace/inbox/files", "inbox"),
    ("workspace/data/raw", "raw"),
    ("workspace/data/normalized", "normalized"),
    ("workspace/data/quarantine", "quarantine"),
    ("workspace/vault", "vault"),
    ("workspace/site/data", "site-data"),
    ("workspace/site/dist", "site-build"),
)


class ProjectBackupError(RuntimeError):
    """Raised when a complete project backup cannot be proven safe."""


@dataclass(frozen=True)
class ProjectBackupResult:
    source: Path
    package: Path
    sha256: str
    file_count: int
    total_bytes: int
    schema_version: int
    size_bytes: int
    created_at: str


@dataclass(frozen=True)
class ProjectBackupVerification:
    package: Path
    sha256: str
    file_count: int
    total_bytes: int
    schema_version: int
    integrity: str = "ok"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _safe_relative(path: str) -> bool:
    if not path or path.startswith(("/", "\\")) or "\\" in path:
        return False
    return all(part not in {"", ".", ".."} for part in Path(path).parts)


def _zip_symlink(info: zipfile.ZipInfo) -> bool:
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


def _iter_source_files(root: Path) -> Iterable[Tuple[Path, str]]:
    for relative_root, kind in _SOURCE_ROOTS:
        candidate_root = root / relative_root
        if candidate_root.is_symlink():
            raise ProjectBackupError("backup rejects symbolic link: {}".format(relative_root))
        if relative_root.endswith(".json"):
            if not candidate_root.is_file():
                raise ProjectBackupError("backup requires missing file: {}".format(relative_root))
            yield candidate_root, kind
            continue
        if not candidate_root.exists():
            continue
        if not candidate_root.is_dir():
            raise ProjectBackupError("backup source is not a directory: {}".format(relative_root))
        for path in sorted(candidate_root.rglob("*")):
            if path.is_symlink():
                raise ProjectBackupError(
                    "backup rejects symbolic link: {}".format(path.relative_to(root).as_posix())
                )
            if path.is_dir():
                continue
            if not path.is_file():
                raise ProjectBackupError(
                    "backup contains a non-regular file: {}".format(path.relative_to(root).as_posix())
                )
            yield path, kind


def _entry(path: str, source: Path, kind: str) -> Dict[str, Any]:
    size = source.stat().st_size
    if size > MAX_MEMBER_BYTES:
        raise ProjectBackupError("backup member exceeds the 512 MiB limit: {}".format(path))
    return {"path": path, "kind": kind, "size_bytes": size, "sha256": _sha256(source)}


def _write_member(archive: zipfile.ZipFile, source: Path, relative: str) -> None:
    info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100600 << 16
    with source.open("rb") as source_handle, archive.open(info, "w") as target:
        shutil.copyfileobj(source_handle, target, length=1024 * 1024)


def package_project(
    project_root: PathLike,
    output_directory: PathLike,
) -> ProjectBackupResult:
    """Verify a private candidate before publishing the complete backup bundle."""

    root = Path(project_root).expanduser().resolve()
    paths = ProjectPaths.from_root(root)
    output = Path(output_directory).expanduser().resolve()
    try:
        output.relative_to(paths.workspace_dir)
    except ValueError as exc:
        raise ProjectBackupError("backup output must stay inside workspace") from exc
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    source_entries: List[Tuple[Path, str, str]] = []
    seen = set()
    total_bytes = 0
    for source, kind in _iter_source_files(root):
        relative = source.relative_to(root).as_posix()
        if not _safe_relative(relative) or relative in seen or relative == DATABASE_MEMBER:
            raise ProjectBackupError("backup contains an unsafe or duplicate path: {}".format(relative))
        seen.add(relative)
        entry = _entry(relative, source, kind)
        total_bytes += int(entry["size_bytes"])
        if total_bytes > MAX_TOTAL_BYTES:
            raise ProjectBackupError("backup contents exceed the 4 GiB limit")
        source_entries.append((source, relative, kind))

    with tempfile.TemporaryDirectory(prefix="knowledge-backup-") as temporary:
        snapshot = create_sqlite_snapshot(paths.database_file, Path(temporary), prefix="bundle")
        try:
            restore = run_restore_drill(snapshot.snapshot, expected_sha256=snapshot.sha256)
        except RestoreDrillError as exc:
            raise ProjectBackupError("database snapshot verification failed") from exc
        database_entry = {
            "path": DATABASE_MEMBER,
            "kind": "database",
            "size_bytes": snapshot.size_bytes,
            "sha256": snapshot.sha256,
        }
        total_bytes += snapshot.size_bytes
        if total_bytes > MAX_TOTAL_BYTES:
            raise ProjectBackupError("backup contents exceed the 4 GiB limit")
        entries = [database_entry]
        entries.extend(_entry(relative, source, kind) for source, relative, kind in source_entries)
        if len(entries) > MAX_ENTRIES:
            raise ProjectBackupError("backup contains too many files")
        manifest = {
            "schema_version": 1,
            "bundle_type": "knowledge-project-backup",
            "created_at": created_at,
            "database_schema_version": restore.schema_version,
            "database_counts": restore.counts,
            "database_snapshot": DATABASE_MEMBER,
            "file_count": len(entries),
            "total_bytes": total_bytes,
            "files": entries,
        }
        manifest_bytes = (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".knowledge-backup-", suffix=".zip.tmp", dir=str(output)
        )
        os.close(descriptor)
        temporary_zip = Path(temporary_name)
        try:
            with zipfile.ZipFile(
                temporary_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
            ) as archive:
                _write_member(archive, snapshot.snapshot, DATABASE_MEMBER)
                for source, relative, _kind in source_entries:
                    _write_member(archive, source, relative)
                manifest_info = zipfile.ZipInfo(
                    MANIFEST_NAME, date_time=(1980, 1, 1, 0, 0, 0)
                )
                manifest_info.compress_type = zipfile.ZIP_DEFLATED
                manifest_info.external_attr = 0o100600 << 16
                archive.writestr(manifest_info, manifest_bytes)
            digest = _sha256(temporary_zip)
            # Verify the bytes actually written, including the SQLite snapshot,
            # before the candidate can appear as a usable final backup.
            verify_project_backup(temporary_zip, expected_sha256=digest)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            final_path = output / "knowledge-backup-{}-{}.zip".format(stamp, digest[:12])
            if final_path.exists():
                if _sha256(final_path) != digest:
                    raise ProjectBackupError("backup destination already exists with different content")
                temporary_zip.unlink()
            else:
                os.replace(str(temporary_zip), str(final_path))
            try:
                os.chmod(final_path, 0o600)
            except OSError:
                pass
            return ProjectBackupResult(
                source=root,
                package=final_path,
                sha256=digest,
                file_count=len(entries),
                total_bytes=total_bytes,
                schema_version=restore.schema_version,
                size_bytes=final_path.stat().st_size,
                created_at=created_at,
            )
        except (OSError, sqlite3.Error, ValueError, zipfile.BadZipFile) as exc:
            raise ProjectBackupError("backup package creation failed: {}".format(exc)) from exc
        finally:
            if temporary_zip.exists():
                temporary_zip.unlink()


def verify_project_backup(
    package_path: PathLike, *, expected_sha256: Optional[str] = None
) -> ProjectBackupVerification:
    """Verify every member and the SQLite snapshot without restoring live state."""

    unresolved = Path(package_path).expanduser()
    if unresolved.is_symlink():
        raise ProjectBackupError("backup verification rejects a symbolic link")
    package = unresolved.resolve()
    if not package.is_file():
        raise ProjectBackupError("backup package is not a regular file")
    if package.stat().st_size > MAX_PACKAGE_BYTES:
        raise ProjectBackupError("backup package exceeds the 4 GiB limit")
    digest = _sha256(package)
    if expected_sha256 is not None and digest.casefold() != expected_sha256.casefold():
        raise ProjectBackupError("backup package SHA-256 does not match the expected value")
    try:
        with zipfile.ZipFile(package) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES + 1:
                raise ProjectBackupError("backup contains too many files")
            by_name: Dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                if not _safe_relative(info.filename) or _zip_symlink(info):
                    raise ProjectBackupError("backup contains an unsafe archive path")
                if info.filename in by_name:
                    raise ProjectBackupError("backup contains a duplicate archive path")
                by_name[info.filename] = info
            manifest_info = by_name.get(MANIFEST_NAME)
            if manifest_info is None or manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise ProjectBackupError("backup manifest is missing or too large")
            try:
                manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
                raise ProjectBackupError("backup manifest is invalid") from exc
            if (
                not isinstance(manifest, dict)
                or manifest.get("schema_version") != 1
                or manifest.get("bundle_type") != "knowledge-project-backup"
            ):
                raise ProjectBackupError("backup manifest schema is unsupported")
            file_items = manifest.get("files")
            if not isinstance(file_items, list) or len(file_items) > MAX_ENTRIES:
                raise ProjectBackupError("backup manifest file list is invalid")
            expected: Dict[str, Tuple[int, str]] = {}
            for item in file_items:
                if not isinstance(item, dict):
                    raise ProjectBackupError("backup manifest entry is invalid")
                name, size, item_digest = item.get("path"), item.get("size_bytes"), item.get("sha256")
                if (
                    not isinstance(name, str)
                    or not _safe_relative(name)
                    or name == MANIFEST_NAME
                    or name in expected
                    or not isinstance(size, int)
                    or size < 0
                    or size > MAX_MEMBER_BYTES
                    or not isinstance(item_digest, str)
                    or len(item_digest) != 64
                ):
                    raise ProjectBackupError("backup manifest entry is invalid")
                if (
                    name.startswith("workspace/data/state/")
                    and name != DATABASE_MEMBER
                ) or name.startswith("workspace/data/logs/"):
                    raise ProjectBackupError("backup contains transient runtime state")
                expected[name] = (size, item_digest.casefold())
            for required in ("config/taxonomy.json", "config/runtime.json", DATABASE_MEMBER):
                if required not in expected:
                    raise ProjectBackupError("backup manifest is missing {}".format(required))
            actual = set(by_name) - {MANIFEST_NAME}
            if actual != set(expected):
                raise ProjectBackupError("backup manifest does not match archive files")
            total_bytes = 0
            with tempfile.TemporaryDirectory(prefix="knowledge-backup-verify-") as temporary:
                database_path = Path(temporary) / "knowledge.sqlite3"
                with database_path.open("wb") as destination:
                    for name, (expected_size, expected_digest) in expected.items():
                        info = by_name[name]
                        if info.file_size > MAX_MEMBER_BYTES:
                            raise ProjectBackupError("backup member exceeds the 512 MiB limit")
                        member_digest = hashlib.sha256()
                        member_size = 0
                        with archive.open(info, "r") as source:
                            while True:
                                block = source.read(1024 * 1024)
                                if not block:
                                    break
                                member_size += len(block)
                                if member_size > MAX_MEMBER_BYTES:
                                    raise ProjectBackupError("backup member exceeds the 512 MiB limit")
                                member_digest.update(block)
                                if name == DATABASE_MEMBER:
                                    destination.write(block)
                        if member_size != expected_size or member_digest.hexdigest() != expected_digest:
                            raise ProjectBackupError("backup file digest does not match manifest")
                        total_bytes += member_size
                        if total_bytes > MAX_TOTAL_BYTES:
                            raise ProjectBackupError("backup contents exceed the 4 GiB limit")
                try:
                    restored = run_restore_drill(
                        database_path,
                        expected_sha256=expected[DATABASE_MEMBER][1],
                    )
                except RestoreDrillError as exc:
                    raise ProjectBackupError("backup SQLite snapshot verification failed") from exc
            if manifest.get("file_count") != len(expected) or manifest.get("total_bytes") != total_bytes:
                raise ProjectBackupError("backup manifest totals do not match archive")
            if manifest.get("database_schema_version") != restored.schema_version:
                raise ProjectBackupError("backup schema version does not match SQLite snapshot")
            if manifest.get("database_counts") != restored.counts:
                raise ProjectBackupError("backup table counts do not match SQLite snapshot")
    except ProjectBackupError:
        raise
    except (OSError, sqlite3.Error, ValueError, zipfile.BadZipFile) as exc:
        raise ProjectBackupError("backup verification failed: {}".format(type(exc).__name__)) from exc
    return ProjectBackupVerification(
        package=package,
        sha256=digest,
        file_count=len(expected),
        total_bytes=total_bytes,
        schema_version=restored.schema_version,
    )


__all__ = [
    "DATABASE_MEMBER",
    "MANIFEST_NAME",
    "ProjectBackupError",
    "ProjectBackupResult",
    "ProjectBackupVerification",
    "package_project",
    "verify_project_backup",
]
