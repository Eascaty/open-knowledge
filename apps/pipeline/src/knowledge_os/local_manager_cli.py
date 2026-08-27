"""Command-line controls for the project-local knowledge manager."""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .config import ProjectPaths, initialize_layout
from .local_manager import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    SERVICE_NAME,
    STOP_PATH,
    LocalKnowledgeManager,
    ManagerError,
    _log_path,
    _port_is_open,
    _probe_state,
    _public_state,
    _read_state,
    _request_json,
    _state_path,
    _utc_now,
    _write_state,
)


def _prepare_start_state(paths: ProjectPaths, instance_id: str, port: int) -> None:
    previous = _read_state(_state_path(paths))
    state = {
        "service": SERVICE_NAME,
        "instance_id": instance_id,
        "control_token": secrets.token_urlsafe(32),
        "status": "starting",
        "host": DEFAULT_HOST,
        "port": port,
        "url": "http://{}:{}/".format(DEFAULT_HOST, port),
        "started_at": _utc_now(),
        "last_success_at": previous.get("last_success_at"),
        "last_result": previous.get("last_result"),
        "successful_inbox_fingerprint": previous.get(
            "successful_inbox_fingerprint"
        ),
        "recent_uploads": previous.get("recent_uploads", []),
    }
    _write_state(_state_path(paths), state)


def _print_status(status: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(dict(status), ensure_ascii=False, indent=2))
        return
    print("本地知识管家：{}".format(status.get("status", "unknown")))
    if status.get("url"):
        print("知识网站：{}".format(status["url"]))
    if status.get("last_success_at"):
        print("最近成功：{}".format(status["last_success_at"]))
    last_result = status.get("last_result")
    if isinstance(last_result, Mapping):
        print(
            "最近处理：新增 {ingested}，重复 {duplicates}，知识 {documents}".format(
                ingested=last_result.get("ingested", 0),
                duplicates=last_result.get("duplicates", 0),
                documents=last_result.get("documents", 0),
            )
        )
    if status.get("last_error"):
        print("最近错误：{}".format(status["last_error"]))


def _command_start(arguments: argparse.Namespace) -> int:
    paths = ProjectPaths.from_root(arguments.root)
    initialize_layout(paths)
    live = _probe_state(_read_state(_state_path(paths)))
    if live is not None:
        _print_status(live, as_json=arguments.json)
        if not arguments.no_open:
            webbrowser.open(str(live["url"]))
        return 0
    if _port_is_open(DEFAULT_HOST, arguments.port):
        raise ManagerError(
            "127.0.0.1:{} 已被其他程序占用；未尝试终止它".format(arguments.port)
        )

    instance_id = secrets.token_hex(16)
    _prepare_start_state(paths, instance_id, arguments.port)
    log_path = _log_path(paths)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-B",
        "-m",
        "knowledge_os.local_manager_cli",
        "--root",
        str(paths.root),
        "_serve",
        "--instance-id",
        instance_id,
        "--port",
        str(arguments.port),
        "--poll-seconds",
        str(arguments.poll_seconds),
        "--settle-seconds",
        str(arguments.settle_seconds),
        "--retry-seconds",
        str(arguments.retry_seconds),
    ]
    with log_path.open("ab", buffering=0) as log_handle:
        try:
            log_path.chmod(0o600)
        except OSError:
            pass
        process = subprocess.Popen(
            command,
            cwd=str(paths.root),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    deadline = time.monotonic() + arguments.wait_seconds
    live = None
    while time.monotonic() < deadline:
        state = _read_state(_state_path(paths))
        if state.get("instance_id") == instance_id:
            live = _probe_state(state)
            if live is not None:
                break
        if process.poll() is not None:
            break
        time.sleep(0.1)
    if live is None:
        raise ManagerError("本地知识管家未能启动；请查看 {}".format(log_path))
    _print_status(live, as_json=arguments.json)
    if not arguments.no_open:
        webbrowser.open(str(live["url"]))
    return 0


def _command_status(arguments: argparse.Namespace) -> int:
    paths = ProjectPaths.from_root(arguments.root)
    state = _read_state(_state_path(paths))
    live = _probe_state(state)
    if live is None:
        stopped = _public_state(state)
        stopped["status"] = "stopped"
        stopped["ok"] = False
        _print_status(stopped, as_json=arguments.json)
        return 1
    _print_status(live, as_json=arguments.json)
    return 0


def _command_stop(arguments: argparse.Namespace) -> int:
    paths = ProjectPaths.from_root(arguments.root)
    path = _state_path(paths)
    state = _read_state(path)
    if _probe_state(state) is None:
        if state:
            state.update({"status": "stopped", "stopped_at": _utc_now()})
            _write_state(path, state)
        print("本地知识管家没有运行。")
        return 0
    token = state.get("control_token")
    port = state.get("port")
    if not isinstance(token, str) or not isinstance(port, int):
        raise ManagerError("控制状态不完整，拒绝停止未知进程")
    response = _request_json(
        DEFAULT_HOST, port, STOP_PATH, method="POST", token=token
    )
    if response is None:
        raise ManagerError("本机控制接口拒绝停止请求")
    deadline = time.monotonic() + arguments.wait_seconds
    while time.monotonic() < deadline:
        if _probe_state(state) is None:
            print("本地知识管家已停止。")
            return 0
        time.sleep(0.1)
    print("停止请求已接受；当前流水线完成后进程会退出。")
    return 0


def _command_serve(arguments: argparse.Namespace) -> int:
    paths = ProjectPaths.from_root(arguments.root)
    state = _read_state(_state_path(paths))
    if state.get("instance_id") != arguments.instance_id:
        raise ManagerError("启动实例与本地状态不匹配")
    token = state.get("control_token")
    if not isinstance(token, str) or len(token) < 32:
        raise ManagerError("本地控制令牌无效")
    LocalKnowledgeManager(
        paths.root,
        instance_id=arguments.instance_id,
        control_token=token,
        port=arguments.port,
        poll_seconds=arguments.poll_seconds,
        settle_seconds=arguments.settle_seconds,
        retry_seconds=arguments.retry_seconds,
    ).serve()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge-manager",
        description="自动监听本地收件箱、运行知识流水线并保持网站可访问",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start", help="启动或复用本地知识管家")
    start.add_argument("--port", type=int, default=DEFAULT_PORT)
    start.add_argument("--poll-seconds", type=float, default=2.0)
    start.add_argument("--settle-seconds", type=float, default=3.0)
    start.add_argument("--retry-seconds", type=float, default=30.0)
    start.add_argument("--wait-seconds", type=float, default=15.0)
    start.add_argument("--no-open", action="store_true")
    start.add_argument("--json", action="store_true")
    start.set_defaults(handler=_command_start)

    status = commands.add_parser("status", help="查看后台状态和最近处理结果")
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=_command_status)

    stop = commands.add_parser("stop", help="安全停止匹配的后台实例")
    stop.add_argument("--wait-seconds", type=float, default=10.0)
    stop.set_defaults(handler=_command_stop)

    serve = commands.add_parser("_serve", help="内部后台进程（无需手工调用）")
    serve.add_argument("--instance-id", required=True)
    serve.add_argument("--port", type=int, required=True)
    serve.add_argument("--poll-seconds", type=float, required=True)
    serve.add_argument("--settle-seconds", type=float, required=True)
    serve.add_argument("--retry-seconds", type=float, required=True)
    serve.set_defaults(handler=_command_serve)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        return int(arguments.handler(arguments))
    except (ManagerError, OSError, ValueError) as exc:
        print("knowledge-manager: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
