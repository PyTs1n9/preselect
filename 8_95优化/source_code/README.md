# 专业文献科普改写程序

本程序读取赛题指定的四份 PDF，核对 evidence.json 中的原文摘录和页码，调用兼容 Chat Completions 的模型服务进行起草与第二阶段复核，再检查 Markdown 的结构、来源和字数。两阶段可使用同一模型，因此第二阶段不构成独立事实鉴定。正式运行时保留提示词、原始响应和文件哈希，便于查验生成过程。模型输出存在不确定性，重跑不承诺逐字一致。

## 环境

- Python 3.11 或更新版本
- 安装依赖：python -m pip install -r requirements.txt
- 将四份原始 PDF 放在输入目录，文件名与 evidence.json 一致
- 使用环境变量 MODEL_API_URL、MODEL_NAME、MODEL_API_KEY，可选 MODEL_WIRE_API（responses 或 chat_completions）；也可传入 --credentials-file 指向本机 TOML 配置。密钥文件不得进入提交包

## 运行

先验证输入与证据：python main.py --validate-sources --input-dir <PDF目录>

测试实际接口：python main.py --credentials-file <本机配置文件> --probe

生成四篇文章：python main.py --credentials-file <本机配置文件> --input-dir <PDF目录> --output-dir <文章目录> --log-dir <日志目录>

程序拒绝输出超出字数或缺少章节、来源的文章；需要人工核对事实和解释质量。人工修改终稿后，应保留与程序生成稿的差异，并重新审计与评分。score.py 的硬检查不会替代评委或领域专家判断。

保留原始模型输出和对应日志后，可运行 verify_run.py --input-dir <PDF目录> --raw-dir <原始输出目录> --final-dir <人工修订目录> --log-dir <日志目录> --credentials-file <本机配置文件>，检查原文、原始输出和日志哈希，以及日志是否含凭证。

## 事实审计与内部评分

终稿确定后运行 `python audit.py --credentials-file <本机配置文件> --article-dir <终稿目录> --output-dir <审计目录>`。程序按正文段落提取事实主张，让模型用证据卡及 PDF 页面的摘录标记支持、需限定、证据不足或示意类比。引文和段落位置会机械核验；模型只看到选取的原文上下文，审计结论仍须人工复核。

对“需限定”和“证据不足”的项目，先核对完整 PDF。需要修改文章时，修改后重新运行审计；保留解释性文字时，在 `assessment/audit_resolutions.json` 中记录主张编号、原文句子、处理方式和依据。随后运行 `python score.py`。评分器核对文章及 PDF 哈希、审计结构、引文位置、编审处理记录和覆盖映射；任一记录过期便不显示完整内部总分。`assessment/review.json` 中的主观分数须在审阅当前文本后重新评定并更新文章哈希，不能由程序自动加分；AI 辅助内部编审不得冒称独立专家复核。

运行测试：`python -m unittest discover -s tests -v`。评分报告只用于内部改进；最终 ZIP 格式须在实际打包后单独验收。

## 模型接口

程序支持 Responses 与兼容 Chat Completions 的 HTTPS 接口。TOML 方式读取 model_provider、model、model_providers 下的 base_url、wire_api、experimental_bearer_token 和自定义请求头。不同服务商可能有额外要求，接入时按其官方文档调整适配层。

## 安全与可追溯

日志不保存 API 密钥或完整 API 地址，但包含原文片段和模型响应。按赛事允许的范围保存或提交日志。evidence.json 的摘录在生成前逐页验证；这证明摘录位置存在，不自动证明成稿语义正确。
