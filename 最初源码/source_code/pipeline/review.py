# -*- coding: utf-8 -*-
"""作品质量评审。

自检（validate.py）只回答"格式合不合规"，回答不了"文章写得好不好"。
本模块补上后者，分两部分：

1. ``check_numbers``：确定性核对。把文档里出现的每个数值拿去原文里找，
   找不到的就列出来。这是模型最容易出问题的地方——编造一个看起来
   很合理的数字，人眼几乎发现不了，但科学准确性是一票否决式的底线。
2. ``llm_review``：让模型以评委视角按评分维度打分，并要求它必须引用
   具体句子举出问题，避免只给一句"写得不错"。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 数值提取：支持 312.6 / 1,234 / 56 这类写法
NUMBER_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")

PROVENANCE_MARKER = "- **对应材料编号**"


def source_window(text: str, head: int = 8000, tail: int = 4000) -> str:
    """为评审截取原文窗口：取开头与结尾，而不是只取前 N 字。

    英文论文的关键结论常在「结果 / 讨论」段（中后部），摘要与引言在开头。
    只截前 N 字会把评审模型看得「看不到关键数据」，从而把原文确实存在的
    数字误判为「材料未提供」。首尾拼接能同时覆盖摘要与结论这两处最该核对的地方。
    """
    text = text.strip()
    if len(text) <= head + tail:
        return text
    return (
        text[:head]
        + "\n\n……（中段省略，仅用于评审，不影响文档内容）……\n\n"
        + text[-tail:]
    )


def strip_provenance(text: str) -> str:
    """去掉文末溯源块，避免把 sha256 与时间戳当成正文数值。"""
    index = text.find(PROVENANCE_MARKER)
    if index == -1:
        return text
    cut = text.rfind("\n---", 0, index)
    return text[:cut] if cut != -1 else text[:index]


def _values(text: str) -> list[float]:
    values: list[float] = []
    for match in NUMBER_RE.finditer(text):
        try:
            values.append(float(match.group(0).replace(",", "")))
        except ValueError:
            continue
    return values


def _body_numbers(document: str) -> list[tuple[str, float]]:
    """取出正文中的数值，跳过标题行（那是章节序号，不是数据）。"""
    found: list[tuple[str, float]] = []
    for line in strip_provenance(document).split("\n"):
        if line.lstrip().startswith("#"):
            continue
        for match in NUMBER_RE.finditer(line):
            raw = match.group(0)
            try:
                found.append((raw, float(raw.replace(",", ""))))
            except ValueError:
                continue
    return found


@dataclass
class NumberReport:
    total: int = 0
    exact: list[str] = field(default_factory=list)
    approx: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    skipped: int = 0

    @property
    def consistent_rate(self) -> float:
        if not self.total:
            return 1.0
        return (len(self.exact) + len(self.approx)) / self.total


def check_numbers(document: str, source: str) -> NumberReport:
    """逐个核对文档中的数值能否在原文中找到。

    个位数（1-9）多为列表序号或"三个部分"这类表述，直接跳过，
    否则报告里全是噪声，反而看不清真正的问题。
    """
    source_values = _values(source)
    report = NumberReport()

    for raw, value in _body_numbers(document):
        if value < 10 and float(value).is_integer():
            report.skipped += 1
            continue

        report.total += 1
        if any(abs(value - src) < 1e-9 for src in source_values):
            report.exact.append(raw)
            continue

        # 允许 1% 以内的偏差：模型可能把 56.14 写成 56.1
        tolerance = max(0.005, abs(value) * 0.01)
        if any(abs(value - src) <= tolerance for src in source_values):
            report.approx.append(raw)
            continue

        # 四舍五入也应算"有依据"：原文 0.87 写成 0.9 属正常取舍，
        # 但原文 56.1 写成 63.8 是篡改，下面这条不会放过。
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        if any(abs(value - round(src, decimals)) < 1e-9 for src in source_values):
            report.approx.append(raw)
            continue

        report.missing.append(raw)

    return report


@dataclass
class DocStats:
    chars: int = 0
    paragraphs: int = 0
    h3_sections: int = 0
    numbers: int = 0
    annotated_terms: int = 0


def stats(document: str) -> DocStats:
    """粗略统计篇幅与结构，用于判断是否"看起来很单薄"。"""
    body = strip_provenance(document)
    paragraphs = [p for p in body.split("\n\n") if len(p.strip()) >= 30]
    return DocStats(
        chars=len(body),
        paragraphs=len(paragraphs),
        h3_sections=len(re.findall(r"^### ", body, re.M)),
        numbers=len(_body_numbers(document)),
        # "也就是说""通俗地说""可以理解为" 这类解释性衔接词的出现次数，
        # 作为"有没有真的在解释"的粗略代理指标
        annotated_terms=len(
            re.findall(r"通俗地说|也就是说|可以理解为|简单说|直观地说|指的是", body)
        ),
    )


def llm_review(client, prompt_template: str, document: str, source: str) -> str:
    """让模型按评分维度评审一篇文档。不通过则抛出 LLMError 由调用方处理。"""
    prompt = prompt_template.format(
        document=strip_provenance(document),
        source=source,
    )
    return client.chat(
        "你是本次赛项的评审专家，负责按评分维度给作品打分。"
        "必须严格依据给定的原始材料判断，不得凭你自己的领域知识评价对错。"
        "不要客套，不要给鼓励性评价。",
        prompt,
    )
