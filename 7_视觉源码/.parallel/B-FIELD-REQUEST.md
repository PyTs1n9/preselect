# 给 B 的字段请求单（阻塞 A9 的三项校验）

> 提出方：A（系统工程）　答复方：B（Skill 与质量）
> 依据：`docs/接口冻结清单_A-B.md` 的 I3 —— evidence 的字段提案权在 B，A 只落盘、不发明。
> 影响：A9 的引用校验、数值一致性、串稿检测三项无法实现，其余五项已开工。
> 请在下方「答复」区直接填写，不必另开会话。

---

## 1. 为什么现在卡住

`schemas/evidence.schema.json` 当前只声明了三个字段：

```json
{"page_number": "integer >= 1", "region": "object", "quote": "string"}
```

它是**单条事实**的形状，没有列表容器。而 I3 的表格写的是"事实列表，每条含
`page_number`、`block_id`、`quote`"——`block_id` 在文件里也没有。

A9 需要回答三个问题，现在的 schema 一个都回答不了：

1. 正文的 `[3]` 指的是 evidence 里的哪一条？（缺唯一标识）
2. 哪些事实是"必须被引用"的核心事实？（缺 core 标记）
3. 正文写"准确率 92.3%"时，怎么判断证据里支持的是同一个数、同一个单位？（缺数值与单位）

## 2. evidence 需要补的字段

请按你的语义设计填写。A 不预设类型，下面只是"要解决什么问题"。

| # | 要解决的问题 | 建议字段 | 你的答复 |
|---|---|---|---|
| 1 | 事实集合放在哪个顶层键下，元素是什么结构 | `facts: [...]` 或 `items: [...]` | |
| 2 | 每条事实的唯一标识，供正文 `[n]` 映射 | `fact_id` 或 `id` | |
| 3 | 标记核心事实，供"未被引用"检查 | `core: boolean` 或 `priority: core\|supporting` | |
| 4 | 数值，供一致性比对 | `value: number` | |
| 5 | 单位，供"同值同单位"判定 | `unit: string` | |
| 6 | 块号，I3 已要求但 schema 缺 | `block_id: string` | |

补充说明（如适用请写在答复里）：

- `value` 是否会出现区间（"80–90%"）、多值（"3 组，分别为…"）、负数、科学计数法？
  如果会，是否需要 `value_text: string` 保留原始表述？
- `unit` 是否会出现复合单位（"mg/kg"）、无量纲（"倍"）、百分比与小数混用？
- 一条事实是否可能对应多个页码或多个块？如果是，字段要设计成数组。

## 3. 正文侧需要约定的格式

这三项不是 schema，是 Markdown 的书写约定，同样由 B 定。

| # | 要解决的问题 | 你的答复 |
|---|---|---|
| 7 | `material_id` 写在正文什么位置、什么格式（front-matter / HTML 注释 / 首行 / 其他） | |
| 8 | 标题与章节标题的写法（`# 标题`？固定五节？节名用词？） | |
| 9 | 引用标记与来源块的写法（正文 `[1]`，来源块在哪一节、每行什么格式） | |

## 4. 需要你定义的判定口径

| # | 问题 | 你的答复 |
|---|---|---|
| 10 | "core 事实"的判定标准：全部事实？前 N 条？由你在 evidence 里逐条标注？ | |
| 11 | 数值一致性容差：精确相等，还是允许四舍五入到几位？百分比与小数是否等价？ | |
| 12 | 引用编号是否允许跳号（有 [1][3] 无 [2]）、是否允许一条事实被多次引用？ | |

## 5. 答复后 A 会做什么

1. 按你给的片段**原样落盘**到 `schemas/evidence.schema.json`，A 不增删字段。
2. 把 `checks.skipped` 中对应的五条 code 移除，A9 补齐三项校验。
3. 用你给的 `fixtures/quality/` 夹具跑 `tests/test_engineering_fixtures.py` 验证。
4. 若你的字段与 I3 表格冲突（例如不打算要 `block_id`），同步改 I3 并升 schema 版本。

## 6. 最小可用集

如果时间紧，**先给第 1、2、3 项**（列表容器、唯一标识、core 标记）就能解锁引用校验；
第 4、5 项解锁数值校验；第 7 项解锁串稿检测。可以分批给，A9 按批移除 skipped。

---

## 答复区

```text
## evidence 字段
facts_container:
fact_id:
core:
value:
unit:
block_id:

## 正文元数据
material_id_position:
title_and_sections:
citation_and_sources:

## 判定口径
core_definition:
number_tolerance:
citation_rules:

## 计划
可交付时间:
是否需要同步修改 I3:
```
