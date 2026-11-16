import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit import article_paragraphs, parse_model_json, validate_audit


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.paragraphs = article_paragraphs(
            "# 标题\n\n## 引言\n\n第一段有事实。\n\n"
            "## 正文\n\n第二段没有事实。\n\n## 总结\n\n结论。\n\n"
            "## 原文来源\n\n材料编号：AI-01"
        )
        self.contexts = [{
            "id": "AI1", "page": 2, "claim": "事实",
            "context": "The original source supports this fact.",
        }]

    def test_valid_evidence_locations(self):
        data = {"paragraphs": [
            {"id": "P01", "claims": [{
                "article_excerpt": "第一段有事实",
                "status": "supported",
                "evidence": [{"card_id": "AI1", "page": 2, "quote": "supports this fact"}],
                "reason": "原文支持",
            }]},
            {"id": "P02", "claims": [], "no_factual_claim_reason": "此处为过渡"},
            {"id": "P03", "claims": [], "no_factual_claim_reason": "此处为总结"},
        ]}
        self.assertEqual(validate_audit(data, self.paragraphs, self.contexts), [])
        data["paragraphs"][0]["claims"][0]["evidence"][0]["page"] = 3
        self.assertIn(
            "P01/C1: evidence page or quote does not match source context",
            validate_audit(data, self.paragraphs, self.contexts),
        )
        data["paragraphs"][0]["claims"][0]["evidence"][0]["page"] = 2
        data["paragraphs"][0]["claims"][0]["evidence"][0]["quote"] = ""
        self.assertIn(
            "P01/C1: evidence page or quote does not match source context",
            validate_audit(data, self.paragraphs, self.contexts),
        )

    def test_missing_paragraph_and_unsupported_reference(self):
        data = {"paragraphs": [{
            "id": "P01", "claims": [{
                "article_excerpt": "不存在的文字",
                "status": "supported", "evidence": [],
            }]
        }]}
        issues = validate_audit(data, self.paragraphs, self.contexts)
        self.assertTrue(any("Paragraph IDs" in issue for issue in issues))
        self.assertTrue(any("excerpt is not in" in issue for issue in issues))
        self.assertTrue(any("has no evidence" in issue for issue in issues))

    def test_json_response_can_have_code_fence(self):
        self.assertEqual(
            parse_model_json(chr(96) * 3 + 'json\n{"paragraphs":[]}\n' + chr(96) * 3),
            {"paragraphs": []},
        )
        with self.assertRaises(ValueError):
            parse_model_json('{"wrong":[]}')


if __name__ == "__main__":
    unittest.main()
