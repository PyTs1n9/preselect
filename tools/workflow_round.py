"""Collect four review reports and compare a candidate with its fixed baseline."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
ROUNDS = ROOT / "workflow-benchmark" / "rounds"
FLOWS = {
    "accuracy": "2_科学准确性工作流",
    "expression": "3_科普表达工作流",
    "coverage": "4_内容完整性工作流",
    "format": "5_格式规范性工作流",
}
MAXIMA = {"accuracy": 15, "expression": 20, "coverage": 20, "format": 10}
IDS = {"AI-01", "BIO-02", "IND-03", "MED-04"}
EVALUATOR_FILES = ["pipeline/criterion.py", "pipeline/single_review.py", "pipeline/evidence.py",
                   "pipeline/contracts.py", "pipeline/review.py", "pipeline/settings.py", "pipeline/extractors.py",
                   "pipeline/validate.py", "pipeline/package.py"]
GENERATION_REVIEW_NAMES = {"LLMClient", "LLMError", "_strip_code_fence", "_normalize_quote",
                           "_page_chunks", "_validated_facts", "CHUNK_SIZE", "MAX_RETRY"}


def generation_review_fingerprint(source):
    tree = ast.parse(source)
    parts = []
    found = set()
    for node in tree.body:
        names = {node.name} if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else set()
        if isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
        if names & GENERATION_REVIEW_NAMES or isinstance(node, (ast.Import, ast.ImportFrom)):
            parts.append(ast.dump(node, include_attributes=False))
            found.update(names & GENERATION_REVIEW_NAMES)
    if found != GENERATION_REVIEW_NAMES:
        raise ValueError("生成模块的评审依赖已改名或缺失，需复核后重新建立基线")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def round_path(name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise ValueError("轮次名称只接受字母、数字、下划线和连字符，最多64字符")
    return ROUNDS / name


def load_round(path):
    return {key: json.loads((path / f"{key}.json").read_text(encoding="utf-8")) for key in FLOWS}


def collect(name):
    destination = round_path(name)
    if destination.exists():
        raise ValueError(f"轮次已存在，拒绝覆盖：{destination}")
    reports = {}
    for key, flow in FLOWS.items():
        directory = ROOT / flow / "source_code" / "result"
        paths = sorted(directory.glob("review_*.json"), key=lambda p: p.stat().st_mtime_ns)
        if not paths:
            raise ValueError(f"{flow} 尚无结构化报告，请先运行 review")
        report = paths[-1]
        payload = json.loads(report.read_text(encoding="utf-8"))
        if payload.get("criterion") != key:
            raise ValueError(f"报告指标不符：{report}")
        if payload.get("evaluator_unchanged") is not True:
            raise ValueError(f"{flow} 评审运行期间评审源码有变化或未记录稳定性，请重新运行")
        generate_path = directory.parent / "pipeline" / "generate.py"
        generate_bytes = generate_path.read_bytes()
        if hashlib.sha256(generate_bytes).hexdigest() != payload.get("evaluator_sha256", {}).get("pipeline/generate.py"):
            raise ValueError(f"{flow} 的生成模块在评审后发生变化，请重新运行评审再收集")
        payload["generation_review_sha256"] = generation_review_fingerprint(generate_bytes.decode("utf-8"))
        if key == "format":
            checks = directory / "format_rejection_checks.json"
            if checks.is_file():
                payload["rejection_checks"] = json.loads(checks.read_text(encoding="utf-8"))
        reports[key] = (report, payload)
    hashes = [payload["document_sha256"] for _, payload in reports.values()]
    if any(value != hashes[0] for value in hashes[1:]):
        raise ValueError("四项评审使用的文章不一致，请先对同一组文章运行四项评审")
    if set(hashes[0]) != {f"{mid}.md" for mid in IDS}:
        raise ValueError("本轮必须包含四份指定材料对应的文章")
    destination.mkdir(parents=True)
    for key, (report, payload) in reports.items():
        (destination / f"{key}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        for markdown in sorted(report.parent.glob("review_*.md"), reverse=True):
            if str(report) in markdown.read_text(encoding="utf-8"):
                shutil.copy2(markdown, destination / f"{key}.md")
                break
    print(f"[收集] {destination}")
    for key, (_, payload) in reports.items():
        print(f"- {key}: {'完整评分' if payload.get('complete') else '有失败或待核验项，不能验收提升'}")


def average_percent(report, key):
    if report.get("criterion") != key or report.get("max_score") != MAXIMA[key]:
        raise ValueError(f"{key} 的指标或满分字段无效")
    if not report.get("complete") or not report.get("inputs_unchanged"):
        raise ValueError(f"{key} 评审未完成、存在待核验项或输入发生变化")
    if set(report.get("document_sha256", {})) != {f"{mid}.md" for mid in IDS}:
        raise ValueError(f"{key} 未评估完整的四篇指定文章")
    entries = report.get("results", [])
    expected = {"group"} if key == "format" else IDS
    actual = [entry.get("material_id") for entry in entries]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError(f"{key} 缺少评分或存在重复评分")
    scores = []
    for entry in entries:
        score = entry.get("score")
        if (type(score) not in (int, float) or not math.isfinite(score)
                or not 0 <= score <= MAXIMA[key] or entry.get("pending") or entry.get("error")):
            raise ValueError(f"{key}/{entry.get('material_id')} 不具备有效完整评分")
        scores.append(score / MAXIMA[key] * 100)
    return sum(scores) / len(scores)


def compare(baseline, candidate, target):
    rows, blockers = [], []
    for label, group in (("基线", baseline), ("候选", candidate)):
        hashes = [report.get("document_sha256") for report in group.values()]
        if any(value != hashes[0] for value in hashes[1:]):
            blockers.append(f"{label}四项评审使用的文章不一致")
    for key in FLOWS:
        before, after = baseline[key], candidate[key]
        for field in ("model", "review_config", "material_sha256"):
            if not before.get(field) or before.get(field) != after.get(field):
                blockers.append(f"{key} 的 {field} 与基线不一致")
        for filename in EVALUATOR_FILES:
            digest = before.get("evaluator_sha256", {}).get(filename)
            if not digest or digest != after.get("evaluator_sha256", {}).get(filename):
                blockers.append(f"{key} 的评审依赖已变化：{filename}")
        if before.get("generation_review_sha256"):
            if before["generation_review_sha256"] != after.get("generation_review_sha256"):
                blockers.append(f"{key} 的生成模块中评审所依赖的校验、分片或模型客户端已变化")
        else:
            # 旧诊断报告未记录函数级依赖，继续采用保守的整文件比较。
            digest = before.get("evaluator_sha256", {}).get("pipeline/generate.py")
            if not digest or digest != after.get("evaluator_sha256", {}).get("pipeline/generate.py"):
                blockers.append(f"{key} 的评审依赖已变化：pipeline/generate.py")
        try:
            old = average_percent(before, key)
            new = average_percent(after, key)
            delta = new - old
            rows.append({"criterion": key, "baseline_percent": old, "candidate_percent": new,
                         "delta_percentage_points": delta})
            if key == "format" and old >= 100 - 1e-9:
                if new < 100 - 1e-9:
                    blockers.append("格式基线已满分，候选稿须保持10/10")
                checks = after.get("rejection_checks", {})
                if (checks.get("passed") is not True or checks.get("tests_run", 0) <= 0
                        or any(checks.get("evaluator_sha256", {}).get(name) != after.get("evaluator_sha256", {}).get(name)
                            for name in ("pipeline/contracts.py", "pipeline/validate.py", "pipeline/package.py"))):
                    blockers.append("格式负例拦截检查未通过，或检查结果与当前源码不一致")
            elif key == target and delta < 5 - 1e-9:
                blockers.append(f"{key} 平均提升不足5个百分点")
            elif key != target and delta < -5 - 1e-9:
                blockers.append(f"{key} 平均降幅超过5个百分点")
        except ValueError as exc:
            blockers.append(str(exc))
        if key in {"accuracy", "coverage"}:
            old_risks = {entry["material_id"]: entry.get("risk") for entry in before.get("results", [])}
            for entry in after.get("results", []):
                if entry.get("risk") == "高" and old_risks.get(entry["material_id"]) != "高":
                    blockers.append(f"{key}/{entry['material_id']} 新增高风险问题")
    if candidate["format"].get("hard_checks_passed") is not True:
        blockers.append("候选稿的整组格式与ZIP硬性检查未全部通过")
    return {"target": target, "numerical_gate_passed": not blockers, "rows": rows,
            "blockers": blockers, "requires_content_review": True,
            "note": "数值门槛通过后，仍需逐条确认没有新增严重事实或内容问题；此工具不执行合并。"}


def check_format():
    import hashlib

    source = ROOT / FLOWS["format"] / "source_code"
    sys.path.insert(0, str(source))
    suite = unittest.defaultTestLoader.discover(str(source / "tests"), pattern="test_validation_regressions.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    names = ("pipeline/contracts.py", "pipeline/validate.py", "pipeline/package.py")
    payload = {"passed": result.wasSuccessful() and result.testsRun > 0 and not result.skipped,
        "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "evaluator_sha256": {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in names}}
    directory = source / "result"
    directory.mkdir(exist_ok=True)
    path = directory / "format_rejection_checks.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[格式负例检查] {path}")
    return 0 if payload["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("name")
    sub.add_parser("check-format")
    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("baseline")
    compare_parser.add_argument("candidate")
    compare_parser.add_argument("--target", required=True, choices=FLOWS)
    args = parser.parse_args()
    try:
        if args.command == "check-format":
            return check_format()
        if args.command == "collect":
            collect(args.name)
            return 0
        baseline = round_path(args.baseline)
        candidate = round_path(args.candidate)
        result = compare(load_round(baseline), load_round(candidate), args.target)
        path = candidate / f"comparison-{args.target}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("指标 | 基线百分比 | 候选百分比 | 变化（百分点）")
        for row in result["rows"]:
            print(f"{row['criterion']} | {row['baseline_percent']:.2f} | {row['candidate_percent']:.2f} | {row['delta_percentage_points']:+.2f}")
        for blocker in result["blockers"]:
            print("[未通过] " + blocker)
        print(result["note"])
        print(f"[对比报告] {path}")
        return 0 if result["numerical_gate_passed"] else 1
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"[错误] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
