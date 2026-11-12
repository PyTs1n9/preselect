"""生成链路失败保护与原句证据回归测试；不访问网络、不改参赛稿。"""
from __future__ import annotations

import json
import os
import re
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline import extractors, generate


VALID_BODY = "## 引言\n研究关注如何辨别答案是否可靠。\n\n## 正文\n### 怎样研究\n比较同一问题的多个回答，先看含义是否一致。\n\n## 总结\n输出稳定不保证事实正确，仍应核对来源。"
QUOTE = "The measured accuracy was 80% for adults in this experiment."


def fact(quote=QUOTE, page=1):
    return {"page": page, "kind": "finding", "claim": "此实验测量成人样本准确率。",
            "quote": quote, "subject": "adults", "metric": "accuracy", "value": "80%",
            "unit": "%", "condition": "in this experiment", "strength": ""}


class SequenceClient:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.requests = []

    def chat(self, system, user):
        self.requests.append(user)
        return next(self.outputs)


class TestResponseCompletion(unittest.TestCase):
    def _chat(self, reason, content):
        calls = []
        response = types.SimpleNamespace(status_code=200, json=lambda: {
            "choices": [{"finish_reason": reason, "message": {"content": content}}]})
        requests = types.SimpleNamespace(post=lambda *a, **k: (calls.append(k), response)[1])
        with patch.dict("sys.modules", {"requests": requests}), patch.dict(os.environ, {"LLM_API_KEY": "test-value"}):
            client = generate.LLMClient({"base_url": "http://example.invalid", "model": "test"})
            with self.assertRaises(generate.LLMError):
                client.chat("system", "user")
        self.assertEqual(len(calls), 1)

    def test_partial_and_missing_finish_reason_are_usable_but_filtered_responses_are_rejected(self):
        for reason in ("length", None):
            with self.subTest(reason=reason):
                response = types.SimpleNamespace(status_code=200, json=lambda: {
                    "choices": [{"finish_reason": reason, "message": {"content": VALID_BODY}}]})
                requests = types.SimpleNamespace(post=lambda *a, **k: response)
                with patch.dict("sys.modules", {"requests": requests}), patch.dict(os.environ, {"LLM_API_KEY": "test-value"}):
                    client = generate.LLMClient({"base_url": "http://example.invalid", "model": "test"})
                    self.assertEqual(client.chat("system", "user"), VALID_BODY)
        for reason in ("content_filter", "tool_calls"):
            with self.subTest(reason=reason):
                self._chat(reason, VALID_BODY)

    def test_stop_with_empty_or_non_text_content_is_rejected(self):
        for content in ("", "  ", None, []):
            with self.subTest(content=content):
                self._chat("stop", content)


class TestPageEvidence(unittest.TestCase):
    def test_extract_pages_keeps_blank_pdf_page_and_original_text_join(self):
        raw = ["First page has sufficient extractable text.", "", "Third page text."]
        reader = types.SimpleNamespace(pages=[types.SimpleNamespace(extract_text=lambda t=t: t) for t in raw])
        with patch.dict("sys.modules", {"pypdf": types.SimpleNamespace(PdfReader=lambda p: reader)}):
            pages = extractors.extract_pages(Path("test.pdf"))
            self.assertEqual([p["page"] for p in pages], [1, 2, 3])
            self.assertEqual(pages[1]["text"], "")
            self.assertEqual(extractors.extract_text(Path("test.pdf")), extractors.clean_text("\n".join(raw)))

    def test_non_pdf_has_one_logical_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "material.txt"
            path.write_text("材料内容，有完整的一句话。", encoding="utf-8")
            self.assertEqual(extractors.extract_pages(path), [{"page": 1, "text": extractors.extract_text(path)}])

    def test_quote_must_occur_on_claimed_page_not_elsewhere(self):
        chunk = [{"page": 1, "text": "A different observation was reported."}, {"page": 2, "text": QUOTE}]
        with self.assertRaisesRegex(generate.LLMError, "第1页找不到"):
            generate._validated_facts(json.dumps({"facts": [fact()]}), chunk, 1)

    def test_field_cannot_borrow_unrelated_number_from_page(self):
        row = fact()
        row["value"] = "20%"
        chunk = [{"page": 1, "text": QUOTE + " Another measure was 20%."}]
        with self.assertRaisesRegex(generate.LLMError, "字段value"):
            generate._validated_facts(json.dumps({"facts": [row]}), chunk, 1)

    def test_fabricated_quote_retries_once_then_fails(self):
        invalid = json.dumps({"facts": [fact()]})
        client = SequenceClient([invalid, invalid])
        with self.assertRaisesRegex(generate.LLMError, "事实证据校验失败"):
            generate._extract_facts(client, "提取事实", [{"page": 1, "text": "Unrelated original material."}], 1)
        self.assertEqual(len(client.requests), 2)

    def test_valid_evidence_preserves_page_and_whitespace_tolerant_quote(self):
        rows = generate._validated_facts(json.dumps({"facts": [fact(page=7)]}),
                                        [{"page": 7, "text": QUOTE.replace("accuracy was", "accuracy\nwas")}], 3)
        self.assertEqual(rows[0]["page"], 7)
        self.assertEqual(rows[0]["id"], "C003-F01")
        self.assertTrue(rows[0]["quote_verified"])

    def test_long_material_produces_traceable_facts_and_type_guidance(self):
        pages = [{"page": i, "text": (QUOTE + "\n") * 180} for i in (1, 2, 3)]
        class EvidenceClient:
            def __init__(self):
                self.requests = []
            def chat(self, system, user):
                self.requests.append(user)
                if "【片段内容】" in user:
                    page = int(re.search(r"\[PAGE (\d+)\]", user).group(1))
                    # 一页可能切成两块；仅引用当前片段中完整出现的原句。
                    return json.dumps({"facts": [fact(page=page)]})
                return VALID_BODY
        client, artifacts = EvidenceClient(), {}
        out = generate.generate_with_llm(client, "\n".join(p["text"] for p in pages),
                                         "M01", "研究论文", 1200, pages=pages,
                                         document_type="experiment", artifacts=artifacts)
        self.assertEqual(out, VALID_BODY)
        self.assertEqual({row["page"] for row in artifacts["facts"]}, {1, 2, 3})
        self.assertIn("实验研究", client.requests[-1])
        self.assertEqual(artifacts["validation"][-1]["errors"], [])


class TestDraftAcceptance(unittest.TestCase):
    def test_missing_section_is_repaired_from_evidence(self):
        incomplete = "## 引言\n研究问题。\n\n## 正文\n研究方法与结果。"
        client = SequenceClient([incomplete, VALID_BODY])
        out = generate.generate_with_llm(client, "原始材料中已有问题方法结果局限。", "M01", "某论文", 1200)
        self.assertEqual(out, VALID_BODY)
        self.assertIn("原始材料中已有", client.requests[1])
        self.assertNotIn("原文未单列", out)

    def test_persistently_missing_sections_fail_after_bounded_repairs(self):
        client = SequenceClient(["## 引言\n只有引言。"] * 3)
        with self.assertRaisesRegex(generate.LLMError, "有限修复"):
            generate.generate_with_llm(client, "原始材料。", "M01", "某论文", 1200)
        self.assertEqual(len(client.requests), 3)

    def test_empty_section_is_not_accepted(self):
        invalid = "## 引言\n问题。\n## 正文\n### 只有小标题\n## 总结\n局限。"
        self.assertTrue(any("内容为空" in error for error in generate._draft_errors(invalid)))

    def test_assembly_rejects_missing_section_instead_of_inventing_source_gap(self):
        with self.assertRaises(generate.LLMError):
            generate.assemble("## 引言\n内容。", "M01", "某论文", "原文", "llm", "标题")

    def test_compression_that_still_exceeds_limit_is_rejected(self):
        overlong = VALID_BODY.replace("研究方法", "研究方法") + "长" * 2600
        client = SequenceClient([overlong])
        with self.assertRaisesRegex(generate.LLMError, "压缩结果"):
            generate._maybe_compress(client, overlong)

    def test_compression_that_loses_a_section_is_rejected(self):
        client = SequenceClient(["## 引言\n内容。\n## 总结\n内容。"])
        with self.assertRaisesRegex(generate.LLMError, "压缩结果"):
            generate._maybe_compress(client, VALID_BODY + "长" * 2600)

    def test_core_evidence_conflict_is_not_repaired_away(self):
        client = SequenceClient(["[需复核] C001-F01与C002-F02的研究对象尚不能确认。"])
        with self.assertRaisesRegex(generate.LLMError, "核心证据"):
            generate.generate_with_llm(client, "原始材料。", "M01", "某论文", 1200)
        self.assertEqual(len(client.requests), 1)

    def test_english_extractive_output_is_rejected(self):
        with self.assertRaisesRegex(generate.LLMError, "外文摘录"):
            generate.generate_extractive(QUOTE * 20, "M01", "某论文")

    def test_provenance_does_not_guarantee_no_manual_edits(self):
        out = generate.assemble(VALID_BODY, "M01", "某论文", "原文", "llm", "标题")
        self.assertNotIn("未经人工改写", out)
        self.assertIn("不证明改写准确性或生成过程", out)


def quality_result(issues=None, body=VALID_BODY):
    coverage = [{"article_quote": sentence, "evidence_quote": QUOTE,
                 "status": "supported", "fact_ids": ["RAW-CONTENT"]}
                for sentence in generate._claim_sentences(body)]
    return json.dumps({"checked": sorted(generate._QUALITY_DIMENSIONS),
                       "issues": issues or [], "coverage": coverage}, ensure_ascii=False)


def quality_issue(category="accuracy", article="比较同一问题的多个回答，先看含义是否一致。", quote=QUOTE):
    return {"category": category, "article_quote": article, "evidence_quote": quote,
            "reason": "应明确本次实验的对象和比较指标。",
            "suggestion": "按证据说明对象是成人、指标是准确率。"}


class TestEvidenceQualityGate(unittest.TestCase):
    def run_generation(self, outputs, artifacts=None):
        client = SequenceClient(outputs)
        result = generate.generate_with_llm(client, QUOTE, "M01", "某论文", 1200,
                                           artifacts=artifacts, quality_check=True)
        return result, client

    def test_pass_records_scope_without_claiming_scientific_guarantee(self):
        artifacts = {}
        body, client = self.run_generation([VALID_BODY, quality_result()], artifacts)
        self.assertEqual(body, VALID_BODY)
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(len(artifacts["quality_checks"]), 1)
        self.assertIn("不保证科学正确性", artifacts["quality_scope"])

    def test_grounded_issue_is_revised_once_and_rechecked(self):
        revised = VALID_BODY.replace("比较同一问题的多个回答，先看含义是否一致。", "本次实验的成人样本准确率为80%。")
        artifacts = {}
        body, client = self.run_generation([VALID_BODY, quality_result([quality_issue()]),
                                            revised, quality_result(body=revised)], artifacts)
        self.assertEqual(body, revised)
        self.assertEqual(len(client.requests), 4)
        self.assertEqual(len(artifacts["quality_checks"]), 2)
        self.assertIn(QUOTE, client.requests[2])

    def test_unresolved_core_issue_is_reported_without_discarding_article(self):
        artifacts = {}
        client = SequenceClient([VALID_BODY, quality_result([quality_issue()]),
                                 VALID_BODY, quality_result([quality_issue()])])
        body = generate.generate_with_llm(client, QUOTE, "M01", "某论文", 1200,
                                           quality_check=True, artifacts=artifacts)
        self.assertEqual(body, VALID_BODY)
        self.assertEqual(artifacts["quality_checks"][-1]["status"], "unresolved")
        self.assertEqual(len(client.requests), 4)

    def test_malformed_or_incomplete_review_is_not_a_pass(self):
        for raw in ("No issues found.", "{}", json.dumps({"checked": [], "issues": []})):
            with self.subTest(raw=raw):
                artifacts = {}
                body, _ = self.run_generation([VALID_BODY, raw], artifacts)
                self.assertEqual(body, VALID_BODY)
                self.assertEqual(artifacts["quality_checks"][-1]["status"], "unavailable")

    def test_fake_article_or_evidence_quote_is_rejected(self):
        for issue in (quality_issue(article="作品里并没有这句话。"),
                      quality_issue(quote="The actual accuracy was 95% for all patients.")):
            with self.subTest(issue=issue):
                artifacts = {}
                body, _ = self.run_generation([VALID_BODY, quality_result([issue])], artifacts)
                self.assertEqual(body, VALID_BODY)
                self.assertEqual(artifacts["quality_checks"][-1]["status"], "unavailable")

    def test_coverage_omission_allows_empty_article_quote_but_needs_evidence(self):
        issue = quality_issue(category="coverage", article="")
        checked = generate._validated_quality_check(quality_result([issue]), VALID_BODY, [QUOTE])
        self.assertEqual(checked["issues"][0]["article_quote"], "")
        issue["evidence_quote"] = ""
        with self.assertRaises(generate.LLMError):
            generate._validated_quality_check(quality_result([issue]), VALID_BODY, [QUOTE])

    def test_fact_claim_metadata_cannot_substitute_for_raw_evidence(self):
        issue = quality_issue(quote="这句中文只是模型概括而不是原句")
        with self.assertRaisesRegex(generate.LLMError, "本轮原文证据"):
            generate._validated_quality_check(quality_result([issue]), VALID_BODY, [QUOTE])

    def test_bad_structure_in_quality_revision_keeps_valid_original(self):
        artifacts = {}
        client = SequenceClient([VALID_BODY, quality_result([quality_issue()]), "## 引言\n只有引言。"])
        body = generate.generate_with_llm(client, QUOTE, "M01", "某论文", 1200,
                                           quality_check=True, artifacts=artifacts)
        self.assertEqual(body, VALID_BODY)
        self.assertEqual(artifacts["quality_checks"][-1]["status"], "revision_invalid")
        self.assertEqual(len(client.requests), 3)

    def test_remaining_expression_advice_is_retained_without_infinite_revisions(self):
        issue = quality_issue(category="expression")
        artifacts = {}
        body, client = self.run_generation([VALID_BODY, quality_result([issue]),
                                            VALID_BODY, quality_result([issue])], artifacts)
        self.assertEqual(body, VALID_BODY)
        self.assertEqual(len(client.requests), 4)
        self.assertEqual(artifacts["quality_checks"][-1]["issues"][0]["category"], "expression")

    def test_compressed_article_is_the_one_checked(self):
        oversized = VALID_BODY + "长" * 2600
        body, client = self.run_generation([oversized, VALID_BODY, quality_result()])
        self.assertEqual(body, VALID_BODY)
        self.assertEqual(len(client.requests), 3)
        self.assertIn(VALID_BODY, client.requests[2])
        self.assertNotIn(oversized, client.requests[2])


if __name__ == "__main__":
    unittest.main()
