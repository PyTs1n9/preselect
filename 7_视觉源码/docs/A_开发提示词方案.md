# A 号位开发提示词方案（三天冲刺）

> 配套文档：`docs/接口冻结清单_A-B.md`（编号 I1–I10）。
> 本方案中每张任务卡的【接口】栏直接引用该文档编号，避免口头约定。

---

## 0. 怎么用这份文档

| 步骤 | 做法 |
|---|---|
| 开一次 Codex 会话 | 先贴 §2 公共头，只贴一次 |
| 按顺序做任务 | 一次贴一张 §3 任务卡，A0 → A16，做完一张再贴下一张 |
| 会话变长或重置 | 贴 §4 的 U1 交接卡，拿到交接摘要后重贴公共头 |
| 每天收工 | 跑 §5 的当日闸门，未过闸门不得进入次日 |

三条不可让步的前提：

1. **一次只交付一个小目标。** 任务卡的粒度已经是上限，不要再合并两张卡。
2. **先方案后代码。** 每张卡都要求第 1 步只给文件清单与验收命令，确认后才动手。
3. **验收命令必须真实执行。** 贴出来的必须是终端原文，不接受"应该会通过"。

补一条：任务卡与仓库实际情况不符时，让 Agent 停下来报告差异，**不要让它自行修改需求**。这是 agent 编码最常见的失控点。

---

## 1. 基线事实（写方案前必须承认的四件事）

| 事实 | 影响 |
|---|---|
| **Codex 接入层完全缺失**：`CodexAdapter` 只有一个 `extract()` 抛 `NotImplementedError`，全仓库没有任何 `openai-codex` 的导入或调用 | 这是本项目的架构主干，不是"某个 adapter 没写"。A1/A7 必须先把它立起来，其余阶段才有模型可用 |
| `pipeline/` 下 8 个模块全部是 stub | 没有任何"现有链路"可复用，A0 只做观测不做补救 |
| `validate_document()` 只检查 4 个顶层键，`errors` 是 `list[str]` | I6 必须升级为结构化错误对象，且要在 D1 冻结，否则 B 的修复提示词无输入契约 |
| `schemas/*.json` 字段为空壳（`items: {"type":"object"}`） | 字段提案权在 B，A 只做落盘与加载，不能自己补字段 |

**架构主干（已核实，不得偏离）**：

```text
Python Orchestrator
  → openai-codex SDK          # pip install openai-codex，导入名 openai_codex
  → Codex app-server          # JSON-RPC v2 over stdio，SDK 自动拉起
  → Codex Agent
  → pdf-vision Skill          # SkillInput(name, path)，由 Codex 加载
  → 底层模型（Responses API）  # 经 model_providers.<id>.base_url + env_key 配置
```

关键推论三条：

1. **Python 侧不直接调模型 HTTP 接口。** 不引入 `openai` SDK，不自写 HTTP 客户端。
   模型调用只有一条路径：Codex SDK。
2. **PDF 必须先在 Python 侧转成 PNG。** SDK 的输入类型只有 `LocalImageInput` /
   `ImageInput`，没有 PDF 文件输入。所以 `decision.vision_path` 恒为 `image_png`。
3. **Codex 是 agentic 的。** 每次 `thread_start` 必须带 `sandbox=Sandbox.read_only`
   与 `ephemeral=True`，否则它会按自己的判断改文件，污染 run 目录。

**实测结论（2026-11-13，本机）**：`CodexAdapter.generate()` 已跑通，返回
`turn_status=completed`、`text="OK"`，usage 与 raw 落盘正常。原因是本机
`~/.codex/config.toml` 已配置自建网关（`model_providers.custom`，
`wire_api=responses`，`requires_openai_auth=false`，bearer token 直接写在配置里）。
两点推论：

- `codex login` **不是必需前置**。走自定义 gateway 时不需要 ChatGPT 登录态。
- 项目若要在 `.env` 里指定自己的网关，只需提供 `LLM_BASE_URL` + `LLM_API_KEY`；
  adapter 会用 `model_providers.llm` 覆盖，与用户的 `custom` provider 互不影响。
  两者都为空时才回退到全局 config.toml。
- 注意：bearer token 明文存在 Codex 全局配置里，属 Codex 自身机制。项目产物、
  manifest、raw 文件均不得出现该值（raw 已做 `redact`）。

`main.py` 目前只接受位置参数 `pdf`，没有 `doctor`、没有 `--stage`、没有退出码区分。A 的完成标准第 1 条就要求 `doctor --offline`，因此 A4 是硬前置。

---

## 2. 公共头（会话级，只贴一次）

```text
你是本项目的【系统工程执行者】，工号 A。你只对 source_code 下的工程链路负责。
你不负责判断文章的科学质量，不负责撰写业务提示词与 Skill 文本。

【项目事实】
- 流水线：PDF → 页面 PNG → Codex 视觉识别 → recognition.json → document_ir.json
  → evidence.json → draft.md → 确定性校验 → final.md
- 模型调用只有一条路径：openai-codex SDK → Codex app-server → Codex Agent
  → pdf-vision Skill → 底层模型。Python 不直连模型 HTTP 接口。
- 三层边界：原始识别层不得含写作结论；证据层是写作 Agent 的唯一事实来源；
  格式与长度由 Python 校验器裁决，不由模型宣布通过。
- 内容资产（skills/、prompts/、fixtures/quality/）归工号 B 所有。你只读取、
  只哈希、只校验存在性。任何情况下不写入、不改写、不"顺手补一个占位文件"。

【硬约束 · 全程有效】
1. 先读文件再动手。禁止猜测项目结构、函数名、字段名、目录名。
2. 只改任务卡白名单内的文件。白名单外一律视为越界，先报告再等指令。
3. 一次只做任务卡里的一个目标。发现别的问题写进"未覆盖风险"，不要顺手改。
4. 失败必须失败。禁止裸 except、禁止 return {} 兜底、禁止把异常转成空结果继续跑。
5. 校验结论只来自 Python 校验器。你不得输出"已通过校验"这类判断。
6. 密钥只从环境读取。禁止打印、禁止写日志、禁止进入异常栈、禁止落盘。
7. 新增依赖必须同时更新 requirements.txt 并说明理由。
8. 禁止为了让验收命令变绿而放宽断言、跳过用例、mock 掉被验证的对象。
9. 改动前后各跑一次任务卡验收命令，贴终端原始输出，不要复述。
10. 不得用 openai SDK、requests、httpx 或任何自写客户端替代 Codex SDK。
    需要调模型时只有一个入口：pipeline/codex_adapter.py 的 CodexAdapter。
11. Codex 是 agentic 的。每次 thread_start 必须 sandbox=Sandbox.read_only
    且 ephemeral=True；给它写权限会改坏工作区。

【每次回复的固定格式】
A. 拟修改文件清单（路径 + 该文件的责任边界，一句话）
B. 验收命令 + 预期输出特征
C. （等我确认后再实施）
D. 变更摘要：文件 / 新增或变更的函数签名 / 行为变化
E. 验收命令的真实输出
F. 失败原因（若有）与未覆盖风险

【第一轮只输出 A、B 两项，然后停下等我确认。】
```

---

## 3. 任务卡（按序执行）

### 第一天 · 基线、接口、最小骨架

#### A0 · 基线冻结（D1 09:00–10:00）

```text
【A0 基线冻结】
目标：确认仓库当前真实状态，产出一份只含观测事实的基线，不改任何实现代码。

读取：source_code/main.py、pipeline/ 下全部 .py、schemas/ 下全部 .json、
requirements.txt、skills/pdf-vision/SKILL.md、README.md、.env.example

要做：
1. 记录 python -V、pip -V、操作系统与 shell。
2. pip install -r requirements.txt，记录实际安装到的版本（不是文件里写的版本）。
3. 逐条执行并贴原始输出：
   python -c "import jsonschema,pypdf,dotenv;print('ok')"
   python main.py --help
   python main.py doctor --offline        # 预期不存在，记为缺失即可，不要实现
   python main.py <任意单页PDF>            # 无测试 PDF 则报告，并给出最小构造方案
4. 逐文件标注完成度：stub（NotImplementedError / 纯 return {} / 空函数体）、
   部分实现、可用。判定依据必须是代码行，不是注释。

产出：docs/baseline-engineering.md，只有观测事实，没有计划、没有建议。
禁止：修改任何 .py；安装 requirements 之外的包；把 stub 写成"已完成"。
验收：文档中每条结论都能对应一条命令和一段原始输出。
```

#### A1 · Codex 运行时与供应商能力探测（D1 10:00–11:30）· 接口 I5

```text
【A1 Codex 运行时与供应商能力探测】
目标：新建 provider_smoke.py，用官方 openai-codex SDK 探明两件事——Codex 运行时是否可用、
底层供应商支持什么。产出机器可读能力清单。不做 PDF 解析，不做业务逻辑。

【技术前提 · 已核实，不要另找方案】
- 包名 openai-codex，导入名 openai_codex，PyPI 稳定版 0.169.0
- SDK 通过 JSON-RPC 控制本地 codex app-server，不是 HTTP 直连模型
- 导出：Codex / AsyncCodex / CodexConfig / Sandbox / ApprovalMode /
  Thread / TurnHandle / TurnResult / TextInput / ImageInput / LocalImageInput / SkillInput
- Codex() 构造即启动 app-server 并完成 initialize，必须用 with 或显式 close()
- 供应商配置经 CodexConfig(config_overrides=("key=value", ...)) 透传 -c 键：
  model、model_provider、model_providers.<id>.base_url / env_key / wire_api
- env_key 只放环境变量名（LLM_API_KEY），密钥值由进程环境提供，不得写入 config.toml
- codex 0.167.0 未登录，第 2 项会失败，这是预期结果，如实记录不要绕过

读取：config.json、.env.example、pipeline/codex_adapter.py、pipeline/vision_provider.py

要做：写 source_code/provider_smoke.py，逐项探测，每项独立 try 并记录失败类型：
1. 运行时：`openai_codex.__version__` 与 SDK 自带的 pinned runtime 版本；
   PATH 上的 `codex --version` 仅作参考，不参与判定。
   SDK 发布版锁定自己的 runtime，与系统安装的 CLI 版本不一致属正常现象。
   记录 Codex() 能否启动并完成 initialize 握手。
2. 账号：codex.account() 的登录态；未登录记 AUTH_MISSING，禁止尝试登录。
3. 模型：codex.models() 列出可见模型；config.json 的 model 是否在其中。
4. 供应商：设 model_provider + base_url + env_key + wire_api 后跑一次最小 turn；
   wire_api 先试 responses，失败再试 chat，两级都记 ok 与 error_class。
5. 结构化输出：同一次 turn 传 output_schema，确认 final_response 能被 json.loads；
   失败记 SCHEMA_UNSUPPORTED。
6. 图片输入：LocalImageInput(path=...) 能否被接受。测试图用脚本内硬编码的最小 PNG
   字节写入临时文件，不得为此引入 Pillow 或其他图像库。
7. Skill 输入：SkillInput(name="pdf-vision", path="skills/pdf-vision/SKILL.md")
   能否被接受；文件不存在记 ASSET_MISSING。
8. 中断：thread.turn() 取 TurnHandle，到阈值后 interrupt()，
   记录 status 变为 interrupted 的耗时。
9. 截断可观测性：构造长输出，记录 TurnStatus 是否出现 interrupted、
   usage 是否可读、duration_ms 是否有值。

输出 provider_capabilities.json，字段固定：
{"probed_at",
 "sdk":{"version","pinned_runtime_version","path_codex_version"},
 "runtime":{"started","initialize_ok"},
 "auth":{"ok","account_class"},
 "provider":{"model_provider","base_url_host","wire_api","ok","error_class"},
 "models_visible":[],
 "structured_output":{"output_schema_ok","parsed_ok"},
 "vision":{"local_image","skill_input"},
 "interrupt":{"supported","latency_ms"},
 "truncation":{"status_observable","usage_observable"},
 "decision":{"vision_path":"image_png","skill_transport":"skill_input|prompt_text"}}

约束：
- decision.vision_path 恒为 image_png。SDK 的输入类型只有 LocalImageInput /
  ImageInput，没有 PDF 文件输入。不要臆造 pdf_input_file 分支。
- decision.skill_transport 由第 7 项结果推导：SkillInput 可用记 skill_input，
  否则记 prompt_text（退回由 Python 读 SKILL.md 拼进 TextInput）。
- 每项捕获具体异常后归类（CodexError / ServerBusyError / TransportClosedError /
  TimeoutError / FileNotFoundError），禁止一个 except Exception 吞掉全部。
- 任何输出不得含 key 片段；base_url 只留 host（用 urllib.parse 取 netloc）。
- 单项超时 60s（app-server 首启动慢），全流程上限 300s。
- 不得写入 prompts/，不得创建占位资产。

验收：
python provider_smoke.py ; echo "exit=$?"
python -c "import json;d=json.load(open('provider_capabilities.json',encoding='utf-8'));print(d['decision'],d['runtime']['started'])"

失败分支：
- SDK 导入失败 → 退出码 2，打印 ImportError 原文，不得降级为 HTTP 客户端
- 未登录 → 退出码 8，只打印 AUTH_MISSING，不打印账号细节
- app-server 起不来 → 退出码 5，打印 stderr 末 20 行（脱敏后）
```

#### A2 · 配置入口（D1 11:30–13:00）· 接口 I1、I5

```text
【A2 配置入口】
目标：建立唯一配置读取入口，支持优先级与脱敏日志，消除各模块自行读 env 的写法。

读取：config.json、.env.example、pipeline/ 下现有读配置的位置

要做：新增 source_code/pipeline/settings.py，提供 load_settings() -> Settings
（frozen dataclass）。优先级固定为：
CLI 参数 > 环境变量 > .env > config.json > 内置默认值
每个生效值必须能回答"它从哪一层来的"，用于 doctor 输出。

字段至少覆盖：vision.provider / vision.model / vision.base_url / vision.wire_api /
vision.timeout_s / vision.max_retries / vision.dpi / vision.effort /
pipeline.max_repair_attempts / pipeline.keep_intermediate / paths.result_dir /
paths.prompts_dir / paths.skills_dir / paths.fixtures_dir

注意：没有 vision.temperature。Codex 不暴露采样温度，写进配置即为伪造参数。
provider 取 openai_codex，wire_api 取 responses|chat。

约束：
- 密钥字段类型为 SecretStr 级别封装，__repr__ 与异常信息中一律输出 ***。
- 密钥变量名是 LLM_API_KEY，不是 OPENAI_API_KEY。该值经 Codex 的
  model_providers.<id>.env_key 间接读取，A 侧不直接发起 HTTP 请求。
- 缺少必需密钥时抛 ConfigError（自定义异常），不要 KeyError、不要静默用空串。
- config.json 未知键不报错但要汇总到 doctor 的 warnings，便于发现拼写错误。
- 禁止在任何模块内再出现 os.getenv 读取模型或路径。

验收：
python main.py doctor --offline
必须打印每个生效配置项及其来源层级，且 LLM_API_KEY 显示为 ***。
补充验收：构造一次缺 Key 的调用，贴出 ConfigError 的完整信息，确认无 key 泄漏。
```

#### A3 · Schema 加载器与错误码（D1 14:00–15:30）· 接口 I3、I6

```text
【A3 Schema 加载器与错误码】
目标：建立 Schema 加载与校验的统一入口，把校验结果升级为结构化错误对象。
注意：字段内容由 B 提供，你只实现加载、校验、错误归一化，不新增业务字段。

读取：schemas/ 下 4 个 .json、pipeline/validate.py、docs/接口冻结清单_A-B.md

要做：
1. 新增 pipeline/schema_loader.py：
   load_schema(name) -> dict            # 缺失即抛 SchemaNotFound，不允许回退空 schema
   validate_instance(name, obj) -> list[Issue]
2. 新增 pipeline/errors.py，定义 Issue 为不可变对象，序列化后形如：
   {"code","stage","path","severity","message","hint"}
   code 使用 I6 表中的枚举值；path 用 JSON Pointer 风格（如 /blocks/3/page_number）。
3. schema 文件缺失、JSON 语法错误、$schema 版本不认识，三种情况分别给不同 code。
4. 记录每个 schema 文件的 sha256，供 manifest 使用。

约束：
- 只做"加载 + 校验 + 归一化"，不改 schemas/ 下的字段定义。发现字段不足，
  写入未覆盖风险并指出需要 B 补哪一项，不要自己加。
- 校验必须 fail closed：instance 为 None 也走校验并产出 issue，不许短路返回空列表。

验收：
python -c "from pipeline.schema_loader import load_schema;print(sorted(load_schema('evidence')['properties']))"
python -c "from pipeline.schema_loader import validate_instance;print([i.code for i in validate_instance('evidence',{})])"
两条都必须给出具体输出；第二条不得为空列表（空对象必然缺必需字段）。
```

#### A4 · 主流程骨架与 doctor（D1 15:30–17:00）· 接口 I1、I2、I7、I9

```text
【A4 主流程骨架与 doctor】
目标：建立可执行的空流程：阶段状态机、运行目录、退出码、doctor 子命令。
不允许把任何阶段业务逻辑写进 main.py。

读取：main.py、pipeline/orchestrator.py、pipeline/manifest.py、
docs/接口冻结清单_A-B.md 的 I1/I2/I7/I9

要做：
1. 重构 main.py 为子命令式 CLI（argparse subparsers），只做参数解析与退出码映射，
   单文件不超过 120 行：
   run <pdf> [--config] [--output] [--material-id] [--stage ...]
   doctor [--offline]
   validate --run-id <id>
   package --materials ... --out <zip>
2. pipeline/orchestrator.py 实现：
   Stage 枚举（recognize/normalize/evidence/write/validate/package）
   RunContext（run_id、run_dir、settings、artifact 路径表）
   阶段状态机 pending→running→ok|failed|skipped，每阶段耗时与产物路径入 manifest
   阶段注册表 register_stage(name, fn)（I9 扩展点，B 后续可挂 quality_gate）
3. 运行目录严格按 I2 布局创建，空目录也要建，便于 B 定位。
4. 统一异常 → 退出码映射，按 I7 表执行；未捕获异常退出码 1 并打印 traceback 到
   日志文件（不含密钥），终端只打印摘要。
5. doctor --offline：不发起任何网络请求，检查 python 版本、依赖版本、目录可写、
   schema 可加载、内容资产存在性与哈希、配置来源。

约束：
- main.py 内不得出现业务判断，只允许参数解析、调用 orchestrator、打印、返回码。
- 阶段失败必须使退出码非 0，禁止"继续跑完再说"。
- 缺资产（prompts/ 或 skills/）在 doctor --offline 中报 warning 不报 error，
  因为 B 尚未交付；但在 run 中必须报 error 并失败。

验收：
python main.py doctor --offline ; echo "exit=$?"
python main.py run <单页PDF> --stage recognize ; echo "exit=$?"
第二条预期失败且退出码非 0（阶段未实现），这是正确行为，贴出实际值。
```

#### A5 · 接口冻结（D1 17:00–18:00）· 非 Agent 任务

这一步是人做的，不给 Codex 写代码。做法：

1. 打开 `docs/接口冻结清单_A-B.md`，与 B 逐行过 I1–I10。
2. 双方对每一行给出"同意 / 需改"，需改的当场写进文档，不留在聊天记录里。
3. 冻结后在文档末尾填写 commit hash 与两人确认。
4. **任何字段口头约定但未写入 Schema 或冻结清单，视为未完成。**

Codex 在这一步只做一件事：按最终冻结内容回填 `schemas/` 中的字段骨架（B 提供的片段原样落盘），不要发挥。

---

### 第二天 · 视觉接入、编排、确定性校验

#### A6 · PDF 输入与页面化（D2 09:00–11:00）· 接口 I2

```text
【A6 PDF 输入与页面化】
目标：把 PDF 变成可审计的页面清单与 PNG 序列，覆盖异常输入。

读取：pipeline/pdf_input.py、docs/baseline-engineering.md、provider_capabilities.json

要做：
1. 重写 load_pdf() 返回：
   {"sha256","page_count","file_size","encrypted","pages":[
     {"page_number","width_pt","height_pt","rotation","has_text_layer","render_path"}]}
2. **渲染是必做步骤，不是分支。** Codex SDK 只能接收 LocalImageInput / ImageInput，
   没有 PDF 文件输入，所以每一页都必须渲染成 PNG。
   DPI 从配置读，默认 200；输出到 run_dir/input/page_%04d.png。
   不要写"若 vision_path == image_png 才渲染"这类条件分支——该值恒为 image_png，
   分支只会掩盖漏渲染的 bug。
3. 明确状态返回（不得抛裸异常）：加密 PDF、0 页、单页超 20MB、总页数超 50、
   渲染失败（附页号）。每种状态对应 I7 退出码。
4. sha256 对文件字节计算，流式读取，不整文件入内存。
5. 渲染库用 pypdfium2。不得换成 PyMuPDF（AGPL）或 pdf2image（需外部 poppler）。

约束：
- 不引入 OCR 依赖；has_text_layer 只判断是否存在可抽取文本，不做识别。
- 页号一律 1-based，与 B 的 Schema 保持一致，不得出现 0-based。
- 渲染失败不得静默跳页，必须整次失败并列明页号。

验收：
python -c "from pipeline.pdf_input import load_pdf;from pathlib import Path;d=load_pdf(Path(r'<PDF>'));print(d['page_count'],d['sha256'][:12],len(d['pages']))"
再各构造一个加密 PDF 与一个 0 页 PDF，贴出两条明确的状态返回。
```

#### A7 · Codex 接入层（D2 11:00–13:00）· 接口 I5

```text
【A7 Codex 接入层】
目标：把 Codex 调用收敛到一个 Adapter。它只做"把一段文本 + 若干图片 + 一个 skill 引用
交给 Codex，拿回最终文本或结构化对象"，并负责超时、中断、重试、截断检测、原始响应落盘。
Adapter 不认识业务字段，不写提示词，不解析 PDF。

【分层，不得混层】
Python Orchestrator
  → openai-codex SDK（JSON-RPC over stdio）
  → Codex app-server / Codex Agent
  → pdf-vision Skill
  → 底层模型（Responses API）
- Python 不直接调用模型 HTTP 接口，不引入 openai SDK
- Codex Agent 不负责 PDF 转页面，那是 A6
- Adapter 不读 prompts/ 内容，提示词文本由调用方按 I4 传入

读取：pipeline/codex_adapter.py、pipeline/vision_provider.py、pipeline/settings.py、
provider_capabilities.json、docs/接口冻结清单_A-B.md 的 I5

要做：
1. 复用 contracts.py 的 ModelRequest，字段：
   stage、prompt_name、prompt_text、payload、json_schema（可选）、
   images（本地 PNG 路径列表，可选）、skill（name + path，可选）、timeout_s、model
2. ModelResult 字段（与 I5 冻结一致）：
   text、parsed、raw、usage、truncated、latency_ms、attempts、request_hash、
   model、turn_status、error_message
   turn_status 取 TurnStatus：completed / interrupted / failed / in_progress
3. 实现 CodexAdapter.generate(request) -> ModelResult：
   - 输入序列一次组装：[SkillInput(name, path)] + [LocalImageInput(path)] +
     TextInput(prompt_text)，交给同一次 thread.run()
   - Codex 实例在 run 级别复用，一个 run 只启一个 app-server；
     禁止每次调用 new Codex()，否则每个阶段都要重启进程
   - thread_start(sandbox=Sandbox.read_only, ephemeral=True, model=..., model_provider=...)
     read_only 是硬要求：Codex 是 agentic 的，不给写权才不会污染 run 目录与仓库
   - 结构化输出用 run(output_schema=request.json_schema)，不要靠提示词催 JSON
   - 超时：thread.turn() 取 TurnHandle，按 timeout_s 等待，超时调 interrupt()，
     再取 result；turn_status=interrupted 时置 truncated=True 并抛 TruncatedResponse
   - turn_status=failed 且 error 非空 → 按 error.message 归类为 ModelRefused 或
     InvalidModelOutput，不得吞成空结果
   - 重试：只对 is_retryable_error(exc) 为真的异常重试（ServerBusyError /
     RetryLimitExceededError / TransportClosedError），次数取 settings.vision.max_retries；
     CodexError 中参数类错误立即失败，不消耗重试次数
   - 原始响应：TurnResult.items 逐项 model_dump 后写 run_dir/raw/<stage>_<attempt>.json，
     附 usage、duration_ms、turn_status；文件内不得出现 key 片段
4. 保留 VisionProvider Protocol，get_provider(name) 分发：
   openai_codex → CodexAdapter；未知 provider 抛 ConfigError，不得静默回退

约束：
- Adapter 内不得出现 pages/blocks/evidence 等业务字段名
- 不得读 prompts/、skills/ 内容；skill 只把路径交给 Codex，由 Codex 自己加载
- 日志与异常不得含 LLM_API_KEY；SDK stderr 只保留末 20 行，且先做脱敏
- 本项目模型调用只有一条路径：Codex SDK。不新增 openai 依赖，不自写 HTTP 客户端
- 不得把 Codex 当 PDF 解析器：输入只有 PNG，PDF → PNG 在 A6

验收：
python -c "import openai_codex,inspect;print(openai_codex.__version__);from openai_codex import Codex;print(inspect.signature(Codex.thread_start))"
一次真实最小调用（需已 codex login）：贴 ModelResult 的 latency_ms / attempts /
truncated / turn_status / usage
再构造一次超时（timeout_s=0.001）：贴异常类型、interrupt 是否生效、exit_code_for 结果
再构造一次未登录（CODEX_HOME 指向空目录）：贴 AUTH_MISSING 的归类结果
```

#### A8 · 三阶段编排（D2 14:00–15:30）· 接口 I2、I3、I4、I6

```text
【A8 三阶段编排】
目标：串联 recognize → normalize → evidence，每阶段独立落盘、独立校验，
上一阶段产物不合法时禁止进入下一阶段。

读取：pipeline/orchestrator.py、pipeline/normalize.py、pipeline/evidence.py、
pipeline/schema_loader.py、docs/接口冻结清单_A-B.md 的 I2/I3/I4/I6

要做：
1. 新增 pipeline/prompt_loader.py（I4）：
   load_prompt(name, base_dir) -> {"text","sha256","path","version"}
   版本取文件首行 HTML 注释 <!-- version: x.y.z -->，缺版本号则 warning。
   文件缺失抛 AssetMissing（code=ASSET_MISSING），不回退内置默认提示词。
   load_skill(path) -> {"text","sha256","path"}
   text 只用于校验与哈希；传给 Codex 的是 SkillInput(name, path)，正文不进 prompt。
   两者投递方式不同，不得混用。
2. recognize 阶段：构造 ModelRequest
   - images = run_dir/input/ 下全部 page_%04d.png（本地 PNG 路径）
   - skill = ("pdf-vision", skills/pdf-vision/SKILL.md)，由 Codex 加载，正文不进 prompt
   - prompt_text = prompt_loader("vision").text
   - json_schema = recognition schema
   一次 thread.run() → ModelResult → recognition.json（原样落盘，不改结构）
   → 用 recognition schema 校验。缺少 PNG 或 skill 文件时抛对应 ASSET_/PDF_ 错误，
   不得退回"只用文本描述页面"的降级路径。
3. normalize 阶段：读 recognition.json + prompt_loader("normalize") → 请求结构化输出
   → document_ir.json → 用 document_ir schema 校验。
4. evidence 阶段：读 document_ir.json + prompt_loader("evidence") → evidence.json
   → 用 evidence schema 校验。
5. 每阶段结束写 stage_status 到 manifest：{name,status,started_at,ended_at,
   artifacts[],issues[],model_calls}。
6. 任一阶段校验失败：写 validation.json（I6 结构），标记 failed，不进入下一阶段。

约束：
- 三个阶段的 run 函数签名一致：(ctx, inputs) -> outputs，便于 I9 注册表挂载。
- 阶段之间只通过磁盘文件传递，不通过内存对象传递。这是可断点重跑的前提。
- 禁止跨阶段复用模型返回的原始文本缓冲。
- 提示词文本每阶段读一次并记录 sha256，不得缓存跨阶段。

验收：
python main.py run <单页PDF> --stage recognize ; echo "exit=$?"
python main.py run <单页PDF> --stage normalize ; echo "exit=$?"
ls result/runs/<run_id>/
预期能看到 recognition.json、document_ir.json、evidence.json 与各自 issue 记录；
若 B 的 prompts/ 尚未交付，你必须在 AssetMissing 处失败并贴出该错误，不许绕过。
```

#### A9 · 确定性校验器（D2 15:30–17:00）· 接口 I6

```text
【A9 确定性校验器】
目标：把"是否合格"从模型手里拿走。所有结论来自可复算的 Python 检查。

读取：pipeline/validate.py、schemas/validation.schema.json、
docs/接口冻结清单_A-B.md 的 I6、skills/pdf-vision/SKILL.md

要做：重写 validate.py，实现以下检查，每项产出 Issue（code 见 I6）：
1. JSON 结构：三次 schema 校验（recognition / document_ir / evidence）。
2. Markdown 结构：final.md 或 draft.md 的章节存在性（标题/引言/正文/总结/来源）。
3. 正文长度：中文字符计数口径固定为"去除 Markdown 标记与来源块后的汉字数"，
   阈值从 config 读，默认 2500；同时给出上限（默认 4000）防止失控。
4. 编码：文件必须 UTF-8 可解码，且不含 BOM、不含 \r\n 混用。
5. 来源块：文中每个 [n] 引用必须能在 evidence.json 找到对应条目；
   evidence 中每个 core 事实必须在正文至少被引用一次。
6. 数字一致性：正文中出现的数值若带单位，必须能在 evidence 中找到同值同单位项。
7. 敏感信息：正文与日志中不得出现 sk- 前缀、API key 形态、
   本机绝对路径（盘符或 /Users/、/home/）。
8. 串稿检测：material_id 必须在 document_ir 与正文元数据中一致，
   且正文不得包含其他材料的 material_id 或标题。

输出 validation.json：
{"valid":bool,"schema_version","checked_at","stats":{...},"issues":[Issue...]}
stats 至少含：汉字数、引用数、证据条数、未引用证据条数。

约束：
- 每条检查必须有单元测试，位于 tests/test_engineering_validate.py。
- 检查函数不得调用模型，不得读取网络。纯函数，输入路径输出 Issue 列表。
- 任一 issue 的 severity 为 error 时 valid 必须为 false，不得靠 warning 蒙过去。

验收：
python -m pytest tests/test_engineering_validate.py -q
python main.py validate --run-id <run_id> ; echo "exit=$?"
再手动制造三种坏输入（缺章节、字数不足、伪造引用），各贴一次 issue code。
```

#### A10 · 单页端到端（D2 17:00–18:00）

```text
【A10 单页端到端】
目标：用测试 PDF 跑通完整链路，留下可复核的运行目录。这一步原则上只允许
"发现并修复集成问题"，不允许新增功能。

读取：result/runs/ 下最近一次运行、docs/接口冻结清单_A-B.md

要做：
1. python main.py run <单页PDF>，完整跑 recognize → normalize → evidence → validate。
2. 核对 result/runs/<run_id>/ 是否严格符合 I2 布局，缺哪个文件列出来。
3. 核对 manifest.json 是否记录了：input sha256、schema 哈希、prompt 哈希、
   skill 哈希、model、调用次数、各阶段耗时。
4. 把本次运行的三个 JSON 与 validation.json 的 issues 摘要写进
   docs/e2e-run-1.md，附命令与退出码。
5. 任何集成问题：先分类（接口不符 / 缺资产 / 模型行为 / 自身 bug），只修自身 bug。
   接口不符与缺资产一律报告给 A/B 对接人，不自行改契约。

禁止：为了让链路"跑通"而降低 schema 严格度、跳过阶段、mock 模型返回。
验收：docs/e2e-run-1.md 里的每条结论都有对应文件路径与命令输出。
```

---

### 第三天 · 写作闭环、四材料、发布

#### A11 · 写作与有界修复（D3 09:00–10:30）· 接口 I4、I6

```text
【A11 写作与有界修复】
目标：接入 B 的 writer 提示词生成 draft.md，并按 validation.json 做有界修复。

读取：pipeline/writer.py、pipeline/repair.py、pipeline/prompt_loader.py、
docs/接口冻结清单_A-B.md 的 I4/I6

要做：
1. writer 阶段：唯一输入是 evidence.json（+ prompt_loader("writer")）。
   禁止把 PDF 文本或 recognition.json 传入写作请求。这一条要在代码里体现：
   构造 payload 的函数不得持有 pdf 路径或 recognition 对象。
2. repair 阶段：输入 draft.md + validation.json，只修 issues 中列出的 code，
   最多 settings.pipeline.max_repair_attempts 次（默认 2）。
   每次修复落盘 raw/repair_<n>_request.json 与 repair_<n>_diff.md。
3. 修复后重新跑 validate；仍不通过则退出码按 I7，禁止无限循环、禁止放宽检查。
4. 输出 final.md（修复成功）或保留 draft.md 并标记失败，两者不并存。
5. 修复模型必须收到"只改这些 code，其余不得改动"的明确指令约束。
   若 B 提供了 prompts/repair.md 则使用之；未提供时按 AssetMissing 失败。

约束：
- repair 循环上限硬编码从配置读，禁止写死 2 之外的魔数。
- 每轮修复都必须留 diff，禁止只留最终稿。
- 修复不得新增事实：若 diff 中出现 evidence 中不存在的数值或实体，直接判失败。

验收：
python main.py run <单页PDF> --stage write ; echo "exit=$?"
ls result/runs/<run_id>/raw/
构造一次必然失败的 validate（把阈值调高），确认修复 2 次后停止且退出码正确。
```

#### A12 · Manifest 与缓存（D3 10:30–12:00）· 接口 I8

```text
【A12 Manifest 与缓存】
目标：让"这次运行到底用了什么"可以逐项复算，并建立不骗人的缓存。

读取：pipeline/manifest.py、provider_capabilities.json、docs/接口冻结清单_A-B.md 的 I8

要做：
1. manifest.json 记录：run_id、started_at、ended_at、python 版本、平台、
   input{path,sha256,page_count}、code{git_commit 或 pipeline/*.py 逐个 sha256}、
   assets{prompts{name:sha256}, skills{path:sha256}, schemas{name:sha256}}、
   config{生效值 + 每项来源层级}、model{provider,model,temperature,max_retries}、
   calls{stage:次数}、stages[{name,status,duration_ms}]、deps{pip freeze 关键包}、
   cache{enabled,key,hit}。
2. 缓存键 = sha256(input_sha256 + stage + prompt_sha256 + schema_sha256 +
   model + 关键参数)。命中时复用产物但在 manifest 标 cache.hit=true。
3. 缓存目录 result/cache/<key>/，只在 keep_intermediate 为真时启用。

约束：
- 禁止在 manifest 中写入密钥、完整 base_url、用户目录绝对路径。
- 禁止声称"逐字可复现"。temperature>0 时必须在 manifest 写
  reproducibility: "not-bitwise"。
- 缓存键遗漏任一维度视为 bug，需在测试中覆盖（改 model 名必然 miss）。

验收：
python -m pytest tests/test_engineering_manifest.py -q
连跑两次同一输入，贴出两次 manifest 的 cache.hit 与 key；
再改 config 中 temperature 跑第三次，确认 key 变化、cache.hit=false。
```

#### A13 · 四份比赛材料（D3 13:00–15:00）

```text
【A13 四份比赛材料】
目标：按材料编号独立跑四份，互不污染，并给出汇总核对表。

读取：比赛材料目录、config.json、docs/接口冻结清单_A-B.md 的 I2/I7

要做：
1. CLI 支持 --material-id，运行目录为 result/runs/<material_id>/<run_id>/，
   禁止四个材料共享任何可变状态（模块级全局、单例、临时文件同名）。
2. 逐份运行，每份独立 manifest。任一份失败不影响其余三份的产物完整性判断。
3. 产出 docs/four-material-run.md：
   material_id | 页数 | 汉字数 | issues(error/warning) | 退出码 | run_id
4. 显式校验四份不串稿：material_id 与标题、正文关键实体不得跨材料出现。
   检测到重复或缺失立即停止并报告，不要继续跑完。

禁止：为凑齐四份而复用上一次的 draft.md 或 final.md；禁止跨材料复制临时文件。
验收：docs/four-material-run.md 四行齐全，每行有可访问的 run_id 路径。
任一材料失败时，报告具体阶段与 issue code，不许汇总成"部分成功"。
```

#### A14 · 故障注入（D3 15:00–16:00）

```text
【A14 故障注入】
目标：证明异常不会被静默吞掉。每个用例必须有确定的失败表现。

读取：tests/ 现有测试、pipeline/ 全部、docs/接口冻结清单_A-B.md 的 I6/I7

要做：在 tests/test_engineering_faults.py 中实现六个用例，全部用可离线复现的
假实现（monkeypatch），不依赖真实网络：
1. 模型超时 → TruncatedResponse 或 TimeoutError，退出码按 I7，不得返回空 JSON。
2. 坏 JSON（截断字符串）→ InvalidModelOutput，附原文片段，raw 文件已落盘。
3. 假引用（正文 [9] 但 evidence 只有 3 条）→ issue code 为 I6 中引用类 code。
4. 输入变化（同 run_id 换 PDF）→ manifest 的 input.sha256 不一致，必须报错。
5. 写作超长（汉字数 4200）→ 超限 issue，repair 触发但不得删核心事实。
6. 落盘失败（result 目录只读）→ 明确 IO 错误与退出码，不得静默 continue。

约束：
- 每个用例断言"失败方式"，不只断言"失败了"。必须断言 code 与退出码。
- 禁止用 try/except 包住断言来让测试通过。

验收：python -m pytest tests/test_engineering_faults.py -q，六条全绿，
并在 docs/fault-injection.md 记录每条用例 → 期望 code → 实际 code。
```

#### A15 · 打包与读回（D3 16:00–17:00）

```text
【A15 打包与读回】
目标：生成符合比赛要求的 ZIP，并用读回比较证明内容一致。

读取：比赛提交要求、pipeline/、.env.example、result/runs/

要做：
1. main.py package --materials M1,M2,M3,M4 --out submission.zip
2. 打包前先跑一次 validate，任一材料 valid=false 则拒绝打包（退出码按 I7）。
3. 排除清单硬编码并集中在一处：.env、*.key、源 PDF、result/、cache/、
   __pycache__、.git、历史报告（docs/ 下非最终报告）、provider_capabilities.json。
4. 打包后立即读回：逐条目比较 CRC 与字节数，并核对文件清单与预期清单完全相等
   （多一个少一个都算失败）。
5. 输出 package_report.json：{entries:[{name,size,crc32}],excluded:[...],
   verified:bool,built_at}。

约束：
- 禁止用 zip 命令行工具，用标准库以便逐条核验。
- 读回比较必须是字节级或 CRC 级，不接受"能打开就算通过"。
- ZIP 内出现 .env、key、源 PDF 时直接失败，不做"警告后继续"。

验收：
python main.py package --materials ... --out submission.zip ; echo "exit=$?"
python -c "import zipfile;z=zipfile.ZipFile('submission.zip');print(len(z.namelist()));print(z.testzip())"
testzip 必须返回 None；再手动把 .env 塞进去，确认打包直接失败。
```

#### A16 · 干净环境演示（D3 17:00–18:00）

```text
【A16 干净环境演示】
目标：证明换一台机器、换一个 key 也能跑起来。这一步不允许改代码，
只允许改文档中写错的命令。

要做：
1. 新建虚拟环境（不继承当前 site-packages），python -m venv .venv-demo。
2. 只按 requirements-lock.txt 安装，记录安装耗时与失败项。
3. 用新 key 执行 python main.py doctor --offline 与一份材料的完整运行。
4. 按 README 顺序逐条执行，凡命令与文档不一致，记录为文档缺陷，改文档不改代码。
5. 产出 docs/clean-env-demo.md：环境、命令序列、退出码、耗时、遇到的问题与解决。

禁止：从当前环境复制 site-packages；复用旧 key；跳过 doctor 直接跑材料。
验收：docs/clean-env-demo.md 中的命令可以被第三个人按顺序复现，无隐含前置步骤。
```

---

## 4. 救场卡

### U1 · 会话压缩与交接

```text
【U1 生成交接摘要】
当前会话上下文已长。请生成一份交接摘要，供新会话继续，不要写新代码。
必须包含：
1. 已完成的文件与其当前真实行为（逐文件一行）。
2. 已冻结的接口编号及对应实现位置（I1–I10 中哪些已落地、哪些还是空）。
3. 未解决的风险与已知缺陷（按是否阻塞下一步排序）。
4. 下一步应执行的任务卡编号，以及它依赖的前置条件是否满足。
5. 任何"看起来完成但其实是 stub"的位置，必须点名。
输出为 Markdown，不超过 80 行。
```

### U2 · 改动自审

```text
【U2 自审 diff】
请只做审查，不修改代码。输出 git diff 全文并逐条自问：
1. 是否越界改了白名单外的文件？
2. 是否有裸 except、空 except、异常被转成默认值？
3. 是否有密钥进入日志、异常信息、返回值、manifest？
4. 是否有把模型输出当作校验结论的地方？
5. 是否新增了未写入 requirements 的依赖？
6. 是否有测试被放宽、跳过、mock 掉被验证对象？
7. 是否引入了跨阶段的内存状态（破坏断点重跑）？
每项给"是/否 + 证据行号"。发现"是"时给出最小修正方案，但先不要改。
```

### U3 · 故障定位

```text
【U3 定位失败】
现象：<粘贴命令与完整输出>
请按此顺序定位，每一步都要贴证据，禁止猜测：
1. 读失败阶段对应的 run 目录，列出实际存在的文件与预期清单的差集。
2. 读 manifest.json，确认该阶段 status、duration、issues、calls。
3. 读 raw/ 下最近一次响应，判断是模型行为还是解析问题。
4. 给出最可能的三个原因，按可能性排序，每个附可验证的判据。
5. 只对可能性最高的一个给出最小修复，改完立即跑该阶段的验收命令。
若证据不足以下结论，直接说"证据不足，需要补 X"，不要编原因。
```

### U4 · 给 B 的复现说明

```text
【U4 生成 B 侧复现说明】
目标：让工号 B 在不问你任何问题的情况下复现一次完整运行。
基于当前仓库真实状态撰写 docs/reproduce-for-B.md，包含：
1. 前置条件：python 版本、依赖安装命令、必需环境变量名（不含值）。
2. 需要 B 提供才能跑通的内容资产清单（精确到路径与最小可用内容要求）。
3. 逐条命令与每条的预期输出特征、预期退出码。
4. 常见失败与对应处置（AssetMissing、SchemaNotFound、TruncatedResponse、
   InvalidModelOutput、ConfigError），每条给"看到什么 → 怎么办"。
5. 结果核对清单：run 目录应有哪些文件、validation.json 应有哪些字段。
禁止写入任何真实密钥或 base_url。不允许出现"问 A 即可"这类步骤。
```

---

## 5. 每日闸门

| 日 | 准出条件（不满足不得进入次日） |
|---|---|
| D1 | `doctor --offline` 可运行并显示配置来源；I1–I10 冻结清单有 commit 与双人确认；`provider_capabilities.json` 中 `runtime.started`、`decision.skill_transport` 为确定值，且 `auth.ok` 如实反映当前登录态（未登录必须为 false，不得填 true） |
| D2 | `py -c` 三条命令都能产出真实 JSON；`result/runs/<run_id>/` 严格符合 I2 布局；`tests/test_engineering_validate.py` 全绿；A10 的 e2e 文档存在 |
| D3 | 四份 `final.md` 各有独立 run_id；六个故障用例全绿；`submission.zip` 读回 `testzip() is None` 且无敏感文件；干净环境演示文档可被第三人复现 |

对应 A 的完成标准逐条对齐：

| 完成标准 | 由哪张卡保证 |
|---|---|
| `python main.py doctor --offline` 可运行 | A4 |
| 单页 PDF 生成合法 `recognition.json` | A6 + A7 + A8 |
| 四阶段状态可追踪、失败有退出码 | A4 + A8 + I7 |
| `validation.json` 能指出具体错误 | A3 + A9 |
| 能生成并读回最终 ZIP | A15 |
| 日志不泄露密钥 | A2 + A7 + A15（三处独立把关） |
| B 能不看口头解释复现一次运行 | U4 + A16 |

---

## 6. 明确不做的事

以下内容属于 B 或属于人工判断，A 的提示词全程不得触碰，出现即为越界：

1. 不写 `skills/pdf-vision/SKILL.md`、不写 `prompts/` 下任何文件。
2. 不判断文章科学质量是否达标、不评价科普表达好坏。
3. 不定义 `schemas/` 的业务字段（只做落盘，字段内容由 B 提供）。
4. 不用更宽松的校验规则去掩盖识别失败。
5. 不为了把四篇压到 2500 字而删除核心数据、过程或限制。
6. 不在提示词里承诺"模型绝对准确"或"完全可复现"。
7. 不把模型调用改写成 HTTP 客户端。Codex SDK 是架构主干，绕开它去调
   `/v1/responses` 会让 skill 层、沙箱、会话审计全部失效。
8. 不把 SKILL.md 正文拼进 prompt 文本。skill 以 `(name, path)` 经 `SkillInput` 交给
   Codex，由 Codex 自己加载；A 侧读取只用于计算 sha256 与存在性校验，不参与拼装。
