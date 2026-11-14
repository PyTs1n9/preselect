# 卡级看板

状态只允许：`todo` / `doing` / `done` / `blocked`。
`blocked` 必须在备注写清阻塞原因与解除条件。

| 卡 | Wave | owner | 状态 | 验收退出码 | 备注 |
|---|---|---|---|---|---|
| W0 | W0 | S0 | done | 0 | 19:39–20:20 完成，但模型链路一度写成 HTTP；21:30 已纠偏为 Codex SDK 并通过验收 |
| A6 | W1 | S1 | doing | — | pdf_input 已实现；渲染改为必做；待补加密/空页用例 |
| A7 | W1 | S1 | doing | — | codex_adapter 已重写为 SDK 版，`generate()` 实测返回 completed |
| A9 | W1 | S2 | done | 0 | 八类中 5 类实现，5/6/8 锁 B 字段；`python -m pytest -q` 81 passed；评审修复见 handoffs/REVIEW-FIXES-2026-11-13.md |
| A8 | W1 | S3 | done | 0 | `pytest -q` 137 passed；三阶段已实现并落盘登记；stub_prompts 为工程夹具、非运行资产；快照测试参数化收窄为 `glob("run_*")`；正式 run 停在 ASSET_MISSING 属计划内终点，见 handoffs/A8.md |
| A11 | W2 | S3 | done | 0 | 162 passed；prompts/ 未交付，真实 run 停在 ASSET_MISSING 属计划内终点 |
| A14 | W2 | S2 | todo | — | 先做 1–4 例 |
| A15 | W2 | S3 | blocked | 7 | 门禁与读回机制、拒绝路径已交付并验收；7 为拒绝路径退出码；正向 exit=0 依赖 A14 关闭五项一致性检查或按 §11 变更 I6 第 290 行 |
| A17 | W2 | S0 | done | 0 | evidence schema 1.0.0 落盘（B1 片段原样，A 未增删字段）；5 个工程夹具迁移并重生成 validation.json；194 passed 未降；I3 已登记，B 正式双签待补 |
| A18 | W2 | S0 | done | 0 | recognition/document_ir 按 strict 模式落盘 1.0.0、evidence 升 1.0.1；识别改 5 页/批并校验页覆盖；写入改两段式（draft_en → draft）；201 passed。未覆盖：prompts/translate.md 未交付，真实 run 停在 ASSET_MISSING；block_id 交叉校验归 A14 |
| A19 | W2 | S0 | done | 0 | 文本层优先、按页视觉；239 passed；MED-04 五页全 text_page、0 次模型调用，manifest recognize 1103.935ms、CLI 总耗时 1493.862ms；recognition schema 通过；三专用产物下游尚未消费，正式双签待补 |
| A10 | W2 | S0 | todo | — | 端到端，只跑不改 |
| A13 | W3 | S0 | todo | — | 四份材料串行 |
| A16 | W3 | S0 | todo | — | 干净环境 |
| A1 | W1 | S1 | done | 0 | 网关模式无需 codex 登录；卡面 exit 8 已被 I7 冻结条款取代为 2 |
| A12 | 待定 | S0 | todo | — | 等 A10 产出真实 run 目录 |
