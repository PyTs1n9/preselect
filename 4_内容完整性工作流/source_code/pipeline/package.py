# -*- coding: utf-8 -*-
"""预检、构建临时 ZIP、读回核验，最后替换上一份成功的提交包。"""
from __future__ import annotations

import hashlib
import os
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import contracts, validate

EXCLUDE_DIRS = {"__pycache__", ".git", ".venv", "venv", ".idea", ".vscode", ".pytest_cache", "result"}
EXCLUDE_FILES = {".env", ".DS_Store", "Thumbs.db"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".log", ".zip"}


def _should_skip(path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return True
    name = path.name.lower()
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    return path.name in EXCLUDE_FILES or path.suffix.lower() in EXCLUDE_SUFFIXES


def _readme_bytes(docs: list[Path], source_count: int) -> bytes:
    lines = ["# 提交说明", "", f"打包时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "",
             "## 文档清单", "", "| 文件名 |", "| --- |"]
    lines.extend(f"| {doc.name} |" for doc in docs)
    lines.extend([
        "", "## 结构与复现", "",
        "压缩包顶层包含上述 Markdown 文档、本说明及 source_code/。",
        f"本包纳入 {source_count} 个 Python 源码文件；入口、依赖和运行说明位于 source_code/。",
        "文档为打包时读取的版本，已与 ZIP 内对应文件逐字节核对。",
        "程序提供原始材料读取、生成和检查流程；按赛题规则可对生成稿进行人工检查、修改和完善。",
        "请按 source_code/README.md 安装依赖、放置本次原始材料并配置模型，再运行：", "",
        "```bash", "cd source_code", "python main.py all", "```", "",
        "## 提交前检查", "",
        "已完成文件名、章节、来源字段、中文正文、篇幅和 ZIP 内容的确定性检查。",
        "已排除本地 .env 及其变体，仅允许空值 .env.example 模板；拟归档内容已作敏感凭证模式扫描。",
        "科学事实、表达质量和来源真实性仍须结合原始材料审阅。", "",
    ])
    return "\n".join(lines).encode("utf-8")


def _write_readme_txt(target: Path, docs: list[Path], source_root: Path) -> None:
    """兼容旧调用；正常打包直接写 ZIP，不在 docs 内创建临时说明。"""
    target.write_bytes(_readme_bytes(docs, len(list(source_root.rglob("*.py")))))


def _content_errors(name: str, data: bytes) -> list[str]:
    if PurePosixPath(name).name.lower() == ".env.example":
        try:
            return validate.env_template_errors(data.decode("utf-8-sig"))
        except UnicodeError:
            return [".env.example 不是 UTF-8 空值模板"]
    # 对全部拟归档字节扫描，不仅依赖文件后缀；不打印匹配到的内容。
    return validate.secret_errors(data.decode("utf-8", errors="ignore"))


def _output_path(project_root: Path, package_name: str) -> Path:
    """跨平台只接受单个 ZIP 文件名，禁止配置将输出移出项目根。"""
    if (not isinstance(package_name, str) or not package_name.strip()
            or package_name != package_name.strip()
            or any(char in package_name for char in '/\\:<>|"*?\x00')
            or '..' in package_name
            or PurePosixPath(package_name).is_absolute()
            or PureWindowsPath(package_name).drive
            or PureWindowsPath(package_name).is_absolute()
            or Path(package_name).suffix.lower() != '.zip'
            or not Path(package_name).stem):
        raise ValueError("package_name 必须是单个 .zip 文件名，不得包含路径、盘符或 ..")
    root = project_root.resolve()
    output = root / package_name
    if output.is_symlink() or output.resolve().parent != root:
        raise ValueError("提交包目标必须位于项目根内，且不得为符号链接")
    return output


def build(project_root: Path, docs_dir: Path, source_dir: Path,
          package_name: str, expected_ids: list[str] | None = None,
          expected_sources: dict[str, str] | None = None,
          materials_dir: Path | None = None) -> tuple[Path, int]:
    output = _output_path(project_root, package_name)
    materials = Path(materials_dir) if materials_dir is not None else Path("materials")
    if not materials.is_absolute():
        materials = project_root / materials
    docs = validate.document_paths(docs_dir)
    if not docs:
        raise FileNotFoundError(f"{docs_dir} 下没有可提交的 .md 文档")
    report = validate.Report()
    validate.check_documents(docs_dir, materials, report, expected_ids, expected_sources)
    validate.check_source_code(source_dir, report)
    if report.failed:
        raise ValueError("打包预检未通过：\n" + "\n".join(report.errors))

    entries = {doc.name: doc.read_bytes() for doc in docs}
    expected_docs = dict(entries)
    originals: dict[Path, bytes] = {doc: expected_docs[doc.name] for doc in docs}
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file() or _should_skip(path.relative_to(source_dir)):
            continue
        if path.is_symlink():
            raise ValueError(f"源码目录含符号链接，须提供可核查的实际文件：{path.name}")
        name = f"source_code/{path.relative_to(source_dir).as_posix()}"
        entries[name] = path.read_bytes()
        originals[path] = entries[name]
    for name, data in entries.items():
        problems = _content_errors(name, data)
        if problems:
            raise ValueError(f"拟归档文件 {name} 未通过敏感内容检查：{'；'.join(problems)}")
    count = sum(name.startswith("source_code/") and name.endswith(".py") for name in entries)
    entries["提交说明.md"] = _readme_bytes(docs, count)

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{output.stem}-", suffix=".tmp.zip",
                                         dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        problems = verify(temporary, expected_docs=expected_docs, expected_ids=expected_ids,
                          expected_sources=expected_sources)
        if problems:
            raise ValueError("临时压缩包未通过核验：\n" + "\n".join(problems))
        for path, expected in originals.items():
            if not path.exists() or path.read_bytes() != expected:
                raise ValueError(f"打包期间输入发生变化，请重新打包：{path.name}")
        os.replace(temporary, output)
        return output, len(entries)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def verify(package: Path, expected_docs: dict[str, bytes] | None = None,
           expected_ids: list[str] | None = None,
           expected_sources: dict[str, str] | None = None) -> list[str]:
    """检查实际 ZIP 内容；传 expected_docs 时核对当次稿件哈希，拒绝旧稿。"""
    problems: list[str] = []
    try:
        with zipfile.ZipFile(package) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                problems.append("压缩包包含重复文件名")
            data_of = {name: archive.read(name) for name in names if not name.endswith("/")}
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        return [f"压缩包无法完整读取：{type(exc).__name__}"]

    docs = {name: data for name, data in data_of.items()
            if "/" not in name and name.endswith(".md") and name != "提交说明.md"}
    if not docs:
        problems.append("压缩包内没有科普文档")
    required = {"source_code/main.py", "source_code/requirements.txt", "source_code/README.md", "提交说明.md"}
    for missing in sorted(required - set(data_of)):
        problems.append(f"压缩包缺少 {missing}")
    expected_names = ({f"{mid}.md" for mid in expected_ids} if expected_ids is not None
                      else set(expected_docs) if expected_docs is not None else None)
    if expected_names is not None and set(docs) != expected_names:
        problems.append("压缩包科普文档集合与本次要求不一致")

    for name, data in data_of.items():
        path = PurePosixPath(name)
        if "\\" in name or path.is_absolute() or ".." in path.parts:
            problems.append(f"压缩包包含不安全路径：{name}")
        if name not in docs and name != "提交说明.md" and not name.startswith("source_code/"):
            problems.append(f"压缩包包含非预期文件：{name}")
        if _should_skip(path):
            problems.append(f"压缩包包含禁止提交的本地或构建文件：{name}")
        problems.extend(f"{name}：{error}" for error in _content_errors(name, data))
    for name, data in docs.items():
        try:
            text = data.decode("utf-8-sig")
        except UnicodeError:
            problems.append(f"{name} 不是 UTF-8 文档")
            continue
        mid = PurePosixPath(name).stem
        problems.extend(f"{name}：{error}" for error in contracts.document_errors(
            text, mid, (expected_sources or {}).get(mid)))
        if expected_docs is not None and name in expected_docs:
            if hashlib.sha256(data).digest() != hashlib.sha256(expected_docs[name]).digest():
                problems.append(f"{name} 与当前文档哈希不一致，压缩包可能包含旧稿")
    return problems
