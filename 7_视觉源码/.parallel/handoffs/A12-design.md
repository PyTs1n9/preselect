# A12 设计冻结：manifest 与缓存键（只读）

本 memo 只依据 `git show 1bbfce0:<path>` 的内容，不依据工作区正在进行的重构。
基准复核命令是：

```text
git show 1bbfce0:7_视觉源码/source_code/pipeline/manifest.py
git show 1bbfce0:7_视觉源码/source_code/pipeline/orchestrator.py
git show 1bbfce0:7_视觉源码/source_code/pipeline/settings.py
git show 1bbfce0:7_视觉源码/source_code/pipeline/stages/_shared.py
git show 1bbfce0:7_视觉源码/source_code/pipeline/codex_adapter.py
git show 1bbfce0:7_视觉源码/docs/接口冻结清单_A-B.md
```

工作区状态在开工时逐项记录，未 stash、清理或评价：README、config、main、codex_adapter、
orchestrator、pdf_input、schema_loader、settings、stages/recognize、三个测试文件被修改，
evidence.py 与 normalize.py 被删除；另有 START-A11、runner.py、tools/demo.py、tmp/pdf-review
及父目录 tmp 的未跟踪内容。当前状态共 26 行。它们不构成本 memo 的实现依据。

## 1. 现状表

| I8 字段 | 状态 | 1bbfce0 位置与证据 |
|---|---|---|
| `run_id`、`material_id` | 部分实现 | `manifest.py:26-29` 接收并初始化；没有验证格式、唯一性或重跑复用语义。 |
| `started_at` | 已实现（运行开始值） | `manifest.py:28` 调 `utc_now()`；时间精度为秒。 |
| `ended_at` | 缺失 | `manifest.py:28` 永远初始化为 `None`；`orchestrator.py:101-130` 没有 run 结束写回。 |
| `python`、`platform` | 已实现 | `manifest.py:29` 写 `platform.python_version()` 和 `platform.platform()`。 |
| `input.path_name`、`input.sha256`、`input.page_count` | 部分实现 | 结构在 `manifest.py:30`；PDF 读入后 `stages/_shared.py:128-132` 写文件名、哈希和页数；没有旧值比较或输入变更错误。 |
| `code.git_commit`、`code.files` | 缺失 | `manifest.py:31` 仅写 `None` 与空对象；没有 Git/源码哈希收集器。 |
| `assets.prompts`、`assets.skills`、`assets.schemas` | 缺失 | `manifest.py:32` 仅写三个空对象；`StageRecord.prompt`（`_shared.py:178-184`）只把 prompt 哈希放入 stage call，未汇总到 assets。 |
| `config` 生效值与 source | 已实现 | `settings.py:83-117` 的 `Settings.describe()` 输出 value/source；`manifest.py:33` 初始写入，`_shared.py:90-105` 做脱敏和路径收敛。 |
| `model.sdk`、`sdk_version`、provider、model、wire_api、sandbox、ephemeral、max_retries | 部分实现 | `manifest.py:34-38` 已写；`sdk_version` 可为 `None`，`binary_version` 永远 `None`。 |
| `model.temperature` | 待裁决/当前缺失 | I8 示例没有该字段的强制实现；A12 原卡曾要求它，但冻结文本同时写明 Codex 不暴露 temperature。不能伪造固定值。 |
| `calls` | 缺失 | `manifest.py:39` 初始化空对象；`StageRecord.model_calls` 只写进每个 stage，未聚合为 `{stage: count}`。 |
| `stages` | 部分实现 | `_shared.py:108-125` 记录 name/status/时间/duration/artifacts/issues/model_calls；没有 root ended_at，也没有统一失败后结束记录。 |
| `deps` | 缺失 | `manifest.py:39` 为空对象；无 pip freeze/关键包收集函数。 |
| `reproducibility` | 部分实现 | `manifest.py:39` 固定写 `not-bitwise`；没有记录造成不可逐字复现的具体运行差异。 |
| `cache.enabled`、`cache.key`、`cache.hit` | 部分实现 | `manifest.py:40` 只按 `keep_intermediate` 写 enabled，key 为 `None`、hit 为 `False`；没有 `result/cache` 读写或命中流程。 |
| 原子写与脱敏 | 已实现（manifest/阶段文件） | `_shared.py:20-47` 的 `_atomic_target` 使用临时文件与 `os.replace`；`_shared.py:90-105` 脱敏 URL、密钥和绝对路径。 |

I2 的基准布局是 `result/runs/<material_id>/<run_id>/manifest.json`（接口清单 I2
`1bbfce0:docs/接口冻结清单_A-B.md:58-81`）。A12 原卡中的 `result/cache/<key>/` 是新增缓存区，
不应混入某个 run 目录。当前没有 `pipeline/cache.py`、缓存目录或专门的 manifest/cache 测试；
现有测试只覆盖 `create_run_manifest` 的形状、阶段记录、输入哈希落盘、request hash 和 replay
门禁（例如 `tests/test_engineering_stages.py:358-398`、`tests/test_engineering_demo.py:84-100`、
`tests/test_engineering_package.py:348-419`）。

## 2. 缓存键公式

建议版本化为 `cache-v1`，使用 UTF-8 的 NUL 分隔、带字段名和长度前缀的规范串，避免值中
出现分隔符导致歧义：

```text
field(name, value) = name + "=" + decimal(len(value_utf8)) + ":" + value
key_material = join("\0", [
  "cache-v1",
  field("material_id", material_id),
  field("input_sha256", source_pdf_sha256),
  field("stage", stage_name),
  field("stage_input_sha256", canonical_hash_of_stage_inputs),
  field("prompt_sha256", prompt_bytes_sha256_or_none),
  field("schema_sha256", schema_bytes_sha256_or_none),
  field("provider", provider_id),
  field("model", model_name),
  field("temperature", "absent"),
  field("timeout_s", canonical_float(timeout_s)),
  field("max_retries", decimal(max_retries)),
  field("wire_api", wire_api),
  field("effort", effort),
  field("dpi", decimal(dpi)),
  field("stage_parameters", canonical_json(stage_specific_parameters)),
])
key = hex(sha256(key_material.encode("utf-8")))
```

`stage_input_sha256` 是该阶段实际从磁盘读取的上游 artifact 集合的规范 JSON 哈希；例如
normalize 包含 recognition.json 的字节哈希，evidence 包含 document_ir.json，write 包含
evidence.json 与 writer/translate prompt 哈希。recognize 以源 PDF `input_sha256` 为主，
并把 page plan/classifier 参数放入 `stage_parameters`。所有 `None` 均统一写成 `none`，浮点
使用 Python `format(value, ".17g")` 后再规范化，JSON 使用 `sort_keys=True`、紧凑分隔符和 UTF-8。

每个维度的来源如下：

- `input_sha256`：`pdf_input.load_pdf()` 返回的 `PdfInfo.sha256`，当前落盘入口是
  `_shared.record_input` `128-132`。
- `stage`：`StageSpec.name` / stage module 的 `STAGE_NAME`，注册和执行在
  `orchestrator.py:47-55,101-130`。
- `prompt_sha256`：`prompt_loader.load_prompt()` 生成的 PromptAsset；当前 stage call 在
  `_shared.py:178-184` 已有该哈希。
- `schema_sha256`：`schema_loader` 对实际发送的 schema 文件做字节 SHA-256；现有
  `manifest.file_sha256` 只有通用文件哈希函数（`manifest.py:49-53`），没有汇总调用。
- `model`、provider、wire_api、timeout、max_retries、effort、dpi：`Settings` 和
  `ModelRequest`；timeout 实际读取于 `codex_adapter.py:93,298`，模型结果模型名在
  `codex_adapter.py:348`。
- `temperature`：当前 Codex 配置没有该参数，必须写稳定的 `absent`，不得伪造数值；若未来
  接口真正增加温度，则从 `absent` 升级 key 版本或明确加入数值。
- `stage_parameters`：BATCH/分类阈值、输出 schema 名、修复上限等会改变产物的参数；不能只靠
  stage 名掩盖它们。

必须排除：`run_id`、started_at、ended_at、调用耗时、attempt 序号、临时目录、绝对路径、
raw 响应中的 turn id。它们描述一次运行或机器位置；纳入后同一输入无法复用，且绝对路径会破坏
跨机复现。`material_id` 建议保留以隔离 A13 四材料，即使两个材料偶然拥有相同 PDF 字节。

现有 `CodexAdapter._request_hash`（`codex_adapter.py:153-166`）不能直接当缓存键：它包含
images/skill 的路径字符串、payload 和 timeout，但没有 source/stage input hash、prompt/schema
字节哈希、material_id、关键配置规范化，也没有 cache-v1 版本域。

## 3. 命中语义

缓存条目必须按 stage 保存完整、已验证的 stage artifact 集合及其摘要。命中条件是：目录存在、
entry metadata 的 key 等于请求 key、每个列出的文件存在、字节 SHA-256 与 metadata 一致、
schema 校验通过。仅部分文件或旧版本 metadata 不算命中。

- recognize、normalize、evidence、write、repair 的阶段产物可复用；repair 只能复用已经
  成功并包含 final.md 的条目，失败修复不能进入 cache。
- 命中后仍需重建当前 run 的 manifest，并把缓存 artifact 以原子方式复制/链接到 run 目录；
  `validation.json` 必须在当前 run 重跑，因为校验器版本、路径和时间属于当前审计上下文。
- 命中不调用 provider；manifest 的 `cache.key=key`、`cache.hit=true`，stage.status 写
  `skipped`（这是 I7 规定的断点重跑语义，不是失败或未实现）。
- calls 记录为零模型调用；建议另加 `cache_hits.{stage}=1` 或 stage 的 `cache_hit=true`，
  但该新增字段需 I8 变更登记，不能把命中伪装成一次 provider call。
- 未命中写 `cache.hit=false`，正常执行并在成功后发布 cache entry；失败执行不产生可命中条目。

待裁决：I2 说阶段性产物失败时保留 run 内中间文件，I8 没有规定缓存失败条目是否保留。建议
缓存目录只保留成功 entry，失败临时目录清理；若要保留调试文件，应放 run/raw，不得让半成品
被其他 run 命中。

## 4. 存储布局与原子写

建议布局：

```text
result/cache/<key>/
├── entry.json                 # cache-v1、key、stage、来源哈希、文件清单、创建时间
├── artifacts/
│   ├── recognition.json       # 或该 stage 的全部产物
│   ├── document_ir.json
│   ├── evidence.json
│   └── ...
└── raw/                       # 仅在策略明确允许且不参与命中内容时保存诊断
```

建议先在 `result/cache/.tmp/<key>.<uuid>/` 写 entry 和 artifacts，逐文件 fsync（平台支持时），
再用同卷 `os.replace(temp_dir, result/cache/<key>)` 发布；已存在同 key 时以 metadata 哈希复核，
完全相同则视为并发同键成功，内容不同则显式键冲突。禁止先创建可见的半成品 key 目录。

run 目录仍按 I2 写自己的 manifest、input、raw 和阶段 artifacts；缓存是可复用的只读来源，
不能让 run 直接把 manifest 指向缓存绝对路径。复制后 run 内文件必须有自己的字节和审计记录。

`keep_intermediate` 的判定已在 manifest 初始值 `manifest.py:40` 读取
`settings.pipeline.keep_intermediate`；建议同一配置值在 cache manager 的入口先判定：false 时
不读、不写、不创建 `result/cache`，并把 cache.enabled=false、key=null、hit=false 写入 manifest。
不能先读缓存再把 enabled 改成 false。

## 5. 失败语义

| 失败 | 处理 | code/去向 | 是否影响 valid |
|---|---|---|---|
| entry.json 不存在或 key 不匹配 | cache miss，重新执行 | 无 warning；manifest `cache.hit=false` | 不直接影响 |
| entry JSON 损坏、文件缺失、哈希不符 | 显式缓存损坏失败 | `SCHEMA_INVALID`/`IO_FAILURE`，写 stage failed；不静默回源 | run 失败，valid 不生成成功结论 |
| 临时目录/entry 原子发布权限错误 | 显式失败 | `IO_FAILURE`/7；保留 run 已有诊断，清理可清理临时目录 | run 失败 |
| 并发同 key 且字节完全相同 | 等待/复核后命中 | 无 error，单一 entry | 不影响 |
| 并发同 key 但 metadata/字节不同 | 键冲突，停止发布 | `IO_FAILURE`/7（或单独 code，需 I7/I8 裁决） | run 失败 |
| keep_intermediate=false | 禁用缓存路径 | 不读写 cache；不发 warning | 不影响 |
| cache entry schema 版本过旧 | miss 还是失败 | 建议显式 `SCHEMA_VERSION_MISMATCH`，不静默使用旧 entry | run 失败 |

缓存 warning 不能被 `valid=true` 掩盖；缓存校验失败时应有 stage/manifest 状态和退出码。只有
不存在的 key 是正常 miss，不能把损坏、半写、权限错误伪装成 miss。缓存发布成功后再更新
manifest，manifest 写失败仍按现有 `_atomic_target` 映射 `IO_FAILURE`。

## 6. 插入点清单与冲突风险

每一项都可用 `git show 1bbfce0:<path> | grep -n` 复现；下列行号均来自该基准。

| 文件/函数/行号 | 现状 | 最小改动面 | 工作区冲突风险 |
|---|---|---|---|
| `pipeline/manifest.py:create_run_manifest:26-41` | skeleton manifest | 增加 cache-v1 字段约束、统一初始 enabled/key/hit | 否（当前 status 未列该文件） |
| `pipeline/manifest.py:write_manifest:44-46` | JSON 写盘 | 保持序列化；缓存 entry 需独立原子 writer | 否 |
| `pipeline/manifest.py:file_sha256:49-53` | 单文件哈希工具 | 复用到 assets/cache entry，不改语义 | 否 |
| `pipeline/stages/_shared.py:_atomic_target:20-47` | run 文件原子写 | 抽出/复用同卷原子发布；避免把缓存路径暴露给 run | 否（但将被核心重构消费） |
| `_shared.py:_read_manifest/_write_manifest:75-105` | 读取、脱敏并原子更新 manifest | 接受 cache 状态/结束时间更新；保持秘密和绝对路径脱敏 | 否（当前 status 未列） |
| `_shared.py:record_stage:108-125` | 写 stages/model_calls | 追加 calls 聚合、cache hit/skipped 记录和 stage artifact 哈希 | 否 |
| `_shared.py:record_input:128-132` | 写 input 哈希 | 作为 cache `input_sha256` 来源；不要在此读旧 run 做复用决定 | 否 |
| `_shared.py:StageRecord.prompt:178-184` | 记录 prompt hash/version | 汇总 prompt asset 哈希到 key/manifest assets | 否 |
| `_shared.py:stage_record:213-224` | 阶段结束/失败记账 | 成功/失败后触发 cache publish 或禁止 publish | 否 |
| `orchestrator.py:47-55,101-130` | 注册、排序、逐阶段执行 | 每个 stage 前 cache lookup，命中返回 skipped；失败不发布 | **是**，当前工作区修改了 orchestrator |
| `settings.py:83-117,398-476` | describe/source merge/load | 增加 cache policy 只应扩展配置契约并登记；当前 source map 可直接复用 | **是**，当前工作区修改了 settings |
| `codex_adapter.py:153-166` | request_hash 包含路径和局部请求字段 | 保留模型请求审计；新增 cache key builder，不能复用为最终 key | **是**，当前工作区修改了 adapter |
| `prompt_loader.py` 的 load_prompt/load_skill | 资产字节哈希入口 | 把实际 prompt/schema/skill hash 传给 key builder | 当前 status 未列，但可能被核心重构依赖 |
| `main.py` run/context 建立处 | 当前 UUID 和 run_dir 由 CLI 创建 | 只传 cache policy/context；不让 cache key 依赖 run_id | **是**，工作区有 main 改动 |

插入点定位命令示例：

```powershell
git show 1bbfce0:7_视觉源码/source_code/pipeline/manifest.py | Select-String -Pattern 'def create_run_manifest|def write_manifest|def file_sha256'
git show 1bbfce0:7_视觉源码/source_code/pipeline/stages/_shared.py | Select-String -Pattern 'def _atomic_target|def record_stage|def record_input|def stage_record'
git show 1bbfce0:7_视觉源码/source_code/pipeline/orchestrator.py | Select-String -Pattern 'def run_stages'
git show 1bbfce0:7_视觉源码/source_code/pipeline/codex_adapter.py | Select-String -Pattern 'def _request_hash|def generate'
```

## 7. 测试清单

新增实现卡时应新建 manifest/cache 专项测试；基准没有 `tests/test_engineering_manifest.py`
或 `tests/test_engineering_cache.py`。

| 场景 | 必须断言 |
|---|---|
| 同参两次 | 第二次 `cache.hit=true`、provider 调用数为 0、entry/产物哈希一致、当前 validation 仍重跑 |
| 改 model | key 改变、miss、调用 provider |
| 改 prompt 字节或版本 | prompt_sha256/key 改变、miss |
| 改 schema 字节 | schema_sha256/key 改变、miss |
| 改 source input | input_sha256/key 改变、miss；不同 material_id 也按隔离策略验证 |
| 改 temperature | 当前 absent→显式温度或温度值变化必须 miss；Codex 当前不得伪造 temperature |
| 改 timeout/effort/dpi/repair 上限等关键参数 | stage_parameters/key 改变、miss |
| 损坏 entry、缺文件、哈希不符 | `SCHEMA_INVALID` 或 `IO_FAILURE`、stage failed、无静默回源 |
| 并发同 key | 完全同字节只生成一个可验证 entry；不同内容显式键冲突；无半写目录被命中 |
| keep_intermediate=false | cache 目录不创建、不读写、manifest enabled=false/key=null/hit=false |
| 命中后产物 | run 内产物字节 SHA 与 miss 路径相同，manifest 重写但 run_id/时间不进入 key |
| 失败阶段 | cache entry 不存在或不可命中，run/raw 诊断保留，退出码明确 |

测试还应固定 `calls` 口径：cache hit 的 stage calls 为 0，provider 调用统计不把缓存复用伪装成
模型调用；重写 manifest 的 I/O 不应改变 cache key。并发测试需使用真实临时目录和原子发布，
不把 `sleep` 或 mock 的“成功写入”当作竞态证明。

## 8. 与 A15/A13 的交互

- **A15 packaging**：基准 `packaging.MATERIAL_FILES` 只打包 `final.md`、`validation.json`、
  `manifest.json`（`packaging.py:22`）；cache 目录不应进入提交包，也不应作为打包必需文件。
  manifest 可以保留 `cache.enabled/key/hit` 供审计，但 package 不应因 cache miss 拒绝一个已完成的
  run；entry 损坏/validation 失败则由 run 的显式状态和 gate 拒绝。是否把 cache key 写入最终
  package_report 是 A15 的待裁决，不在本卡代定。
- **A13 四材料**：key 至少含 `material_id`、源 input hash 和 stage input hash；四材料即使并行
  也只能共享完全同一 key 的只读 entry，不能按 run_id 共享可变状态。建议 cache/<key> 发布使用
  `material_id` 隔离，run 目录仍各自独立。
- **replay**：I5/replay 测试通过 manifest 的 provider/config 识别 replay；与 C84C91D 拒收 replay
  包的约束一致，replay 运行默认只读既有 raw/fixtures，不写生产 cache。若允许 replay 预热缓存，
  必须单独命名 namespace、标记 provenance，并先获 I8/A15 同意；本卡不推定允许。

## 待裁决

1. A12 原卡提到 temperature，但 I8 冻结文本说明 Codex 不暴露该参数：选择 `absent` 并保持
   cache-v1，还是未来增加 temperature 时升 key 版本。
2. 缓存 entry 损坏是显式失败还是可重算 miss；本 memo 推荐显式失败，避免缓存损坏被静默掩盖。
3. 并发同 key 的发布锁与不同字节冲突 code；本 memo 推荐同字节复用、不同字节 `IO_FAILURE/7`。
4. A15 package_report 是否记录 cache key/hit；I2 目录中 cache 不入包，两者需在 A15 冻结。
5. replay 是否拥有独立只读 cache namespace；在 C84C91D 拒收 replay 包的前提下，本 memo 推荐
   replay 不写生产 cache。
6. I8 要求 root `manifest.json`，A12 cache 使用 `result/cache/<key>/entry.json`；两者位置不冲突，
   但“命中后 validation 必须重跑”与 I2 的阶段 skipped 语义需要 A/B 双签确认。

本卡只提交该设计 memo，不改代码、schema、接口冻结清单、fixtures 或 tests。下一步应等核心
重构提交、工作树干净后，再开 A12 实施卡。
