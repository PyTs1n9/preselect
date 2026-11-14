# PDF Vision Source Code

这是已实现的 PDF 视觉识别流水线源码。流水线将 PDF 页面输入视觉模型，规范化为
Document IR，保留页码和区域证据，并输出校验结果与运行清单。

## 目录

- `pipeline/`: 输入、视觉适配、规范化、证据、写入、校验和修复逻辑
- `schemas/`: 识别结果、Document IR、证据和校验结果的 JSON Schema
- `skills/pdf-vision/SKILL.md`: 视觉 PDF 处理约束
- `result/runs/`: 每次运行的可审计产物

## 环境与安装

使用 **Python 3.11+**。代码使用 `X | Y` 联合类型语法和 `Path.is_relative_to()`；
以下命令均在 `source_code/` 目录执行。

```powershell
cd source_code
python -m pip install -r requirements.txt
```

模型调用使用已实现的 Codex SDK 适配器。运行前准备可用的 Codex 凭据；若使用
自定义网关，可复制 `.env.example` 为 `.env` 并设置 `LLM_BASE_URL`、`LLM_API_KEY`。

## run：执行流水线

将 PDF 放在命令指定的路径，然后执行：

```powershell
python main.py run materials/MED-04.pdf --material-id MED-04
```

`pdf` 为必需的位置参数。`--material-id` 指定材料编号，省略时使用 PDF 文件名去掉
扩展名后的部分。可选参数为 `--config`（默认 `config.json`）、`--output`（默认
`result/runs`）和 `--stage`（默认 `all`）。成功时输出包含 `run_id`、`run_dir` 和
阶段结果的 JSON；后续复检使用其中的 `run_id`。

## doctor：离线检查配置

```powershell
python main.py doctor --offline
```

显示解析后的配置、配置来源和未知配置项警告。离线模式不要求提供密钥，也不调用模型。

## validate：复检已有运行

将 `<run_id>` 替换为 `run` 输出的实际 ID：

```text
python main.py validate --run-id <run_id>
```

在配置项 `paths.result_dir` 下查找运行，更新该运行的 `validation.json` 并输出
报告。`valid=false` 时退出码为 4。`--run-id` 也可使用 `MED-04/<run_id>` 的形式。

## package：打包并读回校验

```powershell
python main.py package --materials MED-04 --out dist/submission.zip
```

`--materials` 接受一个或多个材料编号（空格或逗号分隔）；`--out` 为目标 ZIP
路径。打包使用配置项 `paths.result_dir` 下的运行，检查提交门槛并读回验证 ZIP。
如使用 `run --output` 自定义运行根目录，应让 `paths.result_dir` 指向同一目录，
供 `validate` 和 `package` 查找。

## 运行目录布局

默认运行目录为 `result/runs/<material_id>/<run_id>/`：

```text
result/runs/MED-04/<run_id>/
├── input/
│   ├── source.pdf.sha256    # 源 PDF 的 SHA-256，不复制源 PDF
│   └── page_0001.png ...   # 渲染后的逐页 PNG
├── raw/                   # 模型原始响应、修复请求与差异记录
├── logs/                  # 运行时创建的日志目录
├── recognition.json       # 逐页识别结果
├── page_text.json         # 清洗后的逐页文本层
├── page_plan.json         # 页面分类与视觉调用决策依据
├── document_ir.json       # 规范化的 Document IR
├── evidence.json          # 写作所依据的事实证据
├── draft_en.md            # 英文草稿
├── draft.md               # 中文初稿
├── final.md               # 终稿校验通过后保留
├── validation.json        # Python 确定性校验报告
└── manifest.json          # 输入、资产哈希与阶段执行记录
```

产物随阶段执行逐步写入；失败时保留已有中间产物。识别阶段还会按需生成
`tables.json`、`figures.json`、`formulas.json`。

## 退出码

异常映射以 `pipeline/errors.py:exit_code_for` 的实际实现为准；成功返回、参数解析
失败和 `validate` 报告不合格的返回值由 `main.py` 直接决定。

| 退出码 | 含义 | 实际归属 |
|---|---|---|
| 0 | 命令成功 | `main.py` 成功返回，不经过 `exit_code_for` |
| 1 | 未分类异常 | 非 `PipelineError` 异常或未匹配下列分类的 `PipelineError` |
| 2 | 参数、配置或凭据错误 | `ConfigError`、`AuthMissing`；argparse 参数错误直接返回 2 |
| 3 | PDF 输入非法 | 错误码以 `PDF_` 开头 |
| 4 | 结构或内容校验失败 | 错误码以 `SCHEMA_`、`IR_` 开头；`main.py` 的 `validate` 在 `valid=false` 时直接返回 4 |
| 5 | 模型调用失败 | 错误码以 `MODEL_` 开头 |
| 6 | 修复耗尽或失败 | 错误码以 `REPAIR_` 开头 |
| 7 | I/O、打包或读回校验失败 | `IO_FAILURE`，含继承 `IoFailure` 的 `PackageFailure` |
| 8 | 内容资产缺失或无效 | 错误码以 `ASSET_` 开头；凭据缺失 `AuthMissing` 归入 2 |

## 最小配置

| 键 | 默认值 | 生效位置 |
|---|---|---|
| `vision.dpi` | 200 | recognize 显式传给 `load_pdf`，用于逐页 PNG 渲染 |
| `pipeline.max_pdf_mib` | 20 | `load_pdf` 的 PDF 文件大小检查，单位为 MiB |
| `pipeline.max_pages` | 100 | `load_pdf` 的 PDF 页数检查 |

两项上限须为非零正整数，可在 `config.json` 的 `pipeline` 节配置，也可通过
`PIPELINE_MAX_PDF_MIB`、`PIPELINE_MAX_PAGES` 环境变量覆盖。配置来源随
`doctor --offline` 和 manifest 记录。recognize 将同一份已解析配置传入
`load_pdf`，因此 `run --config` 选择的上限会实际生效。

直接调用 `load_pdf` 时必须显式传入 `dpi`；缺失时抛出
`ConfigError("vision.dpi is required")`，配置异常会直接失败。

## 测试

从 `source_code/` 使用项目虚拟环境执行全部测试：

```powershell
../venv/Scripts/python.exe -m pytest -q
```

若已激活安装过 `requirements.txt` 的 Python 3.11+ 环境，也可执行：

```powershell
python -m pytest -q
```
