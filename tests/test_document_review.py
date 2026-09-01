from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from knowledge_os import db
from knowledge_os.config import ProjectPaths, initialize_layout
from knowledge_os.local_review import BrowserReviewError, parse_review_request
from knowledge_os.operations.review import (
    DocumentReviewError,
    change_document_review,
)
from knowledge_os.processing.artifacts import build_site_data
from knowledge_os.storage.reviews import review_status_for_document


class DocumentReviewTests(unittest.TestCase):
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
                      ('java','root','Java',1,'["知识","Java"]',1,0,1);
                INSERT INTO sources(id,kind,origin,original_name,raw_path,sha256,mime_type,size_bytes,imported_at,status)
                VALUES('source','file','note.md','note.md','workspace/data/raw/note.md','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','text/markdown',4,'2026-09-01T00:00:00+00:00','completed');
                INSERT INTO documents(id,source_id,title,normalized_path,body,tags_json,created_at,updated_at)
                VALUES('doc','source','测试卡','workspace/data/normalized/doc.md','正文','[]','2026-09-01T00:00:00+00:00','2026-09-01T00:00:00+00:00');
                INSERT INTO placements(document_id,node_id,confidence,method,classified_at)
                VALUES('doc','java',0.5,'rules','2026-09-01T00:00:00+00:00');
                """
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_preview_apply_and_export_latest_review_status(self) -> None:
        preview = change_document_review(
            self.root,
            "doc",
            "supported",
            expected_status="unverified",
            dry_run=True,
        )
        self.assertTrue(preview.changed)
        self.assertTrue(preview.dry_run)
        with db.connect(self.paths.database_file) as connection:
            self.assertEqual(review_status_for_document(connection, "doc"), "unverified")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

        applied = change_document_review(
            self.root,
            "doc",
            "supported",
            expected_status="unverified",
        )
        self.assertEqual(applied.previous_status, "unverified")
        with db.connect(self.paths.database_file) as connection:
            self.assertEqual(review_status_for_document(connection, "doc"), "supported")
            event = json.loads(
                connection.execute(
                    "SELECT details_json FROM events WHERE event_type='document_review_status_changed'"
                ).fetchone()[0]
            )
            self.assertEqual(event["target_status"], "supported")
            canonical = build_site_data(
                connection,
                self.paths,
                {
                    "version": 1,
                    "root": {
                        "id": "root",
                        "name": "知识",
                        "locked": True,
                        "keywords": [],
                        "children": [
                            {
                                "id": "java",
                                "name": "Java",
                                "locked": True,
                                "keywords": [],
                                "children": [],
                            }
                        ],
                    },
                    "rules": {},
                },
                {"site": {"title": "测试"}},
                output=self.paths.site_data_dir / "review-test.json",
            )
        self.assertEqual(canonical["documents"][0]["status"], "supported")

    def test_rejects_stale_missing_invalid_and_extra_browser_fields(self) -> None:
        with self.assertRaises(DocumentReviewError):
            change_document_review(
                self.root, "doc", "supported", expected_status="personal"
            )
        with self.assertRaises(DocumentReviewError):
            change_document_review(
                self.root, "missing", "supported", expected_status="unverified"
            )
        with self.assertRaises(DocumentReviewError):
            change_document_review(
                self.root, "doc", "invented", expected_status="unverified"
            )
        with self.assertRaises(BrowserReviewError):
            parse_review_request(
                json.dumps(
                    {
                        "action": "apply",
                        "document_id": "doc",
                        "target_status": "supported",
                        "expected_status": "unverified",
                        "unexpected": True,
                    }
                ).encode("utf-8")
            )
        with db.connect(self.paths.database_file) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
