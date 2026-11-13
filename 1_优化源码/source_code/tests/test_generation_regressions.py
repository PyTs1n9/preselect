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

    def test_only_stop_completion_is_usable(self):
        for reason in ("length", None, "content_filter", "tool_calls"):
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

    def test_fact_claim_cannot_add_dataset_or_number_missing_from_quote(self):
        row = fact()
        row["claim"] = "成人样本准确率80%，并评估了SVAMP数据集。"
        with self.assertRaisesRegex(generate.LLMError, "SVAMP"):
            generate._validated_facts(
                json.dumps({"facts": [row]}, ensure_ascii=False),
                [{"page": 1, "text": QUOTE}], 1,
            )

    def test_fabricated_quote_retries_once_then_fails(self):
        invalid = json.dumps({"facts": [fact()]})
        client = SequenceClient([invalid, invalid])
        with self.assertRaisesRegex(generate.LLMError, "事实证据校验失败"):
            generate._extract_facts(client, "提取事实", [{"page": 1, "text": "Unrelated original material."}], 1)
        self.assertEqual(len(client.requests), 2)

    def test_mixed_valid_and_invalid_facts_keep_only_verified_rows(self):
        invalid = fact()
        invalid["quote"] = "This quote is absent from the page."
        raw = json.dumps({"facts": [fact(), invalid]}, ensure_ascii=False)
        client = SequenceClient([raw, raw])
        rejected = []
        rows = generate._extract_facts(
            client, "提取事实", [{"page": 1, "text": QUOTE}], 1, rejected
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(len(client.requests), 2)

    def test_valid_evidence_preserves_page_and_whitespace_tolerant_quote(self):
        rows = generate._validated_facts(json.dumps({"facts": [fact(page=7)]}),
                                        [{"page": 7, "text": QUOTE.replace("accuracy was", "accuracy\nwas")}], 3)
        self.assertEqual(rows[0]["page"], 7)
        self.assertEqual(rows[0]["id"], "C003-F01")
        self.assertTrue(rows[0]["quote_verified"])

    def test_quote_matching_normalizes_literal_json_linebreak_escapes(self):
        quote = "The code for paragraph-length experiments can be found at github.com/example/project."
        escaped = "The code for paragraph-length experiments\\ncan be found at github.com/example/project."
        self.assertEqual(generate._normalize_quote(escaped), generate._normalize_quote(quote))

    def test_quote_matching_preserves_en_dash_at_pdf_line_wrap(self):
        wrapped = "mRNA–\nlipid nanoparticle vaccines"
        compact = "mRNA–lipid nanoparticle vaccines"
        self.assertEqual(generate._normalize_quote(wrapped), generate._normalize_quote(compact))
        self.assertEqual(generate._normalize_quote("inter-\nnational"), "international")

    def test_rich_chunk_fact_limit_preserves_up_to_configured_cap(self):
        rows = [fact() for _ in range(generate.MAX_FACTS_PER_CHUNK)]
        accepted = generate._validated_facts(
            json.dumps({"facts": rows}, ensure_ascii=False),
            [{"page": 1, "text": QUOTE}], 1,
        )
        self.assertEqual(len(accepted), generate.MAX_FACTS_PER_CHUNK)
        too_many = rows + [fact()]
        with self.assertRaisesRegex(generate.LLMError, r"49项，超过上限48"):
            generate._validated_facts(
                json.dumps({"facts": too_many}, ensure_ascii=False),
                [{"page": 1, "text": QUOTE}], 1,
            )

    def test_reduce_evidence_excludes_repository_and_author_housekeeping(self):
        rows = [
            {"id": "C001-F01", "page": 1, "kind": "finding", "claim": "核心发现",
             "quote": "The method improves AUROC in the evaluated tasks."},
            {"id": "C001-F02", "page": 1, "kind": "background", "claim": "代码发布",
             "quote": "We release all code at github.com/example/project."},
            {"id": "C001-F03", "page": 1, "kind": "background", "claim": "作者贡献",
             "quote": "S.F. led the work from conception to completion."},
        ]
        selected = generate._facts_for_reduce(rows)
        self.assertEqual([row["id"] for row in selected], ["C001-F01"])

    def test_must_cover_keeps_trial_design_and_alternative_delivery_path(self):
        rows = [
            {"id": "C001-F01", "kind": "method", "claim": "I期临床测试",
             "quote": "Both vaccines were tested in a prime-boost setting (4 weeks between injections) in healthy individuals."},
            {"id": "C001-F02", "kind": "method", "claim": "共递送方案",
             "quote": "Co-delivery of mRNA encoding upper respiratory tract-homing molecules could elicit mucosal immunity."},
        ]
        self.assertEqual(
            {row["id"] for row in generate._facts_for_must_cover(rows)},
            {"C001-F01", "C001-F02"},
        )

    def test_fact_overflow_retry_requests_compact_core_facts(self):
        too_many = json.dumps(
            {"facts": [fact() for _ in range(generate.MAX_FACTS_PER_CHUNK + 1)]},
            ensure_ascii=False,
        )
        valid = json.dumps({"facts": [fact()]}, ensure_ascii=False)
        client = SequenceClient([too_many, valid])
        rows = generate._extract_facts(client, "提取事实", [{"page": 1, "text": QUOTE}], 1)
        self.assertEqual(len(rows), 1)
        self.assertIn(f"严格只保留最多{generate.MAP_FACT_TARGET}条", client.requests[1])

    def test_fact_parser_distinguishes_missing_list_from_overflow(self):
        with self.assertRaisesRegex(generate.LLMError, "缺少facts字段"):
            generate._validated_facts("{}", [{"page": 1, "text": QUOTE}], 1)
        with self.assertRaisesRegex(generate.LLMError, "超过上限"):
            generate._validated_facts(
                json.dumps({"facts": [fact()] * (generate.MAX_FACTS_PER_CHUNK + 1)}),
                [{"page": 1, "text": QUOTE}], 1,
            )

    def test_split_fact_retry_does_not_publish_partial_recovery(self):
        client = SequenceClient([])
        chunks = [[{"page": 1, "text": "first"}], [{"page": 1, "text": "second"}]]
        with patch.object(generate, "_split_chunk_for_retry", return_value=chunks), \
             patch.object(generate, "_extract_facts", side_effect=[
                 generate.LLMError("initial overflow"), [fact()],
                 generate.LLMError("second subchunk failed"),
             ]):
            with self.assertRaisesRegex(generate.LLMError, "second subchunk failed"):
                generate._extract_fact_batches(
                    client, [(1, [{"page": 1, "text": "large"}], "request")]
                )

    def test_long_material_produces_traceable_facts_and_type_guidance(self):
        pages = [{"page": i, "text": (QUOTE + "\n") * 180} for i in (1, 2, 3)]
        class EvidenceClient:
            def __init__(self):
                self.requests = []
            def chat(self, system, user):
                self.requests.append(user)
                if "【片段内容】" in user:
                    pages_in_chunk = sorted({int(value) for value in re.findall(r"\[PAGE (\d+)\]", user)})
                    # 一页可能切成两块；仅引用当前片段中完整出现的原句。
                    return json.dumps({"facts": [fact(page=page) for page in pages_in_chunk]})
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
                 "dimension": "finding", "status": "supported",
                 "fact_ids": ["RAW-CONTENT"]}
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

    def test_unresolved_core_issue_blocks_generation(self):
        artifacts = {}
        client = SequenceClient([VALID_BODY, quality_result([quality_issue()]),
                                 VALID_BODY, quality_result([quality_issue()])])
        with self.assertRaisesRegex(generate.LLMError, "仍未解决"):
            generate.generate_with_llm(client, QUOTE, "M01", "某论文", 1200,
                                       quality_check=True, artifacts=artifacts)
        self.assertEqual(artifacts["quality_checks"][-1]["status"], "checked")
        self.assertEqual(len(client.requests), 4)

    def test_malformed_or_incomplete_review_is_not_a_pass(self):
        for raw in ("No issues found.", "{}", json.dumps({"checked": [], "issues": []})):
            with self.subTest(raw=raw):
                artifacts = {}
                with self.assertRaises(generate.LLMError):
                    self.run_generation([VALID_BODY, raw, raw], artifacts)
                self.assertEqual(artifacts["quality_checks"][-1]["status"], "invalid")

    def test_quality_issue_overflow_is_a_retryable_format_error(self):
        raw = json.loads(quality_result())
        raw["issues"] = [quality_issue() for _ in range(21)]
        with self.assertRaises(generate.QualityReviewFormatError):
            generate._validated_quality_check(
                json.dumps(raw, ensure_ascii=False), VALID_BODY, [QUOTE]
            )

    def test_quality_checker_dimension_aliases_remain_blocking_accuracy_issues(self):
        sentence = generate._claim_sentences(VALID_BODY)[1]
        raw = quality_result([{
            "type": "objects_metrics",
            "article_quote": sentence,
            "evidence_quote": QUOTE,
            "note": "核查器指出对象与指标表述需要修正。",
        }])
        checked = generate._validated_quality_check(raw, VALID_BODY, [QUOTE])
        self.assertEqual(checked["issues"][0]["category"], "accuracy")
        self.assertEqual(checked["issues"][0]["reason"], "核查器指出对象与指标表述需要修正。")

    def test_quality_type_coverage_alias_is_normalized(self):
        raw = quality_result([{
            "type": "core_coverage",
            "article_quote": "",
            "evidence_quote": QUOTE,
            "note": "缺少核心发现。",
        }])
        checked = generate._validated_quality_check(raw, VALID_BODY, [QUOTE])
        self.assertEqual(checked["issues"][0]["category"], "coverage")

    def test_fake_article_or_evidence_quote_is_rejected(self):
        for issue in (quality_issue(article="作品里并没有这句话。"),
                      quality_issue(quote="The actual accuracy was 95% for all patients.")):
            with self.subTest(issue=issue):
                artifacts = {}
                with self.assertRaises(generate.LLMError):
                    self.run_generation(
                        [VALID_BODY, quality_result([issue]), quality_result([issue])], artifacts
                    )
                self.assertNotEqual(artifacts["quality_checks"][-1]["status"], "checked")

    def test_full_gate_never_treats_unlisted_supported_quote_as_pass(self):
        raw = json.loads(quality_result())
        raw["coverage"][0]["evidence_quote"] = "A claim with no source in this audit."
        with self.assertRaises(generate.CoverageAuditError):
            generate._validated_quality_check(
                json.dumps(raw, ensure_ascii=False), VALID_BODY, [QUOTE],
                {"RAW-CONTENT"}, allow_unquoted_issues=True,
            )

    def test_full_gate_does_not_ignore_untrusted_issue_for_a_sentence(self):
        sentence = generate._claim_sentences(VALID_BODY)[1]
        issue = quality_issue(article=sentence, quote="An unrelated evidence quote outside this audit.")
        with self.assertRaises(generate.CoverageAuditError):
            generate._validated_quality_check(
                quality_result([issue]), VALID_BODY, [QUOTE],
                {"RAW-CONTENT"}, allow_unquoted_issues=True,
            )

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

    def test_unknown_coverage_fact_id_is_a_retryable_format_error(self):
        raw = json.loads(quality_result())
        raw["coverage"][0]["fact_ids"] = ["C999-F99"]
        with self.assertRaises(generate.QualityCoverageFormatError):
            generate._validated_quality_check(
                json.dumps(raw, ensure_ascii=False), VALID_BODY, [QUOTE],
                {"C001-F01"},
            )

    def test_required_core_fact_must_be_linked_to_supported_article_sentence(self):
        required = {"C001-F01": {"claim": "核心限制", "quote": QUOTE}}
        raw = json.loads(quality_result())
        with self.assertRaises(generate.CoverageAuditError):
            generate._validated_quality_check(
                json.dumps(raw, ensure_ascii=False), VALID_BODY, [QUOTE],
                {"RAW-CONTENT", "C001-F01"}, required_facts=required,
            )
        raw["coverage"][0]["fact_ids"] = ["C001-F01"]
        checked = generate._validated_quality_check(
            json.dumps(raw, ensure_ascii=False), VALID_BODY, [QUOTE],
            {"RAW-CONTENT", "C001-F01"}, required_facts=required,
        )
        self.assertTrue(checked["quotes_verified"])

    def test_coverage_must_quote_the_whole_claim_sentence(self):
        sentence = generate._claim_sentences(VALID_BODY)[0]
        partial = sentence[:12]
        raw = json.dumps({
            "checked": sorted(generate._QUALITY_DIMENSIONS),
            "issues": [],
            "coverage": [{
                "dimension": "finding",
                "article_quote": partial,
                "evidence_quote": QUOTE,
                "fact_ids": ["RAW-CONTENT"],
                "status": "supported",
            }],
        }, ensure_ascii=False)
        with self.assertRaisesRegex(generate.LLMError, "完整匹配"):
            generate._validated_quality_check(raw, VALID_BODY, [QUOTE])

    def test_claim_sentence_does_not_split_question_inside_quotation(self):
        sentence = "例如在问题“法国首都是什么？”的语境下，答案确实蕴含较长的完整陈述。"
        self.assertIn(sentence, generate._claim_sentences("## 正文\n" + sentence))

    def test_unsupported_sentence_cleanup_preserves_required_sections(self):
        summary_sentence = generate._claim_sentences(VALID_BODY)[-1]
        cleaned, removed = generate._drop_unverified_sentences(VALID_BODY, [{
            "category": "accuracy", "article_quote": summary_sentence,
            "evidence_quote": "",
        }])
        self.assertEqual(cleaned, VALID_BODY)
        self.assertEqual(removed, [])

    def test_unannotated_sentence_cleanup_preserves_required_sections(self):
        cleaned, removed = generate._drop_unannotated_sentences(VALID_BODY)
        self.assertEqual(cleaned, VALID_BODY)
        self.assertEqual(removed, [])

    def test_repair_prompt_requires_reducing_overlong_fact_sentences(self):
        request = generate.prompts.REPAIR_PROMPT.format(
            errors="正文需要删去重复内容", evidence="核心事实", body=VALID_BODY,
        )
        self.assertIn("删除重复和次要事实句", request)
        self.assertIn("保留下方标记的所有核心事实ID", request)

    def test_coverage_allows_markdown_and_punctuation_formatting(self):
        sentence = generate._claim_sentences(VALID_BODY)[0]
        raw = json.dumps({
            "checked": sorted(generate._QUALITY_DIMENSIONS),
            "issues": [],
            "coverage": [{
                "dimension": "finding",
                "article_quote": "**" + sentence.replace("。", "") + "**",
                "evidence_quote": QUOTE,
                "fact_ids": ["RAW-CONTENT"],
                "status": "supported",
            }],
        }, ensure_ascii=False)
        with self.assertRaises(generate.CoverageAuditError):
            generate._validated_quality_check(raw, VALID_BODY, [QUOTE])

    def test_bad_structure_in_quality_revision_blocks_generation(self):
        artifacts = {}
        client = SequenceClient([VALID_BODY, quality_result([quality_issue()]), "## 引言\n只有引言。"])
        with self.assertRaisesRegex(generate.LLMError, "修订稿未通过"):
            generate.generate_with_llm(client, QUOTE, "M01", "某论文", 1200,
                                       quality_check=True, artifacts=artifacts)
        self.assertEqual(artifacts["quality_checks"][-1]["status"], "revision_invalid")
        self.assertEqual(len(client.requests), 3)

    def test_overlong_quality_revision_is_compressed_then_rechecked(self):
        overlong = VALID_BODY + "\n\n" + "这是一段重复说明。" * 400
        artifacts = {}
        client = SequenceClient([
            VALID_BODY,
            quality_result([quality_issue()]),
            overlong,
            VALID_BODY,
            quality_result(),
        ])
        result = generate.generate_with_llm(
            client, QUOTE, "M01", "某论文", 1200,
            quality_check=True, artifacts=artifacts,
        )
        self.assertEqual(result, VALID_BODY)
        self.assertEqual(len(client.requests), 5)
        self.assertIn("压缩到 2200 字以内", client.requests[3])
        self.assertEqual(artifacts["quality_checks"][-1]["status"], "checked")

    def test_coverage_failure_is_revised_and_rechecked(self):
        revised = VALID_BODY.replace(
            "比较同一问题的多个回答，先看含义是否一致。",
            "本次实验的成人样本准确率为80%。",
        )
        incomplete = json.dumps({
            "checked": sorted(generate._QUALITY_DIMENSIONS),
            "issues": [],
            "coverage": [],
        }, ensure_ascii=False)
        artifacts = {}
        body, client = self.run_generation(
            [VALID_BODY, incomplete, revised, quality_result(body=revised)], artifacts
        )
        self.assertEqual(body, revised)
        self.assertEqual(len(client.requests), 4)
        self.assertEqual(
            [check["status"] for check in artifacts["quality_checks"]],
            ["coverage_failed", "checked"],
        )

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
