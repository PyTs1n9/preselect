# -*- coding: utf-8 -*-
"""指定材料的科普改写、证据评审与提交验收。生成失败保留上一轮成果。"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import io
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from pipeline import contracts, evidence, extractors, generate, package, prompts, review, settings, validate

SOURCE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SOURCE_DIR.parent
CONFIG_PATH = SOURCE_DIR / "config.json"


def load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_environment() -> list[str]:
    return settings.load_dotenv(SOURCE_DIR / ".env")


def collect_materials(materials_dir: Path) -> tuple[list[Path], list[Path]]:
    """只读枚举，不创建缺失目录。"""
    usable, skipped = [], []
    if not materials_dir.is_dir():
        return usable, skipped
    for path in sorted(materials_dir.iterdir(), key=lambda p: p.name):
        if path.is_file() and not path.name.startswith("."):
            (usable if path.suffix.lower() in extractors.SUPPORTED_SUFFIXES else skipped).append(path)
    return usable, skipped


def _expected_material_ids(config: dict, materials: list[Path]) -> list[str]:
    explicit = (config.get("review") or {}).get("required_material_ids")
    if explicit:
        return sorted({str(item).strip() for item in explicit if str(item).strip()})
    return sorted(extractors.guess_material_id(p, i) for i, p in enumerate(materials, 1))


def _expected_sources(config: dict) -> dict[str, str]:
    return {key: value["source"] for key, value in
            config.get("document", {}).get("overrides", {}).items() if value.get("source")}


def _material_map(materials: list[Path]) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    for i, path in enumerate(materials, 1):
        material_id = extractors.guess_material_id(path, i)
        if material_id in mapping:
            raise ValueError(f"材料编号重复：{material_id}；请修正文件名，不自动改号")
        mapping[material_id] = path
    return mapping


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _directory_snapshot(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    if path.is_symlink() or any(p.is_symlink() for p in path.rglob("*")):
        raise ValueError("输出目录含符号链接，拒绝自动复制或替换")
    return {p.relative_to(path).as_posix(): _sha256(p) for p in path.rglob("*") if p.is_file()}


def _runtime_record() -> dict:
    versions = {}
    for name in ("requests", "pypdf", "fonttools"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    files = [SOURCE_DIR / "main.py", SOURCE_DIR / "config.json", SOURCE_DIR / "requirements.txt"]
    files += sorted((SOURCE_DIR / "pipeline").glob("*.py"))
    return {"python": sys.version.split()[0], "dependencies": versions,
            "code_sha256": {p.relative_to(SOURCE_DIR).as_posix(): _sha256(p) for p in files if p.is_file()}}


def _safe_output_dir(config: dict) -> Path:
    """目录交换只允许项目内独立输出目录，不触碰源码、材料或项目根。"""
    root = PROJECT_ROOT.resolve()
    dest = (root / config.get("output_dir", "docs")).resolve()
    protected_paths = [(root / config.get("materials_dir", "materials")).resolve(), SOURCE_DIR.resolve()]
    if dest == root or root not in dest.parents:
        raise ValueError("output_dir 必须是项目内的独立子目录")
    for protected in protected_paths:
        if dest == protected or dest in protected.parents or protected in dest.parents:
            raise ValueError("output_dir 不得与源码或材料目录重叠")
    return dest


def _publish_documents(staged: Path, destination: Path, backup: Path) -> None:
    """验收后同盘目录交换；失败恢复旧稿，成功仍保留备份。"""
    root = PROJECT_ROOT.resolve()
    paths = [p.resolve() for p in (staged, destination, backup)]
    if any(p == root or root not in p.parents for p in paths):
        raise ValueError("生成目录交换超出项目范围")
    if backup.exists():
        raise ValueError("备份路径已存在，拒绝覆盖")
    destination.parent.mkdir(parents=True, exist_ok=True)
    had_previous = destination.exists()
    if had_previous:
        destination.rename(backup)
    try:
        staged.rename(destination)
    except OSError:
        if had_previous:
            backup.rename(destination)
        raise


def cmd_generate(config: dict) -> int:
    """预检全部输入 → 独立生成并保存证据 → 全部验收 → 替换。"""
    run_dir = None
    try:
        docs_dir = _safe_output_dir(config)
        materials_dir = PROJECT_ROOT / config.get("materials_dir", "materials")
        materials, skipped = collect_materials(materials_dir)
        if skipped:
            raise ValueError("存在不支持的材料，未开始生成：" + "、".join(p.name for p in skipped))
        mapping = _material_map(materials)
        expected_ids = _expected_material_ids(config, materials)
        if not mapping or set(mapping) != set(expected_ids):
            raise ValueError(f"材料编号不完整：期望 {expected_ids}，实际 {sorted(mapping)}")
        llm_cfg, preset, note = settings.resolve_llm(config)
        client = generate.LLMClient(llm_cfg)
        if note or not client.available:
            raise ValueError(note or "缺少模型服务地址/模型名；生成未开始")
        if not os.environ.get(client.api_key_env, "").strip():
            raise ValueError(f"未设置 {client.api_key_env}；生成停止，保留现有文档")
        target_words = int(config.get("document", {}).get("target_words", 1800))
        if not 800 <= target_words <= 2400:
            raise ValueError("document.target_words 应为 800—2400")
        extras = {p.name for p in docs_dir.glob("*.md")} - {f"{x}.md" for x in expected_ids} - {"提交说明.md"}
        if extras:
            raise ValueError("输出目录含未映射文档，请先确认用途：" + "、".join(sorted(extras)))
        previous_docs = _directory_snapshot(docs_dir)
        source_hashes = {mid: _sha256(path) for mid, path in mapping.items()}
        inputs = {}
        for material_id, path in mapping.items():
            pages = extractors.extract_pages(path)
            content = extractors.clean_text("\n".join(p["text"] for p in pages))
            if not content:
                raise ValueError(f"{path.name} 抽取为空")
            inputs[material_id] = (pages, content)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8]
        run_dir = SOURCE_DIR / "result" / "runs" / stamp
        run_dir.mkdir(parents=True)
        staged = run_dir / "generated_docs"
        if docs_dir.exists():
            shutil.copytree(docs_dir, staged)
        else:
            staged.mkdir()
        records = []
        overrides = config.get("document", {}).get("overrides", {})
        runtime = _runtime_record()
        print(f"[引擎] {preset or '自定义'} / {client.model}；生成期间保留现有文档")
        for index, (material_id, path) in enumerate(mapping.items(), 1):
            pages, content = inputs[material_id]
            override = overrides.get(material_id, {})
            source_name = override.get("source") or path.stem
            title = override.get("title") or generate.guess_title(content, source_name)
            artifacts: dict = {}
            print(f"({index}/{len(mapping)}) {path.name} → {material_id}")
            try:
                body = generate.generate_with_llm(client, content, material_id, source_name, target_words,
                    pages=pages, document_type=override.get("document_type", "auto"), artifacts=artifacts,
                    quality_check=True)
            finally:
                # 即使事实/内容核查失败也保存已有诊断，便于定位问题。
                (run_dir / f"{material_id}_evidence.json").write_text(
                    json.dumps(artifacts, ensure_ascii=False, indent=2), encoding="utf-8")
            document = generate.assemble(body, material_id, source_name, content, "llm", title)
            errors = contracts.document_errors(document, material_id, source_name)
            if errors:
                raise ValueError(f"{material_id} 未通过验收：" + "；".join(errors))
            out = generate.write_document(staged, index, material_id, document)
            records.append({"index": index, "material_id": material_id, "source_file": path.name,
                "source_name": source_name, "doc_file": out.name, "engine": "llm",
                "fingerprint": extractors.fingerprint(content), "source_sha256": source_hashes[material_id],
                "output_sha256": _sha256(out), "chars": len(document), "runtime": runtime,
                "model": {key: llm_cfg.get(key) for key in ("model", "temperature", "max_tokens")},
                "document_type": artifacts.get("document_type", override.get("document_type", "auto")),
                "generated_at": datetime.now().astimezone().isoformat(),
                "evidence_file": f"{material_id}_evidence.json", "run_id": stamp})
        generate.write_manifest(staged, records)
        report = validate.Report()
        validate.check_documents(staged, materials_dir, report, expected_ids=expected_ids,
                                 expected_sources=_expected_sources(config))
        if report.failed:
            raise ValueError(report.render())
        if _directory_snapshot(docs_dir) != previous_docs:
            raise ValueError("生成期间现有文档被修改，已中止替换以保留最新版本")
        if any(_sha256(path) != source_hashes[mid] for mid, path in mapping.items()):
            raise ValueError("生成期间原始材料被修改，已中止替换")
        _publish_documents(staged, docs_dir, run_dir / "previous_docs")
        print(f"[完成] {len(records)} 份文档已通过结构验收；旧稿及证据：{run_dir}")
        print("[待审读] 结构验收不证明科学准确；请运行review并逐条核查待复核主张。")
        return 0
    except (ValueError, OSError, extractors.ExtractError, generate.LLMError) as exc:
        print(f"[失败] {exc}\n[保留] 原有文档未被失败产物替换。")
        if run_dir:
            print(f"[诊断] 本轮中间结果：{run_dir}")
        return 1


def _load_review_manifest(manifest: Path) -> tuple[list[dict], dict[str, str]]:
    if not manifest.exists():
        return [], {}
    try:
        records = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
            raise ValueError("生成清单必须是记录对象列表")
        for record in records:
            if any(not isinstance(record.get(key), str) or not record[key].strip()
                   for key in ("doc_file", "source_file", "material_id")):
                raise ValueError("生成清单的doc_file/source_file/material_id必须为非空字符串")
        return records, {r["doc_file"]: r["source_file"] for r in records
                         if r.get("doc_file") and r.get("source_file")}
    except (ValueError, OSError) as exc:
        raise ValueError(f"生成清单损坏，未执行模型评审：{type(exc).__name__}") from exc


def _review_facts(numbers: review.NumberReport, stat: review.DocStats) -> str:
    return "\n".join([
        f"- 正文 {stat.chars} 字符；数字 {numbers.total} 处。数值检出不等于语义正确。",
        f"- 未检出：{numbers.missing}；单位候选冲突：{numbers.unit_mismatch}。",
        f"- 上下文候选冲突：{numbers.context_mismatch}；语义待核验：{numbers.semantic_pending}。",
        "- 不要把解释性衔接词数量作为科普质量，也不要把候选检出作为事实通过。",
    ])


def _print_content_scores(text: str, format_score: int | None = None) -> None:
    maxima = {"科学准确性": 15, "科普表达": 20, "内容完整性": 20}
    found = re.findall(r"^##\s*(科学准确性|科普表达|内容完整性)\s*：\s*(\d+(?:\.\d+)?)/(\d+)", text, re.M)
    scores = {}
    for name, value, maximum in found:
        if name in scores or int(maximum) != maxima[name] or not 0 <= float(value) <= maxima[name]:
            print("[待复核] 评分字段重复或超出范围，不计算小计。")
            return
        scores[name] = float(value)
    if len(scores) == 3:
        print(f"[模拟内容分] {sum(scores.values()):g}/55；不含格式10分和源码35分，不代表官方成绩。")
    else:
        print("[待复核] 内容证据或评分不足，不合成总分。")


def _source_quality_notes() -> None:
    print("\n源码质量（官方35分，仅列证据，不自动赋分）：")
    print("- 输入/生成/评审/验收/打包已分模块；config控制材料类型和模型参数。")
    print("- 运行依赖见requirements；生成保留旧稿，失败不会自动改用英文摘录。")
    print("- 同一验收规则用于生成与提交；测试需另行运行，模块存在不等于测试通过。")
    print("- 准确性仍需原文审读；本地跨语言候选检索不能证明主张成立。")


def _run_review(config: dict, offline: bool = False) -> int:
    from pipeline import single_review
    return single_review.run(config, PROJECT_ROOT, SOURCE_DIR, offline=offline)


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
        return len(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def cmd_review(config: dict, offline: bool = False) -> int:
    captured = io.StringIO()
    with contextlib.redirect_stdout(_Tee(sys.stdout, captured)):
        code = _run_review(config, offline=offline)
    result_dir = SOURCE_DIR / "result"
    result_dir.mkdir(exist_ok=True)
    path = result_dir / (datetime.now().strftime("review_%Y%m%d_%H%M%S_") + uuid4().hex[:6] + ".md")
    path.write_text(captured.getvalue(), encoding="utf-8")
    print(f"[报告] {path}")
    return code


def cmd_validate(config: dict) -> int:
    materials_dir = PROJECT_ROOT / config.get("materials_dir", "materials")
    materials, _ = collect_materials(materials_dir)
    report = validate.run(PROJECT_ROOT, PROJECT_ROOT / config.get("output_dir", "docs"),
        materials_dir, SOURCE_DIR, expected_ids=_expected_material_ids(config, materials),
        expected_sources=_expected_sources(config))
    print(report.render())
    return 1 if report.failed else 0


def cmd_package(config: dict) -> int:
    if cmd_validate(config):
        return 1
    materials, _ = collect_materials(PROJECT_ROOT / config.get("materials_dir", "materials"))
    try:
        output, count = package.build(PROJECT_ROOT, PROJECT_ROOT / config.get("output_dir", "docs"),
            SOURCE_DIR, config.get("package_name", "作品.zip"),
            expected_ids=_expected_material_ids(config, materials), expected_sources=_expected_sources(config),
            materials_dir=PROJECT_ROOT / config.get("materials_dir", "materials"))
    except (OSError, ValueError) as exc:
        print(f"[失败] {exc}")
        return 1
    print(f"[完成] {output}，共{count}个文件；已读回核对，内容质量仍以审读为准。")
    return 0


def cmd_doctor(config: dict, dotenv_keys: list[str], offline: bool = False) -> int:
    errors = []
    print(f"Python {sys.version.split()[0]}")
    if sys.version_info < (3, 10):
        errors.append("需要Python 3.10及以上")
    for name in ("requests", "pypdf", "fontTools"):
        try:
            __import__(name)
            print(f"[通过] {name}可导入")
        except ImportError:
            errors.append(f"缺少{name}：pip install -r requirements.txt")
    cfg, preset, note = settings.resolve_llm(config)
    client = generate.LLMClient(cfg)
    if note or not client.available:
        errors.append(note or "缺少模型服务配置")
    elif not os.environ.get(client.api_key_env, "").strip():
        errors.append(f"缺少{client.api_key_env}；generate将停止，validate和本地review仍可使用")
    elif not offline:
        try:
            client.chat("只回复OK", "连接测试")
            print(f"[通过] {preset or '自定义'}接口返回完整响应")
        except generate.LLMError as exc:
            errors.append(str(exc))
    else:
        print("[未测试] 离线模式跳过API连通性")
    materials, skipped = collect_materials(PROJECT_ROOT / config.get("materials_dir", "materials"))
    try:
        mapping = _material_map(materials)
        if set(mapping) != set(_expected_material_ids(config, materials)) or not mapping:
            errors.append("材料不齐全")
        for path in materials:
            pages = extractors.extract_pages(path)
            print(f"[通过] {path.name}：{len(pages)}页可抽取；图表语义仍需核验")
    except (ValueError, extractors.ExtractError) as exc:
        errors.append(str(exc))
    if skipped:
        errors.append("存在不支持的材料：" + "、".join(p.name for p in skipped))
    for error in errors:
        print(f"[错误] {error}")
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="专业文献科普改写：生成、证据评审、验收、打包")
    parser.add_argument("command", choices=["doctor", "generate", "review", "validate", "package", "all"])
    parser.add_argument("--offline", action="store_true", help="doctor/review只做本地检查，不调用模型")
    args = parser.parse_args()
    if args.offline and args.command not in {"doctor", "review"}:
        parser.error("--offline仅用于doctor或review；生成需要配置模型")
    try:
        config = load_config()
        keys = load_environment()
        if args.command == "doctor":
            return cmd_doctor(config, keys, args.offline)
        if args.command == "review":
            return cmd_review(config, args.offline)
        actions = {"generate": cmd_generate, "validate": cmd_validate, "package": cmd_package}
        if args.command in actions:
            return actions[args.command](config)
        for action in (cmd_generate, cmd_validate, cmd_package):
            code = action(config)
            if code:
                return code
        return 0
    except (OSError, ValueError) as exc:
        print(f"[错误] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
