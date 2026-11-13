你是本项目的【并行工作会话 S2】，负责确定性校验器（卡 A9）。
当前阶段：Wave 1。其他会话可能同时在改别的文件。

【工作目录】D:/桌面/金砖大赛/preselect/7_视觉源码
【Git】仓库根是 D:/桌面/金砖大赛/preselect，提交时路径前缀为 7_视觉源码/

【当前仓库真实状态 · 已核验，不要重新假设】
- W0 基础设施已完成，且不要重做：
  pipeline/errors.py（30 条 Code 枚举）、contracts.py、settings.py、
  schema_loader.py、orchestrator.py（注册表 + pkgutil 自动发现）、
  main.py（run / doctor / validate / package 四个子命令）
- Codex 接入层已完成：codex_adapter.py 用 openai-codex SDK，
  generate() 本机实测返回 status=completed
- pdf_input.py 已实现，渲染是必做步骤（pypdfium2），不是分支
- 仍未实现：pipeline/stages/ 三个阶段、validate.py、writer.py、repair.py、packaging.py
- source_code/prompts/ 不存在（B 未交付）。这是事实，不要创建，也不要内联提示词

【并行纪律 · 违反即回滚】
1. 只写白名单内的文件。白名单外一律不动，包括"就改一行"。
2. contracts.py / errors.py / settings.py / orchestrator.py / main.py /
   codex_adapter.py / config.json / requirements.txt / schemas/ 全部只读。
3. 不得新增第三方依赖；不得修改他人测试的断言。
4. 不得 mock 被测对象本身；被测的是校验规则，规则不能被替身顶掉。
5. 完成后写 .parallel/handoffs/A9.md，五节齐全：
   改动清单 / 冻结签名 / 验收原始输出 / 未覆盖风险 / 下游需知。
6. 提交：git add 仅本卡文件，message 前缀 [A9]，正文含验收命令与退出码。不推送。

【本卡目标】
实现 pipeline/validate.py 的 validate_run()，把"是否合格"从模型手里拿走，
全部由可复算的 Python 检查裁决，并落盘 run_dir/validation.json。

【本轮范围 · 已确认的契约缺口】
八类检查里有三项依赖 evidence 的字段结构，而 evidence.schema.json 当前只有
page_number / region / quote，缺事实列表容器、引用编号、core 标记、数值与单位。
这些字段的提案权在 B（见 docs/接口冻结清单_A-B.md 的 I3），A 不得自行补齐。
因此本轮：

- 实现：第 1、2、3、4、7 项（JSON 结构、Markdown 章节、字数、编码、敏感信息）
- 跳过：第 5、6、8 项（引用、数值、串稿），等 B 交付字段

**跳过不等于通过。** 必须在 validation.json 的 checks.skipped 中逐条列出未实现的
code，禁止让 valid=true 被读成"八项全过"。

【已冻结契约，不得改】
- 签名：validate_run(run_dir: Path, settings: Settings) -> ValidationReport
- Issue：dataclass(code: Code, stage: str, path: str, severity: str,
  message: str, hint: str)，构造时自动校验 severity 只能是 error / warning
- ValidationReport：valid / schema_version / checked_at / stats / issues / checks。
  checks 为 dict[str, list[str]]，本次新增，已在 contracts.py 中就位，
  固定两个键：enabled（本轮真正跑的 code）与 skipped（因缺字段未实现的 code）
- schemas/validation.schema.json 已升级到 1.1.0，含两条硬约束：
  · allOf：存在 severity=error 的 issue 时 valid 必须为 false，否则必须为 true
  · checks 为必需字段，内部只允许 enabled / skipped 两个键
- 落盘要求：写完 validation.json 后必须用
  schema_loader.validate_instance("validation", payload) 自检，
  非空即视为本卡自身 bug，抛 PipelineError 而不是静默返回

【八类检查 · 本轮做 1/2/3/4/7，跳过 5/6/8】
1. JSON 结构（做）：recognition / document_ir / evidence 三次校验。
   一律走 pipeline.schema_loader.validate_instance，不要自己调 jsonschema。
   三个 JSON 缺失时产出 SCHEMA_INVALID 或 ASSET_MISSING，不得抛裸异常中断。
2. Markdown 结构（做）：final.md 优先，不存在则 draft.md；
   必需章节：标题 / 引言 / 正文 / 总结 / 来源。缺一即 MD_MISSING_SECTION。
3. 正文长度（做）：汉字数，阈值取 settings.pipeline.body_min_han 与 body_max_han
   （当前为 2500 / 4000），不得写死数字。
4. 编码（做）：必须 UTF-8 可解码、无 BOM、不得 \r\n 与 \n 混用 → MD_BAD_ENCODING。
5. 来源块（**跳过**）：正文每个 [n] 必须能在 evidence 找到对应条目；
   evidence 中每条 core 事实至少被引用一次。
   → 缺 evidence 的事实列表容器、引用编号、core 标记，等 B 交付。
   → 列入 skipped：CITE_NOT_IN_EVIDENCE、EVIDENCE_UNREFERENCED
6. 数字一致性（**跳过**）：带单位的数值必须能在 evidence 找到同值同单位。
   → 缺 evidence 的数值与单位字段，等 B 交付。
   → 列入 skipped：NUMBER_MISMATCH、UNIT_MISSING
7. 敏感信息（做）：sk- 前缀、API key 形态、本机绝对路径（盘符、/Users/、/home/）
   → SECRET_LEAK / ABS_PATH_LEAK。
8. 串稿（**跳过**）：material_id 在 manifest 与正文元数据一致；正文不得含其他
   material_id。→ 缺"正文里 material_id 写在哪个位置、什么格式"的约定，等 B 交付。
   → 列入 skipped：MATERIAL_MISMATCH

skipped 必须恰好是这五条 code，顺序不限：
["CITE_NOT_IN_EVIDENCE", "EVIDENCE_UNREFERENCED", "NUMBER_MISMATCH",
 "UNIT_MISSING", "MATERIAL_MISMATCH"]
enabled 列出 1/2/3/4/7 中你实际产出的 code（含 MD_BAD_ENCODING、SECRET_LEAK 等）。

【关键约束】
- 纯函数：不读网络、不调模型、不读模块级可变状态。
- 汉字计数必须拆成独立纯函数并单测覆盖：先去掉 Markdown 标记与来源块，
  再数汉字；全角标点不计入。口径写进 docstring。
- 任一 severity=error → valid 必须 false。warning 必须进 issues，不得丢弃。
- stats 至少含：han_chars / citations / evidence_count / unreferenced_evidence。
  citations 与 unreferenced_evidence 本轮因跳过第 5 项而无从统计，一律填 0，
  并在 checks.skipped 中体现，不得填一个看起来合理的假数字。
- 当前没有任何真实 run 目录（stages 未实现），所以你必须自己构造最小测试输入，
  放在 tests/fixtures/engineering/ 下，本轮至少覆盖：
  run_ok（合法）/ run_missing_section（缺章节）/ run_too_short（字数不足）/
  run_bad_encoding（含 BOM 或 CRLF 混用）/ run_secret_leak（正文含 sk- 形态）

【白名单】
- pipeline/validate.py
- tests/test_engineering_validate.py
- tests/fixtures/engineering/**（仅校验器所需的最小构造，不含提示词）

【禁止】
- 不得改 schemas/validation.schema.json（已冻结）
- 不得改 main.py 的 validate 子命令
- 不得为了让测试变绿而放宽断言、跳过用例、mock 掉校验规则
- 不得创建 prompts/ 或任何属于 B 的资产
- 不得写隐藏的跳过开关。跳过必须体现在 checks.skipped 里，不允许只在代码注释里说明

【验收 · 逐条贴终端原文与退出码】
python -m pytest tests/test_engineering_validate.py -q

python -c "from pathlib import Path;from pipeline.validate import validate_run;from pipeline.settings import load_settings;s=load_settings(require_secret=False);r=validate_run(Path('tests/fixtures/engineering/run_ok'),s);print(r.valid,len(r.issues))"
   期望 True 0

python -c "from pathlib import Path;from pipeline.validate import validate_run;from pipeline.settings import load_settings;s=load_settings(require_secret=False);r=validate_run(Path('tests/fixtures/engineering/run_ok'),s);print(r.checks['skipped'])"
   期望恰好 ['CITE_NOT_IN_EVIDENCE','EVIDENCE_UNREFERENCED','NUMBER_MISMATCH','UNIT_MISSING','MATERIAL_MISMATCH']
   （顺序不限；这一条是防"跳过被伪装成通过"的唯一证据）

python -c "from pathlib import Path;from pipeline.validate import validate_run;from pipeline.settings import load_settings;s=load_settings(require_secret=False);r=validate_run(Path('tests/fixtures/engineering/run_missing_section'),s);print([i.code.value for i in r.issues])"
   期望包含 MD_MISSING_SECTION

python -c "from pathlib import Path;from pipeline.validate import validate_run;from pipeline.settings import load_settings;s=load_settings(require_secret=False);r=validate_run(Path('tests/fixtures/engineering/run_too_short'),s);print([i.code.value for i in r.issues])"
   期望包含 MD_BODY_TOO_SHORT

python -c "from pathlib import Path;from pipeline.validate import validate_run;from pipeline.settings import load_settings;s=load_settings(require_secret=False);r=validate_run(Path('tests/fixtures/engineering/run_bad_encoding'),s);print([i.code.value for i in r.issues])"
   期望包含 MD_BAD_ENCODING

python -c "from pathlib import Path;from pipeline.validate import validate_run;from pipeline.settings import load_settings;s=load_settings(require_secret=False);r=validate_run(Path('tests/fixtures/engineering/run_secret_leak'),s);print([i.code.value for i in r.issues])"
   期望包含 SECRET_LEAK

python -c "import json;from pipeline.schema_loader import validate_instance;print(validate_instance('validation',json.load(open('tests/fixtures/engineering/run_ok/validation.json',encoding='utf-8'))))"
   期望空列表，证明落盘产物自身合法（含 version 1.1.0 与 checks 段）

【回复格式】第一轮只输出 A（拟改文件 + 责任边界）、B（验收命令 + 预期特征），
停下等确认。确认后才实施。中途发现契约缺口，写进"未覆盖风险"，不要自行改契约。
