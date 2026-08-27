"""Compatibility facade for the SQLite storage implementation.

New code should import from :mod:`knowledge_os.storage`; this module keeps the
v0.3 public imports stable while the project moves to explicit module bounds.
"""

from .storage.sqlite import (
    SCHEMA_VERSION,
    add_event,
    claim_next_job,
    connect,
    documents_by_source,
    enqueue_job,
    fail_job,
    finish_job,
    index_document,
    initialize_database,
    insert_source,
    mark_source_status,
    place_document,
    query_documents,
    recover_stale_jobs,
    replace_source_documents,
    source_by_hash,
    source_by_id,
    status_summary,
    sync_taxonomy,
    update_document_enrichment,
    upsert_document,
    utc_now,
)

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
