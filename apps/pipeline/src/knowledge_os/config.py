"""Project layout and JSON configuration.

The runtime deliberately treats ``config/taxonomy.json`` as the single
authoritative taxonomy.  YAML may be used for human design notes, but it is
never silently parsed by the pipeline.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Union


class ConfigError(ValueError):
    """Raised when project configuration violates an invariant."""


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    workspace_dir: Path
    config_dir: Path
    taxonomy_file: Path
    runtime_file: Path
    inbox_dir: Path
    data_dir: Path
    raw_dir: Path
    normalized_dir: Path
    state_dir: Path
    database_file: Path
    quarantine_dir: Path
    runtime_logs_dir: Path
    vault_dir: Path
    site_dir: Path
    site_data_dir: Path
    exports_dir: Path
    private_exports_dir: Path

    @classmethod
    def from_root(
        cls, root: Union[os.PathLike[str], str]
    ) -> "ProjectPaths":
        resolved = Path(root).expanduser().resolve()
        workspace = resolved / "workspace"
        data = workspace / "data"
        site = workspace / "site"
        config = resolved / "config"
        return cls(
            root=resolved,
            workspace_dir=workspace,
            config_dir=config,
            taxonomy_file=config / "taxonomy.json",
            runtime_file=config / "runtime.json",
            inbox_dir=workspace / "inbox",
            data_dir=data,
            raw_dir=data / "raw",
            normalized_dir=data / "normalized",
            state_dir=data / "state",
            database_file=data / "state" / "knowledge.sqlite3",
            quarantine_dir=data / "quarantine",
            runtime_logs_dir=data / "logs",
            vault_dir=workspace / "vault",
            site_dir=site,
            site_data_dir=site / "data",
            exports_dir=resolved / "exports",
            private_exports_dir=workspace / "exports" / "private",
        )

    def directories(self) -> Iterable[Path]:
        return (
            self.root,
            self.workspace_dir,
            self.config_dir,
            self.inbox_dir,
            self.inbox_dir / "files",
            self.data_dir,
            self.raw_dir,
            self.normalized_dir,
            self.state_dir,
            self.quarantine_dir,
            self.runtime_logs_dir,
            self.vault_dir,
            self.site_dir,
            self.site_data_dir,
            self.exports_dir,
            self.exports_dir / "public",
            self.private_exports_dir,
        )


DEFAULT_RUNTIME: Dict[str, Any] = {
    "version": 1,
    "model": {
        "provider": "rules",
        "ollama": {
            "base_url": "http://127.0.0.1:11434",
            "model": "qwen3:8b",
            "timeout_seconds": 120,
        },
    },
    "pipeline": {
        "max_attempts": 3,
        "retry_base_seconds": 30,
        "max_file_mb": 512,
        "stale_job_minutes": 30,
        "split_markdown_headings": True,
        "split_min_chars": 1200,
        "split_min_sections": 2,
        "split_min_section_chars": 120,
        "split_max_sections": 24,
    },
    "site": {
        "title": "我的知识体系",
        "visibility": "private",
    },
    "publish": {
        "enabled": False,
        "provider": "cloudflare-pages",
        "project_name": "",
        "debounce_minutes": 15,
        "maximum_deployments_per_day": 10,
        "deploy_only_when_changed": True,
        "require_checks": True,
    },
}


def atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically without exposing a partially-written artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
    )


def _packaged_default_taxonomy() -> Dict[str, Any]:
    raw = (
        resources.files("knowledge_os")
        .joinpath("default_taxonomy.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(raw)


def _validate_node(
    node: Mapping[str, Any],
    *,
    level: int,
    seen: Set[str],
    names_by_parent: Optional[Set[str]] = None,
) -> None:
    node_id = node.get("id")
    name = node.get("name")
    if not isinstance(node_id, str) or not node_id.strip():
        raise ConfigError(f"taxonomy level {level}: node id must be a non-empty string")
    if node_id in seen:
        raise ConfigError(f"taxonomy contains duplicate node id: {node_id}")
    seen.add(node_id)
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"taxonomy node {node_id}: name must be a non-empty string")
    if names_by_parent is not None:
        normalized_name = name.strip().casefold()
        if normalized_name in names_by_parent:
            raise ConfigError(
                f"taxonomy node {node_id}: duplicate sibling name {name!r}"
            )
        names_by_parent.add(normalized_name)
    keywords = node.get("keywords", [])
    if not isinstance(keywords, list) or any(
        not isinstance(keyword, str) for keyword in keywords
    ):
        raise ConfigError(f"taxonomy node {node_id}: keywords must be a string list")
    children = node.get("children", [])
    if not isinstance(children, list):
        raise ConfigError(f"taxonomy node {node_id}: children must be a list")
    sibling_names: Set[str] = set()
    for child in children:
        if not isinstance(child, Mapping):
            raise ConfigError(f"taxonomy node {node_id}: child must be an object")
        _validate_node(
            child, level=level + 1, seen=seen, names_by_parent=sibling_names
        )


def validate_taxonomy(taxonomy: Mapping[str, Any]) -> None:
    if not isinstance(taxonomy.get("version"), int):
        raise ConfigError("taxonomy.version must be an integer")
    root = taxonomy.get("root")
    if not isinstance(root, Mapping):
        raise ConfigError("taxonomy.root must be an object")
    _validate_node(root, level=0, seen=set())
    rules = taxonomy.get("rules")
    if not isinstance(rules, Mapping):
        raise ConfigError("taxonomy.rules must be an object")
    uncertain = rules.get("uncertain_destination")
    if not isinstance(uncertain, str):
        raise ConfigError("rules.uncertain_destination must be a node id")
    node_ids = {node["id"] for node in walk_nodes(root)}
    if uncertain not in node_ids:
        raise ConfigError(
            f"rules.uncertain_destination references unknown node: {uncertain}"
        )


def walk_nodes(root: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    yield root
    for child in root.get("children", []):
        yield from walk_nodes(child)


def load_json_file(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"missing configuration file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"configuration root must be an object: {path}")
    return value


def load_taxonomy(paths: ProjectPaths) -> Dict[str, Any]:
    taxonomy = load_json_file(paths.taxonomy_file)
    validate_taxonomy(taxonomy)
    return taxonomy


def load_runtime(paths: ProjectPaths) -> Dict[str, Any]:
    runtime = load_json_file(paths.runtime_file)
    if not isinstance(runtime.get("pipeline"), Mapping):
        raise ConfigError("runtime.pipeline must be an object")
    if not isinstance(runtime.get("model"), Mapping):
        raise ConfigError("runtime.model must be an object")
    return runtime


def initialize_layout(paths: ProjectPaths) -> bool:
    """Create missing project state, preserving every existing user file.

    Returns ``True`` if at least one configuration file was created.
    """

    for directory in paths.directories():
        directory.mkdir(parents=True, exist_ok=True)
    changed = False
    if not paths.taxonomy_file.exists():
        taxonomy = _packaged_default_taxonomy()
        validate_taxonomy(taxonomy)
        atomic_write_json(paths.taxonomy_file, taxonomy)
        changed = True
    else:
        load_taxonomy(paths)
    if not paths.runtime_file.exists():
        atomic_write_json(paths.runtime_file, DEFAULT_RUNTIME)
        changed = True
    else:
        load_runtime(paths)
    return changed
