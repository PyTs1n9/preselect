import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import (
    ModelClient, ModelConfig, body_count, evidence_contexts, generate_one,
    validate_article,
)


class PipelineTests(unittest.TestCase):
    def test_article_validation(self):
        article = (
            "# 标题\n## 引言\n介绍。\n## 正文\n解释。\n"
            "## 总结\n归纳。\n## 原文来源\nAI-01 Sample source title DOI: 10.1234/example\n"
        )
        self.assertEqual(validate_article(article, "AI-01", "10.1234/example"), [])
        self.assertGreater(body_count(article), 0)

    def test_evidence_page_check(self):
        record = {"cards": [{"id": "X1", "page": 2, "claim": "示例", "quote": "source phrase"}]}
        contexts = evidence_contexts(record, ["other", "A source phrase appears here."])
        self.assertEqual(contexts[0]["page"], 2)
        with self.assertRaises(ValueError):
            evidence_contexts(record, ["source phrase", "not here"])

    def test_missing_source_is_rejected(self):
        article = "# 标题\n## 引言\n文字\n## 正文\n文字\n## 总结\n文字\n## 原文来源\n"
        self.assertTrue(validate_article(article, "AI-01", "10.1234/example"))

    def test_internal_evidence_marker_is_rejected(self):
        article = (
            "# 标题\n## 引言\n介绍。\n## 正文\n解释。【IND1，p.2】\n"
            "## 总结\n归纳。\n## 原文来源\nIND-03 Sample source title DOI: 10.1234/example\n"
        )
        self.assertIn(
            "正文包含内部证据卡编号",
            validate_article(article, "IND-03", "10.1234/example", "Sample source title"),
        )

    def test_generation_writes_article_and_trace(self):
        article = (
            "# 标题\n## 引言\n介绍。\n## 正文\n解释。\n"
            "## 总结\n归纳。\n## 原文来源\nAI-01 Sample source title DOI: 10.1234/example\n"
        )

        class FakeClient:
            config = SimpleNamespace(
                model="test-model", api_url="https://example.invalid",
                temperature=0.2, wire_api="responses"
            )

            def __init__(self):
                self.calls = 0

            def complete(self, system, user):
                self.calls += 1
                return article

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.pdf"
            source.write_bytes(b"fixture")
            record = {
                "file": "sample.pdf",
                "title": "Sample source title",
                "authors": "Example Author",
                "venue": "Example Journal",
                "year": "2024",
                "doi": "10.1234/example",
                "type": "research_article",
                "cards": [
                    {"id": "X1", "page": 1, "claim": "示例主张", "quote": "source phrase"}
                ],
            }
            client = FakeClient()
            with patch("pipeline.read_pages", return_value=["A source phrase appears."]):
                result = generate_one("AI-01", record, root, root / "out", root / "logs", client)
            self.assertEqual(client.calls, 2)
            self.assertEqual(result["material_id"], "AI-01")
            self.assertTrue((root / "out" / "AI-01.md").is_file())
            self.assertTrue((root / "logs" / "AI-01.json").is_file())

    def test_generation_repairs_missing_source_title(self):
        valid = (
            "# 标题\n## 引言\n介绍。\n## 正文\n解释。\n"
            "## 总结\n归纳。\n## 原文来源\nAI-01 Sample source title DOI: 10.1234/example\n"
        )
        invalid = valid.replace("Sample source title", "材料未提供")

        class RepairClient:
            config = SimpleNamespace(
                model="test-model", api_url="https://example.invalid",
                temperature=0.2, wire_api="responses"
            )

            def __init__(self):
                self.calls = 0

            def complete(self, system, user):
                self.calls += 1
                return invalid if self.calls == 2 else valid

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.pdf").write_bytes(b"fixture")
            record = {
                "file": "sample.pdf",
                "title": "Sample source title",
                "authors": "Example Author",
                "venue": "Example Journal",
                "year": "2024",
                "doi": "10.1234/example",
                "type": "research_article",
                "cards": [
                    {"id": "X1", "page": 1, "claim": "示例主张", "quote": "source phrase"}
                ],
            }
            client = RepairClient()
            with patch("pipeline.read_pages", return_value=["A source phrase appears."]):
                generate_one("AI-01", record, root, root / "out", root / "logs", client)
            self.assertEqual(client.calls, 3)
            log = (root / "logs" / "AI-01.json").read_text(encoding="utf-8")
            self.assertIn("来源处缺少原文标题", log)
            self.assertIn("Sample source title", (root / "out" / "AI-01.md").read_text())

    def test_unsupported_model_wire_api_fails_before_request(self):
        client = ModelClient(ModelConfig(
            api_url="https://example.invalid", model="test",
            api_key="test", wire_api="unsupported",
        ))
        with self.assertRaisesRegex(ValueError, "Unsupported wire_api"):
            client.complete("system", "user")

    def test_malformed_model_response_is_rejected(self):
        client = ModelClient(ModelConfig(
            api_url="https://example.invalid", model="test",
            api_key="test", wire_api="responses",
        ))
        with self.assertRaisesRegex(ValueError, "empty or non-text"):
            with patch("pipeline.urllib.request.urlopen") as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = b'{"output":[]}'
                client.complete("system", "user")


if __name__ == "__main__":
    unittest.main()
