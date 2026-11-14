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
| A6+A7 | S1 | source_code/pipeline/codex_adapter.py | 2026-11-13 20:48 | 占用中 |
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

## 契约变更申请

> 需要改 `contracts.py` / `errors.py` 枚举 / `settings.py` 字段 / `config.json` 键 /
> `requirements.txt` 时，在此登记，等 S0 裁决后由 S0 执行。**不得自行修改。**

| 申请方 | 目标文件 | 变更内容 | 理由 | 裁决 |
|---|---|---|---|---|
| S0 | W0 共享内核 | 按本次用户明确指令完成 W0，包括 validation 的 issues 对齐 | 用户再次明确“你来采用最优解修复W0范围” | 本次 W0 执行授权；不代表 A/B 已双签，不代签；其余业务 schema 不改 |
| A15 | S3 | I2 package_report.json 位置 | D1 放在 ZIP 同目录，与 I2 运行目录清单冲突；记录契约变更申请 | 待 S0 裁决 |
