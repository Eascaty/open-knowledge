from __future__ import annotations

import hashlib
import io
import json
import stat
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from typing import Any, Dict
from unittest import mock

from knowledge_os import db
from knowledge_os.config import ProjectPaths, initialize_layout
from knowledge_os.local_inbox import (
    HISTORY_LIMIT,
    InboxUploadError,
    InboxUploadStore,
    accepted_extensions,
    limited_upload_history,
    parse_content_length,
    safe_upload_filename,
    upload_outcomes,
)


class _BlockingStream:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def read(self, size: int = -1) -> bytes:
        if self.offset == 0:
            end = min(len(self.payload), max(1, min(size, 3)))
            chunk = self.payload[:end]
            self.offset = end
            self.started.set()
            return chunk
        self.release.wait(timeout=3.0)
        if self.offset >= len(self.payload):
            return b""
        end = (
            len(self.payload)
            if size < 0
            else min(len(self.payload), self.offset + size)
        )
        chunk = self.payload[self.offset:end]
        self.offset = end
        return chunk


class LocalInboxTests(unittest.TestCase):
    def _store(
        self, temporary: str, maximum: int = 1024
    ) -> tuple[ProjectPaths, InboxUploadStore]:
        paths = ProjectPaths.from_root(temporary)
        initialize_layout(paths)
        return paths, InboxUploadStore(paths, maximum)

    def test_upload_stays_outside_inbox_until_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths, store = self._store(temporary)
            payload = b"complete browser upload"
            stream = _BlockingStream(payload)
            outcome: Dict[str, Any] = {}

            def receive() -> None:
                try:
                    outcome["receipt"] = store.receive(
                        stream,
                        encoded_filename="note.md",
                        content_length=str(len(payload)),
                        upload_id="upload-one",
                        received_at="2026-08-23T00:00:00+00:00",
                    )
                except BaseException as exc:  # surfaced in the test thread
                    outcome["error"] = exc

            worker = threading.Thread(target=receive)
            worker.start()
            self.assertTrue(stream.started.wait(timeout=2.0))

            inbox_files = paths.inbox_dir / "files"
            self.assertEqual(list(inbox_files.rglob("*")), [])
            staging = paths.data_dir / "uploads" / "staging"
            self.assertEqual(len(list(staging.rglob("*.part"))), 1)

            stream.release.set()
            worker.join(timeout=3.0)
            self.assertFalse(worker.is_alive())
            self.assertNotIn("error", outcome)
            receipt = outcome["receipt"]
            self.assertEqual(receipt.sha256, hashlib.sha256(payload).hexdigest())
            final = inbox_files / "upload-one" / "note.md"
            self.assertEqual(final.read_bytes(), payload)
            directory_mode = stat.S_IMODE(final.parent.stat().st_mode)
            file_mode = stat.S_IMODE(final.stat().st_mode)
            self.assertEqual(directory_mode & 0o077, 0)
            self.assertEqual(file_mode, 0o600)
            self.assertEqual(list(staging.rglob("*.part")), [])

    def test_interrupted_upload_is_removed_without_publishing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths, store = self._store(temporary)

            with self.assertRaises(InboxUploadError) as raised:
                store.receive(
                    io.BytesIO(b"short"),
                    encoded_filename="broken.md",
                    content_length="20",
                    upload_id="interrupted",
                    received_at="2026-08-23T00:00:00+00:00",
                )

            self.assertEqual(raised.exception.status, 400)
            self.assertEqual(raised.exception.code, "incomplete_upload")
            self.assertEqual(list((paths.inbox_dir / "files").rglob("*")), [])
            staging = paths.data_dir / "uploads" / "staging"
            self.assertEqual(list(staging.rglob("*")), [])

    def test_same_filename_never_overwrites_an_existing_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths, store = self._store(temporary)
            for upload_id, payload in (("first", b"first"), ("second", b"second")):
                store.receive(
                    io.BytesIO(payload),
                    encoded_filename="same.md",
                    content_length=str(len(payload)),
                    upload_id=upload_id,
                    received_at="2026-08-23T00:00:00+00:00",
                )

            first = paths.inbox_dir / "files" / "first" / "same.md"
            second = paths.inbox_dir / "files" / "second" / "same.md"
            self.assertEqual(first.read_bytes(), b"first")
            self.assertEqual(second.read_bytes(), b"second")

            with self.assertRaises(InboxUploadError) as raised:
                store.receive(
                    io.BytesIO(b"replacement"),
                    encoded_filename="same.md",
                    content_length=str(len(b"replacement")),
                    upload_id="first",
                    received_at="2026-08-23T00:01:00+00:00",
                )
            self.assertEqual(raised.exception.status, 409)
            self.assertEqual(raised.exception.code, "upload_conflict")
            self.assertEqual(first.read_bytes(), b"first")

    def test_filename_rejects_paths_invalid_encoding_and_unknown_types(self) -> None:
        rejected = (
            ("../escape.md", 400, "invalid_filename"),
            (urllib.parse.quote("folder/note.md", safe=""), 400, "invalid_filename"),
            (urllib.parse.quote("folder\\note.md", safe=""), 400, "invalid_filename"),
            ("%FF.md", 400, "invalid_filename"),
            ("program.exe", 415, "unsupported_type"),
        )
        for encoded, status, code in rejected:
            with self.subTest(encoded=encoded):
                with self.assertRaises(InboxUploadError) as raised:
                    safe_upload_filename(encoded)
                self.assertEqual(raised.exception.status, status)
                self.assertEqual(raised.exception.code, code)

        self.assertEqual(
            safe_upload_filename(urllib.parse.quote("金融 笔记.PDF")),
            "金融 笔记.PDF",
        )

    def test_content_length_enforces_empty_invalid_and_maximum_sizes(self) -> None:
        self.assertEqual(parse_content_length("8", 8), 8)
        cases = (
            ("", 8, 411, "length_required"),
            ("not-a-number", 8, 400, "invalid_length"),
            ("0", 8, 400, "empty_file"),
            ("-1", 8, 400, "invalid_length"),
            ("9", 8, 413, "file_too_large"),
        )
        for value, maximum, status, code in cases:
            with self.subTest(value=value):
                with self.assertRaises(InboxUploadError) as raised:
                    parse_content_length(value, maximum)
                self.assertEqual(raised.exception.status, status)
                self.assertEqual(raised.exception.code, code)

    def test_pdf_is_not_advertised_or_accepted_without_pdftotext(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch(
            "knowledge_os.local_inbox.shutil.which", return_value=None
        ):
            paths, store = self._store(temporary)
            self.assertNotIn(".pdf", accepted_extensions())
            self.assertNotIn(".pdf", store.accepted_extensions)
            for upload_id, filename in (
                ("unsupported-pdf", "report.pdf"),
                ("unadvertised-extensionless", "README"),
            ):
                with self.subTest(filename=filename):
                    with self.assertRaises(InboxUploadError) as raised:
                        store.receive(
                            io.BytesIO(b"pdf"),
                            encoded_filename=filename,
                            content_length="3",
                            upload_id=upload_id,
                            received_at="2026-08-23T00:00:00+00:00",
                        )
                    self.assertEqual(raised.exception.status, 415)
                    self.assertEqual(raised.exception.code, "unsupported_type")
            self.assertEqual(list((paths.inbox_dir / "files").rglob("*")), [])

    def test_history_bounds_completed_and_permanent_failures_together(self) -> None:
        actionable = [
            {"upload_id": "queued", "status": "queued"},
            {
                "upload_id": "retry",
                "status": "failed",
                "error": {"retryable": True},
            },
        ]
        terminal = [
            {
                "upload_id": "terminal-{}".format(index),
                "status": "completed" if index % 2 else "failed",
                "error": {"retryable": False},
            }
            for index in range(HISTORY_LIMIT + 8)
        ]
        limited = limited_upload_history(actionable + terminal)
        self.assertEqual(limited[:2], actionable)
        self.assertEqual(len(limited), len(actionable) + HISTORY_LIMIT)

    def test_outcomes_prefer_published_site_and_isolate_permanent_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = ProjectPaths.from_root(temporary)
            initialize_layout(paths)
            good_digest = "a" * 64
            bad_digest = "b" * 64
            pending_digest = "c" * 64
            site = paths.site_dir / "dist"
            (site / "data").mkdir(parents=True)
            (site / "build-meta.json").write_text(
                json.dumps({"content_digest": "published-revision"}),
                encoding="utf-8",
            )
            (site / "data" / "site-data.json").write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "id": "sha256-{}".format(good_digest),
                                "source_id": "sha256-{}".format(good_digest),
                                "title": "Good",
                                "node_id": "ai-agent",
                                "path": ["AI", "Agent"],
                                "source": {"sha256": good_digest},
                            },
                            {
                                "id": "card-secondary",
                                "source_id": "sha256-{}".format(good_digest),
                                "title": "Secondary card",
                                "node_id": "technology",
                                "path": ["技术"],
                                "source": {"sha256": good_digest},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            connection = db.connect(paths.database_file)
            try:
                db.initialize_database(connection)
                for digest in (bad_digest, pending_digest):
                    db.insert_source(
                        connection,
                        {
                            "id": "sha256-{}".format(digest),
                            "kind": "file",
                            "origin": "manual",
                            "original_name": "note.md",
                            "raw_path": "workspace/data/raw/note.md",
                            "sha256": digest,
                            "mime_type": "text/markdown",
                            "size_bytes": 4,
                        },
                        max_attempts=1,
                    )
                connection.execute(
                    "UPDATE jobs SET status='failed' WHERE source_id=?",
                    ("sha256-{}".format(bad_digest),),
                )
                connection.commit()
            finally:
                connection.close()

            outcomes = upload_outcomes(
                paths,
                {
                    "good": good_digest,
                    "bad": bad_digest,
                    "pending": pending_digest,
                },
            )
            self.assertEqual(outcomes["good"]["status"], "completed")
            self.assertEqual(
                outcomes["good"]["document"]["id"],
                "sha256-{}".format(good_digest),
            )
            self.assertFalse(outcomes["bad"]["retryable"])
            self.assertTrue(outcomes["pending"]["retryable"])


if __name__ == "__main__":
    unittest.main()
