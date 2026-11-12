# -*- coding: utf-8 -*-
"""科普文档生成引擎。

两种引擎
--------
- ``llm``      : 调用 OpenAI 兼容接口（DeepSeek / 通义 / 本地 Ollama 均可）。
                 长材料自动走 map-reduce：先分片提炼要点，再合写成文。
- ``extractive``: 显式调用的中文摘录草稿辅助，不作为模型失败的提交兜底。

密钥永远从环境变量读取，不写入任何文件。
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path

from . import contracts, prompts
from .extractors import fingerprint, split_chunks

# 单次调用的输入上限（字符）。超过则启用 map-reduce。
# 20000 字以内直接单次生成（短篇论文一次调用连贯性更好）；超过则分片。
SINGLE_CALL_LIMIT = 20000
# 长材料分片大小。本赛道材料多为英文综述（PDF 抽取后可达 15 万字符），
# 取 10000 字/片可在「提炼完整度」与「调用次数」之间取得平衡。
CHUNK_SIZE = 10000
# 超限触发压缩；压缩和有限结构修复后仍不合规则失败，不输出占位稿。
BODY_LIMIT = 2500
MAX_RETRY = 3
MAX_REPAIR = 2


class LLMError(RuntimeError):
    pass


class LLMClient:
    """最小可用的 OpenAI 兼容 Chat Completions 客户端。"""

    def __init__(self, cfg: dict):
        self.base_url = (cfg.get("base_url") or "").rstrip("/")
        self.model = cfg.get("model") or ""
        self.api_key_env = cfg.get("api_key_env") or "LLM_API_KEY"
        self.api_key_header = str(cfg.get("api_key_header") or "Authorization").strip()
        prefix = cfg.get("api_key_prefix")
        if prefix is None:
            prefix = "Bearer " if self.api_key_header.lower() == "authorization" else ""
        self.api_key_prefix = str(prefix)
        self.temperature = float(cfg.get("temperature", 0.3))
        self.max_tokens = int(cfg.get("max_tokens", 4096))
        self.timeout = int(cfg.get("timeout", 180))
        # DeepSeek V4 系列默认开启思考模式，会把大量 token 花在内部思维链上、
        # 挤占 max_tokens 额度，导致正文被截断（finish_reason='length'）。
        # 服务商预设里可显式配置 thinking="disabled" 关闭；其他服务商不填则不发送。
        self.thinking = (cfg.get("thinking") or "").strip()

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.model)

    def _api_key(self) -> str:
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise LLMError(
                f"环境变量 {self.api_key_env} 未设置。"
                f"请先设置密钥；不会将离线摘录冒充中文科普成稿。"
            )
        return key

    def chat(self, system: str, user: str) -> str:
        try:
            import requests
        except ImportError as exc:
            raise LLMError("调用大模型需要 requests：pip install requests") from exc

        url = f"{self.base_url}/chat/completions"
        headers = {
            self.api_key_header: f"{self.api_key_prefix}{self._api_key()}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.thinking:
            payload["thinking"] = {"type": self.thinking}

        last_error = ""
        for attempt in range(1, MAX_RETRY + 1):
            try:
                resp = requests.post(
                    url, headers=headers, json=payload, timeout=self.timeout
                )
                if resp.status_code == 200:
                    data = resp.json()
                    choice = data["choices"][0]
                    reason = choice.get("finish_reason")
                    if reason != "stop":
                        raise LLMError(
                            f"模型未正常完成（finish_reason={reason!r}），拒绝使用截断或不完整输出"
                        )
                    content = choice.get("message", {}).get("content")
                    if not isinstance(content, str) or not content.strip():
                        raise LLMError("模型返回空正文或非文本内容")
                    return content.strip()

                # 服务商错误体可能回显密钥或请求，不写入终端/报告。
                last_error = f"HTTP {resp.status_code}；请检查模型配置、权限与余额"
                # 鉴权失败、模型名不存在、参数错 —— 重试多少次结果都一样，
                # 立即失败，由上层保留已有稿件并报告原因。
                if resp.status_code in {400, 401, 403, 404}:
                    raise LLMError(last_error)
            except LLMError:
                raise
            except Exception as exc:  # 网络抖动、超时、JSON 解析失败
                last_error = type(exc).__name__
            if attempt < MAX_RETRY:
                time.sleep(2 * attempt)  # 指数退避，避免连环失败
        raise LLMError(f"调用失败（已重试 {MAX_RETRY} 次）：{last_error}")


# --------------------------------------------------------------------------
# 文本处理辅助
# --------------------------------------------------------------------------
def _strip_code_fence(text: str) -> str:
    """模型偶尔会把整篇内容包进 ``` 里，去掉以免污染 Markdown 结构。"""
    if not isinstance(text, str):
        raise LLMError("生成内容必须是文本")
    text = text.strip()
    fence = re.match(r"^```[a-zA-Z]*\s*\n(.*)\n```$", text, re.S)
    return fence.group(1).strip() if fence else text


#: 事实 ID 的形态：map-reduce 阶段给每条事实编的内部溯源号（C001-F01）。
#: 模型合写时偶尔会把它们当引用标注抄进正文（全角或半角括号都可能出现，
#: 括号内还可能用顿号/逗号连写多个编号，如 （C004-F08、C004-F09））。
_FACT_ID_RE = re.compile(r"[（(]\s*C\d{3}-F\d{2}(?:\s*[、,，]\s*C\d{3}-F\d{2})*\s*[）)]")


def _strip_fact_ids(text: str) -> str:
    """去掉正文里泄漏出来的内部事实 ID（如 （C001-F01））。

    事实 ID 只用于 [需复核] 冲突定位与评审追溯，不应出现在面向读者的最终成稿里。
    这里做确定性兜底清洗：即使提示词约束失效、模型仍把编号写进正文，也能在成稿前
    抹掉，且不触碰 [需复核] 这类有明确语义的中文标记。
    """
    if not isinstance(text, str):
        return text
    return _FACT_ID_RE.sub("", text)


def _ensure_structure(body: str, title: str) -> str:
    """补一级标题并验收；不编写原文缺失理由或虚造章节内容。"""
    body = body.strip()
    if not body.startswith("# "):
        body = f"# {title}\n\n{body}"
    errors = _draft_errors(body)
    if errors:
        raise LLMError("文章未通过验收：" + "；".join(errors))
    return body


def _draft_errors(body: str) -> list[str]:
    """复用最终文档规则，临时加来源只为检查尚未组装的正文。"""
    text = _strip_code_fence(body)
    if not text.startswith("# "):
        text = "# 待组装标题\n\n" + text
    text += "\n\n---\n\n- **对应材料编号**：DRAFT\n- **原文来源**：本次原始专业材料\n"
    errors = contracts.document_errors(text, "DRAFT", "本次原始专业材料")
    if "[需复核]" in body:
        errors.append("模型报告核心证据需复核，不能作为成稿")
    if re.search(r"原文未(?:单列引言|对整体结论作集中表述)", body):
        errors.append("正文包含未核实的章节缺失占位说明")
    return errors


def _split_title(body: str, fallback: str) -> tuple[str, str]:
    """把一级标题从正文中拆出来，便于统一加溯源块。"""
    lines = body.split("\n")
    if lines and lines[0].startswith("# "):
        return lines[0][2:].strip() or fallback, "\n".join(lines[1:]).lstrip("\n")
    return fallback, body


# --------------------------------------------------------------------------
# LLM 引擎
# --------------------------------------------------------------------------
def generate_with_llm(
    client: LLMClient,
    content: str,
    material_id: str,
    source_name: str,
    target_words: int,
    *,
    pages: list[dict] | None = None,
    document_type: str = "auto",
    artifacts: dict | None = None,
    quality_check: bool = False,
) -> str:
    """生成后验收，长材料保留可追溯事实；artifacts由调用者选择保存。

    引文校验仅证明字符串确实出现在输入页，不证明概括或最终主张正确。
    后续评审仍需比较对象、指标、适用范围和结论强度。
    """
    if not content.strip():
        raise LLMError("原始材料内容为空")
    kind = _document_type(document_type, content, source_name)
    guidance = f"【文献类型】{kind}\n{prompts.DOCUMENT_TYPE_GUIDANCE[kind]}\n\n"
    if artifacts is not None:
        artifacts.update({"document_type": kind, "facts": [], "chunks": [], "validation": [],
                          "quality_checks": [], "quality_check_enabled": quality_check})
    if len(content) <= SINGLE_CALL_LIMIT:
        evidence = content
        evidence_quotes = [content]
        quality_scope = "仅核查本轮提供的原始抽取文本，不保证科学正确性或未抽取内容的覆盖。"
        request = prompts.DOC_PROMPT.format(
            material_id=material_id, source_name=source_name,
            target_words=target_words, content=content,
        )
    else:
        chunks = _page_chunks(pages or [{"page": 1, "text": content}])
        facts: list[dict] = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_text = "\n\n".join(
                f"[PAGE {part['page']}]\n{part['text']}" for part in chunk
            )
            request = prompts.MAP_PROMPT.format(
                index=index, total=len(chunks), max_points=24, chunk=chunk_text,
            )
            valid = _extract_facts(client, guidance + request, chunk, index)
            facts.extend(valid)
            if artifacts is not None:
                artifacts["facts"].extend(valid)
                artifacts["chunks"].append({"index": index,
                    "pages": sorted({part["page"] for part in chunk}),
                    "fact_count": len(valid)})
            print(f"      分片 {index}/{len(chunks)}：{len(valid)} 条原句已核验")
        if not facts:
            raise LLMError("长材料未提取到任何可核验事实，停止生成")
        evidence = json.dumps(facts, ensure_ascii=False, indent=2)
        evidence_quotes = [row["quote"] for row in facts]
        quality_scope = "仅核查生成使用的已验证原句事实记录；未覆盖事实记录之外的全文，不保证科学正确性。"
        request = prompts.REDUCE_PROMPT.format(
            material_id=material_id, source_name=source_name,
            target_words=target_words, points=evidence,
        )
    body = client.chat(prompts.SYSTEM_PROMPT, guidance + request)
    for attempt in range(MAX_REPAIR + 1):
        body = _strip_code_fence(body)
        errors = _draft_errors(body)
        if artifacts is not None:
            artifacts["validation"].append({"attempt": attempt, "errors": errors})
        if not errors:
            if artifacts is not None:
                artifacts["quality_scope"] = quality_scope
            result = (_quality_gate(client, body, evidence, evidence_quotes, guidance, artifacts)
                      if quality_check else body)
            return _strip_fact_ids(result)
        if "[需复核]" in body:
            raise LLMError("核心证据需人工复核：" + body[:500])
        if attempt == MAX_REPAIR:
            raise LLMError("有限修复后仍未通过验收：" + "；".join(errors))
        if len(contracts.body_text(body)) > BODY_LIMIT:
            body = _maybe_compress(client, body)
        else:
            body = client.chat(prompts.SYSTEM_PROMPT, guidance + prompts.REPAIR_PROMPT.format(
                errors="；".join(errors), evidence=evidence, body=body,
            ))
    raise LLMError("生成未完成")


_QUALITY_DIMENSIONS = {"objects_metrics", "strength", "negative_claims",
                       "core_coverage", "boundaries", "expression"}


def _validated_quality_check(raw: str, body: str, evidence_quotes: list[str]) -> dict:
    """验证核查格式和双方引文，不能凭引用存在断言模型判断正确。"""
    try:
        result = json.loads(_strip_code_fence(raw))
    except (ValueError, TypeError) as exc:
        raise LLMError("内容核查必须返回合法JSON，不能视为通过") from exc
    if not isinstance(result, dict):
        raise LLMError("内容核查不是JSON对象")
    checked, issues = result.get("checked"), result.get("issues")
    if (not isinstance(checked, list) or not all(isinstance(v, str) for v in checked)
            or set(checked) != _QUALITY_DIMENSIONS):
        raise LLMError("内容核查没有完整声明六项核查维度")
    if not isinstance(issues, list):
        raise LLMError("内容核查issues必须为数组")
    # 模型偶尔会多列几条问题，按严重程度排序，截断到前 20 条。
    if len(issues) > 20:
        issues = issues[:20]
    verified = []
    for issue in issues:
        # 逐条严格校验：任何一条坏证据（错类型、空理由、引文对不上）都整轮失败，
        # 触发上层反馈重试，而不是悄悄丢弃——否则「核查」就失去了拦截假引文的意义。
        if not isinstance(issue, dict):
            raise LLMError("内容核查issue必须是对象")
        if issue.get("category") not in {"accuracy", "coverage", "expression"}:
            raise LLMError("内容核查issue的category不合法")
        fields = ("article_quote", "evidence_quote", "reason", "suggestion")
        if not all(isinstance(issue.get(field), str) for field in fields):
            raise LLMError("内容核查issue的字段必须是字符串")
        if not issue["reason"].strip() or not issue["suggestion"].strip():
            raise LLMError("内容核查issue的reason/suggestion不能为空")
        article_raw = issue["article_quote"]
        if article_raw:
            if not _quote_in_source(article_raw, body):
                raise LLMError("内容核查issue的文章引文不在文章中")
        elif issue["category"] != "coverage":
            raise LLMError("内容核查issue的文章引文为空（仅coverage缺漏项可为空）")
        quote_raw = issue["evidence_quote"]
        if len(quote_raw.strip()) < 8 or not any(_quote_in_source(quote_raw, src) for src in evidence_quotes):
            raise LLMError("内容核查issue的原文证据不在本轮原文证据中")
        verified.append({field: issue[field] for field in ("category",) + fields})
    return {"checked": checked, "issues": verified, "quotes_verified": True}


def _quality_gate(client: LLMClient, body: str, evidence: str, evidence_quotes: list[str],
                  guidance: str, artifacts: dict | None) -> str:
    """一次核查，至多一次修订和一次复核；未关闭事实/核心覆盖问题则停止。"""
    check_prompt = prompts.QUALITY_CHECK_PROMPT.format(body=body, evidence=evidence, guidance=guidance)
    check = _validated_quality_check(client.chat(prompts.SYSTEM_PROMPT, check_prompt), body, evidence_quotes)
    check.update({"attempt": 0, "status": "checked", "article_fingerprint": fingerprint(body)})
    if artifacts is not None:
        artifacts["quality_checks"].append(check)
    issues = check["issues"]
    if not issues:
        return body
    revised = _strip_code_fence(client.chat(prompts.SYSTEM_PROMPT, prompts.QUALITY_REVISE_PROMPT.format(
        body=body, evidence=evidence, guidance=guidance,
        issues=json.dumps(issues, ensure_ascii=False, indent=2),
    )))
    # 修订补内容（尤其 coverage 补缺失边界）常把字数顶过上限，走同一套压缩/硬截断兜底。
    if len(contracts.body_text(revised)) > BODY_LIMIT:
        revised = _maybe_compress(client, revised)
    errors = _draft_errors(revised)
    if errors:
        if artifacts is not None:
            artifacts["quality_checks"].append({"attempt": 1, "status": "revision_invalid", "errors": errors})
        raise LLMError("内容修订稿未通过结构/字数验收：" + "；".join(errors))
    recheck_prompt = prompts.QUALITY_CHECK_PROMPT.format(body=revised, evidence=evidence, guidance=guidance)
    recheck = _validated_quality_check(client.chat(prompts.SYSTEM_PROMPT, recheck_prompt), revised, evidence_quotes)
    recheck.update({"attempt": 1, "status": "checked", "article_fingerprint": fingerprint(revised)})
    if artifacts is not None:
        artifacts["quality_checks"].append(recheck)
    blocking = [issue for issue in recheck["issues"] if issue["category"] in {"accuracy", "coverage"}]
    if blocking:
        raise LLMError("修订一次后仍未解决的事实或核心覆盖问题：" + "；".join(i["reason"] for i in blocking))
    return revised


def _document_type(value: str, content: str, source_name: str) -> str:
    aliases = {"experimental": "experiment", "research": "experiment", "实验": "experiment",
               "综述": "review", "opinion": "perspective", "观点": "perspective"}
    value = aliases.get(value, value)
    if value == "auto":
        heading = source_name + "\n" + content[:2000]
        if re.search(r"\b(review|综述)\b", heading, re.I):
            return "review"
        if re.search(r"\b(perspective|viewpoint|commentary|观点)\b", heading, re.I):
            return "perspective"
        return "general"
    if value not in prompts.DOCUMENT_TYPE_GUIDANCE:
        raise LLMError(f"未知文献类型：{value}")
    return value


def _page_chunks(pages: list[dict]) -> list[list[dict]]:
    """按页分块，长页再切；每段始终保留输入中的物理页码。"""
    chunks: list[list[dict]] = []
    current: list[dict] = []
    used = 0
    seen: set[int] = set()
    for page in pages:
        number, text = page.get("page"), page.get("text")
        if type(number) is not int or number < 1 or number in seen or not isinstance(text, str):
            raise LLMError("分页材料格式错误或页码重复")
        seen.add(number)
        for part in split_chunks(text, CHUNK_SIZE):
            if not part.strip():
                continue
            if current and used + len(part) > CHUNK_SIZE:
                chunks.append(current)
                current, used = [], 0
            current.append({"page": number, "text": part})
            used += len(part)
    if current:
        chunks.append(current)
    return chunks


def _normalize_quote(value: str) -> str:
    """把引文与原文归一到可比较的规范形，只容忍抽取产生的机械差异。

    PDF 抽取会引入三类与「编造」无关的差异：换行把单词拆开（of\\nten 本应
    是 often）、Unicode 等价字符（−/-、‘/'、连字 ﬀ/ff、µ/μ、全角标点）、软连字符。
    这里用 NFKC 统一等价字符、去掉软连字符、抹平全部空白，再要求字符序列一致——
    模型凭空编造的引文依然无法通过，而「逐字抄对却因断行被误判」的情况会被消除。
    """
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\u00ad", "")   # soft hyphen
    value = value.replace("\u2011", "-")  # non-breaking hyphen -> 普通连字符
    # 断行连字符：行尾的 "-" 后紧跟换行/空白，是排版断词（adap-\\ntations 本应是
    # adaptations）。去掉它再抹空白；词内连字符（state-of-the-art）后跟字母，不受影响。
    value = re.sub(r"-\s+", "", value)
    value = re.sub(r"\s+", "", value)     # 抹平全部空白
    return value


def _quote_in_source(quote: str, source: str) -> bool:
    """判断 quote 是否真实来自 source，容忍机械性微差但不放过编造。

    首选归一化后的连续子串匹配；失败时退回 token 覆盖率：quote 的实义 token 中
    至少 85% 能在 source 里找到。连续子串能证明「非拼接」，token 覆盖率能拦截
    凭空编造（编造句子的关键词在原文不存在），同时容忍模型逐字复制时漏字、
    引用上标、断行连字符等机械性差异。
    """
    if _normalize_quote(quote) in _normalize_quote(source):
        return True
    tokens = re.findall(r"[a-z0-9\u4e00-\u9fff]+", quote.lower())
    if len(tokens) < 5:
        return False
    source_lower = source.lower()
    matched = sum(1 for tok in tokens if tok in source_lower)
    return matched / len(tokens) >= 0.85


def _validated_facts(raw: str, chunk: list[dict], chunk_index: int) -> list[dict]:
    """JSON 结构错误整块失败（触发重试）；坏引文、错字段同样失败，不静默跳过。

    每条保留事实的引文必须逐字落在声称页码，字段必须逐字摘自该引文。任何一处对不上
    都 raise，交由 ``_extract_facts`` 带反馈重试一次——宁可不通过，也不把无法定位的
    引文或「借来」的数字混进合写记录。
    """
    try:
        result = json.loads(_strip_code_fence(raw))
    except (TypeError, ValueError) as exc:
        raise LLMError("事实提取必须返回合法JSON") from exc
    rows = result.get("facts") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise LLMError("事实提取必须包含facts列表")
    # max_points 是软上限：模型偶尔会多返回几条。截断到前 24 条，不因此整片失败。
    if len(rows) > 24:
        rows = rows[:24]
    verified = []
    kinds = {"problem", "method", "finding", "limitation", "example", "background"}
    for index, fact in enumerate(rows, 1):
        if not isinstance(fact, dict):
            continue
        quote, page = fact.get("quote"), fact.get("page")
        if not isinstance(quote, str) or len(quote.strip()) < 8 or type(page) is not int:
            continue
        normalized = _normalize_quote(quote)
        claimed = [part for part in chunk if part["page"] == page]
        if not claimed or normalized not in _normalize_quote(claimed[0]["text"]):
            raise LLMError(f"第{page}页找不到引文，不能凭摘要信任事实")
        if fact.get("kind") not in kinds or not isinstance(fact.get("claim"), str) or not fact["claim"].strip():
            continue
        for field in ("subject", "metric", "value", "unit", "condition", "strength"):
            value = fact.get(field)
            if not isinstance(value, str):
                value = ""
            if value and _normalize_quote(value) not in normalized:
                raise LLMError(f"字段{field}与引文不符，不能凭摘要信任事实")
            fact[field] = value
        record = {key: fact[key] for key in ("page", "kind", "claim", "quote", "subject",
                                            "metric", "value", "unit", "condition", "strength")}
        record.update({"id": f"C{chunk_index:03d}-F{index:02d}", "quote_verified": True})
        verified.append(record)
    return verified


def _extract_facts(client: LLMClient, request: str, chunk: list[dict], index: int) -> list[dict]:
    feedback = ""
    for attempt in range(2):
        raw = client.chat(prompts.SYSTEM_PROMPT, request + feedback)
        try:
            return _validated_facts(raw, chunk, index)
        except LLMError as exc:
            if attempt:
                raise LLMError(f"片段{index}的事实证据校验失败：{exc}") from exc
            feedback = f"\n【上次输出未通过校验】{exc}。重新按原片段提取全部事实，不能猜测证据。"
    raise LLMError("事实提取未完成")


def _hard_cut(body: str, limit: int = BODY_LIMIT) -> str:
    """确定性硬截断：模型压缩无效时，按句砍掉「正文」中段，守住字数红线。

    只删减正文中段的次要表述，保留「## 引言」「## 正文」「## 总结」三节结构，
    引言与总结整体不动。逐句回填时按真实长度测量，末尾再兜底裁剪，保证精确不超限。
    """
    text = _strip_code_fence(body).strip()
    if len(contracts.body_text(text)) <= limit:
        return text
    positions: dict[str, int] = {}
    for match in re.finditer(r"(?m)^##[ \t]+(引言|正文|总结)[ \t]*$", text):
        positions.setdefault(match.group(1), match.start())
    if not all(key in positions for key in ("引言", "正文", "总结")):
        return text[:limit]
    head = text[: positions["正文"]]                       # 标题 + 引言
    middle = text[positions["正文"]: positions["总结"]]    # 正文
    tail = text[positions["总结"]:]                        # 总结
    title_match = re.match(r"(?m)(^##[ \t]+正文[ \t]*\n*)", middle)
    title = title_match.group(1) if title_match else ""
    rest = middle[title_match.end():] if title_match else middle

    def measure(candidate: str) -> int:
        return len(contracts.body_text(candidate))

    kept = ""
    for sentence in re.split(r"(?<=[。！？；])", rest):
        if measure(head + title + kept + sentence + "\n\n" + tail) > limit:
            break
        kept += sentence
    result = (head + title + kept.rstrip() + "\n\n" + tail).strip()
    # 兜底：逐字裁剪尾部，确保不超限（可能出现在预算极紧、单句也塞不下的场景）
    while measure(result) > limit and kept:
        kept = kept[:-1]
        result = (head + title + kept.rstrip() + "\n\n" + tail).strip()
    return result


def _maybe_compress(client: LLMClient, body: str) -> str:
    """正文超过 2500 字时，先调用大模型压缩，压缩无效再确定性硬截断。

    提示词里已给出「不超过 2400 字」的约束，但部分模型对「压缩到 X 字」不敏感，
    压缩结果可能仍超限。这里用硬截断兜底，守住「正文 ≤ 2500 字」这条红线。
    """
    length = len(contracts.body_text(body))
    if length <= BODY_LIMIT:
        return body
    print(f"      正文 {length} 字超限，尝试压缩到 2200 字以内…")
    compressed = _strip_code_fence(client.chat(
        prompts.SYSTEM_PROMPT,
        prompts.COMPRESS_PROMPT.format(length=length, body=body),
    ))
    if len(contracts.body_text(compressed)) > BODY_LIMIT:
        print("      模型压缩仍超限，改按句硬截断兜底…")
        compressed = _hard_cut(compressed, BODY_LIMIT)
    errors = _draft_errors(compressed)
    if errors:
        raise LLMError("压缩结果未通过验收：" + "；".join(errors))
    return compressed


# --------------------------------------------------------------------------
# 中文摘录草稿辅助（须显式调用，不是可直接提交的离线替代品）
# --------------------------------------------------------------------------
# 原文中的小标题形态：「1 研究背景」「2.3 分类方法」「三、结论」
HEADING_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*|[一二三四五六七八九十]+[、.．])\s*\S{0,20}$")


def _is_cjk(char: str) -> bool:
    return "\u4e00" <= char <= "\u9fff"


def _join_lines(lines: list[str]) -> str:
    """拼接句中断行。中文直接相连，西文之间补一个空格，避免把单词粘在一起。"""
    if not lines:
        return ""
    result = lines[0]
    for line in lines[1:]:
        if _is_cjk(result[-1]) or _is_cjk(line[0]):
            result += line
        else:
            result += " " + line
    return result


def _flatten(paragraph: str) -> str:
    """把段内硬换行拼回整句，并剥掉段首的小标题行。

    中文材料常见「1 研究背景\\n城市化进程加快……」这种排版，
    若直接拼接会得到「1 研究背景城市化进程加快」，既难看也影响检索。
    """
    lines = [ln.strip() for ln in paragraph.split("\n") if ln.strip()]
    while len(lines) > 1 and HEADING_RE.match(lines[0]):
        lines.pop(0)
    return _join_lines(lines).strip()


def _guess_title(content: str, fallback: str) -> str:
    """材料首行若是一句短标题，就直接用它当文档标题。

    PDF 首页常有英文页眉（如 "RACING TRAINING"），中文材料却被它占了标题，
    因此纯英文行在中文正文里一律跳过。
    """
    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", content))
    mostly_cjk = cjk_chars > len(content) * 0.15
    for line in content.split("\n")[:5]:
        candidate = line.strip().lstrip("#").strip()
        if not 8 <= len(candidate) <= 40:
            continue
        if re.search(r"[。！？：:，,]", candidate):
            continue
        if mostly_cjk and not re.search(r"[\u4e00-\u9fff]", candidate):
            continue
        return candidate
    return fallback


# 对外暴露的别名：文件名（如 "M01_材料"）不适合当文章标题，
# 优先用原文首行，取不到才退回文件名。
guess_title = _guess_title


def _to_paragraphs(text: str, target: int = 300) -> list[str]:
    """还原段落结构。

    PDF 与网页抽取后常常只剩单个换行，段落间隔丢失。若不处理，
    整篇材料会变成一个"超大段落"，正文只能全塞进一节，结构就废了。
    这里对含换行的块按行重组，攒到 target 字左右、且正好停在句末时断段。
    小标题（如「2 数据与方法」）单独成段，随后由 ``_flatten`` 剥除；
    否则它会和正文粘成「2 数据与方法研究采用……」，句子读不通。
    """
    paragraphs: list[str] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if len(lines) <= 1:
            paragraphs.append(block)
            continue

        buffer = ""
        for line in lines:
            if HEADING_RE.match(line):
                if buffer:
                    paragraphs.append(buffer)
                    buffer = ""
                paragraphs.append(line)  # 单独成段，交给 _flatten 剥掉
                continue
            buffer += line
            if len(buffer) >= target and re.search(r"[。！？；]$", line):
                paragraphs.append(buffer)
                buffer = ""
        if buffer:
            paragraphs.append(buffer)
    return paragraphs


def _split_head(text: str, budget: int) -> tuple[str, str]:
    """按句末切出前段，返回 (前段, 后段)，两段都在文档顺序上。"""
    sentences = [s for s in re.split(r"(?<=[。！？；])", text) if s]
    if len(sentences) <= 1:
        return text, ""
    taken = ""
    index = 0
    while index < len(sentences) and len(taken) < budget:
        taken += sentences[index]
        index += 1
    return taken, "".join(sentences[index:])


def _split_tail(text: str, budget: int) -> tuple[str, str]:
    """按句末切出后段，返回 (前段, 后段)，两段都在文档顺序上。"""
    sentences = [s for s in re.split(r"(?<=[。！？；])", text) if s]
    if len(sentences) <= 1:
        return "", text
    keep: list[str] = []
    for sentence in reversed(sentences):
        keep.insert(0, sentence)
        if sum(len(s) for s in keep) >= budget:
            break
    tail = "".join(keep)
    return text[: len(text) - len(tail)], tail


def generate_extractive(content: str, material_id: str, source_name: str) -> str:
    """摘录中文材料供人工参考；不声称完成科普改写或结构验收。"""
    letters = len(re.findall(r"[A-Za-z\u4e00-\u9fff]", content))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", content))
    if not letters or cjk / letters < 0.15:
        raise LLMError("抽取式引擎仅支持中文摘录草稿，不能把外文摘录作为中文科普交付")
    paragraphs = [_flatten(p) for p in _to_paragraphs(content)]
    paragraphs = [p for p in paragraphs if len(p) >= 12]
    if not paragraphs:
        paragraphs = [content.strip()]

    title = _guess_title(content, source_name)

    # 段落太少时（材料短、或排版把整篇挤成一段）退到句子级切分，
    # 否则引言和正文会取到同一段文字，读起来像复制粘贴。
    units = paragraphs
    if len(units) < 3:
        sentences = [
            s for s in re.split(r"(?<=[。！？；])", "".join(units)) if len(s.strip()) >= 8
        ]
        if len(sentences) >= 3:
            step = max(1, len(sentences) // 3)
            units = ["".join(sentences[i : i + step]) for i in range(0, len(sentences), step)]

    intro = units[0]
    closing = units[-1] if len(units) >= 2 else ""
    if len(units) >= 3:
        body_units = units[1:-1]
    elif len(units) == 2:
        body_units, closing = units[1:2], ""
    else:
        body_units = []

    # 引言过长时只留前几句，剩下的并回正文；结论同理保留末尾几句。
    # 关键是"不整段丢弃"——截断掉的字若直接没了，就是内容不完整。
    if len(intro) > 320:
        head, leftover = _split_head(intro, 200)
        intro = head
        if leftover:
            body_units = [leftover] + body_units
    if len(closing) > 320:
        leftover, tail = _split_tail(closing, 200)
        closing = tail
        if leftover:
            body_units = body_units + [leftover]

    third = max(1, len(body_units) // 3) if body_units else 0
    groups = [
        ("原文提出的问题", body_units[:third]),
        ("使用的方法与数据", body_units[third : third * 2]),
        ("得到的结果与讨论", body_units[third * 2 :]),
    ]

    body_parts: list[str] = []
    for heading, group in groups:
        if not group:
            continue
        body_parts.append(f"### {heading}\n\n" + "\n\n".join(group))
    if not body_parts:
        body_parts.append(
            "### 说明\n\n原文篇幅较短，未包含可以单独成节的细节内容，"
            "本节不作拆分，以免重复引言与总结的表述。"
        )

    return (
        f"# {title}（摘录草稿）\n\n"
        f"## 引言\n\n{intro}\n\n"
        "以下仅为原文摘录草稿，尚未完成科普改写与事实核验，不能直接提交。\n\n"
        f"## 正文\n\n" + "\n\n".join(body_parts) + "\n\n"
        "## 总结\n\n"
        + (f"{closing}\n\n" if closing else "")
        + "以上内容按原文表述摘录整理，未对研究结论、数据含义与适用范围作改写；"
        "原文未展开的部分，本文不作补充推断。\n"
    )


# --------------------------------------------------------------------------
# 组装最终 Markdown
# --------------------------------------------------------------------------
def assemble(body: str, material_id: str, source_name: str, source_text: str,
             engine: str, fallback_title: str) -> str:
    title, rest = _split_title(_strip_code_fence(body), fallback_title)
    document = _ensure_structure(rest, title)
    if not document.startswith("# "):
        document = f"# {title}\n\n{document}"

    provenance = prompts.PROVENANCE_TEMPLATE.format(
        material_id=material_id,
        source_name=source_name,
        fingerprint=fingerprint(source_text),
        engine=engine,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    return f"{document.rstrip()}\n\n{provenance}"


def write_document(docs_dir: Path, index: int, material_id: str, text: str) -> Path:
    """文档名直接采用材料编号（如 AI-01.md），与赛项要求的命名一致。

    赛项规定"文件名须与目录结构一致"，并给出了 AI-01.md / BIO-02.md 的示例，
    因此不再加序号前缀与"科普文档"后缀；领域代码（AI/BIO/IND/MED）本身
    就保证了字母序即材料顺序。
    """
    safe = re.sub(r'[\\/:*?"<>|]', "_", material_id)
    path = docs_dir / f"{safe}.md"
    path.write_text(text, encoding="utf-8")
    return path


def write_manifest(docs_dir: Path, records: list[dict]) -> Path:
    """生成清单，方便自查"材料与文档是否一一对应"。"""
    path = docs_dir / "_manifest.json"
    path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
