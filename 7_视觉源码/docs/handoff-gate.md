# G1 交付闸门自证：契约漂移与真实链路缺口

取证日期：2026-11-14（Asia/Shanghai）。审计基线 HEAD：69f693f5ed76202e8c8b0006233ee37b554b6e94。本文仅记录工程事实，不评价内容科学质量，不代填签字，不修改冻结清单、内容资产、业务逻辑或 verify.py。

“真实 run”在表二中指 manifest 的 model.provider 与 config["vision.provider"].value 均为 openai_codex；另列是否存在 model_calls，避免把纯文本识别或模型调用前失败当成已调用模型。阶段成功仅按 manifest 的 status=="ok" 计数；缺席阶段写“未执行”，不补写成功。auth.ok 仅是 provider_capabilities.json 的存档值，本卡未联网复测登录态。

执行目录为 source_code/；所有下文给出的回归命令均从该目录执行。源文件、资产与冻结清单的前后 SHA-256 比较另见取证记录。verify.py 自身会调用 demo 并生成被 Git 忽略的 replay 运行目录，本卡未把这些新增目录计入真实 provider 证据。

## 一、契约对齐表

| 接口 | 文档声明 | 代码事实 | 一致？ | 证据 |
|---|---|---|---|---|
| I1 | CLI 具备 run/doctor/validate/package；默认 stdout 人读、--json 单个 JSON；缺省 material_id 有 warning；可断点复用。 | 四命令存在且 --stage 递归选前置；无 --json 参数，成功和异常都直接输出 JSON；缺省编号直接取 stem，未发 warning；每次 run 用 uuid 新建目录，orchestrator 不读旧产物以跳过阶段。 | 否；G1-B04。 | docs/接口冻结清单_A-B.md:26–49；source_code/main.py:19–57,117–132；source_code/pipeline/orchestrator.py:58–129。 |
| I2 | 固定 material_id/run_id 布局、保留中间文件、失败无 final.md；A19 按页记录 page_text/page_plan，视觉附属产物按需。 | CLI 创建 input/raw/logs；recognize 登记三项必产物；真实 run 只有 recognize，未覆盖后续目录契约；10 份 openai_codex manifest 同目录均无 final.md。 | 已执行识别段有证据；完整运行目录未证；G1-B08。 | docs/接口冻结清单_A-B.md:53–95；source_code/main.py:47–55；source_code/pipeline/stages/recognize.py:23,173–219；表二逐个 manifest 路径。 |
| I3 | 正文列 7 份 schema，但 §12 I3 仍写“现有 schema 为空壳”；正文又称当前 4 个 additionalProperties=true。 | recognition/document_ir/evidence/validation/table/figure/formula 七文件均有 $id、schema_version、properties、非空 required；版本依次为 1.0.0/1.0.0/1.0.1/1.1.0/1.0.0/1.0.0/1.0.0，顶层 additionalProperties 全为 false；加载器逐 schema 登记版本。 | 否；G1-B03。 | docs/接口冻结清单_A-B.md:103–117,532；source_code/schemas/recognition.schema.json、document_ir.schema.json、evidence.schema.json、validation.schema.json、table.schema.json、figure.schema.json、formula.schema.json（均位于 source_code/schemas/）；source_code/pipeline/schema_loader.py:21–36,90–115。 |
| I4 | 路径表声明 vision/normalize/evidence/writer/translate/repair 六个 prompt；所有资产空白须 ASSET_EMPTY，首行无 HTML version 须 ASSET_NO_VERSION；§12 又写需定 5 个文件名。 | 实际 8 个 prompt：vision/normalize/evidence/writer/repair/table/figure/formula；writer 仍调用缺失的 translate；识别通过变量选择 vision/table/figure/formula；8 文件首行版本均合式。Skill 首行是 YAML frontmatter，load_skill 仅读取与哈希，不检查空白/版本，也不产生版本 warning。 | 否；文件集合 G1-B01、Skill 加载语义 G1-B02。 | docs/接口冻结清单_A-B.md:165–171,193–198,533；source_code/prompts/；source_code/skills/pdf-vision/SKILL.md:1–4；source_code/pipeline/prompt_loader.py:23–46；source_code/pipeline/writer.py:44–58；source_code/pipeline/stages/recognize.py:30–31,200–214。 |
| I5 | TextInput 与 SkillInput 分离，read-only/ephemeral；同 run 复用一个 Codex；重试退避交给 retry_on_overload。 | SkillInput(name,path)、图片与提示词分开；thread_start 使用 read_only/ephemeral=True。writer 与视觉调用每次获取 provider，generate_and_close 每次关闭，下一次重新建适配器/Codex；重试直接 time.sleep。 | 传输/沙箱静态实现对齐；生命周期/退避不符；G1-B05。 | docs/接口冻结清单_A-B.md:212–228,254–269；source_code/pipeline/codex_adapter.py:102–118,135–143,168–180,224–238,352–359；source_code/pipeline/writer.py:29–37；source_code/pipeline/stages/recognize.py:217–219；source_code/pipeline/stages/_shared.py:148–164。 |
| I6 | validation 为 issues 对象、checks.enabled/skipped；缺输入与 skipped 语义已有待双签的 A14-1 条款；§12 备注仍要求从 errors[str] 升级。 | validation schema 1.1.0 要求 issues/checks；Python 按 error 计算 valid；checks.skipped=[]，缺输入产生 ASSET_MISSING；repair 只传 error issue code 并限制次数。旧备注与实现不符；未据此代补 B 签字。 | 结构实现对齐；旧备注/正式冻结待处理；G1-B03、G1-B09。 | docs/接口冻结清单_A-B.md:295–335,380–382,535；source_code/schemas/validation.schema.json；source_code/pipeline/validate.py:539–598；source_code/pipeline/repair.py:73–108。 |
| I7 | AUTH_MISSING→2 且不进入 Issue Code；PDF→3、SCHEMA/IR→4、MODEL→5、REPAIR→6、ASSET→8；validate valid=false→4。 | AuthMissing 独立于 Code；exit_code_for 和 CLI validate 按上述映射；阶段异常向外抛出，未实现阶段失败；本卡未重跑全部错误分支。 | 所列静态映射对齐；运行验收仍受 G1-B11 阻塞。 | docs/接口冻结清单_A-B.md:374–417；source_code/pipeline/errors.py:10–40,95–105,173–193；source_code/main.py:95–107；source_code/pipeline/orchestrator.py:63–67,124–129。 |
| I8 | assets 下按 prompts/skills/schemas 汇总哈希；记录 commit、SDK/二进制版本、调用数、依赖、缓存键。 | 所有扫描到的 manifest 的 assets 三项均为空；调用中的 prompt/skill 哈希只出现在 stages[].model_calls；初始化 code.git_commit=null、binary_version=null、calls/deps={}、cache.key=null，record_stage 不汇总这些字段。 | 否；G1-B06。 | docs/接口冻结清单_A-B.md:425–458；source_code/pipeline/manifest.py:26–40；source_code/pipeline/stages/_shared.py:108–132,178–185；表二全部 manifest 的 assets 字段。 |
| I9 | register_stage 注册 name/fn/requires/produces；阶段经磁盘产物通信；扩展阶段无需改 orchestrator 的业务分支。 | discovery 读取四项元数据并注册；拓扑序自动补前置；调用时传 artifact 路径副本，返回后检查必产物与文件存在；兼容路径字典或 StageResult。 | 所列扩展/产物机制静态对齐；不据此声称真实全链路成功。 | docs/接口冻结清单_A-B.md:462–477；source_code/pipeline/orchestrator.py:25–55,58–123；source_code/pipeline/stages/__init__.py。 |
| I10 | 遍历质量夹具，断言 expect_codes、must_not_codes；夹具缺失须 skipped 并外显。 | 5 类夹具存在；测试复制到临时目录、校验、比对 code、检查源哈希不变；缺目录或必需 case 时 assert 失败，未调用 pytest.skip；测试允许额外错误，实测每类均有 SCHEMA_INVALID。 | 只读消费对齐；缺失状态契约不符；G1-B07。 | docs/接口冻结清单_A-B.md:481–510；source_code/tests/test_engineering_fixtures.py:16–57；source_code/fixtures/quality/；补充测试输出原文。 |

### 静态检查的口径

check_assets.py 只对 pipeline/**/*.py 中直接写出的 load_prompt("name", ...) / load_prompt('name', ...) 做正则提取，不对变量、字典或控制流作 AST/运行时推断。因此代码字面量集合是 evidence/normalize/repair/translate/writer，文件集合是 evidence/figure/formula/normalize/repair/table/vision/writer。missing=["translate"]；extra=["figure","formula","table","vision"]。extra 表示“没有对应字面量调用”，不表示未使用：vision/table/figure/formula 的动态选择事实位于 source_code/pipeline/stages/recognize.py:30–31,200–201。

脚本同样以正则提取 load_skill(ctx.settings.paths.skills_dir / "pdf-vision" / "SKILL.md")，与 I4 表格 Skill 路径比对；未支持的路径表达式显式报 SCHEMA_INVALID，不猜测。每个现存 prompt 文件首行按严格 x.y.z 格式逐个检查；Skill 的 frontmatter/无版本检查冲突单列。ok 精确等于 missing、extra、doc_mismatch、version_issues 四项均为空。本工具无管线模块导入、无网络或写文件操作；退出码为 0（一致）、8（资产契约差异）、7（读取失败）、4（无法解析的检查输入），不改变 verify.py 判定。

## 二、真实链路证据表

| provider / 检查项 | stage 序列及调用记录 | 失败 code（issues 与 model_calls.error_code 并集） | final.md | 说明 | 证据 |
|---|---|---|---|---|---|
| replay | recognize=ok（model_calls=0） → normalize=ok（model_calls=1） → evidence=ok（model_calls=1） → write=ok（model_calls=2） → repair=ok（model_calls=0） | — | 有 | 回放；工程 stub；repair 无模型调用 | source_code/result/runs/DEMO-01/06aecabc06734e66b56ac733d5d6e101/manifest.json |
| replay | recognize=ok（model_calls=0） → normalize=ok（model_calls=1） → evidence=ok（model_calls=1） → write=ok（model_calls=2） → repair=ok（model_calls=0） | — | 有 | 回放；工程 stub；repair 无模型调用 | source_code/result/runs/DEMO-01/2e2cd89a39b64a17984c8397bab381fc/manifest.json |
| replay | recognize=ok（model_calls=0） → normalize=ok（model_calls=1） → evidence=ok（model_calls=1） → write=ok（model_calls=2） → repair=ok（model_calls=0） | — | 有 | 回放；工程 stub；repair 无模型调用 | source_code/result/runs/DEMO-01/7adf05f3ba57481da50be837d6e2e7d3/manifest.json |
| replay | recognize=ok（model_calls=0） → normalize=ok（model_calls=1） → evidence=ok（model_calls=1） → write=ok（model_calls=2） → repair=ok（model_calls=0） | — | 有 | 回放；工程 stub；repair 无模型调用 | source_code/result/runs/DEMO-01/7bb763219186478c92b2c8607053edda/manifest.json |
| replay | recognize=ok（model_calls=0） → normalize=ok（model_calls=1） → evidence=ok（model_calls=1） → write=ok（model_calls=2） → repair=ok（model_calls=0） | — | 有 | 回放；工程 stub；repair 无模型调用 | source_code/result/runs/DEMO-01/7fefab356ed146c189cfe78f78a50896/manifest.json |
| replay | recognize=ok（model_calls=0） → normalize=ok（model_calls=1） → evidence=ok（model_calls=1） → write=ok（model_calls=2） → repair=ok（model_calls=0） | — | 有 | 回放；工程 stub；repair 无模型调用 | source_code/result/runs/DEMO-01/9b507536f05e4417b28ab235c78feda2/manifest.json |
| replay | recognize=ok（model_calls=0） → normalize=ok（model_calls=1） → evidence=ok（model_calls=1） → write=ok（model_calls=2） → repair=ok（model_calls=0） | — | 有 | 回放；工程 stub；repair 无模型调用 | source_code/result/runs/DEMO-01/a1e66b40e48a455c9e7acad7876c07d3/manifest.json |
| replay | recognize=ok（model_calls=0） → normalize=ok（model_calls=1） → evidence=ok（model_calls=1） → write=ok（model_calls=2） → repair=ok（model_calls=0） | — | 有 | 回放；工程 stub；repair 无模型调用 | source_code/result/runs/DEMO-01/c23e82f374c74d1aa6036edf518bb44f/manifest.json |
| replay | recognize=ok（model_calls=0） → normalize=ok（model_calls=1） → evidence=ok（model_calls=1） → write=ok（model_calls=2） → repair=ok（model_calls=0） | — | 有 | 回放；工程 stub；repair 无模型调用 | source_code/result/runs/DEMO-01/ce3e4b7cd86d4ce0ae98b2446fb34e36/manifest.json |
| replay | recognize=ok（model_calls=0） → normalize=ok（model_calls=1） → evidence=ok（model_calls=1） → write=ok（model_calls=2） → repair=ok（model_calls=0） | — | 有 | 回放；工程 stub；repair 无模型调用 | source_code/result/runs/DEMO-01/efe79de258034a268da8820c777f0af7/manifest.json |
| replay | recognize=ok（model_calls=0） → normalize=ok（model_calls=1） → evidence=ok（model_calls=1） → write=ok（model_calls=2） → repair=ok（model_calls=0） | — | 有 | 回放；工程 stub；repair 无模型调用 | source_code/result/runs/DEMO-01/fc7584e2855d4a60bc7d419e97479647/manifest.json |
| replay | recognize=ok（model_calls=0） → normalize=ok（model_calls=1） → evidence=ok（model_calls=1） → write=ok（model_calls=2） → repair=ok（model_calls=0） | — | 有 | 回放；工程 stub；repair 无模型调用 | source_code/result/runs/DEMO-01/fea3188703ef4d2ba7ecdee88c9fdae7/manifest.json |
| openai_codex | recognize=ok（model_calls=5） | — | 无 | 有模型调用记录；后续四阶段未执行 | source_code/result/runs/MED-04/07b1596fa58349428aee43f2aab2a28d/manifest.json |
| openai_codex | recognize=failed（model_calls=1） | MODEL_API_ERROR | 无 | 有模型调用记录；后续四阶段未执行 | source_code/result/runs/MED-04/1af4bc8486a34b688d3ed45f813dee01/manifest.json |
| openai_codex | recognize=ok（model_calls=0） | — | 无 | 无远端调用记录 | source_code/result/runs/MED-04/37cb0cdccd694eb5b32148c6f3646580/manifest.json |
| openai_codex | recognize=failed（model_calls=1） | MODEL_TRUNCATED | 无 | 有模型调用记录；后续四阶段未执行 | source_code/result/runs/MED-04/829448593121420797f6483174983b68/manifest.json |
| openai_codex | recognize=failed（model_calls=1） | MODEL_TRUNCATED | 无 | 有模型调用记录；后续四阶段未执行 | source_code/result/runs/MED-04/fd5194c9e799446f992b79e0fe10e132/manifest.json |
| openai_codex | recognize=failed（model_calls=0） | ASSET_MISSING | 无 | 无远端调用记录 | source_code/result/runs/one-page/cffcda0ee57d41b38d2263b1659391d9/manifest.json |
| openai_codex | recognize=failed（model_calls=0） | ASSET_MISSING | 无 | 无远端调用记录 | source_code/result/runs/one-page/dba8d02270a84bb7b3d0643b0d39ee37/manifest.json |
| openai_codex | recognize=failed（model_calls=0） | ASSET_MISSING | 无 | 无远端调用记录 | source_code/result/runs/single-page/27b2654c76044c80ba8fce264a57337e/manifest.json |
| openai_codex | recognize=failed（model_calls=0） | ASSET_MISSING | 无 | 无远端调用记录 | source_code/result/runs/single-page/72f3095b786e45ebaad34816e2d1470c/manifest.json |
| openai_codex | recognize=failed（model_calls=0） | ASSET_MISSING | 无 | 无远端调用记录 | source_code/result/runs/single-page/af69afb353f74292acdaa8468c2701ca/manifest.json |
| openai_codex / normalize 汇总 | 阶段记录 0；ok 0 | 未执行，无失败 code 可填 | 无 | 曾成功？否 | source_code/result/runs/*/*/manifest.json（上列 10 个真实配置 run 逐份比对） |
| openai_codex / evidence 汇总 | 阶段记录 0；ok 0 | 未执行，无失败 code 可填 | 无 | 曾成功？否 | source_code/result/runs/*/*/manifest.json（上列 10 个真实配置 run 逐份比对） |
| openai_codex / write 汇总 | 阶段记录 0；ok 0 | 未执行，无失败 code 可填 | 无 | 曾成功？否 | source_code/result/runs/*/*/manifest.json（上列 10 个真实配置 run 逐份比对） |
| openai_codex / repair 汇总 | 阶段记录 0；ok 0 | 未执行，无失败 code 可填 | 无 | 曾成功？否 | source_code/result/runs/*/*/manifest.json（上列 10 个真实配置 run 逐份比对） |
| 能力探测存档 | auth.ok=false；runtime.started=true；skill_transport=skill_input | 未联网复测 | 不适用 | 不把存档值伪称实时认证结果 | source_code/provider_capabilities.json:8–13,52 |

全量遍历共 22 份 manifest：replay=12，openai_codex=10；所有文件均成功解析。openai_codex 的 10 份记录中，4 份有 model_calls，6 份在模型调用前失败或走纯文本分支；成功且有模型调用的只有 MED-04/07b1596fa58349428aee43f2aab2a28d。**normalize、evidence、write、repair 在真实 provider 下成功过吗？否：四阶段记录数和成功数均为 0，真实 final.md 为 0。** 这不是把 failed 写成“未执行”：真实 run 的实际失败 code 已在上表逐个保留。

所有 22 份 manifest 的 assets 都是 {"prompts":{},"schemas":{},"skills":{}}。历史真实调用中的 prompt_sha256/skill_sha256 仅位于 stages[].model_calls。当前 Skill 哈希与成功识别记录相同，但 vision prompt 已变更，旧识别成功不覆盖当前资产组合。replay 的 final.md 只证明工程回放流程执行；source_code/main.py:62–68 将 demo 资产目录指向 tests/fixtures/demo，source_code/pipeline/packaging.py:382–388 明确拒绝 replay 打包。

每日闸门对照：D1 的双签尚缺；D2 没有真实 normalize/evidence 和完整 e2e 文档；D3 没有四材料真实 final 或干净环境证明。存在性扫描中缺失文档为 docs/e2e-run-1.md、docs/four-material-run.md、docs/clean-env-demo.md；依据为 docs/A_开发提示词方案.md:612–630,659–703,770–776。

## 三、资产并入门槛表

任务文字称“7 个 prompt”，实际目录是 8 个；本表逐个覆盖 8 个现存文件，另列缺失的 translate，不删减资产来满足数量。离线测试中的 fake provider/stub prompt 只验证工程机制，不能验证 B 正文在真实模型中的效果；下列真实命令列为后续解阻动作，本卡未联网执行。

| 资产 | 是否已在真实链路被加载 | 改动后可执行的验证命令 / 并入状态 | 证据 |
|---|---|---|---|
| skills/pdf-vision | 当前哈希在 07b1596fa58349428aee43f2aab2a28d 的 5 次成功 recognize 调用中登记；A 端读取与传 SkillInput 有证据，Codex 内部实际读取正文没有独立日志；后续四阶段无真实证据。 | python tools/check_assets.py 可查静态契约；python -m pytest tests/test_engineering_stages.py tests/test_engineering_extract.py -q 可查临时资产加载/路由机制；真实回归 BLOCKED：现有最新 MED-04 识别无模型调用，缺少保证触发视觉分支的正式输入与复测记录。 | source_code/skills/pdf-vision/SKILL.md；source_code/result/runs/MED-04/07b1596fa58349428aee43f2aab2a28d/manifest.json；source_code/result/runs/MED-04/37cb0cdccd694eb5b32148c6f3646580/manifest.json；source_code/pipeline/stages/recognize.py:196–219。 |
| prompts/vision.md | 历史 1.0.0 进入成功真实识别，记录哈希 02968c9e2881ed5e448258ff546a2cec515543a14a7938bbfc79a86758273fae；当前 1.2.0 哈希 a0631737b396ae47d6b0b93e0ec52f83af0338fc00ab2afa9518318a1ed8f182，没有同哈希真实调用。 | python tools/check_assets.py 校验版本/集合；真实正文回归 BLOCKED：须选取会生成 scan_page 的正式 PDF 后运行 recognize，并比对新增 model_calls 的当前哈希，不能把现存文本页 recognize 当作视觉验证。 | source_code/prompts/vision.md:1；source_code/result/runs/MED-04/07b1596fa58349428aee43f2aab2a28d/manifest.json；source_code/pipeline/stages/recognize.py:30,196–205。 |
| prompts/table.md | 没有真实 model_calls.prompt_name=table。 | python tools/check_assets.py；python -m pytest tests/test_engineering_extract.py -q 验证路由机制；真实正文回归 BLOCKED：没有已取证的 table_page 输入与同哈希真实输出。 | source_code/prompts/table.md:1；source_code/pipeline/stages/recognize.py:30,200–228；表二 manifest 全量扫描。 |
| prompts/figure.md | 没有真实 model_calls.prompt_name=figure。 | python tools/check_assets.py；python -m pytest tests/test_engineering_extract.py -q 验证路由机制；真实正文回归 BLOCKED：没有已取证的 text_page_with_figure 输入与同哈希真实输出。 | source_code/prompts/figure.md:1；source_code/pipeline/stages/recognize.py:31,200–219；表二 manifest 全量扫描。 |
| prompts/formula.md | 没有真实 model_calls.prompt_name=formula。 | python tools/check_assets.py；python -m pytest tests/test_engineering_extract.py -q 验证路由机制；真实正文回归 BLOCKED：没有已取证的 formula_page 输入与同哈希真实输出。 | source_code/prompts/formula.md:1；source_code/pipeline/stages/recognize.py:31,200–219；表二 manifest 全量扫描。 |
| prompts/normalize.md | 没有真实 normalize 阶段或同名调用。 | python tools/check_assets.py；凭据与接口解阻后 python main.py run ../materials/ges_docs_MED-04.pdf --material-id G1-NORMALIZE --stage normalize，并核对该文件当前哈希及 document_ir.json；当前真实回归 BLOCKED。 | source_code/prompts/normalize.md:1；source_code/pipeline/stages/normalize.py:18–27；表二全部真实 manifest。 |
| prompts/evidence.md | 没有真实 evidence 阶段或同名调用。 | python tools/check_assets.py；前置阶段解阻后 python main.py run ../materials/ges_docs_MED-04.pdf --material-id G1-EVIDENCE --stage evidence，并核对当前哈希与 evidence.json；当前真实回归 BLOCKED。 | source_code/prompts/evidence.md:1；source_code/pipeline/stages/evidence.py:18–27；表二全部真实 manifest。 |
| prompts/writer.md | 没有真实 write 阶段或 writer 调用。 | python tools/check_assets.py；translate 与前置解阻后 python main.py run ../materials/ges_docs_MED-04.pdf --material-id G1-WRITE --stage write，核对当前哈希、draft_en.md、draft.md；当前 BLOCKED。 | source_code/prompts/writer.md:1；source_code/pipeline/writer.py:40–64；表二全部真实 manifest。 |
| prompts/repair.md | 没有真实 repair 阶段或 repair 调用；replay 的 repair=ok 且 model_calls=[]，没有加载 repair prompt 的证明。 | python tools/check_assets.py；python -m pytest tests/test_engineering_write_repair.py -q 验证错误驱动与轮次机制；真实正文回归 BLOCKED：须在获准的测试目录构造 error issue 后真实触发修复，单纯 all 或 repair=ok 不保证 load_prompt("repair") 被执行。 | source_code/prompts/repair.md:1；source_code/pipeline/repair.py:73–108；source_code/result/runs/DEMO-01/2e2cd89a39b64a17984c8397bab381fc/manifest.json。 |
| prompts/translate.md（缺失） | 无文件；真实无调用；只有 demo 临时 stub 被回放消费。 | python tools/check_assets.py 显式给 missing；BLOCKED：由 B 按最终双签契约交付，或 A/B 正式调整写作依赖后另卡实现，再运行 G1-WRITE 命令。 | source_code/pipeline/writer.py:56–58；docs/接口冻结清单_A-B.md:170；source_code/main.py:62–68。 |
| fixtures/quality/fake_citation | 非模型阶段输入；无真实 provider 消费记录；离线临时副本执行，actual 含 CITE_NOT_IN_EVIDENCE、SCHEMA_INVALID。 | python -m pytest tests/test_engineering_fixtures.py -q -s -k fake_citation；可验证 expected/must_not 与源文件不变，不证明 schema 整体合规。 | source_code/fixtures/quality/fake_citation/expect.json；source_code/tests/test_engineering_fixtures.py:33–57；补充测试原文。 |
| fixtures/quality/evidence_unreferenced | 非模型阶段输入；无真实 provider 消费记录；离线 actual 含 EVIDENCE_UNREFERENCED、MD_MISSING_SECTION、SCHEMA_INVALID。 | python -m pytest tests/test_engineering_fixtures.py -q -s -k evidence_unreferenced；可验证既有 fixture 契约。 | source_code/fixtures/quality/evidence_unreferenced/expect.json；source_code/tests/test_engineering_fixtures.py:33–57；补充测试原文。 |
| fixtures/quality/number_mismatch | 非模型阶段输入；无真实 provider 消费记录；离线 actual 含 NUMBER_MISMATCH、SCHEMA_INVALID。 | python -m pytest tests/test_engineering_fixtures.py -q -s -k number_mismatch；可验证既有 fixture 契约。 | source_code/fixtures/quality/number_mismatch/expect.json；source_code/tests/test_engineering_fixtures.py:33–57；补充测试原文。 |
| fixtures/quality/unit_missing | 非模型阶段输入；无真实 provider 消费记录；离线 actual 含 UNIT_MISSING、SCHEMA_INVALID。 | python -m pytest tests/test_engineering_fixtures.py -q -s -k unit_missing；可验证既有 fixture 契约。 | source_code/fixtures/quality/unit_missing/expect.json；source_code/tests/test_engineering_fixtures.py:33–57；补充测试原文。 |
| fixtures/quality/material_mismatch | 非模型阶段输入；无真实 provider 消费记录；离线 actual 含 MATERIAL_MISMATCH、SCHEMA_INVALID。 | python -m pytest tests/test_engineering_fixtures.py -q -s -k material_mismatch；可验证既有 fixture 契约。 | source_code/fixtures/quality/material_mismatch/expect.json；source_code/tests/test_engineering_fixtures.py:33–57；补充测试原文。 |

## 四、§12 双签准备表

以下均为待人工核对的独立条目，空框不代表签字；原 §12 的 A/B 勾选与 commit 保持原状。每条仅列一个可勾选动作；原表 10 项 B 栏均为空，只有 I6/I7 的 A 栏有勾，正式双签缺口为 G1-B09。

| 接口 | 待勾选的具体条目 | 证据 |
|---|---|---|
| I1 | ☐ 用四个 CLI 命令的实际帮助输出逐字核对参数、--stage 前置语义及 --json。 | source_code/main.py:19–39；docs/接口冻结清单_A-B.md:30–48。 |
| I1 | ☐ 用同一输入复跑证实复用时只将已校验前置阶段记为 skipped。 | source_code/main.py:47–57；source_code/pipeline/orchestrator.py:101–129。 |
| I1 | ☐ 核对缺省 material_id 的推导 warning 与失败的非零退出码。 | source_code/main.py:42–44,127–132；docs/接口冻结清单_A-B.md:45–48。 |
| I2 | ☐ 将一份真实全链路 run 的文件逐个与 I2 树比对。 | docs/接口冻结清单_A-B.md:55–95；source_code/result/runs/。 |
| I2 | ☐ 核对失败 run 保留中间文件且 final.md 不存在。 | source_code/pipeline/repair.py:137–139；表二真实 manifest 路径。 |
| I2 | ☐ 核对 A19 的三项必产物和按需视觉产物登记。 | source_code/pipeline/stages/recognize.py:23,184–219；docs/接口冻结清单_A-B.md:89–95。 |
| I3 | ☐ 逐文件核对七份 schema 的 $id、schema_version 与非空 required。 | source_code/schemas/；source_code/pipeline/schema_loader.py:21–36,90–115。 |
| I3 | ☐ 按实际 additionalProperties=false 与七份落盘状态修订旧备注。 | docs/接口冻结清单_A-B.md:117,532；source_code/schemas/。 |
| I3 | ☐ 由 B 核对字段片段和现行版本后填入对应变更 commit。 | docs/接口冻结清单_A-B.md:121–145,518–521。 |
| I4 | ☐ 逐项决定 vision/table/figure/formula/normalize/evidence/writer/translate/repair 的最终文件集合。 | docs/接口冻结清单_A-B.md:165–171；source_code/pipeline/stages/recognize.py:30–31；source_code/pipeline/writer.py:44–56。 |
| I4 | ☐ 单独决定 Skill 的 frontmatter 版本来源与缺版本 warning 规则。 | source_code/skills/pdf-vision/SKILL.md:1–4；source_code/pipeline/prompt_loader.py:40–46；docs/接口冻结清单_A-B.md:197。 |
| I4 | ☐ 单独决定空白 Skill 是否必须产生 ASSET_EMPTY。 | source_code/pipeline/prompt_loader.py:40–46；docs/接口冻结清单_A-B.md:196。 |
| I4 | ☐ 核对新的真实请求中 SkillInput 只传路径且提示词以 TextInput 交付。 | source_code/pipeline/codex_adapter.py:168–180；source_code/pipeline/stages/recognize.py:211–215。 |
| I5 | ☐ 核对真实 thread_start 的 sandbox=read_only 与 ephemeral=True。 | source_code/pipeline/codex_adapter.py:224–238；docs/接口冻结清单_A-B.md:226–228。 |
| I5 | ☐ 逐个模型调用核对同一 run 的 Codex 实例复用和关闭时机。 | source_code/pipeline/writer.py:29–37；source_code/pipeline/stages/_shared.py:148–164。 |
| I5 | ☐ 核对 timeout 的 interrupt 证据以及重试由 SDK 承担的调用路径。 | source_code/pipeline/codex_adapter.py:216–244,354–359；docs/接口冻结清单_A-B.md:254–269。 |
| I6 | ☐ 读取实际 validation.json 核对 issues 对象、checks 两数组与 valid 的 error 规则。 | source_code/pipeline/validate.py:582–598；source_code/schemas/validation.schema.json。 |
| I6 | ☐ 对 A14-1 的缺输入失败与 skipped 语义逐句定案。 | docs/接口冻结清单_A-B.md:330–335；source_code/pipeline/validate.py:574–595。 |
| I6 | ☐ 核对 repair 请求只携带 error issue 的 code 与受限修改约束。 | source_code/pipeline/repair.py:89–105；docs/接口冻结清单_A-B.md:380–382。 |
| I7 | ☐ 分别复算 AUTH_MISSING→2、validate invalid→4 和 ASSET_MISSING→8。 | source_code/pipeline/errors.py:173–190；source_code/main.py:95–107。 |
| I7 | ☐ 核对任意阶段 failed 均向 CLI 传播为非零退出码。 | source_code/pipeline/orchestrator.py:124–129；source_code/main.py:127–132。 |
| I8 | ☐ 复算一份新真实 manifest 的 prompts/skills/schemas 三组哈希。 | docs/接口冻结清单_A-B.md:432–434,455；source_code/pipeline/manifest.py:32。 |
| I8 | ☐ 核对 commit、SDK/二进制版本、calls、deps 和 cache.key 都有真实来源。 | docs/接口冻结清单_A-B.md:431–444,456–457；source_code/pipeline/manifest.py:31–40。 |
| I8 | ☐ 核对 config 每项来源及 manifest 无密钥、完整端点或用户绝对路径。 | source_code/pipeline/stages/_shared.py:50–103；docs/接口冻结清单_A-B.md:450–458。 |
| I9 | ☐ 用扩展阶段注册测试核对依赖拓扑与必产物缺失失败。 | source_code/pipeline/orchestrator.py:36–55,58–121；source_code/tests/test_engineering_orchestrator.py。 |
| I9 | ☐ 核对阶段返回的路径产物确已写盘且不靠跨阶段原始文本缓冲。 | source_code/pipeline/orchestrator.py:105–123；docs/接口冻结清单_A-B.md:473–475。 |
| I10 | ☐ 逐一复跑五类质量夹具并保留 expected、actual、must_not 的结果。 | source_code/tests/test_engineering_fixtures.py:16–55；source_code/fixtures/quality/。 |
| I10 | ☐ 对缺失夹具采取失败还是 skipped 作出逐字约定。 | docs/接口冻结清单_A-B.md:509；source_code/tests/test_engineering_fixtures.py:27–35。 |
| I10 | ☐ 复算测试前后夹具哈希并将额外 SCHEMA_INVALID 列为独立事实。 | source_code/tests/test_engineering_fixtures.py:49–57；补充测试输出原文。 |

## 阻塞项与最小动作

1. **G1-B01：提示词集合漂移且 translate 缺失。** 由 A/B 逐个对齐 I4 的六项声明、八个现存文件和写作依赖，B 交付契约要求的 translate 或双方另卡正式变更依赖；本卡不造文件（docs/接口冻结清单_A-B.md:165–171,533；source_code/pipeline/writer.py:56）。
2. **G1-B02：Skill 的版本和空白语义未统一。** A/B 明确 frontmatter 的版本来源、ASSET_NO_VERSION 与 ASSET_EMPTY 是否适用于 Skill，再由 A 在另卡实施与测试；本卡保留实际差异（source_code/skills/pdf-vision/SKILL.md:1–4；source_code/pipeline/prompt_loader.py:40–46；docs/接口冻结清单_A-B.md:196–198）。
3. **G1-B03：Schema/validation 状态备注过期。** 文档责任方按七份落盘 schema 及现有 issues/checks 更新第 117、532、535 行等旧备注，再走变更流程；本卡仅引用（docs/接口冻结清单_A-B.md:117,532,535；source_code/schemas/；source_code/pipeline/validate.py:589–595）。
4. **G1-B04：CLI 输出、缺省编号提示与断点复用不符。** 由 A/B 选定实际交付语义后，另卡补齐 --json/人读输出、warning 与复用逻辑或正式修改相应契约（docs/接口冻结清单_A-B.md:40–48；source_code/main.py:19–57,125；source_code/pipeline/orchestrator.py:101–129）。
5. **G1-B05：Codex 生命周期与重试策略不符。** 另卡落实 I5 的 run 内实例复用及 SDK 退避，或由双方正式变更这两条约定并补生命周期/失败证据（docs/接口冻结清单_A-B.md:254–269；source_code/pipeline/stages/_shared.py:148–164；source_code/pipeline/codex_adapter.py:354–359）。
6. **G1-B06：Manifest 资产/过程汇总缺失。** 另卡填充 I8 的 assets、commit、二进制版本、calls/deps/cache.key，并让新真实 run 的三组资产哈希可逐个复算（docs/接口冻结清单_A-B.md:425–458；source_code/pipeline/manifest.py:26–40；source_code/pipeline/stages/_shared.py:108–132）。
7. **G1-B07：质量夹具缺失时的状态契约不符。** A/B 在 assert 失败与文档的 skipped 语义之间定案，另卡同步代码/契约/测试；额外 SCHEMA_INVALID 的具体输入归因留给 B 与 schema 责任方，不在本卡改资产或放宽断言（docs/接口冻结清单_A-B.md:507–510；source_code/tests/test_engineering_fixtures.py:27–57）。
8. **G1-B08：真实全链路与当前资产组合没有成功证据。** 获取可用凭据并重新取证 auth（存档 auth.ok=false 不能替代实时探测），先完成当前资产的真实 normalize→evidence→write→repair 与视觉分支回归，保留 model_calls、raw、校验报告和 final.md；历史旧 vision 或 replay 不替代（source_code/provider_capabilities.json:12–13；表二全部真实 manifest；docs/A_开发提示词方案.md:774–775）。
9. **G1-B09：I1–I10 未双签。** 双方逐条完成表四后亲自补 §12 的勾选与冻结 commit；本卡不签字（docs/接口冻结清单_A-B.md:528–541）。
10. **G1-B10：D2/D3 发布证明缺失。** 完成 A10/A13/A15/A16 所需真实运行、四份独立 final、ZIP 读回和干净环境复现，并由对应卡产出 e2e-run-1.md、four-material-run.md、clean-env-demo.md；当前三份文档不存在且真实 run 无 final（docs/A_开发提示词方案.md:612–630,659–703,775–776；表二；docs/ 文件存在性扫描）。
11. **G1-B11：本地验收环境与干净工作区门槛未满足。** 在依赖完整的 Python 环境按既有 requirements-lock.txt 配置 openai-codex 后重跑四命令，再由用户处置本卡两个新增文件后在干净工作区重跑 verify；本卡不安装依赖、不提交、不改 clean 或 PASS 判定（source_code/tests/test_engineering_faults.py:17；source_code/requirements-lock.txt；source_code/tools/verify.py:35–60,143–160；验收原文）。

## 执行记录与复核边界

### 改动前基线（本轮写入前）

命令：python tools/verify.py；退出码 1。设置 PYTHONIOENCODING=utf-8 仅用于避免 Windows GBK 控制台编码异常，不改变项目判定；默认 Python 为 D:/Anaconda3/python.exe。此前未设置编码的调用曾在输出 JSON 时触发 UnicodeEncodeError，该次输出不作为完整 JSON 报告。

```text
verify: FAIL; failed=pytest
{"clean": {"nonzero_count": 0, "ok": true, "status_returncode": 0, "status_stderr": "", "tracked_dirty": [], "tracked_dirty_count": 0, "untracked": [], "untracked_count": 0}, "demo": {"han_chars": 2692, "ok": true, "parse_error": null, "returncode": 0, "stderr_tail": "模型输出为回放桩，仅验证链路，不代表识别质量\n", "stdout_tail": "{\n  \"run_id\": \"06aecabc06734e66b56ac733d5d6e101\",\n  \"run_dir\": \"result\\\\runs\\\\DEMO-01\\\\06aecabc06734e66b56ac733d5d6e101\",\n  \"stages\": [\n    {\n      \"name\": \"recognize\",\n      \"status\": \"ok\",\n      \"duration_ms\": 754.8067000025185,\n      \"artifacts\": {\n        \"page_text\": \"result\\\\runs\\\\DEMO-01\\\\06aecabc06734e66b56ac733d5d6e101\\\\page_text.json\",\n        \"page_plan\": \"result\\\\runs\\\\DEMO-01\\\\06aecabc06734e66b56ac733d5d6e101\\\\page_plan.json\",\n        \"recognition\": \"result\\\\runs\\\\DEMO-01\\\\06aecabc06734e66b56ac733d5d6e101\\\\recognition.json\"\n      },\n      \"issues\": []\n    },\n    {\n      \"name\": \"normalize\",\n      \"status\": \"ok\",\n      \"duration_ms\": 14.333699997223448,\n      \"artifacts\": {\n        \"document_ir\": \"result\\\\runs\\\\DEMO-01\\\\06aecabc06734e66b56ac733d5d6e101\\\\document_ir.json\"\n      },\n      \"issues\": []\n    },\n    {\n      \"name\": \"evidence\",\n      \"status\": \"ok\",\n      \"duration_ms\": 25.435299998207483,\n      \"artifacts\": {\n        \"evidence\": \"result\\\\runs\\\\DEMO-01\\\\06aecabc06734e66b56ac733d5d6e101\\\\evidence.json\"\n      },\n      \"issues\": []\n    },\n    {\n      \"name\": \"write\",\n      \"status\": \"ok\",\n      \"duration_ms\": 5.655100001604296,\n      \"artifacts\": {\n        \"draft_en\": \"result\\\\runs\\\\DEMO-01\\\\06aecabc06734e66b56ac733d5d6e101\\\\draft_en.md\",\n        \"draft\": \"result\\\\runs\\\\DEMO-01\\\\06aecabc06734e66b56ac733d5d6e101\\\\draft.md\"\n      },\n      \"issues\": []\n    },\n    {\n      \"name\": \"repair\",\n      \"status\": \"ok\",\n      \"duration_ms\": 89.85100000427337,\n      \"artifacts\": {\n        \"final\": \"result\\\\runs\\\\DEMO-01\\\\06aecabc06734e66b56ac733d5d6e101\\\\final.md\"\n      },\n      \"issues\": []\n    }\n  ],\n  \"valid\": true,\n  \"han_chars\": 2692,\n  \"issues\": []\n}\n", "valid": true}, "head": {"head": "69f693f5ed76202e8c8b0006233ee37b554b6e94", "ok": true, "returncode": 0, "stderr": ""}, "materials": {"count": 4, "directory": "D:\\桌面\\金砖大赛\\preselect\\7_视觉源码\\materials", "errors": [], "files": [{"bytes": 2294477, "name": "ges_docs_AI-01.pdf", "sha256": "52e7ad2740b25cc7697b69e12d69ca0dde844eca9f79dfa304a79be5b0e99c1a"}, {"bytes": 1813475, "name": "ges_docs_BIO-02.pdf", "sha256": "2e7c63cea244c7fe2e989c3633dd81d4cac9e4714c39d828482821434c43ec7c"}, {"bytes": 5926866, "name": "ges_docs_IND-03.pdf", "sha256": "8033208a33b08a971ad04d287d8c5b99e4b2b75f52382d0d97624e64593b5d34"}, {"bytes": 1396559, "name": "ges_docs_MED-04.pdf", "sha256": "95e32e78f76efc7854852de4c5fbb299fc239f151bdddf852b502342351fcad0"}], "ok": true}, "pytest": {"errors": 1, "failed": 0, "ok": false, "passed": 0, "returncode": 2, "stderr_tail": "", "stdout_tail": "\n=================================== ERRORS ====================================\n______________ ERROR collecting tests/test_engineering_faults.py ______________\nImportError while importing test module 'D:\\桌面\\金砖大赛\\preselect\\7_视觉源码\\source_code\\tests\\test_engineering_faults.py'.\nHint: make sure your test modules/packages have valid Python names.\nTraceback:\nD:\\Anaconda3\\Lib\\importlib\\__init__.py:88: in import_module\n    return _bootstrap._gcd_import(name[level:], package, level)\ntests\\test_engineering_faults.py:17: in <module>\n    import openai_codex\nE   ModuleNotFoundError: No module named 'openai_codex'\n=========================== short test summary info ===========================\nERROR tests/test_engineering_faults.py\n!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!\n1 error in 0.31s\n", "xfailed": 0}}
```

### 质量夹具补充检查

命令：python -m pytest tests/test_engineering_fixtures.py -q -s；退出码 0。以下为原文；6 项由一个 inventory 测试和五个 case 组成，额外 SCHEMA_INVALID 没有被隐藏。

```text
.evidence_unreferenced -> expected=['EVIDENCE_UNREFERENCED'] -> actual=['EVIDENCE_UNREFERENCED', 'MD_MISSING_SECTION', 'SCHEMA_INVALID'] -> skipped=[]
.fake_citation -> expected=['CITE_NOT_IN_EVIDENCE'] -> actual=['CITE_NOT_IN_EVIDENCE', 'SCHEMA_INVALID'] -> skipped=[]
.material_mismatch -> expected=['MATERIAL_MISMATCH'] -> actual=['MATERIAL_MISMATCH', 'SCHEMA_INVALID'] -> skipped=[]
.number_mismatch -> expected=['NUMBER_MISMATCH'] -> actual=['NUMBER_MISMATCH', 'SCHEMA_INVALID'] -> skipped=[]
.unit_missing -> expected=['UNIT_MISSING'] -> actual=['SCHEMA_INVALID', 'UNIT_MISSING'] -> skipped=[]
.
6 passed in 0.32s
```

### 验收前闸门

命令：python tools/verify.py；退出码 1。按 AGENTS.md 在正式四命令之前单独执行，以下保留完整 JSON。

```text
verify: FAIL; failed=clean,pytest
{"clean": {"nonzero_count": 2, "ok": false, "status_returncode": 0, "status_stderr": "", "tracked_dirty": [], "tracked_dirty_count": 0, "untracked": ["\"7_\\350\\247\\206\\350\\247\\211\\346\\272\\220\\347\\240\\201/docs/handoff-gate.md\"", "\"7_\\350\\247\\206\\350\\247\\211\\346\\272\\220\\347\\240\\201/source_code/tools/check_assets.py\""], "untracked_count": 2}, "demo": {"han_chars": 2692, "ok": true, "parse_error": null, "returncode": 0, "stderr_tail": "模型输出为回放桩，仅验证链路，不代表识别质量\n", "stdout_tail": "{\n  \"run_id\": \"a1e66b40e48a455c9e7acad7876c07d3\",\n  \"run_dir\": \"result\\\\runs\\\\DEMO-01\\\\a1e66b40e48a455c9e7acad7876c07d3\",\n  \"stages\": [\n    {\n      \"name\": \"recognize\",\n      \"status\": \"ok\",\n      \"duration_ms\": 773.0035999993561,\n      \"artifacts\": {\n        \"page_text\": \"result\\\\runs\\\\DEMO-01\\\\a1e66b40e48a455c9e7acad7876c07d3\\\\page_text.json\",\n        \"page_plan\": \"result\\\\runs\\\\DEMO-01\\\\a1e66b40e48a455c9e7acad7876c07d3\\\\page_plan.json\",\n        \"recognition\": \"result\\\\runs\\\\DEMO-01\\\\a1e66b40e48a455c9e7acad7876c07d3\\\\recognition.json\"\n      },\n      \"issues\": []\n    },\n    {\n      \"name\": \"normalize\",\n      \"status\": \"ok\",\n      \"duration_ms\": 14.493599999696016,\n      \"artifacts\": {\n        \"document_ir\": \"result\\\\runs\\\\DEMO-01\\\\a1e66b40e48a455c9e7acad7876c07d3\\\\document_ir.json\"\n      },\n      \"issues\": []\n    },\n    {\n      \"name\": \"evidence\",\n      \"status\": \"ok\",\n      \"duration_ms\": 17.38370000384748,\n      \"artifacts\": {\n        \"evidence\": \"result\\\\runs\\\\DEMO-01\\\\a1e66b40e48a455c9e7acad7876c07d3\\\\evidence.json\"\n      },\n      \"issues\": []\n    },\n    {\n      \"name\": \"write\",\n      \"status\": \"ok\",\n      \"duration_ms\": 5.281399993691593,\n      \"artifacts\": {\n        \"draft_en\": \"result\\\\runs\\\\DEMO-01\\\\a1e66b40e48a455c9e7acad7876c07d3\\\\draft_en.md\",\n        \"draft\": \"result\\\\runs\\\\DEMO-01\\\\a1e66b40e48a455c9e7acad7876c07d3\\\\draft.md\"\n      },\n      \"issues\": []\n    },\n    {\n      \"name\": \"repair\",\n      \"status\": \"ok\",\n      \"duration_ms\": 90.67579999828013,\n      \"artifacts\": {\n        \"final\": \"result\\\\runs\\\\DEMO-01\\\\a1e66b40e48a455c9e7acad7876c07d3\\\\final.md\"\n      },\n      \"issues\": []\n    }\n  ],\n  \"valid\": true,\n  \"han_chars\": 2692,\n  \"issues\": []\n}\n", "valid": true}, "head": {"head": "69f693f5ed76202e8c8b0006233ee37b554b6e94", "ok": true, "returncode": 0, "stderr": ""}, "materials": {"count": 4, "directory": "D:\\桌面\\金砖大赛\\preselect\\7_视觉源码\\materials", "errors": [], "files": [{"bytes": 2294477, "name": "ges_docs_AI-01.pdf", "sha256": "52e7ad2740b25cc7697b69e12d69ca0dde844eca9f79dfa304a79be5b0e99c1a"}, {"bytes": 1813475, "name": "ges_docs_BIO-02.pdf", "sha256": "2e7c63cea244c7fe2e989c3633dd81d4cac9e4714c39d828482821434c43ec7c"}, {"bytes": 5926866, "name": "ges_docs_IND-03.pdf", "sha256": "8033208a33b08a971ad04d287d8c5b99e4b2b75f52382d0d97624e64593b5d34"}, {"bytes": 1396559, "name": "ges_docs_MED-04.pdf", "sha256": "95e32e78f76efc7854852de4c5fbb299fc239f151bdddf852b502342351fcad0"}], "ok": true}, "pytest": {"errors": 1, "failed": 0, "ok": false, "passed": 0, "returncode": 2, "stderr_tail": "", "stdout_tail": "\n=================================== ERRORS ====================================\n______________ ERROR collecting tests/test_engineering_faults.py ______________\nImportError while importing test module 'D:\\桌面\\金砖大赛\\preselect\\7_视觉源码\\source_code\\tests\\test_engineering_faults.py'.\nHint: make sure your test modules/packages have valid Python names.\nTraceback:\nD:\\Anaconda3\\Lib\\importlib\\__init__.py:88: in import_module\n    return _bootstrap._gcd_import(name[level:], package, level)\ntests\\test_engineering_faults.py:17: in <module>\n    import openai_codex\nE   ModuleNotFoundError: No module named 'openai_codex'\n=========================== short test summary info ===========================\nERROR tests/test_engineering_faults.py\n!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!\n1 error in 0.32s\n", "xfailed": 0}}
```

### 四条验收命令（按列出顺序逐条执行）

统一工作目录：source_code/。PowerShell 中仅设置 PYTHONIOENCODING=utf-8，并用 $LASTEXITCODE 保留子进程退出码；没有切换 Python、安装依赖或改变测试参数。

#### 1. python -m pytest -q

退出码：2。stdout/stderr 工具返回原文如下。

```text

=================================== ERRORS ====================================
______________ ERROR collecting tests/test_engineering_faults.py ______________
ImportError while importing test module 'D:\桌面\金砖大赛\preselect\7_视觉源码\source_code\tests\test_engineering_faults.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
D:\Anaconda3\Lib\importlib\__init__.py:88: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
tests\test_engineering_faults.py:17: in <module>
    import openai_codex
E   ModuleNotFoundError: No module named 'openai_codex'
=========================== short test summary info ===========================
ERROR tests/test_engineering_faults.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.31s
```

#### 2. python tools/verify.py

退出码：1。stdout/stderr 工具返回原文如下。

```text
verify: FAIL; failed=clean,pytest
{"clean": {"nonzero_count": 2, "ok": false, "status_returncode": 0, "status_stderr": "", "tracked_dirty": [], "tracked_dirty_count": 0, "untracked": ["\"7_\\350\\247\\206\\350\\247\\211\\346\\272\\220\\347\\240\\201/docs/handoff-gate.md\"", "\"7_\\350\\247\\206\\350\\247\\211\\346\\272\\220\\347\\240\\201/source_code/tools/check_assets.py\""], "untracked_count": 2}, "demo": {"han_chars": 2692, "ok": true, "parse_error": null, "returncode": 0, "stderr_tail": "模型输出为回放桩，仅验证链路，不代表识别质量\n", "stdout_tail": "{\n  \"run_id\": \"7adf05f3ba57481da50be837d6e2e7d3\",\n  \"run_dir\": \"result\\\\runs\\\\DEMO-01\\\\7adf05f3ba57481da50be837d6e2e7d3\",\n  \"stages\": [\n    {\n      \"name\": \"recognize\",\n      \"status\": \"ok\",\n      \"duration_ms\": 772.1498999962932,\n      \"artifacts\": {\n        \"page_text\": \"result\\\\runs\\\\DEMO-01\\\\7adf05f3ba57481da50be837d6e2e7d3\\\\page_text.json\",\n        \"page_plan\": \"result\\\\runs\\\\DEMO-01\\\\7adf05f3ba57481da50be837d6e2e7d3\\\\page_plan.json\",\n        \"recognition\": \"result\\\\runs\\\\DEMO-01\\\\7adf05f3ba57481da50be837d6e2e7d3\\\\recognition.json\"\n      },\n      \"issues\": []\n    },\n    {\n      \"name\": \"normalize\",\n      \"status\": \"ok\",\n      \"duration_ms\": 14.295199995103758,\n      \"artifacts\": {\n        \"document_ir\": \"result\\\\runs\\\\DEMO-01\\\\7adf05f3ba57481da50be837d6e2e7d3\\\\document_ir.json\"\n      },\n      \"issues\": []\n    },\n    {\n      \"name\": \"evidence\",\n      \"status\": \"ok\",\n      \"duration_ms\": 17.912499999511056,\n      \"artifacts\": {\n        \"evidence\": \"result\\\\runs\\\\DEMO-01\\\\7adf05f3ba57481da50be837d6e2e7d3\\\\evidence.json\"\n      },\n      \"issues\": []\n    },\n    {\n      \"name\": \"write\",\n      \"status\": \"ok\",\n      \"duration_ms\": 5.731400000513531,\n      \"artifacts\": {\n        \"draft_en\": \"result\\\\runs\\\\DEMO-01\\\\7adf05f3ba57481da50be837d6e2e7d3\\\\draft_en.md\",\n        \"draft\": \"result\\\\runs\\\\DEMO-01\\\\7adf05f3ba57481da50be837d6e2e7d3\\\\draft.md\"\n      },\n      \"issues\": []\n    },\n    {\n      \"name\": \"repair\",\n      \"status\": \"ok\",\n      \"duration_ms\": 88.46359999733977,\n      \"artifacts\": {\n        \"final\": \"result\\\\runs\\\\DEMO-01\\\\7adf05f3ba57481da50be837d6e2e7d3\\\\final.md\"\n      },\n      \"issues\": []\n    }\n  ],\n  \"valid\": true,\n  \"han_chars\": 2692,\n  \"issues\": []\n}\n", "valid": true}, "head": {"head": "69f693f5ed76202e8c8b0006233ee37b554b6e94", "ok": true, "returncode": 0, "stderr": ""}, "materials": {"count": 4, "directory": "D:\\桌面\\金砖大赛\\preselect\\7_视觉源码\\materials", "errors": [], "files": [{"bytes": 2294477, "name": "ges_docs_AI-01.pdf", "sha256": "52e7ad2740b25cc7697b69e12d69ca0dde844eca9f79dfa304a79be5b0e99c1a"}, {"bytes": 1813475, "name": "ges_docs_BIO-02.pdf", "sha256": "2e7c63cea244c7fe2e989c3633dd81d4cac9e4714c39d828482821434c43ec7c"}, {"bytes": 5926866, "name": "ges_docs_IND-03.pdf", "sha256": "8033208a33b08a971ad04d287d8c5b99e4b2b75f52382d0d97624e64593b5d34"}, {"bytes": 1396559, "name": "ges_docs_MED-04.pdf", "sha256": "95e32e78f76efc7854852de4c5fbb299fc239f151bdddf852b502342351fcad0"}], "ok": true}, "pytest": {"errors": 1, "failed": 0, "ok": false, "passed": 0, "returncode": 2, "stderr_tail": "", "stdout_tail": "\n=================================== ERRORS ====================================\n______________ ERROR collecting tests/test_engineering_faults.py ______________\nImportError while importing test module 'D:\\桌面\\金砖大赛\\preselect\\7_视觉源码\\source_code\\tests\\test_engineering_faults.py'.\nHint: make sure your test modules/packages have valid Python names.\nTraceback:\nD:\\Anaconda3\\Lib\\importlib\\__init__.py:88: in import_module\n    return _bootstrap._gcd_import(name[level:], package, level)\ntests\\test_engineering_faults.py:17: in <module>\n    import openai_codex\nE   ModuleNotFoundError: No module named 'openai_codex'\n=========================== short test summary info ===========================\nERROR tests/test_engineering_faults.py\n!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!\n1 error in 0.32s\n", "xfailed": 0}}
```

#### 3. python tools/check_assets.py

退出码：8。stdout/stderr 工具返回原文如下。

```text
{"doc_mismatch": [{"code": "ASSET_MISSING", "declared_only": ["vision"], "kind": "i4_vs_literal_prompt_calls", "message": "I4 declaration differs from the observed set", "observed_only": [], "path": "docs/接口冻结清单_A-B.md"}, {"code": "ASSET_MISSING", "declared_only": ["translate"], "kind": "i4_vs_prompt_files", "message": "I4 declaration differs from the observed set", "observed_only": ["figure", "formula", "table"], "path": "docs/接口冻结清单_A-B.md"}], "extra": ["figure", "formula", "table", "vision"], "missing": ["translate"], "ok": false, "version_issues": [{"code": "ASSET_NO_VERSION", "contract": "docs/接口冻结清单_A-B.md", "line": 1, "loader": "source_code/pipeline/prompt_loader.py", "message": "SKILL.md 首行是 YAML frontmatter、load_skill 无版本检查", "path": "source_code/skills/pdf-vision/SKILL.md"}]}
```

#### 4. git status --porcelain

退出码：0。stdout/stderr 工具返回原文如下。

```text
?? "7_\350\247\206\350\247\211\346\272\220\347\240\201/docs/handoff-gate.md"
?? "7_\350\247\206\350\247\211\346\272\220\347\240\201/source_code/tools/check_assets.py"
```

### 确定性 Python 复核

另以 python - 执行 SHA-256/JSON 断言：输入为写入前捕获的 68 个文件哈希与写入前后 verify 原始 JSON；重新读取现存文件并断言逐个哈希相等，比较 pytest 的 returncode/passed/failed/xfailed/errors/ok、demo 的 ok/valid/han_chars/returncode 和 materials 全对象，断言只新增两项 untracked 且 tracked_dirty_count=0。退出码 0，原文：

```json
{"changed_files": [], "checked_existing_files": 68, "clean_after": false, "clean_before": true, "demo_result_unchanged": true, "materials_unchanged": true, "new_untracked_files": 2, "pytest_counts_and_status_unchanged": true}
```

68 个文件覆盖 source_code/prompts/、source_code/skills/、source_code/fixtures/quality/ 全部文件，以及 pipeline/**/*.py、七份 schema、tools/verify.py 和 docs/接口冻结清单_A-B.md。Python 同时对检查脚本做了内存中的字面量正则、严格版本和五个顶层键断言；没有创建测试资产或依赖。

基线与本轮 pytest 都在收集阶段因 openai_codex 缺失终止，返回 2；verify 本身均返回 1。demo 仍为回放，valid=true、han_chars=2692，四份材料哈希相同；verify 的失败项由 pytest 增加为 clean,pytest，因为本卡恰好新增两个未跟踪文件。不能把“既有行为未变”写成“验收完成”，也不能声称 verify 报告全文相同；run_id/计时与 clean 状态确有变化。

本轮只新增 docs/handoff-gate.md 与 source_code/tools/check_assets.py，无新增依赖、无 Git 提交；原有文件与签字内容保持原字节。由于工作区不干净，本卡只报告执行证据与阻塞事实，不作验收完成的结论。卡外修复、真实联网运行、发布打包和重建干净环境均未执行。


当前不可交付：共 11 项阻塞（G1-B01–G1-B11）；最小解阻动作是由责任方逐项消除上述契约和资产差异、补齐真实链路与发布证据、亲自完成双签，并在依赖完整且干净的工作区重跑四条验收命令后再决定能否将 B 的 Skill 并入验收。


---

## 二轮处置（2026-11-14 晚）：11 项阻塞的当前状态

本节由 A 在 G1 之后执行，逐项处置上表阻塞。判定标准不变：每项必须有文件路径、
命令或原始输出支撑。**未执行的项目不会写成已修。**

| 编号 | 状态 | 证据 |
|---|---|---|
| G1-B01 translate 缺失 | **仍阻断（B 侧）** | `tools/check_assets.py` 输出 `missing:["translate"]`；`pipeline/writer.py:56` 无条件 `load_prompt("translate")`；真实链路由此停在 `ASSET_MISSING`、退出码 8。I4 表已登记该文件为 B 待交项，A 未创建该文件 |
| G1-B02 Skill 版本语义 | **A 侧已修，B 补一行即完成** | `pipeline/prompt_loader.py` 新增 `_skill_version()`：frontmatter `version:` / `metadata.version:` / 首行 HTML 注释三种写法均可解析；空 Skill 抛 `AssetEmpty`。`model_calls[].skill_version` 记录取值，缺失记 `ASSET_NO_VERSION` warning。I4 冻结语义表已改为双轨制。测试 `tests/test_engineering_skill_version.py` 5 条 |
| G1-B03 Schema 旧备注 | **已修** | I3 表 evidence 升至 1.0.2；"现有 schema 为空壳""4 个文件都是 true"两处表述已按实际改写；§12 的 I3/I4 备注同步为事实 |
| G1-B04 CLI 契约 | **已修** | `main.py` 增加 `--json`（单行 JSON）与 `--resume`；缺省 `--material-id` 推导时向 stderr 打 warning；`orchestrator.run_stages(..., resume=True)` 按 manifest `ok` + 产物可解析双条件跳过。测试 `test_engineering_cli.py` 2 条 + `test_engineering_resume.py` 3 条 |
| G1-B05 Codex 生命周期与退避 | **未修，另卡** | `writer.py` / `recognize.py` 仍是每次 `get_provider` + `generate_and_close`；退避仍是 `time.sleep`。不影响 B 并入（B 不触 SDK），列为 A 后续卡 |
| G1-B06 Manifest 汇总 | **已修** | `pipeline/manifest.py` 新增 `asset_digests` / `code_provenance` / `dependency_versions` / `cache_key` / `call_counts`，`_read_manifest` 首次创建时 `enrich_manifest`。新 run 的 `assets.prompts/skills/schemas`、`code.git_commit`+`files`、`deps`、`calls`、`cache.key` 均非空，`tests/test_engineering_stages.py` 断言三组哈希可复算 |
| G1-B07 夹具缺失语义 | **已修** | `tests/test_engineering_fixtures.py` 改为 `pytest.skip` 并说明缺失项；`tools/verify.py` 新增 `quality_fixtures` 门禁（缺失或 `expect_codes` 为空则 FAIL） |
| G1-B08 真实链路 | **部分达成，断点已定位到 B 资产** | 真实 provider（`api.mixtk.com`，`gpt-6.1-sol`）下：`normalize` status=ok、issues 为空；`evidence` 首次因 `page_number [1,1]` 被 `uniqueItems` 拒绝，修正 schema 后 status=ok、issues 为空；`write` 精确失败于 `Required asset is missing: prompts/translate.md`、`ASSET_MISSING`、退出码 8。见下方命令输出 |
| G1-B09 未双签 | **仍阻断（B 侧）** | A 侧可勾选行已按证据补登，B 侧保持空白；不代签 |
| G1-B10 D2/D3 发布证明 | **仍缺** | 依赖 B08 完成后才能产出四份 `final.md`、ZIP 读回与干净环境演示；`docs/four-material-run.md`、`docs/clean-env-demo.md` 仍不存在 |
| G1-B11 环境与干净工作区 | **已修** | 四条命令改用 `venv/Scripts/python.exe`（`openai_codex` 可导入）；`verify.py` 新增 `interpreter` 诊断字段（executable / version / sdk_importable），避免"用错解释器"被误读为代码缺陷；本轮改动提交后重跑 |

### 真实链路原始输出（G1-B08）

```text
# normalize（真实 provider）
{"stage": "normalize", "status": "ok", "issues": []}

# evidence 第一次：被 schema 拒绝
{"error": "PipelineError", "code": "SCHEMA_INVALID",
 "message": "/facts/1/page_number:SCHEMA_INVALID; /facts/2/page_number:SCHEMA_INVALID; ..."}
  原始模型输出片段：{"page_number": [1, 1], "block_id": ["B013", "B014"], ...}

# evidence 第二次（evidence schema 1.0.2 移除 uniqueItems 后）
{"stage": "evidence", "status": "ok", "issues": []}

# write（B 资产缺失时的精确失败点）
{"error": "AssetMissing", "code": "ASSET_MISSING",
 "message": "Required asset is missing: prompts/translate.md", "exit_code": 8}
```

上述 `page_number [1,1]` 是本次唯一一处**由真实模型暴露、桩环境永远无法暴露**的契约缺陷：
`uniqueItems: true` 与该字段自述的"与 `block_id` 按索引对应"互相矛盾。修正已记入 I3 变更记录，
标注"待 B 确认"，未代签。

### 二轮判定

**可交付给 B，不可作为比赛终稿交付。**

- 可交付给 B：`skills/` 与 `prompts/` 的加载、哈希、版本、失败语义已全部就位；B 改一次 SKILL.md，
  新 run 的 `manifest.assets.skills` 与 `model_calls[].skill_version` 会同时变化，可逐项复算。
- 不可作为终稿：`translate.md` 未交（B01）、§12 未双签（B09）、四份 final/打包/干净环境未产出（B10）、
  Codex 生命周期未按 I5 复用（B05）。

B 侧最小动作两项：`prompts/translate.md`（首行 `<!-- version: x.y.z -->`，输入 `draft_en`，输出中文正文）
与 `skills/pdf-vision/SKILL.md` 的 frontmatter 增加 `version: 1.1.0`。两项到位后，`check_assets.py`
的 `missing` 与 `version_issues` 应同时为空，`write` 阶段可继续向下执行。
