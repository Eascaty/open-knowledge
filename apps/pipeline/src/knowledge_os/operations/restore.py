"""Non-destructive SQLite restore drills for existing snapshots."""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from contextlib import ExitStack, closing
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


REQUIRED_TABLES = (
    "documents",
    "documents_fts",
    "events",
    "jobs",
    "metadata",
    "nodes",
    "placements",
    "relations",
    "sources",
)


class RestoreDrillError(RuntimeError):
    """Raised when a snapshot cannot be safely restored and verified."""


@dataclass(frozen=True)
class RestoreDrillResult:
    snapshot: Path
    sha256: str
    schema_version: int
    counts: Dict[str, int]
    integrity: str = "ok"
    foreign_key_violations: int = 0


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
    return "{}?mode=ro&immutable=1".format(path.resolve().as_uri())


def run_restore_drill(
    snapshot_path: Path, *, expected_sha256: Optional[str] = None
) -> RestoreDrillResult:
    """Restore into a disposable candidate database; never touch live state."""

    unresolved = snapshot_path.expanduser()
    if unresolved.is_symlink():
        raise RestoreDrillError("restore drill rejects symbolic links")
    snapshot = unresolved.resolve()
    if not snapshot.is_file():
        raise RestoreDrillError("snapshot is not a regular file")
    digest = _sha256(snapshot)
    if expected_sha256 is not None and digest.casefold() != expected_sha256.casefold():
        raise RestoreDrillError("snapshot SHA-256 does not match the expected value")

    try:
        with tempfile.TemporaryDirectory(prefix="knowledge-restore-drill-") as temporary:
            candidate_path = Path(temporary) / "candidate.sqlite3"
            with ExitStack() as stack:
                source = stack.enter_context(
                    closing(
                        sqlite3.connect(
                            _readonly_uri(snapshot), uri=True, timeout=5.0
                        )
                    )
                )
                candidate = stack.enter_context(
                    closing(sqlite3.connect(str(candidate_path), timeout=5.0))
                )
                source.backup(candidate)
                candidate.commit()
                integrity = [
                    str(row[0])
                    for row in candidate.execute("PRAGMA integrity_check").fetchall()
                ]
                if integrity != ["ok"]:
                    raise RestoreDrillError("candidate integrity_check failed")
                violations = candidate.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise RestoreDrillError("candidate foreign_key_check failed")
                tables = {
                    str(row[0])
                    for row in candidate.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                missing = sorted(set(REQUIRED_TABLES) - tables)
                if missing:
                    raise RestoreDrillError(
                        "candidate is missing required tables: {}".format(
                            ", ".join(missing)
                        )
                    )
                version_row = candidate.execute(
                    "SELECT value FROM metadata WHERE key='schema_version'"
                ).fetchone()
                if version_row is None or int(version_row[0]) not in {1, 2}:
                    raise RestoreDrillError("candidate schema version is unsupported")
                version = int(version_row[0])
                counts = {
                    table: int(
                        candidate.execute(
                            "SELECT COUNT(*) FROM {}".format(table)
                        ).fetchone()[0]
                    )
                    for table in REQUIRED_TABLES
                }
                source_counts = {
                    table: int(
                        source.execute(
                            "SELECT COUNT(*) FROM {}".format(table)
                        ).fetchone()[0]
                    )
                    for table in REQUIRED_TABLES
                }
                if counts != source_counts:
                    raise RestoreDrillError("candidate row counts do not match snapshot")
    except RestoreDrillError:
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        raise RestoreDrillError(
            "snapshot restore drill failed: {}".format(type(exc).__name__)
        ) from exc
    return RestoreDrillResult(
        snapshot=snapshot,
        sha256=digest,
        schema_version=version,
        counts=counts,
    )
