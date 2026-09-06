"""Command-line interface for the local knowledge pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import __version__, db
from .ai import adapter_from_runtime
from .config import (
    ConfigError,
    ProjectPaths,
    initialize_layout,
    load_runtime,
    load_taxonomy,
)
from .ingest import IngestError, discover_files, ingest_file, ingest_text
from .importers.chat_exports import (
    ChatExportError,
    materialize_conversations,
    parse_export,
)
from .knowledge import build_site_data, process_jobs
from .operations.lock import LockUnavailable, ProjectLock


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False))


def _open_project(root: Path) -> Any:
    paths = ProjectPaths.from_root(root)
    initialize_layout(paths)
    connection = db.connect(paths.database_file)
    db.initialize_database(connection)
    taxonomy = load_taxonomy(paths)
    db.sync_taxonomy(connection, taxonomy)
    runtime = load_runtime(paths)
    return paths, connection, taxonomy, runtime


def _command_init(arguments: argparse.Namespace) -> int:
    paths = ProjectPaths.from_root(arguments.root)
    changed = initialize_layout(paths)
    connection = db.connect(paths.database_file)
    try:
        db.initialize_database(connection)
        taxonomy = load_taxonomy(paths)
        db.sync_taxonomy(connection, taxonomy)
        _emit(
            {
                "ok": True,
                "project_root": str(paths.root),
                "created_configuration": changed,
                "database": str(paths.database_file),
                "taxonomy": str(paths.taxonomy_file),
            }
        )
    finally:
        connection.close()
    return 0


def _command_ingest(arguments: argparse.Namespace) -> int:
    paths, connection, _taxonomy, runtime = _open_project(arguments.root)
    try:
        results = []
        if arguments.text is not None:
            result = ingest_text(
                connection,
                paths,
                arguments.text,
                runtime,
                title=arguments.title or "手工笔记",
            )
            results.append(result)
        input_values = list(arguments.inputs)
        if not input_values and arguments.text is None:
            input_values = [str(paths.inbox_dir)]
        if input_values:
            discovered = list(
                discover_files(
                    [Path(value) for value in input_values],
                    paths,
                    recursive=not arguments.no_recursive,
                )
            )
            # The checked-in inbox README is operational documentation, not a
            # user source. Explicitly naming it still imports it.
            if input_values == [str(paths.inbox_dir)]:
                discovered = [
                    path
                    for path in discovered
                    if not (
                        path.parent == paths.inbox_dir
                        and path.name.casefold() == "readme.md"
                    )
                ]
            for path in discovered:
                results.append(ingest_file(connection, paths, path, runtime))
        payload = {
            "ok": True,
            "ingested": sum(1 for result in results if not result.duplicate),
            "duplicates": sum(1 for result in results if result.duplicate),
            "sources": [
                {
                    "source_id": result.source_id,
                    "sha256": result.sha256,
                    "original_name": result.original_name,
                    "raw_path": result.raw_path,
                    "duplicate": result.duplicate,
                }
                for result in results
            ],
        }
        _emit(payload)
        return 0
    finally:
        connection.close()


def _command_import_chat_export(arguments: argparse.Namespace) -> int:
    paths, connection, _taxonomy, runtime = _open_project(arguments.root)
    try:
        conversations = parse_export(Path(arguments.input), arguments.provider)
        output = (
            paths.inbox_dir / "files" / "chat-exports"
            if not arguments.output
            else Path(arguments.output).expanduser().resolve()
        )
        workspace = paths.workspace_dir.resolve()
        if output != workspace and workspace not in output.parents:
            raise ChatExportError("导出结果目录必须位于项目 workspace 内")
        materialized = materialize_conversations(
            conversations, output, dry_run=arguments.dry_run
        )
        ingested = []
        if not arguments.dry_run:
            for item in materialized:
                result = ingest_file(connection, paths, item.path, runtime)
                ingested.append(
                    {
                        "source_id": result.source_id,
                        "sha256": result.sha256,
                        "duplicate": result.duplicate,
                    }
                )
        _emit(
            {
                "ok": True,
                "provider": arguments.provider,
                "conversations": len(conversations),
                "files": [
                    {
                        "path": str(item.path),
                        "provider": item.provider,
                        "conversation_id": item.conversation_id,
                        "sha256": item.sha256,
                        "duplicate": item.duplicate,
                    }
                    for item in materialized
                ],
                "ingested": ingested,
                "dry_run": bool(arguments.dry_run),
                "next": "知识管家会继续处理 workspace/inbox/files/chat-exports 中的 Markdown。",
            }
        )
        return 0
    finally:
        connection.close()


def _command_run(arguments: argparse.Namespace) -> int:
    paths, connection, taxonomy, runtime = _open_project(arguments.root)
    try:
        adapter = adapter_from_runtime(runtime)
        summary = process_jobs(
            connection,
            paths,
            runtime,
            taxonomy,
            adapter,
            max_jobs=arguments.max_jobs,
        )
        status = db.status_summary(connection)
        failed = int(status["job_status"].get("failed", 0))
        pending = int(status["jobs_pending"])
        queue_ok = failed == 0 and pending == 0
        site_data = None
        if not arguments.no_build_data:
            site_data = build_site_data(
                connection,
                paths,
                taxonomy,
                runtime,
                visibility=arguments.visibility,
            )
        _emit(
            {
                "ok": queue_ok,
                "jobs": {
                    "claimed": summary.claimed,
                    "completed": summary.completed,
                    "retried": summary.retried,
                    "failed": summary.failed,
                    "pending": pending,
                },
                "site_data": (
                    str(paths.site_data_dir / "site-data.json")
                    if site_data is not None
                    else None
                ),
                "status": status,
            }
        )
        return 0 if queue_ok else 2
    finally:
        connection.close()


def _command_query(arguments: argparse.Namespace) -> int:
    _paths, connection, _taxonomy, _runtime = _open_project(arguments.root)
    try:
        results = db.query_documents(connection, arguments.query, arguments.limit)
        if arguments.json:
            _emit({"query": arguments.query, "count": len(results), "results": results})
        elif not results:
            print("没有找到匹配的知识。")
        else:
            for result in results:
                print(f"- {result['title']}")
                print(f"  路径：{' / '.join(result['path'][1:])}")
                print(f"  摘要：{result['summary'][:240]}")
        return 0
    finally:
        connection.close()


def _command_build_data(arguments: argparse.Namespace) -> int:
    paths, connection, taxonomy, runtime = _open_project(arguments.root)
    try:
        output = Path(arguments.output).expanduser().resolve() if arguments.output else None
        data = build_site_data(
            connection,
            paths,
            taxonomy,
            runtime,
            visibility=arguments.visibility,
            output=output,
        )
        _emit(
            {
                "ok": True,
                "output": str(output or paths.site_data_dir / "site-data.json"),
                "documents": len(data["documents"]),
                "nodes": len(data["nodes"]),
                "relations": len(data["relations"]),
                "visibility": arguments.visibility,
            }
        )
        return 0
    finally:
        connection.close()


def _command_status(arguments: argparse.Namespace) -> int:
    paths, connection, _taxonomy, runtime = _open_project(arguments.root)
    try:
        status = db.status_summary(connection)
        status["project_root"] = str(paths.root)
        status["database"] = str(paths.database_file)
        status["model_provider"] = runtime.get("model", {}).get("provider", "rules")
        if arguments.json:
            _emit(status)
        else:
            print(f"项目：{status['project_root']}")
            print(
                "资料：{sources}；知识文档：{documents}；节点：{nodes}；关系：{relations}".format(
                    **status["counts"]
                )
            )
            print(
                "任务："
                + "，".join(
                    f"{name}={count}" for name, count in status["job_status"].items()
                )
            )
        failed = int(status["job_status"].get("failed", 0))
        pending = int(status["jobs_pending"])
        return 0 if failed == 0 and pending == 0 else 2
    finally:
        connection.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge-os",
        description="本地优先、零付费 API 的个人知识流水线",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="项目根目录（默认当前目录）",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    init_parser = commands.add_parser("init", help="初始化项目目录、JSON 配置和数据库")
    init_parser.set_defaults(handler=_command_init)

    ingest_parser = commands.add_parser("ingest", help="导入文件、目录或一段文本")
    ingest_parser.add_argument(
        "inputs", nargs="*", help="文件或目录；省略时扫描 workspace/inbox/"
    )
    ingest_parser.add_argument("--text", help="直接导入一段 UTF-8 文本")
    ingest_parser.add_argument("--title", help="--text 的标题")
    ingest_parser.add_argument(
        "--no-recursive", action="store_true", help="目录仅扫描第一层"
    )
    ingest_parser.set_defaults(handler=_command_ingest)

    export_parser = commands.add_parser(
        "import-chat-export", help="离线导入 ChatGPT/Gemini 导出文件"
    )
    export_parser.add_argument("input", help="JSON、HTML、ZIP 或已解压的导出目录")
    export_parser.add_argument(
        "--provider", choices=("auto", "chatgpt", "gemini"), default="auto"
    )
    export_parser.add_argument(
        "--output", help="输出目录，必须位于 workspace 内；默认写入 chat-exports"
    )
    export_parser.add_argument("--dry-run", action="store_true", help="只解析，不写入项目")
    export_parser.set_defaults(handler=_command_import_chat_export)

    run_parser = commands.add_parser("run", help="处理任务直到队列为空或达到上限")
    run_parser.add_argument("--max-jobs", type=int, default=100)
    run_parser.add_argument(
        "--visibility", choices=("private", "public"), default="private"
    )
    run_parser.add_argument(
        "--no-build-data", action="store_true", help="处理后不重建网站数据"
    )
    run_parser.set_defaults(handler=_command_run)

    query_parser = commands.add_parser("query", help="搜索本地知识")
    query_parser.add_argument("query")
    query_parser.add_argument("--limit", type=int, default=20)
    query_parser.add_argument("--json", action="store_true")
    query_parser.set_defaults(handler=_command_query)

    build_parser_command = commands.add_parser(
        "build-data", help="生成网站 canonical JSON、搜索索引和关系图"
    )
    build_parser_command.add_argument(
        "--visibility", choices=("private", "public"), default="private"
    )
    build_parser_command.add_argument("--output", help="自定义 canonical JSON 输出路径")
    build_parser_command.set_defaults(handler=_command_build_data)

    status_parser = commands.add_parser("status", help="查看资料、任务与索引状态")
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(handler=_command_status)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        with ProjectLock(
            arguments.root,
            purpose="cli:{}".format(arguments.command),
        ):
            return int(arguments.handler(arguments))
    except (
        ConfigError,
        ChatExportError,
        IngestError,
        LockUnavailable,
        ValueError,
        RuntimeError,
    ) as exc:
        print(f"knowledge-os: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
