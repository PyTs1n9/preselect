# 科普改写与单项调优

| 目录 | 用途 |
|---|---|
| `0_最初源码` | 最初版本 |
| `1_优化源码` | 已跑通的共同生成基线与最终总源码 |
| `2_科学准确性工作流` | 科学准确性调优与单项评审，满分15 |
| `3_科普表达工作流` | 科普表达调优与单项评审，满分20 |
| `4_内容完整性工作流` | 内容完整性调优与单项评审，满分20 |
| `5_格式规范性工作流` | 格式调优与整组文件、ZIP评审，满分10 |

四条单链共用优化源码的生成实现和初始生成参数，分别保留自己的指标评审。每轮只调整目标指标的生成提示或逻辑，再对同一组四篇文章运行四项评审。

运行方法与每轮验收见 [单项调优说明](docs/single-metric-workflows.md)，已确认的选择见 [决策记录](docs/decisions-single-metric-workflows.md)。

工作区已安装 Python 3.14，可在根目录用以下命令评审某条单链：

```powershell
py -3.14 -B 2_科学准确性工作流/source_code/main.py review
```

每轮四项评审全部结束后，收集报告并与基线对比：

```powershell
py -3.14 -X utf8 -B tools/workflow_round.py check-format
py -3.14 -X utf8 -B tools/workflow_round.py collect baseline
py -3.14 -X utf8 -B tools/workflow_round.py collect accuracy-r01
py -3.14 -X utf8 -B tools/workflow_round.py compare baseline accuracy-r01 --target accuracy
```

收集工具要求四项报告对应同一套四篇文章；轮次名称不能重复。待核验或失败项不会被当作零分或满分参与平均。
