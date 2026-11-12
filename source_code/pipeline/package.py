# -*- coding: utf-8 -*-
"""打包：把科普文档与源码压成一个压缩包，并生成提交说明。

赛项要求
--------
- 提交内容打包为**一个**压缩包，不得拆分提交
- 文档与代码必须打包在一起
- 源码目录统一命名 source_code

打包策略
--------
压缩包顶层直接是 .md 文档 + source_code/，不再套一层文件夹，
这样解压后就是"文档与代码并列"的结构，最贴合原文表述。
"""
from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

EXCLUDE_DIRS = {"__pycache__", ".git", ".venv", "venv", ".idea", ".vscode", ".pytest_cache"}
EXCLUDE_FILES = {".env", ".DS_Store", "Thumbs.db"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".log", ".zip"}


def _should_skip(path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return True
    if path.name in EXCLUDE_FILES:
        return True
    return path.suffix.lower() in EXCLUDE_SUFFIXES


def _write_readme_txt(target: Path, docs: list[Path], source_root: Path) -> None:
    """压缩包里的提交说明，让评委一眼看懂结构。"""
    lines = [
        "# 提交说明",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 一、压缩包结构",
        "",
        "```",
        "作品.zip",
        "├── <材料编号>.md                  （每份专业材料对应 1 份，如 AI-01.md）",
        "└── source_code/                    （Python 源码 + 依赖配置 + 运行说明）",
        "```",
        "",
        "## 二、科普文档清单",
        "",
        "| 序号 | 文件名 |",
        "| --- | --- |",
    ]
    for index, doc in enumerate(docs, start=1):
        lines.append(f"| {index} | {doc.name} |")

    lines += [
        "",
        "## 三、文档生成方式",
        "",
        "全部 `.md` 文件由 `source_code` 中的程序读取原始专业材料后自动生成，",
        "未经人工改写正文。每份文档末尾均标注了对应材料编号、原文来源、",
        "原文内容指纹（sha256 前 16 位）与生成时间，可用于逐份溯源核验。",
        "",
        "复现命令：",
        "",
        "```bash",
        "cd source_code",
        "python main.py all",
        "```",
        "",
        "## 四、安全说明",
        "",
        "源码、配置与说明文档中不含任何真实 API Key、访问令牌或密码。",
        "模型调用所需的密钥统一从环境变量读取（见 `source_code/README.md`）。",
        "",
        "## 五、目录说明",
        "",
        f"- `source_code/`：程序源码，入口为 `main.py`",
        f"- 源码文件数：{len(list(source_root.rglob('*.py')))} 个 Python 文件",
        "",
    ]
    target.write_text("\n".join(lines), encoding="utf-8")


def build(project_root: Path, docs_dir: Path, source_dir: Path,
          package_name: str) -> tuple[Path, int]:
    docs = sorted(p for p in docs_dir.glob("*.md") if not p.name.startswith("_"))
    if not docs:
        raise FileNotFoundError(f"{docs_dir} 下没有可提交的 .md 文档，请先运行 generate")

    output = project_root / package_name
    if output.exists():
        output.unlink()

    # 提交说明先写到 docs/，再一起打进包；不留在 docs/ 里以免下次重复统计
    note = docs_dir / "提交说明.md"
    _write_readme_txt(note, docs, source_dir)

    file_count = 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for doc in docs:
            zf.write(doc, arcname=doc.name)
            file_count += 1
        zf.write(note, arcname=note.name)
        file_count += 1

        for path in sorted(source_dir.rglob("*")):
            if not path.is_file() or _should_skip(path):
                continue
            arcname = Path(source_dir.name) / path.relative_to(source_dir)
            zf.write(path, arcname=str(arcname).replace("\\", "/"))
            file_count += 1

    note.unlink(missing_ok=True)  # 清理临时文件，保持 docs/ 干净
    return output, file_count


def verify(package: Path) -> list[str]:
    """解压前先读一遍索引，确认关键文件都在包里。"""
    problems: list[str] = []
    with zipfile.ZipFile(package) as zf:
        names = zf.namelist()
    docs = [n for n in names if n.endswith(".md") and not n.startswith("source_code/")]
    codes = [n for n in names if n.startswith("source_code/")]
    if not docs:
        problems.append("压缩包内没有科普文档")
    if not any(n.endswith("main.py") for n in codes):
        problems.append("压缩包内 source_code/ 缺少入口 main.py")
    if not any(n.endswith("requirements.txt") for n in codes):
        problems.append("压缩包内 source_code/ 缺少 requirements.txt")
    if not any(n.endswith("README.md") for n in codes):
        problems.append("压缩包内 source_code/ 缺少 README.md")
    return problems
