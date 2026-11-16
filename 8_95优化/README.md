# 8_95优化

本目录是当前比赛作品的 95 分阶段优化版本。

## 内容

- `AI-01.md`、`BIO-02.md`、`IND-03.md`、`MED-04.md`：四篇中文科普改写终稿
- `source_code/`：PDF 证据校验、模型生成、逐段事实审计、评分和测试代码
- `assessment/`：评分标准、95 分内部评估、审计处理记录和编审修改记录
- `api_run/`、`run_logs/`：真实模型生成起稿及运行追踪记录

## 当前验收结果

- 内部阶段估计：95/100
- 42 张原文证据卡通过 PDF 页码校验
- 14 项 Python 测试通过
- 四份原始输出与运行日志哈希一致，日志未包含 API 凭证
- ZIP 打包和最终提交格式尚未验收

运行说明见 [`source_code/README.md`](source_code/README.md)，完整评分依据见 [`assessment/REPORT.md`](assessment/REPORT.md)。

## 安全说明

本目录不包含 API 密钥或本机凭证。运行程序时请通过环境变量或本地 TOML 配置提供模型服务配置，不要将配置文件提交到仓库。
