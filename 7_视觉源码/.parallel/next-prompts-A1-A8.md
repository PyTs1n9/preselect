# 下一步开发提示词（A1 → A8）

> 本文件是可直接投喂的工作指令，不是方案说明。投喂单位仍然是**一张卡**。
>
> 用法：
> 1. 开一个 Codex 会话，贴 §1 公共头，只贴一次。
> 2. 贴 §2（A1 卡）。第一轮它只会回 A、B 两项，确认后再让它实施。
> 3. A1 验收通过、写完 `.parallel/handoffs/A1.md` 后，**再**贴 §3（A8 卡）。
> 4. 不要把 §2 和 §3 一起贴——两卡一起贴必然被合并执行。

---

## 0 当前仓库事实（2026-11-13 22:40 核验，不要再假设）

| 项 | 事实 | 核验命令 |
|---|---|---|
| SDK | `openai_codex` 0.169.0，`Codex()` 本机启动约 3.0 s | `python -c "import openai_codex;print(openai_codex.__version__)"` |
| 账号 | `Codex().account()` 返回对象，`requires_openai_auth=False`（走全局 config.toml 自建网关） | `python -c "import openai_codex;c=openai_codex.Codex();print(c.account());c.close()"` |
| 模型 | `Codex().models()` 返回 `ModelListResponse(data=[Model...])`；`config.json` 的 `vision.model` 为空串 | 同上，把 `account()` 换成 `models()` |
| 契约 | `AUTH_MISSING` 是 `AuthMissing`，退出码 2，**不在** 30 条 `Code` 枚举内 | `python -c "from pipeline.errors import AuthMissing,exit_code_for;print(exit_code_for(AuthMissing()))"` |
| CLI | `validate` 在 `valid=false` 时退出码 4；`run` 已按 I2 建 `input/ raw/ logs/` 并把源 PDF 放进 `ctx.artifacts["source_pdf"]` | `python main.py --help` |
| PDF | `load_pdf(path, run_dir=...)`，渲染目标必须显式传 run 目录；已删除 `PIPELINE_RUN_DIR` | `python -c "import inspect;from pipeline.pdf_input import load_pdf;print(inspect.signature(load_pdf))"` |
| Skill | `skills/pdf-vision/SKILL.md` 存在 | `ls skills/pdf-vision/SKILL.md` |
| 提示词 | `prompts/` **不存在**（B 未交付），这是事实，不得创建 | `ls prompts` |
| 阶段 | `pipeline/stages/` 只有自动发现，没有任何阶段模块 | `ls pipeline/stages` |
| 编排 | `--stage all` 按 `requires` 拓扑排序（不再按模块名字母序）；空注册表退出非 0 | `python -m pytest tests/test_engineering_orchestrator.py -q` |
| 测试 | `python -m pytest -q` = 86 passed（`pytest.ini` 已禁用无关的 hydra 插件） | `python -m pytest -q` |

阶段现状：A6（pdf_input）、A7（codex_adapter）、A9（validate）已完成；**A1、A8 未做**。
A9 的 5/6/8 项（引用、数值、串稿）锁在 B 的 evidence 字段上，与本文件两卡无关。

**所有命令一律在 `source_code/` 下执行**（`cd source_code`），路径按仓库相对路径写。

---

## 1 公共头（会话级，只贴一次）

```text
你是本项目的【并行工作会话】。当前 Wave 1，仓库里可能还有别的会话在改别的文件。

【工作目录】D:/桌面/金砖大赛/preselect/7_视觉源码
【git 根】D:/桌面/金砖大赛/preselect，提交路径前缀 7_视觉源码/

【项目事实】
- 流水线：PDF → 页面 PNG → Codex 视觉识别 → recognition.json → document_ir.json
  → evidence.json → draft.md → 确定性校验 → final.md
- 模型调用只有一条路径：openai-codex SDK → Codex app-server → Codex Agent
  → pdf-vision Skill → 底层模型。Python 不直连模型 HTTP 接口。
- 判定权在 Python：是否合格只由 pipeline/validate.py 给出，模型说了不算。
- 内容资产（skills/、prompts/、fixtures/quality/）归工号 B。你只读、只哈希、
  只校验存在性，任何情况下不写入、不改写、不补占位文件。

【并行纪律】
1. 只写本卡白名单内的文件。白名单外一律不动，包括"就改一行"。
2. contracts.py / errors.py / settings.py / orchestrator.py / main.py / manifest.py /
   schema_loader.py / validate.py / config.json / requirements.txt / schemas/ 全部只读。
   需要改 → 停下来报告，不要自行修改。
3. 不得新增第三方依赖；不得修改他人测试的断言；不得 mock 被测对象本身。
4. 完成后写 .parallel/handoffs/<CARD>.md，五节齐全：
   改动清单 / 冻结签名 / 验收原始输出 / 未覆盖风险 / 下游需知。
5. 提交：git add 仅本卡文件，message 以 [<CARD>] 开头，正文含验收命令与退出码。不推送。

【硬约束 · 全程有效】
1. 先读文件再动手。禁止猜测函数名、字段名、目录名。
2. 失败必须失败：禁止裸 except、禁止 return {} 兜底、禁止把异常转成空结果。
3. 密钥只从环境读取。禁止打印、禁止写日志、禁止进入异常栈、禁止落盘。
   本项目的密钥变量名是 LLM_API_KEY。
4. 验收命令必须真实执行，贴终端原文，不接受"应该会通过"。
5. 任务卡与仓库不符时停下来报告差异，不要自行改需求。
6. 不得用 openai SDK、requests、httpx 或任何自写客户端替代 Codex SDK。
7. Codex 是 agentic 的：thread_start 必须 sandbox=Sandbox.read_only、ephemeral=True。

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

## 2 A1 卡 · Codex 运行时与供应商能力探测

```text
【A1 Codex 运行时与供应商能力探测】
目标：新建 provider_smoke.py，用官方 openai-codex SDK 探明两件事——Codex 运行时
是否可用、底层供应商支持什么。产出机器可读能力清单 provider_capabilities.json。
不做 PDF 解析，不做业务逻辑，不写提示词。

【已核实事实 · 不要重新探测，也不要另找方案】
- 包名 openai-codex，导入名 openai_codex，版本 0.169.0
- SDK 通过 JSON-RPC 控制本地 codex app-server，不是 HTTP 直连模型
- 导出：Codex / AsyncCodex / CodexConfig / Sandbox / ApprovalMode / Thread /
  TurnHandle / TurnResult / TextInput / ImageInput / LocalImageInput / SkillInput /
  is_retryable_error / ServerBusyError / RetryLimitExceededError / TransportClosedError
- Codex(config=CodexConfig(config_overrides=(...)))；Codex() 构造即启动 app-server，
  必须 close()
- Codex.thread_start(*, sandbox=..., ephemeral=..., model=..., model_provider=...)
- Thread.turn(input, *, output_schema=None, model=None, effort=None, sandbox=None)
  返回 TurnHandle；TurnHandle.run() 返回 TurnResult；TurnHandle.interrupt() 中断
- TurnResult 字段：status / error / duration_ms / final_response / items / usage /
  started_at / completed_at
- Sandbox 只有三个值：read_only / workspace_write / full_access
- 本机 Codex 启动约 3.0 s；account() 返回对象且 requires_openai_auth=False；
  models() 返回 ModelListResponse(data=[...])

【白名单】
- 新增 source_code/provider_smoke.py
- 新增 source_code/provider_capabilities.json（运行产物）
- 新增 .parallel/handoffs/A1.md
其余文件全部只读，包括 pipeline/ 下任何模块、config.json、requirements.txt、schemas/。

【要做】写 source_code/provider_smoke.py，逐项探测，每项独立 try 并记录失败类型：

1. 运行时：openai_codex.__version__；PATH 上 `codex --version` 与
   `shutil.which("codex")` 的结果（仅作参考，不参与判定）；Codex() 能否启动，
   启动耗时是否记录。
2. 账号：codex.account() 的返回；把 requires_openai_auth 与 user_id 是否存在
   折算成 account_class（"gateway"/"chatgpt"/"none"），不得猜测账号细节。
3. 模型：codex.models() 的 Model.id 列表写入 models_visible；
   config.json 的 vision.model 是否在其中（当前为空串，必须如实记 false 并注明
   "model not configured"，不得伪造一个默认模型名）。
4. 供应商：读 load_settings(require_secret=False)。
   - 若 vision.base_url 非空：用 CodexConfig.config_overrides 传
     model_provider="llm"、model_providers.llm.base_url、env_key="LLM_API_KEY"、
     wire_api，跑一次最小 turn。
   - 若为空：不加 overrides，直接跑一次最小 turn（走全局 config.toml），
     provider.model_provider 记 null，并在 provider.ok 如实记录结果。
   wire_api 先试 responses，失败再试 chat，两级都记 ok 与 error_class。
5. 结构化输出：同一次 turn 传 output_schema（最小对象 schema），
   确认 final_response 能被 json.loads；失败记 SCHEMA_UNSUPPORTED。
6. 图片输入：把脚本内硬编码的最小 PNG 字节写入临时文件，
   LocalImageInput(path=...) 能否被接受。**不得为此引入 Pillow 或任何图像库。**
7. Skill 输入：SkillInput(name="pdf-vision", path="skills/pdf-vision/SKILL.md")
   能否被接受；文件不存在记 ASSET_MISSING。
8. 中断：thread.turn() 取 TurnHandle，约 1.5 s 后 interrupt()，记录 status 变为
   interrupted 的耗时（latency_ms）。
9. 截断可观测性：构造长输出请求，记录 TurnStatus 是否出现 interrupted/failed、
   usage 是否可读、duration_ms 是否有值。

【输出 provider_capabilities.json，字段固定，不增不删】
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

【约束】
- decision.vision_path 恒为 image_png。SDK 的输入类型只有 LocalImageInput /
  ImageInput，没有 PDF 文件输入。不要臆造 pdf_input_file 分支。
- decision.skill_transport 由第 7 项结果推导：SkillInput 可用记 skill_input，
  否则记 prompt_text。不得两头都填。
- 每项捕获具体异常后归类（CodexError / ServerBusyError / TransportClosedError /
  TimeoutError / FileNotFoundError / ConfigError），禁止一个 except Exception 吞全部。
- 任何输出不得含 key 片段；base_url 只留 host（urllib.parse 取 netloc），
  空值记 null。
- 单项超时 60 s，全流程上限 300 s；超时项记 error_class="TIMEOUT"，继续下一项。
- 不得写入 prompts/，不得创建占位资产，不得修改 schemas/。
- 未登录/无凭据只允许归为 AuthMissing（code=AUTH_MISSING，退出码 2），
  **不是退出码 8**：I7 的 8 属于 ASSET_*，且 30 条 Code 枚举里没有 AUTH_MISSING。
  直接 from pipeline.errors import AuthMissing 使用，不得新增枚举成员
  （tests/test_engineering_cli.py 会断言 Code 恒为 30 条）。

【验收 · 贴终端原文与退出码】
python provider_smoke.py ; echo "exit=$?"
    预期 exit=0；探测流程完成即成功，不代表 provider.ok 必须为 true
python -c "import json;d=json.load(open('provider_capabilities.json',encoding='utf-8'));print(d['decision'],d['runtime']['started'])"
    预期 {'vision_path': 'image_png', 'skill_transport': 'skill_input'} True
python -c "import json,re;t=open('provider_capabilities.json',encoding='utf-8').read();print(bool(re.search(r'sk-[A-Za-z0-9_-]{10,}',t)))"
    预期 False（无密钥片段）
python -c "import json;d=json.load(open('provider_capabilities.json',encoding='utf-8'));print(sorted(d)==sorted(['probed_at','sdk','runtime','auth','provider','models_visible','structured_output','vision','interrupt','truncation','decision']))"
    预期 True（字段固定）

【失败分支】
- SDK 导入失败 → 退出码 2，打印 ImportError 原文，不得降级为 HTTP 客户端
- 无凭据且网关也不可用 → 退出码 2，只打印 AUTH_MISSING，不打印账号细节
- app-server 起不来 → 退出码 5，打印 stderr 末 20 行（脱敏后）
- 单探测项失败 → 该项记 false + error_class，继续其余项，最终退出码 0
```

---

## 3 A8 卡 · 三阶段编排

> 前置条件全部已满足，开工前逐条用命令核对，不要在会话里"回忆"：
>
> ```text
> python -c "from pipeline.pdf_input import load_pdf;import inspect;print(inspect.signature(load_pdf))"
>     # 必须打印 (path: pathlib.Path, run_dir: pathlib.Path | None = None)
> python -c "from pipeline.errors import AuthMissing,exit_code_for;print(exit_code_for(AuthMissing()))"
>     # 必须打印 2
> python -m pytest -q          # 必须 86 passed
> ls prompts                   # 必须不存在
> ls skills/pdf-vision/SKILL.md
> ```
>
> 另需确认 `provider_capabilities.json` 已由 A1 产出，且
> `decision.skill_transport` 有确定值。缺失则先报告，不要在本卡里补做 A1。

```text
【A8 三阶段编排】
目标：串联 recognize → normalize → evidence，每阶段独立落盘、独立校验，
上一阶段产物不合法时禁止进入下一阶段。

【白名单】
- pipeline/prompt_loader.py（替换现有 stub）
- pipeline/stages/recognize.py、normalize.py、evidence.py（新增）
- tests/test_engineering_stages.py（新增）
- tests/fixtures/engineering/stub_prompts/{vision,normalize,evidence}.md（新增，工程夹具）
其余文件只读。pipeline/stages/__init__.py 是自动发现，不得修改。
orchestrator.py / main.py / contracts.py / schemas/ 一律不动。

【冻结签名，逐字照用】
- load_prompt(name: str, base_dir: Path) -> PromptAsset(text, sha256, path, version)
- load_skill(path: Path) -> SkillAsset(text, sha256, path)
- 阶段函数：(ctx: RunContext, inputs: dict[str, Path]) -> StageResult
- 阶段模块必须暴露 STAGE_NAME: str、run、REQUIRES: list[str]、PRODUCES: list[str]
  （orchestrator.discover_stages 按这四个名字发现）
- 阶段之间只通过磁盘文件传递，不通过内存对象。每个阶段从磁盘读上一阶段产物。

【要做】

1. pipeline/prompt_loader.py（I4）
   - 用流式 sha256；text 只用于哈希与校验，**不拼进 prompt 正文以外的地方**
   - 版本号取文件首行 HTML 注释 <!-- version: x.y.z -->；缺失时 version=None，
     由调用阶段产出 ASSET_NO_VERSION warning（warning 不得使阶段失败）
   - 文件缺失 → AssetMissing（ASSET_MISSING）；文件为空或仅空白 → AssetEmpty
   - 不回退任何内置提示词，不读 prompts/ 之外的同名文件
   - load_skill 只做存在性、可读性、哈希；正文不进入 ModelRequest.prompt_text

2. stages/recognize.py
   - STAGE_NAME="recognize"，REQUIRES=[]，PRODUCES=["recognition"]
     （**requires 里只能写已注册的阶段名**；源 PDF 不是阶段，从
     ctx.artifacts["source_pdf"] 取，写成 ["source_pdf"] 会被编排器判为
     STAGE_NOT_REGISTERED）
   - 从 inputs["source_pdf"] 取源 PDF；调用
     load_pdf(pdf, run_dir=ctx.run_dir) —— **必须显式传 run_dir**，
     不得依赖默认值，更不得读环境变量
   - images = sorted((ctx.run_dir/"input").glob("page_*.png"))；数量必须等于
     page_count，不一致抛 PdfRenderFailed
   - skill_path = ctx.settings.paths.skills_dir/"pdf-vision"/"SKILL.md"；
     缺失抛 AssetMissing
   - prompt = load_prompt("vision", ctx.settings.paths.prompts_dir)
   - 构造 ModelRequest(stage="recognize", prompt_name="vision",
     prompt_text=prompt.text, payload={}, json_schema=load_schema("recognition"),
     images=images, skill=("pdf-vision", str(skill_path)),
     timeout_s=settings.vision.timeout_s, model=settings.vision.model or None)
   - provider = get_provider(settings.vision.provider, settings=settings,
     run_dir=ctx.run_dir)；try: generate() finally: provider.close()
   - 把 result.parsed 原样写入 ctx.run_dir/"recognition.json"
     （UTF-8、无 BOM、indent=2、ensure_ascii=False），不改结构
   - 用 validate_instance("recognition", parsed) 校验；有 issue 时：
     写入 manifest.stages[].issues，抛 PipelineError(SCHEMA_INVALID)，退出码 4
   - 返回 StageResult("recognize","ok",duration,{"recognition": path},issues)

3. stages/normalize.py、stages/evidence.py
   - 与 recognize 同构；normalize 的 REQUIRES=["recognize"]，
     evidence 的 REQUIRES=["normalize"]，其余同名规则同上
   - payload 分别是 {"recognition": <从磁盘读的对象>} 与
     {"document_ir": <从磁盘读的对象>}；schema 分别是 document_ir / evidence
   - 提示词名分别是 normalize / evidence
   - 不传 images、不传 skill（这两个阶段是文本到文本）

4. manifest（I8 的既有字段，不新增字段）
   - 每个阶段开始/结束时读改写 ctx.run_dir/"manifest.json"：
     stages[] 追加或更新 {name,status,started_at,ended_at,duration_ms,artifacts,
     issues,model_calls}；失败也要写（status="failed"）再抛错
   - 首次进入时用 manifest.create_run_manifest(ctx.run_id, ctx.material_id,
     ctx.settings) 初始化，再用 manifest.write_manifest 落盘
   - 不得写入密钥、完整 base_url、绝对路径；artifacts 只写文件名或相对 run 目录路径

5. 失败语义
   - 缺失资产 → AssetMissing（退出码 8），不得降级用内置提示词
   - JSON 不符合 schema → PipelineError(SCHEMA_INVALID)（退出码 4），不进入下一阶段
   - 模型异常/超时 → 由 CodexAdapter 抛出 ModelTimeout/TruncatedResponse/
     InvalidModelOutput/AuthMissing，阶段不得捕获后继续

【本卡修订（待双签，必须照此执行）】
A8 原卡要求"阶段校验失败写 validation.json"。本卡改为：**阶段不写 run 级
validation.json**。原因：该文件的 stats/checks 是整次运行的全局裁决语义（I6），
阶段级写它会让下游把"某一阶段失败"误读成"整次运行已裁决"。
阶段级问题只进 manifest.stages[].issues，run 级报告仍由 `main.py validate` 产出。

【关键设计决策 · 不得自行另选】
- 一个阶段一个 CodexAdapter（每个阶段 finally close）。I5 的"一个 run 一个
  app-server"当前无法满足：RunContext 是不可变契约且 I9 只传路径，没有存放
  provider 的位置。不要为此修改 contracts.py，也不要用模块级全局缓存。
  在 handoff 的"未覆盖风险"里记一条：每 run 启动 3 次 app-server，约多 6 s。
- 测试隔离：monkeypatch `pipeline.stages.recognize.get_provider` 返回假 provider，
  断言它收到的 ModelRequest 字段（images 有序、skill 二元组、prompt_text、
  json_schema）。**被测对象是阶段函数，不是 adapter**，所以这不算 mock 被测对象；
  必须在测试 docstring 里写明这一点。
- 工程夹具 stub_prompts 是三份最小 Markdown（首行带版本注释），只用于测试，
  不参与正式运行。禁止把它们复制进 prompts/。

【禁止】
- 不得创建 source_code/prompts/ 或任何属于 B 的资产
- 不得在代码里内联提示词文本，不得为让链路跑通而跳过 AssetMissing
- 不得把 SKILL.md 正文拼进 prompt_text（skill 只传路径给 Codex）
- 不得跨阶段复用内存对象或模型返回缓冲
- 不得修改 stages/__init__.py、orchestrator.py、main.py 的 --stage 语义

【验收 · 贴终端原文与退出码】
python -m pytest tests/test_engineering_stages.py -q
    预期全绿；至少覆盖：三阶段成功落盘、缺提示词 AssetMissing、
    缺 skill AssetMissing、schema 失败不进入下一阶段、PNG 数量不符、
    manifest 阶段状态、stub 提示词版本号 warning

python main.py run <任一合法单页 PDF> --stage recognize ; echo "exit=$?"
    预期 exit=8，错误 code=ASSET_MISSING，指向 prompts/vision.md
    （因为 prompts/ 未交付，这是正确行为，不是缺陷）
    若得到 3/4/5 或 0，说明检查顺序或失败语义不对，必须修正后再交

python -c "import glob,json,os;p=sorted(glob.glob('result/runs/*/*/manifest.json'),key=os.path.getmtime)[-1];print(sorted(json.load(open(p,encoding='utf-8'))['stages'][0]))"
    预期包含 name/status/started_at/ended_at/duration_ms/artifacts/issues/model_calls
```

---

## 4 两卡之间的人工闸门

A1 交付后，人工只核对三件事，不读代码细节：

1. `provider_capabilities.json` 的 `decision.skill_transport` 是否为确定值。
   若为 `prompt_text`，A8 的 skill 投递方式要改，**先停下来讨论**，不要把
   SKILL.md 正文拼进 prompt。
2. `auth.ok` 是否如实反映当前登录态。本机走自建网关时允许为 true，
   但 `account_class` 不能编。
3. `models_visible` 是否非空；`config.json` 的 `vision.model` 为空串时，
   A8 的请求必须传 `model=None`，由 Codex 用默认模型，不得硬编码模型名。

A8 交付后核对：

1. `python -m pytest -q` 仍全绿（A8 不得修改 A9 测试）。
2. `main.py run ... --stage recognize` 的退出码确实是 8，且错误码是
   `ASSET_MISSING`，不是 `PDF_*`——这证明失败发生在资产检查，不是输入检查。
3. `result/runs/<material>/<run_id>/` 有 `input/ raw/ logs/`，但没有
   `recognition.json`（因为资产缺失，阶段未产出），manifest 里该阶段为 `failed`。

---

## 5 本轮契约修订速查（贴给 Codex 时不用解释，照做即可）

| 事项 | 结论 | 落地位置 |
|---|---|---|
| 未登录/无凭据 | `AuthMissing`，`code=AUTH_MISSING`，退出码 2；不加进 `Code` 枚举 | `pipeline/errors.py` |
| `validate` 子命令 | `valid=false` → 退出码 4 | `main.py` |
| `run` 子命令 | 先建 I2 布局；源 PDF 走 `ctx.artifacts["source_pdf"]` | `main.py` |
| PDF 渲染目标 | `load_pdf(path, run_dir=ctx.run_dir)`；禁止环境变量 | `pipeline/pdf_input.py` |
| 章节标题 | 必须逐字为一级标题 +「引言」「正文」「总结」「来源」，不认编号前缀 | `pipeline/validate.py` |
| 测试命令 | `python -m pytest -q`（`pytest.ini` 已处理第三方插件冲突） | `pytest.ini` |
| 渲染依赖 | Pillow 已声明，不得移除 | `requirements.txt` |
| `stats.evidence_count` | 0/1 对象数，不是事实条数 | `validation.schema.json` 的 description |
