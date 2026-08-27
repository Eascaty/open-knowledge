"""Retryable extraction, enrichment and indexing use cases."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .. import db
from ..ai import Adapter
from ..config import ProjectPaths, atomic_write_text
from .artifacts import _quarantine, _upsert_relations, _write_knowledge_note
from .classification import classify_document
from .extraction import ExtractionError, _normalized_markdown, extract_source

@dataclass(frozen=True)
class RunSummary:
    claimed: int
    completed: int
    retried: int
    failed: int
    pending: int = 0

def _process_extract(
    connection: Any,
    paths: ProjectPaths,
    source: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> None:
    raw_path = (paths.root / str(source["raw_path"])).resolve()
    raw_root = paths.raw_dir.resolve()
    try:
        raw_path.relative_to(raw_root)
    except ValueError as exc:
        raise ExtractionError(
            f"raw source escapes the project raw directory: {raw_path}"
        ) from exc
    if not raw_path.is_file():
        raise ExtractionError(f"raw source is missing: {raw_path}")
    title, body = extract_source(raw_path, str(source["original_name"]))
    if not body.strip():
        raise ExtractionError("source yielded an empty document")
    normalized_path = (
        paths.normalized_dir
        / str(source["sha256"])[:2]
        / f"{source['sha256']}.md"
    )
    markdown = _normalized_markdown(
        source_id=str(source["id"]),
        sha256=str(source["sha256"]),
        title=title,
        original_name=str(source["original_name"]),
        imported_at=str(source["imported_at"]),
        body=body,
    )
    atomic_write_text(normalized_path, markdown)
    relative_normalized = normalized_path.relative_to(paths.root).as_posix()
    db.upsert_document(
        connection,
        {
            "id": source["id"],
            "source_id": source["id"],
            "title": title,
            "normalized_path": relative_normalized,
            "body": body,
            "visibility": "private",
            "model_name": "pending",
            "prompt_version": "pending",
        },
    )
    db.enqueue_job(
        connection,
        str(source["id"]),
        "enrich",
        max_attempts=int(runtime.get("pipeline", {}).get("max_attempts", 3)),
    )


def _process_enrich(
    connection: Any,
    paths: ProjectPaths,
    source: Mapping[str, Any],
    runtime: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    adapter: Adapter,
) -> None:
    document = connection.execute(
        "SELECT * FROM documents WHERE id=?", (source["id"],)
    ).fetchone()
    if document is None:
        raise ExtractionError("document was not extracted before enrichment")
    extraction = adapter.extract(
        title=str(document["title"]),
        body=str(document["body"]),
        taxonomy=taxonomy,
    )
    classification = classify_document(
        title=str(document["title"]),
        body=str(document["body"]),
        extraction=extraction,
        taxonomy=taxonomy,
    )
    db.update_document_enrichment(
        connection,
        str(document["id"]),
        summary=extraction.summary,
        key_points=extraction.key_points,
        tags=extraction.tags,
        model_name=extraction.model_name,
        prompt_version=extraction.prompt_version,
    )
    db.place_document(
        connection,
        str(document["id"]),
        classification.node_id,
        classification.confidence,
        classification.method,
    )
    _upsert_relations(connection, str(document["id"]), extraction.relations)
    enriched_document = dict(document)
    enriched_document.update(
        {
            "summary": extraction.summary,
            "key_points": extraction.key_points,
            "tags": extraction.tags,
        }
    )
    vault_path = _write_knowledge_note(
        paths,
        source=source,
        document=enriched_document,
        extraction=extraction,
        classification=classification,
    )
    db.add_event(
        connection,
        "knowledge_note_written",
        source_id=str(source["id"]),
        details={"path": vault_path, "node_id": classification.node_id},
    )
    connection.commit()
    db.enqueue_job(
        connection,
        str(source["id"]),
        "index",
        max_attempts=int(runtime.get("pipeline", {}).get("max_attempts", 3)),
    )


def process_jobs(
    connection: Any,
    paths: ProjectPaths,
    runtime: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    adapter: Adapter,
    *,
    max_jobs: int = 100,
) -> RunSummary:
    pipeline = runtime.get("pipeline", {})
    db.recover_stale_jobs(
        connection, int(pipeline.get("stale_job_minutes", 30))
    )
    claimed = completed = retried = failed = 0
    for _ in range(max(0, int(max_jobs))):
        job = db.claim_next_job(connection)
        if job is None:
            break
        claimed += 1
        source = db.source_by_id(connection, str(job["source_id"]))
        if source is None:
            outcome = db.fail_job(
                connection,
                int(job["id"]),
                "source record is missing",
                retry_base_seconds=0,
            )
            failed += 1 if outcome == "failed" else 0
            retried += 1 if outcome == "retry" else 0
            continue
        try:
            stage = str(job["stage"])
            if stage == "extract":
                _process_extract(connection, paths, source, runtime)
            elif stage == "enrich":
                _process_enrich(
                    connection, paths, source, runtime, taxonomy, adapter
                )
            elif stage == "index":
                db.index_document(connection, str(source["id"]))
            else:
                raise ExtractionError(f"unknown pipeline stage: {stage}")
            db.finish_job(connection, int(job["id"]))
            completed += 1
        except Exception as exc:
            outcome = db.fail_job(
                connection,
                int(job["id"]),
                f"{type(exc).__name__}: {exc}",
                retry_base_seconds=int(pipeline.get("retry_base_seconds", 30)),
            )
            if outcome == "failed":
                failed += 1
                _quarantine(
                    paths,
                    source,
                    stage=str(job["stage"]),
                    error=f"{type(exc).__name__}: {exc}",
                )
            else:
                retried += 1
    pending_row = connection.execute(
        """
        SELECT COUNT(*)
        FROM jobs
        WHERE status IN ('queued', 'running', 'retry')
        """
    ).fetchone()
    pending = int(pending_row[0]) if pending_row is not None else 0
    return RunSummary(
        claimed=claimed,
        completed=completed,
        retried=retried,
        failed=failed,
        pending=pending,
    )
