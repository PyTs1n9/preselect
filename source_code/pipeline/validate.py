# -*- coding: utf-8 -*-
"""提交前自检：把评分规则里能机器判定的部分全部检查一遍。

对应赛项要求
------------
- 每份材料对应 1 份 Markdown，含标题 / 引言 / 正文 / 总结
- 文末注明对应材料编号及原文来源
- 源码目录统一命名 source_code，内含 Python 源码、依赖配置、运行说明
- 源码、配置、说明文档中不得出现真实 API Key、访问令牌等敏感凭证
- 压缩包不得拆分，文档与代码必须打包在一起
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import extractors

# 常见密钥形态：OpenAI/DeepSeek 风格、GitHub、AWS、JWT、私钥块
SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "疑似 API Key（sk- 开头）"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "疑似 GitHub Token"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "疑似 AWS Access Key"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\."), "疑似 JWT 令牌"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "疑似私钥文件内容"),
]

REQUIRED_HEADINGS = ["## 引言", "## 正文", "## 总结"]
REQUIRED_TAIL_FIELDS = ["对应材料编号", "原文来源"]

SCAN_SUFFIXES = {".py", ".json", ".md", ".txt", ".yaml", ".yml", ".env", ".ini", ".cfg"}
# materials 是组委会原始材料，不属于提交物，不纳入扫描
SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "materials"}


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.passed: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def ok(self, msg: str) -> None:
        self.passed.append(msg)

    @property
    def failed(self) -> bool:
        return bool(self.errors)

    def render(self) -> str:
        lines = ["", "=" * 62, "提交前自检报告", "=" * 62]
        for msg in self.passed:
            lines.append(f"  [通过] {msg}")
        for msg in self.warnings:
            lines.append(f"  [警告] {msg}")
        for msg in self.errors:
            lines.append(f"  [错误] {msg}")
        lines.append("-" * 62)
        lines.append(
            f"  通过 {len(self.passed)} 项 / 警告 {len(self.warnings)} 项 / "
            f"错误 {len(self.errors)} 项"
        )
        lines.append("  结论：" + ("不可提交，请先修复错误项" if self.failed else "可以提交"))
        lines.append("=" * 62 + "\n")
        return "\n".join(lines)


def _iter_scan_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        # .env 是放密钥的地方，且打包时已被排除、不会进入提交物，
        # 扫它只会制造假警报、把打包步骤卡死。真正要防的是密钥写进被提交的文件。
        if path.name == ".env" or path.name.startswith(".env."):
            continue
        if path.suffix.lower() not in SCAN_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def check_documents(docs_dir: Path, materials_dir: Path, report: Report) -> None:
    if not docs_dir.exists():
        report.error(f"文档目录不存在：{docs_dir}")
        return

    docs = sorted(p for p in docs_dir.glob("*.md") if not p.name.startswith("_"))
    if not docs:
        report.error(f"{docs_dir.name}/ 下没有任何 .md 文档")
        return
    report.ok(f"文档数量：{len(docs)} 份")

    materials = (
        [
            p
            for p in materials_dir.iterdir()
            if p.is_file()
            and not p.name.startswith(".")
            and p.suffix.lower() in extractors.SUPPORTED_SUFFIXES
        ]
        if materials_dir.exists()
        else []
    )
    if materials:
        if len(docs) != len(materials):
            report.error(
                f"材料 {len(materials)} 份，文档 {len(docs)} 份，未做到一一对应"
            )
        else:
            report.ok(f"材料与文档一一对应（各 {len(materials)} 份）")
    else:
        report.warn("materials/ 为空，无法校验「材料—文档」一一对应关系")

    structure_ok = True
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        missing = [h for h in REQUIRED_HEADINGS if h not in text]
        if missing:
            report.error(f"{doc.name} 缺少章节：{'、'.join(missing)}")
            structure_ok = False
        if not text.lstrip().startswith("# "):
            report.error(f"{doc.name} 缺少一级标题")
            structure_ok = False

        tail = text[-800:]  # 只看文末，避免正文里出现同名词造成误判
        absent = [f for f in REQUIRED_TAIL_FIELDS if f not in tail]
        if absent:
            report.error(f"{doc.name} 文末缺少溯源字段：{'、'.join(absent)}")
            structure_ok = False

        # 赛项要求「正文不超过 2500 字，标题及参考来源不计入」。
        # 正文 = 全文去掉一级标题与文末溯源块。
        idx = text.find("- **对应材料编号**")
        body = text[:idx] if idx != -1 else text
        lines = body.split("\n")
        if lines and lines[0].lstrip().startswith("# "):
            body = "\n".join(lines[1:])
        if len(body.strip()) > 2500:
            report.error(
                f"{doc.name} 正文 {len(body.strip())} 字，超过 2500 字上限"
            )

        if len(text) < 800:
            report.warn(f"{doc.name} 篇幅偏短（{len(text)} 字符），可能影响内容完整性")

    if structure_ok:
        report.ok("全部文档结构合规（标题 / 引言 / 正文 / 总结 + 文末溯源）")


def check_secrets(root: Path, report: Report) -> None:
    hits: list[str] = []
    for path in _iter_scan_files(root):
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern, label in SECRET_PATTERNS:
            for match in pattern.finditer(content):
                snippet = match.group(0)[:12]
                hits.append(f"{path.relative_to(root)} → {label}（{snippet}…）")
    if hits:
        for hit in hits:
            report.error(f"发现疑似敏感凭证：{hit}")
    else:
        report.ok("源码与文档中未发现 API Key / 访问令牌（.env 不参与扫描，打包时已排除）")


def check_source_code(source_dir: Path, report: Report) -> None:
    if not source_dir.exists():
        report.error(f"源码目录不存在：{source_dir}")
        return
    if source_dir.name != "source_code":
        report.error(f"源码目录必须命名为 source_code，当前为 {source_dir.name}")
    else:
        report.ok("源码目录命名为 source_code")

    py_files = list(source_dir.rglob("*.py"))
    if not py_files:
        report.error("source_code 下没有 Python 源码")
    else:
        report.ok(f"Python 源码：{len(py_files)} 个文件")

    if not (source_dir / "requirements.txt").exists():
        report.error("source_code/requirements.txt 缺失（依赖配置）")
    else:
        report.ok("依赖配置 requirements.txt 存在")

    if not (source_dir / "README.md").exists():
        report.error("source_code/README.md 缺失（运行说明）")
    else:
        report.ok("运行说明 README.md 存在")

    env_files = list(source_dir.glob(".env"))
    if env_files:
        report.warn("source_code 下存在 .env 文件，打包时会被自动排除，请勿提交真实密钥")


def check_package_structure(root: Path, report: Report) -> None:
    """模拟解压后的结构，确认压缩包顶层同时包含文档与源码。"""
    docs = list((root / "docs").glob("*.md")) if (root / "docs").exists() else []
    has_source = (root / "source_code").exists()
    if docs and has_source:
        report.ok("压缩包顶层将同时包含科普文档与 source_code/")
    else:
        report.error("压缩包结构不完整：需要同时包含 .md 文档与 source_code/")


def run(project_root: Path, docs_dir: Path, materials_dir: Path,
        source_dir: Path) -> Report:
    report = Report()
    check_documents(docs_dir, materials_dir, report)
    check_source_code(source_dir, report)
    check_secrets(project_root, report)
    check_package_structure(project_root, report)

    manifest = docs_dir / "_manifest.json"
    if manifest.exists():
        try:
            records = json.loads(manifest.read_text(encoding="utf-8"))
            report.ok(f"生成清单 _manifest.json 可解析（{len(records)} 条记录）")
        except json.JSONDecodeError as exc:
            report.error(f"_manifest.json 格式损坏：{exc}")
    return report
