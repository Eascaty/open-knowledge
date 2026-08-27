"""Health-report orchestration with no network access."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from ..config import ProjectPaths
from .checks import (
    CheckResult,
    CheckStatus,
    check_broken_links,
    check_database,
    check_disk,
    check_json_document,
    check_privacy,
    offline_network_check,
)


PathLike = Union[str, os.PathLike]
REQUIRED_TABLES = ("documents", "jobs", "nodes", "sources")


@dataclass(frozen=True)
class HealthReport:
    project_root: Path
    generated_at: str
    status: CheckStatus
    checks: Tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        return self.status != CheckStatus.FAIL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "project_root": str(self.project_root),
            "generated_at": self.generated_at,
            "status": self.status.value,
            "checks": [check.to_dict() for check in self.checks],
        }


def _overall(checks: Sequence[CheckResult]) -> CheckStatus:
    if any(check.status == CheckStatus.FAIL for check in checks):
        return CheckStatus.FAIL
    if any(check.status == CheckStatus.WARN for check in checks):
        return CheckStatus.WARN
    return CheckStatus.PASS


def _first_existing(candidates: Sequence[Path]) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def run_health_checks(
    project_root: PathLike,
    *,
    canonical_path: Optional[PathLike] = None,
    candidate_roots: Optional[Sequence[PathLike]] = None,
    include_vault_links: bool = True,
    minimum_free_bytes: int = 2 * 1024**3,
    warning_free_bytes: int = 10 * 1024**3,
) -> HealthReport:
    """Run deterministic local checks and return one structured report."""

    root = Path(project_root).expanduser().resolve()
    paths = ProjectPaths.from_root(root)
    database = paths.database_file
    canonical = (
        Path(canonical_path).expanduser().resolve()
        if canonical_path is not None
        else _first_existing(
            (
                paths.site_data_dir / "site-data.json",
                paths.site_data_dir / "knowledge.json",
            )
        )
    )
    site_dist = paths.site_dir / "dist"
    public_export = root / "exports" / "public"
    candidates = (
        tuple(Path(item).expanduser().resolve() for item in candidate_roots)
        if candidate_roots is not None
        else None
    )
    if candidates is not None and not candidates:
        raise ValueError("health checks require at least one candidate root")
    privacy_roots = candidates or (site_dist, public_export)

    checks: List[CheckResult] = [
        check_disk(root, minimum_free_bytes, warning_free_bytes),
        check_database(database, required_tables=REQUIRED_TABLES),
        check_json_document(canonical, name="canonical-data"),
        check_privacy(
            privacy_roots,
            project_root=root,
            max_public_file_bytes=25 * 1024**2,
        ),
    ]
    link_roots: List[Path] = list(candidates or (site_dist,))
    if include_vault_links:
        link_roots.append(paths.vault_dir)
    checks.extend(
        (
            check_broken_links(link_roots, project_root=root),
            offline_network_check(),
        )
    )
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return HealthReport(
        project_root=root,
        generated_at=generated_at,
        status=_overall(checks),
        checks=tuple(checks),
    )


def render_health_markdown(report: HealthReport) -> str:
    lines = [
        "# Knowledge OS 健康报告",
        "",
        "- 生成时间：`{}`".format(report.generated_at),
        "- 总体状态：**{}**".format(report.status.value),
        "- 网络请求：`0`（默认离线检查）",
        "",
        "| 检查 | 状态 | 结果 | 耗时 |",
        "|---|---:|---|---:|",
    ]
    for check in report.checks:
        summary = check.summary.replace("|", "\\|").replace("\n", " ")
        lines.append(
            "| `{}` | **{}** | {} | {} ms |".format(
                check.name, check.status.value, summary, check.duration_ms
            )
        )
    for check in report.checks:
        if not check.details:
            continue
        lines.extend(("", "## {}".format(check.name), ""))
        lines.extend("- {}".format(detail) for detail in check.details)
    lines.extend(
        (
            "",
            "## 结构化结果",
            "",
            "```json",
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            "```",
            "",
        )
    )
    return "\n".join(lines)


def write_health_report(
    report: HealthReport, destination: Optional[PathLike] = None
) -> Path:
    target = (
        Path(destination).expanduser().resolve()
        if destination is not None
        else ProjectPaths.from_root(report.project_root).private_exports_dir
        / "health.md"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".health-", suffix=".md.tmp", dir=str(target.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(render_health_markdown(report))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(target))
    finally:
        if temporary.exists():
            temporary.unlink()
    return target
