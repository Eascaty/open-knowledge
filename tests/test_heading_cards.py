from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from knowledge_os import db
from knowledge_os.ai import RuleBasedAdapter
from knowledge_os.config import (
    ProjectPaths,
    initialize_layout,
    load_runtime,
    load_taxonomy,
)
from knowledge_os.ingest import ingest_file
from knowledge_os.knowledge import build_site_data, process_jobs
from knowledge_os.processing.heading_cards import split_document


class HeadingCardTests(unittest.TestCase):
    def test_long_markdown_creates_independently_classified_cards(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ProjectPaths.from_root(root)
            initialize_layout(paths)
            runtime = load_runtime(paths)
            runtime["pipeline"].update(
                {"split_min_chars": 80, "split_min_section_chars": 0}
            )
            taxonomy = load_taxonomy(paths)
            source_path = root / "mixed.md"
            source_path.write_text(
                "# 跨领域复盘\n\n前言保留在第一张卡。\n\n"
                "## 信用卡与美股\n\n金融 财经 信用卡 美股消费与风险。\n\n"
                "## Agent 记忆\n\nAI Agent 智能体长期记忆与检索。\n\n"
                "## Java G1\n\nJava JVM 垃圾回收 G1 Mixed GC。\n",
                encoding="utf-8",
            )
            connection = db.connect(paths.database_file)
            try:
                db.initialize_database(connection)
                db.sync_taxonomy(connection, taxonomy)
                result = ingest_file(connection, paths, source_path, runtime)
                raw = paths.root / result.raw_path
                before = raw.read_bytes()
                summary = process_jobs(
                    connection,
                    paths,
                    runtime,
                    taxonomy,
                    RuleBasedAdapter(),
                    max_jobs=20,
                )
                self.assertEqual(summary.failed, 0)
                self.assertEqual(summary.pending, 0)
                self.assertEqual(summary.completed, 3)
                self.assertEqual(raw.read_bytes(), before)
                rows = connection.execute(
                    """
                    SELECT d.id, d.source_id, d.section_index, d.heading_path_json,
                           d.source_line_start, d.source_line_end,
                           d.body_sha256, d.splitter_version, p.node_id
                    FROM documents d
                    JOIN placements p ON p.document_id=d.id
                    ORDER BY d.section_index
                    """
                ).fetchall()
                self.assertEqual(len(rows), 3)
                self.assertEqual([row["section_index"] for row in rows], [0, 1, 2])
                self.assertEqual({row["source_id"] for row in rows}, {result.source_id})
                self.assertEqual(rows[0]["id"], result.source_id)
                self.assertEqual(len({row["id"] for row in rows}), 3)
                self.assertTrue(
                    all(row["splitter_version"] == "markdown-h2-v1" for row in rows)
                )
                self.assertEqual(
                    {row["node_id"] for row in rows},
                    {
                        "finance-economy-credit-card-us-stocks",
                        "ai-agent-intelligent-agent",
                        "technology-programmer-java-jvm-gc-g1",
                    },
                )
                self.assertEqual(
                    json.loads(rows[1]["heading_path_json"])[-1], "Agent 记忆"
                )
                canonical = build_site_data(
                    connection,
                    paths,
                    taxonomy,
                    runtime,
                    visibility="private",
                )
                self.assertEqual(len(canonical["documents"]), 3)
                self.assertTrue(
                    all(
                        document["source_id"] == result.source_id
                        for document in canonical["documents"]
                    )
                )
                self.assertTrue(
                    all(":L" in document["evidence"][0]["locator"] for document in canonical["documents"])
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM documents_fts"
                    ).fetchone()[0],
                    3,
                )
                duplicate = ingest_file(connection, paths, source_path, runtime)
                self.assertTrue(duplicate.duplicate)
                rerun = process_jobs(
                    connection,
                    paths,
                    runtime,
                    taxonomy,
                    RuleBasedAdapter(),
                    max_jobs=20,
                )
                self.assertEqual(rerun.claimed, 0)
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                    3,
                )

                # A deliberate reprocess with a tighter card cap reconciles
                # all generated artifacts without leaving stale copies.
                runtime["pipeline"]["split_max_sections"] = 2
                connection.execute(
                    "DELETE FROM jobs WHERE source_id=? AND stage IN ('enrich', 'index')",
                    (result.source_id,),
                )
                connection.execute(
                    """
                    UPDATE jobs SET status='queued', attempts=0, last_error=NULL
                    WHERE source_id=? AND stage='extract'
                    """,
                    (result.source_id,),
                )
                connection.commit()
                reconciled = process_jobs(
                    connection,
                    paths,
                    runtime,
                    taxonomy,
                    RuleBasedAdapter(),
                    max_jobs=20,
                )
                self.assertEqual(reconciled.failed, 0)
                self.assertEqual(reconciled.pending, 0)
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                    2,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM documents_fts"
                    ).fetchone()[0],
                    2,
                )
                self.assertEqual(
                    len(list(paths.normalized_dir.glob("**/*.md"))), 2
                )
                self.assertEqual(
                    len(list(paths.vault_dir.glob("**/资料/*.md"))), 2
                )
            finally:
                connection.close()

    def test_frontmatter_and_fenced_headings_do_not_create_cards(self):
        runtime = {
            "pipeline": {
                "split_min_chars": 0,
                "split_min_sections": 2,
                "split_min_section_chars": 0,
                "split_max_sections": 10,
            }
        }
        body = (
            "---\n## metadata-heading\n---\n# 文档\n\n"
            "```markdown\n## fenced-heading\n```\n\n"
            "## 第一节\n内容一。\n\n## 第二节\n内容二。\n"
        )
        cards = split_document(
            title="文档", body=body, original_name="note.md", runtime=runtime
        )
        self.assertEqual([card.title for card in cards], ["第一节", "第二节"])
        combined = "\n".join(card.body for card in cards)
        self.assertEqual(combined.count("metadata-heading"), 1)
        self.assertEqual(combined.count("fenced-heading"), 1)

    def test_maximum_card_limit_merges_overflow_without_losing_text(self):
        runtime = {
            "pipeline": {
                "split_min_chars": 0,
                "split_min_sections": 2,
                "split_min_section_chars": 0,
                "split_max_sections": 2,
            }
        }
        body = "# 文档\n\n" + "\n\n".join(
            "## 第{}节\n唯一正文{}".format(index, index) for index in range(1, 6)
        )
        cards = split_document(
            title="文档", body=body, original_name="note.md", runtime=runtime
        )
        self.assertEqual(len(cards), 2)
        combined = "\n".join(card.body for card in cards)
        for index in range(1, 6):
            self.assertEqual(combined.count("唯一正文{}".format(index)), 1)


if __name__ == "__main__":
    unittest.main()
