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
SINGLE_CALL_LIMIT = 20000
# Larger page-aware chunks reduce round trips for long English PDFs while
# keeping each extraction request within a practical context budget.
CHUNK_SIZE = 24000
# 超限触发压缩；压缩和有限结构修复后仍不合规则失败，不输出占位稿。
BODY_LIMIT = 2500
MAX_RETRY = 2
MAX_REPAIR = 2
# Keep the reduce request bounded. Full page quotes are useful for diagnostics,
# but sending every quote to the writer makes large PDFs exceed provider limits.
REDUCE_EVIDENCE_LIMIT = 32000
MAX_FACTS_PER_CHUNK_FOR_REDUCE = 6


class LLMError(RuntimeError):
    pass


class CoverageAuditError(LLMError):
    """The article contains sentences the evidence audit cannot support."""

    def __init__(self, message: str, issues: list[dict]):
        super().__init__(message)
        self.issues = issues


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
        # Some reasoning-capable providers spend most of a request budget on
        # hidden thinking.  Keep this opt-in so ordinary OpenAI-compatible
        # providers receive the same payload, while a preset can explicitly
        # disable that mode for faster bounded generation.
        self.thinking = str(cfg.get("thinking") or "").strip()

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
                    # A draft that was cut off (or whose provider omitted the
                    # completion reason) is not a complete article.  Letting
                    # it enter structure/coverage checks creates the exact
                    # failure mode this gate is meant to prevent: an
                    # incomplete draft can look valid after a superficial
                    # repair and still lose claims before review.
                    if reason != "stop":
                        raise LLMError(f"模型返回不可用结果（finish_reason={reason!r}）")
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


_FACT_ID_RE = re.compile(
    r"[（(]\s*C\d{3}-F\d{2}(?:\s*[、,，]\s*C\d{3}-F\d{2})*\s*[）)]"
)


def _strip_fact_ids(text: str) -> str:
    """Remove internal evidence IDs if the model leaks them into the article."""
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
    evidence_ids: set[str] | None = None
    if len(content) <= SINGLE_CALL_LIMIT:
        evidence = json.dumps(
            [{"id": "RAW-CONTENT", "quote": content}],
            ensure_ascii=False,
        )
        evidence_quotes = [content]
        evidence_ids = {"RAW-CONTENT"}
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
                index=index, total=len(chunks), max_points=12, chunk=chunk_text,
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
        reduce_facts = _facts_for_reduce(facts)
        evidence = json.dumps(reduce_facts, ensure_ascii=False, separators=(",", ":"))
        evidence_quotes = [row["quote"] for row in reduce_facts]
        evidence_ids = {row["id"] for row in reduce_facts}
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
            if quality_check and evidence_quotes:
                return _strip_fact_ids(_quality_gate(
                    client, body, evidence, evidence_quotes, guidance, artifacts, evidence_ids
                ))
            elif quality_check and artifacts is not None:
                artifacts["quality_checks"].append(
                    {"status": "skipped", "reason": "本轮没有可用于逐字核对的证据引文"})
            return _strip_fact_ids(body)
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
_COVERAGE_DIMENSIONS = {"problem", "method", "finding", "limitation", "example"}


def _claim_sentences(body: str) -> list[str]:
    """Return substantive article sentences that must have an evidence mapping."""
    text = contracts.body_text(_strip_code_fence(body))
    text = re.sub(r"(?m)^#{1,6}[ \t]+.*$", "", text)
    candidates = []
    for paragraph in text.splitlines():
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        candidates.extend(re.split(
            r"(?<=[。！？；!?])\s*|(?<=[.!?])\s+(?=[A-Z\u4e00-\u9fff])",
            paragraph,
        ))
    result: list[str] = []
    for sentence in candidates:
        sentence = sentence.strip(" \t\r\n-·")
        plain = re.sub(r"[_*#>\[\]()（）「」“”\"'：:，,、；;。！？!?…\s]", "", sentence)
        if len(plain) >= 12 and re.search(r"[^\W_]", plain, re.UNICODE):
            result.append(sentence)
    return result


def _sentence_key(value: str) -> str:
    """Normalize a coverage quote for exact whole-sentence matching."""
    value = _normalize_quote(value).strip(" \t\r\n-·•")
    return value.rstrip("。！？!?；;")


def _validated_quality_check(raw: str, body: str, evidence_quotes: list[str],
                            evidence_ids: set[str] | None = None) -> dict:
    """验证核查格式和双方引文，不能凭引用存在断言模型判断正确。"""
    try:
        result = json.loads(_strip_code_fence(raw))
    except (ValueError, TypeError) as exc:
        raise LLMError("内容核查必须返回合法JSON，不能视为通过") from exc
    if not isinstance(result, dict):
        raise LLMError("内容核查不是JSON对象")
    checked, issues, coverage = result.get("checked"), result.get("issues"), result.get("coverage")
    if (not isinstance(checked, list) or not all(isinstance(v, str) for v in checked)
            or set(checked) != _QUALITY_DIMENSIONS):
        raise LLMError("内容核查没有完整声明六项核查维度")
    if not isinstance(issues, list) or len(issues) > 20:
        raise LLMError("内容核查issues必须为最多20项的数组")
    if not isinstance(coverage, list) or len(coverage) > 200:
        raise LLMError("内容核查coverage必须为最多200项的数组")
    normalized_body = _normalize_quote(body)
    claim_sentences = _claim_sentences(body)
    claim_keys = {_sentence_key(sentence): sentence for sentence in claim_sentences}
    sources = [_normalize_quote(quote) for quote in evidence_quotes]
    verified = []
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("category") not in {"accuracy", "coverage", "expression"}:
            raise LLMError("内容核查问题类型无效")
        fields = ("article_quote", "evidence_quote", "reason", "suggestion")
        if not all(isinstance(issue.get(field), str) for field in fields):
            raise LLMError("内容核查缺少引文、理由或修改建议")
        if not issue["reason"].strip() or not issue["suggestion"].strip():
            raise LLMError("内容核查理由或建议为空")
        article = _normalize_quote(issue["article_quote"])
        quote = _normalize_quote(issue["evidence_quote"])
        if not article and issue["category"] != "coverage":
            raise LLMError("非缺漏问题必须引用正文原句")
        if article and article not in normalized_body:
            raise LLMError("核查引用的正文原句不存在，拒绝凭空审稿意见")
        if len(quote) < 8 or not any(quote in source for source in sources):
            raise LLMError("核查证据引文不在本轮原文证据中，不能视为通过")
        verified.append({field: issue[field] for field in ("category",) + fields})
    verified_coverage = []
    for row in coverage:
        if not isinstance(row, dict):
            raise LLMError("内容核查coverage条目必须是对象")
        status = row.get("status")
        dimension = row.get("dimension")
        article_raw = row.get("article_quote")
        quote_raw = row.get("evidence_quote")
        fact_ids = row.get("fact_ids", [])
        if status not in {"supported", "unsupported"}:
            raise LLMError("内容核查coverage的status无效")
        if dimension not in _COVERAGE_DIMENSIONS:
            raise LLMError("内容核查coverage的dimension无效")
        if not isinstance(article_raw, str) or not article_raw.strip():
            raise LLMError("内容核查coverage缺少文章原句")
        if "\n" in article_raw or len(article_raw.strip()) > 600:
            raise LLMError("coverage的article_quote必须是一条完整句子，不能覆盖整段正文")
        article = _normalize_quote(article_raw)
        article_key = _sentence_key(article_raw)
        if article_key not in claim_keys:
            raise LLMError("coverage的article_quote必须完整匹配正文中的一条实质句")
        if not isinstance(quote_raw, str):
            raise LLMError("内容核查coverage缺少原文引文")
        if not isinstance(fact_ids, list) or not all(isinstance(value, str) for value in fact_ids):
            raise LLMError("内容核查coverage的fact_ids必须是字符串数组")
        if evidence_ids is not None:
            if status == "supported" and not fact_ids:
                raise LLMError("有证据句必须绑定至少一个事实ID")
            if any(value not in evidence_ids for value in fact_ids):
                raise LLMError("coverage引用了本轮事实清单之外的事实ID")
        quote = _normalize_quote(quote_raw)
        if status == "supported":
            if len(quote) < 8 or not any(quote in source for source in sources):
                raise LLMError("coverage支持引文不在本轮原文证据中")
        elif quote and not any(quote in source for source in sources):
            raise LLMError("coverage待修订引文不在本轮原文证据中")
        verified_coverage.append({
            "dimension": dimension,
            "article_quote": article_raw,
            "evidence_quote": quote_raw,
            "status": status,
            "fact_ids": fact_ids,
        })
    normalized_coverage = {_sentence_key(row["article_quote"]) for row in verified_coverage}
    missing = [
        sentence for sentence in claim_sentences
        if _sentence_key(sentence) not in normalized_coverage
    ]
    unsupported = [row for row in verified_coverage if row["status"] == "unsupported"]
    if missing:
        issues = [{
            "category": "coverage",
            "article_quote": sentence,
            "evidence_quote": "",
            "reason": "该句没有完成到已核验原文的逐句证据映射。",
            "suggestion": "删除该句，或改写为本轮已核验原文明确支持的表述。",
        } for sentence in missing]
        raise CoverageAuditError(f"内容核查coverage缺少{len(missing)}个事实句映射", issues)
    if unsupported:
        issues = [{
            "category": "accuracy",
            "article_quote": row["article_quote"],
            "evidence_quote": row["evidence_quote"],
            "reason": "该句未能由本轮已核验原文支持，可能包含额外推断、对象或限定条件。",
            "suggestion": "删除无证据内容，或严格按对应原文引文改写并保留对象、条件和结论强度。",
        } for row in unsupported]
        raise CoverageAuditError(f"内容核查发现{len(unsupported)}个无证据事实句", issues)
    return {
        "checked": checked,
        "issues": verified,
        "coverage": verified_coverage,
        "quotes_verified": True,
    }


def _quality_gate(client: LLMClient, body: str, evidence: str, evidence_quotes: list[str],
                  guidance: str, artifacts: dict | None,
                  evidence_ids: set[str] | None = None) -> str:
    """一次核查，至多一次修订和一次复核；未关闭事实/核心覆盖问题则停止。"""
    check_prompt = prompts.QUALITY_CHECK_PROMPT.format(
        body=body, evidence=evidence, guidance=guidance,
    )
    coverage_failure: CoverageAuditError | None = None
    try:
        check = _validated_quality_check(
            client.chat(prompts.SYSTEM_PROMPT, check_prompt), body, evidence_quotes, evidence_ids
        )
    except CoverageAuditError as exc:
        coverage_failure = exc
        check = {
            "checked": sorted(_QUALITY_DIMENSIONS),
            "issues": exc.issues,
            "coverage": [],
            "quotes_verified": False,
        }
    except LLMError as exc:
        if artifacts is not None:
            artifacts["quality_checks"].append({"attempt": 0, "status": "invalid", "error": str(exc)})
        raise
    check.update({
        "attempt": 0,
        "status": "coverage_failed" if coverage_failure else "checked",
        "article_fingerprint": fingerprint(body),
    })
    if artifacts is not None:
        artifacts["quality_checks"].append(check)
    issues = check["issues"]
    if not issues:
        return body

    try:
        revised = _strip_code_fence(client.chat(
            prompts.SYSTEM_PROMPT,
            prompts.QUALITY_REVISE_PROMPT.format(
                body=body, evidence=evidence, guidance=guidance,
                issues=json.dumps(issues, ensure_ascii=False, indent=2),
            ),
        ))
    except LLMError as exc:
        if artifacts is not None:
            artifacts["quality_checks"].append({
                "attempt": 1, "status": "unavailable", "error": str(exc),
            })
        raise
    errors = _draft_errors(revised)
    if errors:
        if artifacts is not None:
            artifacts["quality_checks"].append({
                "attempt": 1, "status": "revision_invalid", "errors": errors,
            })
        raise LLMError("内容修订稿未通过结构/字数验收：" + "；".join(errors))

    try:
        recheck = _validated_quality_check(
            client.chat(prompts.SYSTEM_PROMPT, prompts.QUALITY_CHECK_PROMPT.format(
                body=revised, evidence=evidence, guidance=guidance,
            )),
            revised,
            evidence_quotes,
            evidence_ids,
        )
    except CoverageAuditError as exc:
        if artifacts is not None:
            artifacts["quality_checks"].append({
                "attempt": 1, "status": "coverage_failed", "error": str(exc),
                "issues": exc.issues,
            })
        raise
    except LLMError as exc:
        if artifacts is not None:
            artifacts["quality_checks"].append({
                "attempt": 1, "status": "invalid", "error": str(exc),
            })
        raise
    recheck.update({"attempt": 1, "status": "checked", "article_fingerprint": fingerprint(revised)})
    if artifacts is not None:
        artifacts["quality_checks"].append(recheck)
    blocking = [issue for issue in recheck["issues"] if issue["category"] in {"accuracy", "coverage"}]
    if blocking:
        raise LLMError("修订一次后仍未解决的事实或核心覆盖问题：" + "；".join(
            issue["reason"] for issue in blocking
        ))
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
    value = unicodedata.normalize("NFKC", value).replace("\u00ad", "")
    # PDF extraction often inserts a line break at a printed hyphen boundary.
    value = re.sub(r"(?<=[A-Za-z])\s*[-‐‑‒–—]\s*\n\s*(?=[A-Za-z])", "", value)
    return " ".join(value.split())


def _facts_for_reduce(facts: list[dict]) -> list[dict]:
    """Select a bounded, source-spread set of facts for the writing request."""
    priority = {"problem": 0, "method": 1, "finding": 2, "limitation": 3,
                "example": 4, "background": 5}
    grouped: dict[str, list[dict]] = {}
    for row in facts:
        chunk_id = str(row.get("id", "")).split("-F", 1)[0]
        grouped.setdefault(chunk_id, []).append(row)
    groups = [sorted(rows, key=lambda row: (priority.get(row.get("kind"), 9), row.get("id", "")))
              for _, rows in sorted(grouped.items())]
    selected: list[dict] = []
    used = 2
    for round_index in range(MAX_FACTS_PER_CHUNK_FOR_REDUCE):
        for rows in groups:
            if round_index >= len(rows):
                continue
            source = rows[round_index]
            item = {key: source[key] for key in ("id", "page", "kind", "claim", "quote")}
            item["claim"] = item["claim"][:180]
            # Keep full verified quotations so the quality gate can inspect
            # the complete claim context instead of a clipped sentence.
            item["quote"] = item["quote"][:900]
            size = len(json.dumps(item, ensure_ascii=False, separators=(",", ":"))) + 1
            if used + size > REDUCE_EVIDENCE_LIMIT:
                continue
            selected.append(item)
            used += size
    return selected


def _validated_facts(raw: str, chunk: list[dict], chunk_index: int, *, max_facts: int = 12) -> list[dict]:
    """JSON不合法或任一引文/摘录字段无法定位时整块失败，不忽略坏证据。"""
    try:
        result = json.loads(_strip_code_fence(raw))
    except (TypeError, ValueError) as exc:
        raise LLMError("事实提取必须返回合法JSON") from exc
    rows = result.get("facts") if isinstance(result, dict) else None
    if not isinstance(rows, list) or len(rows) > max_facts:
        raise LLMError(f"事实提取必须包含最多{max_facts}项的facts列表")
    verified = []
    kinds = {"problem", "method", "finding", "limitation", "example", "background"}
    for index, fact in enumerate(rows, 1):
        if not isinstance(fact, dict):
            raise LLMError("事实记录不是对象")
        quote, page = fact.get("quote"), fact.get("page")
        if not isinstance(quote, str) or len(quote.strip()) < 8 or type(page) is not int:
            raise LLMError("事实缺少有效原句或整数页码")
        normalized = _normalize_quote(quote)
        if not any(part["page"] == page and normalized in _normalize_quote(part["text"])
                   for part in chunk):
            raise LLMError(f"事实引文在当前片段第{page}页找不到，不接受编造或跨页拼接证据")
        if fact.get("kind") not in kinds or not isinstance(fact.get("claim"), str) or not fact["claim"].strip():
            raise LLMError("事实缺少类型或概括")
        for field in ("subject", "metric", "value", "unit", "condition", "strength"):
            value = fact.get(field)
            if not isinstance(value, str) or (value and _normalize_quote(value) not in normalized):
                raise LLMError(f"事实字段{field}不是原句中的摘录")
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
            # Validate the complete response as one unit.  Silently dropping
            # malformed rows used to leave a partial fact list that looked
            # complete to the writer and caused unsupported claims downstream.
            verified = _validated_facts(raw, chunk, index)
            if not verified:
                return []
            return verified
        except LLMError as exc:
            if attempt:
                raise LLMError(f"片段{index}的事实证据校验失败：{exc}") from exc
            feedback = f"\n【上次输出未通过校验】{exc}。重新按原片段提取全部事实，不能猜测证据。"
        except (TypeError, ValueError) as exc:
            error = LLMError("事实提取必须返回合法JSON对象")
            if attempt:
                raise LLMError(f"片段{index}的事实证据校验失败：{error}") from exc
            feedback = f"\n【上次输出未通过校验】{error}。重新按要求只输出合法JSON。"
    raise LLMError("事实提取未完成")


def _hard_cut(body: str, limit: int = BODY_LIMIT) -> str:
    """模型压缩仍超限时，确定性删减正文中段，保留文章结构。"""
    text = _strip_code_fence(body).strip()
    if len(contracts.body_text(text)) <= limit:
        return text
    positions = {
        match.group(1): match.start()
        for match in re.finditer(r"(?m)^##[ \t]+(引言|正文|总结)[ \t]*$", text)
    }
    if not all(section in positions for section in ("引言", "正文", "总结")):
        return text[:limit]
    head = text[:positions["正文"]]
    middle = text[positions["正文"]:positions["总结"]]
    tail = text[positions["总结"]:]
    heading = re.match(r"(?m)^##[ \t]+正文[ \t]*\n*", middle)
    body_heading = heading.group(0) if heading else "## 正文\n\n"
    rest = middle[heading.end():] if heading else middle
    kept = ""
    for sentence in re.split(r"(?<=[。！？；])", rest):
        candidate = (head + body_heading + kept + sentence + "\n\n" + tail).strip()
        if len(contracts.body_text(candidate)) > limit:
            break
        kept += sentence
    result = (head + body_heading + kept.rstrip() + "\n\n" + tail).strip()
    while len(contracts.body_text(result)) > limit and kept:
        kept = kept[:-1]
        result = (head + body_heading + kept.rstrip() + "\n\n" + tail).strip()
    return result


def _maybe_compress(client: LLMClient, body: str) -> str:
    """正文超过 2500 字时压缩；模型压缩无效则确定性兜底。"""
    length = len(contracts.body_text(body))
    if length <= BODY_LIMIT:
        return body
    print(f"      正文 {length} 字超限，尝试压缩到 2200 字以内…")
    compressed = _strip_code_fence(client.chat(
        prompts.SYSTEM_PROMPT,
        prompts.COMPRESS_PROMPT.format(length=length, body=body),
    ))
    if len(contracts.body_text(compressed)) > BODY_LIMIT:
        print("      模型压缩仍超限，按句硬截断兜底…")
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
