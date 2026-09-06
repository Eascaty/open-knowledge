"""Vault notes, relations, quarantine and canonical exports."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .. import db
from ..ai import KnowledgeExtraction, RelationSuggestion
from ..config import ProjectPaths, atomic_write_json, atomic_write_text
from ..storage.reviews import (
    DEFAULT_REVIEW_STATUS,
    review_metadata_by_document,
)
from .classification import Classification
from .extraction import ExtractionError

def _safe_component(name: str) -> str:
    value = re.sub(r"[\x00-\x1f/\\:]+", "-", name).strip(" .")
    return (value or "未命名")[:100]


def _remove_generated_knowledge_notes(
    paths: ProjectPaths,
    document_id: str,
    *,
    keep: Optional[Path] = None,
) -> int:
    """Remove obsolete generated notes without touching arbitrary Vault files."""

    expected_id = "id: {}".format(json.dumps(document_id, ensure_ascii=False))
    kept = keep.resolve() if keep is not None else None
    vault_root = paths.vault_dir.resolve()
    removed = 0
    for candidate in paths.vault_dir.glob("**/资料/*.md"):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            resolved = candidate.resolve()
            resolved.relative_to(vault_root)
            if kept is not None and resolved == kept:
                continue
            with candidate.open("r", encoding="utf-8") as handle:
                prefix = handle.read(16384)
        except (OSError, UnicodeError, ValueError):
            continue
        frontmatter = prefix.split("---", 2)
        if (
            len(frontmatter) < 3
            or expected_id not in frontmatter[1].splitlines()
            or "source_sha256:" not in frontmatter[1]
            or "prompt_version:" not in frontmatter[1]
        ):
            continue
        candidate.unlink()
        removed += 1
    return removed


def _write_knowledge_note(
    paths: ProjectPaths,
    *,
    source: Mapping[str, Any],
    document: Mapping[str, Any],
    extraction: KnowledgeExtraction,
    classification: Classification,
) -> str:
    display_path = classification.path_names[1:]
    directory = paths.vault_dir.joinpath(
        *[_safe_component(name) for name in display_path]
    )
    filename = f"{_safe_component(str(document['title']))}-{str(document['id'])[-12:]}.md"
    output = directory / "资料" / filename
    _remove_generated_knowledge_notes(
        paths, str(document["id"]), keep=output
    )
    metadata = {
        "generated_by": "knowledge-os/card-v1",
        "id": document["id"],
        "source_id": source["id"],
        "source_sha256": source["sha256"],
        "section_index": document.get("section_index", 0),
        "heading_path": json.loads(document.get("heading_path_json", "[]")),
        "source_line_start": document.get("source_line_start"),
        "source_line_end": document.get("source_line_end"),
        "body_sha256": document.get("body_sha256", ""),
        "splitter_version": document.get("splitter_version", "single-v1"),
        "taxonomy_node_id": classification.node_id,
        "taxonomy_path": display_path,
        "classification_method": classification.method,
        "classification_confidence": round(classification.confidence, 4),
        "visibility": document.get("visibility", "private"),
        "model": extraction.model_name,
        "prompt_version": extraction.prompt_version,
        "updated_at": db.utc_now(),
    }
    lines = ["---"]
    for key, value in metadata.items():
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.extend(
        [
            "---",
            "",
            f"# {document['title']}",
            "",
            "## 摘要",
            "",
            extraction.summary or "尚无摘要。",
            "",
            "## 关键知识",
            "",
        ]
    )
    if extraction.key_points:
        lines.extend(f"- {point}" for point in extraction.key_points)
    else:
        lines.append("- 尚未提取关键知识。")
    lines.extend(
        [
            "",
            "## 标签",
            "",
            "、".join(extraction.tags) if extraction.tags else "无",
            "",
            "## 来源证据",
            "",
            f"- 原始文件：`{source['raw_path']}`",
            f"- SHA-256：`{source['sha256']}`",
            f"- 原始名称：{source['original_name']}",
            "- 原文行号：{}".format(
                "L{}-L{}".format(
                    document.get("source_line_start"),
                    document.get("source_line_end"),
                )
                if document.get("source_line_start") is not None
                else "未记录"
            ),
            "",
            "## 标准化正文",
            "",
            str(document["body"]).strip(),
            "",
        ]
    )
    atomic_write_text(output, "\n".join(lines))
    try:
        return output.relative_to(paths.root).as_posix()
    except ValueError:
        return str(output)


def _upsert_relations(
    connection: Any,
    document_id: str,
    relations: Sequence[RelationSuggestion],
) -> None:
    valid_ids = {
        str(row[0]) for row in connection.execute("SELECT id FROM nodes WHERE active=1")
    }
    connection.execute("DELETE FROM relations WHERE document_id=?", (document_id,))
    for relation in relations:
        if (
            relation.from_node_id not in valid_ids
            or relation.to_node_id not in valid_ids
            or relation.from_node_id == relation.to_node_id
        ):
            continue
        identity = "|".join(
            [
                document_id,
                relation.from_node_id,
                relation.to_node_id,
                relation.relation_type,
            ]
        )
        relation_id = "rel-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        connection.execute(
            """
            INSERT OR REPLACE INTO relations(
                id, from_node_id, to_node_id, relation_type, label,
                document_id, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                relation_id,
                relation.from_node_id,
                relation.to_node_id,
                relation.relation_type[:80],
                relation.label[:200],
                document_id,
                max(0.0, min(float(relation.confidence), 1.0)),
            ),
        )
    connection.commit()


def _quarantine(
    paths: ProjectPaths,
    source: Mapping[str, Any],
    *,
    stage: str,
    error: str,
) -> None:
    record = {
        "source_id": source["id"],
        "stage": stage,
        "error": error,
        "raw_path": source["raw_path"],
        "recorded_at": db.utc_now(),
        "note": "原始资料仍保留在 raw；修复解析器后可重试。",
    }
    atomic_write_json(paths.quarantine_dir / f"{source['id']}.json", record)



def rebuild_vault_indexes(
    connection: Any, paths: ProjectPaths
) -> int:
    nodes = connection.execute(
        "SELECT * FROM nodes WHERE active=1 ORDER BY level, sort_order, name"
    ).fetchall()
    written = 0
    for node in nodes:
        names = json.loads(node["path_json"])
        if int(node["level"]) == 0:
            directory = paths.vault_dir
        else:
            directory = paths.vault_dir.joinpath(
                *[_safe_component(name) for name in names[1:]]
            )
        children = connection.execute(
            """
            SELECT name FROM nodes
            WHERE parent_id=? AND active=1
            ORDER BY sort_order, name
            """,
            (node["id"],),
        ).fetchall()
        documents = connection.execute(
            """
            SELECT d.title, d.id, d.summary
            FROM documents d
            JOIN placements p ON p.document_id=d.id
            WHERE p.node_id=?
            ORDER BY d.updated_at DESC
            """,
            (node["id"],),
        ).fetchall()
        lines = [
            "---",
            f"id: {json.dumps(node['id'], ensure_ascii=False)}",
            f"taxonomy_path: {json.dumps(names[1:], ensure_ascii=False)}",
            'generated_by: "knowledge-os/index-v1"',
            "---",
            "",
            f"# {node['name']}",
            "",
        ]
        if children:
            lines.extend(["## 子级", ""])
            lines.extend(f"- [{child['name']}](./{_safe_component(child['name'])}/)" for child in children)
            lines.append("")
        lines.extend(["## 本节点资料", ""])
        if documents:
            for document in documents:
                lines.append(f"- **{document['title']}** — {document['summary'][:180]}")
        else:
            lines.append("- 暂无资料。")
        lines.append("")
        atomic_write_text(directory / "_index.md", "\n".join(lines))
        written += 1
    return written


def _document_rows(connection: Any, visibility: str) -> List[Any]:
    condition = "" if visibility == "private" else "WHERE d.visibility='public'"
    return connection.execute(
        f"""
        SELECT d.*, s.kind, s.origin, s.original_name, s.raw_path, s.sha256,
               p.node_id, p.confidence, p.method, n.path_json
        FROM documents d
        JOIN sources s ON s.id=d.source_id
        JOIN placements p ON p.document_id=d.id
        JOIN nodes n ON n.id=p.node_id
        {condition}
        ORDER BY d.updated_at DESC, d.id
        """
    ).fetchall()


def build_site_data(
    connection: Any,
    paths: ProjectPaths,
    taxonomy: Mapping[str, Any],
    runtime: Mapping[str, Any],
    *,
    visibility: str = "private",
    output: Optional[Path] = None,
) -> Dict[str, Any]:
    if visibility not in {"private", "public"}:
        raise ValueError("visibility must be private or public")
    rebuild_vault_indexes(connection, paths)
    document_rows = _document_rows(connection, visibility)
    active_node_ids = {
        str(row[0])
        for row in connection.execute(
            "SELECT id FROM nodes WHERE active=1"
        ).fetchall()
    }
    orphaned_documents = [
        str(row["id"])
        for row in document_rows
        if str(row["node_id"]) not in active_node_ids
    ]
    if orphaned_documents:
        raise ExtractionError(
            "documents reference inactive taxonomy nodes: {}".format(
                ", ".join(orphaned_documents[:10])
            )
        )
    relation_rows = connection.execute(
        """
        SELECT r.* FROM relations r
        LEFT JOIN documents d ON d.id=r.document_id
        WHERE ?='private' OR d.visibility='public'
        ORDER BY r.id
        """,
        (visibility,),
    ).fetchall()
    relations = [
        {
            "id": row["id"],
            "from_node_id": row["from_node_id"],
            "to_node_id": row["to_node_id"],
            "type": row["relation_type"],
            "label": row["label"],
            "document_id": row["document_id"],
            "confidence": row["confidence"],
        }
        for row in relation_rows
    ]
    relations_by_document: Dict[str, List[str]] = {}
    for relation in relations:
        document_id = relation.get("document_id")
        if document_id:
            relations_by_document.setdefault(str(document_id), []).append(
                str(relation["id"])
            )

    documents: List[Dict[str, Any]] = []
    review_metadata = review_metadata_by_document(connection)
    for row in document_rows:
        path = json.loads(row["path_json"])[1:]
        raw_origin = str(row["origin"])
        # Absolute local paths reveal usernames and folder names without
        # helping a remotely viewed static site. Only durable web citations
        # survive an export; local provenance remains in private SQLite.
        source_origin = (
            raw_origin
            if raw_origin.startswith(("https://", "http://"))
            else ("manual" if raw_origin == "manual" else "local-file")
        )
        current_review = review_metadata.get(
            str(row["id"]),
            {"status": DEFAULT_REVIEW_STATUS, "note": "", "reviewed_at": "", "history": []},
        )
        document_payload = {
                "id": row["id"],
                "source_id": row["source_id"],
                "section_index": row["section_index"],
                "heading_path": json.loads(row["heading_path_json"]),
                "source_line_start": row["source_line_start"],
                "source_line_end": row["source_line_end"],
                "body_sha256": row["body_sha256"],
                "splitter_version": row["splitter_version"],
                "title": row["title"],
                "summary": row["summary"],
                "content": row["body"],
                "key_points": json.loads(row["key_points_json"]),
                "path": path,
                "node_id": row["node_id"],
                "source": {
                    "kind": row["kind"],
                    "original_name": row["original_name"],
                    "origin": source_origin,
                    "sha256": row["sha256"],
                },
                "evidence": [
                    {
                        "id": f"{row['id']}-source",
                        "excerpt": re.sub(r"\s+", " ", str(row["body"])).strip()[:500],
                        "locator": (
                            "{}:L{}-L{}".format(
                                row["original_name"],
                                row["source_line_start"],
                                row["source_line_end"],
                            )
                            if row["source_line_start"] is not None
                            else row["original_name"]
                        ),
                        "source_label": row["original_name"],
                    }
                ],
                "tags": json.loads(row["tags_json"]),
                "visibility": row["visibility"],
                "status": current_review["status"],
                "relations": relations_by_document.get(str(row["id"]), []),
                "classification": {
                    "confidence": row["confidence"],
                    "method": row["method"],
                },
                "model": {
                    "name": row["model_name"],
                    "prompt_version": row["prompt_version"],
                },
                "updated_at": row["updated_at"],
            }
        if visibility == "private":
            document_payload["review"] = {
                "note": current_review.get("note", ""),
                "reviewed_at": current_review.get("reviewed_at", ""),
                "history": current_review.get("history", []),
            }
        documents.append(document_payload)
    counts_by_node = {
        str(row["node_id"]): int(row["count"])
        for row in connection.execute(
            """
            SELECT p.node_id, COUNT(*) AS count
            FROM placements p JOIN documents d ON d.id=p.document_id
            WHERE ?='private' OR d.visibility='public'
            GROUP BY p.node_id
            """,
            (visibility,),
        )
    }
    taxonomy_tree = deepcopy(taxonomy["root"])

    def attach_counts(node: Dict[str, Any]) -> int:
        direct = counts_by_node.get(str(node["id"]), 0)
        total = direct
        for child in node.get("children", []):
            total += attach_counts(child)
        node["direct_document_count"] = direct
        node["document_count"] = total
        return total

    attach_counts(taxonomy_tree)
    node_rows = connection.execute(
        "SELECT * FROM nodes WHERE active=1 ORDER BY level, sort_order, name"
    ).fetchall()
    nodes = [
        {
            "id": row["id"],
            "parent_id": row["parent_id"],
            "name": row["name"],
            "level": row["level"],
            "path": json.loads(row["path_json"])[1:],
            "locked": bool(row["locked"]),
            "document_count": counts_by_node.get(str(row["id"]), 0),
        }
        for row in node_rows
    ]
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    status = db.status_summary(connection)
    canonical: Dict[str, Any] = {
        "schema_version": 1,
        "generated_at": generated_at,
        "visibility": visibility,
        "site": {
            "title": runtime.get("site", {}).get("title", "我的知识体系"),
            "visibility": visibility,
        },
        "root": str(taxonomy["root"]["id"]),
        "taxonomy": taxonomy_tree,
        "nodes": nodes,
        "documents": documents,
        "relations": relations,
        "stats": status["counts"],
    }
    output_path = output or (paths.site_data_dir / "site-data.json")
    atomic_write_json(output_path, canonical)
    # Compatibility alias for simple consumers; content is intentionally
    # identical and remains a disposable build artifact.
    if output is None:
        atomic_write_json(paths.site_data_dir / "knowledge.json", canonical)
        atomic_write_json(
            paths.site_data_dir / "taxonomy.json",
            {
                "schema_version": 1,
                "generated_at": generated_at,
                "root": taxonomy_tree,
                "nodes": nodes,
            },
        )
        atomic_write_json(
            paths.site_data_dir / "search-index.json",
            {
                "schema_version": 1,
                "generated_at": generated_at,
                "documents": [
                    {
                        "id": item["id"],
                        "title": item["title"],
                        "summary": item["summary"],
                        "content": item["content"],
                        "path": item["path"],
                        "tags": item["tags"],
                    }
                    for item in documents
                ],
            },
        )
        atomic_write_json(
            paths.site_data_dir / "graph.json",
            {
                "schema_version": 1,
                "generated_at": generated_at,
                "nodes": nodes,
                "relations": relations,
                "document_placements": [
                    {"document_id": item["id"], "node_id": item["node_id"]}
                    for item in documents
                ],
            },
        )
    return canonical
