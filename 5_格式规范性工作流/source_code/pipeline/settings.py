# -*- coding: utf-8 -*-
"""运行配置解析：``.env`` 读取 + 服务商预设展开。

放这里的原因，是两个在开赛当天特别容易踩、又特别难自查的坑：

1. 代码只从环境变量取密钥，但大多数人习惯把密钥写进 ``.env`` 文件。
   若不显式加载，程序会一直报「环境变量未设置」，而人眼看着 ``.env`` 里明明有值，
   于是开始怀疑代码、怀疑 key、怀疑网络 —— 最不该在这种事上耗时。
2. OpenAI 兼容接口的 ``base_url`` 与 ``model`` 名各家不同，填错就是 404 或
   「model not found」。预设表让「换一家服务商」退化为改一个词。

密钥可保存在本地 ``.env``，该文件不提交；``config.json`` 不得保存真实密钥。
"""
from __future__ import annotations

import os
from pathlib import Path

#: 未指定 preset 时的默认服务商
DEFAULT_PRESET = "deepseek"


def load_dotenv(path: Path) -> list[str]:
    """把 ``.env`` 中的键值读入环境变量。

    已存在的环境变量优先、不被覆盖 —— 这样临时 ``set LLM_API_KEY=...``
    可以压过文件里的旧值，排查问题时不至于改两次。

    返回实际生效的键名，便于向用户提示「读到了哪些配置」，但不返回键值。
    """
    if not path.is_file():
        return []

    loaded: list[str] = []
    # utf-8-sig：Windows 记事本另存为 UTF-8 时会带 BOM，不处理会让第一个键名变成怪字符
    for raw in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # 允许 KEY="value" 这种写法
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]

        if not key or not value:
            # 空值视为未配置，保留「密钥未设置」这个明确报错，
            # 而不是让一个空字符串进入请求头、换来一个看不懂的 401。
            continue
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def resolve_llm(config: dict) -> tuple[dict, str, str]:
    """把 ``llm.preset`` 展开为具体的 ``base_url`` / ``model``。

    返回 ``(展开后的 llm 配置, 生效的服务商名, 提示信息)``。

    显式填写的 ``base_url`` / ``model`` 优先于预设，方便接自建服务或代理。
    """
    llm = dict(config.get("llm") or {})
    presets = config.get("llm_presets") or {}
    name = (llm.get("preset") or "").strip()

    note = ""
    preset: dict = {}
    if name:
        if name in presets:
            preset = presets[name]
        else:
            note = f"预设「{name}」不存在，可选：{'、'.join(presets) or '（无）'}"

    if not llm.get("base_url"):
        llm["base_url"] = preset.get("base_url", "")
    if not llm.get("model"):
        llm["model"] = preset.get("model", "")
    for key in ("api_key_header", "api_key_prefix"):
        if key not in llm and key in preset:
            llm[key] = preset[key]
    return llm, name, note


def describe_key(key: str) -> str:
    """生成可安全打印的密钥描述：只给长度和末 4 位，防止日志/截图泄漏完整密钥。"""
    if not key:
        return "未设置"
    if len(key) <= 8:
        return f"已设置（{len(key)} 位，过短，疑似填错）"
    return f"已设置（{len(key)} 位，末 4 位 …{key[-4:]}）"
