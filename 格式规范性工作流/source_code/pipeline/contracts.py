# -*- coding: utf-8 -*-
"""生成、评审和打包共用的确定性文档验收规则。

这里只检查可由文本证明的要求，不推断事实真伪，也不模拟官方扣分。
"""
from __future__ import annotations

import re

BODY_LIMIT = 2500
REQUIRED_HEADINGS = ("引言", "正文", "总结")
_ID_FIELD = re.compile(r"(?m)^[ \t]*(?:-[ \t]*)?(?:\*\*)?对应材料编号(?:\*\*)?[ \t]*[：:][ \t]*(.*?)[ \t]*$")
_SOURCE_FIELD = re.compile(r"(?m)^[ \t]*(?:-[ \t]*)?(?:\*\*)?原文来源(?:\*\*)?[ \t]*[：:][ \t]*(.*?)[ \t]*$")
_PLACEHOLDERS = {"", "待填", "待填写", "未知", "未提供", "原文来源", "来源", "todo", "tbd", "unknown", "n/a", "...", "……"}


def _without_provenance(text: str) -> str:
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    marker = _ID_FIELD.search(text)
    if marker:
        text = text[:marker.start()].rstrip()
        text = re.sub(r"\n\s*---\s*$", "", text).rstrip()
    return text


def body_text(text: str) -> str:
    """正文的保守字符口径：不计一级标题和文末来源，保留正文排版字符。"""
    text = _without_provenance(text)
    lines = text.splitlines()
    if lines and re.match(r"^#[ \t]+\S", lines[0]):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _prose(text: str) -> str:
    lines = [line for line in text.splitlines() if not re.match(r"^\s*#{1,6}\s+", line)]
    return re.sub(r"[\s#*_`>\-|]+", "", "\n".join(lines))


def document_errors(text: str, material_id: str | None = None,
                    source_name: str | None = None) -> list[str]:
    """检查单篇结构、来源字段、中文和长度；来源真实性仍需原文核验。"""
    errors: list[str] = []
    normalized = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not re.match(r"^#[ \t]+[^\s][^\n]*", normalized):
        errors.append("缺少非空一级标题")
    content = _without_provenance(normalized)
    headings = list(re.finditer(r"(?m)^##[ \t]+([^\n]+?)[ \t]*$", content))
    names = [m.group(1).strip() for m in headings]
    if any(names.count(name) != 1 for name in REQUIRED_HEADINGS):
        errors.append("章节必须各出现一次：引言、正文、总结")
    elif [name for name in names if name in REQUIRED_HEADINGS] != list(REQUIRED_HEADINGS):
        errors.append("章节顺序应为：引言、正文、总结")
    for index, heading in enumerate(headings):
        name = heading.group(1).strip()
        if name in REQUIRED_HEADINGS:
            end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
            if not re.search(r"[\w\u4e00-\u9fff]", _prose(content[heading.end():end])):
                errors.append(f"章节“{name}”内容为空，不能只放标题")

    for label, pattern, expected in (
        ("对应材料编号", _ID_FIELD, material_id),
        ("原文来源", _SOURCE_FIELD, source_name),
    ):
        matches = list(pattern.finditer(normalized))
        if len(matches) != 1:
            errors.append(f"文末须有且仅有一个非空{label}字段")
            continue
        match = matches[0]
        value = match.group(1).strip()
        if value.casefold() in _PLACEHOLDERS or re.fullmatch(r"[<【\[].*[>】\]]", value):
            errors.append(f"{label}为空或仍是占位内容")
        elif expected is not None and " ".join(value.split()) != " ".join(str(expected).split()):
            errors.append(f"{label}与本次材料配置不一致（应为 {expected}）")
        summary = re.search(r"(?m)^##[ \t]+总结[ \t]*$", normalized)
        if summary is None or match.start() < summary.end():
            errors.append(f"{label}须位于总结之后的文末来源块")

    body = body_text(normalized)
    if len(body) > BODY_LIMIT:
        errors.append(f"正文 {len(body)} 字符，超过 {BODY_LIMIT} 字上限")
    prose = _prose(body)
    cjk = len(re.findall(r"[\u4e00-\u9fff]", prose))
    letters = len(re.findall(r"[A-Za-z\u4e00-\u9fff]", prose))
    if letters and (not cjk or cjk / letters < 0.15):
        errors.append("正文主要为外文，未满足中文科普的基本语言要求")
    return errors
