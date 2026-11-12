# 科普表达工作流

本目录是一套完整、可单独复制运行的优化源码副本。PDF到中文Markdown的生成、生成提示词、事实提取、生成阶段质量核查、有限修复、旧稿保护、validate和package均保持原版行为。只有review入口改为本目录的单项评估。

## 使用

在本工作流根目录运行以下命令：

    py -m pip install -r source_code/requirements.txt
    py source_code/main.py generate
    py source_code/main.py review
    py source_code/main.py validate
    py source_code/main.py package
    cd source_code
    python -B -m unittest discover -s tests -v
将四份原始PDF放入本工作流的materials/，保留AI-01、BIO-02、IND-03、MED-04编号。将source_code/.env.example复制为source_code/.env，填写LLM_API_KEY；在source_code/config.json配置模型、标题、来源和文献类型。Python要求3.10以上。

generate与正式review均调用LLM。缺少密钥或模型配置时正式review直接报错。显式review --offline是本地诊断，不调用模型、不评分。all保留原版generate→validate→package语义；需要评审时另运行review。

## 本项目唯一评估指标

科普表达：满分20。官方规则：是否通俗易懂，专业术语是否解释清楚，是否适合普通读者阅读。

依据文章原句评价术语解释、句子、逻辑衔接、例子及普通读者理解成本，只评价阅读体验。

只显示本项模拟评分、风险、逐项扣分依据和修改建议，不合成其他指标或总分。依据用户提供的官方整体规则评分，不编造官方固定逐错扣分表。引文必须真实存在，分数必须等于满分减去扣分合计。未核验项存在时暂不计分。

格式工作流对四份成稿和ZIP整组调用一次LLM；本地只提供确定性证据。要完整评估ZIP，先运行package。科普表达每篇一次LLM，不扫描PDF。准确性和完整性在原文可放入证据预算时直接提供全文，每篇一次LLM；长原文才按片取证。准确性沿用原版语义检索与引文校验，完整性从原文提炼要点反查文章遗漏。review.evidence_max_chars控制证据预算，review.max_evidence_calls控制长原文取证调用上限。达到上限和证据缺口都会列出。

报告保存在source_code/result/review_*.md，review不修改成稿。已有成稿可直接复制同一批materials/及docs/到需要使用的工作流，随后执行review，无需为了评审重复生成。模型调用次数与原文长度有关；生成阶段费用保持原版，节省来自只运行所需单项review。

## 代码

- main.py：保留原CLI，review转交pipeline/single_review.py。
- pipeline/criterion.py：当前目录唯一指标、满分及官方规则。
- pipeline/single_review.py：按指标取证、LLM评估及输出校验。
- 原有pipeline模块保持优化源码逐字节一致；旧review评分辅助函数供原回归测试保留，不再作为CLI多指标评分入口。

在source_code目录读取本说明时，materials/及docs/均指上一级工作流根目录。

## 固定基线与每轮对比

共享生成实现已对齐到 `1_优化源码`，初始目标长度统一为1700字。当前 `docs/` 载入同一批基线文章；manifest保留其原生成记录。只在本链调整目标指标对应的生成提示或逻辑，再把同一组候选文章交给四条链评审。

评审预算为32000字符、长文每篇最多32次取证调用；`review.request_timeout=180` 为独立网络等待时间。模型、温度、输出上限及评分规则保持统一。完整性取证允许每片最多24条，生成取证仍最多12条。

评审同时保存 `source_code/result/review_*.md` 和 `review_*.json`，逐请求显示进度。相同文章、证据与模型参数的成功响应会保存在 `review_cache/` 并在重跑时重新校验，失败请求不缓存。未覆盖、未核验或模型回复无效时不形成完整分数。

四项评审对同一组四篇文章运行后，可在仓库根使用 `tools/workflow_round.py collect <轮次>` 保存本轮报告，再用 `compare baseline <轮次> --target <指标英文名>` 检查5个百分点的门槛、其他指标退步和评审配置是否变化。格式是整组评分一次；其余三项分别对四篇评分。

格式基线已满分时按保持10/10及负例拦截验收，不要求额外涨分。收集轮次前运行 `tools/workflow_round.py check-format`，记录与当前验证器一致的负例检查结果。
