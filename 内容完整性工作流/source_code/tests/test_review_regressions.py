"""Regression cases for misleading numeric matches and incomplete evidence.

Fixtures are local, synthetic assertions with known contradictions; no network,
API key, real documents, or submitted outputs are modified.
"""
from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import evidence, review


class NumberRegressionTests(unittest.TestCase):
    def assert_not_passed(self, report):
        self.assertEqual(report.exact, [])
        self.assertEqual(report.approx, [])

    def test_existing_other_metric_does_not_validate_tampered_accuracy(self):
        report = review.check_numbers("模型准确率为20%。", "The accuracy was 80%, while the error rate was 20%.")
        self.assert_not_passed(report)
        self.assertTrue(report.context_mismatch)
        self.assertEqual(report.evidence[0]["candidates"][0]["metric"], "error_rate")

    def test_kilometres_are_not_metres(self):
        report = review.check_numbers("距离为10米。", "The distance was 10 km.")
        self.assert_not_passed(report)
        self.assertTrue(report.unit_mismatch)

    def test_english_participant_count_does_not_validate_ten_thousand_scale(self):
        report = review.check_numbers("参与者为20万人。", "We enrolled 20 participants.")
        self.assert_not_passed(report)
        self.assertTrue(report.unit_mismatch)

    def test_equivalent_units_are_numeric_candidates(self):
        report = review.check_numbers("距离为10000米。", "The distance was 10 km.")
        self.assertEqual(report.exact, ["10000"])
        self.assertEqual(report.unit_mismatch, [])

    def test_minus_sign_is_not_discarded(self):
        report = review.check_numbers("温度为5℃。", "The temperature was −5°C.")
        self.assert_not_passed(report)
        self.assertTrue(report.context_mismatch)

    def test_adjacent_year_is_not_rounding(self):
        report = review.check_numbers("研究发表于2025年。", "The study was published in 2024.")
        self.assert_not_passed(report)
        self.assertIn("2025", report.missing)

    def test_integer_sample_count_never_gets_percentage_tolerance(self):
        report = review.check_numbers("参与者为101人。", "There were 100 participants.")
        self.assert_not_passed(report)
        self.assertIn("101", report.missing)

    def test_list_marker_skips_only_itself(self):
        report = review.check_numbers("1. 研究纳入5个样本。", "研究纳入3个样本。")
        self.assertEqual(report.total, 1)
        self.assertIn("5", report.missing)

    def test_scientific_number_in_title_is_checked(self):
        report = review.check_numbers("# 准确率达到99%\n正文。", "The accuracy was 80%.")
        self.assertIn("99", report.missing)

    def test_unknown_cross_language_number_is_pending(self):
        report = review.check_numbers("这种装置的指标达到42。", "The flurbin index was 42.")
        self.assert_not_passed(report)
        self.assertTrue(report.semantic_pending)

    def test_real_decimal_rounding_remains_supported(self):
        report = review.check_numbers("Kappa系数为0.9。", "The Kappa coefficient was 0.87.")
        self.assertEqual(report.approx, ["0.9"])

    def test_sign_and_scientific_notation_are_normalized(self):
        report = review.check_numbers("距离为1e3米。", "The distance was 1 km.")
        self.assertEqual(report.exact, ["1e3"])


class EvidenceRegressionTests(unittest.TestCase):
    def test_searches_a_page_outside_old_head_middle_tail_windows(self):
        pages = [{"page": i, "text": "Generic filler. " * 400} for i in range(1, 31)]
        pages[6]["text"] = "The zeroflux mechanism converts oscillation into heat. The zeroflux effect was measured in the chamber."
        result = evidence.build_review_context("# Zeroflux mechanism\nThe zeroflux mechanism converts oscillation into heat.", pages)
        claim = result["claims"][1]
        self.assertEqual(claim["candidates"][0]["page"], 7)
        self.assertIn("zeroflux", claim["candidates"][0]["text"])
        self.assertFalse(result["coverage"]["review_complete"])

    def test_negative_claim_is_never_proved_by_missing_search_hit(self):
        result = evidence.build_review_context("原文未展开范式的含义。", [{"page": 12, "text": "Technologies and paradigms include frameworks, architectures and interoperability."}])
        self.assertEqual(result["claims"][0]["status"], "negative_pending")
        self.assertTrue(result["uncovered"])
        self.assertIn("否定", result["text"])

    def test_unknown_translation_is_uncovered_not_implicitly_translated(self):
        result = evidence.build_review_context("这种古怪装置改善了旋涡现象。", [{"page": 1, "text": "The flurbin apparatus enhanced vorticity."}])
        self.assertEqual(result["coverage"]["candidate_claims"], 0)
        self.assertTrue(result["uncovered"])

    def test_number_only_overlap_is_not_evidence_coverage(self):
        result = evidence.build_review_context("参与者增长了42倍。", [{"page": 1, "text": "The current is 42 amperes."}])
        self.assertEqual(result["coverage"]["candidate_claims"], 0)
        self.assertTrue(result["uncovered"])

    def test_budget_exclusion_is_visible(self):
        result = evidence.build_review_context("The zeroflux mechanism converts oscillation into heat.", [{"page": 7, "text": "The zeroflux mechanism converts oscillation into heat."}], max_chars=100)
        self.assertLessEqual(len(result["text"]), 100)
        self.assertEqual(result["claims"][0]["status"], "budget_limited")
        self.assertEqual(result["coverage"]["candidate_claims"], 0)

    def test_structure_cues_keep_page_and_do_not_claim_full_review(self):
        result = evidence.build_review_context("The calibration protocol is reproducible.", [{"page": 2, "text": "Methods\nThe calibration protocol is reproducible."}])
        self.assertTrue(any(item["section"] == "方法/综述筛选" and item["page"] == 2 for item in result["structure"]))
        self.assertFalse(result["coverage"]["review_complete"])

    def test_uncovered_claim_prevents_model_full_score_from_being_accepted(self):
        class Client:
            def chat(self, system, prompt):
                return "## 科学准确性：15/15（优秀）\n未发现\n## 内容完整性：20/20（优秀）\n未发现"
        context = evidence.build_review_context("原文未展开范式。", [{"page": 1, "text": "Background"}])
        result = review.llm_review(Client(), "{document}\n{source}\n{facts}", "原文未展开范式。", context["text"], evidence_context=context)
        self.assertIn("待复核", result)
        self.assertNotIn("15/15", result)
        self.assertNotIn("20/20", result)


class SemanticEvidenceRegressionTests(unittest.TestCase):
    CLAIM = "这种装置改善了旋涡现象。"
    QUOTE = "The flurbin apparatus enhanced vorticity in the chamber."

    class Client:
        def __init__(self, callback):
            self.callback = callback
            self.calls = []

        def chat(self, system, prompt):
            self.calls.append(prompt)
            return self.callback(prompt, len(self.calls))

    def payload(self, quote=None, page=1, relation="supports", claim_id="C001"):
        return json.dumps({"matches": [{"claim_id": claim_id, "page": page,
                                       "quote": quote or self.QUOTE, "relation": relation}]})

    def test_verified_cross_language_quote_improves_candidate_coverage(self):
        client = self.Client(lambda *_: self.payload())
        context = evidence.build_semantic_review_context(client, self.CLAIM, [{"page": 1, "text": self.QUOTE}])
        self.assertEqual(context["coverage"]["candidate_claims"], 1)
        candidate = context["claims"][0]["candidates"][0]
        self.assertTrue(candidate["quote_verified"])
        self.assertFalse(candidate["relation_verified"])
        self.assertTrue(context["coverage"]["semantic_scan"]["complete"])
        self.assertFalse(context["coverage"]["review_complete"])

    def test_invented_quote_is_rejected(self):
        client = self.Client(lambda *_: self.payload("The flurbin apparatus cures all known diseases."))
        context = evidence.build_semantic_review_context(client, self.CLAIM, [{"page": 1, "text": self.QUOTE}])
        self.assertEqual(context["coverage"]["semantic_scan"]["rejected_quotes"], 1)
        self.assertFalse(context["coverage"]["semantic_scan"]["complete"])
        self.assertEqual(context["coverage"]["candidate_claims"], 0)
        self.assertNotIn("cures all", context["text"])
        self.assertTrue(context["uncovered"])

    def test_real_quote_with_wrong_page_is_rejected(self):
        client = self.Client(lambda *_: self.payload(page=9))
        context = evidence.build_semantic_review_context(client, self.CLAIM, [{"page": 1, "text": self.QUOTE}])
        self.assertEqual(context["coverage"]["semantic_scan"]["validated_candidates"], 0)
        self.assertTrue(context["uncovered"])

    def test_scans_middle_page_and_every_chunk(self):
        pages = [{"page": i, "text": "Background filler " * 20} for i in range(1, 8)]
        pages[3]["text"] = self.QUOTE
        client = self.Client(lambda prompt, _: self.payload(page=4) if self.QUOTE in prompt else '{"matches": []}')
        context = evidence.build_semantic_review_context(client, self.CLAIM, pages, chunk_chars=400)
        scan = context["coverage"]["semantic_scan"]
        self.assertEqual(scan["calls"], scan["total_chunks"])
        self.assertTrue(scan["complete"])
        self.assertEqual(context["claims"][0]["candidates"][0]["page"], 4)

    def test_partial_failure_cannot_become_complete_even_with_claim_candidate(self):
        def callback(prompt, call):
            if call == 2:
                raise RuntimeError("synthetic failure")
            return self.payload() if self.QUOTE in prompt else '{"matches": []}'
        client = self.Client(callback)
        context = evidence.build_semantic_review_context(client, self.CLAIM,
            [{"page": 1, "text": self.QUOTE}, {"page": 2, "text": "Other source content. " * 10}], chunk_chars=100)
        scan = context["coverage"]["semantic_scan"]
        self.assertFalse(scan["complete"])
        self.assertEqual(len(scan["failed_chunks"]), 1)
        self.assertTrue(any(item["id"] == "SCAN" for item in context["uncovered"]))

    def test_call_limit_is_respected_and_skips_are_visible(self):
        client = self.Client(lambda *_: '{"matches": []}')
        context = evidence.build_semantic_review_context(client, self.CLAIM,
            [{"page": 1, "text": "Source content. " * 80}], chunk_chars=100, max_calls=2)
        scan = context["coverage"]["semantic_scan"]
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(scan["skipped_chunks"])
        self.assertFalse(scan["complete"])

    def test_verified_quote_removed_by_budget_does_not_count_as_covered(self):
        client = self.Client(lambda *_: self.payload())
        context = evidence.build_semantic_review_context(client, self.CLAIM,
            [{"page": 1, "text": self.QUOTE}], max_chars=100)
        self.assertLessEqual(len(context["text"]), 100)
        self.assertEqual(context["coverage"]["semantic_scan"]["validated_candidates"], 1)
        self.assertEqual(context["coverage"]["candidate_claims"], 0)
        self.assertEqual(context["claims"][0]["status"], "budget_limited")
        self.assertFalse(context["claims"][0]["candidates"][0]["included"])

    def test_whitespace_only_normalization_keeps_actual_source_quote(self):
        client = self.Client(lambda *_: self.payload())
        actual = self.QUOTE.replace("enhanced vorticity", "enhanced\nvorticity")
        context = evidence.build_semantic_review_context(client, self.CLAIM, [{"page": 1, "text": actual}])
        self.assertEqual(context["claims"][0]["candidates"][0]["text"], actual)

    def test_negative_or_model_contradiction_remains_pending(self):
        for document, relation in [("原文未展开这种装置。", "supports"), (self.CLAIM, "contradicts")]:
            with self.subTest(document=document):
                client = self.Client(lambda *_: self.payload(relation=relation))
                context = evidence.build_semantic_review_context(client, document, [{"page": 1, "text": self.QUOTE}])
                self.assertEqual(context["coverage"]["candidate_claims"], 0)
                self.assertTrue(context["uncovered"])

    def test_malformed_json_is_a_failed_chunk(self):
        client = self.Client(lambda *_: 'not json')
        context = evidence.build_semantic_review_context(client, self.CLAIM, [{"page": 1, "text": self.QUOTE}])
        self.assertFalse(context["coverage"]["semantic_scan"]["complete"])
        self.assertTrue(any(item["id"] == "SCAN" for item in context["uncovered"]))

    def test_bad_match_types_are_rejected_without_crashing(self):
        client = self.Client(lambda *_: json.dumps({"matches": [{"claim_id": {}, "relation": []}]}))
        context = evidence.build_semantic_review_context(client, self.CLAIM, [{"page": 1, "text": self.QUOTE}])
        self.assertEqual(context["coverage"]["semantic_scan"]["rejected_quotes"], 1)


class FormatRegressionTests(unittest.TestCase):
    def test_format_errors_have_no_invented_fixed_price(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = review.evaluate_format(root, root / "docs", root / "materials", root / "source_code", [], ["AI-01"], [], [], "作品.zip")
            self.assertTrue(report.errors)
            self.assertIsNone(report.score)


if __name__ == "__main__":
    unittest.main()
