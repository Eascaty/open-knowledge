from __future__ import annotations

import re
import subprocess
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from knowledge_os import __version__, db, knowledge
from knowledge_os.processing import service as processing_service
from knowledge_os.site import builder as site_builder
from knowledge_os.site.build import builder as site_build_impl
from knowledge_os.storage import sqlite as sqlite_storage


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_project_versions_stay_aligned(self):
        root = Path(__file__).resolve().parents[1]
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
        self.assertIsNotNone(match, "pyproject.toml 缺少项目版本")
        python_version = match.group(1)

        pom_root = ElementTree.parse(root / "apps" / "api" / "pom.xml").getroot()
        namespace = {"m": "http://maven.apache.org/POM/4.0.0"}
        java_version = pom_root.findtext("m:version", namespaces=namespace)

        self.assertEqual(__version__, python_version)
        self.assertEqual(java_version, python_version)
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## {python_version} —", changelog)
        java_import = (root / "scripts" / "java-import").read_text(encoding="utf-8")
        self.assertIn("-DskipTests clean package", java_import)
        self.assertIn("personal-knowledge-service-*.jar", java_import)
        self.assertIn("! -name '*.jar.original'", java_import)

    def test_release_gate_accepts_only_current_version_tag(self):
        root = Path(__file__).resolve().parents[1]
        verifier = root / "scripts" / "verify-release"

        accepted = subprocess.run(
            [str(verifier), f"v{__version__}"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(accepted.stdout.strip(), __version__)

        for invalid in (__version__, "v0.0.0"):
            rejected = subprocess.run(
                [str(verifier), invalid],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0, invalid)

    def test_release_workflow_attests_only_verified_main_tags(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/release.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn('tags:\n      - "v[0-9]*.[0-9]*.[0-9]*"', workflow)
        self.assertIn('git merge-base --is-ancestor "${GITHUB_SHA}" origin/main', workflow)
        self.assertIn("./scripts/verify-release", workflow)
        self.assertIn("actions/attest-build-provenance@v3", workflow)
        self.assertIn("-Dproject.build.outputTimestamp", workflow)
        self.assertIn('test "${first_hash}" = "${second_hash}"', workflow)
        self.assertIn("sha256sum", workflow)
        self.assertIn("gh release create", workflow)
        self.assertNotIn("workspace/", workflow)

        ci_workflow = (root / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("-Dproject.build.outputTimestamp", ci_workflow)
        self.assertIn('test "${first_hash}" = "${second_hash}"', ci_workflow)

    def test_legacy_facades_keep_public_entrypoints(self):
        self.assertIs(db.connect, sqlite_storage.connect)
        self.assertIs(knowledge.process_jobs, processing_service.process_jobs)
        self.assertIs(site_builder.build_site, site_build_impl.build_site)

    def test_python_implementation_modules_stay_reviewable(self):
        source_root = (
            Path(__file__).resolve().parents[1]
            / "apps"
            / "pipeline"
            / "src"
            / "knowledge_os"
        )
        oversized = []
        for path in source_root.rglob("*.py"):
            lines = path.read_text(encoding="utf-8").count("\n") + 1
            if lines > 600:
                oversized.append(f"{path.relative_to(source_root)}:{lines}")
        self.assertEqual(oversized, [], "模块超过600行，应按职责继续拆分")

    def test_python_sources_have_stable_line_endings(self):
        source_root = (
            Path(__file__).resolve().parents[1]
            / "apps"
            / "pipeline"
            / "src"
            / "knowledge_os"
        )
        violations = []
        for path in source_root.rglob("*.py"):
            content = path.read_text(encoding="utf-8")
            relative_path = path.relative_to(source_root)
            if not content.endswith("\n") or content.endswith("\n\n"):
                violations.append(f"{relative_path}:文件末尾应恰好保留一个换行")
            for line_number, line in enumerate(content.splitlines(), start=1):
                if line.endswith((" ", "\t")):
                    violations.append(f"{relative_path}:{line_number}:存在行尾空白")
        self.assertEqual(violations, [], "\n".join(violations))

    def test_http_controller_depends_on_application_service(self):
        root = Path(__file__).resolve().parents[1]
        controller = (
            root
            / "apps"
            / "api"
            / "src"
            / "main"
            / "java"
            / "io"
            / "github"
            / "eascaty"
            / "knowledge"
            / "api"
            / "KnowledgeController.java"
        ).read_text(encoding="utf-8")
        self.assertIn("KnowledgeQueryService", controller)
        self.assertNotIn("KnowledgeQueryRepository", controller)

    def test_container_boundary_excludes_private_workspace(self):
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "apps" / "api" / "Dockerfile").read_text(
            encoding="utf-8"
        )
        dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")
        compose = (root / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("eclipse-temurin:21.0.11_10-jdk-alpine-3.23", dockerfile)
        self.assertIn("eclipse-temurin:21.0.11_10-jre-alpine-3.23", dockerfile)
        self.assertIn("! -name '*.jar.original'", dockerfile)
        self.assertNotIn("COPY workspace", dockerfile)
        dockerignore_lines = dockerignore.splitlines()
        self.assertEqual(dockerignore_lines[0], "**")
        self.assertIn("!apps/api/pom.xml", dockerignore_lines)
        self.assertIn("!apps/api/src/**", dockerignore_lines)
        self.assertNotIn("!workspace", dockerignore_lines)
        self.assertIn("read_only: true", compose)
        self.assertIn("no-new-privileges:true", compose)
        self.assertIn("internal: true", compose)
        self.assertIn("pids_limit: 256", compose)
        self.assertIn("-Dorg.sqlite.tmpdir=/sqlite-native", dockerfile)
        self.assertIn("/sqlite-native:size=16m,uid=10001,gid=10001", compose)
        self.assertIn("mem_limit:", compose)
        self.assertIn("cpus:", compose)
        self.assertIn("KNOWLEDGE_DB_FILE:?", compose)


if __name__ == "__main__":
    unittest.main()
