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

from . import contracts, extractors

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
# materials 是组委会原始材料，result 是本地评审输出，均不属于提交物，不纳入扫描
SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "materials", "result"}


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
        lines.append("  结论：" + ("不可打包，请先修复错误项" if self.failed else "本地确定性检查通过；事实质量及最终 ZIP 仍须核验"))
        lines.append("=" * 62 + "\n")
        return "\n".join(lines)


def _iter_scan_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        # .env 是放密钥的地方，且打包时已被排除、不会进入提交物，
        # 扫它只会制造假警报、把打包步骤卡死。真正要防的是密钥写进被提交的文件。
        if path.name.lower() == ".env" or (path.name.lower().startswith(".env.") and path.name.lower() != ".env.example"):
            continue
        if path.suffix.lower() not in SCAN_SUFFIXES and path.name.lower() != ".env.example":
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def document_paths(docs_dir: Path) -> list[Path]:
    return sorted(p for p in docs_dir.glob("*.md")
                  if p.is_file() and not p.name.startswith("_") and p.name != "提交说明.md")


def check_documents(docs_dir: Path, materials_dir: Path, report: Report,
                    expected_ids: list[str] | None = None,
                    expected_sources: dict[str, str] | None = None) -> None:
    if not docs_dir.exists():
        report.error(f"文档目录不存在：{docs_dir}")
        return

    docs = document_paths(docs_dir)
    if not docs:
        report.error(f"{docs_dir.name}/ 下没有任何 .md 文档")
        return
    report.ok(f"文档数量：{len(docs)} 份")

    materials = (
        sorted([
            p
            for p in materials_dir.iterdir()
            if p.is_file()
               and not p.name.startswith(".")
               and p.suffix.lower() in extractors.SUPPORTED_SUFFIXES
        ])
        if materials_dir.exists()
        else []
    )
    inferred = [extractors.guess_material_id(p, i) for i, p in enumerate(materials, 1)]
    if len(set(inferred)) != len(inferred):
        report.error("原始材料编号重复，无法确认一一对应关系")
    required = list(expected_ids) if expected_ids is not None else inferred
    if len(set(required)) != len(required):
        report.error("期望材料编号重复")
    if required:
        expected_names = {f"{mid}.md" for mid in required}
        actual_names = {doc.name for doc in docs}
        missing, extra = sorted(expected_names - actual_names), sorted(actual_names - expected_names)
        if missing or extra:
            report.error("材料与文档未做到一一对应："
                         + (f"缺少 {'、'.join(missing)}；" if missing else "")
                         + (f"多余或错名 {'、'.join(extra)}" if extra else ""))
        else:
            report.ok(f"材料编号与文档名一一对应（{len(required)} 份）")
        if expected_ids is not None and inferred and set(inferred) != set(required):
            report.error("当前材料编号集合与本次要求不一致")
    elif not materials:
        report.warn("materials/ 为空，无法校验「材料—文档」一一对应关系")

    structure_ok = True
    for doc in docs:
        try:
            text = doc.read_text(encoding="utf-8-sig")
        except (UnicodeError, OSError) as exc:
            report.error(f"{doc.name} 不是可读取的 UTF-8 文档：{type(exc).__name__}")
            structure_ok = False
            continue
        errors = contracts.document_errors(text, material_id=doc.stem,
                                           source_name=(expected_sources or {}).get(doc.stem))
        for error in errors:
            report.error(f"{doc.name}：{error}")
        structure_ok = structure_ok and not errors
        if len(contracts.body_text(text)) < 800:
            report.warn(f"{doc.name} 篇幅偏短，需人工确认核心内容覆盖；不据此固定扣分")

    if structure_ok:
        report.ok("全部文档结构合规（标题 / 引言 / 正文 / 总结 + 文末溯源）")


def secret_errors(content: str) -> list[str]:
    """仅返回凭证类型，不把疑似密钥内容写到日志。"""
    return [label for pattern, label in SECRET_PATTERNS if pattern.search(content)]


def env_template_errors(content: str) -> list[str]:
    """允许注释、空行和空值赋值；模板中的注释也参与凭证扫描。"""
    errors = secret_errors(content)
    for number, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not re.fullmatch(r"(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*(?:\"\"|'')?\s*(?:#.*)?", stripped):
            errors.append(f".env.example 第 {number} 行不是空值模板")
    return errors


def check_secrets(root: Path, report: Report) -> None:
    hits: list[str] = []
    for path in _iter_scan_files(root):
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        problems = env_template_errors(content) if path.name.lower() == ".env.example" else secret_errors(content)
        hits.extend(f"{path.relative_to(root)} → {label}" for label in problems)
    if hits:
        for hit in hits:
            report.error(f"发现疑似敏感凭证：{hit}")
    else:
        report.ok("扫描未发现匹配的敏感凭证；本地 .env 及其变体不提交，空模板单独检查")


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


def check_package_structure(root: Path, report: Report, docs_dir: Path | None = None,
                            source_dir: Path | None = None) -> None:
    """模拟解压后的结构，确认压缩包顶层同时包含文档与源码。"""
    docs = document_paths(docs_dir or root / "docs")
    has_source = (source_dir or root / "source_code").exists()
    if docs and has_source:
        report.ok("待打包输入包含科普文档与 source_code/；最终 ZIP 由打包步骤读回核验")
    else:
        report.error("压缩包结构不完整：需要同时包含 .md 文档与 source_code/")


def run(project_root: Path, docs_dir: Path, materials_dir: Path,
        source_dir: Path, expected_ids: list[str] | None = None,
        expected_sources: dict[str, str] | None = None) -> Report:
    report = Report()
    check_documents(docs_dir, materials_dir, report, expected_ids, expected_sources)
    check_source_code(source_dir, report)
    # 只扫描拟提交的源码与成稿；临时环境/原文/评审报告不属于提交物。
    check_secrets(source_dir, report)
    check_secrets(docs_dir, report)
    check_package_structure(project_root, report, docs_dir, source_dir)

    manifest = docs_dir / "_manifest.json"
    if manifest.exists():
        try:
            records = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
                report.error("_manifest.json 应为记录对象的列表")
            else:
                report.ok(f"生成清单 _manifest.json 可解析（{len(records)} 条记录）")
        except (json.JSONDecodeError, UnicodeError, OSError) as exc:
            report.error(f"_manifest.json 格式损坏：{exc}")
    return report
