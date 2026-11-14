# 文件占用登记

规则：开工前追加一行；交付后把状态改为「已释放」，不删行。
发现目标文件已被他人标记「占用中」→ 停止，报告 S0，不得抢写。
一次最多占用 3 个文件；占而不用超过 40 分钟视为放弃，S0 可回收。

| 卡号 | 会话 | 文件 | 开始时间 | 状态 |
|---|---|---|---|---|
| W0 | S0 | source_code/pipeline/errors.py | 2026-11-13 | 已释放 |
| W0 | S0 | source_code/pipeline/contracts.py | 2026-11-13 | 已释放 |
| W0 | S0 | source_code/schemas/validation.schema.json | 2026-11-13 | 已释放 |

| W0 | S0 | source_code/pipeline/settings.py | 2026-11-13 | 已释放 |
| W0 | S0 | source_code/pipeline/schema_loader.py | 2026-11-13 | 已释放 |
| W0 | S0 | source_code/config.json | 2026-11-13 | 已释放 |

| W0 | S0 | source_code/pipeline/manifest.py | 2026-11-13 | 已释放 |
| W0 | S0 | source_code/pipeline/orchestrator.py | 2026-11-13 | 已释放 |
| W0 | S0 | source_code/main.py | 2026-11-13 | 已释放 |

| A6+A7 | S1 | source_code/pipeline/pdf_input.py | 2026-11-13 20:48 | 占用中 |
| A6+A7 | S1 | source_code/pipeline/codex_adapter.py | 2026-11-13 20:48 | 已释放（A14b 回收，超 40 分钟未交付） |
| A6+A7 | S1 | source_code/pipeline/vision_provider.py | 2026-11-13 20:48 | 占用中 |
| A6+A7 | S1 | source_code/tests/test_engineering_input.py | 2026-11-13 20:48 | 占用中 |
| A8 | S3 | source_code/pipeline/prompt_loader.py、source_code/pipeline/stages/{_shared,recognize,normalize,evidence}.py、source_code/tests/test_engineering_stages.py、source_code/tests/test_engineering_fixture_snapshot.py（跨卡，仅改 1 行参数化）、source_code/tests/fixtures/engineering/stub_prompts/{vision,normalize,evidence}.md | 2026-11-14 00:30（事后补登：A8 未在开工前登记） | 已释放 |
| A11 | S3 | source_code/pipeline/writer.py（批 1） | 2026-11-14 08:49:49 +08:00 | 已释放 |
| A11 | S3 | source_code/pipeline/repair.py（批 1） | 2026-11-14 08:49:49 +08:00 | 已释放 |
| A11 | S3 | source_code/pipeline/stages/write.py（批 1） | 2026-11-14 08:49:49 +08:00 | 已释放 |
| A11 | S3 | source_code/pipeline/stages/repair.py（批 2） | 2026-11-14 08:51:24 +08:00 | 已释放 |
| A11 | S3 | source_code/tests/test_engineering_write_repair.py（批 2） | 2026-11-14 08:51:24 +08:00 | 已释放 |
| A11 | S3 | source_code/tests/test_engineering_stages.py（批 2） | 2026-11-14 08:51:24 +08:00 | 已释放 |
| A11 | S3 | source_code/tests/fixtures/engineering/stub_prompts/writer.md（批 3） | 2026-11-14 08:57:44 +08:00 | 已释放 |
| A11 | S3 | source_code/tests/fixtures/engineering/stub_prompts/repair.md（批 3） | 2026-11-14 08:57:44 +08:00 | 已释放 |
| A15 | S3 | source_code/pipeline/packaging.py | 2026-11-14 10:15:09 +08:00 | 已释放 |
| A15 | S3 | source_code/tests/test_engineering_package.py | 2026-11-14 10:15:09 +08:00 | 已释放 |

| A15-R | S3 | source_code/pipeline/packaging.py | 2026-11-14 10:56:46 +08:00 | 已释放 |
| A15-R | S3 | source_code/tests/test_engineering_package.py | 2026-11-14 10:56:46 +08:00 | 已释放 |
| A1 | S3 | source_code/provider_capabilities.json | 2026-11-14 11:07:57 +08:00 | 已释放 |
| A1 | S3 | .parallel/handoffs/A1.md | 2026-11-14 11:07:57 +08:00 | 已释放 |
| A17 | S0 | source_code/schemas/evidence.schema.json、source_code/pipeline/schema_loader.py、source_code/tests/test_engineering_validate.py、source_code/tests/fixtures/engineering/run_*/{evidence,validation}.json、docs/接口冻结清单_A-B.md（事后补登：用户直接指令下开工） | 2026-11-14 11:41:20 +08:00 | 已释放 |
| A18 | S0 | source_code/schemas/{recognition,document_ir,evidence}.schema.json、source_code/pipeline/{schema_loader,writer}.py、source_code/pipeline/stages/{recognize,write}.py、source_code/prompts/{normalize,evidence}.md、source_code/tests/{test_engineering_stages,test_engineering_validate,test_engineering_write_repair}.py、source_code/tests/fixtures/engineering/**（事后补登：用户「按这个走」后连续实施） | 2026-11-14 14:04:58 +08:00 | 已释放 |

| A19 | S0 | 本卡确认的 17 个白名单文件：pipeline/extract.py、pipeline/stages/recognize.py、pipeline/schema_loader.py；schemas/{table,figure,formula}.schema.json；prompts/{vision,table,figure,formula}.md；tests/test_engineering_{extract,stages,validate}.py（以上均在 source_code/）；docs/接口冻结清单_A-B.md、.parallel/{STATE,CLAIMS}.md、.parallel/handoffs/A19.md。用户已确认实施范围及 vision region 说明对齐既有 schema | 2026-11-14 15:04:50 +08:00 | 已释放 |

| A14 | S0 | source_code/pipeline/validate.py；tests/test_engineering_{fixtures,validate,package,write_repair}.py 与 tests/fixtures/engineering/run_*/（均在 source_code/）；docs/接口冻结清单_A-B.md；.parallel/{STATE,CLAIMS}.md 与 handoffs/A14.md。用户确认五项检查口径并扩容 write_repair 测试；B 夹具只读；before/after确认后逐份重生成快照 | 2026-11-14 15:39:29 +08:00 | 已释放 |

| A14b | S0 | source_code/tests/test_engineering_faults.py、source_code/pipeline/codex_adapter.py（仅 turn.run 异常路径失败审计）、docs/fault-injection.md、.parallel/{STATE,CLAIMS}.md、.parallel/handoffs/A14b.md；按用户裁决回收 adapter 旧占用，其余 A6+A7 文件不动 | 2026-11-14 16:31:56 +08:00 | 已释放 |

## 契约变更申请

> 需要改 `contracts.py` / `errors.py` 枚举 / `settings.py` 字段 / `config.json` 键 /
> `requirements.txt` 时，在此登记，等 S0 裁决后由 S0 执行。**不得自行修改。**

| 申请方 | 目标文件 | 变更内容 | 理由 | 裁决 |
|---|---|---|---|---|
| S0 | W0 共享内核 | 按本次用户明确指令完成 W0，包括 validation 的 issues 对齐 | 用户再次明确“你来采用最优解修复W0范围” | 本次 W0 执行授权；不代表 A/B 已双签，不代签；其余业务 schema 不改 |
| A15 | S3 | I2 package_report.json 位置 | D1 放在 ZIP 同目录，与 I2 运行目录清单冲突；记录契约变更申请 | 待 S0 裁决 |
