# -*- coding: utf-8 -*-
"""科普文档生成引擎。

两种引擎
--------
- ``llm``      : 调用 OpenAI 兼容接口（DeepSeek / 通义 / 本地 Ollama 均可）。
                 长材料自动走 map-reduce：先分片提炼要点，再合写成文。
- ``extractive``: 无网络、无密钥时的兜底。纯抽取式成文，
                 结构合规、内容忠实，但文笔有限 —— 保证"有东西可交"。

密钥永远从环境变量读取，不写入任何文件。
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from . import prompts
from .extractors import fingerprint, split_chunks

# 单次调用的输入上限（字符）。超过则启用 map-reduce。
# 20000 字以内直接单次生成（短篇论文一次调用连贯性更好）；超过则分片。
SINGLE_CALL_LIMIT = 20000
# 长材料分片大小。本赛道材料多为英文综述（PDF 抽取后可达 15 万字符），
# 取 10000 字/片可在「提炼完整度」与「调用次数」之间取得平衡。
CHUNK_SIZE = 10000
# 正文软上限：超过则触发一次压缩调用，确保满足「正文 ≤ 2500 字」的硬性规定。
BODY_LIMIT = 2500
MAX_RETRY = 3


class LLMError(RuntimeError):
    pass


class LLMClient:
    """最小可用的 OpenAI 兼容 Chat Completions 客户端。"""

    def __init__(self, cfg: dict):
        self.base_url = (cfg.get("base_url") or "").rstrip("/")
        self.model = cfg.get("model") or ""
        self.api_key_env = cfg.get("api_key_env") or "LLM_API_KEY"
        self.api_key_header = str(cfg.get("api_key_header") or "Authorization").strip()
        prefix = cfg.get("api_key_prefix")
        if prefix is None:
            prefix = "Bearer " if self.api_key_header.lower() == "authorization" else ""
        self.api_key_prefix = str(prefix)
        self.temperature = float(cfg.get("temperature", 0.3))
        self.max_tokens = int(cfg.get("max_tokens", 4096))
        self.timeout = int(cfg.get("timeout", 180))

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.model)

    def _api_key(self) -> str:
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise LLMError(
                f"环境变量 {self.api_key_env} 未设置。"
                f"请先设置密钥，或改用 extractive 引擎。"
            )
        return key

    def chat(self, system: str, user: str) -> str:
        try:
            import requests
        except ImportError as exc:
            raise LLMError("调用大模型需要 requests：pip install requests") from exc

        url = f"{self.base_url}/chat/completions"
        headers = {
            self.api_key_header: f"{self.api_key_prefix}{self._api_key()}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        last_error = ""
        for attempt in range(1, MAX_RETRY + 1):
            try:
                resp = requests.post(
                    url, headers=headers, json=payload, timeout=self.timeout
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"].strip()

                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                # 鉴权失败、模型名不存在、参数错 —— 重试多少次结果都一样，
                # 只会白白拖长等待，还让人误以为是"网络不稳"。立即失败，交由上层回退。
                if resp.status_code in {400, 401, 403, 404}:
                    raise LLMError(last_error)
            except LLMError:
                raise
            except Exception as exc:  # 网络抖动、超时、JSON 解析失败
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < MAX_RETRY:
                time.sleep(2 * attempt)  # 指数退避，避免连环失败
        raise LLMError(f"调用失败（已重试 {MAX_RETRY} 次）：{last_error}")


# --------------------------------------------------------------------------
# 文本处理辅助
# --------------------------------------------------------------------------
def _strip_code_fence(text: str) -> str:
    """模型偶尔会把整篇内容包进 ``` 里，去掉以免污染 Markdown 结构。"""
    text = text.strip()
    fence = re.match(r"^```[a-zA-Z]*\s*\n(.*)\n```$", text, re.S)
    return fence.group(1).strip() if fence else text


def _ensure_structure(body: str, title: str) -> str:
    """确保四个必备部分存在：标题、引言、正文、总结。

    模型偶尔会漏掉某个二级标题，这里做一次兜底补齐，
    以满足"含标题、引言、正文、总结"的硬性要求。
    """
    body = body.strip()
    if not body.startswith("# "):
        body = f"# {title}\n\n{body}"

    if "## 引言" not in body:
        head, _, rest = body.partition("\n")
        gap = "\n\n## 引言\n\n（原文未单列引言，以下直接进入正文。）\n\n"
        body = f"{head}{gap}{rest}"

    if "## 正文" not in body:
        anchor = body.find("## 总结")
        if anchor > 0:
            body = f"{body[:anchor].rstrip()}\n\n## 正文\n\n{body[anchor:]}"
        else:
            body = f"{body.rstrip()}\n\n## 正文"

    if "## 总结" not in body:
        body = (
            f"{body.rstrip()}\n\n## 总结\n\n"
            "（原文未对整体结论作集中表述，此处不作推断。）"
        )
    return body


def _split_title(body: str, fallback: str) -> tuple[str, str]:
    """把一级标题从正文中拆出来，便于统一加溯源块。"""
    lines = body.split("\n")
    if lines and lines[0].startswith("# "):
        return lines[0][2:].strip() or fallback, "\n".join(lines[1:]).lstrip("\n")
    return fallback, body


# --------------------------------------------------------------------------
# LLM 引擎
# --------------------------------------------------------------------------
def generate_with_llm(
    client: LLMClient,
    content: str,
    material_id: str,
    source_name: str,
    target_words: int,
) -> str:
    if len(content) <= SINGLE_CALL_LIMIT:
        body = client.chat(
            prompts.SYSTEM_PROMPT,
            prompts.DOC_PROMPT.format(
                material_id=material_id,
                source_name=source_name,
                target_words=target_words,
                content=content,
            ),
        )
    else:
        # 长材料：先分片提炼要点，再合写，避免超出上下文窗口导致信息丢失
        chunks = split_chunks(content, CHUNK_SIZE)
        points: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            part = client.chat(
                prompts.SYSTEM_PROMPT,
                prompts.MAP_PROMPT.format(
                    index=index, total=len(chunks), max_points=18, chunk=chunk
                ),
            )
            points.append(f"### 片段 {index}/{len(chunks)}\n{part}")
            print(f"      分片 {index}/{len(chunks)} 提炼完成")

        body = client.chat(
            prompts.SYSTEM_PROMPT,
            prompts.REDUCE_PROMPT.format(
                material_id=material_id,
                source_name=source_name,
                target_words=target_words,
                points="\n\n".join(points),
            ),
        )

    return _maybe_compress(client, body)


def _maybe_compress(client: LLMClient, body: str) -> str:
    """正文超过 2500 字时，再调用一次大模型压缩到合规长度。

    提示词里已给出「不超过 2400 字」的约束，但长篇综述素材信息量太大，
    模型偶尔仍会超限。这里做确定性兜底，守住「正文 ≤ 2500 字」这条红线。
    """
    if len(body) <= BODY_LIMIT:
        return body
    print(f"      正文 {len(body)} 字超限，自动压缩到 2200 字以内…")
    return client.chat(
        prompts.SYSTEM_PROMPT,
        prompts.COMPRESS_PROMPT.format(length=len(body), body=body),
    )


# --------------------------------------------------------------------------
# 抽取式兜底引擎（离线可用）
# --------------------------------------------------------------------------
# 原文中的小标题形态：「1 研究背景」「2.3 分类方法」「三、结论」
HEADING_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*|[一二三四五六七八九十]+[、.．])\s*\S{0,20}$")


def _is_cjk(char: str) -> bool:
    return "\u4e00" <= char <= "\u9fff"


def _join_lines(lines: list[str]) -> str:
    """拼接句中断行。中文直接相连，西文之间补一个空格，避免把单词粘在一起。"""
    if not lines:
        return ""
    result = lines[0]
    for line in lines[1:]:
        if _is_cjk(result[-1]) or _is_cjk(line[0]):
            result += line
        else:
            result += " " + line
    return result


def _flatten(paragraph: str) -> str:
    """把段内硬换行拼回整句，并剥掉段首的小标题行。

    中文材料常见「1 研究背景\\n城市化进程加快……」这种排版，
    若直接拼接会得到「1 研究背景城市化进程加快」，既难看也影响检索。
    """
    lines = [ln.strip() for ln in paragraph.split("\n") if ln.strip()]
    while len(lines) > 1 and HEADING_RE.match(lines[0]):
        lines.pop(0)
    return _join_lines(lines).strip()


def _guess_title(content: str, fallback: str) -> str:
    """材料首行若是一句短标题，就直接用它当文档标题。

    PDF 首页常有英文页眉（如 "RACING TRAINING"），中文材料却被它占了标题，
    因此纯英文行在中文正文里一律跳过。
    """
    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", content))
    mostly_cjk = cjk_chars > len(content) * 0.15
    for line in content.split("\n")[:5]:
        candidate = line.strip().lstrip("#").strip()
        if not 8 <= len(candidate) <= 40:
            continue
        if re.search(r"[。！？：:，,]", candidate):
            continue
        if mostly_cjk and not re.search(r"[\u4e00-\u9fff]", candidate):
            continue
        return candidate
    return fallback


# 对外暴露的别名：文件名（如 "M01_材料"）不适合当文章标题，
# 优先用原文首行，取不到才退回文件名。
guess_title = _guess_title


def _to_paragraphs(text: str, target: int = 300) -> list[str]:
    """还原段落结构。

    PDF 与网页抽取后常常只剩单个换行，段落间隔丢失。若不处理，
    整篇材料会变成一个"超大段落"，正文只能全塞进一节，结构就废了。
    这里对含换行的块按行重组，攒到 target 字左右、且正好停在句末时断段。
    小标题（如「2 数据与方法」）单独成段，随后由 ``_flatten`` 剥除；
    否则它会和正文粘成「2 数据与方法研究采用……」，句子读不通。
    """
    paragraphs: list[str] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if len(lines) <= 1:
            paragraphs.append(block)
            continue

        buffer = ""
        for line in lines:
            if HEADING_RE.match(line):
                if buffer:
                    paragraphs.append(buffer)
                    buffer = ""
                paragraphs.append(line)  # 单独成段，交给 _flatten 剥掉
                continue
            buffer += line
            if len(buffer) >= target and re.search(r"[。！？；]$", line):
                paragraphs.append(buffer)
                buffer = ""
        if buffer:
            paragraphs.append(buffer)
    return paragraphs


def _split_head(text: str, budget: int) -> tuple[str, str]:
    """按句末切出前段，返回 (前段, 后段)，两段都在文档顺序上。"""
    sentences = [s for s in re.split(r"(?<=[。！？；])", text) if s]
    if len(sentences) <= 1:
        return text, ""
    taken = ""
    index = 0
    while index < len(sentences) and len(taken) < budget:
        taken += sentences[index]
        index += 1
    return taken, "".join(sentences[index:])


def _split_tail(text: str, budget: int) -> tuple[str, str]:
    """按句末切出后段，返回 (前段, 后段)，两段都在文档顺序上。"""
    sentences = [s for s in re.split(r"(?<=[。！？；])", text) if s]
    if len(sentences) <= 1:
        return "", text
    keep: list[str] = []
    for sentence in reversed(sentences):
        keep.insert(0, sentence)
        if sum(len(s) for s in keep) >= budget:
            break
    tail = "".join(keep)
    return text[: len(text) - len(tail)], tail


def generate_extractive(content: str, material_id: str, source_name: str) -> str:
    """不改写、不概括语义，靠挑选原文关键句成文。

    目标是"结构合规 + 内容忠实"：引言、正文、总结分别取自原文不同部位，
    互不重复；文采让位于准确性。离线时的兜底方案。
    """
    paragraphs = [_flatten(p) for p in _to_paragraphs(content)]
    paragraphs = [p for p in paragraphs if len(p) >= 12]
    if not paragraphs:
        paragraphs = [content.strip()]

    title = _guess_title(content, source_name)

    # 段落太少时（材料短、或排版把整篇挤成一段）退到句子级切分，
    # 否则引言和正文会取到同一段文字，读起来像复制粘贴。
    units = paragraphs
    if len(units) < 3:
        sentences = [
            s for s in re.split(r"(?<=[。！？；])", "".join(units)) if len(s.strip()) >= 8
        ]
        if len(sentences) >= 3:
            step = max(1, len(sentences) // 3)
            units = ["".join(sentences[i : i + step]) for i in range(0, len(sentences), step)]

    intro = units[0]
    closing = units[-1] if len(units) >= 2 else ""
    if len(units) >= 3:
        body_units = units[1:-1]
    elif len(units) == 2:
        body_units, closing = units[1:2], ""
    else:
        body_units = []

    # 引言过长时只留前几句，剩下的并回正文；结论同理保留末尾几句。
    # 关键是"不整段丢弃"——截断掉的字若直接没了，就是内容不完整。
    if len(intro) > 320:
        head, leftover = _split_head(intro, 200)
        intro = head
        if leftover:
            body_units = [leftover] + body_units
    if len(closing) > 320:
        leftover, tail = _split_tail(closing, 200)
        closing = tail
        if leftover:
            body_units = body_units + [leftover]

    third = max(1, len(body_units) // 3) if body_units else 0
    groups = [
        ("原文提出的问题", body_units[:third]),
        ("使用的方法与数据", body_units[third : third * 2]),
        ("得到的结果与讨论", body_units[third * 2 :]),
    ]

    body_parts: list[str] = []
    for heading, group in groups:
        if not group:
            continue
        body_parts.append(f"### {heading}\n\n" + "\n\n".join(group))
    if not body_parts:
        body_parts.append(
            "### 说明\n\n原文篇幅较短，未包含可以单独成节的细节内容，"
            "本节不作拆分，以免重复引言与总结的表述。"
        )

    return (
        f"# {title}\n\n"
        f"## 引言\n\n{intro}\n\n"
        "本文按原文顺序梳理这项工作的背景、方法与结果，"
        "力求让没有专业背景的读者也能理解它在解决什么问题。\n\n"
        f"## 正文\n\n" + "\n\n".join(body_parts) + "\n\n"
        "## 总结\n\n"
        + (f"{closing}\n\n" if closing else "")
        + "以上内容按原文表述摘录整理，未对研究结论、数据含义与适用范围作改写；"
        "原文未展开的部分，本文不作补充推断。\n"
    )


# --------------------------------------------------------------------------
# 组装最终 Markdown
# --------------------------------------------------------------------------
def assemble(body: str, material_id: str, source_name: str, source_text: str,
             engine: str, fallback_title: str) -> str:
    title, rest = _split_title(_strip_code_fence(body), fallback_title)
    document = _ensure_structure(rest, title)
    if not document.startswith("# "):
        document = f"# {title}\n\n{document}"

    provenance = prompts.PROVENANCE_TEMPLATE.format(
        material_id=material_id,
        source_name=source_name,
        fingerprint=fingerprint(source_text),
        engine=engine,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    return f"{document.rstrip()}\n\n{provenance}"


def write_document(docs_dir: Path, index: int, material_id: str, text: str) -> Path:
    """文档名直接采用材料编号（如 AI-01.md），与赛项要求的命名一致。

    赛项规定"文件名须与目录结构一致"，并给出了 AI-01.md / BIO-02.md 的示例，
    因此不再加序号前缀与"科普文档"后缀；领域代码（AI/BIO/IND/MED）本身
    就保证了字母序即材料顺序。
    """
    safe = re.sub(r'[\\/:*?"<>|]', "_", material_id)
    path = docs_dir / f"{safe}.md"
    path.write_text(text, encoding="utf-8")
    return path


def write_manifest(docs_dir: Path, records: list[dict]) -> Path:
    """生成清单，方便自查"材料与文档是否一一对应"。"""
    path = docs_dir / "_manifest.json"
    path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
