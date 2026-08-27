"""Retryable extraction, enrichment and indexing use cases."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from .. import db
from ..ai import Adapter
from ..config import ProjectPaths, atomic_write_text
from .artifacts import (
    _quarantine,
    _remove_generated_knowledge_notes,
    _upsert_relations,
    _write_knowledge_note,
)
from .classification import classify_document
from .extraction import ExtractionError, _normalized_markdown, extract_source
from .heading_cards import document_id_for_card, split_document


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

    cards = split_document(
        title=title,
        body=body,
        original_name=str(source["original_name"]),
        runtime=runtime,
    )
    records = []
    current_paths = set()
    for card in cards:
        document_id = document_id_for_card(
            str(source["id"]), str(source["sha256"]), card
        )
        filename = (
            f"{source['sha256']}.md"
            if card.section_index == 0
            else "{}-{:03d}-{}.md".format(
                source["sha256"], card.section_index, document_id[-12:]
            )
        )
        normalized_path = (
            paths.normalized_dir / str(source["sha256"])[:2] / filename
        )
        markdown = _normalized_markdown(
            document_id=document_id,
            source_id=str(source["id"]),
            sha256=str(source["sha256"]),
            title=card.title,
            original_name=str(source["original_name"]),
            imported_at=str(source["imported_at"]),
            body=card.body,
            section_index=card.section_index,
            heading_path=card.heading_path,
            source_line_start=card.source_line_start,
            source_line_end=card.source_line_end,
            body_sha256=card.body_sha256,
            splitter_version=card.splitter_version,
        )
        atomic_write_text(normalized_path, markdown)
        relative_normalized = normalized_path.relative_to(paths.root).as_posix()
        current_paths.add(relative_normalized)
        records.append(
            {
                "id": document_id,
                "source_id": source["id"],
                "section_index": card.section_index,
                "heading_path": list(card.heading_path),
                "source_line_start": card.source_line_start,
                "source_line_end": card.source_line_end,
                "body_sha256": card.body_sha256,
                "splitter_version": card.splitter_version,
                "title": card.title,
                "normalized_path": relative_normalized,
                "body": card.body,
                "visibility": "private",
                "model_name": "pending",
                "prompt_version": "pending",
            }
        )
    stale_paths = db.replace_source_documents(
        connection, str(source["id"]), records
    )
    for stale_id, relative in stale_paths:
        if relative in current_paths:
            continue
        stale_path = (paths.root / relative).resolve()
        try:
            stale_path.relative_to(paths.normalized_dir.resolve())
        except ValueError:
            continue
        if stale_path.is_file() and not stale_path.is_symlink():
            stale_path.unlink()
        _remove_generated_knowledge_notes(paths, stale_id)
    if len(cards) > 1:
        db.add_event(
            connection,
            "source_split",
            source_id=str(source["id"]),
            details={
                "documents": len(cards),
                "splitter_version": cards[0].splitter_version,
            },
        )
        connection.commit()
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
    documents = db.documents_by_source(connection, str(source["id"]))
    if not documents:
        raise ExtractionError("document was not extracted before enrichment")
    for document in documents:
        heading_path = json.loads(str(document["heading_path_json"]))
        analysis_title = (
            " / ".join(str(value) for value in heading_path)
            if heading_path
            else str(document["title"])
        )
        extraction = adapter.extract(
            title=analysis_title,
            body=str(document["body"]),
            taxonomy=taxonomy,
        )
        classification = classify_document(
            title=analysis_title,
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
            details={
                "document_id": str(document["id"]),
                "path": vault_path,
                "node_id": classification.node_id,
            },
        )
        connection.commit()
    db.mark_source_status(connection, str(source["id"]), "classified")
    db.enqueue_job(
        connection,
        str(source["id"]),
        "index",
        max_attempts=int(runtime.get("pipeline", {}).get("max_attempts", 3)),
    )


def _process_index(connection: Any, source: Mapping[str, Any]) -> None:
    documents = db.documents_by_source(connection, str(source["id"]))
    if not documents:
        raise ExtractionError("source has no documents to index")
    for document in documents:
        db.index_document(connection, str(document["id"]))
    db.mark_source_status(connection, str(source["id"]), "completed")


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
                _process_index(connection, source)
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
