"""Derive taxonomy, search and graph payloads from canonical data."""

from __future__ import annotations

from typing import Any, Mapping

def _taxonomy_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": data["schema_version"],
        "generated_at": data["generated_at"],
        "root": data["root"],
        "nodes": [
            {
                key: node[key]
                for key in (
                    "id",
                    "parent_id",
                    "name",
                    "level",
                    "path",
                    "locked",
                    "summary",
                    "children",
                    "direct_document_count",
                    "document_count",
                )
            }
            for node in data["nodes"]
        ],
    }


def _search_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for node in data["nodes"]:
        items.append(
            {
                "id": node["id"],
                "type": "node",
                "node_id": node["id"],
                "title": node["name"],
                "path": node["path"],
                "summary": node["summary"],
                "tags": [],
                "status": "",
                "updated_at": "",
                "search_text": " ".join(
                    [*node["path"], node["summary"]]
                ).casefold(),
            }
        )
    for document in data["documents"]:
        searchable = [
            document["title"],
            document["summary"],
            document["content"],
            *document["key_points"],
            *document["tags"],
            *document["path"],
            *[evidence["excerpt"] for evidence in document["evidence"]],
        ]
        items.append(
            {
                "id": document["id"],
                "type": "document",
                "node_id": document["node_id"],
                "title": document["title"],
                "path": document["path"],
                "summary": document["summary"],
                "tags": document["tags"],
                "status": document.get("status", "unverified"),
                "updated_at": document["updated_at"],
                "search_text": " ".join(searchable).casefold(),
            }
        )
    return {
        "schema_version": data["schema_version"],
        "generated_at": data["generated_at"],
        "items": items,
    }


def _graph_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    nodes = [
        {
            "id": node["id"],
            "name": node["name"],
            "path": node["path"],
            "level": node["level"],
            "document_count": node["document_count"],
        }
        for node in data["nodes"]
    ]
    tree_edges = [
        {
            "id": f"tree:{node['parent_id']}:{node['id']}",
            "from": node["parent_id"],
            "to": node["id"],
            "type": "parent_of",
        }
        for node in data["nodes"]
        if node["parent_id"] is not None
    ]
    relation_edges = [
        {
            "id": relation["id"],
            "from": relation["from_node_id"],
            "to": relation["to_node_id"],
            "type": relation["type"],
            "label": relation["label"],
            "document_id": relation["document_id"],
            "confidence": relation["confidence"],
            "evidence_ids": relation["evidence_ids"],
        }
        for relation in data["relations"]
    ]
    return {
        "schema_version": data["schema_version"],
        "generated_at": data["generated_at"],
        "nodes": nodes,
        "tree_edges": tree_edges,
        "relation_edges": relation_edges,
    }
