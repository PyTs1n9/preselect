import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import sha256
from score import check


class ScoreAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.article_dir = self.root / "articles"
        self.audit_dir = self.root / "audits"
        self.article_dir.mkdir()
        self.audit_dir.mkdir()
        self.source = self.root / "source.pdf"
        self.source.write_bytes(b"source")
        self.article = self.article_dir / "AI-01.md"
        self.article.write_text(
            "# 标题\n\n## 引言\n\n事实A。\n\n## 正文\n\n解释。\n\n"
            "## 总结\n\n结论。\n\n## 原文来源\n\n"
            "材料编号：AI-01 Sample title 10.1/example\n",
            encoding="utf-8",
        )
        self.records = {"AI-01": {
            "file": "source.pdf", "doi": "10.1/example",
            "title": "Sample title", "cards": [],
        }}
        self.contexts = [{"id": "AI1", "page": 1, "context": "source quote"}]
        (self.root / "evidence.json").write_text(
            json.dumps(self.records), encoding="utf-8"
        )

    def write_audit(self, status="insufficient_evidence"):
        report = {
            "article_sha256": sha256(self.article),
            "source_sha256": sha256(self.source),
            "paragraphs": [
                {"id": "P01", "claims": [{
                    "article_excerpt": "事实A", "status": status,
                    "evidence": ([{"card_id": "AI1", "page": 1,
                                  "quote": "source quote"}]
                                 if status == "supported" else []),
                    "reason": "待核查",
                }]},
                {"id": "P02", "claims": [], "no_factual_claim_reason": "解释"},
                {"id": "P03", "claims": [], "no_factual_claim_reason": "总结"},
            ],
        }
        (self.audit_dir / "AI-01.json").write_text(
            json.dumps(report), encoding="utf-8"
        )

    def check(self, resolution_path=None):
        with patch("score.IDS", ("AI-01",)), patch("score.HERE", self.root):
            with patch("score.read_pages", return_value=["source quote"]):
                with patch("score.evidence_contexts", return_value=self.contexts):
                    return check(
                        self.article_dir, self.root, audit_dir=self.audit_dir,
                        resolution_path=resolution_path,
                    )["claim_audit"]

    def test_unresolved_claim_requires_matching_record(self):
        self.write_audit()
        self.assertEqual(self.check()["unresolved"], ["AI-01/P01/C1"])
        path = self.root / "resolutions.json"
        path.write_text(json.dumps({"AI-01/P01/C1": {
            "resolution": "accepted_as_explanation", "reason": "科普解释",
            "article_excerpt": "其他句子",
        }}), encoding="utf-8")
        self.assertEqual(self.check(path)["unresolved"], ["AI-01/P01/C1"])
        path.write_text(json.dumps({"AI-01/P01/C1": {
            "resolution": "accepted_as_explanation", "reason": "科普解释",
            "article_excerpt": "事实A",
        }}), encoding="utf-8")
        self.assertEqual(self.check(path)["unresolved"], [])

    def test_changed_source_invalidates_audit(self):
        self.write_audit()
        self.source.write_bytes(b"changed source")
        self.assertEqual(self.check()["stale_or_missing"], ["AI-01"])

    def test_malformed_audit_is_reported(self):
        self.write_audit(status="supported")
        report_path = self.audit_dir / "AI-01.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["paragraphs"][0]["claims"][0]["evidence"][0]["quote"] = "wrong"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        self.assertIn("AI-01", self.check()["invalid"])


if __name__ == "__main__":
    unittest.main()
