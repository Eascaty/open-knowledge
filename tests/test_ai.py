from __future__ import annotations

import json
import unittest
from unittest import mock

from knowledge_os.ai import (
    AIAdapterError,
    FallbackAdapter,
    KnowledgeExtraction,
    OllamaAdapter,
    RuleBasedAdapter,
    adapter_from_runtime,
)


TAXONOMY = {
    "root": {
        "id": "root",
        "name": "我的知识体系",
        "keywords": [],
        "children": [{"id": "ai", "name": "AI", "keywords": ["Agent"], "children": []}],
    }
}


class Response:
    def __init__(self, value: object) -> None:
        self.payload = json.dumps(value, ensure_ascii=False).encode("utf-8")

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int = -1) -> bytes:
        return self.payload


def wrapper(value: object) -> dict[str, object]:
    return {"message": {"content": json.dumps(value, ensure_ascii=False)}}


class AiAdapterTests(unittest.TestCase):
    def test_ollama_response_is_strictly_normalized(self) -> None:
        value = {
            "summary": "  本地摘要  ",
            "key_points": [" 第一条 "],
            "tags": ["Agent"],
            "suggested_path_ids": ["root", "ai"],
            "relations": [{
                "from_node_id": "ai",
                "to_node_id": "root",
                "confidence": 0.8,
            }],
        }
        adapter = OllamaAdapter(base_url="http://127.0.0.1:11434", model="test")
        with mock.patch("knowledge_os.ai.urllib.request.urlopen", return_value=Response(wrapper(value))):
            result = adapter.extract(title="Agent", body="正文", taxonomy=TAXONOMY)
        self.assertEqual(result.summary, "本地摘要")
        self.assertEqual(result.key_points, ["第一条"])
        self.assertEqual(result.model_name, "ollama:test")
        self.assertEqual(result.relations[0].confidence, 0.8)

    def test_invalid_ollama_shape_is_rejected(self) -> None:
        adapter = OllamaAdapter(base_url="http://127.0.0.1:11434", model="test")
        with mock.patch(
            "knowledge_os.ai.urllib.request.urlopen",
            return_value=Response(wrapper({"summary": "ok", "key_points": "not-list"})),
        ):
            with self.assertRaises(AIAdapterError):
                adapter.extract(title="Agent", body="正文", taxonomy=TAXONOMY)

    def test_ollama_failure_falls_back_to_rules(self) -> None:
        primary = mock.Mock()
        primary.extract.side_effect = AIAdapterError("offline")
        result = FallbackAdapter(primary).extract(
            title="Agent", body="Agent 可以规划任务。", taxonomy=TAXONOMY
        )
        self.assertEqual(result.model_name, "rules-fallback-v1")
        self.assertTrue(result.summary)

    def test_runtime_ollama_adapter_is_resilient(self) -> None:
        adapter = adapter_from_runtime({
            "model": {
                "provider": "ollama",
                "ollama": {"base_url": "http://127.0.0.1:11434", "model": "test"},
            }
        })
        self.assertIsInstance(adapter, FallbackAdapter)
        self.assertIsInstance(adapter.fallback, RuleBasedAdapter)


if __name__ == "__main__":
    unittest.main()
