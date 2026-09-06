from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

import unittest

from knowledge_os.importers.chat_exports import (
    ChatExportError,
    materialize_conversations,
    parse_export,
    render_conversation,
)


class ChatExportTests(unittest.TestCase):
    def test_chatgpt_mapping_follows_current_branch(self) -> None:
        payload = [
            {
                "id": "conversation-1",
                "title": "Java 讨论",
                "create_time": 1700000000,
                "mapping": {
                    "root": {"message": None, "parent": None},
                    "user": {
                        "parent": "root",
                        "message": {
                            "author": {"role": "user"},
                            "content": {"parts": ["请解释 JVM"]},
                            "create_time": 1700000001,
                        },
                    },
                    "assistant": {
                        "parent": "user",
                        "message": {
                            "author": {"role": "assistant"},
                            "content": {"parts": ["JVM 是 Java 虚拟机。"]},
                            "create_time": 1700000002,
                        },
                    },
                },
                "current_node": "assistant",
            }
        ]
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "conversations.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            conversations = parse_export(source, "auto")
        self.assertEqual(len(conversations), 1)
        self.assertEqual(conversations[0].provider, "chatgpt")
        self.assertEqual([item.role for item in conversations[0].messages], ["user", "assistant"])
        self.assertIn("请解释 JVM", render_conversation(conversations[0]))

    def test_gemini_entries_and_activity_fallback_are_supported(self) -> None:
        payload = {
            "conversations": [
                {
                    "title": "智能体设计",
                    "conversation_id": "gemini-1",
                    "entries": [
                        {"role": "user", "text": "什么是 Agent？"},
                        {"role": "model", "text": "Agent 是能规划并执行任务的系统。"},
                    ],
                },
                {
                    "title": "Prompted 解释 RAG",
                    "time": "2026-09-06T00:00:00Z",
                    "safeHtmlItem": [{"html": "<p>RAG 会检索外部知识。</p>"}],
                },
            ]
        }
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "gemini.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            conversations = parse_export(source, "gemini")
        self.assertEqual(len(conversations), 2)
        self.assertEqual(conversations[0].messages[1].role, "assistant")
        self.assertEqual(conversations[1].messages[0].text, "解释 RAG")
        self.assertEqual(conversations[1].messages[1].text, "RAG 会检索外部知识。")

    def test_zip_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "export.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../escape.json", "{}")
            with self.assertRaises(ChatExportError):
                parse_export(archive_path)

    def test_materialization_is_deterministic_and_does_not_overwrite(self) -> None:
        payload = {
            "conversations": [{
                "title": "一次对话",
                "entries": [{"role": "user", "text": "你好"}],
            }]
        }
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "export.json"
            output = Path(temporary) / "out"
            source.write_text(json.dumps(payload), encoding="utf-8")
            conversation = parse_export(source, "gemini")[0]
            first = materialize_conversations([conversation], output)
            second = materialize_conversations([conversation], output)
            self.assertEqual(first[0].path, second[0].path)
            self.assertFalse(first[0].duplicate)
            self.assertTrue(second[0].duplicate)
            self.assertEqual(len(list(output.glob("*.md"))), 1)


if __name__ == "__main__":
    unittest.main()
