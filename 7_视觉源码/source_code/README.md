# PDF Vision Source Code

这是 PDF 视觉识别流水线的源码骨架。流水线将 PDF 页面输入视觉模型，规范化为
Document IR，保留页码和区域证据，并输出校验结果与运行清单。

## 目录

- `pipeline/`: 输入、视觉适配、规范化、证据、写入、校验和修复逻辑
- `schemas/`: 识别结果、Document IR、证据和校验结果的 JSON Schema
- `skills/pdf-vision/SKILL.md`: 视觉 PDF 处理约束
- `result/runs/`: 每次运行的可审计产物

## 使用

```powershell
cd source_code
python -m pip install -r requirements.txt
python main.py path/to/input.pdf
```

运行前复制 `.env.example` 为 `.env`，并配置视觉服务所需的密钥。当前默认适配器
保留了 provider 接口，具体模型调用应在 `pipeline/codex_adapter.py` 中接入。
