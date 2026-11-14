# pdf-vision SKILL.md v1.1.0（已定稿并落盘）

> 提出方：A（系统工程）　内容权：B　落盘：A（2026-11-14，`source_code/skills/pdf-vision/SKILL.md`）
> 决策记录：B 于 2026-11-14 逐条答复下列 5 项，A 按答复定稿；措辞若需调整，B 直接改文件即可，不必再走本草案。
> 依据：I4 资产契约、`pipeline/stages/recognize.py` 的实际注入点、Agent Skills 规范的 frontmatter 要求。

## 1. v1.0.0 的问题（已修，留档）

| # | 问题 | 证据 |
|---|---|---|
| 1 | 无 YAML frontmatter | 首行是 `# PDF Vision Skill`；规范要求 `name` + `description`，后者决定是否被加载 |
| 2 | 把流水线写成模型职责 | 7 步里“渲染、调 provider、归一化、跑校验、修复、写 manifest”全是 A 的代码；skill 只在 `recognize` 阶段经 `SkillInput` 注入（`stages/recognize.py:23`） |
| 3 | 作用域串到证据层 | “Attach page and region evidence to every extracted claim”是证据阶段的职责 |
| 4 | 缺块级规则 | 页不漏、`block_id` 唯一、阅读顺序、表格/公式处理均无规定 |
| 5 | 提了不存在的环节 | “or OCR result”——本链路只有页面图像 |
| 6 | `region` 坐标系未定义 | 文件写“source-page pixels”，`PageInfo` 却是 PDF 点，渲染是 200 DPI 像素，相差 2.78 倍 |

## 2. 五条决策与落地映射

| # | B 的决策 | 落在哪 | 副作用 |
|---|---|---|---|
| 1 | `region` 用**归一化 0–1** | SKILL.md「坐标」节：`{"x0","y0","x1","y1"}`，原点左上，按整页宽高归一 | 改 DPI 不返工；但 schema 未声明项结构，A 暂时只能靠自检约束（见 §4） |
| 2 | **不加新字段** | 不引入 `kind` | 表格/图/公式的语义在 IR 里不可标记，下游只能靠文本形态与位置推断 |
| 3 | 表格**每单元格一个 block** | SKILL.md 硬规则 6 | 引用粒度变细：引用一条事实可能连引多个 block；行内拼接由写作层负责 |
| 4 | **不允许 LaTeX** | SKILL.md 硬规则 7：线性写法，不补全不可辨符号 | `quote` 可逐字比对，`CITE_NOT_IN_EVIDENCE` 保持可验证 |
| 5 | **必须翻译**（工具本质是把英文论文变成中文可读） | 见 §3：翻译放在写作层，识别层仍逐字保原文 | 追溯链可保；若翻译上移到识别层，链条断裂 |

## 3. 翻译的分层（本稿最关键的一处）

“必须翻译”与“逐字转录”看起来冲突，实际是两层：

| 层 | 语言 | 产物 | 为什么这样定 |
|---|---|---|---|
| 识别（本 skill） | **源语言，逐字** | `recognition.json` → `document_ir.json` → `evidence.quote` | `quote` 是追溯锚点，必须能在页面图像上原样搜到；译成中文后就无法回查 PDF，A14 的引用校验会退化成“只校验编号存在” |
| 写作（`writer.md`） | **中文** | `final.md` 正文 | 读者要的是中文解读；数值与单位原样搬用，术语保留英文 |

因此：**正文是中文，引用指向英文原文片段**。这满足“把英文论文变成中文可读”，同时让评委能逐跳核对“这句话来自第几页哪一块的哪句原文”。

若你要求识别层也输出中文，需要接受两个后果：`quote` 与页面文字不再一致、人工复核必须靠人读英文原文比对；A 会按你的指示改，但会把这条写进 I4 的风险项。

## 4. 落地后的影响（A 侧已核）

1. **不改代码**：`load_skill` 只校验存在性、UTF-8 解码与 sha256（`pipeline/prompt_loader.py:40`）；frontmatter 不影响加载。
2. **manifest 哈希变化**：`assets.skills["pdf-vision"]` 的 sha256 改变，历史 run 与新 run 不可直接比对，属预期。
3. **`region` 仍是自由 object**：`schemas/evidence.schema.json` 与 `document_ir` 都未声明 `region` 项结构，SKILL.md 的 0–1 约定目前只有提示词层面的约束力。若 B 允许把 `x0/y0/x1/y1` 写进 schema 的 `region.items`（仅补 properties，不新增顶层键），A14 才能做确定性校验；否则只能靠自检清单。
4. **`writer.md` 需要三行补充**（B 侧、提示词资产，A 不改）：
   - 输入可能为英文，正文用中文译述，数值与单位不得改变；
   - 专有名词、模型名、数据集名、机构名保留英文原形；
   - 来源节的 `quote` 保留原文语言，不翻译。
5. **表格语义代价**：不加 `kind` 后，IR 无法区分“表格单元格”与“普通短行”，写作阶段只能按位置与文本形态推断；若后续发现引用错位，优先怀疑这一点。

## 5. 来源与许可

- 参考实现：[c-narcissus/agent-paper-grounded-reading](https://github.com/c-narcissus/agent-paper-grounded-reading)。仅借鉴规则表述（“没有锚点不许落笔”“never invent evidence”、收尾自检），**未复制其模板、脚本或 reader 资源**；该仓库页面未见 LICENSE 声明。
- 规范：[Agent Skills](https://skill.md/)；frontmatter 最小集取自 [anthropics/skills 的 template](https://github.com/anthropics/skills)，该文件仅 `name` 与 `description` 两个字段。
