# 看板对账（2026-11-14）

本记录只对齐 `git log main` 与现有 `.parallel/handoffs/*.md`，不新增实现、不补造交接文件。

## 判定规则

- 有明确提交且有对应交接文件：可标 `done`。
- 仅有提交、缺对应交接文件：标 `blocked`，备注“缺交接/缺证据”。
- 无提交且无交接文件：标 `blocked`，备注“缺交接/缺证据”。
- 已有历史 blocked 状态保留，并写明解除条件。

## 逐卡证据表

| 卡 | 状态 | 提交证据 | 交接证据 | 判定理由 |
|---|---|---|---|---|
| W0 | done | 既有内核提交线 | W0-kernel.md | 现有看板与交接一致 |
| A0 | blocked | 无 | 无 | 缺交接/缺证据 |
| A1 | done | e793c22、a1fb271 | A1.md | 提交和交接均存在 |
| A2-A5 | blocked | 无独立卡证据 | 无对应交接 | 缺交接/缺证据 |
| A6 | blocked | de13105 | 无 A6.md | 缺交接/缺证据；需补 PDF 输入验收 |
| A7 | blocked | 4a4ed5a | 无 A7.md | 缺交接/缺证据；需补 Codex 接入验收 |
| A8 | done | 6c7e984 | A8.md | 提交和交接均存在 |
| A9 | done | 6811524 | A9.md | 提交和交接均存在 |
| A10 | blocked | 无 | 无 | 缺交接/缺证据；依赖真实 run |
| A11 | done | 089edb5 | A11.md | 提交和交接均存在 |
| A12 | blocked | ead3cd5 | A12-design.md | 只有设计冻结，缺实现/真实 run 证据 |
| A13 | blocked | 无 | 无 | 缺交接/缺证据 |
| A14 | done | 636022f | A14.md | 提交和交接均存在 |
| A14b | blocked | c5f79ed | A14b.md | 入口复用仍有 xfail，解除条件已写于交接 |
| A15 | blocked | 8969148、0d483e7 | A15.md | 正向打包依赖真实 run/门禁条件 |
| A15-2 | done | 69f693f | A15-2.md | 提交和交接均存在 |
| A16 | blocked | 无 | 无 | 缺交接/缺证据 |
| A17 | done | 994f958 | A17.md | 提交和交接均存在 |
| A18 | done | 9d95bf3、a104e6f | A18.md | 提交和交接均存在 |
| A19 | done | b1c80a1 | A19.md | 提交和交接均存在 |
| A20 | done | 1bbfce0 | A20.md | 提交和交接均存在 |
| A21 | blocked | eafb5cb | 无 A21.md | 缺交接/缺证据 |
| A22 | blocked | 0b6e4f1 | 无 A22.md | 缺交接/缺证据 |
| M1/M1-fix | blocked | 3dec9cf、3b325d1 | 无独立 handoff | 缺交接/缺证据 |
| M2 | blocked | b24a06c | 无独立 handoff | 缺交接/缺证据 |
| M3 | blocked | 307baf3 | 无独立 handoff | 缺交接/缺证据 |
| M4 | blocked | ea2cd8b | 无独立 handoff | 缺交接/缺证据 |
| M5 | blocked | f72e6ba | 无独立 handoff | 缺交接/缺证据 |

## 下一张建议执行的卡

建议下一张执行 **A6 证据补齐/交接复核**。理由：A6 是最早的 blocked 输入卡，A7、A10、A12、A13 的后续证据链依赖它；当前缺少 `handoffs/A6.md` 和对应确定性验收记录，不能直接把 A6 标为 done。
