# 8_95优化

本目录是当前比赛作品的高分阶段优化版本（目录名沿用 95 分阶段）。

## 内容

- `AI-01.md`、`BIO-02.md`、`IND-03.md`、`MED-04.md`：四篇中文科普改写稿（当前与模型原始输出一致）
- `source_code/`：PDF 证据校验、模型生成、逐段事实审计、评分和测试代码
- `assessment/`：评分标准、内部评分报告与复现状态、审计处理记录和编审修改记录
- `api_run/`：真实模型生成起稿、运行日志与运行记录

## 当前验收结果

- 格式硬检查：9/9 项通过（ZIP 1 分待打包），折算 10/10 仅代表格式维度
- 现稿四篇与 `api_run/raw_run2/` 原始输出逐字节一致，当前无人工修订差异
- 42 张原文证据卡通过 PDF 页码校验
- 14 项 Python 测试通过
- 四份原始输出与运行日志哈希一致、日志未包含 API 凭证（上一版记录，现稿修订后待重跑 `verify_run.py`）
- 现稿无逐段审计记录（历史审计与过期运行产物已清理）；`score.py` 只做客观检查，不输出总分
- 主观四项（科学 15、科普 20、完整 20、源码 35）不在仓库内自评，由评委判定；`score.py` 默认只做客观检查
- ZIP 打包和最终提交格式尚未验收

运行说明见 [`source_code/README.md`](source_code/README.md)，客观检查结果见 [`assessment/REPORT.md`](assessment/REPORT.md)。

## 安全说明

本目录的提交包（四篇 Markdown 与 `source_code`）不包含 API 密钥或本机凭证。本机运行用的根目录 `config.toml` 含真实令牌，已被 `.gitignore` 忽略，不得进入 ZIP 或版本库；`source_code/config.toml` 的令牌为空，提交前请核对。
