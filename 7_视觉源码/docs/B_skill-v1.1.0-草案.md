# pdf-vision SKILL.md v1.1.0 草案（A 提，B 审）

> 提出方：A（系统工程）　内容权：B　落盘方：B（`source_code/skills/pdf-vision/SKILL.md`）
> 依据：I4 的资产契约、`pipeline/stages/recognize.py` 的实际注入点、Agent Skills 规范的 frontmatter 要求。
> 本文只出草案与差异，不改现有 SKILL.md，也不改任何代码。

## 1. 现行 v1.0.0 的问题（逐条有据）

现行文件 927 字节，内容为 `# PDF Vision Skill` + Purpose + Required flow（7 步）+ Quality rules（4 条）。

| # | 问题 | 证据 |
|---|---|---|
| 1 | **没有 YAML frontmatter**，缺 `name` / `description` | 文件首行是 `# PDF Vision Skill`；规范要求 frontmatter 至少含这两个字段 |
| 2 | **描述的是流水线，不是模型的职责** | 第 1–7 步要求"渲染页面、调 provider、归一化、跑 schema 校验、修复、写 manifest"，这些全部由 A 的代码完成。skill 只在 `recognize` 阶段经 `SkillInput(path)` 注入（`stages/recognize.py:23`），模型只负责"看图 → 输出 recognition JSON" |
| 3 | **作用域串层** | "Attach page and region evidence to every extracted claim" 属证据层（`evidence` 阶段）的职责；在 recognize 阶段谈 claim，模型会去抽取事实，产出的结构过不了 recognition schema |
| 4 | **缺块级规则** | 没有一条规定：页必须 1..N 不漏、`block_id` 页内唯一、阅读顺序、多栏/表格/公式怎么处理、不能翻译不能改写 |
| 5 | **提了不存在的环节** | "or OCR result"——本链路只有页面图像与模型，没有 OCR 步骤，这句话会诱导模型声称做过 OCR |
| 6 | **`region` 坐标系未定义** | 文件写"source-page pixels"，但 `PageInfo` 用的是 PDF 点（`width_pt`/`height_pt`），渲染却是 200 DPI 的 PNG 像素。两个空间差约 2.78 倍，schema 里 `region` 又是自由 object，没有任何校验能兜住 |

## 2. 三方对照

| 维度 | 现行 v1.0.0 | Agent Skills 规范 | 参考实现 `agent-paper-grounded-reading` |
|---|---|---|---|
| 元数据 | 无 | frontmatter `name` + `description`（description 决定是否被加载） | 有 frontmatter 与 `agents/openai.yaml` |
| 正文定位 | 流水线 7 步 | 程序性工作流，重内容放 `references/` | 明确"每次跑完必须产出哪些工件" |
| 证据纪律 | 一句"never invent" | — | "Do not silently leave any claim ungrounded"；"never invent evidence"；推断与证据分级标注 |
| 自检 | 无 | — | 收尾必须跑 `validate_*` 脚本 |
| 产出契约 | 无 | — | 每个工件文件名与字段固定 |

可直接借的三条（只借规则表述，不搬模板与脚本）：
1. **"没有锚点就不许落笔"**——对应我们的"每个 block 必须有页号与 region"。
2. **证据、合理推断、猜测三级标注**——对应"不可辨区域必须留空，不许猜"。
3. **收尾自检**——对应"页数必须等于输入页数"，由 A 侧校验，skill 里只声明义务。

## 3. v1.1.0 正文草案（可直接替换）

```markdown
---
name: pdf-vision
description: 把 PDF 页面图像逐页转成可追溯的识别 JSON（稳定 block_id、逐字原文、页内区域），供结构归一化与证据抽取使用。仅在需要从 PDF 页面图像生成 recognition JSON 时使用。
---

# PDF Vision

## 何时生效

你在识别阶段被调用：输入是若干页 PNG 图像与页数，输出是一个 recognition JSON 对象。
结构归一化、事实抽取、写作、校验、修复都由上游其他阶段完成，不在你的职责内。

## 输出契约

- 只输出一个 JSON 对象。不要输出 Markdown、代码围栏、解释、前言或结语。
- 顶层键 `pages`，元素为页对象，按 `page_number` 升序。
- 页对象含 `page_number`（从 1 开始）与 `blocks`（数组，顺序即阅读顺序）。
- 每个 block 含 `block_id`、`text`，可选 `region`。

## 硬规则

1. **逐页覆盖**：输入 N 页就必须输出 1..N 每页恰好一次，不重不漏。
2. **逐字转录**：不翻译、不改写、不纠错别字、不合并段落、不补全被裁切的词，保留原文标点与大小写。
3. **不臆造**：图像模糊、被遮挡、裁切导致无法辨认时，保留该区域的 block，`text` 留空字符串，靠 `region` 标出位置。禁止根据上下文猜测内容。
4. **block_id 稳定**：页内唯一、形如 `B001`，按阅读顺序编号；同一页重复识别时编号规则一致。
5. **阅读顺序**：单栏自上而下；多栏按列优先；图注紧随其图；表格按行优先，单元格文本用制表符分隔。
6. **公式**：保留可见字符，用线性写法（如 `R = k * C`）；无法线性化的符号保留原字符，不编造含义。
7. **只转录**：不总结、不分章、不评价、不补充页面之外的背景知识。
8. **不输出元数据**：标题、作者、期刊等由上游提供，不要凭图像猜。

## 失败处理

- 某页图像缺失或整页不可辨：仍然输出该页对象，`blocks` 为空数组，不要用占位文本填充。
- 任何情况下都不要为了"看起来完整"而虚构 block。

## 自检（提交前逐条过）

- [ ] `pages` 长度等于输入的页数，`page_number` 恰好覆盖 1..N
- [ ] 每个 block 的 `block_id` 页内唯一，且从 `B001` 连续
- [ ] 没有空 `text` 且无 `region` 的 block
- [ ] 输出是纯 JSON，能被 `json.loads` 直接解析
```

## 4. 需要 B 定的 5 件事（我不代定）

| # | 决策点 | 选项 | 影响 |
|---|---|---|---|
| 1 | `region` 坐标系 | (a) PDF 点，原点左上（与 `PageInfo.width_pt` 同空间）　(b) 渲染像素 @200dpi　(c) 归一化 0–1 | 决定 A 能否校验 region 落在页内；选 (c) 最耐改 DPI |
| 2 | 是否需要块类型字段 | 增加 `kind: text\|table\|figure\|equation`，或不加 | A14 的追溯与人工复核受益；属新字段，须走 I3 变更 |
| 3 | 表格粒度 | 整表一个 block / 每个单元格一个 block | 影响 `block_id` 数量与 evidence 引用粒度 |
| 4 | 公式是否允许 LaTeX 化 | 仅线性文本（可逐字校验）/ 允许 `$...$` | 允许 LaTeX 会让 `quote` 逐字比对失效，`CITE_NOT_IN_EVIDENCE` 需放宽 |
| 5 | 语言策略 | 原文语言原样保留（不翻译） | 若允许翻译，`quote` 与原文不同，追溯链断 |

## 5. B 采纳后的影响（A 侧已核）

1. **不改任何代码**：`load_skill` 只校验存在性、UTF-8 解码与 sha256，frontmatter 不影响加载（`pipeline/prompt_loader.py:40`）。
2. **manifest 会变**：`assets.skills["pdf-vision"]` 的 sha256 改变，历史 run 与新 run 不可直接比对该哈希，属预期。
3. **I4 需要一行补充**：I4.1 只对 `prompts/*.md` 规定了首行版本注释，skill 没有版本约定；建议给 skill 也加 `<!-- version: 1.1.0 -->` 或写进 frontmatter，由 B 定。
4. **顺带发现的 A 侧小缺口**（不在本草案范围）：`load_skill` 不拒绝空文件，而 I4 的表格要求"为空或仅空白 → `ASSET_EMPTY`"。建议单开一张小卡修，不在 B 的交付里混改。

## 6. 来源与许可

- 参考实现：[c-narcissus/agent-paper-grounded-reading](https://github.com/c-narcissus/agent-paper-grounded-reading)。仅借鉴规则表述（"Do not silently leave any claim ungrounded"、"never invent evidence"、收尾自检），**不复制其模板、脚本或 reader 资源**；该仓库页面未见 LICENSE 声明，未做任何代码级复用。
- 规范：[Agent Skills](https://skill.md/)；frontmatter 最小集取自 [anthropics/skills 的 template](https://github.com/anthropics/skills)，该文件仅 `name` 与 `description` 两个字段。
- 若 B 决定采纳，请在本文末尾批注"采纳/部分采纳/不采纳 + 理由"，A 再按批注落盘并复跑 recognize。
