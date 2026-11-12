# -*- coding: utf-8 -*-
"""评审辅助：数值、单位和局部指标候选核对，以及证据约束的模型评审。

本地匹配不是语义证明，未覆盖项保留待复核状态；不把缺少细则的验收结果
换算为官方扣分。全文候选检索见 evidence.build_review_context。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from . import contracts

# 数值提取：支持 312.6 / 1,234 / 56 这类写法
NUMBER_RE = re.compile(r"(?<![\d.])[+\-−]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][+\-]?\d+)?")

PROVENANCE_MARKER = "- **对应材料编号**"


def source_window(
    text: str, head: int = 6000, tail: int = 6000, middle: int = 6000
) -> str:
    """旧接口，仅供兼容；正式评审必须使用逐主张全文候选检索。"""
    text = text.strip()
    limit = head + middle + tail
    if len(text) <= limit:
        return text
    middle_start = max((len(text) - middle) // 2, head)
    middle_end = middle_start + middle
    return (
        text[:head]
        + "\n\n……（开头与中段之间省略，仅用于评审）……\n\n"
        + text[middle_start:middle_end]
        + "\n\n……（中段与结尾之间省略，仅用于评审）……\n\n"
        + text[-tail:]
    )


def strip_provenance(text: str) -> str:
    """去掉文末溯源块，避免把 sha256 与时间戳当成正文数值。"""
    index = text.find(PROVENANCE_MARKER)
    if index == -1:
        return text
    cut = text.rfind("\n---", 0, index)
    return text[:cut] if cut != -1 else text[:index]


def _is_structural_number(line: str, match: re.Match[str]) -> bool:
    """只跳过编号本身，不能把编号行后面的研究数据一起跳过。"""
    marker = re.match(r"^\s*\d+[.)、]\s*", line)
    if marker and match.start() < marker.end():
        return True
    if re.match(r"^\s*\d+\s+(?:研究|背景|引言|正文|总结)", line) and match.start() <= len(line) - len(line.lstrip()):
        return True
    before = line[: match.start()]
    after = line[match.end() :]
    if re.search(r"(?:第|分|分为|分成|包括)\s*$", before) and re.match(
        r"\s*(?:个|项|部分|类|种|步)", after
    ):
        return True
    return False


def _body_number_matches(document: str) -> list[tuple[str, float, str, int]]:
    """取出数值，标题只移除章节编号，保留标题中有科学含义的数据。"""
    found: list[tuple[str, float, str, int]] = []
    for line in strip_provenance(document).split("\n"):
        if line.lstrip().startswith("#"):
            line = re.sub(r"^\s*#{1,6}\s+(?:\d+(?:\.\d+)*[.、]?\s+)?", "", line)
        for match in NUMBER_RE.finditer(line):
            raw = match.group(0)
            try:
                value = float(raw.replace(",", "").replace("−", "-"))
            except ValueError:
                continue
            if value < 10 and value.is_integer() and _is_structural_number(line, match):
                continue
            found.append((raw, value, line, match.start()))
    return found


def _body_numbers(document: str) -> list[tuple[str, float]]:
    """兼容旧调用：只返回正文中的原始数字和值。"""
    return [(raw, value) for raw, value, _, _ in _body_number_matches(document)]


@dataclass
class NumberReport:
    total: int = 0
    exact: list[str] = field(default_factory=list)
    approx: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unit_mismatch: list[str] = field(default_factory=list)
    context_mismatch: list[str] = field(default_factory=list)
    semantic_pending: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    skipped: int = 0

    @property
    def consistent_rate(self) -> float:
        """局部候选匹配率，不是科学准确率或全文核验通过率。"""
        if not self.total:
            return 1.0
        return (len(self.exact) + len(self.approx)) / self.total


METRICS = {
    "accuracy": ("准确率", "分类精度", "准确度", "accuracy"),
    "error_rate": ("错误率", "误差率", "error rate"),
    "precision": ("查准率", "precision"),
    "recall": ("召回率", "敏感性", "灵敏度", "recall", "sensitivity"),
    "specificity": ("特异性", "specificity"),
    "area": ("面积", "area"),
    "distance": ("距离", "长度", "distance", "length"),
    "participants": ("参与者", "受试者", "人数", "样本量", "participants", "subjects", "sample size"),
    "growth": ("增幅", "增长率", "growth"),
    "kappa": ("kappa",),
    "temperature": ("温度", "temperature"),
    "efficacy": ("保护效力", "效力", "efficacy"),
    "concentration": ("浓度", "concentration"),
}
UNIT_SPECS = [
    (r"%|％|percent\b|per cent\b", "ratio", "0.01"),
    (r"平方公里|平方千米|km(?:²|2)\b|square kilometres?\b|square kilometers?\b", "area", "1000000"),
    (r"平方米|m(?:²|2)\b|square metres?\b|square meters?\b", "area", "1"),
    (r"公里|千米|km\b|kilometres?\b|kilometers?\b", "length", "1000"),
    (r"厘米|cm\b|centimetres?\b|centimeters?\b", "length", "0.01"),
    (r"毫米|mm\b|millimetres?\b|millimeters?\b", "length", "0.001"),
    (r"米|m\b|metres?\b|meters?\b", "length", "1"),
    (r"吨|tonnes?\b", "mass", "1000000"),
    (r"千克|公斤|kg\b|kilograms?\b", "mass", "1000"),
    (r"毫克|mg\b|milligrams?\b", "mass", "0.001"),
    (r"克|g\b|grams?\b", "mass", "1"),
    (r"小时|hours?\b|h\b", "duration", "3600"),
    (r"分钟|minutes?\b|min\b", "duration", "60"),
    (r"秒|seconds?\b|s\b", "duration", "1"),
    (r"(?:名|个)?(?:参与者|受试者|患者)|participants?\b|subjects?\b|patients?\b|人|例", "people", "1"),
    (r"年|years?\b", "year", "1"),
    (r"月|months?\b", "month", "1"),
    (r"日|天|days?\b", "day", "1"),
    (r"°\s*C|℃|degrees? Celsius\b", "celsius", "1"),
    (r"个|项|种|类|次|samples?\b", "count", "1"),
]
MAGNITUDES = ((r"亿", "100000000"), (r"万", "10000"), (r"千|thousand\b", "1000"),
              (r"million\b", "1000000"), (r"billion\b", "1000000000"))


def _unit(suffix: str) -> tuple[str, str, Decimal]:
    suffix = suffix.lstrip()
    magnitude = Decimal(1)
    prefix = ""
    # 千米/千克 are units, not a magnitude followed by a unit.
    if not suffix.startswith(("千米", "千克")):
        for pattern, multiplier in MAGNITUDES:
            match = re.match(pattern, suffix, re.I)
            if match:
                prefix = match.group()
                magnitude = Decimal(multiplier)
                suffix = suffix[match.end():].lstrip()
                break
    for pattern, dimension, scale in UNIT_SPECS:
        match = re.match(pattern, suffix, re.I)
        if match:
            return prefix + match.group(), dimension, magnitude * Decimal(scale)
    return prefix, "" if magnitude == 1 else "count", magnitude


def _number_unit(text: str, match: re.Match[str]) -> str:
    return _unit(text[match.end():match.end()+40])[0]


def _sentence(text: str, start: int, end: int) -> tuple[str, int]:
    # Periods inside decimals must not split a numerical assertion.
    window_start = max(0, start - 300)
    window_end = min(len(text), end + 300)
    window = text[window_start:window_end]
    boundaries = list(re.finditer(r"[。！？!?；;\n]|\.(?=\s+[A-Z]|\s*$)", window))
    start, end = start-window_start, end-window_start
    left = max((m.end() for m in boundaries if m.end() <= start), default=0)
    right = min((m.start() for m in boundaries if m.start() >= end), default=len(window))
    return window[left:right], start-left


def _metric(context: str, position: int) -> str:
    candidates = []
    lower = context.lower()
    for key, aliases in METRICS.items():
        for alias in aliases:
            for match in re.finditer(re.escape(alias), lower):
                distance = position-match.end() if match.end() <= position else match.start()-position+8
                if -8 <= distance <= 80:
                    candidates.append((max(0, distance), -len(alias), key))
    return min(candidates)[2] if candidates else ""


def _measurement(text: str, match: re.Match[str]) -> dict:
    raw = match.group()
    value = Decimal(raw.replace(",", "").replace("−", "-"))
    unit, dimension, scale = _unit(text[match.end():match.end()+40])
    context, offset = _sentence(text, match.start(), match.end())
    is_integer = value == value.to_integral_value()
    is_date = is_integer and 1900 <= value <= 2100 and dimension in {"", "year"}
    return {"raw": raw, "value": value, "normalized": value*scale, "scale": scale,
            "unit": unit, "dimension": dimension, "context": context.strip(),
            "metric": _metric(context, offset), "integer": is_integer, "date": is_date}


def _compatible_units(left: dict, right: dict) -> bool:
    if left["date"] and right["date"]:
        return True
    return left["dimension"] == right["dimension"]


def _context_agrees(left: dict, right: dict) -> bool:
    if left["metric"] and right["metric"]:
        return left["metric"] == right["metric"]
    if left["metric"] or right["metric"]:
        return False
    # Unknown bilingual paraphrases cannot be declared consistent just because
    # their numbers match. Require a shared nonnumeric lexical key.
    from .evidence import terms
    shared = terms(left["context"]) & terms(right["context"])
    return any(not key.startswith("num:") for key in shared)


def _rounded(left: dict, right: dict) -> bool:
    # Integers (sample sizes, dates, counts) never use percentage tolerances.
    if left["integer"] or right["integer"] or left["date"] or right["date"]:
        return False
    if not _compatible_units(left, right) or not _context_agrees(left, right):
        return False
    decimals = max(0, -left["value"].as_tuple().exponent)
    try:
        converted = right["normalized"] / left["scale"]
        rounded = converted.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return False
    return left["value"] == rounded and (left["value"] >= 0) == (right["value"] >= 0)


def check_numbers(document: str, source: str) -> NumberReport:
    """Normalize units/sign/scale and bind matches to local metric candidates.

    exact/approx mean only a local numeric candidate was found. Unidentified
    cross-language or metric relations are semantic_pending, never an automatic
    pass. Every decision retains the quoted source context for human inspection.
    """
    source_items = [_measurement(source, match) for match in NUMBER_RE.finditer(source)]
    report = NumberReport()
    for raw, _, line, position in _body_number_matches(document):
        match = NUMBER_RE.search(line, position)
        if match is None:
            continue
        item = _measurement(line, match)
        report.total += 1
        equal = [s for s in source_items if s["normalized"] == item["normalized"] and _compatible_units(item, s)]
        bound = [s for s in equal if _context_agrees(item, s)]
        approximate = [s for s in source_items if _rounded(item, s)] if not bound else []
        same_raw = [s for s in source_items if s["value"] == item["value"]]
        conflicts = [s for s in equal if item["metric"] and s["metric"] and item["metric"] != s["metric"]]
        unit_conflicts = [s for s in same_raw if item["unit"] and s["unit"] and
                          (not _compatible_units(item, s) or item["scale"] != s["scale"])]
        sign_conflicts = [s for s in source_items if item["normalized"] != 0 and
                          s["normalized"] == -item["normalized"] and _compatible_units(item, s)
                          and _context_agrees(item, s)]
        if bound:
            status, candidates = "numeric_candidate", bound
            report.exact.append(raw)
        elif approximate:
            status, candidates = "rounding_candidate", approximate
            report.approx.append(raw)
        elif conflicts or sign_conflicts:
            status, candidates = "context_mismatch", conflicts + sign_conflicts
            report.context_mismatch.append(f"{raw}{item['unit']}：指标或正负号冲突")
        elif unit_conflicts:
            status, candidates = "unit_mismatch", unit_conflicts
            report.unit_mismatch.append(f"{raw}{item['unit']}：单位或量级冲突")
        elif equal or same_raw:
            status, candidates = "semantic_pending", equal or same_raw
            report.semantic_pending.append(f"{raw}{item['unit']}：数值出现，但单位/指标关系未确认")
        else:
            status, candidates = "missing", []
            report.missing.append(raw)
        # A numeric candidate is not an entailment verdict. Keep its actual
        # local indicator and source quote, including alternatives if ambiguous.
        report.evidence.append({"number": raw, "unit": item["unit"], "metric": item["metric"],
                                "claim": item["context"], "status": status,
                                "candidates": [{"number": s["raw"], "unit": s["unit"], "metric": s["metric"],
                                                "text": s["context"]} for s in candidates[:4]],
                                "limitation": "数值与局部指标候选，不构成语义或全文核验"})
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


@dataclass
class FormatIssue:
    level: str
    message: str


@dataclass
class FormatReport:
    score: int | None
    issues: list[FormatIssue] = field(default_factory=list)
    package_checked: bool = False

    @property
    def errors(self) -> list[FormatIssue]:
        return [issue for issue in self.issues if issue.level == "error"]

    @property
    def warnings(self) -> list[FormatIssue]:
        return [issue for issue in self.issues if issue.level == "warning"]


def _body_without_title_and_provenance(text: str) -> str:
    return contracts.body_text(text)


def evaluate_format(
    project_root: Path,
    docs_dir: Path,
    materials_dir: Path,
    source_dir: Path,
    docs: list[Path],
    expected_ids: list[str],
    required_ids: list[str],
    manifest_records: list[dict],
    package_name: str,
    expected_sources: dict[str, str] | None = None,
) -> FormatReport:
    """共用纯验收规则；官方未给逐错扣分表，格式分保留待人工评定。"""
    from . import package

    issues: list[FormatIssue] = []
    expected = set(expected_ids) | set(required_ids)
    expected_names = {f"{material_id}.md" for material_id in expected}
    actual_names = {doc.name for doc in docs}
    if not docs:
        issues.append(FormatIssue("error", f"{docs_dir.name}/ 下没有 Markdown 文档"))
    if expected:
        for name in sorted(expected_names - actual_names):
            issues.append(FormatIssue("error", f"缺少应提交的 Markdown：{name}"))
        for name in sorted(actual_names - expected_names):
            issues.append(FormatIssue("error", f"存在未对应材料的 Markdown：{name}"))
    else:
        issues.append(FormatIssue("warning", "未配置期望材料编号，无法确认材料与文件名是否齐全"))

    expected_bytes: dict[str, bytes] = {}
    for doc in docs:
        try:
            data = doc.read_bytes()
            text = data.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            issues.append(FormatIssue("error", f"{doc.name} 无法读取为 UTF-8：{exc}"))
            continue
        expected_bytes[doc.name] = data
        for problem in contracts.document_errors(text, material_id=doc.stem,
                source_name=(expected_sources or {}).get(doc.stem)):
            issues.append(FormatIssue("error", f"{doc.name}：{problem}"))

    # The manifest is optional provenance, not an organizer-required submission.
    seen_docs: set[str] = set()
    seen_ids: set[str] = set()
    for record in manifest_records:
        doc_file = str(record.get("doc_file", "")).strip()
        material_id = str(record.get("material_id", "")).strip()
        source_file = str(record.get("source_file", "")).strip()
        if not doc_file or not material_id or not source_file:
            issues.append(FormatIssue("error", "_manifest.json 记录缺少文档、材料编号或源文件字段"))
            continue
        if doc_file in seen_docs or material_id in seen_ids:
            issues.append(FormatIssue("error", f"_manifest.json 重复映射：{doc_file} / {material_id}"))
        if doc_file not in actual_names or Path(doc_file).stem != material_id:
            issues.append(FormatIssue("error", f"_manifest.json 文档与材料编号映射不一致：{doc_file}"))
        if Path(source_file).name != source_file or not (materials_dir / source_file).is_file():
            issues.append(FormatIssue("error", f"_manifest.json 原始材料不存在或路径非法：{source_file}"))
        seen_docs.add(doc_file)
        seen_ids.add(material_id)
    if manifest_records and expected - seen_ids:
        issues.append(FormatIssue("error", "_manifest.json 缺少材料映射：" + "、".join(sorted(expected - seen_ids))))

    if source_dir.name != "source_code":
        issues.append(FormatIssue("error", f"源码目录必须命名为 source_code，当前为 {source_dir.name}"))
    for name in ("main.py", "requirements.txt", "README.md"):
        if not (source_dir / name).is_file():
            issues.append(FormatIssue("error", f"source_code 缺少提交所需文件：{name}"))

    package_path = project_root / package_name
    package_checked = package_path.is_file()
    if not package_checked:
        issues.append(FormatIssue("warning", f"尚未生成 {package_name}，压缩包结构及内容待核验"))
    else:
        for problem in package.verify(package_path, expected_docs=expected_bytes, expected_ids=sorted(expected),
                expected_sources=expected_sources):
            issues.append(FormatIssue("error", problem))
    if not any(issue.level == "error" for issue in issues):
        issues.append(FormatIssue("info", "已执行的结构验收未发现错误；待核验项与内容质量仍需处理"))
    issues.append(FormatIssue("info", "格式规范性满分10分，缺少官方逐项扣分细则，不自动换算分数"))
    return FormatReport(score=None, issues=issues, package_checked=package_checked)


def llm_review(
    client, prompt_template: str, document: str, source: str, facts: str = "无额外确定性提示",
    evidence_context: dict | None = None,
) -> str:
    """模型评审只给证据约束的模拟意见，未覆盖项必须单列待复核。"""
    pending = evidence_context.get("uncovered", []) if evidence_context else []
    if pending:
        facts += "\n【强制待复核项】以下主张未完成证据覆盖，不得认定准确或完整，不得给科学准确性/内容完整性满分：\n"
        facts += "\n".join(f"{item['id']}：{item['sentence']}（{item['reason']}）" for item in pending)
    prompt = prompt_template.format(
        document=strip_provenance(document),
        source=source,
        facts=facts,
    )
    result = client.chat(
        "你是本次赛项的评审专家，负责按评分维度给作品打分。"
        "必须严格依据给定的原始材料判断，不得凭你自己的领域知识评价对错。"
        "提供的原文是逐主张检索的候选；词汇命中不代表支持，未命中不代表原文没有。"
        "遇到未覆盖或否定性缺失主张，标注待复核，不得以未发现问题为由给科学准确性或内容完整性满分。"
        "不要客套，不要给鼓励性评价。",
        prompt,
    )
    if pending:
        # Preserve useful diagnostics but reject overconfident score lines even
        # if a model ignores the instructions. The CLI score parser then cannot
        # turn this incomplete review into a complete document total.
        result = re.sub(
            r"(?m)^##\s*(科学准确性|内容完整性)\s*[:：]\s*\d+(?:\.\d+)?\s*/\s*(?:15|20)[^\n]*",
            lambda match: f"## {match.group(1)}：待复核（证据覆盖未完成，暂不计分）",
            result,
        )
        result += "\n\n待复核主张：" + "、".join(item["id"] for item in pending)
    return result
