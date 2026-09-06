"""Validate and normalize canonical knowledge data."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union
from urllib.parse import urlsplit, urlunsplit

from .model import SCHEMA_VERSION, VALID_VISIBILITIES, SiteDataError
from .review_normalize import normalize_review


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return []
    return [text for item in value if (text := _as_text(item))]


def _as_optional_integer(value: Any, *, minimum: int) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        integer = int(value)
    except (TypeError, ValueError):
        return None
    return integer if integer >= minimum else None


DataInput = Union[Mapping[str, Any], str, os.PathLike]


def _load_data(data_or_path: DataInput) -> dict[str, Any]:
    if isinstance(data_or_path, Mapping):
        return copy.deepcopy(dict(data_or_path))
    path = Path(data_or_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SiteDataError(f"{path} 不是有效 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise SiteDataError("canonical JSON 顶层必须是对象")
    return payload


def _require_identifier(value: Any, field: str) -> str:
    identifier = _as_text(value)
    if not identifier:
        raise SiteDataError(f"{field} 不能为空")
    if len(identifier) > 200:
        raise SiteDataError(f"{field} 过长")
    return identifier


def _safe_public_url(value: Any) -> str:
    """Return a publishable HTTP(S) URL without query credentials or fragments."""

    raw = _as_text(value)
    if not raw:
        return ""
    try:
        split = urlsplit(raw)
    except ValueError:
        return ""
    if split.scheme not in {"http", "https"} or not split.hostname:
        return ""
    if split.username or split.password:
        return ""
    hostname = split.hostname
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if split.port:
        netloc = f"{netloc}:{split.port}"
    # Public knowledge pages do not need tracking or signed query parameters.
    # Dropping the whole query is safer than trying to enumerate secret names.
    return urlunsplit((split.scheme, netloc, split.path, "", ""))


def _normalize_source(source: Any, visibility: str) -> dict[str, Any]:
    if isinstance(source, str):
        source = {"origin": source, "original_name": Path(source).name}
    if not isinstance(source, Mapping):
        return {}

    original_name = Path(_as_text(source.get("original_name"))).name
    public_url = _safe_public_url(source.get("public_url"))
    origin_url = _safe_public_url(source.get("origin"))
    origin = public_url or origin_url
    normalized: dict[str, Any] = {
        "kind": _as_text(source.get("kind"), "unknown"),
        "original_name": original_name,
        "origin": origin,
        "sha256": _as_text(source.get("sha256")),
    }
    if visibility == "private":
        # Local absolute paths are deliberately not shipped even in an
        # Access-protected site.  The basename above is enough to identify the
        # local source while avoiding a home-directory disclosure.
        normalized["available_locally"] = bool(
            _as_text(source.get("origin")) and not origin_url
        )
    return normalized


def _normalize_evidence(evidence: Any) -> list[dict[str, str]]:
    if not isinstance(evidence, Sequence) or isinstance(
        evidence, (str, bytes, bytearray)
    ):
        return []
    result: list[dict[str, str]] = []
    for index, item in enumerate(evidence):
        if isinstance(item, str):
            excerpt = item.strip()
            if excerpt:
                result.append(
                    {
                        "id": f"evidence-{index + 1}",
                        "excerpt": excerpt,
                        "locator": "",
                        "source_label": "",
                    }
                )
            continue
        if not isinstance(item, Mapping):
            continue
        excerpt = _as_text(item.get("excerpt") or item.get("quote"))
        if not excerpt:
            continue
        result.append(
            {
                "id": _as_text(item.get("id"), f"evidence-{index + 1}"),
                "excerpt": excerpt,
                "locator": _as_text(
                    item.get("locator")
                    or item.get("page")
                    or item.get("timestamp")
                ),
                "source_label": _as_text(
                    item.get("source_label") or item.get("source")
                ),
            }
        )
    return result


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _flatten_taxonomy(taxonomy: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Flatten the nested taxonomy format used by project configuration."""

    root_value = taxonomy.get("root")
    root = root_value if isinstance(root_value, Mapping) else taxonomy
    nodes: list[dict[str, Any]] = []
    used_ids: set[str] = set()

    def visit(
        raw_node: Mapping[str, Any], parent_id: Optional[str], names: list[str]
    ) -> None:
        name = _as_text(raw_node.get("name"), "未命名分类")
        raw_id = _as_text(raw_node.get("id"))
        node_id = raw_id or _stable_id("node", *names, name)
        if node_id in used_ids:
            raise SiteDataError(f"嵌套 taxonomy 中存在重复节点 ID：{node_id}")
        used_ids.add(node_id)
        nodes.append(
            {
                "id": node_id,
                "parent_id": parent_id,
                "name": name,
                "locked": bool(raw_node.get("locked", False)),
                "summary": _as_text(raw_node.get("summary")),
                "visibility": _as_text(
                    raw_node.get("visibility"), "private"
                ),
            }
        )
        child_values = raw_node.get("children", [])
        if child_values is None:
            child_values = []
        if not isinstance(child_values, list):
            raise SiteDataError(f"taxonomy 节点 {node_id} 的 children 必须是数组")
        for child in child_values:
            if not isinstance(child, Mapping):
                raise SiteDataError(f"taxonomy 节点 {node_id} 包含无效子节点")
            visit(child, node_id, [*names, name])

    visit(root, None, [])
    return nodes[0]["id"], nodes


def _path_parts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [
            part.strip()
            for part in re.split(r"\s*(?:/|>|→)\s*", value)
            if part.strip()
        ]
    return _as_text_list(value)


def _resolve_node_id(
    raw_document: Mapping[str, Any],
    node_map: Mapping[str, Mapping[str, Any]],
    root_id: str,
) -> str:
    explicit = _as_text(raw_document.get("node_id"))
    if explicit:
        return explicit
    requested_path = _path_parts(raw_document.get("path"))
    if not requested_path:
        return root_id
    matches = []
    for node_id, node in node_map.items():
        path = list(node["path"])
        if path == requested_path or path[1:] == requested_path:
            matches.append(node_id)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SiteDataError(
            f"文档路径不存在于 taxonomy：{' / '.join(requested_path)}"
        )
    raise SiteDataError(f"文档路径不唯一：{' / '.join(requested_path)}")


def _derive_tree(
    nodes: list[dict[str, Any]], root_id: str
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    node_map: dict[str, dict[str, Any]] = {}
    children: dict[str, list[str]] = {}
    for index, raw in enumerate(nodes):
        if not isinstance(raw, Mapping):
            raise SiteDataError(f"nodes[{index}] 必须是对象")
        node_id = _require_identifier(raw.get("id"), f"nodes[{index}].id")
        if node_id in node_map:
            raise SiteDataError(f"重复的节点 ID：{node_id}")
        parent_value = raw.get("parent_id")
        parent_id = None if parent_value in (None, "") else _as_text(parent_value)
        node_map[node_id] = {
            "id": node_id,
            "parent_id": parent_id,
            "name": _as_text(raw.get("name"), node_id),
            "locked": bool(raw.get("locked", False)),
            "summary": _as_text(raw.get("summary")),
            "visibility": (
                _as_text(raw.get("visibility"), "private")
                if _as_text(raw.get("visibility"), "private")
                in VALID_VISIBILITIES
                else "private"
            ),
        }
        if parent_id is not None:
            children.setdefault(parent_id, []).append(node_id)

    if root_id not in node_map:
        raise SiteDataError(f"根节点不存在：{root_id}")
    roots = [item["id"] for item in node_map.values() if item["parent_id"] is None]
    if roots != [root_id]:
        raise SiteDataError(
            f"专业目录必须且只能有一个根节点 {root_id}，当前根节点：{roots}"
        )
    for item in node_map.values():
        parent_id = item["parent_id"]
        if parent_id is not None and parent_id not in node_map:
            raise SiteDataError(
                f"节点 {item['id']} 引用了不存在的父节点 {parent_id}"
            )

    visited: set[str] = set()
    visiting: set[str] = set()

    def walk(node_id: str, path: list[str]) -> None:
        if node_id in visiting:
            raise SiteDataError(f"专业目录存在循环：{' / '.join(path)}")
        if node_id in visited:
            return
        visiting.add(node_id)
        item = node_map[node_id]
        item["level"] = len(path)
        item["path"] = path + [item["name"]]
        for child_id in children.get(node_id, []):
            walk(child_id, item["path"])
        visiting.remove(node_id)
        visited.add(node_id)

    walk(root_id, [])
    if len(visited) != len(node_map):
        unreachable = sorted(set(node_map) - visited)
        raise SiteDataError(f"存在未连接到根节点的分类：{', '.join(unreachable)}")
    return node_map, children


def normalize_site_data(
    data_or_path: DataInput,
    *,
    visibility: str = "private",
) -> dict[str, Any]:
    """Validate, normalize and visibility-filter canonical site data.

    Canonical input contract (schema version 1):

    - ``root``: root taxonomy node ID.
    - ``nodes``: strict tree nodes with ``id``, ``parent_id`` and ``name``.
    - ``documents``: knowledge documents with exactly one ``node_id``.
    - ``relations``: optional auxiliary node-to-node relationships.

    Extra fields are tolerated.  This lets the core evolve without coupling the
    static site to database internals.
    """

    if visibility not in VALID_VISIBILITIES:
        raise ValueError("visibility 必须是 private 或 public")
    raw = _load_data(data_or_path)
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise SiteDataError(
            f"不支持的 schema_version：{raw.get('schema_version')!r}，"
            f"当前只支持 {SCHEMA_VERSION}"
        )

    raw_nodes = raw.get("nodes")
    if raw_nodes is None and isinstance(raw.get("taxonomy"), Mapping):
        inferred_root, raw_nodes = _flatten_taxonomy(raw["taxonomy"])
    else:
        inferred_root = ""
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise SiteDataError("必须提供非空 nodes 数组或嵌套 taxonomy")
    root_id = _require_identifier(
        raw.get("root") or raw.get("root_id") or inferred_root, "root"
    )
    node_map, children = _derive_tree(raw_nodes, root_id)

    raw_documents = raw.get("documents", [])
    if not isinstance(raw_documents, list):
        raise SiteDataError("documents 必须是数组")
    documents: list[dict[str, Any]] = []
    document_ids: set[str] = set()
    embedded_relations: list[dict[str, Any]] = []
    for index, raw_document in enumerate(raw_documents):
        if not isinstance(raw_document, Mapping):
            raise SiteDataError(f"documents[{index}] 必须是对象")
        node_id = _resolve_node_id(raw_document, node_map, root_id)
        doc_id = _as_text(raw_document.get("id")) or _stable_id(
            "document",
            node_id,
            _as_text(raw_document.get("title"), "未命名知识"),
            _as_text(raw_document.get("source_id")),
        )
        if doc_id in document_ids:
            raise SiteDataError(f"重复的文档 ID：{doc_id}")
        document_ids.add(doc_id)
        if node_id not in node_map:
            raise SiteDataError(f"文档 {doc_id} 引用了不存在的节点 {node_id}")
        doc_visibility = _as_text(
            raw_document.get("visibility"), "private"
        ).casefold()
        if doc_visibility not in VALID_VISIBILITIES:
            raise SiteDataError(
                f"文档 {doc_id} 的 visibility 必须是 private 或 public"
            )
        if visibility == "public" and doc_visibility != "public":
            continue

        content = _as_text(
            raw_document.get("content")
            or raw_document.get("body")
            or raw_document.get("body_markdown")
        )
        if content and not bool(raw_document.get("publish_content", True)):
            content = ""
        document = {
            "id": doc_id,
            "source_id": _as_text(raw_document.get("source_id")),
            "title": _as_text(raw_document.get("title"), "未命名知识"),
            "summary": _as_text(raw_document.get("summary")),
            "content": content,
            "key_points": _as_text_list(raw_document.get("key_points")),
            "tags": _as_text_list(raw_document.get("tags")),
            "node_id": node_id,
            "path": list(node_map[node_id]["path"]),
            "visibility": doc_visibility,
            "status": _as_text(raw_document.get("status"), "unverified"),
            "source": _normalize_source(raw_document.get("source"), visibility),
            "evidence": _normalize_evidence(raw_document.get("evidence")),
            "updated_at": _as_text(raw_document.get("updated_at")),
        }
        if visibility == "private":
            document["review"] = normalize_review(raw_document.get("review"), _as_text)
        section_index = _as_optional_integer(
            raw_document.get("section_index"), minimum=0
        )
        source_line_start = _as_optional_integer(
            raw_document.get("source_line_start"), minimum=1
        )
        source_line_end = _as_optional_integer(
            raw_document.get("source_line_end"), minimum=1
        )
        if section_index is not None:
            document["section_index"] = section_index
        heading_path = _as_text_list(raw_document.get("heading_path"))
        if heading_path:
            document["heading_path"] = heading_path
        if source_line_start is not None:
            document["source_line_start"] = source_line_start
        if source_line_end is not None and (
            source_line_start is None or source_line_end >= source_line_start
        ):
            document["source_line_end"] = source_line_end
        body_sha256 = _as_text(raw_document.get("body_sha256")).lower()
        if re.fullmatch(r"[0-9a-f]{64}", body_sha256):
            document["body_sha256"] = body_sha256
        splitter_version = _as_text(raw_document.get("splitter_version"))
        if splitter_version:
            document["splitter_version"] = splitter_version
        documents.append(document)
        document_relations = raw_document.get("relations", [])
        if isinstance(document_relations, list):
            for relation_index, relation in enumerate(document_relations):
                if not isinstance(relation, Mapping):
                    continue
                embedded = dict(relation)
                embedded.setdefault("from_node_id", node_id)
                embedded.setdefault("document_id", doc_id)
                embedded.setdefault("visibility", doc_visibility)
                embedded.setdefault(
                    "id", f"{doc_id}-relation-{relation_index + 1}"
                )
                embedded_relations.append(embedded)

    raw_relations = raw.get("relations", [])
    if not isinstance(raw_relations, list):
        raise SiteDataError("relations 必须是数组")
    raw_relations = [*raw_relations, *embedded_relations]
    allowed_document_ids = {item["id"] for item in documents}
    relations: list[dict[str, Any]] = []
    relation_ids: set[str] = set()
    for index, raw_relation in enumerate(raw_relations):
        if not isinstance(raw_relation, Mapping):
            raise SiteDataError(f"relations[{index}] 必须是对象")
        from_node_id = _require_identifier(
            raw_relation.get("from_node_id") or raw_relation.get("from_id"),
            f"relations[{index}].from_node_id",
        )
        to_node_id = _require_identifier(
            raw_relation.get("to_node_id") or raw_relation.get("to_id"),
            f"relations[{index}].to_node_id",
        )
        if from_node_id not in node_map or to_node_id not in node_map:
            raise SiteDataError(
                f"关系引用了不存在的节点：{from_node_id} → {to_node_id}"
            )
        relation_id = _as_text(
            raw_relation.get("id"), f"relation-{index + 1}"
        )
        if relation_id in relation_ids:
            raise SiteDataError(f"重复的关系 ID：{relation_id}")
        relation_ids.add(relation_id)
        document_id = _as_text(raw_relation.get("document_id"))
        relation_visibility = _as_text(
            raw_relation.get("visibility"), "private"
        ).casefold()
        if relation_visibility not in VALID_VISIBILITIES:
            relation_visibility = "private"
        if visibility == "public":
            # A public document is sufficient provenance for a derived
            # relation.  A document-less relation must itself be explicitly
            # public.
            if document_id:
                if document_id not in allowed_document_ids:
                    continue
            elif relation_visibility != "public":
                continue
        elif document_id and document_id not in document_ids:
            raise SiteDataError(
                f"关系 {relation_id} 引用了不存在的文档 {document_id}"
            )
        try:
            confidence = float(raw_relation.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        relations.append(
            {
                "id": relation_id,
                "from_node_id": from_node_id,
                "to_node_id": to_node_id,
                "type": _as_text(raw_relation.get("type"), "related_to"),
                "label": _as_text(raw_relation.get("label"), "相关"),
                "document_id": document_id,
                "confidence": max(0.0, min(1.0, confidence)),
                "visibility": relation_visibility,
                "evidence_ids": _as_text_list(raw_relation.get("evidence_ids")),
            }
        )

    if visibility == "public":
        required_nodes = {root_id}
        for document in documents:
            current_id: Optional[str] = document["node_id"]
            while current_id is not None and current_id not in required_nodes:
                required_nodes.add(current_id)
                current_id = node_map[current_id]["parent_id"]
        # Only relations whose complete path already belongs to public content
        # are retained.  They must never reveal an otherwise private category.
        relations = [
            relation
            for relation in relations
            if relation["from_node_id"] in required_nodes
            and relation["to_node_id"] in required_nodes
        ]
        node_map = {
            node_id: item
            for node_id, item in node_map.items()
            if node_id in required_nodes
        }
        children = {
            parent_id: [child for child in child_ids if child in required_nodes]
            for parent_id, child_ids in children.items()
            if parent_id in required_nodes
        }

    direct_counts = {node_id: 0 for node_id in node_map}
    for document in documents:
        direct_counts[document["node_id"]] += 1

    def descendant_count(node_id: str) -> int:
        return direct_counts[node_id] + sum(
            descendant_count(child_id) for child_id in children.get(node_id, [])
        )

    nodes: list[dict[str, Any]] = []
    for node_id, item in node_map.items():
        normalized_node = dict(item)
        normalized_node["children"] = list(children.get(node_id, []))
        normalized_node["direct_document_count"] = direct_counts[node_id]
        normalized_node["document_count"] = descendant_count(node_id)
        nodes.append(normalized_node)

    site = raw.get("site") if isinstance(raw.get("site"), Mapping) else {}
    declared_visibility = _as_text(raw.get("visibility"))
    result = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _as_text(raw.get("generated_at")),
        "site": {
            "title": _as_text(site.get("title"), "我的知识体系"),
            "description": _as_text(
                site.get("description"), "按专业母子链路组织的个人知识网络"
            ),
            "language": _as_text(site.get("language"), "zh-CN"),
            "visibility": visibility or declared_visibility,
        },
        "root": root_id,
        "nodes": nodes,
        "documents": documents,
        "relations": relations,
        "stats": {
            "node_count": len(nodes),
            "document_count": len(documents),
            "relation_count": len(relations),
            **(
                dict(raw.get("stats", {}))
                if isinstance(raw.get("stats"), Mapping)
                else {}
            ),
        },
    }
    # These are authoritative after filtering, so overwrite any supplied stats.
    result["stats"].update(
        {
            "node_count": len(nodes),
            "document_count": len(documents),
            "relation_count": len(relations),
        }
    )
    return result
