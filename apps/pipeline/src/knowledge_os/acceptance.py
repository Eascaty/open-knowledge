"""Repeatable, private-data-free batch acceptance suite."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List

from . import db
from .automation import AutomationResult, run_full_pipeline
from .config import ProjectPaths, atomic_write_json, initialize_layout


@dataclass(frozen=True)
class AcceptanceReport:
    ok: bool
    fixture_files: int
    unique_sources: int
    documents: int
    split_cards: int
    first_run_pending: int
    duplicate_second_run: int
    isolated_failure_jobs: int
    isolated_success_documents: int
    gate_allowed: bool
    health_status: str
    network_requests: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _configure(root: Path) -> ProjectPaths:
    paths = ProjectPaths.from_root(root)
    initialize_layout(paths)
    runtime = json.loads(paths.runtime_file.read_text(encoding="utf-8"))
    runtime["pipeline"].update(
        {
            "retry_base_seconds": 0,
            "max_attempts": 2,
            "split_min_chars": 100,
            "split_min_section_chars": 0,
        }
    )
    atomic_write_json(paths.runtime_file, runtime)
    return paths


def _drain(root: Path, first: AutomationResult) -> AutomationResult:
    result = first
    for _ in range(10):
        if result.jobs_pending == 0:
            return result
        result = run_full_pipeline(root, visibility="private", max_jobs=1000)
    raise RuntimeError("acceptance queue did not drain")


def _copy_fixtures(root: Path, fixture_root: Path) -> int:
    paths = ProjectPaths.from_root(root)
    inputs = (
        ("agent_memory.md", fixture_root / "agent_memory.md"),
        ("credit_card_us_stock.md", fixture_root / "credit_card_us_stock.md"),
        ("java_g1.md", fixture_root / "java_g1.md"),
        (
            "long_mixed_knowledge.md",
            fixture_root / "acceptance" / "long_mixed_knowledge.md",
        ),
    )
    for index, (name, source) in enumerate(inputs):
        destination = paths.inbox_dir / "files" / "batch" / str(index) / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    duplicate = paths.inbox_dir / "files" / "duplicates" / "agent-memory-copy.md"
    duplicate.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture_root / "agent_memory.md", duplicate)
    return len(inputs) + 1


def run_acceptance_suite(project_root: Path) -> AcceptanceReport:
    """Exercise healthy batches, splitting, resumption and failure isolation."""

    fixture_root = project_root / "tests" / "fixtures"
    with tempfile.TemporaryDirectory(prefix="knowledge-acceptance-") as temporary:
        healthy_root = Path(temporary) / "healthy"
        healthy_paths = _configure(healthy_root)
        fixture_files = _copy_fixtures(healthy_root, fixture_root)
        first = run_full_pipeline(healthy_root, visibility="private", max_jobs=2)
        first_pending = first.jobs_pending
        healthy = _drain(healthy_root, first)
        second = run_full_pipeline(healthy_root, visibility="private", max_jobs=1000)
        connection = db.connect(healthy_paths.database_file)
        try:
            source_count = int(
                connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            )
            document_count = int(
                connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            )
            split_cards = int(
                connection.execute(
                    "SELECT COUNT(*) FROM documents WHERE splitter_version='markdown-h2-v1'"
                ).fetchone()[0]
            )
        finally:
            connection.close()

        failure_root = Path(temporary) / "failure-isolation"
        failure_paths = _configure(failure_root)
        good = failure_paths.inbox_dir / "files" / "good.md"
        good.write_text("# Java G1\n\nJava JVM G1 垃圾回收排障记录。", encoding="utf-8")
        broken = failure_paths.inbox_dir / "files" / "broken.bin"
        broken.write_bytes(b"\x00\x01\x02\x03")
        isolated = run_full_pipeline(
            failure_root, visibility="private", max_jobs=1000
        )
        connection = db.connect(failure_paths.database_file)
        try:
            isolated_documents = int(
                connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            )
            isolated_failures = int(
                connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status='failed'"
                ).fetchone()[0]
            )
        finally:
            connection.close()

        ok = all(
            (
                first_pending > 0,
                not first.ok,
                healthy.ok,
                second.ok,
                source_count == 4,
                document_count == 6,
                split_cards == 3,
                second.duplicates == fixture_files,
                not isolated.ok,
                isolated.jobs_pending == 0,
                isolated_documents == 1,
                isolated_failures == 1,
                healthy.network_requests == 0,
                isolated.network_requests == 0,
            )
        )
        return AcceptanceReport(
            ok=ok,
            fixture_files=fixture_files,
            unique_sources=source_count,
            documents=document_count,
            split_cards=split_cards,
            first_run_pending=first_pending,
            duplicate_second_run=second.duplicates,
            isolated_failure_jobs=isolated_failures,
            isolated_success_documents=isolated_documents,
            gate_allowed=healthy.gate_allowed,
            health_status=healthy.health_status,
            network_requests=healthy.network_requests + isolated.network_requests,
        )
