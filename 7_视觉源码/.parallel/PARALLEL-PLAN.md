# 多会话并行开发方案 v1

> 适用仓库：`D:/桌面/金砖大赛/preselect/7_视觉源码`（git 根为 `preselect`）
> 配套文档：`docs/A_开发提示词方案.md`（A0–A16）、`docs/接口冻结清单_A-B.md`（I1–I10）
> 本文件是流程规范，不改变任何接口契约。冲突时以接口冻结清单为准。

---

## 投喂规则（先读这一节）

**不要把整份文件贴给 Codex。** 它是地图，不是任务本身。整份贴进去会有三个后果：

1. Codex 看到 12 张卡，会挑一批一起做，违反"一次一张"。
2. 它会读到 `§8.1` 里 W0 的历史事实（"8 个模块均为 stub"），误判仓库状态并覆盖已有代码。
3. 它不知道"现在该做哪张"，因为没有指令告诉它。

**正确的投喂单位是两段拼接**：

```text
[§8.2 并行工作会话通用头]  +  [§8.3 / §8.4 / §8.5 / §8.6 中的一张卡]
```

**当前该做哪一张，只看 `STATE.md`**，不看本文件的章节顺序。

| 场景 | 贴什么 |
|---|---|
| 第一次开工作会话 | `§8.2` 通用头 + `STATE.md` 里标 `doing` 的那张卡 |
| 继续上一轮的活 | 先让 Codex 读 `.parallel/handoffs/<CARD>.md`，再贴该卡 |
| 上下文快满 | `§8.7` 交接卡 |
| 让它自查 | `§8.2` 通用头 + 一句"只做审查，不改代码" |

**不要贴的东西**：§0–§7 是给人看的规则与矩阵，Codex 不需要；贴了只会稀释指令。

---

## 0. 结论

1. **不能把 17 张卡直接分给多个会话。** 当前有 5 个文件被两张以上卡共用，直接并行会在第一小时内产生覆盖式冲突。
2. **唯一可行路径是「先串行冻结共享内核，再并行填实现」。** 1 个会话先把契约落成可 import 的代码（Wave 0），之后 3 路并行各自的文件，互不重叠。
3. **实测压缩比约 2.0×，不是 4×。** 17 张卡约压到 9 卡墙钟当量。瓶颈是人工审核带宽，不是机器并行度。
4. **A1 的凭据阻塞已解除**（本机全局 config.toml 已配自建网关，`generate()` 实测返回 completed），A1 可立即执行。它的产出 `provider_capabilities.json` 由工程夹具临时替代，A6 必须按 fail-closed 处理缺失。

---

## 1. 并行的三个前提事实（本轮已核验）

| # | 事实 | 对并行的直接影响 |
|---|---|---|
| 1 | git 根是 `preselect`，`7_视觉源码/` 下已跟踪 23 个文件，最近提交 `0f09963` | 可以打基线 tag，commit 可作交付锚点 |
| 2 | `.gitignore` 第 9 行 `**/docs/` 使 `7_视觉源码/docs/` 整体不被跟踪 | 现有三份核心文档**不在版本控制内**；交接文档不能放 `docs/` |
| 3 | 5 个文件多卡共用：`orchestrator.py`(A4,A8)、`main.py`(A4,A15)、`manifest.py`(A4,A12)、`config.json`(A2,A7,A9)、`validate.py`(A4 的调用方 vs A9 的入口签名) | 必须在 Wave 0 一次写全，否则并行必撞 |

**推论**：交接与看板目录定为 `7_视觉源码/.parallel/`。该路径已用 `git check-ignore` 验证不被忽略，无需改动父级 `.gitignore`。

---

## 2. 任务拆解与分配原则

### 2.1 四条原则

| 原则 | 说明 | 反面做法（禁止） |
|---|---|---|
| **按文件所有权切，不按卡序切** | 并行单位是「一个文件由谁独占」，不是「一张卡由谁做」 | 两个会话都做 `orchestrator.py` |
| **契约以代码冻结，不以文档冻结** | A8 要调 A7，靠的是 `contracts.py` 里的 dataclass 字段，不是聊天里的约定 | 「我们说过 ModelResult 有 truncated 字段」 |
| **一个文件同一时刻只有一个 owner** | 开工前在 `CLAIMS.md` 登记，未登记即无写权 | 顺手改别人的文件「就一行」 |
| **集成类卡永不可并行** | A10 / A13 / A16 验证的是真实链路行为，mock 化即违反工程约束第 8 条 | 用假 evidence 让 A13「先跑通」 |

### 2.2 Wave 计划

| Wave | 会话数 | 内容 | 墙钟当量 | 出口条件（全部满足才进下一 Wave） |
|---|---|---|---|---|
| **W0 地基** | 1（S0 串行） | 共享内核：`errors.py`、`settings.py`、`schema_loader.py`、`contracts.py`、`orchestrator.py` 骨架、`main.py` 四子命令、`manifest.py` 结构、`stages/__init__.py` 自动发现、9 个业务模块签名骨架、`config.json`/`requirements.txt`/`.env.example` 写全 | 3 卡 | 8 条验收全过；`run` 因阶段未注册而非 PDF 缺失失败；9 个签名已入 `CONTRACTS.md`；打 tag `w0-kernel` |
| **W1 实现** | 3（S1/S2/S3） | S1: A6+A7 ｜ S2: A9 ｜ S3: A8 | 2 卡 | 三路各自单测全绿；`pytest -q` 除 I10 夹具缺失导致的 skip 外无其他 skip；`CLAIMS.md` 无残留占用 |
| **W2 闭环** | 3（S1/S2/S3）+ S0 | S1: A11 ｜ S2: A14 前 4 例 ｜ S3: A15 ｜ S0: A10 端到端 | 2 卡 | A10 产出独立 run 目录且 I2 布局完整；A15 `testzip() is None` |
| **W3 交付** | 1（S0 串行） | A13 四材料 → A14 剩余 2 例 → A16 干净环境 | 2 卡 | 四份 final.md 各有独立 run_id；六例故障全绿；干净环境可复现 |

**W2 为什么能四路并行**：A11 只写 `writer.py`/`repair.py`，A14 只写 `tests/test_engineering_faults.py`，A15 只写 `packaging.py`，A10 只跑不改。前提是 W0 已把 `main.py` 的 4 个子命令和 `orchestrator` 注册表写全，A15 不再需要动 `main.py`。

---

## 3. 角色与职责

| 会话 | 角色 | 拥有文件（唯一写权） | 只读依赖 | 永不触碰 |
|---|---|---|---|---|
| **S0** | 编排者 / 契约守护 | `contracts.py`、`orchestrator.py`、`manifest.py`、`main.py`、`config.json`、`requirements.txt`、`.env.example`、`stages/__init__.py`、`.parallel/` | 全部 | `skills/`、`prompts/`、`fixtures/quality/`、`schemas/` 字段 |
| **S1** | 输入与模型层 | `pdf_input.py`、`codex_adapter.py`、`vision_provider.py`、`provider_smoke.py` | `contracts.py`、`errors.py`、`settings.py` | 上述 S0 文件、`validate.py` |
| **S2** | 校验层 | `validate.py`、`tests/test_engineering_validate.py`、`tests/test_engineering_faults.py` | `errors.py`、`schema_loader.py`、`settings.py` | 业务阶段、`contracts.py` |
| **S3** | 阶段、写作与交付层 | `stages/*.py`（除 `__init__.py`）、`prompt_loader.py`、`writer.py`、`repair.py`、`packaging.py` | `contracts.py`、`errors.py`、`schema_loader.py` | `orchestrator.py`、`main.py` |

**并行度建议：S0 + 3 个工作会话，上限 4 个窗口。** 超过 4 路不会更快——审核与合并由同一个人完成，窗口数超过审核带宽后，冲突排查成本会反超并行收益。

**S0 的核心职责不是写代码，是守契约。** 具体三件事：
1. Wave 0 一次性写全共用文件，之后不再让任何会话改它们。
2. 收到「需要加字段」的请求时，判断是否属于 I5/I6/I9 已冻结范围；属于则驳回，要求改实现而非改契约。
3. 每个 Wave 结束时跑一次全量集成，冲突由 S0 裁决，不由两个工作会话私了。

---

## 4. 文件所有权矩阵

| 文件 | W0 | W1 | W2 | W3 |
|---|---|---|---|---|
| `pipeline/errors.py` | S0 建 | 只读 | 只读 | 只读 |
| `pipeline/settings.py` | S0(A2) | 只读 | 只读 | 只读 |
| `pipeline/schema_loader.py` | S0(A3) | 只读 | 只读 | 只读 |
| `pipeline/contracts.py` | S0 建 | 只读 | 只读 | 只读 |
| `pipeline/orchestrator.py` | S0(A4) | 只读 | 只读 | 只读 |
| `pipeline/manifest.py` | S0 结构 | 只读 | 只读 | S0(A12) |
| `main.py` | S0(A4 全 4 子命令) | 只读 | 只读 | 只读 |
| `config.json` / `requirements.txt` / `.env.example` | S0 写全 | 只读 | 只读 | 只读 |
| `pipeline/pdf_input.py` | S0 留签名 | **S1**(A6) | — | — |
| `pipeline/codex_adapter.py` / `vision_provider.py` | S0 留签名 | **S1**(A7) | — | — |
| `pipeline/validate.py` | S0 留签名 | **S2**(A9) | — | — |
| `pipeline/prompt_loader.py` / `stages/*` | S0 留签名 | **S3**(A8) | — | — |
| `pipeline/writer.py` / `repair.py` | S0 留签名 | — | **S1**(A11) | — |
| `tests/test_engineering_faults.py` | — | — | **S2**(A14) | S2 补 2 例 |
| `pipeline/packaging.py` | S0 留签名 | — | **S3**(A15) | — |
| `schemas/*.json` | 除 validation 外只读 | 只读 | 只读 | 只读 |

`schemas/` 的字段提案权在 B。唯一例外是 `validation.schema.json`：I3 §3 已冻结其顶层键为 `valid`/`issues`，W0 只做落盘对齐，不发明字段。其余三个等 A5 双签后再动。

---

## 5. 协作与信息同步

### 5.1 唯一同步介质：磁盘文件（不走聊天记录）

```text
7_视觉源码/.parallel/
├── PARALLEL-PLAN.md      # 本文件，只读
├── CONTRACTS.md          # W0 冻结的签名与字段，S0 写，其余只读
├── CLAIMS.md             # 文件占用登记，各会话自行追加/释放
├── STATE.md              # 卡级看板：卡号 | owner | 状态 | run_id | 备注
└── handoffs/
    ├── A6.md  A7.md  A9.md  A8.md  ...
    └── W0-kernel.md
```

**为什么用磁盘而不是对话**：多个会话之间没有消息通道。新的会话读不到旧会话的上下文，但能读到文件。所以「事实的唯一载体」必须是文件。

### 5.2 CLAIMS.md 登记格式

```markdown
| 卡号 | 会话 | 文件 | 开始时间 | 状态 |
|---|---|---|---|---|
| A6 | S1 | pipeline/pdf_input.py | 19:40 | 占用中 |
```

规则：
- 开工前先追加一行，`状态=占用中`；交付后改为 `已释放`，不删行。
- 发现目标文件已被他人标记 `占用中` → **停止，报告 S0**，不得抢写。
- 一次最多占用 3 个文件；占而不用超过 40 分钟视为放弃，S0 可回收。

### 5.3 STATE.md 卡级看板

```markdown
| 卡 | Wave | owner | 状态 | 验收命令退出码 | 备注 |
|---|---|---|---|---|---|
| A6 | W1 | S1 | done | 0 | 缺 provider_capabilities 走 fail-closed |
```

`状态` 只允许 `todo / doing / done / blocked`。`blocked` 必须在备注写清阻塞原因与解除条件。

### 5.4 commit 规范

- 一个卡一个 commit，message 前缀 `[A6]`，正文写验收命令与退出码。
- 禁止跨卡混提；禁止把 `.parallel/` 的看板变更混进代码 commit（单独 commit，前缀 `[board]`）。
- commit 后**不自动推送**（沿用现行规则：等本地构建验收通过再推）。

---

## 6. 上下文衔接与交接规范

### 6.1 开场词必须自包含

新会话看不到任何旧上下文。开场词必须自带五要素，缺一即无法开工：

1. 绝对路径与当前 Wave
2. 你的角色与文件白名单
3. 契约文件位置（`CONTRACTS.md` + 对应 `.py`）
4. 本卡的验收命令
5. 「第一轮只输出 A、B 两项」的停止条件

### 6.2 交接文件五段式

`handoffs/<CARD>.md` 必须恰好包含五节，不增不减：

```markdown
## 1 改动清单
<文件路径 → 一句话责任，逐行>

## 2 冻结签名
<新增或变更的函数签名，逐字可复制>

## 3 验收原始输出
<命令 + 终端原文 + 退出码>

## 4 未覆盖风险
<按是否阻塞下游排序；阻塞项必须写解除条件>

## 5 下游需知
<谁在什么条件下会依赖本卡产物；缺什么会失败、失败成什么样>
```

**第 5 节是并行方案的关键。** 串行开发时下游会自己发现，并行时下游看不到你，只能靠这一节。

### 6.3 上下文将满时的降级流程

当会话接近上下文上限，按序执行，不得跳步：

1. 写完 `handoffs/<CARD>.md`（五段式），未完成的分节留 `TODO: 未完成`，不得省略。
2. 更新 `STATE.md` 中本卡状态为 `doing` 或 `blocked`。
3. 贴 §8.7 的交接卡，产出不超过 60 行的摘要，追加到 handoff 末尾。
4. 新会话开场词只引用 handoff 路径，不复述内容。

---

## 7. 防冲突硬规则（10 条）

1. **文件级锁**：未在 `CLAIMS.md` 登记的文件不得写入。一票否决。
2. **禁改契约**：`contracts.py`、`errors.py` 的枚举、`settings.py` 的字段集，除 S0 外任何人不得修改。需要加字段 → 写进 `CLAIMS.md` 的「契约变更申请」区，等 S0 裁决。
3. **禁跨层 import**：`stages/*.py` 只能 import `contracts`/`errors`/`schema_loader`/`prompt_loader`，不得 import `pdf_input` 或 `codex_adapter` 的内部函数（只能经 `contracts` 定义的接口）。
4. **禁共享可变全局**：不得新增模块级可变状态（单例、缓存 dict、计数器）。断点重跑与四材料隔离都依赖这一点。
5. **禁改他人测试**：只可新增自己的测试文件，不得修改他人测试文件里的断言。
6. **禁 mock 被测对象**：可以 mock **边界外的**依赖（A8 mock adapter，因为被测对象是阶段编排）；不可以 mock **被测对象本身**（A9 不得 mock 校验规则）。
7. **禁改 `config.json` 键**：所有键在 W0 写全，后续只读取。缺键 → 报告 S0，不自行添加。
8. **禁新增依赖**：`requirements.txt` 在 W0 冻结。确需新增 → 报告 S0，说明为何现有依赖不可替代。
9. **禁并行改同一运行目录**：`result/runs/<material_id>/<run_id>/` 每个 run 独立，禁止两个会话写同一 run_id。
10. **冲突由 S0 裁决**：两个会话对同一行为有不同理解时，以 `CONTRACTS.md` 为准；文档未覆盖的，S0 当场写入 `CONTRACTS.md` 再继续，**不得口头定案**。

---

## 8. 提示词库

### 8.1 S0 · Wave 0 地基卡（已于 2026-11-13 执行，仅作基线记录）

> **状态：已执行完毕，不要重复跑。** 19:39–20:20 落地，21:30 完成模型链路纠偏
> （HTTP → openai-codex SDK）。当前真实状态见 `STATE.md`。
> 保留本卡是为了让后续会话知道"哪些文件已是 W0 产物、不得再改签名"。

```text
你是本项目的【编排会话 S0】，同时承担系统工程执行者角色。你只对 source_code 下的
工程链路负责，不判断文章科学质量，不撰写业务提示词与 Skill 文本。

【仓库事实 · 执行时快照，现已失效，不要据此判断当前状态】
- 工作目录：D:/桌面/金砖大赛/preselect/7_视觉源码
- git 根为 D:/桌面/金砖大赛/preselect，已跟踪 23 个文件，最近提交 0f09963
- docs/ 被父级 .gitignore 的 **/docs/ 忽略，不要把交付文档放那里
- （执行时）pipeline/ 下 8 个模块均为 stub；main.py 28 行、仅单位置参数
- （执行时）全仓库没有任何 os.getenv；config.json 只有 7 个键
- （执行时）未安装本仓库依赖
**当前实际情况以 STATE.md 与源码为准。本段仅用于解释这张卡当初为什么这么写。**

【本卡目标】
一次性写全"共享内核"，让后续三路并行会话无需再改这些文件。
本卡不实现任何业务阶段，所有阶段函数体为 raise NotImplementedError。

【必须产出】
1. pipeline/errors.py
   - Code 枚举：逐条照抄 docs/接口冻结清单_A-B.md §6.2 全部 code，
     合计 30 条，按 9 类分组，各类条目数必须分别为：
     结构 3 / 资产 3 / 输入 4 / 模型 4 / 文档 3 / Markdown 4 / 一致性 5 / 安全 2 / 修复 2
   - Issue：frozen dataclass，字段 code/stage/path/severity/message/hint，
     提供 to_dict() 输出 I6 §6.1 的 JSON 形状
   - 异常层级：PipelineError(code, message, hint=None) 为基类；
     ConfigError/SchemaNotFound/SchemaVersionMismatch/AssetMissing/AssetEmpty/
     ModelTimeout/TruncatedResponse/InvalidModelOutput/ModelRefused/
     PdfEncrypted/PdfEmpty/PdfTooLarge/PdfRenderFailed/IoFailure 为子类
   - exit_code_for(exc) -> int：严格按 I7 表映射（2/3/4/5/8），
     未知异常返回 1。禁止用 if-else 兜底成 0
2. pipeline/contracts.py
   - ModelRequest：stage, prompt_name, prompt_text, payload, json_schema=None,
     images=None, skill=None, timeout_s=None, model=None
     images 是本地 PNG 路径列表（SDK 的 LocalImageInput），不是 PDF；
     skill 是 (name, path) 二元组，对应 SDK 的 SkillInput
   - ModelResult：text, parsed, raw, usage, truncated, latency_ms, attempts,
     request_hash, model, turn_status, error_message
     turn_status 取 completed/interrupted/failed/in_progress，对应 SDK 的 TurnStatus。
     没有 finish_reason——它属于 HTTP API，Codex 不提供，不要保留该字段
   - PromptAsset：text, sha256, path, version
   - SkillAsset：text, sha256, path
   - RunContext：run_id, material_id, run_dir, settings, artifacts(dict)
   - StageResult：name, status, duration_ms, artifacts, issues
   - PdfInfo：sha256, page_count, file_size, encrypted, pages(list[PageInfo])
   - PageInfo：page_number, width_pt, height_pt, rotation, has_text_layer, render_path
   - ValidationReport：valid, schema_version, checked_at, stats, issues
   - PackageReport：entries, excluded, verified, built_at
   全部 frozen dataclass。这些字段是并行期唯一可依赖的跨层契约，
   后续会话无权增删，只能向你申请
3. 业务模块签名骨架（只写签名与 raise NotImplementedError，不写实现）
   - pdf_input.load_pdf(path: Path) -> PdfInfo
   - codex_adapter.CodexAdapter.generate(request: ModelRequest) -> ModelResult
   - codex_adapter.CodexAdapter.close() -> None（app-server 生命周期，run 结束必调）
   - validate.validate_run(run_dir: Path, settings: Settings) -> ValidationReport
   - prompt_loader.load_prompt(name: str, base_dir: Path) -> PromptAsset
   - prompt_loader.load_skill(path: Path) -> SkillAsset
   - writer.write_draft(evidence_path: Path, ctx: RunContext) -> StageResult
   - repair.repair_draft(draft_path: Path, validation_path: Path, ctx: RunContext) -> StageResult
   - packaging.build_package(materials: list[str], out_path: Path,
                             settings: Settings) -> PackageReport
   理由：W1 三路并行时，S3 的阶段函数要调 A6/A7 的返回类型，S1 的写作要调 A9 的
   校验结果。签名不先冻结，三路就会各自发明返回结构，集成时必然返工。
   这 9 个签名必须逐字写入 CONTRACTS.md，W1 各卡只能填实现，不得改签名。
4. pipeline/settings.py
   - Settings 为 frozen dataclass，字段覆盖：
     vision.{provider,model,base_url,wire_api,timeout_s,max_retries,dpi,effort}
     pipeline.{max_repair_attempts,keep_intermediate,body_min_han,body_max_han}
     paths.{result_dir,prompts_dir,skills_dir,fixtures_dir}
     没有 temperature：Codex 不暴露该参数，写了即为伪造配置
     wire_api 取 responses|chat，决定 model_providers.<id>.wire_api
     provider 取 openai_codex，对应 vision_provider.get_provider 的分发键
   - load_settings(cli_overrides=None, config_path=None, require_secret=False)
     优先级：CLI > 环境变量 > .env > config.json > 默认值
   - describe() -> {"vision.model": {"value": ..., "source": "cli|env|dotenv|config|default"}}
     结构与 I8 的 manifest.config 完全一致
   - unknown_keys() -> list[str]
   - 密钥封装为不可打印类型，__repr__/__str__/异常信息一律 ***
   - 密钥变量名是 LLM_API_KEY（不是 OPENAI_API_KEY）；
     require_secret=True 且缺该变量 → ConfigError；require_secret=False 时允许缺
   - 不新增第三方依赖（不得引入 pydantic）
5. pipeline/schema_loader.py
   - load_schema(name) -> dict；缺失抛 SchemaNotFound，不得回退空 schema
   - validate_instance(name, obj) -> list[Issue]；instance 为 None 也走校验，
     不得短路返回空列表
   - schema_sha256(name) -> str，供 manifest 使用
6. pipeline/orchestrator.py
   - Stage 枚举：recognize/normalize/evidence/write/validate/package
   - STAGE_REGISTRY: dict[str, StageSpec]，register_stage(name, fn, requires, produces)
   - run_stages(ctx, target) -> list[StageResult]：按注册表顺序执行，
     pending→running→ok|failed|skipped，任一 failed 立即停止并向上抛
   - 文件内不得出现任何具体阶段名的业务判断，只认注册表
7. pipeline/manifest.py
   - 按 I8 字段建完整 dict 结构并落盘，字段齐全但值可为 None；
     实现留给 A12，本卡只保证结构与字段名正确
8. main.py
   - argparse subparsers，四个子命令一次写全：
     run <pdf> [--config] [--output] [--material-id] [--stage]
     doctor [--offline]
     validate --run-id <id>
     package --materials ... --out <path>
   - 只做参数解析、调用 orchestrator、打印摘要、返回 exit_code_for(exc)
   - 单文件不超过 120 行；不得出现业务判断
9. pipeline/stages/__init__.py
   - 用 pkgutil 自动发现同包下 STAGE_NAME 属性，禁止显式注册列表
     （显式列表会让两个并行会话同时改本文件）
10. 配置文件一次写全
   - config.json：vision.provider="openai_codex"、vision.model、vision.base_url、
     vision.wire_api="responses"、vision.timeout_s、vision.max_retries、vision.dpi、
     vision.effort；pipeline.body_min_han=2500 / body_max_han=4000；paths 四项。
     **删掉 vision.temperature**：Codex 不暴露该参数，留着就是伪造配置项
   - requirements.txt：保留 jsonschema / pypdf / python-dotenv 三条，新增
     openai-codex、pytest、pypdfium2，每条加行内注释说明用途；requirements-lock.txt 同步
     **装的是 openai-codex，不是 openai**。前者控制本地 Codex runtime，
     后者是直连 HTTP 的 SDK，本项目不使用后者
   - .env.example：LLM_BASE_URL、LLM_API_KEY、LLM_MODEL 三项值留空；
     另加 VISION_WIRE_API=responses、VISION_TIMEOUT_S、VISION_MAX_RETRIES。
     密钥变量名是 LLM_API_KEY，不是 OPENAI_API_KEY
11. .parallel/CONTRACTS.md
   - 把上述所有签名逐字写入，附每个文件的 sha256，作为并行期唯一契约
12. schemas/validation.schema.json（仅此一个 schema，其余四个不动）
   - 按 docs/接口冻结清单_A-B.md 的 I3 §3 表格落盘：顶层键改为
     valid / schema_version / checked_at / stats / issues，additionalProperties=false
   - 理由：I3 已冻结"validation 必须存在的顶层键是 valid、issues"，
     而现有文件是 errors: [string]。不先改，S2(A9) 一交付就与 schema 互相矛盾
   - 这不属于"补充业务字段"：字段清单来自 I3 已冻结内容，你只做落盘，
     不得自行发明 issues 内部字段（严格照 I6 §6.1 的 code/stage/path/severity/message/hint）
   - 其余三个 schema 保持原样，等 A5 双签后再动

【禁止】
- 不得实现 pdf_input / codex_adapter / validate / writer / repair 的任何业务逻辑
- 不得改动 schemas/ 下任何文件（字段提案权在 B）
- 不得创建 prompts/、fixtures/quality/ 或任何占位资产
- 不得写"临时兼容层""先这样后面再改"的过渡代码

【验收 · 八条全过才算完成，贴终端原文与退出码】
python -m pip install -r requirements.txt
python -m compileall -q pipeline main.py
python -c "import pipeline.errors, pipeline.settings, pipeline.schema_loader, pipeline.contracts, pipeline.orchestrator, pipeline.manifest; print('import-ok')"
python -c "from pipeline.errors import Code; print(len(list(Code)))"          # 必须等于 30
python -c "from pipeline.errors import Code, PipelineError, exit_code_for; print(exit_code_for(PipelineError(Code.PDF_ENCRYPTED)))"   # 必须 3
python -c "from pipeline.schema_loader import validate_instance; print([i.code for i in validate_instance('validation', {'valid': True, 'schema_version': '1.0.0', 'checked_at': '2026-11-13T00:00:00Z', 'stats': {}, 'issues': []})])"   # 必须为空列表，证明 schema 已升级
python main.py run tests/does-not-exist.pdf --stage recognize ; echo "exit=$?"
python main.py --help                                                          # 四个子命令齐全

【第六条的判定要求】
该命令的失败原因必须是「阶段未注册 / 未实现」，不能是「PDF 不存在」。
即 run 必须先查 STAGE_REGISTRY，target 阶段缺失即抛错并返回非 0；
若先做 PDF 存在性检查再报错，则 I7 的「未实现阶段必须 failed」并未被验证。

【第一轮只输出 A、B 两项，然后停下等我确认。A 项必须列出你打算写的每个文件
及其责任边界，B 项必须给出上述八条命令。】
```

### 8.2 并行工作会话通用头（每个工作会话贴一次）

```text
你是本项目的【并行工作会话 <S1|S2|S3>】。当前处于 Wave <1|2>，与其他会话同时
在同一个工作目录下编码。

【工作目录】D:/桌面/金砖大赛/preselect/7_视觉源码

【并行纪律 · 违反即回滚】
1. 开工前读 .parallel/CONTRACTS.md 与 .parallel/CLAIMS.md。
2. 只写本卡白名单内的文件。白名单外一律不动，包括"就改一行"。
3. 开工前在 .parallel/CLAIMS.md 追加占用行；交付后改为"已释放"。
4. 若白名单文件已被他人标记"占用中"，立即停止并报告，不得抢写。
5. contracts.py / errors.py / settings.py / orchestrator.py / main.py /
   config.json / requirements.txt 全部只读。需要改 → 写契约变更申请，等 S0 裁决。
6. 不得新增模块级可变状态；不得新增依赖；不得修改他人测试的断言。
7. 不得 mock 被测对象本身；mock 只能用在被测边界之外。
8. 本卡完成后写 .parallel/handoffs/<CARD>.md，五节齐全：
   改动清单 / 冻结签名 / 验收原始输出 / 未覆盖风险 / 下游需知。
9. 提交：git add 仅本卡文件，commit message 以 [<CARD>] 开头，
   正文含验收命令与退出码。不推送。

【上下文将满时】
先写完 handoff（未完成分节写 TODO，不得省略），再更新 STATE.md，再贴交接卡。
禁止在未写 handoff 的情况下结束会话。

【回复格式】第一轮只输出 A（拟改文件 + 责任边界）、B（验收命令 + 预期特征），
停下等确认。确认后才实施。
```

### 8.3 W1 · S1 输入与模型卡（A6 + A7）

```text
【W1-S1 输入与模型层】
白名单：pipeline/pdf_input.py、pipeline/codex_adapter.py、
        pipeline/vision_provider.py、tests/test_engineering_input.py

A6 · PDF 输入与页面化（照 docs/A_开发提示词方案.md 的 A6 卡执行），补充约束：
- 渲染依赖已由 W0 冻结为 pypdfium2，不得换成 PyMuPDF / pdf2image
- **渲染不是可选项。** Codex SDK 的输入只有 LocalImageInput / ImageInput，
  没有 PDF 文件输入，所以每一页都必须渲染成 PNG。不要再写"按
  provider_capabilities 决定是否渲染"的分支
- provider_capabilities.json 缺失时按配置错误失败，不要默认继续
- 独立测试用 tests/fixtures/engineering/provider_capabilities_fake.json
  （A 的工程夹具，不是 B 的 fixtures/quality/）
- 验收需真实构造：正常单页、加密 PDF、0 页 PDF 三种，各贴状态返回

A7 · Codex 接入层（照 A7 卡执行），补充约束：
- 只用 openai_codex 包。W0 已把 openai-codex 写进 requirements，
  **不得**改用 openai SDK 或自写 HTTP 客户端——这是架构分层，不是实现偏好
- ModelRequest/ModelResult 直接复用 pipeline/contracts.py，禁止另建同名字段
- thread_start 必须带 sandbox=Sandbox.read_only 与 ephemeral=True；
  Codex 是 agentic 的，给写权会改工作区
- 超时靠 TurnHandle.interrupt()，SDK 没有 per-turn timeout 参数；
  turn_status=interrupted → truncated=True → 抛 TruncatedResponse
- 重试用 is_retryable_error / retry_on_overload，不自己写退避循环
- 一个 run 只启一个 Codex 实例，close() 由 orchestrator 在 finally 调用
- 验收需构造 timeout_s=0.001 的一次调用，贴 interrupt 是否生效、
  异常类型、exit_code_for 结果
- 未登录（CODEX_HOME 指向空目录）时只允许归类为 AUTH_MISSING，不得伪造 ok

禁止：改 main.py 加子命令；改 config.json 加键；在 adapter 内出现
pages/blocks/evidence 等业务字段名；读 prompts/ 目录。
```

### 8.4 W1 · S2 校验器卡（A9）

```text
【W1-S2 确定性校验器】
白名单：pipeline/validate.py、tests/test_engineering_validate.py

照 docs/A_开发提示词方案.md 的 A9 卡执行，补充约束：
- 八类检查逐条实现，每类至少一个单测；测试文件只新增，不改他人测试
- 所有 issue 必须用 pipeline/errors.py 的 Code 枚举，禁止字符串字面量
- 纯函数：不得读网络、不得调模型、不得读 settings 之外的全局状态
- 阈值从 settings 读（pipeline.body_min_han / body_max_han），不得写死 2500/4000
- 函数签名已由 W0 冻结在 validate.py 与 CONTRACTS.md，本卡只填实现，不得改签名
- 汉字计数口径必须写成可复算的纯函数并单测覆盖：去除 Markdown 标记与来源块后
  的汉字数；全角标点不计入
- 验收需手动制造三种坏输入（缺章节、字数不足、伪造引用），各贴一次 issue code
- 不得为了测试通过而放宽任何断言

禁止：改 schemas/validation.schema.json 的 errors→issues 结构
（该变更需 B 确认，属 A3/A5 范围，不在本卡）。
```

### 8.5 W1 · S3 阶段层卡（A8）

```text
【W1-S3 阶段编排】
白名单：pipeline/prompt_loader.py、pipeline/stages/recognize.py、
        pipeline/stages/normalize.py、pipeline/stages/evidence.py、
        tests/test_engineering_stages.py

照 docs/A_开发提示词方案.md 的 A8 卡执行，补充约束：
- 三个阶段各自一个文件，通过 STAGE_NAME + run(ctx, inputs) 暴露；
  stages/__init__.py 是自动发现，你不得修改它
- 不得修改 orchestrator.py。注册靠自动发现，若发现必须改它，先报告 S0
- 阶段之间只通过磁盘文件传递，不得通过内存对象；不得跨阶段复用缓冲
- A7 已交付（`codex_adapter.py` 已是 SDK 实现，`generate()` 实测返回 completed），
  不再是"尚未交付"。测试里仍可 monkeypatch `CodexAdapter.generate` 来隔离网络，
  被测对象是阶段函数本身，这不算"mock 掉被验证对象"，但必须在测试 docstring 写明
- prompt_loader 严格按 I4：缺失抛 AssetMissing，禁止回退内置提示词；
  首行无版本号记 warning 但加载成功
- 当前**不存在** `source_code/prompts/`（B 未交付，这是事实）。因此：
  · 工程测试用 `tests/fixtures/engineering/stub_prompts/`，该目录不参与正式运行
  · 正式 `python main.py run ... --stage recognize` 必然 AssetMissing 退出 8，
    这是正确行为，不得为了让链路跑通而创建 prompts/ 或内联提示词

禁止：为了让阶段跑通而在代码里内联任何提示词文本；
禁止改 main.py 的 --stage 语义（W0 已定：指定阶段含全部前置）。
```

### 8.6 W2 · 四路并行卡（一句话版，按 §8.2 通用头套用）

| 会话 | 卡 | 白名单 | 关键约束 |
|---|---|---|---|
| S1 | A11 | `writer.py`、`repair.py` | 构造 payload 的函数不得持有 pdf 路径或 recognition 对象；修复上限读 settings；每轮留 diff |
| S2 | A14(1–4) | `tests/test_engineering_faults.py` | 断言 code 与退出码，不只断言"失败了"；禁止 try/except 包住断言 |
| S3 | A15 | `packaging.py` | 用标准库 zipfile；读回逐条比 CRC；脚本内出现 .env/key/源 PDF 直接失败；**不改 main.py** |
| S0 | A10 | 无（只跑不改） | 只允许修自身 bug；接口不符与缺资产报告，不改契约 |

### 8.7 交接卡（并行版，替代原 U1）

```text
【并行交接摘要】
上下文将满。先完成 .parallel/handoffs/<CARD>.md 的五节，再输出本摘要，不写新代码。

摘要必须包含：
1. 本卡已完成 / 未完成的文件（逐行，未完成标注 TODO 与卡住的具体位置）
2. 已占用的 CLAIMS 行是否已释放；未释放的写明
3. 与其他会话的接口接触点：你调用了谁的什么签名，对方是否已交付
4. 阻塞项与解除条件（按是否阻塞下游排序）
5. 下一会话开工前必须先读的三个文件路径

不超过 60 行。凡"看起来完成但实际是占位"的位置，必须点名。
```

---

## 9. 待决策（未定则无法开工）

| 级别 | 事项 | 影响 | 结论 / 建议 |
|---|---|---|---|
| **已定** | 模型调用路径 | 决定 A7 全部实现 | **openai-codex SDK → Codex app-server**。不引入 `openai` SDK，不写 HTTP 客户端。已核实 PyPI 0.169.0，本机 CLI 0.167.0 |
| **已定** | PDF 渲染库 | `requirements.txt` 在 W0 冻结 | `pypdfium2`。已实测 py3.13 下 `pip install --dry-run pypdfium2 openai-codex pytest` 退出码 0 |
| **已定** | `validation.schema.json` 升级为 `issues` | S2(A9) 输出 `ValidationReport` 与现行 schema 互斥 | 升级。I3 §3 已冻结顶层键为 `valid`/`issues`，属落盘对齐而非造字段 |
| **P0** | 是否允许 W0 直接改 `requirements.txt` 新增 3 个依赖 | 「新增依赖需说明理由」需你批准 | 批准。openai-codex（Codex runtime 控制）、pypdfium2（PDF 渲染）、pytest（校验器测试） |
| **已解除** | `codex login` 前置条件 | 实测 `CodexAdapter.generate()` 返回 `completed` | 本机 `~/.codex/config.toml` 已配自建网关（`requires_openai_auth=false`），走自定义 provider 不需登录态。项目侧要独立网关时补 `.env` 的 `LLM_BASE_URL`/`LLM_API_KEY` 即可 |
| P1 | `docs/` 被 git 忽略，三份核心文档不在版本控制 | 接口冻结清单无法填 commit hash（自相矛盾） | Wave 0 后单独提一次 commit，或加白名单 |
| P1 | A1 的 key 何时到位 | A6 渲染分支、A7 真实调用无法验收 | 到位后插在 W1 之后单开一卡补做 |
| P2 | 并行窗口数 | 超过 4 路审核跟不上 | 固定 S0 + 3 路 |

---

## 10. 立即执行顺序（从 19:30 起）

1. 定 §9 的三个 P0（约 5 分钟）。
2. 开 **S0 会话**，贴 §8.1 地基卡。等它给 A、B 两项 → 确认 → 实施。预计 90–120 分钟。
   这一段不能拆分并行：8 个签名骨架与 `contracts.py` 的字段必须由同一个上下文一次定完，
   分给两个会话必然出现字段命名不一致。
3. S0 交付后，你一次性核对八条验收输出，然后 `git tag w0-kernel`。
4. 同时开 **S1 / S2 / S3** 三个会话，各贴 §8.2 通用头 + §8.3 / §8.4 / §8.5 对应卡。
5. 三路开工后，你只需在每路交付时核对 handoff 的第 3 节（验收原始输出）与第 5 节（下游需知）。
