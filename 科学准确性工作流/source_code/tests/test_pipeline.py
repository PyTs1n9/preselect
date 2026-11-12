# -*- coding: utf-8 -*-
"""流水线单元测试。

不依赖网络与外部密钥，覆盖的是"12 号当天最可能出问题"的地方：
编码、格式、章节缺失、长材料切分、密钥泄漏、异常兜底。

运行方式（在 source_code 目录下）：
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import json
import re
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import extractors, generate, prompts, review, settings, validate  # noqa: E402


class TestExtractors(unittest.TestCase):
    def test_clean_text_collapses_whitespace(self):
        raw = "第一段\r\n\r\n\r\n\r\n第二段\u3000有全角空格\u200b"
        out = extractors.clean_text(raw)
        self.assertNotIn("\r", out)
        self.assertNotIn("\n\n\n", out)
        self.assertNotIn("\u200b", out)
        self.assertIn("第二段", out)

    def test_guess_material_id_from_filename(self):
        self.assertEqual(extractors.guess_material_id(Path("M03_某材料.pdf"), 1), "M03")
        self.assertEqual(extractors.guess_material_id(Path("A12-材料.docx"), 2), "A12")
        self.assertEqual(extractors.guess_material_id(Path("随便起的名字.md"), 7), "M07")

    def test_guess_material_id_from_organizer_naming(self):
        # 本届组委会命名形如 ges_docs_AI-01.pdf，编号是"领域代码-序号"
        self.assertEqual(extractors.guess_material_id(Path("ges_docs_AI-01.pdf"), 1), "AI-01")
        self.assertEqual(extractors.guess_material_id(Path("ges_docs_BIO-02.pdf"), 2), "BIO-02")
        self.assertEqual(extractors.guess_material_id(Path("ges_docs_IND-03.pdf"), 3), "IND-03")
        self.assertEqual(extractors.guess_material_id(Path("ges_docs_MED-04.pdf"), 4), "MED-04")

    def test_split_chunks_keeps_all_characters(self):
        text = "\n\n".join(f"第{i}段" + "内容" * 200 for i in range(12))
        chunks = extractors.split_chunks(text, size=1500)
        self.assertGreater(len(chunks), 1)
        joined = "".join(chunks).replace("\n", "")
        self.assertEqual(joined, text.replace("\n", ""))

    def test_split_chunks_handles_single_huge_paragraph(self):
        text = "长" * 5000
        chunks = extractors.split_chunks(text, size=1000)
        self.assertEqual(len(chunks), 5)
        self.assertEqual(len("".join(chunks)), 5000)

    def test_fingerprint_is_stable_and_short(self):
        a = extractors.fingerprint("同一段文字")
        b = extractors.fingerprint("同一段文字")
        c = extractors.fingerprint("另一段文字")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(len(a), extractors.FINGERPRINT_LEN)

    def test_reads_gbk_encoded_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gbk.txt"
            path.write_bytes("这是一段用国标编码保存的中文材料。".encode("gb18030"))
            self.assertIn("国标编码", extractors.extract_text(path))

    def test_reads_docx_paragraphs(self):
        xml = (
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body>'
            "<w:p><w:r><w:t>第一段正文内容，较长以便通过过滤。</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>第二段正文内容，同样保持足够长度。</w:t></w:r></w:p>"
            "</w:body></w:document>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.docx"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("[Content_Types].xml", "<Types/>")
                zf.writestr("word/document.xml", xml)
            text = extractors.extract_text(path)
            self.assertIn("第一段", text)
            self.assertIn("第二段", text)
            self.assertIn("\n", text)

    def test_rejects_unsupported_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "表格.xlsx"
            path.write_bytes(b"whatever")
            with self.assertRaises(extractors.ExtractError) as ctx:
                extractors.extract_text(path)
            self.assertIn("暂不支持", str(ctx.exception))

    def test_empty_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "空.txt"
            path.write_text("   \n\n  ", encoding="utf-8")
            with self.assertRaises(extractors.ExtractError):
                extractors.extract_text(path)


class TestGeneration(unittest.TestCase):
    def test_flatten_strips_leading_headings(self):
        paragraph = "2 数据与方法\n2.1 数据源\n采用 Landsat 8 影像，共 18 期。"
        out = generate._flatten(paragraph)
        self.assertTrue(out.startswith("采用"))
        self.assertNotIn("数据与方法", out)

    def test_flatten_joins_broken_lines_without_gaps(self):
        paragraph = "这是一句被换行\n切断的中文句子。"
        self.assertEqual(generate._flatten(paragraph), "这是一句被换行切断的中文句子。")

    def test_ensure_structure_rejects_missing_sections(self):
        body = "# 标题\n\n## 正文\n\n只有正文。"
        with self.assertRaises(generate.LLMError):
            generate._ensure_structure(body, "兜底标题")

    def test_ensure_structure_adds_title_when_missing(self):
        out = generate._ensure_structure("## 引言\n\n内容。\n\n## 正文\n内容。\n\n## 总结\n内容。", "给的标题")
        self.assertTrue(out.startswith("# 给的标题"))

    def test_extractive_output_has_all_sections(self):
        content = (
            "摘要：本研究测试抽取式引擎。\n\n"
            "1 研究背景\n城市化带来变化，这一点在多数研究中得到印证。\n\n"
            "2 方法\n采用随机森林方法分类，决策树数量为 300 棵。\n\n"
            "3 结论\n总体精度达到 91.3%，适用于中等比例尺。"
        )
        out = generate.generate_extractive(content, "M01", "某材料")
        for heading in ("# ", "## 引言", "## 正文", "## 总结"):
            self.assertIn(heading, out)

    def test_extractive_does_not_repeat_intro_in_body(self):
        content = (
            "摘要：这是引言段落的独特标记甲乙丙。\n\n"
            "第二段内容，讲述研究背景与意义所在。\n\n"
            "第三段内容，讲述方法细节与数据来源。\n\n"
            "第四段结论，总结全文主要发现与局限。"
        )
        out = generate.generate_extractive(content, "M01", "材料")
        self.assertEqual(out.count("独特标记甲乙丙"), 1)

    def test_assemble_appends_provenance(self):
        body = "# 标题\n\n## 引言\n引言。\n\n## 正文\n正文。\n\n## 总结\n总结。"
        out = generate.assemble(body, "M07", "某期刊", "原文", "extractive", "备用")
        self.assertIn("对应材料编号", out)
        self.assertIn("M07", out)
        self.assertIn("原文来源", out)
        self.assertIn("sha256:", out)
        self.assertIn(extractors.fingerprint("原文"), out)

    def test_assemble_strips_code_fence(self):
        body = "```markdown\n# 标题\n\n## 引言\n内容。\n\n## 正文\n内容。\n\n## 总结\n内容。\n```"
        out = generate.assemble(body, "M01", "来源", "原文", "llm", "备用")
        self.assertFalse(out.lstrip().startswith("```"))
        self.assertTrue(out.lstrip().startswith("# "))

    def test_assemble_tolerates_braces_in_content(self):
        body = "# 标题 {带花括号}\n\n## 引言\n公式 f(x)={1,2}。\n\n## 正文\n内容。\n\n## 总结\n内容。"
        out = generate.assemble(body, "M01", "来源", "原文", "llm", "备用")
        self.assertIn("{1,2}", out)

    def test_guess_title_prefers_original_first_line(self):
        # 文件名常是 "M01_材料"，不能拿它当文章标题；应取原文首行
        content = "基于多时相遥感影像的城市扩张变化检测研究\n\n摘要：本文以某市为例……"
        self.assertEqual(
            generate.guess_title(content, "M01_示例材料_遥感影像"),
            "基于多时相遥感影像的城市扩张变化检测研究",
        )

    def test_guess_title_strips_markdown_hash(self):
        content = "# 卷积神经网络的基本原理\n\n## 一、为什么需要卷积"
        self.assertEqual(
            generate.guess_title(content, "M02_材料"), "卷积神经网络的基本原理"
        )

    def test_guess_title_falls_back_when_first_line_is_a_sentence(self):
        content = "摘要：本研究以某市 2015 年至 2023 年的影像为数据源，共 18 期。"
        self.assertEqual(generate.guess_title(content, "M03_材料"), "M03_材料")

    def test_guess_title_skips_english_header_in_chinese_material(self):
        # PDF 首页的英文页眉不应成为中文材料的标题；
        # 后续行若是完整句子（带句号），同样不适合当标题，退回文件名。
        content = "RACING TRAINING\n\n本文讨论城市轨道交通的客流特征与用地关系。"
        self.assertEqual(generate.guess_title(content, "M04_材料"), "M04_材料")

    def test_to_paragraphs_splits_heading_lines(self):
        # PDF / docx 抽取后常只剩单换行，小标题必须单独成段才能被剥掉
        text = "1 引言\n这是引言正文，长度足够成为一段。\n2 数据与方法\n这是方法正文，同样长度足够。"
        paragraphs = generate._to_paragraphs(text)
        self.assertIn("2 数据与方法", paragraphs)
        self.assertTrue(any(p.startswith("这是方法正文") for p in paragraphs))

    def test_to_paragraphs_keeps_pdf_like_long_block_split(self):
        lines = [f"第{i}句内容较长用于验证按行重组是否生效，包含数据{i}。" for i in range(1, 15)]
        paragraphs = generate._to_paragraphs("\n".join(lines), target=100)
        self.assertGreater(len(paragraphs), 1)

    def test_split_head_loses_nothing(self):
        text = "第一句。第二句。第三句。第四句。"
        head, tail = generate._split_head(text, 8)
        self.assertEqual(head + tail, text)
        self.assertTrue(head.startswith("第一句"))

    def test_split_tail_loses_nothing(self):
        text = "第一句。第二句。第三句。第四句。"
        head, tail = generate._split_tail(text, 8)
        self.assertEqual(head + tail, text)
        self.assertTrue(tail.endswith("第四句。"))

    def test_extractive_keeps_tail_paragraph(self):
        # 结论段哪怕很长也必须完整落地，不能因为截断而消失
        tail = "结论段落起始。这份材料的核心发现是甲项指标达到百分之九十。适用边界限于中等规模城市。"
        content = "摘要：本材料研究某问题。\n\n方法段落内容，描述数据与流程。\n\n" + tail
        out = generate.generate_extractive(content, "M01", "材料")
        self.assertIn("适用边界限于中等规模城市", out)


class FakeClient:
    """假客户端：记录调用次数，模拟长材料的 map-reduce 路径。"""

    def __init__(self):
        self.calls = 0
        self.available = True

    def chat(self, system, user):
        self.calls += 1
        if "<<<CHUNK_START>>>" in user:
            chunk = user.split("<<<CHUNK_START>>>", 1)[1].split("<<<CHUNK_END>>>", 1)[0]
            match = re.search(r"\[PAGE (\d+)\]\s*([^\[]+)", chunk)
            quote = match.group(2).strip()[:30]
            fact = {"page": int(match.group(1)), "kind": "background", "claim": "材料背景", "quote": quote}
            fact.update({key: "" for key in ("subject", "metric", "value", "unit", "condition", "strength")})
            return json.dumps({"facts": [fact]}, ensure_ascii=False)
        return "## 引言\n引言段落。\n\n## 正文\n### 小节\n正文段落。\n\n## 总结\n总结段落。"


class TestLLMPath(unittest.TestCase):
    def test_short_material_uses_single_call(self):
        client = FakeClient()
        generate.generate_with_llm(client, "短材料内容。", "M01", "来源", 1200)
        self.assertEqual(client.calls, 1)

    def test_long_material_uses_map_reduce(self):
        client = FakeClient()
        content = "段落内容。" * 6000
        self.assertGreater(len(content), generate.SINGLE_CALL_LIMIT)
        out = generate.generate_with_llm(client, content, "M01", "来源", 1200)
        self.assertGreater(client.calls, 2)
        self.assertIn("## 引言", out)
        self.assertIn("## 总结", out)

    def test_maybe_compress_only_when_overlong(self):
        client = FakeClient()
        # 不超限：不触发压缩
        self.assertEqual(generate._maybe_compress(client, "短正文"), "短正文")
        self.assertEqual(client.calls, 0)
        # 超 2500 字：触发一次压缩
        overlong = "长" * (generate.BODY_LIMIT + 1)
        generate._maybe_compress(client, overlong)
        self.assertEqual(client.calls, 1)


class TestPromptSafety(unittest.TestCase):
    def test_system_prompt_states_hard_constraints(self):
        text = prompts.SYSTEM_PROMPT
        for keyword in ("不得虚构", "不得改变原文", "只依据给定的材料"):
            self.assertIn(keyword, text)

    def test_doc_prompt_accepts_brace_content(self):
        rendered = prompts.DOC_PROMPT.format(
            material_id="M01",
            source_name="来源",
            target_words=1000,
            content="包含 {花括号} 的正文 f(x)={1}",
        )
        self.assertIn("{花括号}", rendered)


class TestValidate(unittest.TestCase):
    def test_detects_api_key(self):
        # 假密钥在运行时拼出来，避免测试文件本身被密钥扫描误报
        fake_key = "sk" + "-" + "a" * 24
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "leak.py").write_text(f'K = "{fake_key}"', encoding="utf-8")
            report = validate.Report()
            validate.check_secrets(root, report)
            self.assertTrue(report.failed)

    def test_ignores_clean_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ok.py").write_text('K = os.environ["API_KEY"]', encoding="utf-8")
            report = validate.Report()
            validate.check_secrets(root, report)
            self.assertFalse(report.failed)

    def test_reports_missing_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = root / "docs"
            docs.mkdir()
            (docs / "a.md").write_text("# 标题\n\n只有这一段。", encoding="utf-8")
            report = validate.Report()
            validate.check_documents(docs, root / "materials", report)
            self.assertTrue(report.failed)

    def test_reports_count_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs, materials = root / "docs", root / "materials"
            docs.mkdir()
            materials.mkdir()
            body = "# 标题\n\n## 引言\n内容。\n\n## 正文\n内容。\n\n## 总结\n内容。\n\n对应材料编号：M01\n原文来源：某处\n"
            (docs / "a.md").write_text(body, encoding="utf-8")
            (materials / "M01.txt").write_text("材料内容", encoding="utf-8")
            (materials / "M02.txt").write_text("材料内容", encoding="utf-8")
            report = validate.Report()
            validate.check_documents(docs, materials, report)
            self.assertTrue(report.failed)
            self.assertTrue(any("一一对应" in e for e in report.errors))

    def test_hidden_files_not_counted_as_materials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs, materials = root / "docs", root / "materials"
            docs.mkdir()
            materials.mkdir()
            (materials / ".gitkeep").write_text("", encoding="utf-8")
            body = "# 标题\n\n## 引言\n内容。\n\n## 正文\n内容。\n\n## 总结\n内容。\n\n对应材料编号：M01\n原文来源：某处\n"
            (docs / "M01.md").write_text(body, encoding="utf-8")
            (materials / "M01.txt").write_text("材料内容", encoding="utf-8")
            report = validate.Report()
            validate.check_documents(docs, materials, report)
            self.assertFalse(report.failed, report.render())


class TestReview(unittest.TestCase):
    """质量评审的数字核对。

    数值检查只提供候选证据与风险，不能代替对象、指标和条件的语义核验。
    """

    SOURCE = (
        "2015 年建设用地面积为 312.6 平方公里，2023 年为 487.9 平方公里，"
        "八年间增加 175.3 平方公里，增幅为 56.1%。总体分类精度为 91.3%，"
        "Kappa 系数为 0.87。"
    )

    def test_numbers_present_in_source_are_exact(self):
        doc = "面积从312.6平方公里增至487.9平方公里。"
        report = review.check_numbers(doc, self.SOURCE)
        self.assertEqual(report.missing, [])
        self.assertEqual(len(report.exact), 2)

    def test_fabricated_number_is_caught(self):
        doc = "面积从312.6平方公里增至487.9平方公里，样本量达47万个像元。"
        report = review.check_numbers(doc, self.SOURCE)
        self.assertIn("47", report.missing)

    def test_tampered_number_is_caught(self):
        # 原文 56.1%，写成 63.8% 属于篡改，必须报出来
        doc = "增幅为63.8%。"
        report = review.check_numbers(doc, self.SOURCE)
        self.assertIn("63.8", report.missing)

    def test_rounding_counts_as_approx_not_missing(self):
        # 0.87 写成 0.9 属正常取舍，归入近似而非编造
        doc = "Kappa 系数为 0.9。"
        report = review.check_numbers(doc, self.SOURCE)
        self.assertEqual(report.missing, [])
        self.assertIn("0.9", report.approx)

    def test_single_digit_noise_is_skipped(self):
        doc = "1 研究背景\n\n研究分 3 个部分。"
        report = review.check_numbers(doc, self.SOURCE)
        self.assertEqual(report.missing, [])
        self.assertEqual(report.total, 0)

    def test_heading_numbers_are_ignored(self):
        # 章节序号不是数据，不该被当成"编造的数值"
        doc = "### 3.1 面积变化\n\n见上表。"
        report = review.check_numbers(doc, self.SOURCE)
        self.assertEqual(report.missing, [])
        self.assertEqual(report.total, 0)

    def test_provenance_block_is_stripped(self):
        doc = (
            "# 标题\n\n正文提到了 312.6。\n\n---\n\n"
            "- **对应材料编号**：M01\n"
            "- **原文内容指纹**：sha256:1a112ee3670be824\n"
            "- **生成时间**：2026-11-10 20:19:50\n"
        )
        body = review.strip_provenance(doc)
        self.assertNotIn("sha256", body)
        self.assertNotIn("2026", body)
        self.assertIn("312.6", body)

    def test_stats_shape(self):
        doc = (
            "# 标题\n\n## 引言\n\n"
            "这是一段足够长的正文内容，用来验证段落统计是否能够正常工作，长度需要超过三十个字符。\n\n"
            "### 小节\n\n"
            "也就是说，这是一段解释性文字，长度同样需要超过三十个字符，才能被统计为独立段落。"
        )
        s = review.stats(doc)
        self.assertEqual(s.h3_sections, 1)
        self.assertGreaterEqual(s.paragraphs, 2)
        self.assertGreaterEqual(s.annotated_terms, 1)


class TestSettings(unittest.TestCase):
    """配置解析：.env 读取与服务商预设。

    这两块出错的表现都是「明明配了却报没配」，最难自查，所以单独覆盖。
    """

    def test_load_dotenv_reads_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("LLM_API_KEY=sk-test-123\n", encoding="utf-8")
            os.environ.pop("LLM_API_KEY", None)
            loaded = settings.load_dotenv(env)
            self.assertEqual(loaded, ["LLM_API_KEY"])
            self.assertEqual(os.environ["LLM_API_KEY"], "sk-test-123")
            os.environ.pop("LLM_API_KEY", None)

    def test_existing_env_wins(self):
        """临时 set 的环境变量应压过 .env，方便排查问题时不改文件。"""
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("LLM_API_KEY=from-file\n", encoding="utf-8")
            os.environ["LLM_API_KEY"] = "from-shell"
            settings.load_dotenv(env)
            self.assertEqual(os.environ["LLM_API_KEY"], "from-shell")
            os.environ.pop("LLM_API_KEY", None)

    def test_blank_value_is_skipped(self):
        """空值不能被当成有效密钥，否则会换来一个看不懂的 401。"""
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("LLM_API_KEY=\n# 注释\n", encoding="utf-8")
            os.environ.pop("LLM_API_KEY", None)
            self.assertEqual(settings.load_dotenv(env), [])
            self.assertNotIn("LLM_API_KEY", os.environ)

    def test_bom_and_quotes(self):
        """记事本另存为 UTF-8 会带 BOM；带引号的写法也该识别。"""
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_bytes('\ufeffLLM_API_KEY="sk-quoted"\n'.encode("utf-8"))
            os.environ.pop("LLM_API_KEY", None)
            settings.load_dotenv(env)
            self.assertEqual(os.environ.get("LLM_API_KEY"), "sk-quoted")
            os.environ.pop("LLM_API_KEY", None)

    def test_resolve_preset(self):
        config = {
            "llm": {"preset": "deepseek", "base_url": "", "model": ""},
            "llm_presets": {
                "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"}
            },
        }
        cfg, name, note = settings.resolve_llm(config)
        self.assertEqual(name, "deepseek")
        self.assertEqual(cfg["base_url"], "https://api.deepseek.com/v1")
        self.assertEqual(cfg["model"], "deepseek-chat")
        self.assertEqual(note, "")

    def test_explicit_values_override_preset(self):
        config = {
            "llm": {"preset": "deepseek", "base_url": "http://127.0.0.1:8000/v1", "model": "local"},
            "llm_presets": {
                "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"}
            },
        }
        cfg, _, _ = settings.resolve_llm(config)
        self.assertEqual(cfg["base_url"], "http://127.0.0.1:8000/v1")
        self.assertEqual(cfg["model"], "local")

    def test_unknown_preset_reports_instead_of_crashing(self):
        config = {"llm": {"preset": "不存在的服务商"}, "llm_presets": {"deepseek": {}}}
        cfg, _, note = settings.resolve_llm(config)
        self.assertTrue(note)
        self.assertEqual(cfg["base_url"], "")

    def test_describe_key_hides_secret(self):
        # 用不足以触发密钥正则的短样例：否则自检会把这个测试文件本身判成泄漏源
        text = settings.describe_key("sk-demo-key-abcd")
        self.assertNotIn("sk-demo-key-ab", text)
        self.assertIn("abcd", text)
        self.assertEqual(settings.describe_key(""), "未设置")


class TestLLMCallBehaviour(unittest.TestCase):
    """调用失败时的行为：鉴权错误不重试，网络抖动才重试。"""

    def _client_with_fake_requests(self, status_code: int, calls: list):
        class FakeResponse:
            def __init__(self, code):
                self.status_code = code
                self.text = "fake body"

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}

        fake = types.SimpleNamespace(
            post=lambda *a, **k: (calls.append(k), FakeResponse(status_code))[1]
        )
        saved = sys.modules.get("requests")
        sys.modules["requests"] = fake
        self.addCleanup(
            lambda: sys.modules.__setitem__("requests", saved)
            if saved is not None
            else sys.modules.pop("requests", None)
        )
        return generate.LLMClient({"base_url": "http://example.invalid/v1", "model": "m"})

    def test_auth_error_is_not_retried(self):
        """401 说明密钥不对，重试三次只是白白拖时间，还会让人误判成网络问题。"""
        os.environ["LLM_API_KEY"] = "sk-test"
        self.addCleanup(lambda: os.environ.pop("LLM_API_KEY", None))
        calls: list = []
        client = self._client_with_fake_requests(401, calls)
        with self.assertRaises(generate.LLMError):
            client.chat("system", "user")
        self.assertEqual(len(calls), 1)

    def test_missing_key_raises_before_network(self):
        os.environ.pop("LLM_API_KEY", None)
        calls: list = []
        client = self._client_with_fake_requests(200, calls)
        with self.assertRaises(generate.LLMError) as ctx:
            client.chat("system", "user")
        self.assertIn("LLM_API_KEY", str(ctx.exception))
        self.assertEqual(len(calls), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
