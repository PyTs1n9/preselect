"""Mechanical checks; subjective dimensions are not scored here."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from audit import article_paragraphs, validate_audit
from pipeline import IDS, body_count, evidence_contexts, normalized, read_pages, sha256, validate_article


HERE = Path(__file__).resolve().parent
MAXIMA = {
    "scientific_accuracy": 15,
    "popular_expression": 20,
    "content_completeness": 20,
    "source_quality": 35,
}


def check(
    article_dir: Path,
    input_dir: Path,
    review_path: Path | None = None,
    audit_dir: Path | None = None,
    resolution_path: Path | None = None,
) -> dict:
    records = json.loads((HERE / "evidence.json").read_text(encoding="utf-8"))
    articles: dict[str, dict] = {}
    source_evidence: dict[str, dict] = {}
    source_contexts: dict[str, list[dict]] = {}
    all_present = True
    all_within_limit = True
    all_structured = True
    all_sources = True
    all_utf8 = True

    for material_id in IDS:
        record = records[material_id]
        path = article_dir / f"{material_id}.md"
        source = input_dir / record["file"]
        try:
            cards = evidence_contexts(record, read_pages(source))
            source_contexts[material_id] = cards
            source_evidence[material_id] = {"checked": len(cards), "issues": []}
        except (FileNotFoundError, ValueError) as error:
            source_evidence[material_id] = {"checked": 0, "issues": [str(error)]}

        if not path.is_file():
            all_present = False
            all_within_limit = False
            all_structured = False
            all_sources = False
            articles[material_id] = {"exists": False, "issues": ["文件缺失"]}
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            all_utf8 = False
            all_within_limit = False
            all_structured = False
            all_sources = False
            articles[material_id] = {"exists": True, "issues": ["不是有效的UTF-8文件"]}
            continue

        issues = validate_article(text, material_id, record["doi"], record["title"])
        count = body_count(text)
        all_within_limit &= count <= 2500 and count > 0
        all_structured &= not any(
            issue in ("章节缺失或顺序错误", "缺少一级文章标题", "正文包含内部证据卡编号")
            for issue in issues
        )
        all_sources &= not any("来源" in issue for issue in issues)
        articles[material_id] = {
            "exists": True,
            "body_characters_conservative": count,
            "issues": issues,
        }

    # 分值与 assessment/SCORING.md 的格式项保持一致；ZIP 项在打包后单独验收。
    # 齐全缺失比字数超限更难补救，故齐全 3 分、字数 2 分。
    format_checks = {
        "four_utf8_markdown_files": {"passed": all_present and all_utf8, "points": 3},
        "body_limit": {"passed": all_present and all_within_limit, "points": 2},
        "required_structure": {"passed": all_present and all_structured, "points": 2},
        "material_and_source": {"passed": all_present and all_sources, "points": 2},
        "single_zip_layout": {"passed": None, "points": 1, "status": "deferred"},
    }
    assessed_points = sum(
        value["points"] for value in format_checks.values()
        if value.get("status") != "deferred"
    )
    earned_points = sum(
        value["points"] for value in format_checks.values() if value["passed"] is True
    )
    # A deferred packaging check is outside the current milestone, not a failed check.
    format_score = round(10 * earned_points / assessed_points) if assessed_points else None
    result = {
        "label": "客观检查结果；主观维度不由程序评分",
        "format_score": format_score,
        "format_max": 10,
        "format_assessed_points": assessed_points,
        "format_earned_points": earned_points,
        "submission_format_score": None,
        "format_checks": format_checks,
        "articles": articles,
        "source_evidence": source_evidence,
        "manual_review": None,
        "claim_audit": None,
        "total_estimate": None,
        "submission_total_estimate": None,
    }

    audit_summary = {
        "stale_or_missing": [], "invalid": {}, "unresolved": [], "claim_counts": {},
    }
    resolutions = {}
    if resolution_path and resolution_path.is_file():
        resolutions = json.loads(resolution_path.read_text(encoding="utf-8"))
    if audit_dir:
        for material_id in IDS:
            article_path = article_dir / f"{material_id}.md"
            audit_path = audit_dir / f"{material_id}.json"
            if not article_path.is_file() or not audit_path.is_file():
                audit_summary["stale_or_missing"].append(material_id)
                continue
            try:
                audit = json.loads(audit_path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                audit_summary["invalid"][material_id] = [str(error)]
                continue
            source_path = input_dir / records[material_id]["file"]
            if (
                audit.get("article_sha256") != sha256(article_path)
                or not source_path.is_file()
                or audit.get("source_sha256") != sha256(source_path)
            ):
                audit_summary["stale_or_missing"].append(material_id)
                continue
            try:
                article = article_path.read_text(encoding="utf-8")
                paragraphs = article_paragraphs(article)
                issues = validate_audit(audit, paragraphs, source_contexts[material_id])
            except (UnicodeDecodeError, ValueError, KeyError) as error:
                issues = [str(error)]
            if issues:
                audit_summary["invalid"][material_id] = issues
                continue
            counts = {}
            for paragraph in audit.get("paragraphs", []):
                for index, claim in enumerate(paragraph.get("claims", []), 1):
                    status = claim.get("status")
                    counts[status] = counts.get(status, 0) + 1
                    if status in ("needs_qualification", "insufficient_evidence"):
                        key = f"{material_id}/{paragraph['id']}/C{index}"
                        decision = resolutions.get(key, {})
                        if (
                            decision.get("resolution") not in (
                                "source_checked", "accepted_as_explanation"
                            )
                            or not decision.get("reason")
                            or decision.get("article_excerpt") != claim.get("article_excerpt")
                        ):
                            audit_summary["unresolved"].append(key)
            audit_summary["claim_counts"][material_id] = counts
        result["claim_audit"] = audit_summary

    if review_path and review_path.is_file():
        review = json.loads(review_path.read_text(encoding="utf-8"))
        scores = review.get("scores", {})
        for dimension, maximum in MAXIMA.items():
            score = scores.get(dimension)
            if not isinstance(score, (int, float)) or not 0 <= score <= maximum:
                raise ValueError(f"Invalid manual score for {dimension}")
        coverage_issues = []
        stale_articles = []
        for material_id in IDS:
            path = article_dir / f"{material_id}.md"
            recorded_hash = review.get("article_sha256", {}).get(material_id)
            if not path.exists() or not recorded_hash or recorded_hash != sha256(path):
                stale_articles.append(material_id)
            article = normalized(path.read_text(encoding="utf-8")) if path.exists() else ""
            for card in records[material_id]["cards"]:
                item = review.get("coverage", {}).get(material_id, {}).get(card["id"], {})
                excerpt = normalized(item.get("article_excerpt", ""))
                if item.get("status") != "covered" or not excerpt or excerpt not in article:
                    coverage_issues.append(f"{material_id}/{card['id']}: not documented as covered")
        result["manual_review"] = {
            "scores": scores,
            "coverage_issues": coverage_issues,
            "stale_articles": stale_articles,
            "limitations": review.get("limitations", []),
        }
        audit_ready = not audit_dir or (
            not audit_summary["stale_or_missing"]
            and not audit_summary["invalid"]
            and not audit_summary["unresolved"]
        )
        if not stale_articles and not coverage_issues and audit_ready and format_score is not None:
            result["total_estimate"] = format_score + sum(scores.values())
    return result


def default_input_dir() -> Path:
    """Locate the competition materials without hardcoding an absolute path."""
    for parent in (HERE.parents[1], HERE.parents[2]):
        candidate = parent / "赛题"
        if candidate.is_dir():
            return candidate
    return HERE.parents[1] / "赛题"


def main() -> None:
    parser = argparse.ArgumentParser(description="Check competition draft and score record")
    parser.add_argument("--article-dir", type=Path, default=HERE.parent)
    parser.add_argument("--input-dir", type=Path, default=default_input_dir())
    parser.add_argument(
        "--review",
        type=Path,
        default=None,
        help="可选的外部评分记录；不提供时只输出客观检查，不计算总分",
    )
    parser.add_argument(
        "--audit-dir",
        type=Path,
        default=None,
        help="可选的逐段审计目录；不提供时只核对格式、字数与证据页",
    )
    parser.add_argument(
        "--audit-resolutions",
        type=Path,
        default=None,
        help="可选的审计处理记录；仅在提供 --audit-dir 时需要",
    )
    args = parser.parse_args()
    print(json.dumps(
        check(args.article_dir, args.input_dir, args.review, args.audit_dir, args.audit_resolutions),
        ensure_ascii=False, indent=2,
    ))


if __name__ == "__main__":
    main()
