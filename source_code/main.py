# -*- coding: utf-8 -*-
"""BRICS2026-ST-003「人工智能大模型产业开发与应用」预选赛作品生成入口。

    python main.py doctor     # 环境体检：确认除密钥外的配置都已就绪
    python main.py generate   # 读取 materials/，生成 docs/ 下的科普文档
    python main.py review     # 质量评审：核对数值、按评分维度打分
    python main.py validate   # 提交前自检（格式与结构）
    python main.py package    # 打包为单个压缩包
    python main.py all        # 依次执行 生成 / 自检 / 打包

目录约定
--------
    preselect-2026/
    ├── materials/      参赛者把组委会下发的材料放这里（不提交）
    ├── docs/           程序生成的科普文档（提交）
    └── source_code/    程序源码（提交）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from pipeline import extractors, generate, package, prompts, review, settings, validate

SOURCE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SOURCE_DIR.parent
CONFIG_PATH = SOURCE_DIR / "config.json"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"找不到配置文件：{CONFIG_PATH}")
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_environment() -> list[str]:
    """读取 source_code/.env（若存在），返回实际生效的键名。"""
    return settings.load_dotenv(SOURCE_DIR / ".env")


def collect_materials(materials_dir: Path) -> tuple[list[Path], list[Path]]:
    """返回 (可用材料, 被忽略的文件)。

    被忽略的文件必须报出来：若组委会发了 8 份材料而程序只认 7 份，
    就会少交一篇文档，"一一对应"那 20 分直接丢，而且很难在最后自查时发现。
    """
    if not materials_dir.exists():
        materials_dir.mkdir(parents=True, exist_ok=True)
        return [], []

    usable: list[Path] = []
    skipped: list[Path] = []
    for path in sorted(materials_dir.iterdir(), key=lambda p: p.name):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() in extractors.SUPPORTED_SUFFIXES:
            usable.append(path)
        else:
            skipped.append(path)
    return usable, skipped


def cmd_generate(config: dict) -> int:
    materials_dir = PROJECT_ROOT / config.get("materials_dir", "materials")
    docs_dir = PROJECT_ROOT / config.get("output_dir", "docs")
    docs_dir.mkdir(parents=True, exist_ok=True)

    # 清掉上一轮的产物，否则换了材料再跑会残留旧文档，
    # 提交时就会出现"文档比材料多"，或者干脆把过期文档一起交上去。
    # docs/ 是纯输出目录，全部 .md 均为程序生成，可放心清理。
    stale = sorted(docs_dir.glob("*.md"))
    for old in stale:
        old.unlink()
    if stale:
        print(f"[清理] 已移除上一轮生成的 {len(stale)} 份文档")
    (docs_dir / "_manifest.json").unlink(missing_ok=True)

    materials, skipped = collect_materials(materials_dir)
    if not materials:
        print(f"[提示] {materials_dir} 下没有可处理的材料。")
        print("       赛题材料下载后放进该目录，再重新运行本命令。")
        if skipped:
            print(f"       注意：该目录有 {len(skipped)} 个文件格式不支持，已忽略。")
        return 1

    llm_cfg, preset_name, preset_note = settings.resolve_llm(config)
    if preset_note:
        print(f"[配置] {preset_note}")
    client = generate.LLMClient(llm_cfg)
    target_words = int(config.get("document", {}).get("target_words", 1800))
    overrides = config.get("document", {}).get("overrides", {}) or {}

    use_llm = client.available
    if use_llm:
        print(f"[引擎] llm  ->  {client.base_url}  model={client.model}")
        if not os.environ.get(client.api_key_env, "").strip():
            print(f"       [注意] 环境变量 {client.api_key_env} 未设置，将在调用时回退到离线引擎。")
            print("              填好 source_code/.env 里的密钥后重跑，即可自动切换。")
    else:
        print("[引擎] extractive  ->  未配置服务商，使用离线抽取式成文")
        print("       如需更高质量：在 config.json 的 llm.preset 选一家，并把密钥写进 .env。")
        print("       先运行 python main.py doctor 可以看到还缺哪些配置。")

    print(f"[输入] 共 {len(materials)} 份材料\n")
    if skipped:
        print(f"[警告] 以下 {len(skipped)} 个文件格式不支持，已被忽略：")
        for item in skipped:
            print(f"       - {item.name}")
        print("       若这些也是赛题材料，请先转成 txt / md / docx / html / pdf，")
        print("       否则提交的文档数会少于材料数，影响「一一对应」。\n")

    records: list[dict] = []
    failed: list[str] = []

    # 先为全部材料分配唯一编号，避免生成到一半才发现撞号
    assignments: dict[int, str] = {}
    used_ids: set[str] = set()
    had_collision = False
    for index, path in enumerate(materials, start=1):
        material_id = extractors.guess_material_id(path, index)
        if material_id in used_ids:
            unique_id = f"{material_id}-{index:02d}"
            print(f"[提醒] {path.name} 的材料编号 {material_id} 与前面的文件重复，改用 {unique_id}")
            material_id = unique_id
            had_collision = True
        used_ids.add(material_id)
        assignments[index] = material_id
    if had_collision:
        print()

    for index, path in enumerate(materials, start=1):
        material_id = assignments[index]
        override = overrides.get(material_id, {})
        source_name = override.get("source") or path.stem
        print(f"({index}/{len(materials)}) {path.name}  ->  {material_id}")

        try:
            content = extractors.extract_text(path)
        except extractors.ExtractError as exc:
            print(f"    [失败] {exc}")
            failed.append(f"{path.name}: {exc}")
            continue

        # 英文文献的 PDF 首行常是期刊页眉（如 "Nature | Vol 630 | ..."），
        # guess_title 会误拿它当标题；配置里显式给出 title 时优先使用。
        fallback_title = override.get("title") or generate.guess_title(content, source_name)

        engine = "llm" if use_llm else "extractive"
        try:
            if use_llm:
                body = generate.generate_with_llm(
                    client, content, material_id, source_name, target_words
                )
            else:
                body = generate.generate_extractive(content, material_id, source_name)
        except generate.LLMError as exc:
            print(f"    [回退] LLM 失败，改用抽取式：{exc}")
            body = generate.generate_extractive(content, material_id, source_name)
            engine = "extractive（LLM 失败回退）"

        document = generate.assemble(
            body,
            material_id,
            source_name,
            content,
            engine,
            fallback_title,
        )
        out = generate.write_document(docs_dir, index, material_id, document)
        print(f"    已生成 {out.name}（{len(document)} 字符）")

        records.append(
            {
                "index": index,
                "material_id": material_id,
                "source_file": path.name,
                "source_name": source_name,
                "doc_file": out.name,
                "engine": engine,
                "fingerprint": extractors.fingerprint(content),
                "chars": len(document),
            }
        )

    manifest = generate.write_manifest(docs_dir, records)
    print(f"\n[清单] {manifest}")

    if failed:
        print("\n[未完成] 以下材料需要人工处理：")
        for item in failed:
            print(f"  - {item}")
        return 1
    print(f"\n完成：{len(records)} 份文档 -> {docs_dir}")
    return 0


def cmd_review(config: dict) -> int:
    """按评分维度评审已生成的文档。

    分两层：数字核对不依赖网络，能查出模型编造的数据；维度打分需要大模型。
    两者都不改变 docs/ 里的任何文件，只输出诊断意见。
    """
    docs_dir = PROJECT_ROOT / config.get("output_dir", "docs")
    materials_dir = PROJECT_ROOT / config.get("materials_dir", "materials")

    docs = (
        sorted(p for p in docs_dir.glob("*.md") if p.name != "提交说明.md")
        if docs_dir.exists()
        else []
    )
    if not docs:
        print(f"[提示] {docs_dir} 下还没有文档，请先运行：python main.py generate")
        return 1

    # 用生成清单把文档对应回原始材料；没有清单时退化为按顺序配对
    manifest = docs_dir / "_manifest.json"
    source_of: dict[str, str] = {}
    if manifest.exists():
        try:
            records = json.loads(manifest.read_text(encoding="utf-8"))
            for record in records if isinstance(records, list) else []:
                source_of[record.get("doc_file", "")] = record.get("source_file", "")
        except (json.JSONDecodeError, OSError):
            source_of = {}

    usable, _ = collect_materials(materials_dir)
    fallback_sources = [p.name for p in usable]

    llm_cfg, preset, _ = settings.resolve_llm(config)
    client = generate.LLMClient(llm_cfg)
    use_llm = client.available

    print("=" * 62)
    print("作品质量评审（只读，不修改任何文件）")
    print("=" * 62)
    if use_llm:
        print(f"  评审引擎：{preset}  {client.model}")
    else:
        print("  评审引擎：未配置大模型，只做数字核对与结构统计")
    print()

    suspicious_total = 0
    for index, doc in enumerate(docs):
        document = doc.read_text(encoding="utf-8")
        source_name = source_of.get(doc.name) or (
            fallback_sources[index] if index < len(fallback_sources) else ""
        )
        source_path = materials_dir / source_name if source_name else None

        print("-" * 62)
        print(f"{doc.name}")
        print("-" * 62)

        if not source_path or not source_path.exists():
            print("  [跳过] 找不到对应的原始材料，无法核对")
            print()
            continue

        try:
            source_text = extractors.extract_text(source_path)
        except extractors.ExtractError as exc:
            print(f"  [跳过] 材料读取失败：{exc}")
            print()
            continue

        number_report = review.check_numbers(document, source_text)
        stat = review.stats(document)

        print(
            f"  篇幅 {stat.chars} 字符 / {stat.paragraphs} 段 / "
            f"{stat.h3_sections} 个小节 / {stat.numbers} 处数值"
        )
        print(
            f"  数值核对：{number_report.total} 处可核对，"
            f"一致 {len(number_report.exact)} 处，"
            f"近似 {len(number_report.approx)} 处，"
            f"原文找不到 {len(number_report.missing)} 处"
        )
        if number_report.approx:
            print(f"    近似值（可能是四舍五入）：{'、'.join(number_report.approx[:8])}")
        if number_report.missing:
            suspicious_total += len(number_report.missing)
            print(
                f"    [需人工确认] 这些数值在原文里找不到："
                f"{'、'.join(number_report.missing[:10])}"
            )
            print("    这类数字最容易被判为不准确，请对照原文逐个人工核一遍。")
        if stat.annotated_terms:
            print(f"  通俗解释标记：{stat.annotated_terms} 处（“也就是说”“可以理解为”等）")
        print()

        if not use_llm:
            continue

        print("  [评审中] 正在按评分维度打分，约 40 秒…")
        try:
            text = review.llm_review(
                client,
                prompts.REVIEW_PROMPT,
                document,
                review.source_window(source_text),
            )
        except generate.LLMError as exc:
            print(f"  [失败] 评审未完成：{exc}")
            print()
            continue

        scores = re.findall(r"##\s*([^：\n]+?)：\s*(\d+(?:\.\d+)?)\s*/\s*(\d+)", text)
        if scores:
            total = sum(float(s[1]) for s in scores)
            ceiling = sum(float(s[2]) for s in scores)
            print()
            for name, got, full in scores:
                print(f"    {name.strip():<10} {got}/{full}")
            if ceiling:
                print(f"    {'小计':<10} {total:.0f}/{ceiling:.0f}")
            print()
        print(text)
        print()

    print("=" * 62)
    if suspicious_total:
        print(f"  结论：有 {suspicious_total} 处数值需要人工对照原文确认，先处理这些再谈表达。")
    else:
        print("  结论：数值全部能在原文找到依据，科学准确性这一项没有明显风险。")
    print("  说明：以上是模拟评审，不能替代官方评分；真正的评分细则由竞赛平台公布。")
    print("=" * 62)
    return 0


def cmd_validate(config: dict) -> int:
    docs_dir = PROJECT_ROOT / config.get("output_dir", "docs")
    materials_dir = PROJECT_ROOT / config.get("materials_dir", "materials")
    report = validate.run(PROJECT_ROOT, docs_dir, materials_dir, SOURCE_DIR)
    print(report.render())
    return 1 if report.failed else 0


def cmd_package(config: dict) -> int:
    docs_dir = PROJECT_ROOT / config.get("output_dir", "docs")
    package_name = config.get("package_name", "作品.zip")
    try:
        output, count = package.build(PROJECT_ROOT, docs_dir, SOURCE_DIR, package_name)
    except FileNotFoundError as exc:
        print(f"[错误] {exc}")
        return 1

    problems = package.verify(output)
    size_mb = output.stat().st_size / 1024 / 1024
    print(f"[输出] {output}  （{count} 个文件，{size_mb:.2f} MB）")
    if problems:
        for problem in problems:
            print(f"[错误] {problem}")
        return 1
    print("[校验] 压缩包结构完整，可直接提交")
    return 0


def _probe_llm(llm_cfg: dict, key: str) -> tuple[str, str]:
    """发一个最小请求，验证地址、模型名与密钥是否三者匹配。

    只判断「能不能连上」是不够的：404 说明地址或模型名写错，401 说明密钥不对。
    这两类错误在开赛当天最容易互相伪装，所以分开报。
    """
    try:
        import requests
    except ImportError:
        return "warn", "缺少 requests，已跳过连通性测试"

    url = f"{str(llm_cfg.get('base_url', '')).rstrip('/')}/chat/completions"
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": llm_cfg.get("model"),
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 4,
            },
            timeout=30,
        )
    except Exception as exc:  # 网络不可达、DNS 失败、超时
        return "warn", f"连不上 {url}（{type(exc).__name__}），考场网络可能需要代理"

    if resp.status_code == 200:
        return "ok", "接口连通，地址 / 模型名 / 密钥三者匹配（已真实调用一次）"
    if resp.status_code in (401, 403):
        # 最常见的真实原因是「地址与密钥不配套」：中转站的密钥配了官方地址，
        # 或者官方密钥配了中转站地址，两边都报 401，很容易被误判成密钥过期。
        return "fail", (
            f"密钥被拒绝（HTTP {resp.status_code}）：{resp.text[:120]}\n"
            f"           当前地址 {llm_cfg.get('base_url')}，模型 {llm_cfg.get('model')}。\n"
            "           若密钥来自第三方中转站，请把 config.json 的 llm.base_url / model\n"
            "           改成该平台文档里给出的那一组；两者必须来自同一家。"
        )
    if resp.status_code == 404:
        return "fail", f"地址或模型名不存在（HTTP 404），请核对 base_url / model：{resp.text[:120]}"
    if resp.status_code == 400:
        # 第三方平台常把「模型名不存在」报成 400 而不是 404
        return "fail", (
            f"请求被拒绝（HTTP 400）：{resp.text[:120]}\n"
            f"           常见原因：模型名 {llm_cfg.get('model')} 不在该平台的可选列表里，\n"
            "           或该平台不支持 max_tokens / temperature 参数。"
        )
    if resp.status_code == 429:
        return "warn", "触发限流或额度用尽（HTTP 429），生成时可能变慢"
    return "warn", f"HTTP {resp.status_code}：{resp.text[:120]}"


def cmd_doctor(config: dict, dotenv_keys: list[str]) -> int:
    """开赛前的环境体检：确认「除密钥之外」的每一项都已就绪。

    刻意把「有没有密钥」和「密钥对不对」分开报：前者只影响文笔（可退回离线引擎），
    后者会让生成直接失败，严重程度完全不同。
    """
    counts = {"error": 0, "warn": 0}

    def ok(msg: str) -> None:
        print(f"  [通过] {msg}")

    def warn(msg: str) -> None:
        counts["warn"] += 1
        print(f"  [注意] {msg}")

    def fail(msg: str) -> None:
        counts["error"] += 1
        print(f"  [错误] {msg}")

    print("=" * 62)
    print("环境体检（除密钥外的项目应全部通过）")
    print("=" * 62)

    info = sys.version_info
    if info >= (3, 9):
        ok(f"Python {info.major}.{info.minor}.{info.micro}  {sys.executable}")
    else:
        fail(f"Python {info.major}.{info.minor} 版本过低，需要 3.9 以上")

    try:
        import requests

        ok(f"requests {requests.__version__}（调用大模型必需）")
    except ImportError:
        fail("缺少 requests，无法调用大模型：pip install requests")

    try:
        import pypdf

        ok(f"pypdf {getattr(pypdf, '__version__', '已安装')}（读取 PDF 材料必需）")
    except ImportError:
        warn("缺少 pypdf，PDF 材料将无法解析：pip install pypdf")

    dotenv = SOURCE_DIR / ".env"
    if not dotenv.is_file():
        warn("没有 source_code/.env；要用大模型请新建该文件并填 LLM_API_KEY")
    elif dotenv_keys:
        ok(f".env 已读取：{'、'.join(dotenv_keys)}")
    else:
        warn(".env 存在但没读到有效键值（值留空会被忽略，写成 KEY=value 才生效）")

    llm_cfg, preset_name, preset_note = settings.resolve_llm(config)
    if preset_note:
        fail(preset_note)
    if llm_cfg.get("base_url") and llm_cfg.get("model"):
        label = preset_name or "自定义"
        ok(f"服务商：{label}  {llm_cfg['base_url']}  model={llm_cfg['model']}")
    else:
        warn("未配置服务商，只能走离线抽取式引擎（结构合规但文笔平实）")

    key_env = llm_cfg.get("api_key_env") or "LLM_API_KEY"
    key = os.environ.get(key_env, "").strip()
    if key:
        ok(f"{key_env} {settings.describe_key(key)}")
        level, message = _probe_llm(llm_cfg, key)
        {"ok": ok, "warn": warn, "fail": fail}[level](message)
    else:
        warn(f"{key_env} 未设置 —— 这是目前唯一待补的配置项")

    materials_dir = PROJECT_ROOT / config.get("materials_dir", "materials")
    docs_dir = PROJECT_ROOT / config.get("output_dir", "docs")
    usable, skipped = collect_materials(materials_dir)
    if usable:
        ok(f"材料：{len(usable)} 份可用")
    else:
        warn(f"{materials_dir.name}/ 里还没有材料，开赛后把赛题材料放进去")
    if skipped:
        names = "、".join(p.name for p in skipped[:5])
        warn(f"{len(skipped)} 个文件格式不支持，会被忽略：{names}")

    docs = sorted(p for p in docs_dir.glob("*.md") if not p.name.startswith("_"))
    if docs:
        ok(f"已生成文档：{len(docs)} 份")
    else:
        ok("尚未生成文档（材料放好后运行 generate）")

    if SOURCE_DIR.name == "source_code":
        ok("源码目录名为 source_code（赛项要求固定命名）")
    else:
        fail(f"源码目录名是 {SOURCE_DIR.name}，赛项要求为 source_code")

    if ".env" in package.EXCLUDE_FILES:
        ok("打包时自动排除 .env，密钥不会进入提交包")
    else:
        fail("打包未排除 .env，密钥有被提交的风险")

    print("-" * 62)
    if counts["error"]:
        print(f"  错误 {counts['error']} 项 / 注意 {counts['warn']} 项 —— 请先修错误项")
    elif counts["warn"]:
        print(f"  错误 0 项 / 注意 {counts['warn']} 项 —— 可以跑，注意项按需处理")
    else:
        print("  全部通过，可以直接开跑")
    print("=" * 62 + "\n")
    return 1 if counts["error"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BRICS2026-ST-003 预选赛作品生成工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "command",
        choices=["doctor", "generate", "review", "validate", "package", "all"],
        help=(
            "doctor=环境体检  generate=生成文档  review=质量评审  "
            "validate=自检  package=打包  all=生成+自检+打包"
        ),
    )
    args = parser.parse_args()
    config = load_config()

    # .env 必须在建 LLMClient 之前读入，否则密钥永远取不到
    dotenv_keys = load_environment()
    if dotenv_keys and args.command != "doctor":
        print(f"[配置] 已从 .env 读取 {len(dotenv_keys)} 项：{'、'.join(dotenv_keys)}\n")

    if args.command == "doctor":
        return cmd_doctor(config, dotenv_keys)
    if args.command == "generate":
        return cmd_generate(config)
    if args.command == "review":
        return cmd_review(config)
    if args.command == "validate":
        return cmd_validate(config)
    if args.command == "package":
        return cmd_package(config)

    for step in (cmd_generate, cmd_validate, cmd_package):
        code = step(config)
        if code != 0:
            print(f"\n[中断] {step.__name__} 未通过，后续步骤已停止。")
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
