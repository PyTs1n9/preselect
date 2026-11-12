# -*- coding: utf-8 -*-
"""材料原文抽取：把组委会下发的各种格式材料统一转成纯文本。

设计原则
--------
1. **只搬运，不改写**。抽取阶段绝不做同义替换、概括或删减语义，
   否则会触碰"不得改变原文研究结论、数据含义与适用范围"的红线。
2. **尽量零依赖**。txt / md / html / docx 全部用标准库处理；
   只有 PDF 需要 pypdf，缺失时给出明确提示而不是静默失败。
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import sys
import warnings
import zipfile
from pathlib import Path

# pypdf 解析某些期刊 PDF 的内嵌 CFF Type1 字体时会提示缺 fontTools，
# 它走的是 logging（pypdf._cmap）而非 warnings。装上 fontTools 可从源头消除，
# 这里再压一级日志作为兜底，避免刷满终端却又不影响文字抽取。
logging.getLogger("pypdf").setLevel(logging.ERROR)

SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown", ".html", ".htm", ".docx", ".pdf"}

# 文本末尾用于溯源的内容指纹长度
FINGERPRINT_LEN = 16


class ExtractError(RuntimeError):
    """材料无法读取时抛出，附带可执行的修复建议。"""


def _read_plain(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|h[1-6]|li|section|article)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return html.unescape(text)


def _read_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", "replace")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ExtractError(f"{path.name} 不是有效的 docx: {exc}") from exc
    xml = re.sub(r"(?i)</w:p>", "\n", xml)
    xml = re.sub(r"(?i)<w:tab[^>]*/>", "\t", xml)
    xml = re.sub(r"(?s)<[^>]+>", "", xml)
    return html.unescape(xml)


def _read_pdf_pages(path: Path) -> list[str]:
    try:
        from pypdf import PdfReader  # 延迟导入：非 PDF 材料不需要这个依赖
    except ImportError as exc:
        raise ExtractError(
            f"读取 {path.name} 需要 pypdf；当前解释器是 {sys.executable}，"
            f"请执行: \"{sys.executable}\" -m pip install pypdf"
        ) from exc
    try:
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # pypdf 的异常类型较杂，统一包装并保留原因
        raise ExtractError(f"{path.name} 解析失败: {exc}") from exc
    text = "\n".join(pages)
    if len(text.strip()) < 20:
        raise ExtractError(
            f"{path.name} 抽取到的文字过少，可能是扫描件（图片型 PDF）。"
            "请改用 OCR 或向组委会索取可复制文本版本。"
        )
    return pages


def _read_pdf(path: Path) -> str:
    return "\n".join(_read_pdf_pages(path))


def clean_text(text: str) -> str:
    """统一换行与空白，去掉不可见字符，便于后续切块。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text(path: Path) -> str:
    """按后缀分派抽取器，返回已清洗的纯文本。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ExtractError(
            f"暂不支持 {suffix or '(无后缀)'} 格式: {path.name}；"
            f"可用格式: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
        )

    if suffix in {".txt", ".md", ".markdown"}:
        text = _read_plain(path)
    elif suffix in {".html", ".htm"}:
        text = _strip_html(_read_plain(path))
    elif suffix == ".docx":
        text = _read_docx(path)
    else:
        text = _read_pdf(path)

    text = clean_text(text)
    if not text:
        raise ExtractError(f"{path.name} 内容为空")
    return text


def fingerprint(text: str) -> str:
    """抽取文本指纹，仅用于定位输入版本，不证明生成过程或内容正确性。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:FINGERPRINT_LEN]


def extract_pages(path: Path) -> list[dict]:
    """保留 PDF 物理页序；其他格式用一个逻辑页，不虚构排版页码。

    空白 PDF 页仍保留，避免后续引用页码错位。extract_text 的清洗和拼接
    行为不变，现有文本指纹不会因增加分页接口而改变。
    """
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        return [{"page": index, "text": clean_text(text)}
                for index, text in enumerate(_read_pdf_pages(path), 1)]
    return [{"page": 1, "text": extract_text(path)}]


def guess_material_id(path: Path, ordinal: int) -> str:
    """从文件名推断材料编号；推断不出时退化为序号 M01、M02……

    例如 `M03_遥感影像解译.pdf` -> M03；`材料二.docx` -> 用序号。
    本届组委会命名形如 `ges_docs_AI-01.pdf`，其中的 `AI-01`（领域代码 + 序号）
    才是提交时文档名要对应的编号，因此优先提取这类「领域-序号」。
    """
    stem = path.stem
    # 领域代码 + 序号：AI-01 / BIO-02 / IND-03 / MED-04
    domain = re.search(r"([A-Za-z]{2,6})[-_](\d{1,3})", stem)
    if domain:
        return f"{domain.group(1).upper()}-{int(domain.group(2)):02d}"
    match = re.match(r"^([A-Za-z]{0,3}\d{1,3})(?=[_\-\s.．]|$)", stem)
    if match:
        return match.group(1).upper()
    return f"M{ordinal:02d}"


def split_chunks(text: str, size: int = 6000) -> list[str]:
    """按段落边界把长文切成不超过 size 字符的块，避免在句子中间截断。"""
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    buffer: list[str] = []
    used = 0
    for para in text.split("\n\n"):
        para_len = len(para)
        if used + para_len + 2 > size and buffer:
            chunks.append("\n\n".join(buffer))
            buffer, used = [], 0
        if para_len > size:  # 单段超长时硬切，保证不丢内容
            for i in range(0, para_len, size):
                chunks.append(para[i : i + size])
            continue
        buffer.append(para)
        used += para_len + 2
    if buffer:
        chunks.append("\n\n".join(buffer))
    return chunks
