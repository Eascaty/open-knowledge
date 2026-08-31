from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from knowledge_os import db
from knowledge_os.config import ProjectPaths, initialize_layout
from knowledge_os.operations.classification import (
    ClassificationCorrectionError,
    correct_document_classification,
)


class ManualClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paths = ProjectPaths.from_root(self.root)
        initialize_layout(self.paths)
        with db.connect(self.paths.database_file) as connection:
            db.initialize_database(connection)
            connection.executescript(
                """
                INSERT INTO nodes(id,parent_id,name,level,path_json,locked,sort_order,active)
                VALUES('root',NULL,'知识',0,'["知识"]',1,0,1),
                      ('old','root','旧分类',1,'["知识","旧分类"]',1,0,1),
                      ('new','root','新分类',1,'["知识","新分类"]',1,1,1),
                      ('inactive','root','停用',1,'["知识","停用"]',0,2,0);
                INSERT INTO sources(id,kind,origin,original_name,raw_path,sha256,mime_type,size_bytes,imported_at,status)
                VALUES('source','file','note.md','note.md','workspace/data/raw/note.md','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','text/markdown',4,'2026-09-01T00:00:00+00:00','completed');
                INSERT INTO documents(id,source_id,title,normalized_path,body,tags_json,created_at,updated_at)
                VALUES('doc','source','测试卡','workspace/data/normalized/doc.md','正文','["标签"]','2026-09-01T00:00:00+00:00','2026-09-01T00:00:00+00:00');
                INSERT INTO placements(document_id,node_id,confidence,method,classified_at)
                VALUES('doc','old',0.5,'rules','2026-09-01T00:00:00+00:00');
                INSERT INTO documents_fts(document_id,title,body,tags,taxonomy_path)
                VALUES('doc','测试卡','正文','标签','知识 / 旧分类');
                """
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_dry_run_then_atomic_correction_updates_index_and_audit(self) -> None:
        preview = correct_document_classification(
            self.root, "doc", "new", expected_node_id="old", dry_run=True
        )
        self.assertTrue(preview.changed)
        with db.connect(self.paths.database_file) as connection:
            self.assertEqual(connection.execute("SELECT node_id FROM placements").fetchone()[0], "old")

        result = correct_document_classification(
            self.root, "doc", "new", expected_node_id="old"
        )
        self.assertTrue(result.changed)
        with db.connect(self.paths.database_file) as connection:
            placement = connection.execute(
                "SELECT node_id, confidence, method FROM placements"
            ).fetchone()
            self.assertEqual(tuple(placement), ("new", 1.0, "manual-v1"))
            self.assertEqual(
                connection.execute("SELECT taxonomy_path FROM documents_fts").fetchone()[0],
                "知识 / 新分类",
            )
            event = json.loads(
                connection.execute(
                    "SELECT details_json FROM events WHERE event_type='classification_corrected'"
                ).fetchone()[0]
            )
            self.assertEqual(event["previous_node_id"], "old")
            self.assertEqual(event["target_node_id"], "new")

    def test_rejects_stale_inactive_root_and_missing_targets_without_changes(self) -> None:
        for target, expected in (("new", "other"), ("inactive", "old"), ("root", "old"), ("missing", "old")):
            with self.assertRaises(ClassificationCorrectionError):
                correct_document_classification(
                    self.root, "doc", target, expected_node_id=expected
                )
        with db.connect(self.paths.database_file) as connection:
            self.assertEqual(connection.execute("SELECT node_id FROM placements").fetchone()[0], "old")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
