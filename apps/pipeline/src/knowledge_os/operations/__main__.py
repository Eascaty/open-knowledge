"""Command-line utilities for health, gate and backup operations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from ..config import ProjectPaths
from ..publish import PublishError, cloudflare_publish_plan
from .classification import (
    ClassificationCorrectionError,
    correct_document_classification,
)
from .checks import check_site_bundle
from .gate import run_prebuild_gate
from .health import run_health_checks, write_health_report
from .lock import LockUnavailable, ProjectLock
from .migration import migrate_project_database
from .restore import RestoreDrillError, run_restore_drill
from .project_restore import restore_project_backup
from .snapshot import SnapshotError, create_sqlite_snapshot
from .project_backup import (
    ProjectBackupError,
    package_project,
    verify_project_backup,
)
from .site_package import SitePackageError, package_site, verify_site_package


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _doctor(arguments: argparse.Namespace) -> int:
    with ProjectLock(arguments.root, purpose="doctor"):
        report = run_health_checks(arguments.root)
        destination = write_health_report(report)
    payload = report.to_dict()
    payload["report"] = str(destination)
    _emit(payload)
    return 0 if report.passed else 1


def _gate(arguments: argparse.Namespace) -> int:
    with ProjectLock(arguments.root, purpose="gate"):
        result = run_prebuild_gate(arguments.root)
    _emit(
        {
            "allowed": result.allowed,
            "summary": result.summary,
            "checks": [check.to_dict() for check in result.checks],
        }
    )
    return 0 if result.allowed else 1


def _publish_plan(arguments: argparse.Namespace) -> int:
    root = arguments.root.expanduser().resolve()
    with ProjectLock(root, purpose="publish-plan"):
        result = cloudflare_publish_plan(
            root,
            project_name=arguments.project_name,
            visibility=arguments.visibility,
        )
    _emit(
        {
            "ok": True,
            "executed": result.executed,
            "ready": result.ready,
            "summary": result.summary,
            "command": list(result.command),
            "gate": {
                "allowed": result.gate.allowed,
                "summary": result.gate.summary,
                "checks": [check.to_dict() for check in result.gate.checks],
            },
            "network": False,
        }
    )
    return 0 if result.ready else 1


def _backup(arguments: argparse.Namespace) -> int:
    root = arguments.root.expanduser().resolve()
    database = ProjectPaths.from_root(root).database_file
    output = (
        arguments.output.expanduser().resolve()
        if arguments.output
        else ProjectPaths.from_root(root).private_exports_dir / "backups"
    )
    with ProjectLock(root, purpose="backup"):
        result = create_sqlite_snapshot(database, output)
    _emit(
        {
            "ok": True,
            "snapshot": str(result.snapshot),
            "sha256": result.sha256,
            "size_bytes": result.size_bytes,
            "created_at": result.created_at,
            "integrity": result.integrity,
        }
    )
    return 0


def _migrate(arguments: argparse.Namespace) -> int:
    root = arguments.root.expanduser().resolve()
    with ProjectLock(root, purpose="schema-migration"):
        result = migrate_project_database(root)
    _emit(
        {
            "ok": True,
            "changed": result.changed,
            "from_version": result.from_version,
            "to_version": result.to_version,
            "requeued_sources": result.requeued_sources,
            "snapshot": (
                str(result.snapshot.snapshot) if result.snapshot is not None else None
            ),
            "snapshot_sha256": (
                result.snapshot.sha256 if result.snapshot is not None else None
            ),
        }
    )
    return 0


def _restore_drill(arguments: argparse.Namespace) -> int:
    result = run_restore_drill(
        arguments.snapshot,
        expected_sha256=arguments.sha256,
    )
    _emit(
        {
            "ok": True,
            "snapshot": str(result.snapshot),
            "sha256": result.sha256,
            "schema_version": result.schema_version,
            "counts": result.counts,
            "integrity": result.integrity,
            "foreign_key_violations": result.foreign_key_violations,
            "live_database_modified": False,
        }
    )
    return 0


def _package_site(arguments: argparse.Namespace) -> int:
    root = arguments.root.expanduser().resolve()
    paths = ProjectPaths.from_root(root)
    source = (
        arguments.source.expanduser().resolve()
        if arguments.source
        else paths.site_dir / "dist"
    )
    output = (
        arguments.output.expanduser().resolve()
        if arguments.output
        else paths.private_exports_dir / "site-packages"
    )
    if not _path_within_workspace(source, root):
        raise SitePackageError("site package source must stay inside the project")
    if not _path_within_workspace(output, paths.workspace_dir):
        raise SitePackageError("site package output must stay inside workspace")
    with ProjectLock(root, purpose="package-site"):
        bundle_check = check_site_bundle(source, expected_visibility=arguments.visibility)
        if not bundle_check.passed:
            raise SitePackageError(
                "site bundle check failed: {}".format(bundle_check.summary)
            )
        result = package_site(source, output, visibility=arguments.visibility)
    _emit(
        {
            "ok": True,
            "source": str(result.source),
            "package": str(result.package),
            "visibility": result.visibility,
            "sha256": result.sha256,
            "file_count": result.file_count,
            "size_bytes": result.size_bytes,
            "created_at": result.created_at,
            "uploaded": False,
        }
    )
    return 0


def _verify_site_package(arguments: argparse.Namespace) -> int:
    result = verify_site_package(arguments.package, expected_sha256=arguments.sha256)
    _emit(
        {
            "ok": True,
            "package": str(result.package),
            "visibility": result.visibility,
            "sha256": result.sha256,
            "file_count": result.file_count,
            "total_bytes": result.total_bytes,
            "integrity": result.integrity,
            "extracted": False,
        }
    )
    return 0


def _backup_bundle(arguments: argparse.Namespace) -> int:
    root = arguments.root.expanduser().resolve()
    paths = ProjectPaths.from_root(root)
    output = (
        arguments.output.expanduser().resolve()
        if arguments.output
        else paths.private_exports_dir / "project-backups"
    )
    if not _path_within_workspace(output, paths.workspace_dir):
        raise ProjectBackupError("backup output must stay inside workspace")
    with ProjectLock(root, purpose="backup-bundle"):
        result = package_project(root, output)
    _emit(
        {
            "ok": True,
            "source": str(result.source),
            "package": str(result.package),
            "sha256": result.sha256,
            "file_count": result.file_count,
            "total_bytes": result.total_bytes,
            "schema_version": result.schema_version,
            "size_bytes": result.size_bytes,
            "created_at": result.created_at,
            "uploaded": False,
        }
    )
    return 0


def _verify_backup_bundle(arguments: argparse.Namespace) -> int:
    result = verify_project_backup(arguments.package, expected_sha256=arguments.sha256)
    _emit(
        {
            "ok": True,
            "package": str(result.package),
            "sha256": result.sha256,
            "file_count": result.file_count,
            "total_bytes": result.total_bytes,
            "schema_version": result.schema_version,
            "integrity": result.integrity,
            "extracted": False,
            "live_database_modified": False,
        }
    )
    return 0


def _restore_backup_bundle(arguments: argparse.Namespace) -> int:
    result = restore_project_backup(arguments.package, arguments.destination, expected_sha256=arguments.sha256)
    _emit({
        "ok": True, "destination": str(result.destination), "sha256": result.sha256,
        "sources": result.sources, "documents": result.documents,
        "schema_version": result.schema_version, "site_rebuilt": True,
        "live_database_modified": False, "uploaded": False,
    })
    return 0


def _path_within_workspace(path: Path, workspace: Path) -> bool:
    try:
        path.relative_to(workspace)
    except ValueError:
        return False
    return True


def _manual_classify(arguments: argparse.Namespace) -> int:
    root = arguments.root.expanduser().resolve()
    with ProjectLock(root, purpose="manual-classification"):
        result = correct_document_classification(
            root,
            arguments.document_id,
            arguments.node_id,
            expected_node_id=arguments.expected_node_id,
            dry_run=arguments.dry_run,
        )
    payload = result.to_dict()
    payload["ok"] = True
    payload["site_rebuild_required"] = result.changed and not result.dry_run
    _emit(payload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m knowledge_os.operations",
        description="Knowledge OS 本地健康检查、发布门禁和一致性备份",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="运行离线健康检查并写入 health.md")
    doctor.set_defaults(handler=_doctor)

    gate = commands.add_parser("gate", help="运行离线发布门禁")
    gate.set_defaults(handler=_gate)

    publish_plan = commands.add_parser(
        "publish-plan",
        help="检查 Cloudflare Pages 发布计划，不联网、不执行上传",
    )
    publish_plan.add_argument("--project-name", required=True)
    publish_plan.add_argument(
        "--visibility", choices=("private", "public"), default="private"
    )
    publish_plan.set_defaults(handler=_publish_plan)

    backup = commands.add_parser("backup", help="生成经过完整性验证的 SQLite 快照")
    backup.add_argument("--output", type=Path)
    backup.set_defaults(handler=_backup)

    migrate = commands.add_parser(
        "migrate", help="先生成一致性快照，再显式迁移数据库 schema"
    )
    migrate.set_defaults(handler=_migrate)

    restore = commands.add_parser(
        "restore-drill",
        help="在临时候选库中恢复并校验快照，不覆盖正式数据库",
    )
    restore.add_argument("snapshot", type=Path)
    restore.add_argument("--sha256")
    restore.set_defaults(handler=_restore_drill)

    package = commands.add_parser(
        "package-site",
        help="把已构建站点打成带清单和 SHA-256 的本地分享包",
    )
    package.add_argument(
        "--visibility", choices=("private", "public"), default="private"
    )
    package.add_argument("--source", type=Path)
    package.add_argument("--output", type=Path)
    package.set_defaults(handler=_package_site)

    verify_package = commands.add_parser(
        "verify-site-package", help="离线验证站点分享包，不解压、不覆盖任何文件"
    )
    verify_package.add_argument("package", type=Path)
    verify_package.add_argument("--sha256")
    verify_package.set_defaults(handler=_verify_site_package)

    backup_bundle = commands.add_parser(
        "backup-bundle",
        help="备份数据库快照、原始资料、Vault 和站点数据到本地私密包",
    )
    backup_bundle.add_argument("--output", type=Path)
    backup_bundle.set_defaults(handler=_backup_bundle)

    verify_backup = commands.add_parser(
        "verify-backup-bundle",
        help="离线核验完整知识备份包，不覆盖正式数据库",
    )
    verify_backup.add_argument("package", type=Path)
    verify_backup.add_argument("--sha256")
    verify_backup.set_defaults(handler=_verify_backup_bundle)

    restore_bundle = commands.add_parser(
        "restore-backup-bundle", help="将完整备份恢复到尚不存在的新目录，离线重建私密网站"
    )
    restore_bundle.add_argument("package", type=Path)
    restore_bundle.add_argument("destination", type=Path)
    restore_bundle.add_argument("--sha256")
    restore_bundle.set_defaults(handler=_restore_backup_bundle)

    classify = commands.add_parser(
        "manual-classify",
        help="在项目锁内原子纠正一张知识卡的主分类",
    )
    classify.add_argument("document_id")
    classify.add_argument("node_id")
    classify.add_argument("--expected-node-id")
    classify.add_argument("--dry-run", action="store_true")
    classify.set_defaults(handler=_manual_classify)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        return int(arguments.handler(arguments))
    except (
        LockUnavailable,
        ClassificationCorrectionError,
        RestoreDrillError,
        SnapshotError,
        SitePackageError,
        ProjectBackupError,
        PublishError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        print("knowledge-os operations: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
