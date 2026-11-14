【A8 三阶段编排 · 本次只做这一张卡，做完停止】

你是本项目的系统工程执行者，工号 A，只对 source_code/ 下的工程链路负责。
你不判断文章科学质量，不撰写业务提示词与 Skill 文本，不定义 schema 业务字段。

━━ 一、当前仓库事实（2026-11-14 00:30 实测，禁止再用旧假设）━━

工作目录：source_code/。解释器固定为仓库根 venv，不是 PATH 上的 python：
  ../venv/Scripts/python.exe     # openai-codex 0.169.0 / pypdf / pypdfium2 / pytest 均已安装

已完成且可用（不要重做、不要"顺手优化"）：
- pipeline/codex_adapter.py    CodexAdapter.generate(ModelRequest) -> ModelResult，真实调用已验证
- pipeline/settings.py         load_settings()，唯一配置入口；settings.paths.{prompts_dir,skills_dir}
- pipeline/errors.py           Issue / Code(30 条) / PipelineError 家族 / exit_code_for()
- pipeline/schema_loader.py    load_schema(name) / validate_instance(name, obj) -> list[Issue]
- pipeline/orchestrator.py     注册表 + pkgutil 自动发现 + requires 拓扑排序 + artifacts 存在性检查
- pipeline/pdf_input.py        load_pdf(path, run_dir=...) -> PdfInfo，必然把每页渲染成 input/page_%04d.png
- pipeline/validate.py         validate_run(run_dir, settings)（A9 已交付，本卡不得改）
- pipeline/manifest.py         create_run_manifest() / write_manifest() / file_sha256()

本卡范围（当前确实为空）：
- pipeline/prompt_loader.py    两个函数都还 raise NotImplementedError
- pipeline/stages/             只有 __init__.py，没有 recognize / normalize / evidence

本卡必须补的断链（全仓实测无人调用）：
- load_pdf() 没有流水线调用方 → PDF 渲染目前永远不会发生
- create_run_manifest() / write_manifest() 没有调用方 → manifest.json 永远不会生成
- I2 要求的 input/source.pdf.sha256 无人写

内容资产现状：skills/pdf-vision/SKILL.md 存在（927 B）；prompts/ 目录不存在。

时序前提（已与需求方确认，不是阻塞）：B 的提示词与 Skill 在系统工程全部完成之后才统一开发。
因此本卡不等待该资产，也不得以"缺提示词所以先内联一版"的方式绕过。
A 的工程闭环必须在不依赖真实资产的前提下自证：阶段逻辑用 tests/fixtures 的 stub 提示词 +
假 provider 覆盖，正式 run 停在 ASSET_MISSING 是计划内的终点，不是回归缺陷。

基线（贴命令前先自己跑一遍确认）：
  ../venv/Scripts/python.exe -m pytest -q
      → 86 passed
  ../venv/Scripts/python.exe main.py run /tmp/x.pdf --stage recognize
      → {"code":"STAGE_NOT_REGISTERED"} exit=1

内容资产边界：skills/、prompts/、schemas/ 的业务字段归 B。你只读取、算哈希、校验存在性，
不写入、不改写、不补占位文件、不在代码里内联提示词。

━━ 二、硬约束 ━━

1. 先读文件再动手，不猜函数名、字段名、返回结构、目录名。
2. 只改【白名单】内文件；白名单外一律先报告再等指令。
3. 一次只做这一张卡；发现别的问题写进"未覆盖风险"，不顺手改。
4. 失败必须失败：禁止裸 except、禁止 return {} 兜底、禁止把异常转成空结果继续跑。
5. 校验结论只来自 Python 校验器；你不得输出"已通过校验"这类判断。
6. 密钥只从 settings.vision.api_key 取；禁止打印、写日志、进异常文本、落盘。
7. 禁止为让验收变绿而放宽断言、跳过用例、mock 掉被验证对象。
8. 不新增依赖；不得使用 openai SDK / requests / httpx 自写模型客户端。
9. 模型调用唯一入口：pipeline.vision_provider.get_provider("openai_codex")。
10. 不得绕过 CodexAdapter 直连 SDK；sandbox=read_only + ephemeral 由 adapter 负责。
11. 任务卡与仓库不符时，停下来报告差异，不自行改需求。

━━ 三、白名单 ━━

改：pipeline/prompt_loader.py
建：pipeline/stages/recognize.py
建：pipeline/stages/normalize.py
建：pipeline/stages/evidence.py
建：pipeline/stages/_shared.py     （三阶段公共助手：原子落盘 / manifest 阶段记账 / base_url 脱敏）
建：tests/test_engineering_stages.py
建：tests/fixtures/engineering/stub_prompts/vision.md
建：tests/fixtures/engineering/stub_prompts/normalize.md
建：tests/fixtures/engineering/stub_prompts/evidence.md

只读、不得修改：pipeline/ 其余模块、main.py、pipeline/stages/__init__.py、
schemas/、skills/、config.json、requirements*.txt、pytest.ini、tests/ 下已有文件、.env。

━━ 四、冻结签名（逐字照用，别改名别改参）━━

load_prompt(name: str, base_dir: Path) -> PromptAsset(text, sha256, path, version)
load_skill(path: Path) -> SkillAsset(text, sha256, path)
阶段函数：(ctx: RunContext, inputs: dict[str, Path]) -> StageResult
阶段模块必须暴露 STAGE_NAME / run / REQUIRES / PRODUCES 四个名字——
orchestrator.discover_stages() 只认这四个，且 REQUIRES 里只能写已注册的阶段名。

RunContext(frozen)：run_id / material_id / run_dir / settings / artifacts（dict[str, Path]）
源 PDF 由 main.py 放进 ctx.artifacts["source_pdf"]；PDF 不是阶段，不要写进 REQUIRES，
否则编排器判 STAGE_NOT_REGISTERED。
StageResult(name, status, duration_ms, artifacts: dict[str, Path], issues: list[Issue])：
status 只能是 "ok" 或 "skipped"；PRODUCES 里的每个 key 必须出现在 artifacts，且对应文件必须已落盘。
duration_ms 会被编排器覆盖，返回 0.0 即可；manifest 里的耗时自己计时。

━━ 五、要做 ━━

1. pipeline/prompt_loader.py（I4）
   - 读字节 → UTF-8 解码（去 BOM）→ sha256（复用 manifest.file_sha256，不要另写一套）
   - 版本号取首行 HTML 注释：^<!--\s*version:\s*(\S+)\s*-->$；缺失时 version=None，
     由调用阶段产出 ASSET_NO_VERSION 的 warning（severity="warning"，不得使阶段失败）
   - 文件不存在 → AssetMissing（ASSET_MISSING，exit 8）
   - 文件为空或仅空白 → AssetEmpty（ASSET_EMPTY，exit 8）
   - 不回退内置提示词、不到 prompts/ 之外找同名文件
   - load_skill 只校验存在性/可读性 + 算哈希；SkillAsset.text 不得进入 prompt_text

2. pipeline/stages/_shared.py（无 STAGE_NAME，自动发现会跳过）
   - write_json_atomic(path, payload)：UTF-8、无 BOM、缩进 2、ensure_ascii=False、结尾换行、
     先写临时文件再 os.replace；OSError → IoFailure
   - record_stage(ctx, name, status, started_at, ended_at, duration_ms, artifacts, issues, model_calls)：
     首次进入用 create_run_manifest(ctx.run_id, ctx.material_id, ctx.settings) 初始化，
     之后读改写 ctx.run_dir/"manifest.json"，按 name 更新或追加 stages[]，字段固定为
     {name,status,started_at,ended_at,duration_ms,artifacts,issues,model_calls}
     artifacts 只写相对 run_dir 的 posix 路径；issues 用 Issue.to_dict()
     写盘前把 config 里的 "vision.base_url" 值压成 host（urllib.parse.urlsplit(...).netloc），
     并把 config 中出现的绝对路径压成 basename —— manifest 不得出现完整 base_url 或本机绝对路径
   - 失败也要记账：捕获异常时先 record_stage(status="failed") 再原样抛出

3. pipeline/stages/recognize.py
   - STAGE_NAME="recognize"，REQUIRES=[]，PRODUCES=["recognition"]
   - 进入先初始化 manifest（status="running"），异常路径写 failed
   - 先校验内容资产，再做重活：
       prompt = load_prompt("vision", ctx.settings.paths.prompts_dir)
       skill_path = ctx.settings.paths.skills_dir / "pdf-vision" / "SKILL.md"；缺失 → AssetMissing
     校验顺序必须是资产 → PDF → 模型；缺资产时不得先渲染
   - info = load_pdf(ctx.artifacts["source_pdf"], run_dir=ctx.run_dir)（必须显式传 run_dir）
   - 写 I2 要求的 input/source.pdf.sha256：64 位小写十六进制 + "\n"
   - images = sorted((ctx.run_dir / "input").glob("page_*.png"))；长度必须等于 info.page_count，
     否则 PdfRenderFailed（exit 3），并在消息里给出期望值与实际值
   - ModelRequest(stage="recognize", prompt_name="vision", prompt_text=prompt.text,
     payload={"material_id": ctx.material_id, "page_count": info.page_count,
              "pages": [{"page_number": p.page_number} for p in info.pages]},
     json_schema=load_schema("recognition"), images=images,
     skill=("pdf-vision", str(skill_path)),
     timeout_s=ctx.settings.vision.timeout_s, model=ctx.settings.vision.model or None)
     payload 只放定位信息，不得自定义业务字段（业务字段归 B）
   - provider = get_provider(ctx.settings.vision.provider, settings=ctx.settings,
     run_dir=ctx.run_dir)；try/finally 必须 close()
   - result.parsed 原样写入 ctx.run_dir/"recognition.json"（不改结构、补字段、排序）
   - 落盘后用 validate_instance("recognition", parsed) 校验：有 error 级 issue → 先记账 failed
     → raise PipelineError(Code.SCHEMA_INVALID, ...)，消息只带 issue 的 path/code，不带模型原文
   - 返回 StageResult("recognize","ok",0.0,{"recognition": path},issues)

4. pipeline/stages/normalize.py、pipeline/stages/evidence.py
   - 与 recognize 同构。normalize：REQUIRES=["recognize"]、PRODUCES=["document_ir"]、
     提示词 "normalize"、schema "document_ir"；evidence：REQUIRES=["normalize"]、
     PRODUCES=["evidence"]、提示词 "evidence"、schema "evidence"
   - 输入从磁盘读：normalize 读 recognition.json，evidence 读 document_ir.json；
     读不到 → AssetMissing；JSON 解析失败 → PipelineError(Code.SCHEMA_INVALID)
   - payload 分别是 {"recognition": <对象>} 与 {"document_ir": <对象>}
   - 不传 images、不传 skill（这两个阶段是文本到文本）
   - 禁止使用 pipeline/normalize.py 的 normalize_response() 与 pipeline/evidence.py 的
     attach_evidence()：它们用 get(...) 填默认值，属静默兜底。这两个文件本卡不改不删，
     若确认全仓无引用，只在报告里列为待清理死代码

5. 阶段间只通过磁盘文件传递：上一阶段产物由下一阶段重新读盘，不得在内存里缓存或复用模型返回缓冲。
   每阶段提示词各自 load 一次并记 sha256，不跨阶段缓存。

6. 资产可替换（本项目的硬要求）：阶段只按 I4 冻结的名字从 settings.paths.prompts_dir 读取
   vision / normalize / evidence，文件名不得自创、不得内置 fallback 文本。
   把 stub 目录整体替换为真实 prompts/ 时必须零代码改动——这一条要用测试证明，
   不能只靠"我觉得可以"。

7. 失败语义（退出码必须落在 I7 上）
   - 缺 prompt / skill → AssetMissing，exit 8
   - 产物不符合 schema → PipelineError(SCHEMA_INVALID)，exit 4，且不进入下一阶段
   - PDF 渲染/页数不符 → PdfRenderFailed，exit 3
   - 模型超时/截断/非法 JSON/拒答 → 由 CodexAdapter 抛 ModelTimeout / TruncatedResponse /
     InvalidModelOutput / ModelRefused / AuthMissing，阶段不得捕获后继续，也不得包装成别 code

━━ 六、三处必须照做的决定（与文档卡文字不一致处已在此裁定，不要自行改回）━━

1. 阶段不写 run 级 validation.json。理由：该文件的 stats/checks 是整次运行的全局裁决语义（I6），
   阶段级写它会让下游把"某阶段失败"误读为"整次运行已裁决"。
   阶段级问题只进 manifest.stages[].issues；run 级报告仍由 main.py validate 产出。
2. 一阶段一个 CodexAdapter，每阶段 finally close()。原因：RunContext 是不可变契约、I9 只传路径，
   没有存放 provider 的位置；本卡不允许改 contracts.py，也不允许用模块级全局缓存。
   代价是每 run 启动 3 次 app-server（约多 6 s），这条必须写进"未覆盖风险"。
3. manifest 只写阶段字段与 input 三项（path_name/sha256/page_count），I8 全量字段留给 A12。
   不新增 manifest 顶层键。

━━ 七、测试要求（tests/test_engineering_stages.py，全程离线、不联网、不调真模型）━━

用假 provider 注入：monkeypatch 各阶段模块里的 get_provider 返回 FakeProvider，
断言它收到的 ModelRequest 字段。被测对象是阶段函数与落盘/失败语义；模型边界已由 A7 单独验收，
替换外部依赖不属于"mock 掉被验证对象"——这句话必须写进测试文件 docstring。

stub_prompts 三份最小 Markdown，首行 `<!-- version: 0.0.1-stub -->`，正文一到两行，
明确标注仅供工程测试。禁止复制进 prompts/。

至少覆盖：
 1. load_prompt：缺失→ASSET_MISSING；全空白→ASSET_EMPTY；无版本注释→version is None 且阶段产出
    ASSET_NO_VERSION warning（status 仍为 ok）；sha256 与 hashlib 直算一致
 2. load_skill：缺失→ASSET_MISSING；正文不进 prompt_text
 3. recognize 成功：recognition.json 内容等于 parsed；images 有序且等于 page_0001..N；
    request.skill == ("pdf-vision", 绝对路径)；request.prompt_text == stub 正文；
    SKILL.md 正文不出现在 prompt_text 里
 4. 缺 prompts/vision.md：AssetMissing，且 FakeProvider 调用次数为 0，recognition.json 不存在
 5. 缺 SKILL.md：AssetMissing（skills_dir 指向空目录）
 6. PNG 数量不符：PdfRenderFailed（fake 掉 load_pdf 返回 page_count=2，只存在 1 张 PNG）
 7. normalize 返回不符合 document_ir 的对象：PipelineError SCHEMA_INVALID；document_ir.json 已落盘
    （失败也保留证据）；evidence.json 不存在；manifest 里 normalize 为 failed、evidence 无记录
 8. run_stages(ctx, "all")：执行顺序为 recognize→normalize→evidence，三个 JSON 均存在，
    manifest.stages 三条且 status 全为 ok
 9. 单跑 normalize：手写 Recognition.json 后能产出 document_ir.json；删除 recognition.json 后
    报 AssetMissing（证明阶段间只通过磁盘传递）
10. manifest 不含 "https://"、不含 sk- 形态串、不含本机绝对路径
11. 资产可替换：同一套阶段函数在 prompts_dir 指向 stub 目录与指向另一份文案不同的临时目录时
    都能正常工作、只有 prompt_text 不同（证明换真实资产零代码改动）

注意：register_stage 对同名阶段只在 fn 身份完全一致时幂等。测试里不要 monkeypatch
阶段模块的 run 属性（会触发 STAGE_DUPLICATE）；需要替换注册表时用
monkeypatch.setattr(orchestrator, "STAGE_REGISTRY", {...})。

━━ 八、验收（先跑基线，再改，再跑；全部贴终端原文与退出码）━━

cd source_code

A. ../venv/Scripts/python.exe -m pytest -q
   预期：全绿，总数 > 86

B. ../venv/Scripts/python.exe -c "from pipeline.stages import discover_modules;print([m.STAGE_NAME for m in discover_modules()])"
   预期：['evidence', 'normalize', 'recognize']

C. ../venv/Scripts/python.exe -c "import os,pathlib;from pypdf import PdfWriter;p=pathlib.Path(os.environ['TEMP'])/'one-page.pdf';w=PdfWriter();w.add_blank_page(width=595,height=842);f=p.open('wb');w.write(f);f.close();print(p)"

D. ../venv/Scripts/python.exe main.py run "$TEMP/one-page.pdf" --stage recognize ; echo "exit=$?"
   预期：exit=8，错误 code=ASSET_MISSING，指向 prompts/vision.md
   （B 的提示词在系统开发完成后才交付，所以这次必然停在这里；这是计划内的终点，不是回归缺陷。
     若得到 0/3/4/5，说明资产检查的顺序或失败语义写错，必须修正后再交）

E. ../venv/Scripts/python.exe -c "import glob,json,os;p=sorted(glob.glob('result/runs/*/*/manifest.json'),key=os.path.getmtime)[-1];print(json.load(open(p,encoding='utf-8'))['stages'])"
   预期：该阶段 name=recognize、status=failed，且 artifacts/issues/model_calls 三键都在

F. ../venv/Scripts/python.exe -c "import glob;t=''.join(open(p,encoding='utf-8').read() for p in glob.glob('result/runs/*/*/manifest.json'));print('https://' in t, 'sk-' in t)"
   预期：False False

━━ 九、汇报格式（严格照此）━━

第一轮只输出 A、B 两项，然后停下等我确认，不要动代码：
A. 拟修改/新增文件清单：每个文件一句话写明它的责任边界
B. 验收命令 + 每条的预期输出特征
（我确认后再实施）

实施完成后输出：
D. 变更摘要：文件 / 新增或变更的函数签名 / 行为变化
E. 上述 A–F 六条验收命令的真实终端输出（原文，不要复述）
F. 失败原因（若有）+ 未覆盖风险（至少包含：每 run 启动 3 次 app-server、
   logs/pipeline.log 仍无人写、三份业务 schema 字段仍是空壳导致阶段校验强度受限、
   真实提示词与字段到位后需做一次集成验证——该验证属集成阶段，不在本卡）
