# 卡级看板

状态只允许：`todo` / `doing` / `done` / `blocked`。
`blocked` 必须在备注写清阻塞原因与解除条件。

| 卡 | Wave | owner | 状态 | 验收退出码 | 备注 |
|---|---|---|---|---|---|
| W0 | W0 | S0 | done | 0 | 19:39–20:20 完成，但模型链路一度写成 HTTP；21:30 已纠偏为 Codex SDK 并通过验收 |
| A6 | W1 | S1 | doing | — | pdf_input 已实现；渲染改为必做；待补加密/空页用例 |
| A7 | W1 | S1 | doing | — | codex_adapter 已重写为 SDK 版，`generate()` 实测返回 completed |
| A9 | W1 | S2 | done | 0 | 八类中 5 类实现，5/6/8 锁 B 字段；`python -m pytest -q` 81 passed；评审修复见 handoffs/REVIEW-FIXES-2026-11-13.md |
| A8 | W1 | S3 | todo | — | stages/ 只有自动发现，三个阶段未实现 |
| A11 | W2 | S1 | todo | — | writer/repair 仍是 9 行骨架 |
| A14 | W2 | S2 | todo | — | 先做 1–4 例 |
| A15 | W2 | S3 | todo | — | packaging.py 仍是 10 行骨架 |
| A10 | W2 | S0 | todo | — | 端到端，只跑不改 |
| A13 | W3 | S0 | todo | — | 四份材料串行 |
| A16 | W3 | S0 | todo | — | 干净环境 |
| A1 | W1 | S1 | todo | — | 凭据阻塞已解除（全局 config.toml 已配网关），可立即执行 |
| A12 | 待定 | S0 | todo | — | 等 A10 产出真实 run 目录 |
