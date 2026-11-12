# -*- coding: utf-8 -*-
"""真实交付风险回归：拒绝错稿、空壳、敏感文件及覆盖成功包。"""
from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import contracts, package, validate


def article(mid: str = "AI-01", source: str = "作者. 研究标题. 期刊, 2024.") -> str:
    return ("# 一个研究问题\n\n## 引言\n\n这项研究解释一个具体的问题。\n\n"
            "## 正文\n\n### 研究过程\n\n研究者比较数据并分析结果，结论只适用于研究中的对象。\n\n"
            "## 总结\n\n研究有助于理解这个问题，仍需要进一步验证。\n\n---\n\n"
            f"- **对应材料编号**：{mid}\n- **原文来源**：{source}\n")


class TestDocumentContracts(unittest.TestCase):
    def test_accepts_complete_chinese_article_and_excludes_provenance(self):
        text = article()
        self.assertEqual(contracts.document_errors(text, "AI-01"), [])
        self.assertNotIn("对应材料编号", contracts.body_text(text))
        self.assertNotIn("一个研究问题", contracts.body_text(text))

    def test_rejects_wrong_id_source_and_placeholder(self):
        self.assertTrue(contracts.document_errors(article("OTHER"), "AI-01"))
        self.assertTrue(contracts.document_errors(article(), "AI-01", "另一原文"))
        self.assertTrue(contracts.document_errors(article(source="待填写"), "AI-01"))
        self.assertTrue(contracts.document_errors(article(source=""), "AI-01"))

    def test_empty_or_heading_only_section_is_not_content(self):
        for replacement in ("", "### 只有小标题\n"):
            text = article().replace("这项研究解释一个具体的问题。", replacement)
            self.assertTrue(any("内容为空" in e for e in contracts.document_errors(text)))

    def test_rejects_wrong_order_and_duplicate_heading(self):
        text = article().replace("## 引言", "## 暂存").replace("## 总结", "## 引言").replace("## 暂存", "## 总结")
        self.assertTrue(any("顺序" in e for e in contracts.document_errors(text)))
        self.assertTrue(contracts.document_errors(article().replace("## 正文", "## 引言")))

    def test_rejects_overlong_and_english_body(self):
        self.assertTrue(any("2500" in e for e in contracts.document_errors(article().replace("这项研究", "内容" * 1300))))
        english = "# Title\n\n## 引言\n" + "English evidence. " * 20 + "\n## 正文\nEnglish discussion.\n## 总结\nEnglish conclusion.\n"
        english += "- **对应材料编号**：AI-01\n- **原文来源**：A real paper.\n"
        self.assertTrue(any("中文" in e for e in contracts.document_errors(english)))

    def test_expected_ids_check_names_and_four_required_materials(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "wrong.md").write_text(article(), encoding="utf-8")
            report = validate.Report()
            validate.check_documents(root, root / "materials", report,
                                     ["AI-01", "BIO-02", "IND-03", "MED-04"])
            self.assertTrue(any("一一对应" in e for e in report.errors))

    def test_invalid_utf8_is_reported_without_crash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "AI-01.md").write_bytes(b"\xff\xfe")
            report = validate.Report()
            validate.check_documents(root, root / "materials", report, ["AI-01"])
            self.assertTrue(any("UTF-8" in e for e in report.errors))


class TestAtomicPackage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.docs = self.root / "docs"
        self.source = self.root / "source_code"
        self.docs.mkdir()
        self.source.mkdir()
        (self.docs / "AI-01.md").write_text(article(), encoding="utf-8")
        for name, text in {"main.py": "print('ok')\n", "README.md": "运行说明", "requirements.txt": "pypdf>=4.0\n"}.items():
            (self.source / name).write_text(text, encoding="utf-8")

    def build(self):
        return package.build(self.root, self.docs, self.source, "作品.zip", ["AI-01"])[0]

    def test_package_matches_current_docs_and_does_not_duplicate_note(self):
        (self.docs / "提交说明.md").write_text("旧说明应被忽略但不删除", encoding="utf-8")
        output = self.build()
        expected = {"AI-01.md": (self.docs / "AI-01.md").read_bytes()}
        self.assertEqual(package.verify(output, expected, ["AI-01"]), [])
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.namelist().count("提交说明.md"), 1)
            self.assertNotIn("未经人工", archive.read("提交说明.md").decode("utf-8"))
        self.assertEqual((self.docs / "提交说明.md").read_text(encoding="utf-8"), "旧说明应被忽略但不删除")

    def test_old_zip_content_is_detected_against_current_docs(self):
        output = self.build()
        current = article().replace("一个研究问题", "修订后的研究问题").encode("utf-8")
        errors = package.verify(output, {"AI-01.md": current}, ["AI-01"])
        self.assertTrue(any("哈希不一致" in e for e in errors))

    def test_local_env_variants_excluded_and_empty_template_allowed(self):
        secret = "sk" + "-" + "a" * 24
        for name in (".env", ".env.local", ".env.backup"):
            (self.source / name).write_text(f"LLM_API_KEY={secret}\n", encoding="utf-8")
        (self.source / ".env.example").write_text("# 密钥仅在本地设置\nLLM_API_KEY=\n", encoding="utf-8")
        output = self.build()
        with zipfile.ZipFile(output) as archive:
            self.assertIn("source_code/.env.example", archive.namelist())
            self.assertNotIn("source_code/.env", archive.namelist())
            self.assertNotIn("source_code/.env.local", archive.namelist())
            self.assertNotIn("source_code/.env.backup", archive.namelist())

    def test_nonempty_template_and_secret_comments_are_rejected(self):
        for content in ("LLM_API_KEY=notempty\n", "# " + "sk" + "-" + "a" * 24):
            (self.source / ".env.example").write_text(content, encoding="utf-8")
            with self.assertRaises(ValueError):
                self.build()

    def test_secret_in_arbitrary_source_file_is_rejected(self):
        (self.source / "backup.data").write_text("sk" + "-" + "a" * 24, encoding="utf-8")
        with self.assertRaises(ValueError):
            self.build()

    def test_verify_rejects_env_in_injected_zip(self):
        output = self.build()
        with zipfile.ZipFile(output, "a") as archive:
            archive.writestr("source_code/.env.local", "KEY=notempty")
        self.assertTrue(any("禁止提交" in e for e in package.verify(output)))

    def test_preflight_failure_preserves_previous_package(self):
        output = self.build()
        previous = output.read_bytes()
        (self.docs / "AI-01.md").write_text("# 不完整", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.build()
        self.assertEqual(output.read_bytes(), previous)

    def test_readback_failure_preserves_previous_package(self):
        output = self.build()
        previous = output.read_bytes()
        with patch.object(package, "verify", return_value=["模拟归档内容错误"]):
            with self.assertRaises(ValueError):
                self.build()
        self.assertEqual(output.read_bytes(), previous)
        self.assertEqual(list(self.root.glob("*.tmp.zip")), [])

    def test_input_changed_during_build_is_not_published(self):
        output = self.build()
        previous = output.read_bytes()
        real_verify = package.verify
        def change_after_readback(*args, **kwargs):
            result = real_verify(*args, **kwargs)
            (self.docs / "AI-01.md").write_text(article().replace("一个研究问题", "新问题"), encoding="utf-8")
            return result
        with patch.object(package, "verify", side_effect=change_after_readback):
            with self.assertRaises(ValueError):
                self.build()
        self.assertEqual(output.read_bytes(), previous)

    def test_package_name_rejects_paths_without_overwriting_external_zip(self):
        project = self.root / "project"
        project.mkdir()
        outside = self.root / "outside.zip"
        original = b"existing external archive"
        outside.write_bytes(original)
        invalid_names = ["../outside.zip", "..\\outside.zip", str(outside),
                         "/outside.zip", "C:\\outside.zip", "C:outside.zip",
                         "nested/archive.zip", "nested\\archive.zip",
                         "version..zip", "..", "archive.txt"]
        for name in invalid_names:
            with self.subTest(name=name), self.assertRaises(ValueError):
                package.build(project, self.docs, self.source, name, ["AI-01"])
            self.assertEqual(outside.read_bytes(), original)
        self.assertEqual(list(project.iterdir()), [])

    def test_custom_material_directory_is_used_for_preflight(self):
        default_materials = self.root / "materials"
        custom_materials = self.root / "custom_materials"
        default_materials.mkdir()
        custom_materials.mkdir()
        (default_materials / "BIO-02.txt").write_text("旧材料", encoding="utf-8")
        (custom_materials / "AI-01.txt").write_text("当前材料", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.build()
        for materials in (custom_materials, Path("custom_materials")):
            output, _ = package.build(self.root, self.docs, self.source, "作品.zip",
                                      ["AI-01"], materials_dir=materials)
            self.assertEqual(package.verify(output, expected_ids=["AI-01"]), [])


if __name__ == "__main__":
    unittest.main()
