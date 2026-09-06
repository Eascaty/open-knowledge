"""Create a local, verifiable ZIP package of a built knowledge site."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Union


PathLike = Union[str, os.PathLike]
MANIFEST_NAME = "knowledge-site-package.json"


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
        return {"visibility": visibility}
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
    return {"visibility": visibility, "content_digest": value.get("content_digest")}


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
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
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
                archive.write(path, relative)
            archive.writestr(MANIFEST_NAME, manifest_bytes)
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


__all__ = ["MANIFEST_NAME", "SitePackageError", "SitePackageResult", "package_site"]
