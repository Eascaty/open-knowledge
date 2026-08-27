"""Verified project database migration orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import ProjectPaths
from ..storage.migrations import migrate_v1_to_v2, read_schema_version
from ..storage.schema import SCHEMA_VERSION, connect
from .snapshot import SnapshotResult, create_sqlite_snapshot


@dataclass(frozen=True)
class MigrationResult:
    changed: bool
    from_version: int
    to_version: int
    requeued_sources: int
    snapshot: Optional[SnapshotResult]


def migrate_project_database(project_root: Path) -> MigrationResult:
    """Migrate one project after creating a verified, recoverable snapshot."""

    paths = ProjectPaths.from_root(project_root)
    connection = connect(paths.database_file)
    try:
        current = read_schema_version(connection)
    finally:
        connection.close()
    if current is None:
        raise RuntimeError("database is not initialized")
    if current == SCHEMA_VERSION:
        return MigrationResult(False, current, current, 0, None)
    if current != 1 or SCHEMA_VERSION != 2:
        raise RuntimeError(
            "unsupported database migration: v{} to v{}".format(
                current, SCHEMA_VERSION
            )
        )

    snapshot = create_sqlite_snapshot(
        paths.database_file,
        paths.private_exports_dir / "backups",
        prefix="knowledge-pre-schema-v2",
    )
    connection = connect(paths.database_file)
    try:
        migration = migrate_v1_to_v2(connection)
        integrity = [
            str(row[0])
            for row in connection.execute("PRAGMA integrity_check").fetchall()
        ]
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        migrated = read_schema_version(connection)
        if integrity != ["ok"] or violations or migrated != SCHEMA_VERSION:
            raise RuntimeError("post-migration database verification failed")
    finally:
        connection.close()
    return MigrationResult(
        True,
        migration.from_version,
        migration.to_version,
        migration.requeued_sources,
        snapshot,
    )
