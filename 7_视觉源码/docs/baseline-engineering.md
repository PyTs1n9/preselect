# 工程基线记录

日期：2026-11-13（Asia/Shanghai）
范围：`source_code/` 当前源码、schema、依赖清单、Skill 与 README。以下记录为本轮命令和文件内容的观测结果。

## 环境

执行命令：

```powershell
python -V
python -m pip -V
$PSVersionTable.PSVersion.ToString()
[System.Runtime.InteropServices.RuntimeInformation]::OSDescription
Get-Location
```

实际输出：

```text
Python 3.13.9
pip 25.1 from D:\Anaconda3\Lib\site-packages\pip (python 3.13)
7.6.9
Microsoft Windows 10.0.22631
D:\桌面\金砖大赛\preselect\7_视觉源码\source_code
```

## 依赖

依赖清单 `requirements.txt` 与 `requirements-lock.txt` 均列出 `jsonschema==4.23.0`、`pypdf==5.1.0`、`python-dotenv==1.0.1`。为避免改动当前 Python 环境，本轮以 target 目录尝试安装：

```powershell
python -m pip install --target .a0-deps -r requirements.txt
```

实际输出（退出码 1；另有四条临时目录清理警告，其本机临时路径未收入本记录）：

```text
ERROR: Could not find a version that satisfies the requirement jsonschema==4.23.0 (from versions: none)
ERROR: No matching distribution found for jsonschema==4.23.0
```

检查当前环境：

```powershell
python -m pip show jsonschema pypdf python-dotenv
```

实际版本结果：`jsonschema` 为 `4.23.0`，`python-dotenv` 为 `1.1.0`，未安装 `pypdf`。

导入验收命令：

```powershell
python -c "import jsonschema,pypdf,dotenv;print('ok')"
```

实际输出（退出码 1）：

```text
ModuleNotFoundError: No module named 'pypdf'
```

## CLI 与单页 PDF 命令

帮助命令：

```powershell
python main.py --help
```

实际输出（退出码 0）：

```text
usage: main.py [-h] [--config CONFIG] [--output OUTPUT] pdf

Extract and validate structured data from a PDF.

positional arguments:
  pdf              Path to the input PDF

options:
  -h, --help       show this help message and exit
  --config CONFIG
  --output OUTPUT
```

离线 doctor 命令：

```powershell
python main.py doctor --offline
```

实际输出（退出码 2）：

```text
usage: main.py [-h] [--config CONFIG] [--output OUTPUT] pdf
main.py: error: unrecognized arguments: --offline
```

工作区未找到 PDF 文件。为观测现有入口，临时生成了一个空白单页 PDF 并执行：

```powershell
python main.py .a0-one-page.pdf --output .a0-runs
```

实际输出（退出码 0）：

```json
{
  "run_id": "20261113T105805Z-c2566552",
  "validation": {
    "valid": true,
    "errors": []
  }
}
```

该运行发生时 `pypdf` 未安装；`pipeline/pdf_input.py` 因此返回 0 页空结果。命令结束后，临时 PDF 与 `.a0-runs/` 已删除。当前依赖条件下可用 pypdf 构造同类输入的命令为：

```powershell
python -c "from pypdf import PdfWriter; w=PdfWriter(); w.add_blank_page(width=612,height=792); w.write('tests/one-page.pdf')"
```

## 文件完成度

判定依据为源码行，不以注释中的目标描述作为完成证据。

| 文件 | 状态 | 代码依据 |
|---|---|---|
| `main.py` | 部分实现 | 12–17 行只解析单一 PDF 命令；20–24 行调用一次流水线并固定返回 0。 |
| `pipeline/codex_adapter.py` | stub | 13–14 行 `extract()` 抛出 `NotImplementedError`。 |
| `pipeline/orchestrator.py` | 部分实现 | 14–31 行读取配置、构造 document、校验并写三个 JSON；未调用视觉识别、归一化、证据或写作阶段。 |
| `pipeline/pdf_input.py` | 部分实现 | 9–18 行仅读取页数；依赖缺失时 15 行返回 0 页；没有页面渲染。 |
| `pipeline/validate.py` | 部分实现 | 6–9 行仅检查四个顶层键并输出 `errors: list[str]`。 |
| `pipeline/normalize.py` | 部分实现 | 6–12 行从输入字典取四个顶层字段并填空默认值。 |
| `pipeline/evidence.py` | 部分实现 | 6–9 行把页号与区域写入 claim，没有独立证据列表处理。 |
| `pipeline/repair.py` | 部分实现 | 6–12 行重复调用 validator；没有修改当前值或请求模型。 |
| `pipeline/vision_provider.py` | 部分实现 | 6–15 行定义协议并选择 openai adapter；adapter 的具体调用未实现。 |
| `pipeline/manifest.py` | 部分实现 | 10–15 行只记录 run id、输入路径字符串和 config version。 |
| `pipeline/writer.py` | 部分实现 | 10–12 行实现 JSON 文件写入。 |

当前 Python 源文件中，`codex_adapter.py` 的 `NotImplementedError` 是直接可见的未实现标记；其余表中“部分实现”均按表列出的现有代码行为判定。

## 目录与结构文件

- `source_code/tests/` 只有 `.gitkeep`，没有测试用例文件。
- 工作区没有 PDF 测试输入。
- `source_code/skills/pdf-vision/SKILL.md` 存在，包含 PDF 视觉处理流程与质量规则。
- 当前文件树中没有 `source_code/prompts/` 或 `source_code/fixtures/quality/`。
- 仓库根目录 `.gitignore` 第 9 行的 `**/docs/` 会忽略 `docs/baseline-engineering.md`；该文件已写入磁盘，但不会出现在普通 `git status` 结果中。
- 四个 schema 文件均有 `$id` 与 `$schema`。`recognition.schema.json` 的必需字段为 `pages`；`document_ir.schema.json` 为 `document_id`、`source`、`pages`、`blocks`；`evidence.schema.json` 为 `page_number`；`validation.schema.json` 为 `valid`、`errors`。四者均设置 `additionalProperties: true`。`validation.schema.json` 目前仍使用字符串数组 `errors`。

## 环境偏差补充记录（2026-11-13 23:05，A1 施工前追加）

A0 记录的 `python` 为 `D:\Anaconda3\python.exe`（3.13.9）。A1 施工前复核发现 PATH 首位 `python` 已指向另一解释器，且各解释器的 `openai_codex` 可用性不一致。为消除解释器歧义，自 A1 起本项目统一使用仓库根 `venv/`。以下为观测事实，不改动上文 A0 段落。

### 解释器对照

| 解释器 | Python | `import openai_codex` |
|---|---|---|
| `python`（PATH 首位，`C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe`） | 3.13.14 | 成功，0.169.0 |
| `py` 启动器 | 未注册 3.13，回落 Microsoft Store 占位 | 失败，ModuleNotFoundError |
| `D:\Anaconda3\python.exe` | 3.13.9 | 失败，ModuleNotFoundError |

基础解释器目录名为 `3.13.12`，而 `sys.version` 为 `3.13.14`；目录名属历史标签，不影响 wheel 选择（win_amd64、cp313）。

### venv 建立与安装

命令：

```bash
BASE="C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe"
"$BASE" -m venv venv
venv/Scripts/python.exe -m pip install -U pip
venv/Scripts/python.exe -m pip install -r source_code/requirements.txt
```

实际输出（创建退出码 0；pip 由 26.1.2 升至 26.3；安装退出码 0）：

```text
Successfully installed annotated-types-0.8.0 attrs-26.1.0 colorama-0.4.6 iniconfig-2.3.1 jsonschema-4.23.0 jsonschema-specifications-2025.9.1 openai-codex-0.169.0 openai-codex-cli-bin-0.169.0 packaging-26.3 pillow-12.3.0 pluggy-1.7.0 pydantic-2.14.3 pydantic-core-2.50.4 pypdf-5.1.0 pypdfium2-4.30.0 pytest-8.3.3 python-dotenv-1.0.1 referencing-0.37.0 rpds-py-2026.9.1 typing-extensions-4.16.0 typing-inspection-4.0.5
```

### pin 核验

以 `importlib.metadata.version` 对 `requirements.txt` 七条 pin 逐项比对：

```text
OK        jsonschema     pin=4.23.0   installed=4.23.0
OK        pypdf          pin=5.1.0    installed=5.1.0
OK        python-dotenv  pin=1.0.1    installed=1.0.1
OK        pytest         pin=8.3.3    installed=8.3.3
OK        openai-codex   pin=0.169.0  installed=0.169.0
OK        pypdfium2      pin=4.30.0   installed=4.30.0
OK        pillow         pin=12.3.0   installed=12.3.0
pins_all_match= True
```

### SDK 导出核验

```text
openai_codex.__version__= 0.169.0
openai_codex.__file__= <repo>\venv\Lib\site-packages\openai_codex\__init__.py
export_missing= []
```

`Codex / AsyncCodex / CodexConfig / Sandbox / ApprovalMode / Thread / TurnHandle / TurnResult / TextInput / ImageInput / LocalImageInput / SkillInput` 十二项全部导出。`openai-codex-cli-bin 0.169.0`（py3-none-win_amd64，约 200 MB）由 `openai-codex` 作为传递依赖自动引入，承载 SDK 的 pinned runtime，未在 `requirements.txt` 显式列名，故依赖清单无需改动。

### 回归核验

在 `venv` 内运行 `source_code/` 现有测试，确认该环境可直接承载工程代码：

```bash
cd source_code && ../venv/Scripts/python.exe -m pytest -q
```

实际输出（退出码 0）：

```text
86 passed in 1.25s
```

### 对 A0 依赖结论的修正

A0 记录的 `ERROR: No matching distribution found for jsonschema==4.23.0 (from versions: none)` 与 `ModuleNotFoundError: No module named 'pypdf'` 均产生于 `D:\Anaconda3` 解释器。同一份 `requirements.txt` 在 `venv` 内一次安装成功（退出码 0，七条 pin 全匹配），因此该两条现象不构成"依赖不可安装"的证据。此修正只改变归因，不改变 A0 的代码完成度判定。

### 未变更项

- `requirements.txt`、`requirements-lock.txt` 未改动。
- `venv/.gitignore`（内容 `*`，由 `python -m venv` 生成）使 `venv/` 不进入 `git status`，无需修改仓库忽略规则。
- git 工作区根为 `D:\桌面\金砖大赛\preselect`；根 `.gitignore` 的 `**/docs/` 使本文件不被跟踪，故本节改动不产生 git 提交。
