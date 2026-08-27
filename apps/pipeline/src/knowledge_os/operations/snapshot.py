"""Consistent, local-only SQLite snapshots."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from contextlib import ExitStack, closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Union


PathLike = Union[str, os.PathLike]


class SnapshotError(RuntimeError):
    """Raised when a database snapshot cannot be proven usable."""


@dataclass(frozen=True)
class SnapshotResult:
    source: Path
    snapshot: Path
    sha256: str
    size_bytes: int
    created_at: str
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


def _readonly_uri(path: Path) -> str:
    return "{}?mode=ro".format(path.resolve().as_uri())


def create_sqlite_snapshot(
    database_path: PathLike,
    output_directory: PathLike,
    *,
    prefix: str = "knowledge",
) -> SnapshotResult:
    """Create and verify a point-in-time SQLite backup.

    SQLite's online backup API is used instead of copying database/WAL files.
    The destination is atomically renamed only after ``integrity_check`` passes.
    Existing snapshots are never overwritten.
    """

    source_path = Path(database_path).expanduser().resolve()
    output_path = Path(output_directory).expanduser().resolve()
    if not source_path.is_file():
        raise SnapshotError("SQLite source does not exist: {}".format(source_path))
    output_path.mkdir(parents=True, exist_ok=True, mode=0o700)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".snapshot-", suffix=".sqlite3.tmp", dir=str(output_path)
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    try:
        with ExitStack() as stack:
            source = stack.enter_context(
                closing(
                    sqlite3.connect(
                        _readonly_uri(source_path), uri=True, timeout=5.0
                    )
                )
            )
            destination = stack.enter_context(
                closing(sqlite3.connect(str(temporary), timeout=5.0))
            )
            source.backup(destination)
            destination.commit()
            integrity_rows = [
                str(row[0])
                for row in destination.execute("PRAGMA integrity_check").fetchall()
            ]
            if integrity_rows != ["ok"]:
                raise SnapshotError(
                    "snapshot integrity_check failed: {}".format(
                        "; ".join(integrity_rows[:5])
                    )
                )
        digest = _sha256(temporary)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_prefix = "".join(
            character
            for character in prefix
            if character.isalnum() or character in ("-", "_")
        )[:48] or "knowledge"
        final_path = output_path / "{}-{}-{}.sqlite3".format(
            safe_prefix, stamp, digest[:12]
        )
        if final_path.exists():
            if _sha256(final_path) != digest:
                raise SnapshotError(
                    "snapshot destination already exists with different content"
                )
            temporary.unlink()
        else:
            os.replace(str(temporary), str(final_path))
        try:
            os.chmod(final_path, 0o600)
        except OSError:
            pass
        return SnapshotResult(
            source=source_path,
            snapshot=final_path,
            sha256=digest,
            size_bytes=final_path.stat().st_size,
            created_at=created_at,
        )
    except (OSError, sqlite3.Error) as exc:
        raise SnapshotError("SQLite snapshot failed: {}".format(exc)) from exc
    finally:
        if temporary.exists():
            temporary.unlink()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(temporary) + suffix)
            if sidecar.exists():
                sidecar.unlink()
