"""Source-grounded popular-science rewriting pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader


IDS = ("AI-01", "BIO-02", "IND-03", "MED-04")
HEADINGS = ("## 引言", "## 正文", "## 总结", "## 原文来源")


def normalized(text: str) -> str:
    return " ".join(text.replace("\u00ad", "").split())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_pages(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(f"Source PDF not found: {path}")
    pages = [(page.extract_text() or "") for page in PdfReader(path).pages]
    if not pages or not any(page.strip() for page in pages):
        raise ValueError(f"No selectable text in source PDF: {path}")
    return pages


def evidence_contexts(record: dict[str, Any], pages: list[str]) -> list[dict[str, Any]]:
    contexts = []
    for card in record["cards"]:
        number = card["page"]
        if number < 1 or number > len(pages):
            raise ValueError(f"{card['id']}: page {number} does not exist")
        page = normalized(pages[number - 1])
        quote = normalized(card["quote"])
        at = page.find(quote)
        if at < 0:
            raise ValueError(f"{card['id']}: quote not found on PDF page {number}")
        contexts.append({
            "id": card["id"],
            "page": number,
            "claim": card["claim"],
            "context": page[max(0, at - 500): min(len(page), at + len(quote) + 650)],
        })
    return contexts


def article_body(text: str) -> str:
    start = text.find("## 引言")
    end = text.find("## 原文来源")
    if start < 0 or end < start:
        return ""
    return text[start:end]


def body_count(text: str) -> int:
    # Conservative: count headings, Markdown punctuation, and prose, excluding spaces.
    return len(re.sub(r"\s+", "", article_body(text)))


def validate_article(
    text: str, material_id: str, doi: str, source_title: str | None = None
) -> list[str]:
    issues: list[str] = []
    stripped = text.lstrip()
    if not stripped.startswith("# ") or stripped.startswith("## "):
        issues.append("缺少一级文章标题")
    positions = [text.find(heading) for heading in HEADINGS]
    if any(pos < 0 for pos in positions) or positions != sorted(positions):
        issues.append("章节缺失或顺序错误")
    if body_count(text) > 2500:
        issues.append(f"正文超出2500字：{body_count(text)}")
    if material_id not in text[text.find("## 原文来源"):]:
        issues.append("来源处缺少材料编号")
    if doi not in text[text.find("## 原文来源"):]:
        issues.append("来源处缺少 DOI")
    if source_title and normalized(source_title) not in normalized(
        text[text.find("## 原文来源"):]
    ):
        issues.append("来源处缺少原文标题")
    if re.search(r"【(?:AI|BIO|IND|MED)\d+", text):
        issues.append("正文包含内部证据卡编号")
    if not article_body(text).strip():
        issues.append("正文为空")
    return issues


@dataclass(frozen=True)
class ModelConfig:
    api_url: str
    model: str
    api_key: str
    wire_api: str = "chat_completions"
    headers: dict[str, str] | None = None
    store: bool = False
    temperature: float = 0.2
    timeout: int = 180

    @classmethod
    def from_environment(cls) -> "ModelConfig":
        api_url = os.getenv("MODEL_API_URL", "").strip()
        model = os.getenv("MODEL_NAME", "").strip()
        api_key = os.getenv("MODEL_API_KEY", "").strip()
        missing = [
            name for name, value in (
                ("MODEL_API_URL", api_url),
                ("MODEL_NAME", model),
                ("MODEL_API_KEY", api_key),
            ) if not value
        ]
        if missing:
            raise ValueError("Missing environment variables: " + ", ".join(missing))
        if not api_url.startswith("https://"):
            raise ValueError("MODEL_API_URL must use HTTPS")
        return cls(
            api_url=api_url,
            model=model,
            api_key=api_key,
            wire_api=os.getenv("MODEL_WIRE_API", "chat_completions"),
        )

    @classmethod
    def from_toml(cls, path: Path) -> "ModelConfig":
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        provider_name = data["model_provider"]
        provider = data["model_providers"][provider_name]
        base_url = provider["base_url"].rstrip("/")
        wire_api = provider["wire_api"]
        if wire_api not in ("responses", "chat_completions"):
            raise ValueError(f"Unsupported wire_api: {wire_api}")
        suffix = "/responses" if wire_api == "responses" else "/chat/completions"
        token = provider.get("experimental_bearer_token", "")
        if not token:
            raise ValueError("Credential file has no bearer token")
        return cls(
            api_url=base_url + suffix,
            model=data["model"],
            api_key=token,
            wire_api=wire_api,
            headers=provider.get("http_headers", {}),
            store=not data.get("disable_response_storage", False),
        )


class ModelClient:
    """Calls a Responses or Chat Completions endpoint."""

    def __init__(self, config: ModelConfig):
        self.config = config

    def complete(self, system: str, user: str) -> str:
        if self.config.wire_api == "responses":
            data = {
                "model": self.config.model,
                "instructions": system,
                "input": user,
                "store": self.config.store,
            }
        elif self.config.wire_api == "chat_completions":
            data = {
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": self.config.temperature,
            }
        else:
            raise ValueError(f"Unsupported wire_api: {self.config.wire_api}")
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self.config.headers or {})
        request = urllib.request.Request(
            self.config.api_url,
            data=payload,
            headers=headers,
            method="POST",
        )
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                    result = json.load(response)
                answer = self._response_text(result)
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError("Model returned an empty or non-text response")
                return answer.strip()
            except urllib.error.HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise RuntimeError(f"Model API HTTP {error.code}") from error
                time.sleep(2 ** attempt)
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == 3:
                    raise RuntimeError("Model API did not respond") from error
                time.sleep(2 ** attempt)
        raise RuntimeError("Model API retry limit reached")

    def _response_text(self, result: dict[str, Any]) -> str:
        if self.config.wire_api == "chat_completions":
            return result["choices"][0]["message"]["content"]
        if isinstance(result.get("output_text"), str):
            return result["output_text"]
        texts = []
        for item in result.get("output", []):
            if item.get("type") == "message":
                for part in item.get("content", []):
                    if part.get("type") == "output_text":
                        texts.append(part.get("text", ""))
        return "\n".join(texts)


def build_draft_prompt(
    material_id: str, record: dict[str, Any], contexts: list[dict[str, Any]]
) -> tuple[str, str]:
    system = (
        "你是面向普通读者的科学编辑。只依据用户提供的原文证据写作，"
        "准确区分研究结果、综述和观点，不补造数字、事实或来源。"
    )
    user = (
        f"请将材料 {material_id} 改写成独立的中文 Markdown 科普文章。"
        "结构严格为一级标题、## 引言、## 正文（可含三级标题）、## 总结、"
        "## 原文来源。引言至总结总长度控制在1800至2200字，绝不超过2500字。"
        "解释关键术语，至少使用两处恰当的生活场景或类比，并说明类比的边界。"
        "覆盖全部证据卡，保留研究对象、数字的统计范围、条件和局限。"
        "不要把综述或观点写成原创实验，不要引用证据外的医学建议。"
        f"来源末尾写材料编号、原文标题、DOI {record['doi']}。"
        f"原文标题必须逐字写为：{record['title']}。"
        f"原文作者：{record['authors']}；期刊：{record['venue']}；年份：{record['year']}。"
        "仅返回 Markdown 正文，不要代码围栏。\n\n"
        f"文献类型：{record['type']}\n"
        f"证据卡及对应原文上下文：\n{json.dumps(contexts, ensure_ascii=False, indent=2)}"
    )
    return system, user


def build_review_prompt(
    material_id: str, record: dict[str, Any], contexts: list[dict[str, Any]], draft: str
) -> tuple[str, str]:
    system = (
        "你执行第二阶段科学事实复核。检查下文是否忠于给定原文证据，"
        "重点修正不支持的事实、数字、过强结论、遗漏的限制和误导性类比。"
    )
    user = (
        f"材料编号：{material_id}；类型：{record['type']}；"
        f"原文标题：{record['title']}；作者：{record['authors']}；"
        f"期刊：{record['venue']}；年份：{record['year']}；DOI：{record['doi']}。"
        "逐项核查证据卡；不要用自身记忆补充原文以外的信息。"
        "保留通俗表达和完整结构，正文严格不超过2500字。"
        "仅返回修订后的完整 Markdown，不要解释或代码围栏。\n\n"
        f"证据：{json.dumps(contexts, ensure_ascii=False, indent=2)}\n\n"
        f"待复核草稿：\n{draft}"
    )
    return system, user


def generate_one(
    material_id: str,
    record: dict[str, Any],
    input_dir: Path,
    output_dir: Path,
    log_dir: Path,
    client: ModelClient,
) -> dict[str, Any]:
    source = input_dir / record["file"]
    pages = read_pages(source)
    contexts = evidence_contexts(record, pages)
    draft_system, draft_user = build_draft_prompt(material_id, record, contexts)
    draft = client.complete(draft_system, draft_user)
    review_system, review_user = build_review_prompt(material_id, record, contexts, draft)
    reviewed = client.complete(review_system, review_user)
    revised = reviewed
    issues = validate_article(revised, material_id, record["doi"], record["title"])
    repair_prompt = None
    repair_response = None
    if issues:
        repair_prompt = (
            "请只修复以下格式问题并返回完整Markdown。不要改动有原文依据的事实，"
            "不得保留内部证据卡编号。问题：" + "；".join(issues)
            + "\n\n原文元数据："
            + json.dumps({
                "material_id": material_id,
                "title": record["title"],
                "authors": record["authors"],
                "venue": record["venue"],
                "year": record["year"],
                "doi": record["doi"],
            }, ensure_ascii=False)
            + "\n\n待修复文章：\n" + revised
        )
        repair_response = client.complete("你是严格的Markdown格式编辑。", repair_prompt)
        revised = repair_response
        issues = validate_article(revised, material_id, record["doi"], record["title"])
    if issues:
        raise ValueError(f"{material_id}: output failed validation: {'; '.join(issues)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{material_id}.md"
    destination.write_text(revised.rstrip() + "\n", encoding="utf-8")
    log = {
        "material_id": material_id,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_file": record["file"],
        "source_sha256": sha256(source),
        "model": client.config.model,
        "api_host": urllib.parse.urlsplit(client.config.api_url).hostname,
        "wire_api": client.config.wire_api,
        "temperature": client.config.temperature if client.config.wire_api == "chat_completions" else None,
        "evidence_ids": [item["id"] for item in contexts],
        "draft_prompt": {"system": draft_system, "user": draft_user},
        "draft_response": draft,
        "review_prompt": {"system": review_system, "user": review_user},
        "review_response": reviewed,
        "repair_prompt": repair_prompt,
        "repair_response": repair_response,
        "output_sha256": sha256(destination),
        "body_character_count": body_count(revised),
    }
    (log_dir / f"{material_id}.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "material_id": material_id,
        "output": str(destination),
        "body_character_count": body_count(revised),
        "source_sha256": log["source_sha256"],
    }
