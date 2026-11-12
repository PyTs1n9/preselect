"""隔离目录验证真实入口的失败保护与整轮提交，不调用模型服务。"""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import main
from pipeline import generate


BODY = "## 引言\n这项研究讨论信息是否可靠。\n\n## 正文\n### 方法\n研究比较多次回答的含义，判断它们是否稳定。\n\n## 总结\n稳定的回答也可能错误，仍须核查事实。"


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source_code"
        self.docs = self.root / "docs"
        self.materials = self.root / "materials"
        for path in (self.source, self.docs, self.materials):
            path.mkdir()
        (self.materials / "M01.txt").write_text("研究比较多次回答的含义。稳定回答也可能错误。", encoding="utf-8")
        (self.docs / "M01.md").write_bytes(b"previous successful document")
        self.config = {"llm": {"base_url": "https://example.invalid/v1", "model": "test"},
                       "review": {"required_material_ids": ["M01"]},
                       "document": {"overrides": {"M01": {"source": "测试来源", "title": "测试文章"}}}}
        for target, value in (("PROJECT_ROOT", self.root), ("SOURCE_DIR", self.source)):
            mock = patch.object(main, target, value)
            mock.start()
            self.addCleanup(mock.stop)
        env = patch.dict(os.environ, {"LLM_API_KEY": "test-only"})
        env.start()
        self.addCleanup(env.stop)

    def run_generate(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return main.cmd_generate(self.config)

    def test_console_streams_are_configured_for_utf8(self):
        stdout, stderr = Mock(), Mock()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            main._configure_console_encoding()
        stdout.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
        stderr.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")

    def test_interrupted_review_keeps_partial_report_and_returns_failure(self):
        def interrupted(*args, **kwargs):
            print("AI-01 已评审；其余文章未完成", flush=True)
            raise KeyboardInterrupt

        with patch.object(main, "_run_review", side_effect=interrupted):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main.cmd_review(self.config), 130)
        reports = list((self.source / "result").glob("review_*.md"))
        self.assertEqual(len(reports), 1)
        text = reports[0].read_text(encoding="utf-8")
        self.assertIn("AI-01 已评审", text)
        self.assertIn("评审未完成", text)

    def test_api_failure_preserves_previous_documents(self):
        with patch.object(generate, "generate_with_llm", side_effect=generate.LLMError("模拟失败")):
            self.assertEqual(self.run_generate(), 1)
        self.assertEqual((self.docs / "M01.md").read_bytes(), b"previous successful document")

    def test_missing_key_never_calls_model_or_touches_old_documents(self):
        with patch.dict(os.environ, {"LLM_API_KEY": ""}), patch.object(generate, "generate_with_llm") as model:
            self.assertEqual(self.run_generate(), 1)
            model.assert_not_called()
        self.assertEqual((self.docs / "M01.md").read_bytes(), b"previous successful document")

    def test_success_keeps_previous_version_and_records_provenance(self):
        with patch.object(generate, "generate_with_llm", return_value=BODY):
            self.assertEqual(self.run_generate(), 0)
        self.assertIn("研究比较", (self.docs / "M01.md").read_text(encoding="utf-8"))
        backups = list((self.source / "result" / "runs").glob("*/previous_docs/M01.md"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"previous successful document")
        import json
        record = json.loads((self.docs / "_manifest.json").read_text(encoding="utf-8"))[0]
        self.assertEqual(record["output_sha256"], main._sha256(self.docs / "M01.md"))
        self.assertEqual(record["source_sha256"], main._sha256(self.materials / "M01.txt"))
        self.assertNotIn("test-only", str(record))

    def test_partial_round_failure_does_not_replace_first_document(self):
        (self.materials / "M02.txt").write_text("第二份材料提供比较结果和适用条件。", encoding="utf-8")
        (self.docs / "M02.md").write_bytes(b"second old document")
        self.config["review"]["required_material_ids"].append("M02")
        with patch.object(generate, "generate_with_llm", side_effect=[BODY, generate.LLMError("第二份失败")]):
            self.assertEqual(self.run_generate(), 1)
        self.assertEqual((self.docs / "M01.md").read_bytes(), b"previous successful document")
        self.assertEqual((self.docs / "M02.md").read_bytes(), b"second old document")

    def test_concurrent_document_edit_is_not_overwritten(self):
        def external_edit(*args, **kwargs):
            (self.docs / "M01.md").write_bytes(b"user edited while generating")
            return BODY
        with patch.object(generate, "generate_with_llm", side_effect=external_edit):
            self.assertEqual(self.run_generate(), 1)
        self.assertEqual((self.docs / "M01.md").read_bytes(), b"user edited while generating")

    def test_publish_failure_restores_original_directory(self):
        original = Path.rename
        def fail_stage(path, target):
            if path.name == "generated_docs":
                raise OSError("模拟目录交换失败")
            return original(path, target)
        with patch.object(generate, "generate_with_llm", return_value=BODY), patch.object(Path, "rename", fail_stage):
            self.assertEqual(self.run_generate(), 1)
        self.assertEqual((self.docs / "M01.md").read_bytes(), b"previous successful document")

    def test_output_may_not_overlap_source(self):
        self.config["output_dir"] = "source_code"
        with patch.object(generate, "generate_with_llm") as model:
            self.assertEqual(self.run_generate(), 1)
            model.assert_not_called()

    def test_duplicate_scores_are_not_summed(self):
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            main._print_content_scores("## 科学准确性：15/15\n## 科学准确性：15/15\n## 科普表达：20/20")
        self.assertNotIn("模拟内容分", capture.getvalue())

    def test_invalid_manifest_is_reported_not_crashed(self):
        (self.docs / "_manifest.json").write_text(
            '[{"doc_file":["M01.md"],"material_id":"M01","source_file":"M01.txt"}]', encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main._run_review(self.config, offline=True), 1)

    def test_review_checks_same_source_as_validate(self):
        text = generate.assemble(BODY, "M01", "错误来源", "原文", "llm", "测试标题")
        (self.docs / "M01.md").write_text(text, encoding="utf-8")
        for name in ("main.py", "requirements.txt", "README.md"):
            (self.source / name).write_text("test", encoding="utf-8")
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            self.assertEqual(main._run_review(self.config, offline=True), 1)
        self.assertIn("原文来源与本次材料配置不一致", capture.getvalue())


if __name__ == "__main__":
    unittest.main()
