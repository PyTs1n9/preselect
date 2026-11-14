# 卡级看板

状态只允许：`todo` / `doing` / `done` / `blocked`。
`blocked` 必须在备注写清阻塞原因与解除条件。

| 卡 | Wave | owner | 状态 | 验收退出码 | 依据与备注 |
|---|---|---|---|---|---|
| W0 | W0 | S0 | done | 0 | handoffs/W0-kernel.md；历史内核交付记录 |
| A0 | W0 | S0 | blocked | — | 缺提交/缺交接；需补基线冻结证据 |
| A1 | W1 | S1 | done | 0 | e793c22、a1fb271 + handoffs/A1.md |
| A2 | W1 | S0 | blocked | — | 缺提交/缺交接；需补配置入口证据 |
| A3 | W1 | S0 | blocked | — | 缺独立提交/交接；现有 schema_loader 改动不能代替卡证据 |
| A4 | W1 | S0 | blocked | — | 缺提交/缺交接；CLI/编排骨架证据不足 |
| A5 | W1 | S0 | blocked | — | 缺提交/缺交接；接口冻结证据不足 |
| A6 | W1 | S1 | blocked | — | de13105；缺 handoffs/A6.md，需补 PDF 输入验收与交接 |
| A7 | W1 | S1 | blocked | — | 4a4ed5a；缺 handoffs/A7.md，需补 Codex 接入验收与交接 |
| A8 | W1 | S3 | done | 0 | 6c7e984 + handoffs/A8.md |
| A9 | W1 | S2 | done | 0 | 6811524 + handoffs/A9.md |
| A10 | W2 | S0 | blocked | — | 缺提交/缺交接；依赖 A6/A7 证据与真实 run |
| A11 | W2 | S3 | done | 0 | 089edb5 + handoffs/A11.md |
| A12 | 待定 | S0 | blocked | — | ead3cd5 + handoffs/A12-design.md 仅为设计冻结，缺实现/真实 run 证据 |
| A13 | W3 | S0 | blocked | — | 缺提交/缺交接；四材料串行未有交付证据 |
| A14 | W2 | S0 | done | 0 | 636022f + handoffs/A14.md |
| A14b | W2 | S0 | blocked | 0（pytest；1 xfailed） | c5f79ed + handoffs/A14b.md；输入复用入口仍阻塞 |
| A15 | W2 | S3 | blocked | 7 | 8969148、0d483e7 + handoffs/A15.md；正向打包依赖真实 run/门禁条件 |
| A15-2 | W2 | S0 | done | 0 | 69f693f + handoffs/A15-2.md |
| A16 | W3 | S0 | blocked | — | 缺提交/缺交接；干净环境无独立交付证据 |
| A17 | W2 | S0 | done | 0 | 994f958 + handoffs/A17.md |
| A18 | W2 | S0 | done | 0 | 9d95bf3、a104e6f + handoffs/A18.md |
| A19 | W2 | S0 | done | 0 | b1c80a1 + handoffs/A19.md |
| A20 | W2 | S0 | done | 0 | 1bbfce0 + handoffs/A20.md |
| A21 | W2 | S0 | blocked | — | eafb5cb；缺 handoffs/A21.md，依据不足 |
| A22 | W2 | S0 | blocked | — | 0b6e4f1；缺 handoffs/A22.md，依据不足 |
| M1 | 恢复 | S0 | blocked | — | 3dec9cf；缺独立 handoff，按规则不得推断 done |
| M1-fix | 恢复 | S0 | blocked | — | 3b325d1；缺独立 handoff，按规则不得推断 done |
| M2 | 恢复 | S0 | blocked | — | b24a06c；缺独立 handoff，按规则不得推断 done |
| M3 | 恢复 | S0 | blocked | — | 307baf3；缺独立 handoff，按规则不得推断 done |
| M4 | 合并 | S0 | blocked | — | ea2cd8b；缺独立 handoff，按规则不得推断 done |
| M5 | 合并 | S0 | blocked | — | f72e6ba；缺独立 handoff，按规则不得推断 done |

## 对账结论

下一张建议执行的卡：**A6 证据补齐/交接复核**。A6 是最早的 blocked 输入卡，且 A7、A10、A12、A13 的可执行证据链依赖它；当前先补齐 handoffs/A6.md 与确定性验收记录，再重新判断后续卡。
