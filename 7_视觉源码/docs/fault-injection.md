# A14b 故障注入与失败路径证据

运行基线为 636022f。以下表格由测试实际生成的 evidence.json 读取生成，不从预期值推断实际结果。
测试真实执行 main.main、阶段注册与执行、provider、CodexAdapter、渲染、校验器和退出码映射。
模型类用例只替换外部 openai_codex.Codex；落盘用例只对当前目标 recognition.json 的 os.replace 注入 OSError。
使用临时配置和已有工程 stub_prompts，源 fixtures、quality、schemas、prompts 均未修改。

## 实测结果

| 用例 | 期望 code | 实际 code | 退出码 | 证据路径 |
|---|---|---|---|---|
| model_timeout | MODEL_TIMEOUT | MODEL_TIMEOUT | 5 | [evidence.json](D:/桌面/金砖大赛/preselect/7_视觉源码/source_code/result/fault-injection/A14b-636022f/model_timeout/evidence.json) |
| invalid_json | MODEL_INVALID_JSON | MODEL_INVALID_JSON | 5 | [evidence.json](D:/桌面/金砖大赛/preselect/7_视觉源码/source_code/result/fault-injection/A14b-636022f/invalid_json/evidence.json) |
| fake_citation | CITE_NOT_IN_EVIDENCE | CITE_NOT_IN_EVIDENCE | 4 | [evidence.json](D:/桌面/金砖大赛/preselect/7_视觉源码/source_code/result/fault-injection/A14b-636022f/fake_citation/evidence.json) |
| input_change_blocker | 待输入校验契约 | 无业务 code（CLI 参数拒绝） | 2 | [evidence.json](D:/桌面/金砖大赛/preselect/7_视觉源码/source_code/result/fault-injection/A14b-636022f/input_change_blocker/evidence.json) |
| overlong_repair | REPAIR_EXHAUSTED | REPAIR_EXHAUSTED | 6 | [evidence.json](D:/桌面/金砖大赛/preselect/7_视觉源码/source_code/result/fault-injection/A14b-636022f/overlong_repair/evidence.json) |
| artifact_write_failure | IO_FAILURE | IO_FAILURE | 7 | [evidence.json](D:/桌面/金砖大赛/preselect/7_视觉源码/source_code/result/fault-injection/A14b-636022f/artifact_write_failure/evidence.json) |

第 4 行只有真实解析器参数拒绝的 exit 2，没有输入变化业务 code；它是 xfailed，不计为通过。
第 5 行触发错误为 MD_BODY_TOO_LONG，实测 4200 汉字，
2/2 次修复后仍超限，最终 code 如上。
所有五条通过用例均验证具体 code、退出码和失败产物，未 mock 被验证的生产函数。

```text
model_timeout -> code=MODEL_TIMEOUT -> exit=5
invalid_json -> code=MODEL_INVALID_JSON -> exit=5
fake_citation -> code=CITE_NOT_IN_EVIDENCE -> exit=4
input_change_blocker -> code=无业务 code（CLI 参数拒绝） -> exit=2 -> xfailed
overlong_repair -> code=REPAIR_EXHAUSTED -> exit=6
artifact_write_failure -> code=IO_FAILURE -> exit=7
```

## raw 与失败状态证据

- model_timeout: [recognize_1.json](D:/桌面/金砖大赛/preselect/7_视觉源码/source_code/result/fault-injection/A14b-636022f/model_timeout/runs/fault/93e861659e054e7a9a89741031b47911/raw/recognize_1.json)
- invalid_json: [recognize_1.json](D:/桌面/金砖大赛/preselect/7_视觉源码/source_code/result/fault-injection/A14b-636022f/invalid_json/runs/fault/ed877988de8749e2b81a1fa408cd3139/raw/recognize_1.json)
- overlong_repair: [repair_1_request.json](D:/桌面/金砖大赛/preselect/7_视觉源码/source_code/result/fault-injection/A14b-636022f/overlong_repair/runs/run_ok/ae276f04461c410d86630f2ebb892902/raw/repair_1_request.json)
- overlong_repair: [repair_1_diff.md](D:/桌面/金砖大赛/preselect/7_视觉源码/source_code/result/fault-injection/A14b-636022f/overlong_repair/runs/run_ok/ae276f04461c410d86630f2ebb892902/raw/repair_1_diff.md)
- overlong_repair: [repair_2_request.json](D:/桌面/金砖大赛/preselect/7_视觉源码/source_code/result/fault-injection/A14b-636022f/overlong_repair/runs/run_ok/ae276f04461c410d86630f2ebb892902/raw/repair_2_request.json)
- overlong_repair: [repair_2_diff.md](D:/桌面/金砖大赛/preselect/7_视觉源码/source_code/result/fault-injection/A14b-636022f/overlong_repair/runs/run_ok/ae276f04461c410d86630f2ebb892902/raw/repair_2_diff.md)

超时失败 raw 只含 stage、attempt、turn_status、error_type、error_message、final_response、items、
usage、duration_ms、timeout_s 十个字段；final_response=null、items=[]、usage={}。
异常文案先去密钥、base_url、URL 和路径再截 200；同一超时用例包含这些合成敏感内容的反例断言。
坏 JSON 的 raw.final_response 与截断字符串逐字一致，原成功响应审计路径未改。

超时用例第二次运行专门注入审计写失败：实际 code=MODEL_TIMEOUT，
exit=5，原 TimeoutError 添加固定 note，未被 IO_FAILURE 替换。
其 raw 不存在是本次明确注入的结果，不能伪装为已成功留存；
证据见 [该次运行](D:/桌面/金砖大赛/preselect/7_视觉源码/source_code/result/fault-injection/A14b-636022f/model_timeout/audit-failure/fault/eb0fc08cbcd24e79a37f8019d1dd25fe) 与主 evidence.json。
第 6 条没有模型调用，所以 raw 列为空；真实原子写路径返回 IO_FAILURE/7，临时文件已清理，
recognition 及下游产物不存在，manifest 的 recognize 为 failed。

## 第 4 条阻塞

```text
usage: __main__.py [-h] {run,doctor,validate,package} ...
__main__.py: error: unrecognized arguments: --run-id existing-run
SystemExit(2)
XFAIL tests/test_engineering_faults.py::test_input_change_requires_missing_run_reuse_entrypoint - 无 --run-id/断点重跑，A14b blocker
```

标记为 @pytest.mark.xfail(strict=True, raises=SystemExit)，直接调用真实解析器。
没有 assert False、skip 或假造输入哈希错误；未来 run --run-id 支持后会出现严格 XPASS，
需要移除该阻塞探针并改为真正的输入变化验证。

| 后续方案（本卡不实现） | I2/I8 影响 |
|---|---|
| validate --run-id ID --pdf PATH | 显式提供 PDF 后比较哈希，I2 布局不变，I8 原 input 哈希只读；需决定缺失/不符的 code 与退出码 |
| run --run-id ID | 复用前核对材料与输入哈希；需约定 I2 已有产物/raw 留存，I8 重跑状态与历史，也涉及 I1 入口契约 |

## 验收命令与原文

在工作区 source_code/ 下运行固定解释器：

```text
../venv/Scripts/python.exe -m pytest -q tests/test_engineering_faults.py
...x..                                                                   [100%]
5 passed, 1 xfailed in 0.94s
exit=0

../venv/Scripts/python.exe -m pytest -q
.............................................x.......................... [ 22%]
........................................................................ [ 44%]
........................................................................ [ 66%]
........................................................................ [ 88%]
......................................                                   [100%]
325 passed, 1 xfailed in 7.37s
exit=0

../venv/Scripts/python.exe -m compileall -q pipeline
（无输出）
exit=0

git -C .. diff --check
exit=0（无空白错误，只有 Git LF→CRLF 提示）
```

## 证据保存及隔离复验

执行过程中共享工作树另出现 README、pdf_input、schema_loader、recognize 的修改与旧 evidence/
normalize 模块删除，均不属于本卡编辑。为证明 A14b 不依赖这些改动，另外从 636022f 归档导出，
只覆盖本卡 adapter 与新测试文件；将当前已验证哈希的只读内容资产及未跟踪 quality 夹具原样复制
作为同一测试输入，使用同一 venv 隔离复验。首次未复制资产时为 8 failed、313 passed、1 xfailed，
原因包括 quality 不在 Git 归档中及归档提示词与工作区换行不同；没有修改生产代码或断言来迁就。

完整同输入副本最终结果：

```text
D:/桌面/金砖大赛/preselect/7_视觉源码/venv/Scripts/python.exe -m pytest -q --basetemp evidence
325 passed, 1 xfailed in 7.72s
exit=0
```

本报告的实测表取自该次隔离运行。证据保存于 [A14b-636022f](D:/桌面/金砖大赛/preselect/7_视觉源码/source_code/result/fault-injection/A14b-636022f)，
复制前后逐文件 SHA256 比对通过，evidence.json 保留原始临时运行路径，报告链接指向保存副本。
证据目录受既有 result/ 忽略规则管理，不提交运行产物；提交的是测试、产品补丁与记录。
测试可重跑生成新 run_id 和新证据，不承诺 UUID 或耗时相同。

## 已知缺口与未覆盖项

交付前共享工作树继续发生并行变更：HEAD 从 636022f 推进至 a5eac95，另有 adapter.generate
的改动。A14b 仅暂存本卡 _run_turn 补丁，generate 及其他并行改动不纳入本卡提交。
此后共享工作树全量实跑结果为 **1 failed, 350 passed, 1 xfailed in 7.75s**，exit=1；
失败在本卡范围外，不自行改代码或断言：

```text
FAILED tests/test_engineering_package.py::test_cli_invalid_report_maps_to_schema_invalid_exit_four
tests/test_engineering_package.py:150
assert code == 4 and payload["error"]["code"] == "SCHEMA_INVALID"
E assert (7 == 4)
```

前文工作区 325 passed 是当时实际运行结果；隔离基线加 A14b 的 325 passed 仍有效。
不把后续共享工作树失败描述成通过，也不把它归入 A14b 已实现的五条失败路径。

- 删除事实正文但保留 [F001] 仍可能通过；A14 的 core 检查只约束引用存在。修复的新增事实守卫
  只检查新增行数字。本次确认每轮 4200 汉字稿保留了核心句与引用，不宣称已有语义删除守卫。
- write/repair 多次调用仍可能覆盖同名 raw；本卡不修命名，逐轮证据依靠 repair_N_request.json、
  repair_N_diff.md 和 manifest。
- 只在 turn.run() 捕获 Exception；thread_start、thread.turn、BaseException 不在本卡异常审计范围。
  重试、分类、成功 _write_raw 原样保留；审计写失败只 add_note 后重抛原异常。
- 第 4 条仍阻塞，不实现 run_id 复用、输入哈希校验，也不接 A15 或三份专用产物下游。
