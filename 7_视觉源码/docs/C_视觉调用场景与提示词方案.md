# 视觉调用场景与提示词方案（C 卷）

> 依据：`source_code` 现有五阶段、I2/I3/I4 契约、`skills/pdf-vision/SKILL.md` v1.1.0、
> 四份真实材料（AI-01/BIO-02/IND-03/MED-04，均有文本层）的实测。
> 定位：本文件是**方案**，不是已落盘资产。提示词可直接复制为 `prompts/*.md`，
> 但新增 schema 属 I3 变更，须 B 确认后由 A 落盘。

## 1. 判定原则（按顺序适用，前一条成立就不走后面）

1. **能用确定性代码拿到，就不调模型**：页码、页眉页脚、DOI、参考文献、字数、坐标、字符偏移。
2. **能走文本层，就不走视觉**：pypdf 提取 + 三步清洗（连字 `ﬁ→fi`、字距 `P h . D . → Ph.D.`、行尾断词）。
3. **必须走视觉时，限定区域与输出结构**：一次只问一类内容（表格 / 图 / 公式 / 整页转写），输出过 schema，不留自由文本。

## 2. 页级触发判定（确定性，先算后调）

```python
text_chars = len(clean(pypdf_page_text))
numeric_ratio = digits / max(1, text_chars)
has_formula_marker = bool(re.search(r"[=∑√∫±^_]", raw_text))

if text_chars < 200:                      kind = "scan_page"      # 无文本层或极稀疏 → 整页转写
elif numeric_ratio > 0.30:                kind = "table_page"     # 数字密集 → 表格结构化
elif has_formula_marker:                  kind = "formula_page"   # 公式符号 → 公式线性化
else:                                     kind = "text_page"      # 正文 → 不调模型
```

图（figure）无法由文本密度判定：只在 `text_page` 中按"页内图像块面积占比 > 15%"追加一次 figure 调用。
阈值写成 `pipeline/extract.py` 的模块常量，不散落在代码里。

## 3. 场景总表

| # | 场景 | 判定信号 | 方案 | 产物 | 是否调模型 |
|---|---|---|---|---|---|
| S1 | 正文页（有文本层） | 上面 `kind=text_page` | pypdf + 清洗 | `page_text.json` | ❌ |
| S2 | 页眉/页脚/DOI/页码 | 版面位置或正则 | 规则过滤，不进入 IR 正文块 | — | ❌ |
| S3 | 参考文献页 | 命中 `References/Bibliography` 标题 | 文本层整段保留，不分块 | — | ❌ |
| S4 | 扫描页（无文本层） | `text_chars < 200` | 整页转写（`vision`） | `recognition.json` | ✅ |
| S5 | 表格页 | `numeric_ratio > 0.30` | 表格结构化（`table`） | `tables.json` | ✅ |
| S6 | 公式页 | 含公式符号 | 公式线性化（`formula`） | `formulas.json` | ✅ |
| S7 | 图/示意图 | 图像块面积占比 > 15% | 图描述（`figure`） | `figures.json` | ✅ |
| S8 | 文本层与图像冲突 | 数字集合不一致 | 逐页数字比对（确定性） | `conflicts.json` | ❌ |
| S9 | 评审对照页 | 打包前 | 渲染 PNG + 报告并排 | `reader/` | ❌ |
| S10 | 手写批注/印章 | 视觉首轮发现 | 归入 S4 的 block，`text` 留空 + region | — | ✅（复用 S4） |

## 4. 提示词 1 · `vision` v1.2.0（整页转写，仅扫描页）

**适用**：`text_chars < 200` 的页；或用户明确要求“照着原页读”。正文页走文本层，不用这条。

**输入约定**：单页 PNG（200 DPI）+ 页号；不传整篇。

**输出约定**：`recognition.schema.json` 1.0.0（`pages[].blocks[]`，含 `block_id`/`text`/`region`）。

```markdown
<!-- version: 1.2.0 -->
你负责把一页 PDF 页面图像转成可追溯的识别 JSON。

只输出一个 JSON 对象，顶层 pages 数组，只有一个页对象：page_number 等于给定页号，blocks 按阅读顺序排列。
每个 block 含 block_id（B001 起连续）、text、region（归一化 [x0,y0,x1,y1]，0–1，原点左上；无法定位写 null）。

硬规则：
1 逐字转录原文：不翻译、不改写、不纠正错别字、不合并段落、不补全被裁切的词，保留原文标点与大小写。
2 图像模糊、被遮挡、裁切导致无法辨认时，保留该区域的 block，text 留空字符串，用 region 标出。禁止猜测。
3 阅读顺序：单栏自上而下；多栏按列优先；图注紧随其图。
4 表格：每个单元格一个 block，按行优先，单元格文本逐字保留，不要把整表合并成一段。
5 公式：保留可见字符的线性写法（如 R = k * C），不使用 LaTeX 记号，不补全无法辨认的符号。
6 页眉、页脚、页码、DOI 也照实转录成独立 block，不要丢弃，由上游决定是否使用。
7 只转录：不总结、不分章、不评价、不补充页面之外的背景知识。
不要输出 Markdown、代码围栏、解释。
```

**关键约束**：一次一页（实测两页即超 300s）；`region` 必须 0–1；`block_id` 页内连续；页号由调用方给定，模型不得自选。

## 5. 提示词 2 · `table` v1.0.0（表格结构化）

**适用**：数字密集页；或正文中引用了“表 N”而文本层表格明显错位的页。

**输入约定**：单页 PNG + 页号。**不传**文本层，避免模型被错位文本带偏。

**输出约定**：`table.schema.json`（新增，见 §9）。核心：`tables[].header` 与 `rows` 列数一致。

```markdown
<!-- version: 1.0.0 -->
你负责把一页 PDF 图像中的表格转成结构化 JSON。

只输出一个 JSON 对象：{"page_number": <给定页号>, "tables": []}。
每个表格包含：
- table_id：T01 起，页内唯一
- caption：表题原文；没有表题写 null
- header：表头单元格文本数组，从左到右；合并表头按重复填写，保证列数一致
- rows：二维字符串数组，行优先；每一行的列数必须等于 header 列数
- bbox：归一化 [x0,y0,x1,y1]，0–1，原点左上；无法确定写 null

硬规则：
1 单元格逐字转录，不翻译、不改写、不做单位换算、不补全无法辨认的内容（写空字符串）。
2 数字与单位保持原样：1,234 不改成 1234；80–90% 保留区间号；±、<、>、上标照抄。
3 合并单元格：在 rows 中重复其文本，保持矩形结构，禁止留空导致列错位。
4 跨页续表：只转录当前页出现的行，不合并其他页。
5 无法判断是否属于表头时，放进 rows，不要猜。
6 本页没有表格时输出 {"page_number": <页号>, "tables": []}。
不要输出 Markdown、代码围栏、解释。
```

**关键约束**：`rows` 每行列数 == `header` 长度（A 侧校验，不符即 `SCHEMA_INVALID`）；数字不做任何规范化（保留千分位与区间号）。

## 6. 提示词 3 · `figure` v1.0.0（图描述）

**适用**：正文需要谈“图 N 显示了什么”，而文本层只有图注。

**输出约定**：`figure.schema.json`（新增）。`description` 是中文，`readouts` 只收图中可读出的数。

```markdown
<!-- version: 1.0.0 -->
你负责描述一页 PDF 图像中的图（折线图、柱状图、示意图、流程图、照片）。

只输出一个 JSON 对象：{"page_number": <给定页号>, "figures": []}。
每个图包含：
- figure_id：G01 起，页内唯一
- caption：图注原文；没有写 null
- description：60–120 字中文，说明这张图在展示什么关系、趋势或结构；不得引入图中看不到的结论
- readouts：[{label, value_text}]，label 用图中原文（如 "Accuracy"），value_text 逐字保留（如 "0.82"、"12%"）；没有可读读数写 []
- bbox：归一化 [x0,y0,x1,y1] 或 null

硬规则：
1 只描述图中可见内容：坐标轴标题、图例、标注按原文抄；看不清的刻度直接略过，不要猜。
2 不做因果推断、不补背景知识、不评价方法好坏。
3 示意图按“由谁指向谁、经过哪些环节”描述，不臆造未画出的步骤。
4 本页没有图时输出 {"page_number": <页号>, "figures": []}。
不要输出 Markdown、代码围栏、解释。
```

**关键约束**：`description` 不得出现原文与图中都没有的实体或数字；`readouts` 的 `value_text` 必须是字符串原样，不做数值化。

## 7. 提示词 4 · `formula` v1.0.0（公式线性化）

**适用**：含公式的页；写作需要“准确复现公式”或“解释符号含义”。

**输出约定**：`formula.schema.json`（新增）。`text` 禁止 LaTeX。

```markdown
<!-- version: 1.0.0 -->
你负责把一页 PDF 图像中的公式转成线性文本。

只输出一个 JSON 对象：{"page_number": <给定页号>, "formulas": []}。
每个公式包含：
- formula_id：E01 起，页内唯一
- text：线性写法，例如 "R = k * C / (1 + t)"、"P(A|B) = P(B|A) * P(A) / P(B)"
- symbols：[{symbol, meaning}]，symbol 用原字符；meaning 只填图中明确标注的含义，未标注写 null
- bbox：归一化 [x0,y0,x1,y1] 或 null

硬规则：
1 禁止 LaTeX：不出现 $、\frac、^{}、\alpha 之类记号。
2 上下标用 ^ 与 _；希腊字母用英文名（alpha、beta、mu）；根号写 sqrt()，求和写 sum()，积分写 integral()。
3 无法辨认的符号写 "?"，不要猜；也不要为了“完整”补一个没出现的变量。
4 不推导、不变形、不解释公式用途；只转录与标注图中已有的符号含义。
5 本页没有公式时输出 {"page_number": <页号>, "formulas": []}。
不要输出 Markdown、代码围栏、解释。
```

## 8. 非模型方案（同样重要）

**A. 文本层与图像冲突复核（S8，确定性）**
`vision`/`table` 产出的数字与 pypdf 文本层数字做集合比对：正则 `-?\d+(?:[.,]\d+)?%?`，忽略千分位差异。
不一致时以**文本层为准**，把差异写入 `manifest.issues`（warning），不阻断；同一页差异超过 3 处时判定该页视觉结果不可信，回退纯文本。

**B. 区域裁切（降 token 与干扰）**
先整页跑一次 `table`/`figure` 得到 bbox，再按 bbox 裁切重跑一次，比对两次结果；不一致以整页结果为准并记 warning。裁切用 `pypdfium2` 已有依赖，不引入新库。

**C. 对照页（S9）**
渲染 PNG + 最终 md 并排，点引用高亮原文块。只渲染不调模型，评审用。

## 9. 新增 schema（待 B 批准，strict 兼容写法）

三份都遵循：每个 object `additionalProperties:false`、`required` 覆盖全部 properties、禁用 `oneOf`、可空字段用 `["type","null"]`。

```json
// table.schema.json（schema_version 1.0.0）
{"type":"object","additionalProperties":false,"required":["page_number","tables"],
 "properties":{"page_number":{"type":"integer","minimum":1},
  "tables":{"type":"array","items":{"type":"object","additionalProperties":false,
   "required":["table_id","caption","header","rows","bbox"],
   "properties":{
    "table_id":{"type":"string","pattern":"^T[0-9]{2,}$"},
    "caption":{"type":["string","null"]},
    "header":{"type":"array","items":{"type":"string"}},
    "rows":{"type":"array","items":{"type":"array","items":{"type":"string"}}},
    "bbox":{"type":["array","null"],"minItems":4,"maxItems":4,
            "items":{"type":"number","minimum":0,"maximum":1}}}}}}}
```

```json
// figure.schema.json
{"type":"object","additionalProperties":false,"required":["page_number","figures"],
 "properties":{"page_number":{"type":"integer","minimum":1},
  "figures":{"type":"array","items":{"type":"object","additionalProperties":false,
   "required":["figure_id","caption","description","readouts","bbox"],
   "properties":{
    "figure_id":{"type":"string","pattern":"^G[0-9]{2,}$"},
    "caption":{"type":["string","null"]},
    "description":{"type":"string","minLength":20},
    "readouts":{"type":"array","items":{"type":"object","additionalProperties":false,
      "required":["label","value_text"],
      "properties":{"label":{"type":"string"},"value_text":{"type":"string"}}}},
    "bbox":{"type":["array","null"],"minItems":4,"maxItems":4,
            "items":{"type":"number","minimum":0,"maximum":1}}}}}}}
```

```json
// formula.schema.json
{"type":"object","additionalProperties":false,"required":["page_number","formulas"],
 "properties":{"page_number":{"type":"integer","minimum":1},
  "formulas":{"type":"array","items":{"type":"object","additionalProperties":false,
   "required":["formula_id","text","symbols","bbox"],
   "properties":{
    "formula_id":{"type":"string","pattern":"^E[0-9]{2,}$"},
    "text":{"type":"string","minLength":1,"not":{"pattern":"\\\\|\\$"}},
    "symbols":{"type":"array","items":{"type":"object","additionalProperties":false,
      "required":["symbol","meaning"],
      "properties":{"symbol":{"type":"string"},"meaning":{"type":["string","null"]}}}},
    "bbox":{"type":["array","null"],"minItems":4,"maxItems":4,
            "items":{"type":"number","minimum":0,"maximum":1}}}}}}}
```

> `formula.text` 的 `not` 约束只能拦 `\` 与 `$`；`^{}` 之类需在代码里再查一次，schema 表达不了。

## 10. 落地映射（A 侧改动清单）

| 项 | 内容 |
|---|---|
| 新阶段 | `extract`（`REQUIRES=[]`，`PRODUCES=["page_plan","recognition","tables","figures","formulas"]` 按需产出） |
| 触发实现 | `pipeline/extract.py`：`classify_page()` + 阈值常量；结论写 `page_plan.json`，每页一行 `{page_number, kind, reasons[]}` |
| 产物命名 | I2 新增 `page_text.json`、`page_plan.json`、`tables.json`、`figures.json`、`formulas.json`、`conflicts.json` |
| 提示词 | `prompts/vision.md` 升 1.2.0；新增 `table.md`、`figure.md`、`formula.md`（各 1.0.0） |
| schema | 新增三份并登记 `SUPPORTED_VERSIONS`；`MODEL_OUTPUT_SCHEMAS` 增加三项，自动走 strict 预检 |
| manifest | `calls` 按 `table/figure/formula/vision` 分别计数；`page_plan` 记录每页走了哪条路 |

## 11. 抽样验收（12 页，四份材料各 3 页）

每份取 1 个正文页、1 个表格页、1 个图页，比较三条路线：

| 路线 | 指标 |
|---|---|
| 纯文本 | 耗时、字符数、数字集合 |
| 纯视觉 | 耗时、块数、数字集合 |
| 混合（本方案） | 耗时、数字集合差异条数、人工抽查 10 个数字的正确数 |

通过标准：混合路线的数字与文本层差异 ≤ 1 处/页；耗时不超过纯视觉的 30%；正文页 0 次模型调用。

## 12. 需要决策的三件事

1. 三份新 schema 是否批准（属 I3 变更，需要 B 签字）。
2. `extract` 是否作为独立阶段（会改 I2 产物布局），还是并入 `recognize`（改动小、但阶段职责变混）。
3. 图描述要不要进最终 md（影响写作提示词与字数预算，属 B 的内容决策）。
