"""Create a local, verifiable ZIP package of a built knowledge site."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


PathLike = Union[str, os.PathLike]
MANIFEST_NAME = "knowledge-site-package.json"
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 10000
MAX_MEMBER_BYTES = 128 * 1024 * 1024


class SitePackageError(RuntimeError):
    """Raised when a site package cannot be created safely."""


@dataclass(frozen=True)
class SitePackageResult:
    source: Path
    package: Path
    visibility: str
    sha256: str
    file_count: int
    size_bytes: int
    created_at: str


@dataclass(frozen=True)
class SitePackageVerification:
    package: Path
    visibility: str
    sha256: str
    file_count: int
    total_bytes: int
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


def _path_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _safe_files(source: Path) -> List[Path]:
    if source.is_symlink():
        raise SitePackageError("site package rejects a symbolic-link source")
    if not source.is_dir():
        raise SitePackageError("site package source is not a directory")
    files: List[Path] = []
    for candidate in sorted(source.rglob("*")):
        if candidate.is_symlink():
            raise SitePackageError(
                "site package rejects symbolic link: {}".format(
                    candidate.relative_to(source).as_posix()
                )
            )
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise SitePackageError(
                "site package contains a non-regular file: {}".format(
                    candidate.relative_to(source).as_posix()
                )
            )
        files.append(candidate)
    if not files:
        raise SitePackageError("site package source is empty")
    return files


def _metadata(source: Path, visibility: str) -> Dict[str, Any]:
    if visibility not in {"private", "public"}:
        raise SitePackageError("visibility must be private or public")
    path = source / "build-meta.json"
    if not path.is_file():
        return {"visibility": visibility, "generated_at": None}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SitePackageError("build-meta.json is not valid JSON") from exc
    if not isinstance(value, dict):
        raise SitePackageError("build-meta.json must contain an object")
    actual = value.get("visibility")
    if actual != visibility:
        raise SitePackageError(
            "site visibility is {}, expected {}".format(actual, visibility)
        )
    return {
        "visibility": visibility,
        "content_digest": value.get("content_digest"),
        "generated_at": value.get("generated_at"),
    }


def package_site(
    source_directory: PathLike,
    output_directory: PathLike,
    *,
    visibility: str,
) -> SitePackageResult:
    """Create a private-by-default ZIP with a machine-readable integrity manifest.

    The package contains only the already-built site. It never includes the
    SQLite database, raw inbox, Vault, logs or runtime configuration.
    """

    source = Path(source_directory).expanduser().resolve()
    output = Path(output_directory).expanduser().resolve()
    files = _safe_files(source)
    metadata = _metadata(source, visibility)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    created_at = metadata.get("generated_at") or datetime.now(timezone.utc).replace(
        microsecond=0
    ).isoformat()
    entries = []
    for path in files:
        relative = path.relative_to(source).as_posix()
        if not relative or relative.startswith("/") or ".." in Path(relative).parts:
            raise SitePackageError("site package contains an unsafe path")
        entries.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    manifest: Dict[str, Any] = {
        "schema_version": 1,
        "created_at": created_at,
        "visibility": visibility,
        "source_kind": "built-static-site",
        "file_count": len(entries),
        "total_bytes": sum(int(item["size_bytes"]) for item in entries),
        "content_digest": metadata.get("content_digest"),
        "files": entries,
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".site-package-", suffix=".zip.tmp", dir=str(output)
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for path in files:
                relative = path.relative_to(source).as_posix()
                info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100600 << 16
                with path.open("rb") as source_handle, archive.open(info, "w") as target:
                    shutil.copyfileobj(source_handle, target, length=1024 * 1024)
            manifest_info = zipfile.ZipInfo(
                MANIFEST_NAME, date_time=(1980, 1, 1, 0, 0, 0)
            )
            manifest_info.compress_type = zipfile.ZIP_DEFLATED
            manifest_info.external_attr = 0o100600 << 16
            archive.writestr(manifest_info, manifest_bytes)
        digest_value = _sha256(temporary)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        final_path = output / "knowledge-site-{}-{}-{}.zip".format(
            visibility, stamp, digest_value[:12]
        )
        if final_path.exists():
            if _sha256(final_path) != digest_value:
                raise SitePackageError(
                    "site package destination already exists with different content"
                )
            temporary.unlink()
        else:
            os.replace(str(temporary), str(final_path))
        try:
            os.chmod(final_path, 0o600)
        except OSError:
            pass
        return SitePackageResult(
            source=source,
            package=final_path,
            visibility=visibility,
            sha256=digest_value,
            file_count=len(entries),
            size_bytes=final_path.stat().st_size,
            created_at=created_at,
        )
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        raise SitePackageError("site package creation failed: {}".format(exc)) from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def _safe_zip_name(name: str) -> bool:
    if not name or name.startswith(("/", "\\")) or "\\" in name:
        return False
    parts = Path(name).parts
    return all(part not in {"", ".", ".."} for part in parts)


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def verify_site_package(
    package_path: PathLike, *, expected_sha256: Optional[str] = None
) -> SitePackageVerification:
    """Verify a package without extracting or modifying any file."""

    unresolved = Path(package_path).expanduser()
    if unresolved.is_symlink():
        raise SitePackageError("site package verification rejects a symbolic link")
    package = unresolved.resolve()
    if not package.is_file():
        raise SitePackageError("site package is not a regular file")
    if package.stat().st_size > MAX_PACKAGE_BYTES:
        raise SitePackageError("site package exceeds the 256 MiB limit")
    digest = _sha256(package)
    if expected_sha256 is not None and digest.casefold() != expected_sha256.casefold():
        raise SitePackageError("site package SHA-256 does not match the expected value")

    try:
        with zipfile.ZipFile(package) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES + 1:
                raise SitePackageError("site package contains too many files")
            by_name: Dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                if not _safe_zip_name(info.filename) or _is_zip_symlink(info):
                    raise SitePackageError("site package contains an unsafe archive path")
                if info.filename in by_name:
                    raise SitePackageError("site package contains a duplicate archive path")
                by_name[info.filename] = info
            manifest_info = by_name.get(MANIFEST_NAME)
            if manifest_info is None or manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise SitePackageError("site package manifest is missing or too large")
            try:
                manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
                raise SitePackageError("site package manifest is invalid") from exc
            if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
                raise SitePackageError("site package manifest schema is unsupported")
            visibility = manifest.get("visibility")
            if visibility not in {"private", "public"}:
                raise SitePackageError("site package manifest visibility is invalid")
            files = manifest.get("files")
            if not isinstance(files, list) or len(files) > MAX_ENTRIES:
                raise SitePackageError("site package manifest file list is invalid")
            expected = {}
            for item in files:
                if not isinstance(item, dict):
                    raise SitePackageError("site package manifest entry is invalid")
                name = item.get("path")
                size = item.get("size_bytes")
                item_digest = item.get("sha256")
                if (
                    not isinstance(name, str)
                    or not _safe_zip_name(name)
                    or name == MANIFEST_NAME
                    or name in expected
                    or not isinstance(size, int)
                    or size < 0
                    or size > MAX_MEMBER_BYTES
                    or not isinstance(item_digest, str)
                    or len(item_digest) != 64
                ):
                    raise SitePackageError("site package manifest entry is invalid")
                expected[name] = (size, item_digest.casefold())
            actual = set(by_name) - {MANIFEST_NAME}
            if actual != set(expected):
                raise SitePackageError("site package manifest does not match archive files")
            total_bytes = 0
            for name, (expected_size, expected_digest) in expected.items():
                info = by_name[name]
                if info.file_size > MAX_MEMBER_BYTES:
                    raise SitePackageError("site package member exceeds the 128 MiB limit")
                member_digest = hashlib.sha256()
                member_size = 0
                with archive.open(info, "r") as handle:
                    while True:
                        block = handle.read(1024 * 1024)
                        if not block:
                            break
                        member_size += len(block)
                        if member_size > MAX_MEMBER_BYTES:
                            raise SitePackageError("site package member exceeds the 128 MiB limit")
                        member_digest.update(block)
                if member_size != expected_size or member_digest.hexdigest() != expected_digest:
                    raise SitePackageError("site package file digest does not match manifest")
                total_bytes += member_size
                if total_bytes > MAX_PACKAGE_BYTES:
                    raise SitePackageError("site package contents exceed the 256 MiB limit")
            if manifest.get("file_count") != len(expected) or manifest.get("total_bytes") != total_bytes:
                raise SitePackageError("site package manifest totals do not match archive")
    except SitePackageError:
        raise
    except (OSError, zipfile.BadZipFile, ValueError, KeyError) as exc:
        raise SitePackageError("site package verification failed: {}".format(type(exc).__name__)) from exc
    return SitePackageVerification(
        package=package,
        visibility=visibility,
        sha256=digest,
        file_count=len(expected),
        total_bytes=total_bytes,
    )


__all__ = [
    "MANIFEST_NAME",
    "SitePackageError",
    "SitePackageResult",
    "SitePackageVerification",
    "package_site",
    "verify_site_package",
]
