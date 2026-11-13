# 评审修复记录（2026-11-13 22:00–22:40）

> 触发：A9 交付评审。本文只记录跨卡修复，不改变 A9 的校验规则范围。
> 所有改动的验收命令均在 `source_code/` 下真实执行，输出见第 2 节。

## 1 改动清单

| 文件 | 责任 | 为什么改 |
|---|---|---|
| `source_code/main.py` | CLI 退出码 + 运行目录 + PDF 传递 | `validate` 在 `valid=false` 时返回 0，打包与 CI 无法判定失败；`run` 未创建 I2 布局，也未把源 PDF 传给阶段，A8 无法开工 |
| `source_code/pipeline/errors.py` | 新增 `AuthMissing`，映射退出码 2 | A1 卡要求 `AUTH_MISSING`，但 30 条 `Code` 枚举里没有它；I7 的 8 只属于 `ASSET_*` |
| `source_code/pipeline/codex_adapter.py` | 登录失败归类为 `AuthMissing` | 原实现归类为 `ConfigError`，A7 卡的 `AUTH_MISSING` 验收项无法通过；顺带用词边界避免 "author" 误判 |
| `source_code/pipeline/pdf_input.py` | `load_pdf(path, run_dir=None)`，删除 `PIPELINE_RUN_DIR` | 渲染目标原由 PDF 位置/环境变量推断，违反 I2（源 PDF 不进运行目录）与 A2（唯一配置入口） |
| `source_code/pipeline/orchestrator.py` | 执行顺序改为按 `requires` 拓扑排序；空注册表抛 `STAGE_NOT_REGISTERED` | 阶段模块由 pkgutil 按字母序注册，原实现会让 evidence 先于 recognize 执行，`--stage all` 会"成功"但顺序错误；空注册表时 `run` 静默退出 0，违反 I7 |
| `source_code/tests/test_engineering_orchestrator.py` | 新增 5 项测试 | 钉住拓扑顺序、依赖补齐、空注册表、未知依赖、环检测 |
| `source_code/pipeline/validate.py` | `MD_MISSING_SECTION` 增加固定 hint；章节名收敛为常量 | B 未确认标题写法；不加 hint 时 `## 1. 引言` 只会得到"缺章节"，无法定位。校验仍严格逐字匹配，未放宽 |
| `source_code/schemas/validation.schema.json` | 仅新增 `description` | 让 `stats.evidence_count`（0/1 对象数，非条数）等语义机器可读；无结构变更，版本保持 1.1.0 |
| `source_code/pytest.ini` | 禁用无关的 `hydra_pytest` 插件 | 全局 `hydra-core` 缺 PyYAML，`python -m pytest` 在收集前崩溃，验收命令不可复现 |
| `source_code/requirements.txt` / `requirements-lock.txt` | 声明 `pillow==12.3.0` | `pypdfium2.bitmap.to_pil()` 依赖 Pillow，未声明则干净环境必然失败 |
| `source_code/tests/test_engineering_cli.py` | 新增 5 项测试 | 钉住 `validate` 退出码、`run` 布局、`AUTH_MISSING`、30 条枚举不变 |
| `source_code/tests/test_engineering_pdf_run_dir.py` | 新增 2 项测试 | 钉住"渲染只写 run 目录"与"环境变量不是配置通道" |
| `source_code/tests/test_engineering_fixture_snapshot.py` | 新增 5 项快照测试 | 验收命令会改写已提交夹具；快照测试在 tmp 内重算并比对（忽略 `checked_at`），防止代码与快照悄悄漂移 |
| `tests/fixtures/engineering/*/validation.json` | 刷新快照 | 因 hint 变更重新生成；`checked_at` 更新，无其他差异 |

## 2 验收原始输出

```text
$ python -m pytest -q
86 passed in 1.96s
exit=0
```

```text
$ python main.py run /tmp/blank.pdf --output /tmp/runs --stage all
{"error": {"code": "STAGE_NOT_REGISTERED", "message": "No stages are registered"}}
all_exit=1
```

```text
$ python main.py validate --run-id revbad >/dev/null ; echo $?
4
$ python main.py validate --run-id revok >/dev/null ; echo $?
0
```

```text
$ python main.py run /tmp/blank.pdf --output /tmp/runs --stage recognize
{"error": {"code": "STAGE_NOT_REGISTERED", "message": "Requested stage is not registered"}}
run_exit=1
/tmp/runs/blank/<run_id>/input
/tmp/runs/blank/<run_id>/logs
/tmp/runs/blank/<run_id>/raw
```

```text
$ python main.py doctor --offline >/dev/null ; echo $?
0
```

## 3 待双签项（不代签）

1. `AUTH_MISSING` 归退出码 2（而非 A1 卡写的 8），理由见 `docs/接口冻结清单_A-B.md` §6.2 补充段。
2. `validate` 子命令 `valid=false` → 退出码 4。I7 原本没有"校验失败"这一行，本次按"结构与内容校验失败"归入 4。
3. `validation.schema.json` 仅加 `description`、不升版本：若 B 认为这也算 Schema 变更，需升 1.1.1 并同步 A9 测试里的版本断言。

## 4 仍未解决（阻塞项保留）

- A9 第 5/6/8 项（引用、数值、串稿）仍锁在 B 的 evidence 字段上；`B-FIELD-REQUEST.md` 答复区仍为空。
- `source_code/prompts/` 不存在；A8 正式运行必然 `AssetMissing`、退出码 8，这是设计行为。
- `docs/` 仍被父级 `.gitignore` 的 `**/docs/` 忽略，接口冻结清单无法填 commit hash。
- 共享内核（`settings.py`、`schema_loader.py`、`stages/` 等）仍是未跟踪状态，"干净检出可运行"不成立。

## 5 下游需知

- A1：未登录一律抛 `AuthMissing`（`code=AUTH_MISSING`，退出码 2），禁止新增枚举成员；`Code` 必须保持 30 条（有测试钉住）。
- A8：`load_pdf(path, run_dir=ctx.run_dir)` 是唯一正确调用方式；不得再读 `PIPELINE_RUN_DIR`。`ctx.artifacts["source_pdf"]` 是源 PDF 路径。阶段模块的 `REQUIRES` 只能写**已注册的阶段名**（源 PDF 不是阶段），否则编排器抛 `STAGE_NOT_REGISTERED`。
- A15：打包门槛应读 `validation.json` 的 `valid` 与 `checks.skipped`；CLI 退出码 4 可作为快速失败信号。
- 任何人跑 A9 验收命令都会改写夹具的 `checked_at`；跑完请 `git restore` 或接受快照测试的结论，不要把时间戳改动混进功能提交。
