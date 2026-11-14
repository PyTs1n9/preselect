# 接口冻结清单（A ↔ B）

> 本文件是 A（系统工程）与 B（Skill 与质量）之间**唯一有效的接口约定**。
> 聊天记录、口头说明、代码注释均不构成约定。
>
> 冻结方式：逐行给出「同意 / 需改」，需改之处当场写入本文件；
> 冻结后在 §12 填写 commit 与确认。
>
> 配套文档：`docs/A_开发提示词方案.md`（任务卡 A0–A16 引用本文件编号）

---

## 0. 边界原则（先定权责，再定字段）

四条原则决定了后面每个接口由谁定义：

| 原则 | 含义 |
|---|---|
| **字段提案权在 B，落盘权在 A** | `schemas/` 目录由 A 写入（A 的文件边界），但字段含义、取值、空值语义由 B 定义。B 提供片段，A 原样落盘，不发挥。 |
| **内容资产只读** | `skills/`、`prompts/`、`fixtures/quality/` 归 B。A 只做加载、哈希、存在性校验，任何情况下不写入。 |
| **判定权在 Python** | 是否合格只由 `validate.py` 的确定性检查给出。B 的提示词可以要求模型写好，但不能作为通过依据。 |
| **失败必须显式** | 任何缺失、任何异常都映射到明确的 code 与退出码。禁止兜底默认值、禁止降级继续。 |

---

## 1. I1 · CLI 契约

**定义方** A　**消费方** A / B / 评委

```text
python main.py run <pdf> [--config PATH] [--output DIR] [--material-id ID]
                        [--stage recognize|normalize|evidence|write|all]
python main.py doctor [--offline]
python main.py validate --run-id <id>
python main.py package --materials M1,M2,M3,M4 --out PATH
```

冻结内容：

| 项 | 约定 |
|---|---|
| `--stage` 语义 | 指定阶段及其**全部前置阶段**都执行；`all` 等价于全链路 |
| 断点重跑 | 前置阶段产物存在且校验通过时跳过，manifest 记 `skipped`，不算失败 |
| `--offline` | `doctor` 唯一允许的离线模式，禁止任何网络请求 |
| `--material-id` | 决定运行目录层级与串稿检测基准，缺省时由 PDF 文件名推导并 warning |
| 输出 | stdout 只输出人读摘要；机器可读结果一律落盘为 JSON。`--json` 时 stdout 输出单个 JSON 对象 |
| 退出码 | 见 I7。任何非 0 都是失败，不得用 0 表示"部分成功" |

B 的用法：B 不需要读 A 的代码，只需要会用 `doctor --offline` 确认环境和 `validate --run-id` 复检产物。

---

## 2. I2 · 运行目录契约

**定义方** A　**消费方** A / B / 评委复核

```text
result/runs/<material_id>/<run_id>/
├── manifest.json              # I8
├── input/
│   ├── source.pdf.sha256      # 只有哈希，不放源 PDF
│   └── page_0001.png ...      # 仅 vision_path=image_png 时存在
├── raw/
│   ├── recognize_1.json       # 每次模型调用的原始响应，按 <stage>_<attempt>
│   ├── normalize_1.json
│   ├── repair_1_request.json
│   └── repair_1_diff.md
├── recognition.json           # 原始识别层，结构不改写
├── page_text.json             # recognize 必产：逐页清洗后的文本层与字符数
├── page_plan.json             # recognize 必产：逐页 kind 与 reasons，模型触发决策的唯一依据
├── tables.json                # recognize 按需产出：通过数字比对的表格页结果
├── figures.json               # recognize 按需产出：通过数字比对的图页结果
├── formulas.json              # recognize 按需产出：通过数字比对的公式页结果
├── document_ir.json           # 结构归一化层
├── evidence.json               # 事实证据层，写作的唯一输入
├── draft_en.md                 # 英文草稿：两段式写作第一段的产物
├── draft.md                    # 写作初稿（中文译写）
├── validation.json             # I6
├── final.md                    # 修复通过后的终稿；失败时不存在
├── logs/pipeline.log
└── package_report.json         # 仅 package 阶段产出
```

冻结内容：

- 文件名与目录名逐字固定，B 的脚本与人工复核依赖此布局。
- 阶段性产物即使为空也必须存在（例如 issues 为空时 `validation.json` 仍是完整对象）。
- 失败时已产出的中间文件**保留不删**，便于定位；只有 `final.md` 在失败时不得存在。
- 源 PDF 不进运行目录，只记 sha256。这是提交包排除源 PDF 的前置保证。

**A19 补充（2026-11-14）**：`recognize.PRODUCES` 为 `recognition`、`page_text`、
`page_plan`；表格、图、公式三个可选产物仅在该 run 有未被拒绝的对应视觉输出时登记到
artifacts，不作为必产物。三个可选文件均为 `{"pages":[<逐页 schema 合规的模型对象>]}`，
页号保持来源页号，按页序汇集，不跨页合并表格。`page_plan.reasons` 记录判定指标、
未知图像面积及数字冲突/拒绝原因。I2 其他阶段与 orchestrator 不变。

---

## 3. I3 · Schema 契约

**定义方** B（字段语义）　**落盘方** A（写入 `schemas/`）　**消费方** A

| Schema | 对应层 | 必须存在的顶层键 | 当前版本 |
|---|---|---|---|
| `recognition.schema.json` | 原始识别层 | `pages`；每页含 `page_number` 与 `blocks`，每块含 `block_id`、`text`、`region`（归一化 0–1，可 null） | **1.0.0**（A 落盘，字段依 SKILL.md v1.1.0） |
| `document_ir.schema.json` | 结构归一化层 | `document_id`、`source{path_name,page_count,sha256}`、`pages`、`blocks`（块结构同识别层） | **1.0.0**（A 落盘） |
| `evidence.schema.json` | 事实证据层 | `material_id`、`facts`；每条事实含 `fact_id`、`core`、`page_number[]`、`block_id[]`、`quote`、`value`、`value_text`、`unit`、`region` | **1.0.2**（B 定义，A 落盘） |
| `validation.schema.json` | 校验结果 | `valid`、`issues`、`checks` | **1.1.0**（A 定义，见下） |
| `table.schema.json` | 表格页视觉输出 | `page_number`、`tables`；表含 `table_id`、`caption`、`header`、`rows`、`bbox` | **1.0.0**（B 定稿，A 原样落盘） |
| `figure.schema.json` | 图页视觉输出 | `page_number`、`figures`；图含 `figure_id`、`caption`、`description`、`readouts`、`bbox` | **1.0.0**（B 定稿，A 原样落盘） |
| `formula.schema.json` | 公式页视觉输出 | `page_number`、`formulas`；公式含 `formula_id`、`text`、`symbols`、`bbox` | **1.0.0**（B 定稿，A 原样落盘） |

冻结内容：

1. 每个 schema 必须有 `$id` 与 `schema_version` 字段，A 的加载器据此判断兼容性。
2. 任一 schema 变更字段名、删除字段、收紧枚举，**必须同时**升 `schema_version` 并双人确认，否则 A 的加载器按 `SCHEMA_VERSION_MISMATCH` 失败。
3. `additionalProperties` 默认 `false`。七份 schema 的每个 object 均已关闭额外属性
   （2026-11-14 由 `schema_loader` 的 strict 预检逐层校验，不合规立即 `SCHEMA_INVALID`）。
   早期"4 个文件都是 true"的表述已作废。
4. A 不为缺失字段提供默认值。字段缺失就是 issue。
5. **版本门逐 schema 独立**：`schema_loader.SUPPORTED_VERSIONS` 按 schema 名登记，
   升 validation 不会波及 recognition。单一全局版本常量不成立，已移除。

**变更记录**：

| 日期 | Schema | 从 → 到 | 原因 | B 确认 |
|---|---|---|---|---|
| 2026-11-14 | `table` / `figure` / `formula` | 新增 → **1.0.0** | A19 按页触发视觉；片段逐字落盘，全部登记既有 strict 预检；表格额外以 Python 检查非空表头和矩形行列 | 字段由 B 于 2026-11-14 定稿，A 落盘，正式双签待补 |
| 2026-11-13 | `validation` | 1.0.0 → 1.1.0 | 新增 `checks{enabled,skipped}`；A9 因缺 evidence 字段需显式声明未执行项，避免 `valid=true` 被误读为全检通过 | 待确认 |
| 2026-11-13 | `validation` | 1.1.0 → 1.1.0（不变） | 仅给 `valid` 与 `stats` 四个字段补 `description`，明确 `evidence_count` 是 0/1 对象数、`citations`/`unreferenced_evidence` 在对应检查 skipped 时恒为 0。无结构、无枚举、无必需字段变化，故不升版本；B 若认定为结构变更，再升 1.1.1 并同步 A9 版本断言 | 暂不确认，保持 1.1.0 |
| 2026-11-14 | `evidence` | 未声明 → **1.0.0** | B1 首次定义字段：顶层 `material_id` + `facts` 数组，事实含 `fact_id`/`core`/`page_number[]`/`block_id[]`/`quote`/`value`/`value_text`/`unit`；`additionalProperties` 由 `true` 收紧为 `false`。片段原样取自 `.parallel/B-FIELD-REQUEST.md` 答复区与 `docs/B1_交付说明.md`，A 未增删字段。已登记 `schema_loader.SUPPORTED_VERSIONS["evidence"]={"1.0.0"}` | B1 答复为字段依据；**正式双签待补**（§12 I3 行仍为 ☐） |

**落盘副作用（2026-11-14 A17）**：`evidence` 收紧后，5 个工程夹具的 `evidence.json`
从"单条事实"迁移为 `facts` 结构并重生成 `validation.json`；`tests/test_engineering_validate.py`
的内联证据同步迁移。`stats.evidence_count` 口径不变（schema 合规的证据对象数，恒 0/1），
因此 `validation` 不升版本。引用数、未引用数与 `checks.skipped` 仍由 A14 负责。

| 2026-11-14 | `recognition` | 未声明 → **1.0.0** | 字段依 `skills/pdf-vision/SKILL.md` v1.1.0：`pages[].blocks[]` 含 `block_id`/`text`/`region`；为通过供应商 strict structured output，每个 object 关闭 `additionalProperties`、`required` 覆盖全部 properties，`region` 为归一化 0–1 且可 `null` | B 依据为 SKILL.md，正式双签待补 |
| 2026-11-14 | `document_ir` | 未声明 → **1.0.0** | 块结构与识别层对齐，`source` 定为 `path_name`/`page_count`/`sha256` 三键可空；`A` 不新增业务字段 | B 依据为 SKILL.md，正式双签待补 |
| 2026-11-14 | `evidence` | 1.0.0 → **1.0.1** | 仅为 strict 兼容：`value_text`/`region` 由可选改为 required + null，`oneOf` 改 `anyOf`。未新增、未删除字段，语义与 B1 答复一致 | 兼容性调整，字段语义不变 |
| 2026-11-14 | `evidence` | 1.0.1 → **1.0.2** | 移除 `page_number` 的 `uniqueItems`。真实 provider 输出 `page_number: [1,1]`（一条事实引用同页两个块），被该约束拒绝，而字段自述是"与 `block_id` 按索引对应"，两者冲突。定位数组是位置式，重复页码必须合法；长度一致性改由 `validate.py` 的 `_check_consistency` 检查（不等长报 `SCHEMA_INVALID`）。未新增、未删除字段 | 证据见 `docs/handoff-gate.md` 真实链路表；**待 B 确认** |

**strict 兼容检查**：`schema_loader.MODEL_OUTPUT_SCHEMAS` 在加载时递归校验三条规则
（每个 object 必须 `additionalProperties:false`、`required` 覆盖全部 properties、禁用 `oneOf`），
不合规立即 `SCHEMA_INVALID` 退出码 4，不再花一次远端往返才被拒。

**A19 实施确认**：用户于 2026-11-14 确认本卡范围，并允许仅将 `vision` 1.2.0 的
`region` 说明改为现有 recognition schema 的坐标对象数组
`[{"x0":x0,"y0":y0,"x1":x1,"y1":y1}]` 或 `null`。既有 recognition schema
及其版本不变；其余提示词文字与三份新 schema 均按附件原文落盘，不代填 §12 签字。

**当前状态**：七份模型输出 schema 已于 2026-11-14 全部落盘（recognition 1.0.0、
document_ir 1.0.0、evidence 1.0.2）。`evidence.facts[].block_id` 与
`document_ir.blocks[].block_id` 的交叉存在性校验**已实现**（`validate.py` 的
`_check_block_locators`，缺失块报 `SCHEMA_INVALID`；同一函数的 `_check_consistency`
另检查 `page_number` 与 `block_id` 等长）。

遗留一项属 B 的内容资产：`fixtures/quality/*/document_ir.json` 的 `blocks` 为空数组，
而对应 `evidence.json` 引用 `B001`。交叉校验会对这五个夹具追加一条 `SCHEMA_INVALID`，
不影响 I10 的 `expect_codes` 断言，但夹具本身不自洽，待 B 补齐块定义。

---

## 4. I4 · 内容资产加载契约

**定义方** A（加载行为）　**内容方** B　**消费方** A

### 4.1 路径约定

| 资产 | 路径 | A 的行为 |
|---|---|---|
| Skill | `source_code/skills/pdf-vision/SKILL.md` | 校验存在性、非空、算 sha256 入 manifest；运行期以 `SkillInput(name, path)` 交给 Codex 加载，**正文不拼进 prompt** |
| 视觉提示词 | `source_code/prompts/vision.md` | 读全文作为 `TextInput`，sha256 入 manifest；扫描页整页转写 |
| 表格提示词 | `source_code/prompts/table.md` | 同上；`table_page` 触发，产出 `tables.json` |
| 图提示词 | `source_code/prompts/figure.md` | 同上；`text_page_with_figure` 触发，产出 `figures.json` |
| 公式提示词 | `source_code/prompts/formula.md` | 同上；`formula_page` 触发，产出 `formulas.json` |
| 归一化提示词 | `source_code/prompts/normalize.md` | 同上 |
| 证据提示词 | `source_code/prompts/evidence.md` | 同上 |
| 写作提示词 | `source_code/prompts/writer.md` | 读全文作为 `TextInput`，sha256 入 manifest；产出英文草稿 `draft_en.md` |
| 翻译提示词 | `source_code/prompts/translate.md` | 读全文作为 `TextInput`，sha256 入 manifest；只吃 `draft_en`，产出中文 `draft.md`。**当前文件不存在，属 B 待交项（G1-B01）**；缺失时 write 阶段按 `ASSET_MISSING` 失败，不降级 |
| 修复提示词 | `source_code/prompts/repair.md` | 同上 |
| 质量夹具 | `source_code/fixtures/quality/<case>/` | 只读消费，结构见 I10 |

**提示词集合以本表 + 代码实际加载点为准**，共 8 个现存文件加 1 个待交项（`translate`）。
`pipeline/stages/recognize.py` 经 `_VISUAL_PROMPTS` 按页类型选择 `vision`/`table`/`figure`/`formula`，
`tools/check_assets.py` 会解析该字面量表，因此动态选择不构成"未声明"。

### 4.2 加载行为（A 实现 `pipeline/prompt_loader.py`）

```python
load_prompt(name: str, base_dir: Path) -> PromptAsset
# PromptAsset: text, sha256, path, version
# text 进入 ModelRequest.prompt_text，最终作为 TextInput 传给 Codex

load_skill(path: Path) -> SkillAsset
# SkillAsset: text, sha256, path
# text 只用于校验与哈希，不进入 prompt_text；
# 传给 Codex 的是 SkillInput(name="pdf-vision", path=str(path))
```

**两种资产的投递方式不同，不得混用**：提示词是文本，由 A 拼装后作为 `TextInput` 发送；
skill 是引用，由 Codex 按路径自行加载。若把 SKILL.md 正文塞进 prompt，Codex 会把
它当成一次性指令而非可复用 Skill，B 后续改 skill 也不会被正确加载。

冻结语义：

| 情况 | 行为 |
|---|---|
| 文件缺失 | 抛 `AssetMissing(code=ASSET_MISSING)`，**不回退内置提示词** |
| 文件为空或仅空白 | `AssetEmpty(ASSET_EMPTY)`，同样失败（提示词与 Skill 均适用，见 2026-11-14 变更） |
| 提示词首行无 `<!-- version: x.y.z -->` | 加载成功但记 `ASSET_NO_VERSION` warning |
| Skill 的 YAML frontmatter 内无 `version:` | 加载成功但记 `ASSET_NO_VERSION` warning；版本值同时写入 `stages[].model_calls[].skill_version` |
| 同一 run 内重复加载 | 允许，但 manifest 只记一份 sha256 |

**Skill 版本为何不写在首行**：Agent Skills 规范要求第 1 行为 `---` 起始的 YAML frontmatter，
首行 HTML 注释与规范互斥。故 Skill 的版本取自 frontmatter 的 `version:`（或 `metadata.version:`），
提示词仍取首行 HTML 注释。`pipeline/prompt_loader.py` 的 `_skill_version()` 同时支持两种写法，
并在 `manifest.assets.skills` 记 sha256、在 `model_calls[].skill_version` 记版本号。
本行替代 2026-11-14 之前"所有资产首行必须有版本注释"的表述。

关键约定：**A 绝不内置业务提示词。** 若 B 未交付，链路就是跑不通——这是刻意设计，避免"看起来能跑但用的是 A 私自编写的提示词"。A 的工程测试使用 `source_code/tests/fixtures/engineering/stub_prompts/`，该目录不参与正式运行，也不会被误当成 B 的资产。

### 4.3 版本记录

每次提示词修改，B 必须更新首行版本号，A 无需额外动作即可在 manifest 中留痕。这是 B 计划书「每次提示词修改都保存版本号」的可执行落点。

---

## 5. I5 · 模型调用契约（Codex 接入层）

**定义方** A　**消费方** A（B 不直接调用 SDK）

### 5.1 分层（冻结，不得混层）

```text
Python Orchestrator
  → openai-codex SDK（pip install openai-codex，import openai_codex）
  → Codex app-server（JSON-RPC v2 over stdio，SDK 启动）
  → Codex Agent
  → pdf-vision Skill（SkillInput 传入，Codex 加载）
  → 底层模型（Responses API，经 model_providers 配置）
```

三条硬约束：

1. Python 侧不直连模型 HTTP 接口。**不引入 `openai` 依赖，不自写 HTTP 客户端。**
2. PDF 不在 SDK 内解析。输入只能是 `LocalImageInput` / `ImageInput`，故 A6 必须先渲染 PNG。
3. Codex 是 agentic 的。`thread_start` 必须带 `sandbox=Sandbox.read_only` 与
   `ephemeral=True`，否则它可能改写工作区文件。

### 5.2 数据结构

```python
ModelRequest(stage, prompt_name, prompt_text, payload,
             json_schema=None, images=None, skill=None,
             timeout_s=None, model=None)

ModelResult(text, parsed, raw, usage, truncated,
            latency_ms, attempts, request_hash, model,
            turn_status, error_message)
```

| 字段 | 来源（SDK 侧） | 说明 |
|---|---|---|
| `images` | `LocalImageInput(path)` 列表 | 只接受本地 PNG 路径，不接受 PDF |
| `skill` | `SkillInput(name, path)` | 传路径给 Codex，由 Codex 加载；A 不读其内容 |
| `json_schema` | `thread.run(output_schema=...)` | 原生结构化输出，不靠提示词催 JSON |
| `text` | `TurnResult.final_response` | 可能为 None（仅工具调用时） |
| `usage` | `TurnResult.usage` | `ThreadTokenUsage` |
| `latency_ms` | `TurnResult.duration_ms` | SDK 原生计时 |
| `raw` | `TurnResult.items` 逐项 dump | 审计用，落 `raw/<stage>_<attempt>.json` |
| `turn_status` | `TurnResult.status` | `completed` / `interrupted` / `failed` / `in_progress` |
| `error_message` | `TurnResult.error.message` | 脱敏后保留，原文进 raw |

### 5.3 冻结语义

| 情形 | 约定 |
|---|---|
| `turn_status == interrupted` | 置 `truncated=True` 并抛 `TruncatedResponse`，半截输出不得进入下游 |
| `turn_status == failed` | 按 `error.message` 归类为 `ModelRefused` 或 `InvalidModelOutput`，禁止转成空结果 |
| `final_response` 为空但 `turn_status == completed` | 抛 `InvalidModelOutput`，不得返回空字符串当作成功 |
| JSON 解析失败 | 抛 `InvalidModelOutput`，附原文前 200 字符（脱敏） |
| 可重试 | `is_retryable_error(exc)` 为真：`ServerBusyError`、`RetryLimitExceededError`、`TransportClosedError` |
| 不可重试 | `CodexError` 中参数类错误、`MethodNotFoundError`、`InvalidParamsError`；立即失败，不消耗重试 |
| 重试次数 | `settings.vision.max_retries`；退避由 `retry_on_overload` 承担，不自行实现 |
| 超时 | SDK 无 per-turn timeout 参数。必须用 `thread.turn()` 取 `TurnHandle`，超时调 `interrupt()`，再判 `interrupted` |
| 原始响应 | 每次尝试落 `raw/<stage>_<attempt>.json`，含 `usage`、`duration_ms`、`turn_status` |
| 进程复用 | 一个 run 内只启一个 `Codex` 实例；禁止每次调用 `Codex()` |
| 密钥 | `LLM_API_KEY` 经 `env_key` 由 Codex 从环境读取；不进日志、不进异常、不进 raw、不进 manifest |
| provider 选择 | `get_provider("openai_codex")` → `CodexAdapter`；未知名字抛 `ConfigError`，禁止静默回退 |

### 5.4 供应商配置

Codex 用 `model_providers` 描述上游，A 通过 `CodexConfig.config_overrides` 透传：

```text
model=<LLM_MODEL>
model_provider=<id>
model_providers.<id>.base_url=<LLM_BASE_URL>
model_providers.<id>.env_key=LLM_API_KEY
model_providers.<id>.wire_api=responses   # 不支持时降级 chat，需在 manifest 记录实际值
```

`env_key` 只写环境变量名，不写密钥值。`config.json` 不存密钥，`.env` 不进版本控制。

B 需要知道的唯一一点：**提示词文本与 skill 路径由 A 按 I4 传入，B 不需要关心 SDK、
重试、超时、沙箱。** B 若需要模型输出特定 JSON，只需在提示词中说明，并把目标 schema
交给 A（A 经 `output_schema` 启用结构化输出）。

---

## 6. I6 · 校验结果与错误码契约

**定义方** A（code 枚举）　**消费方** A（repair）/ B（修复提示词）/ 评委

### 6.1 结构（替代当前 `{"valid":bool,"errors":[str]}`）

```json
{
  "valid": false,
  "schema_version": "1.1.0",
  "checked_at": "2026-11-13T09:00:00Z",
  "stats": {"han_chars": 1780, "citations": 6, "evidence_count": 21,
            "unreferenced_evidence": 15},
  "issues": [
    {
      "code": "MD_BODY_TOO_SHORT",
      "stage": "validate",
      "path": "final.md#body",
      "severity": "error",
      "message": "正文汉字数 1780，低于阈值 2500",
      "hint": "补充实验过程与限制说明，不要删除数据"
    }
  ],
  "checks": {
    "enabled": ["SCHEMA_INVALID", "MD_MISSING_SECTION", "MD_BODY_TOO_SHORT",
                "MD_BAD_ENCODING", "SECRET_LEAK", "ABS_PATH_LEAK"],
    "skipped": ["CITE_NOT_IN_EVIDENCE", "EVIDENCE_UNREFERENCED",
                "NUMBER_MISMATCH", "UNIT_MISSING", "MATERIAL_MISMATCH"]
  }
}
```

**`checks` 的用途（1.1.0 新增）**：回答"这份报告到底查了哪几项"。
`skipped` 非空意味着有检查因缺字段而未执行，此时 `valid=true` 只表示
"已启用的检查全过"，**不表示八项全过**。消费方（打包、人工复核）必须读这个字段：

- A15 打包门槛：`checks.skipped` 必须为空才允许打包。
- 若 `skipped` 非空仍要出报告，必须在报告外显式说明未覆盖项，不得口头带过。

**A14-1 实现登记（2026-11-14，[待确认]）**：五项一致性检查现由 Python 确定性执行并列入
`checks.enabled`；`checks.skipped` 恒为空数组。缺少 evidence 或 final/draft 仍按既有
`ASSET_MISSING` issue 显式失败，不以 skipped 表示缺输入。`citations` 统计正文中成功解析的
引用出现次数（重复计数，未知 ID 不计）；`unreferenced_evidence` 统计未被正文引用的 core
事实数；`evidence_count` 仍为 schema-valid evidence 对象数 0/1。此项改变 skipped 的冻结语义，
正式双签待补，不代任一方确认；`validation.schema.json` 结构和版本不变。

### 6.2 code 枚举（D1 冻结，B 的修复提示词依赖此表）

| 类别 | code | 含义 |
|---|---|---|
| 结构 | `SCHEMA_INVALID` | 不满足对应 JSON Schema |
| 结构 | `SCHEMA_NOT_FOUND` | schema 文件缺失 |
| 结构 | `SCHEMA_VERSION_MISMATCH` | 版本不兼容 |
| 资产 | `ASSET_MISSING` | 提示词或 Skill 缺失 |
| 资产 | `ASSET_EMPTY` | 资产为空 |
| 资产 | `ASSET_NO_VERSION` | 缺版本号（warning） |
| 输入 | `PDF_ENCRYPTED` | PDF 加密 |
| 输入 | `PDF_EMPTY` | 0 页 |
| 输入 | `PDF_TOO_LARGE` | 超页数或体积上限 |
| 输入 | `PDF_RENDER_FAILED` | 某页渲染失败（附页号） |
| 模型 | `MODEL_TIMEOUT` | 调用超时 |
| 模型 | `MODEL_TRUNCATED` | 输出被截断 |
| 模型 | `MODEL_INVALID_JSON` | 输出无法解析 |
| 模型 | `MODEL_REFUSED` | 模型拒答 |
| 文档 | `IR_MISSING_PAGE` | 块缺 `page_number` |
| 文档 | `IR_MISSING_BLOCK_ID` | 块缺 `block_id` |
| 文档 | `IR_PAGE_OUT_OF_RANGE` | 页号超出页数 |
| Markdown | `MD_MISSING_SECTION` | 缺必需章节 |
| Markdown | `MD_BODY_TOO_SHORT` | 正文不足 2500 汉字 |
| Markdown | `MD_BODY_TOO_LONG` | 正文超过上限 |
| Markdown | `MD_BAD_ENCODING` | 非 UTF-8 / 含 BOM |
| 一致性 | `CITE_NOT_IN_EVIDENCE` | 正文引用找不到证据 |
| 一致性 | `EVIDENCE_UNREFERENCED` | 核心证据未被引用 |
| 一致性 | `NUMBER_MISMATCH` | 数值或单位与证据不符 |
| 一致性 | `UNIT_MISSING` | 数值缺单位 |
| 一致性 | `MATERIAL_MISMATCH` | 串稿（material_id 不一致） |
| 安全 | `SECRET_LEAK` | 疑似密钥 |
| 安全 | `ABS_PATH_LEAK` | 本机绝对路径 |
| 修复 | `REPAIR_EXHAUSTED` | 修复次数耗尽 |
| 修复 | `REPAIR_INTRODUCED_FACT` | 修复引入证据外事实 |

`severity` 取值 `error` / `warning`。**只有 error 影响 `valid`**；warning 必须在 `stats` 或 issues 中可见，不得丢弃。

> **2026-11-13 冻结条款（A 侧确认）**：`AUTH_MISSING` **不在上表**。上表是
> `validation.json` 的 issues 枚举，描述"文档哪里不合格"；`AUTH_MISSING` 描述
> "运行环境没有可用凭据"，属于运行失败类，只出现在终端摘要与日志，退出码 2。
> 实现位置：`pipeline/errors.py` 的 `AuthMissing`，不得加进 `Code` 枚举。
> B 侧签字见 §12。

### 6.3 repair 的输入契约

repair 阶段只接收 `issues` 中 `severity=error` 的条目。B 的 `prompts/repair.md` 应约定模型逐条处理 code，不允许"整体重写"。A 的职责是：把 issues 原样传入、限制轮次、检测 `REPAIR_INTRODUCED_FACT`。

---

## 7. I7 · 阶段状态与退出码契约

**定义方** A　**消费方** A / B / 自动化脚本

| 退出码 | 含义 | 典型 code |
|---|---|---|
| 0 | 全部阶段 ok（含 skipped） | — |
| 1 | 未分类异常 | — |
| 2 | 参数、配置或凭据错误 | `ConfigError`、`AUTH_MISSING` |
| 3 | 输入非法 | `PDF_*` |
| 4 | 结构或内容校验失败且不可修复 | `SCHEMA_*`、`IR_*`、`validate` 子命令 `valid=false` |
| 5 | 模型调用失败 | `MODEL_*` |
| 6 | 修复耗尽或修复失败 | `REPAIR_*` |
| 7 | 打包或读回校验失败 | — |
| 8 | 内容资产缺失 | `ASSET_*` |

> **2026-11-13 冻结条款（A 侧确认）**
> 1. `AUTH_MISSING` 归入退出码 2，不归 8。8 的语义是"内容资产缺失"，凭据缺失是
>    运行环境问题；I6 的 30 条 Issue 枚举里没有 `AUTH_MISSING`，它只是
>    `PipelineError.code`，不进入 `validation.json`。
> 2. `validate` 子命令在 `valid=false` 时必须返回 4，不得返回 0。报告仍照常打印，
>    退出码用于打包与 CI 的快速失败。
> 3. 落地位置：`pipeline/errors.py` 的 `AuthMissing`、`main.py` 的 `_validate`；
>    测试见 `tests/test_engineering_cli.py`。B 侧签字见 §12。

阶段状态：`pending` / `running` / `ok` / `failed` / `skipped`。

冻结语义：

- `skipped` 只出现在断点重跑且前置产物通过校验时，**不得**用来表示"跳过因为没实现"。
- 未实现的阶段必须 `failed` 并退出非 0。这一条直接针对"阶段失败伪装成成功"。
- 一个 run 内只要有任一阶段 `failed`，进程退出码必须非 0，即使其余阶段都 ok。

---

## 8. I8 · Manifest 字段契约

**定义方** A　**消费方** B / 评委

```json
{
  "run_id": "...", "material_id": "M1",
  "started_at": "...", "ended_at": "...",
  "python": "3.11.9", "platform": "Windows-10",
  "input": {"path_name": "xxx.pdf", "sha256": "...", "page_count": 8},
  "code": {"git_commit": "...", "files": {"pipeline/validate.py": "sha256..."}},
  "assets": {"prompts": {"writer": "sha256..."},
             "skills": {"pdf-vision": "sha256..."},
             "schemas": {"evidence": "sha256..."}},
  "config": {"vision.model": {"value": "...", "source": "env"}},
  "model": {"sdk": "openai-codex", "sdk_version": "0.169.0",
            "binary_version": "0.167.0", "provider": "<model_provider id>",
            "model": "...", "wire_api": "responses", "sandbox": "read-only",
            "ephemeral": true, "max_retries": 2},
  "calls": {"recognize": 1, "normalize": 1, "repair": 0},
  "stages": [{"name": "recognize", "status": "ok", "duration_ms": 8123}],
  "deps": {"jsonschema": "4.23.0", "pypdf": "5.1.0", "openai-codex": "0.169.0"},
  "reproducibility": "not-bitwise",
  "cache": {"enabled": true, "key": "...", "hit": false}
}
```

冻结语义：

1. `input.path_name` 只存文件名，**不存绝对路径**（避免路径泄漏与迁移失效）。
2. `config` 每项必须带 `source`（cli/env/dotenv/config/default），这是"配置从哪来"的唯一证据。
3. **`reproducibility` 恒为 `not-bitwise`。** Codex 不暴露 `temperature`，
   `model` 段不得出现该字段（写了即为伪造参数）。可复现性靠 I8 的输入/过程哈希保证，
   不承诺逐字一致。
4. `assets` 中每个 sha256 对应 I4 的资产版本，B 改提示词后 manifest 必然变化，可反查。
5. `model.sdk_version` 与 `model.binary_version` 必须同时记录：SDK 与 Codex 二进制
   版本不匹配会直接导致 app-server 协议失败，这是复现问题的第一诊断项。
6. `model.sandbox` 必须是 `read-only`。其他取值视为违反 I5 §5.1 第 3 条。

---

## 9. I9 · Stage 注册契约（扩展点）

**定义方** A　**使用方** B（后续可选）

```python
register_stage(name: str, fn: Callable[[RunContext, dict], dict],
               requires: list[str], produces: list[str])
```

冻结语义：

- 阶段函数签名统一为 `(ctx, inputs) -> outputs`，`inputs` / `outputs` 均为 artifact 路径字典。
- 阶段之间只通过磁盘文件通信，不通过内存对象。这是断点重跑与故障注入的前提。
- B 后续若要加 `quality_gate` 阶段（人工抽查结果比对），只需注册，不改 `orchestrator.py`。这满足 B 计划书「B 不直接修改 pipeline/orchestrator.py」的边界。

A 的承诺：`orchestrator.py` 内不出现任何具体阶段名的业务判断，只认注册表。

---

## 10. I10 · 质量夹具契约

**定义方** B（内容）　**消费方** A（测试执行）

```text
source_code/fixtures/quality/<case_name>/
├── expect.json
└── （可选）draft.md / document_ir.json / evidence.json 等被检输入
```

`expect.json` 结构：

```json
{
  "case": "fake_citation",
  "description": "正文引用 [9]，evidence 只有 3 条",
  "artifact": "final.md",
  "expect_codes": ["CITE_NOT_IN_EVIDENCE"],
  "severity": "error",
  "must_not_codes": ["REPAIR_EXHAUSTED"],
  "notes": "B 的人工判定依据"
}
```

冻结语义：

- A 的 `tests/test_engineering_fixtures.py` 遍历该目录，逐 case 运行校验器，断言 `expect_codes` 全部出现。
- `must_not_codes` 用于防误报：避免"报了错但报错原因不对"也算通过。
- 夹具缺失时该测试标记 `skipped` 并在报告中标红，**不得**因为夹具没做好就让测试整体变绿。
- A 不修改夹具内容，只消费。B 新增 case 无需通知 A，测试自动覆盖。

---

## 11. 变更流程

任一侧要改已冻结内容，走同一流程：

1. 在本文件对应行旁标注 `[待改]`，写明改什么、为什么、影响哪些 code。
2. 对方在同一行回复 `同意` 或 `不同意 + 理由`。
3. 双方同意后，A 在同一个 commit 内同时改：schema / 代码 / 本文件 / 相关测试。
4. 该 commit 的 hash 填入 §12。
5. **禁止**只改代码不改本文件，或只改本文件不改代码。

---

## 12. 冻结签字

| 接口 | A 确认 | B 确认 | 冻结 commit | 备注 |
|---|---|---|---|---|
| I1 CLI | ☑ | ☐ | eafb5cb（代码侧） | `--json` 单行、缺省 material_id 向 stderr 告警、`--resume` 断点重跑均有测试 |
| I2 运行目录 | ☑ | ☐ | eafb5cb（代码侧） | 布局与 recognize 产物已证；完整链路待 B01/B10 |
| I3 Schema | ☑ | ☐ | eafb5cb（代码侧） | 七份 schema 落盘（evidence 1.0.2）；待 B 确认字段语义 |
| I4 内容资产 | ☑ | ☐ | eafb5cb（代码侧） | 8 个提示词 + 1 个 Skill；`translate.md` 属 B 待交项 |
| I5 模型调用 | ☐ | ☐ | | A 侧未确认：run 内实例复用与 SDK 退避未按 I5 落地（G1-B05 另卡） |
| I6 校验与 code | ☑ | ☐ | 23c237b（代码侧） | issues 对象已落地；A 侧 2026-11-13 确认 AUTH_MISSING 不入枚举 |
| I7 状态与退出码 | ☑ | ☐ | 23c237b（代码侧） | A 侧 2026-11-13 确认 AUTH_MISSING→2、validate `valid=false`→4 |
| I8 Manifest | ☑ | ☐ | eafb5cb（代码侧） | assets/code/deps/calls/cache.key 已汇总；新 run 三组哈希可逐个复算 |
| I9 Stage 注册 | ☑ | ☐ | eafb5cb（代码侧） | `register_stage` 与注册表驱动执行已实现；B 可选使用 |
| I10 夹具契约 | ☑ | ☐ | eafb5cb（代码侧） | 缺失转 skipped 并由 verify 门禁标红；仍待 B 补齐 quality 夹具的 IR blocks |

**A 栏含义**：A 已在本方实现并对该接口的代码侧负责，依据是 `eafb5cb` 中的实现与测试；
勾选不等于双签，B 栏为空即接口尚未冻结。I5 的 A 栏保持空白，因为"run 内复用 Codex 实例 +
退避交给 SDK"两条尚未落地（G1-B05），不勾选比假勾选更可信。

评审时逐行过。**任何一行 B 栏未勾选，该接口不得视为冻结，B 的资产变更也不得以"已按冻结契约"申报。**

---

## 13. 三个必须当场决策的分歧点

这三处如果不当场定，第二天一定会返工：

**① `validation.json` 的结构变更**
现状是 `{"valid":bool,"errors":["字符串"]}`。字符串无法被 repair 精确消费，也无法被 B 的提示词逐条引用。建议升级为 I6 的 `issues` 对象数组并在 D1 冻结。代价：`validation.schema.json` 需重写，A9 的测试要同步。收益：repair 从"整体重写"变成"逐条定点修复"，这是 B 计划书里"修复只处理 validation.json 已报告问题"能否落地的唯一前提。

**② `additionalProperties` 的取值**
现状 4 个 schema 全是 `true`，意味着字段拼错不会被发现，模型多输出的字段会被静默接受。建议改 `false`。代价：B 写字段时必须完整。收益：`SCHEMA_INVALID` 才有真实约束力。

**③ 提示词缺失时是失败还是降级**
建议**失败**（`ASSET_MISSING`，退出码 8）。若选择降级到内置提示词，会出现"链跑通了但用的不是 B 审过的提示词"，这在比赛复核时无法解释。代价：B 未交付时 A 无法端到端自测，需用 `tests/fixtures/engineering/stub_prompts/` 替代。收益：产物可解释。
