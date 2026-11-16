"""Claim-level audit of an existing article against verified source excerpts."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from pipeline import IDS, ModelClient, ModelConfig, evidence_contexts, read_pages, sha256


HERE = Path(__file__).resolve().parent
STATUS = {"supported", "needs_qualification", "insufficient_evidence", "illustration"}


def article_paragraphs(article: str) -> list[dict[str, str]]:
    start = article.find("## 引言")
    end = article.find("## 原文来源")
    if start < 0 or end <= start:
        raise ValueError("Article has no complete main text")
    result = []
    for block in re.split(r"\n\s*\n", article[start:end]):
        block = block.strip()
        if block and not block.startswith("#"):
            result.append({"id": f"P{len(result) + 1:02d}", "text": block})
    if not result:
        raise ValueError("Article has no paragraphs to audit")
    return result


def parse_model_json(response: str) -> dict[str, Any]:
    response = response.strip()
    if response.startswith(chr(96) * 3):
        response = re.sub(r"^\x60{3}(?:json)?\s*|\s*\x60{3}$", "", response, flags=re.I)
    data = json.loads(response)
    if not isinstance(data, dict) or not isinstance(data.get("paragraphs"), list):
        raise ValueError("Audit response must contain a paragraphs array")
    return data


def validate_audit(
    data: dict[str, Any],
    paragraphs: list[dict[str, str]],
    contexts: list[dict[str, Any]],
) -> list[str]:
    issues = []
    expected = {item["id"]: item["text"] for item in paragraphs}
    evidence = {item["id"]: item for item in contexts}
    entries = data["paragraphs"]
    seen = [entry.get("id") for entry in entries if isinstance(entry, dict)]
    if len(entries) != len(expected) or set(seen) != set(expected) or len(seen) != len(set(seen)):
        issues.append("Paragraph IDs must cover each article paragraph exactly once")
    for entry in entries:
        if not isinstance(entry, dict):
            issues.append("Paragraph entry is not an object")
            continue
        paragraph_id = entry.get("id")
        if paragraph_id not in expected:
            issues.append(f"{paragraph_id}: unknown paragraph")
            continue
        claims = entry.get("claims")
        if not isinstance(claims, list):
            issues.append(f"{paragraph_id}: claims must be an array")
            continue
        if not claims and not entry.get("no_factual_claim_reason"):
            issues.append(f"{paragraph_id}: empty claims need a reason")
        for index, claim in enumerate(claims, 1):
            prefix = f"{paragraph_id}/C{index}"
            if not isinstance(claim, dict):
                issues.append(f"{prefix}: claim is not an object")
                continue
            excerpt = claim.get("article_excerpt", "")
            if not excerpt or excerpt not in expected[paragraph_id]:
                issues.append(f"{prefix}: excerpt is not in the paragraph")
            status = claim.get("status")
            if status not in STATUS:
                issues.append(f"{prefix}: invalid status")
            refs = claim.get("evidence", [])
            if not isinstance(refs, list):
                issues.append(f"{prefix}: evidence must be an array")
                continue
            if status == "supported" and not refs:
                issues.append(f"{prefix}: supported claim has no evidence")
            if status == "illustration" and not any(
                marker in expected[paragraph_id] for marker in ("例如", "假设", "比作", "可以把", "可把", "设想")
            ):
                issues.append(f"{prefix}: illustration is not signaled as hypothetical")
            for ref in refs:
                card = evidence.get(ref.get("card_id")) if isinstance(ref, dict) else None
                if not card:
                    issues.append(f"{prefix}: unknown evidence card")
                elif (
                    ref.get("page") != card["page"]
                    or not isinstance(ref.get("quote"), str)
                    or not ref["quote"].strip()
                    or ref["quote"] not in card["context"]
                ):
                    issues.append(f"{prefix}: evidence page or quote does not match source context")
    return issues


def audit_one(
    material_id: str,
    record: dict[str, Any],
    source_dir: Path,
    article_dir: Path,
    output_dir: Path,
    client: ModelClient,
) -> dict[str, Any]:
    source = source_dir / record["file"]
    article_path = article_dir / f"{material_id}.md"
    article = article_path.read_text(encoding="utf-8")
    paragraphs = article_paragraphs(article)
    contexts = evidence_contexts(record, read_pages(source))
    system = (
        "你是事实核查助手。只根据给定的英文原文摘录核查中文文章。"
        "每个段落至少核查其关键事实、数字、比较与因果判断；"
        "明确写作假设或类比的例子标illustration，不当作论文报告的实验事实；"
        "没有事实性陈述的段落用空claims并解释原因。"
        "只返回合法JSON，不要Markdown或额外文字。"
    )
    user = (
        "输出JSON格式：{\"paragraphs\":[{\"id\":\"P01\","
        "\"claims\":[{\"article_excerpt\":\"文章中原样摘录的连续短句\","
        "\"status\":\"supported|needs_qualification|insufficient_evidence|illustration\","
        "\"evidence\":[{\"card_id\":\"AI1\",\"page\":1,"
        "\"quote\":\"证据上下文中原样摘录的连续英文短句\"}],"
        "\"reason\":\"简短中文说明\"}],"
        "\"no_factual_claim_reason\":\"仅claims为空时填写\"}]}。"
        "每个段落必须恰好出现一次。若给定摘录不足以支持事实，"
        "标insufficient_evidence，不得凭常识补证；若原文有条件或限制而文章遗漏，"
        "标needs_qualification。illustration必须在文章中用例如、假设或类比明确提示，"
        "且不得冒充原文案例。一个段落可有多个claims。"
        "不同类型的内容要保持原文语气：研究、综述、观点不能混同。"
        f"\n材料：{material_id}；类型：{record['type']}。"
        f"\n论文元数据：标题{record['title']}；作者{record['authors']}；"
        f"期刊{record['venue']}；年份{record['year']}；DOI{record['doi']}。"
        f"\n原文证据：{json.dumps(contexts, ensure_ascii=False)}"
        f"\n待核查段落：{json.dumps(paragraphs, ensure_ascii=False)}"
    )
    data = None
    raw_responses = []
    issues = []
    for attempt in range(2):
        prompt = user if attempt == 0 else user + (
            "\n上一次输出未通过结构校验。请重新输出完整合法JSON。问题："
            + "；".join(issues[:15])
        )
        raw = client.complete(system, prompt)
        raw_responses.append(raw)
        try:
            data = parse_model_json(raw)
            issues = validate_audit(data, paragraphs, contexts)
        except (json.JSONDecodeError, ValueError) as error:
            issues = [str(error)]
        if not issues:
            break
    if data is None or issues:
        raise ValueError(f"{material_id}: audit invalid: {'; '.join(issues)}")
    report = {
        "material_id": material_id,
        "source_sha256": sha256(source),
        "article_sha256": sha256(article_path),
        "model": client.config.model,
        "scope": "Model judgments use the supplied evidence excerpts, not the full PDF.",
        "validation": "Quote, page, paragraph and article excerpt locations were checked mechanically; semantic support requires human review.",
        "paragraphs": data["paragraphs"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{material_id}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / f"{material_id}.responses.json").write_text(
        json.dumps(raw_responses, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    counts = {status: 0 for status in STATUS}
    for paragraph in data["paragraphs"]:
        for claim in paragraph["claims"]:
            counts[claim["status"]] += 1
    return {"material_id": material_id, "paragraphs": len(paragraphs), "claims": counts}


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit article claims against PDF evidence")
    parser.add_argument("--input-dir", type=Path, default=HERE.parent.parent / "赛题")
    parser.add_argument("--article-dir", type=Path, default=HERE.parent)
    parser.add_argument("--output-dir", type=Path, default=HERE.parent / "assessment" / "claim_audit")
    parser.add_argument("--credentials-file", type=Path)
    parser.add_argument("--ids", nargs="+", choices=IDS, default=list(IDS))
    args = parser.parse_args()
    records = json.loads((HERE / "evidence.json").read_text(encoding="utf-8"))
    config = (
        ModelConfig.from_toml(args.credentials_file)
        if args.credentials_file
        else ModelConfig.from_environment()
    )
    client = ModelClient(config)
    for material_id in args.ids:
        result = audit_one(
            material_id, records[material_id], args.input_dir,
            args.article_dir, args.output_dir, client,
        )
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
