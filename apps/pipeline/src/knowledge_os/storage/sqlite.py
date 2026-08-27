"""Backward-compatible facade for split SQLite storage modules."""

from .documents import (
    documents_by_source,
    index_document,
    mark_source_status,
    place_document,
    query_documents,
    replace_source_documents,
    status_summary,
    update_document_enrichment,
    upsert_document,
)
from .queue import (
    claim_next_job,
    enqueue_job,
    fail_job,
    finish_job,
    recover_stale_jobs,
)
from .records import add_event, insert_source, source_by_hash, source_by_id
from .schema import SCHEMA_VERSION, connect, initialize_database, utc_now
from .taxonomy import sync_taxonomy

__all__ = [
    "SCHEMA_VERSION",
    "add_event",
    "claim_next_job",
    "connect",
    "documents_by_source",
    "enqueue_job",
    "fail_job",
    "finish_job",
    "index_document",
    "initialize_database",
    "insert_source",
    "mark_source_status",
    "place_document",
    "query_documents",
    "recover_stale_jobs",
    "replace_source_documents",
    "source_by_hash",
    "source_by_id",
    "status_summary",
    "sync_taxonomy",
    "update_document_enrichment",
    "upsert_document",
    "utc_now",
]
