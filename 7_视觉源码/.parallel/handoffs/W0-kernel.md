## 1 改动清单

`source_code/pipeline/errors.py`：30 个 I6 code、Issue、异常层级与 I7 退出码。
`source_code/pipeline/contracts.py`：W0 冻结的 10 组 frozen dataclass。
`source_code/pipeline/schema_loader.py`：严格加载、版本、格式、哈希与结构化 issue。
`source_code/schemas/validation.schema.json`：从 `errors: [str]` 对齐为 `issues` 对象数组。
`source_code/pipeline/settings.py`：配置层级、dpi/正文阈值、密钥脱敏与错误模块迁移。
`source_code/pipeline/orchestrator.py`：注册表、自动发现、依赖选择和失败停止。
`source_code/pipeline/manifest.py`、`source_code/main.py`：I8 结构和四个 CLI 子命令。
其余 W0 业务模块：保留冻结签名并显式 `NotImplementedError`。

## 2 冻结签名

签名逐字记录在 `.parallel/CONTRACTS.md`；后续 A6/A7/A8/A9/A11/A15 只填实现。

## 3 验收原始输出

`python -m compileall -q pipeline main.py` → `compile-ok`，退出码 0。
`import pipeline.errors, pipeline.settings, pipeline.schema_loader, pipeline.contracts, pipeline.orchestrator, pipeline.manifest` → `import-ok`，退出码 0。
`len(list(Code))` → `30`，退出码 0。
`exit_code_for(PipelineError(Code.PDF_ENCRYPTED))` → `3`，退出码 0。
合法 W0 validation 实例 → `[]`，退出码 0。
`python main.py run tests/does-not-exist.pdf --stage recognize` → `STAGE_NOT_REGISTERED`，退出码 1；先检查阶段注册，未触碰 PDF。
`python main.py --help` → `{run,doctor,validate,package}`，退出码 0。
`python main.py doctor --offline` → 配置来源及 `OPENAI_API_KEY=***`，退出码 0。
IoFailure 归类为 I7 的落盘/打包失败退出码 7。
`python -m pip install -r requirements.txt` → 退出码 0；六项依赖均已满足。

## 4 未覆盖风险

`pipeline/stages/` 当前没有业务阶段模块，因此 `run` 按契约明确失败；A8 交付并注册阶段后解除。
其余三个 schema 尚无 `schema_version`，加载器会 fail-closed 抛版本不匹配，等待 A5/B 双签字段。
I3/I6 在接口清单的 A/B 签字栏仍为空；本次改动依据用户明确授权和文档中的已确认片段落盘，不代替签字。

## 5 下游需知

下游读取 `ValidationReport.to_dict()` 或 `validation.schema.json`，不得读取旧 `errors`。
`schema_loader.validate_instance("validation", ...)` 对旧格式、`None`、未知 code 和额外字段显式返回 `SCHEMA_INVALID` issue。
阶段实现必须通过 `STAGE_NAME`、`run`、`REQUIRES`、`PRODUCES` 自动发现；缺注册、缺产物、重复注册和阶段异常均失败。
