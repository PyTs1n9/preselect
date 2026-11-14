"""Deterministic text cleanup and page routing; no provider or network access."""

from __future__ import annotations

import re

from .errors import Code, PipelineError


MIN_TEXT_CHARS = 200
TABLE_NUMERIC_RATIO = 0.30
FIGURE_AREA_RATIO = 0.15

_LIGATURES = str.maketrans({"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"})
_SPACED_CHARACTERS = re.compile(r"(?<!\S)(?:\S[^\S\n]+){2,}\S(?!\S)")
_NUMERIC_TOKEN = re.compile(r"-?\d+(?:[.,]\d+)?%?")


def clean_text(raw: str) -> str:
    """Clean within lines, keeping non-hyphenated line boundaries for blocks."""

    text = raw.replace("\r\n", "\n").replace("\r", "\n").translate(_LIGATURES)
    text = re.sub(r"(?<=\w)-[^\S\n]*\n[^\S\n]*(?=\w)", "", text)
    text = _SPACED_CHARACTERS.sub(lambda match: re.sub(r"\s+", "", match.group()), text)
    return "\n".join(re.sub(r"[^\S\n]+", " ", line).strip()
                     for line in text.split("\n")).strip()


def classify_page(text_chars: int, numeric_ratio: float, formula_marker: bool,
                  image_area_ratio: float) -> tuple[str, list[str]]:
    reasons = [f"text_chars={text_chars}", f"numeric_ratio={numeric_ratio:.3f}",
               f"formula_marker={str(formula_marker).lower()}",
               f"image_area_ratio={image_area_ratio:.3f}"]
    if text_chars < MIN_TEXT_CHARS:
        kind = "scan_page"
    elif numeric_ratio > TABLE_NUMERIC_RATIO:
        kind = "table_page"
    elif formula_marker:
        kind = "formula_page"
    elif image_area_ratio > FIGURE_AREA_RATIO:
        kind = "text_page_with_figure"
    else:
        kind = "text_page"
    return kind, reasons


def build_blocks_from_text(page_text: str) -> list[dict]:
    lines = [line for line in clean_text(page_text).splitlines() if line]
    return [{"block_id": f"B{index:03d}", "text": line, "region": None}
            for index, line in enumerate(lines, start=1)]


def page_numeric_tokens(text: str) -> list[str]:
    return sorted(token.replace(",", "") for token in _NUMERIC_TOKEN.findall(text))


def check_table_shape(payload: dict) -> None:
    """Enforce the rectangular invariant that JSON Schema cannot express."""

    if not isinstance(payload, dict) or not isinstance(payload.get("tables"), list):
        raise PipelineError(Code.SCHEMA_INVALID, "Table output requires a tables array")
    for table in payload["tables"]:
        if not isinstance(table, dict):
            raise PipelineError(Code.SCHEMA_INVALID, "Table must be an object")
        header = table.get("header")
        rows = table.get("rows")
        if not isinstance(header, list) or not header:
            raise PipelineError(Code.SCHEMA_INVALID, "Table header must be nonempty")
        if (not isinstance(rows, list)
                or any(not isinstance(row, list) or len(row) != len(header) for row in rows)):
            raise PipelineError(Code.SCHEMA_INVALID, "Table row width must match its header")
