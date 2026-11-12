"""单项隔离、真实入口、成本路径和模型坏输出回归；不调用网络。"""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import main
from pipeline import criterion, evidence, extractors, generate, package, review, single_review

BODY = "## 引言\n这项研究讨论信息是否可靠。\n\n## 正文\n### 方法\n研究比较多次回答的含义，判断它们是否稳定。\n\n## 总结\n稳定的回答也可能错误，仍须核查事实。"
SOURCE = "研究比较多次回答的含义，判断它们是否稳定。稳定的回答也可能错误，仍须核查事实。"


class SingleReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source_code"
        self.docs = self.root / "docs"
        self.materials = self.root / "materials"
        for directory in (self.source, self.docs, self.materials):
            directory.mkdir()
        for name in ("main.py", "README.md", "requirements.txt"):
            (self.source / name).write_text("fixture", encoding="utf-8")
        ids = [f"M0{i}" for i in range(1, 5)]
        self.config = {
            "llm": {"base_url": "https://example.invalid/v1", "model": "test"},
            "review": {"required_material_ids": ids, "evidence_max_chars": 32000},
            "document": {"overrides": {mid: {"source": "测试来源"} for mid in ids}},
        }
        for mid in ids:
            (self.materials / f"{mid}.txt").write_text(SOURCE, encoding="utf-8")
            document = generate.assemble(BODY, mid, "测试来源", SOURCE, "llm", "测试文章")
            (self.docs / f"{mid}.md").write_text(document, encoding="utf-8")
        self.env = patch.dict(os.environ, {"LLM_API_KEY": "test-only"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def payload(self, **changes):
        result = {"dimension": criterion.NAME, "score": criterion.MAX_SCORE,
                  "risk": "无", "issues": [], "summary": "未发现本项问题"}
        result.update(changes)
        return json.dumps(result, ensure_ascii=False)

    def invoke(self, offline=False):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = single_review.run(self.config, self.root, self.source, offline=offline)
        return code, output.getvalue()

    def assess(self, response, pending=None):
        client = generate.LLMClient(self.config["llm"])
        with patch.object(client, "chat", return_value=response):
            return single_review.assess(client, BODY, {"text": SOURCE, "pending": pending or []})

    def issue(self):
        return {"deduction": 1, "article_quote": "稳定的回答也可能错误，仍须核查事实。",
                "evidence_quote": "稳定的回答也可能错误，仍须核查事实。",
                "reason": "测试问题", "suggestion": "测试修改建议"}

    def test_online_calls_only_current_metric_and_preserves_documents(self):
        before = {p.name: p.read_bytes() for p in self.docs.iterdir()}
        with patch.object(generate.LLMClient, "chat", return_value=self.payload()) as chat:
            code, output = self.invoke()
        self.assertEqual(code, 0, output)
        self.assertEqual(chat.call_count, 1 if criterion.KEY == "format" else 4)
        self.assertIn(criterion.NAME, output)
        for system, prompt in (call.args for call in chat.call_args_list):
            self.assertIn(criterion.NAME, system)
            for other in ("科学准确性", "科普表达", "内容完整性", "格式规范性"):
                if other != criterion.NAME:
                    self.assertNotIn(other, system + prompt)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.docs.iterdir()})

    def test_offline_never_calls_llm_or_scores(self):
        with patch.object(generate.LLMClient, "chat") as chat:
            code, output = self.invoke(offline=True)
        self.assertEqual(code, 0, output)
        chat.assert_not_called()
        self.assertNotIn("LLM模拟评估", output)

    def test_missing_key_fails_instead_of_silent_offline_fallback(self):
        with patch.dict(os.environ, {"LLM_API_KEY": ""}), patch.object(generate.LLMClient, "chat") as chat:
            code, _ = self.invoke()
        self.assertEqual(code, 1)
        chat.assert_not_called()

    def test_api_failure_does_not_touch_documents(self):
        before = {p.name: p.read_bytes() for p in self.docs.iterdir()}
        with patch.object(generate.LLMClient, "chat", side_effect=generate.LLMError("synthetic failure")):
            code, _ = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.docs.iterdir()})

    def test_main_entry_saves_single_metric_report(self):
        with patch.object(main, "PROJECT_ROOT", self.root), patch.object(main, "SOURCE_DIR", self.source):
            with patch.object(generate.LLMClient, "chat", return_value=self.payload()):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main.cmd_review(self.config), 0)
        reports = list((self.source / "result").glob("review_*.md"))
        self.assertEqual(len(reports), 1)
        self.assertIn(criterion.NAME, reports[0].read_text(encoding="utf-8"))

    def test_wrong_dimension_is_rejected(self):
        with self.assertRaises(generate.LLMError):
            self.assess(self.payload(dimension="另一个指标"))

    def test_extra_score_dimension_is_rejected(self):
        with self.assertRaises(generate.LLMError):
            self.assess(self.payload(other_score=12))

    def test_missing_score_field_is_not_success(self):
        result = json.loads(self.payload())
        del result["score"]
        with self.assertRaises(generate.LLMError):
            self.assess(json.dumps(result))

    def test_out_of_range_non_numeric_and_nan_scores_are_rejected(self):
        for value in (True, "20", -1, criterion.MAX_SCORE + 1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(generate.LLMError):
                self.assess(self.payload(score=value))

    def test_invalid_risk_is_rejected_without_typeerror(self):
        for risk in ([], {}, "未知"):
            with self.subTest(risk=risk), self.assertRaises(generate.LLMError):
                self.assess(self.payload(risk=risk))

    def test_deduction_must_match_score(self):
        with self.assertRaises(generate.LLMError):
            self.assess(self.payload(issues=[self.issue()]))

    def test_grounded_deduction_is_accepted(self):
        result = self.assess(self.payload(score=criterion.MAX_SCORE - 1, issues=[self.issue()]))
        self.assertEqual(result["score"], criterion.MAX_SCORE - 1)

    def test_fabricated_article_quote_is_rejected(self):
        issue = self.issue()
        issue["article_quote"] = "这段话没有出现在成稿里"
        with self.assertRaises(generate.LLMError):
            self.assess(self.payload(score=criterion.MAX_SCORE - 1, issues=[issue]))

    def test_pending_evidence_overrides_overconfident_full_score(self):
        result = self.assess(self.payload(), ["某页未覆盖"])
        self.assertIsNone(result["score"])

    def test_fabricated_source_quote_is_rejected(self):
        if criterion.KEY == "expression":
            self.skipTest("表达项只需要文章原句")
        issue = self.issue()
        issue["evidence_quote"] = "这不是输入原文中的证据"
        with self.assertRaises(generate.LLMError):
            self.assess(self.payload(score=criterion.MAX_SCORE - 1, issues=[issue]))

    def test_format_reviews_verified_zip_with_a_numeric_score(self):
        if criterion.KEY != "format":
            self.skipTest("仅适用于格式规范性")
        package.build(self.root, self.docs, self.source, "作品.zip",
            expected_ids=self.config["review"]["required_material_ids"], materials_dir=self.materials)
        with patch.object(generate.LLMClient, "chat", return_value=self.payload()) as chat:
            code, output = self.invoke()
        self.assertEqual(code, 0, output)
        self.assertEqual(chat.call_count, 1)
        self.assertIn("10/10", output)
        self.assertNotIn("待复核，暂不计分", output)

    def test_format_rejects_a_model_that_ignores_proven_errors(self):
        if criterion.KEY != "format":
            self.skipTest("仅适用于格式规范性")
        document = self.docs / "M01.md"
        document.write_text("# 不完整的文章", encoding="utf-8")
        with patch.object(generate.LLMClient, "chat", return_value=self.payload()) as chat:
            code, output = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(chat.call_count, 1)
        self.assertIn("忽略已确认的格式错误", output)

    def test_format_reports_current_package_readback_mismatch(self):
        if criterion.KEY != "format":
            self.skipTest("仅适用于格式规范性")
        package.build(self.root, self.docs, self.source, "作品.zip",
            expected_ids=self.config["review"]["required_material_ids"], materials_dir=self.materials)
        zip_path = self.root / "作品.zip"
        with zipfile.ZipFile(zip_path, "a") as archive:
            archive.writestr("source_code/.env.local", "SHOULD_NEVER_BE_PACKAGED")
        issue = {"deduction": 2, "article_quote": "",
                 "evidence_quote": "压缩包包含禁止提交的本地或构建文件：source_code/.env.local",
                 "reason": "ZIP内容超出当前源码清单", "suggestion": "重新生成并核验压缩包"}
        response = self.payload(score=criterion.MAX_SCORE - 2, issues=[issue])
        with patch.object(generate.LLMClient, "chat", return_value=response) as chat:
            code, output = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(chat.call_count, 1)
        self.assertIn("禁止提交", output)

    def test_format_sends_missing_all_documents_to_the_llm(self):
        if criterion.KEY != "format":
            self.skipTest("仅适用于格式规范性")
        for path in self.docs.glob("*.md"):
            path.unlink()
        issue = {"deduction": 10, "article_quote": "",
                 "evidence_quote": "缺少应提交的 Markdown：M01.md",
                 "reason": "四份文档全部缺失", "suggestion": "补齐四份文档并打包"}
        with patch.object(generate.LLMClient, "chat", return_value=self.payload(score=None, issues=[issue])) as chat:
            code, output = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(chat.call_count, 1)
        self.assertIn("四份文档全部缺失", output)

    def test_short_source_uses_full_text_without_extra_llm_calls(self):
        if criterion.KEY not in {"coverage", "accuracy"}:
            self.skipTest("当前指标不需要原文取证")
        client = generate.LLMClient(self.config["llm"])
        with patch.object(client, "chat") as chat:
            result = single_review.context_for(client, BODY, [{"page": 1, "text": SOURCE}], self.config)
        chat.assert_not_called()
        self.assertIn(SOURCE, result["text"])
        self.assertEqual(result["pending"], [])

    def test_blank_page_prevents_source_complete_score(self):
        if criterion.KEY not in {"coverage", "accuracy"}:
            self.skipTest("当前指标不需要原文取证")
        result = single_review.context_for(None, BODY,
            [{"page": 1, "text": SOURCE}, {"page": 2, "text": ""}], self.config)
        self.assertTrue(result["pending"])

    def test_expression_avoids_pdf_and_all_source_evidence_calls(self):
        if criterion.KEY != "expression":
            self.skipTest("仅适用于科普表达")
        with patch.object(extractors, "extract_pages", side_effect=AssertionError("不应抽取PDF")):
            with patch.object(single_review, "context_for", side_effect=AssertionError("不应取证")):
                with patch.object(review, "check_numbers", side_effect=AssertionError("不应核对数字")):
                    with patch.object(generate.LLMClient, "chat", return_value=self.payload()) as chat:
                        code, output = self.invoke()
        self.assertEqual(code, 0, output)
        self.assertEqual(chat.call_count, 4)

    def test_format_uses_one_llm_call_without_pdf_extraction(self):
        if criterion.KEY != "format":
            self.skipTest("仅适用于格式规范性")
        with patch.object(extractors, "extract_pages", side_effect=AssertionError("不应抽取PDF")):
            with patch.object(generate.LLMClient, "chat", return_value=self.payload()) as chat:
                code, output = self.invoke()
        self.assertEqual(code, 0, output)
        self.assertEqual(chat.call_count, 1)
        self.assertIn("待复核", output)

    def test_accuracy_long_source_uses_existing_verified_semantic_retrieval(self):
        if criterion.KEY != "accuracy":
            self.skipTest("仅适用于科学准确性")
        config = {"review": {"evidence_max_chars": 1000, "max_evidence_calls": 2}}
        found = {"text": SOURCE, "uncovered": [{"id": "SCAN", "reason": "扫描未完成"}]}
        with patch.object(evidence, "build_semantic_review_context", return_value=found) as retrieve:
            result = single_review.context_for(object(), BODY, [{"page": 1, "text": SOURCE * 100}], config)
        self.assertEqual(retrieve.call_args.kwargs["max_calls"], 2)
        self.assertTrue(result["pending"])

    def test_coverage_long_source_checks_original_points_not_just_article_claims(self):
        if criterion.KEY != "coverage":
            self.skipTest("仅适用于内容完整性")
        quote = 'The omitted key finding was a reduction in uncertainty.'
        page = {"page": 1, "text": quote + " filler" * 190}
        config = {"review": {"evidence_max_chars": 1000, "max_evidence_calls": 2}}
        fact = {"page": 1, "kind": "finding", "claim": "文章没写的重要发现", "quote": quote,
                "subject": "", "metric": "", "value": "", "unit": "", "condition": "", "strength": ""}
        client = generate.LLMClient(self.config["llm"])
        with patch.object(client, "chat", return_value=json.dumps({"facts": [fact]})) as chat:
            context = single_review.context_for(client, BODY, [page], config)
        self.assertEqual(chat.call_count, 1)
        self.assertIn(quote, context["text"])
        self.assertIn("文章没写的重要发现", context["text"])

    def test_coverage_call_limit_and_forged_quotes_stay_pending(self):
        if criterion.KEY != "coverage":
            self.skipTest("仅适用于内容完整性")
        config = {"review": {"evidence_max_chars": 1000, "max_evidence_calls": 1}}
        client = generate.LLMClient(self.config["llm"])
        with patch.object(client, "chat", return_value='{"facts": "invalid"}') as chat:
            result = single_review.context_for(client, BODY, [{"page": 1, "text": "source " * 4000}], config)
        self.assertEqual(chat.call_count, 1)
        self.assertTrue(any("上限" in p for p in result["pending"]))
        self.assertTrue(any("失败" in p for p in result["pending"]))


if __name__ == "__main__":
    unittest.main()
