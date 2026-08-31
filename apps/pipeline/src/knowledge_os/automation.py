"""One-command, offline end-to-end automation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import db
from .ai import adapter_from_runtime
from .config import (
    ProjectPaths,
    initialize_layout,
    load_runtime,
    load_taxonomy,
    atomic_write_json,
)
from .ingest import IngestError, discover_files, ingest_file
from .knowledge import build_site_data, process_jobs
from .operations import (
    ProjectLock,
    run_health_checks,
    run_prebuild_gate,
    write_health_report,
)
from .site import build_site


@dataclass(frozen=True)
class AutomationResult:
    project_root: str
    ingested: int
    duplicates: int
    ingest_failed: int
    jobs_claimed: int
    jobs_completed: int
    jobs_retried: int
    jobs_failed: int
    jobs_pending: int
    documents: int
    site_output: str
    gate_allowed: bool
    health_status: str
    health_report: str
    network_requests: int = 0

    @property
    def ok(self) -> bool:
        return (
            self.jobs_failed == 0
            and self.ingest_failed == 0
            and self.jobs_pending == 0
            and self.gate_allowed
            and self.health_status != "FAIL"
        )

    def to_dict(self) -> Dict[str, object]:
        value = asdict(self)
        value["ok"] = self.ok
        return value


def _inbox_sources(paths: ProjectPaths) -> List[Path]:
    discovered = list(discover_files((paths.inbox_dir,), paths, recursive=True))
    ignored_root_files = {"readme.md", "urls.txt"}
    return [
        path
        for path in discovered
        if not (
            path.parent == paths.inbox_dir
            and path.name.casefold() in ignored_root_files
        )
    ]


def _candidate_directory(site_output: Path) -> Path:
    site_output.parent.mkdir(parents=True, exist_ok=True)
    return Path(
        tempfile.mkdtemp(
            prefix=".{}-candidate-".format(site_output.name),
            dir=str(site_output.parent),
        )
    )


def _resolved_live_site(site_output: Path) -> Path:
    requested_output = site_output.expanduser()
    if requested_output.is_symlink():
        raise ValueError("live site cannot be a symbolic link")
    resolved = requested_output.resolve(strict=False)
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("live site exists and is not a directory")
    return resolved


def _publication_backup(site_output: Path) -> Path:
    return site_output.with_name(".{}-rollback".format(site_output.name))


def _sync_directory(directory: Path) -> None:
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(str(directory), os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        # Some filesystems do not support fsync on directories. The rename is
        # still atomic there; startup recovery remains the fallback.
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _checked_generated_directory(path: Path, *, label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("{} must be a regular directory".format(label))
    return path


def _recover_publication(
    site_output: Path, *, cleanup_candidates: bool = True
) -> Path:
    """Recover an interrupted directory swap and remove stale generated state."""

    site_output = _resolved_live_site(site_output)
    parent = site_output.parent
    backup = _publication_backup(site_output)
    legacy_backups = sorted(
        item
        for item in parent.glob(".{}-rollback-*".format(site_output.name))
        if item != backup
    )
    backups = ([backup] if backup.exists() else []) + legacy_backups
    for item in backups:
        _checked_generated_directory(item, label="rollback site")

    if site_output.exists():
        for item in backups:
            shutil.rmtree(item)
    elif backups:
        if backup.exists():
            selected = backup
        elif len(legacy_backups) == 1:
            selected = legacy_backups[0]
        else:
            raise RuntimeError("multiple rollback sites require manual recovery")
        os.replace(str(selected), str(site_output))
        _sync_directory(parent)
        for item in backups:
            if item.exists():
                shutil.rmtree(item)

    if cleanup_candidates:
        for candidate in parent.glob(
            ".{}-candidate-*".format(site_output.name)
        ):
            _checked_generated_directory(candidate, label="stale candidate site")
            shutil.rmtree(candidate)
    return site_output


def _publish_candidate(candidate: Path, site_output: Path) -> None:
    """Replace the live site, restoring the previous directory on failure."""

    requested_candidate = candidate.expanduser()
    if requested_candidate.is_symlink():
        raise ValueError("candidate site cannot be a symbolic link")
    candidate = requested_candidate.resolve(strict=True)
    site_output = _recover_publication(site_output, cleanup_candidates=False)
    if candidate.parent != site_output.parent:
        raise ValueError("candidate and live site must share one parent directory")
    if not candidate.is_dir():
        raise ValueError("candidate site must be a regular directory")

    backup = _publication_backup(site_output)
    if backup.exists():
        raise ValueError("rollback directory unexpectedly exists")
    had_live_site = site_output.exists()
    if had_live_site:
        os.replace(str(site_output), str(backup))
        _sync_directory(site_output.parent)
    try:
        os.replace(str(candidate), str(site_output))
        _sync_directory(site_output.parent)
    except BaseException:
        if had_live_site and backup.exists() and not site_output.exists():
            os.replace(str(backup), str(site_output))
            _sync_directory(site_output.parent)
        raise
    if backup.exists():
        try:
            shutil.rmtree(backup)
        except OSError:
            # Publication has committed. A retained private rollback copy is
            # safer than reporting failure after the live site changed.
            pass


def run_full_pipeline(
    project_root: Path,
    *,
    visibility: str = "private",
    max_jobs: int = 1000,
) -> AutomationResult:
    """Ingest inbox files, process knowledge, build the site and run checks."""

    if visibility not in ("private", "public"):
        raise ValueError("visibility must be private or public")
    paths = ProjectPaths.from_root(project_root)
    with ProjectLock(paths.root, purpose="full-pipeline"):
        initialize_layout(paths)
        site_output = (
            paths.site_dir / "dist"
            if visibility == "private"
            else paths.exports_dir / "public"
        )
        site_output = _recover_publication(site_output)
        connection = db.connect(paths.database_file)
        try:
            db.initialize_database(connection)
            taxonomy = load_taxonomy(paths)
            runtime = load_runtime(paths)
            db.sync_taxonomy(connection, taxonomy)
            ingested = 0
            duplicates = 0
            ingest_failed = 0
            for source in _inbox_sources(paths):
                try:
                    result = ingest_file(connection, paths, source, runtime)
                    if result.duplicate:
                        duplicates += 1
                    else:
                        ingested += 1
                except (IngestError, OSError, ValueError) as exc:
                    ingest_failed += 1
                    identity = hashlib.sha256(
                        source.name.encode("utf-8", errors="replace")
                    ).hexdigest()[:20]
                    atomic_write_json(
                        paths.quarantine_dir / f"ingest-{identity}.json",
                        {
                            "stage": "ingest",
                            "source_name": source.name,
                            "error_type": type(exc).__name__,
                            "recorded_at": db.utc_now(),
                            "note": "原始收件箱文件未修改；修复后可自动重试。",
                        },
                    )
            summary = process_jobs(
                connection,
                paths,
                runtime,
                taxonomy,
                adapter_from_runtime(runtime),
                max_jobs=max_jobs,
            )
            queue_status = db.status_summary(connection)
            jobs_failed = max(
                int(summary.failed),
                int(queue_status["job_status"].get("failed", 0)),
            )
            jobs_pending = max(
                int(summary.pending), int(queue_status["jobs_pending"])
            )
            canonical = build_site_data(
                connection,
                paths,
                taxonomy,
                runtime,
                visibility=visibility,
            )
            document_count = len(canonical["documents"])
        finally:
            connection.close()

        candidate = _candidate_directory(site_output)
        try:
            build_site(
                paths.site_data_dir / "site-data.json",
                candidate,
                visibility=visibility,
            )
            candidate_canonical = candidate / "data" / "site-data.json"
            gate = run_prebuild_gate(
                paths.root,
                canonical_path=candidate_canonical,
                candidate_roots=(candidate,),
                expected_visibility=visibility,
            )
            health = run_health_checks(
                paths.root,
                canonical_path=candidate_canonical,
                candidate_roots=(candidate,),
            )
            health_path = write_health_report(health)
            if jobs_pending == 0 and gate.allowed and health.passed:
                _publish_candidate(candidate, site_output)
        finally:
            if candidate.exists():
                shutil.rmtree(candidate)

    return AutomationResult(
        project_root=str(paths.root),
        ingested=ingested,
        duplicates=duplicates,
        ingest_failed=ingest_failed,
        jobs_claimed=summary.claimed,
        jobs_completed=summary.completed,
        jobs_retried=summary.retried,
        jobs_failed=jobs_failed,
        jobs_pending=jobs_pending,
        documents=document_count,
        site_output=str(site_output),
        gate_allowed=gate.allowed,
        health_status=health.status.value,
        health_report=str(health_path),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m knowledge_os.automation",
        description="扫描 inbox 并离线完成入库、整理、网站构建和健康检查",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--visibility", choices=("private", "public"), default="private"
    )
    parser.add_argument("--max-jobs", type=int, default=1000)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        result = run_full_pipeline(
            arguments.root,
            visibility=arguments.visibility,
            max_jobs=arguments.max_jobs,
        )
    except Exception as exc:
        print(
            "knowledge-os automation: {}: {}".format(type(exc).__name__, exc),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
