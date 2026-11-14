"""Deterministic document structure, safety and evidence consistency checks."""

from __future__ import annotations

import html
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import schema_loader
from .contracts import ValidationReport
from .errors import Code, IoFailure, Issue, PipelineError
from .settings import Settings

# Single source of truth for the Markdown structure. B owns the wording, but
# the checker must not guess: a numbered heading such as "1. 引言" is reported
# as missing, and the hint tells B the exact frozen text instead of relaxing
# the check silently.
SOURCE_SECTION = "来源"
REQUIRED_SECTIONS = ("引言", "正文", "总结", SOURCE_SECTION)
SECTION_HINT = (
    "标题必须是一级标题；章节标题必须逐字为「引言」「正文」「总结」「来源」，"
    "不得添加编号、前缀或其他变体"
)

_MATERIAL_ID = re.compile(r"[A-Za-z0-9._-]+")
_MATERIAL_COMMENT = re.compile(r"<!--[ \t]*material_id:[ \t]*([A-Za-z0-9._-]+)[ \t]*-->")
_CITATION = re.compile(r"\[(F[0-9]{3,})\]")
_NUMBER = re.compile(
    r"(?<![0-9])(?:(?<![0-9\u3400-\u9fff])-)?"
    r"(?:[0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?"
    r"(?![0-9_]|\.[0-9])"
)
_BODY_SECTIONS = frozenset(REQUIRED_SECTIONS[:-1])
_CREDENTIAL_SHAPED = re.compile(
    r"""(?ix)(?<![a-z0-9_])sk-[a-z0-9_-]*
    | (?<![a-z0-9_])["']?(?:[a-z0-9]+_)*(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|password|secret)["']?\s*[:=]\s*["']?[A-Za-z0-9][^\s"',;}\]]*
    | (?<![a-z0-9_])bearer\s+[^\s"']+"""
)


def _issue(code: Code, path: str, message: str, hint: str = "") -> Issue:
    # Messages are authored here, never interpolated from artifact contents.
    return Issue(code, "validate", path, "error", message, hint)


def _markdown_lines(markdown: str) -> list[str]:
    """Remove metadata, comments and code blocks without exposing their headings."""
    text = markdown.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<!--(?:[\s\S]*?-->|[\s\S]*$)", "", text)
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() in {"---", "..."}), None)
        if end is not None:
            lines[: end + 1] = [""] * (end + 1)
    result: list[str] = []
    fence_char, fence_size = "", 0
    for line in lines:
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence_char:
            if fence and fence[1][0] == fence_char and len(fence[1]) >= fence_size and not fence[2].strip():
                fence_char, fence_size = "", 0
            result.append("")
        elif fence and (fence[1][0] == "~" or "`" not in fence[2]):
            fence_char, fence_size = fence[1][0], len(fence[1])
            result.append("")
        elif line.startswith(("    ", "\t")):
            result.append("")
        else:
            result.append(line)
    return result


def _inline_text(text: str) -> str:
    """Keep visible inline text; drop destinations, reference definitions and tags."""
    text = re.sub(r"(?m)^ {0,3}\[[^\]\n]+\]:[^\n]*$", "", text)
    # Balanced parentheses cover ordinary Markdown destinations such as a_(b).pdf.
    text = re.sub(r"!?\[([^\]\n]*)\]\((?:[^()\n]|\([^()\n]*\))*\)", r"\1", text)
    text = re.sub(r"!?\[([^\]\n]+)\]\[[^\]\n]*\]", r"\1", text)
    text = re.sub(r'''</?[A-Za-z][A-Za-z0-9-]*(?:\s+(?:[^<>"'\n]|"[^"\n]*"|'[^'\n]*')*)?\s*/?>''', "", text)
    text = re.sub(r"(?:https?://|www\.)[^\s<>]+", "", text)
    text = re.sub(r"(?m)^ {0,3}(?:>[ \t]*)+", "", text)
    text = re.sub(r"(?m)^ {0,3}(?:[-*+]\s+|\d+[.)]\s+)", "", text)
    text = re.sub(r"(?m)^\s*[:|\- ]+\s*$", "", text)
    return html.unescape(re.sub(r"[\\`*_~#]", "", text))


def _headings(lines: list[str]) -> list[tuple[int, int, str, int]]:
    headings: list[tuple[int, int, str, int]] = []
    i = 0
    while i < len(lines):
        atx = re.match(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$", lines[i])
        if atx:
            title = re.sub(r"[ \t]+#+[ \t]*$", "", atx[2] or "")
            headings.append((i, len(atx[1]), _inline_text(title).strip(), 1))
        elif lines[i].strip() and not re.match(r"^\s*(?:>|[-*+]\s|\d+[.)]\s)", lines[i]) and i + 1 < len(lines):
            underline = re.fullmatch(r" {0,3}(=+|-+)[ \t]*", lines[i + 1])
            if underline:
                headings.append((i, 1 if underline[1][0] == "=" else 2, _inline_text(lines[i]).strip(), 2))
                i += 1
        i += 1
    return headings


def _without_sources(lines: list[str]) -> list[str]:
    result = list(lines)
    headings = _headings(lines)
    for index, (start, level, title, _) in enumerate(headings):
        if title == SOURCE_SECTION:
            end = next((pos for pos, depth, _, _ in headings[index + 1 :] if depth <= level), len(lines))
            result[start:end] = [""] * (end - start)
    # Setext underlines carry no visible content.
    for start, _, _, size in headings:
        if size == 2:
            result[start + 1] = ""
    return result


def count_han(markdown: str) -> int:
    """Count visible Han characters in title, introduction, body and conclusion.

    Remove balanced front matter, HTML comments/tags, fenced/indented code,
    Markdown markers, link targets/reference definitions and the 来源 section
    (including children, ending at the next heading of equal or lower depth).
    Keep visible link/image labels and inline-code text. Count Unicode unified
    and compatibility Han ideographs, including extensions and 〇; punctuation,
    Latin text and digits do not count. This is independent of settings and I/O.
    """
    text = _inline_text("\n".join(_without_sources(_markdown_lines(markdown))))
    return sum(
        char == "〇" or unicodedata.name(char, "").startswith(("CJK UNIFIED IDEOGRAPH-", "CJK COMPATIBILITY IDEOGRAPH-"))
        for char in text
    )


def _check_markdown(raw: bytes, settings: Settings, name: str) -> tuple[list[Issue], str, int]:
    issues: list[Issue] = []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [_issue(Code.MD_BAD_ENCODING, name, "Markdown is not valid UTF-8")], "", 0
    if raw.startswith(b"\xef\xbb\xbf"):
        issues.append(_issue(Code.MD_BAD_ENCODING, name, "Markdown contains a UTF-8 BOM"))
    if (b"\r\n" in raw and b"\n" in raw.replace(b"\r\n", b"")) or b"\r" in raw.replace(b"\r\n", b""):
        issues.append(_issue(Code.MD_BAD_ENCODING, name, "Markdown contains mixed or unsupported line endings"))
    lines = _markdown_lines(text)
    headings = _headings(lines)
    if not any(level == 1 and title for _, level, title, _ in headings):
        issues.append(_issue(Code.MD_MISSING_SECTION, name + "#title",
                             "Markdown requires a nonempty level-one title", SECTION_HINT))
    for section in REQUIRED_SECTIONS:
        nonempty = False
        for index, (start, level, title, size) in enumerate(headings):
            if title != section:
                continue
            end = next((pos for pos, depth, _, _ in headings[index + 1 :] if depth <= level), len(lines))
            content = list(lines[start + size : end])
            # A hierarchy of empty subheadings is not section content.
            for pos, _, _, width in headings[index + 1 :]:
                if pos >= end:
                    break
                offset = pos - start - size
                content[offset : offset + width] = [""] * width
            if _inline_text("\n".join(content)).strip():
                nonempty = True
        if not nonempty:
            issues.append(_issue(Code.MD_MISSING_SECTION, name + "#" + section,
                                 "Required Markdown section is missing or empty", SECTION_HINT))
    han_chars = count_han(text)
    if han_chars < settings.pipeline.body_min_han:
        issues.append(_issue(Code.MD_BODY_TOO_SHORT, name + "#body", "Han character count is below the configured minimum"))
    if han_chars > settings.pipeline.body_max_han:
        issues.append(_issue(Code.MD_BODY_TOO_LONG, name + "#body", "Han character count exceeds the configured maximum"))
    body = "\n".join(_without_sources(lines))
    return issues, body, han_chars


def scan_credential_shaped(text: str) -> bool:
    """Return whether text contains the frozen credential-shaped pattern."""
    return _CREDENTIAL_SHAPED.search(text) is not None


def _check_sensitive(text: str, path: str) -> list[Issue]:
    """Scan original text, including metadata, code, comments and link targets."""
    issues: list[Issue] = []
    if scan_credential_shaped(text):
        issues.append(_issue(Code.SECRET_LEAK, path, "Credential-shaped text detected", "Remove credentials from the artifact"))
    local_text = re.sub(r"https?://[^\s<>\"')]+", "", text, flags=re.IGNORECASE)
    if re.search(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]|/(?:Users|home)/", local_text):
        issues.append(_issue(Code.ABS_PATH_LEAK, path, "Local absolute path detected", "Use a portable relative reference"))
    return issues


def _consistency_body(markdown: str) -> str:
    """Select prose in the three body sections, excluding entire source subtrees."""
    lines = _markdown_lines(markdown)
    headings = _headings(lines)
    selected = [False] * len(lines)
    excluded = [False] * len(lines)
    for index, (start, level, title, width) in enumerate(headings):
        end = next((pos for pos, depth, _, _ in headings[index + 1:] if depth <= level), len(lines))
        if title in _BODY_SECTIONS:
            selected[start + width:end] = [True] * (end - start - width)
        if title == SOURCE_SECTION:
            excluded[start:end] = [True] * (end - start)
        excluded[start:start + width] = [True] * width
    text = "\n".join(line if selected[i] and not excluded[i] else "\x00SECTION\x00"
                     for i, line in enumerate(lines))
    # Inline code is visible for the frozen Han count, but is not prose evidence.
    text = re.sub(r"(`+)(.*?)\1", lambda match: "\n" * match.group().count("\n"), text, flags=re.DOTALL)
    text = re.sub(r"\\\[F[0-9]{3,}\]", "", text)
    text = re.sub(r"\[((?!F[0-9]{3,}\])[^\]\n]*)\]\[F[0-9]{3,}\]", r"\1", text)
    text = re.sub(r"\[(F[0-9]{3,})\](\((?:[^()\n]|\([^()\n]*\))*\))", "", text)
    # Remove ordinary link destinations before protecting citation markers.
    text = re.sub(r"!?\[([^\]\n]*)\]\((?:[^()\n]|\([^()\n]*\))*\)", r"\1", text)
    markers: list[str] = []

    def protect(match):
        markers.append(match.group())
        return f"\x00CITE{len(markers) - 1}\x00"

    # Otherwise [F001][F002] would be mistaken for a reference-style link.
    text = _inline_text(_CITATION.sub(protect, text))
    for index, marker in enumerate(markers):
        text = text.replace(f"\x00CITE{index}\x00", marker)
    return text


def _check_material(run_dir: Path, text: str | None, evidence: dict | None, name: str) -> list[Issue]:
    """This is the only consistency check that reads the optional manifest."""
    issues: list[Issue] = []
    manifest_id = None
    manifest_path = run_dir / "manifest.json"
    try:
        raw = manifest_path.read_bytes()
    except FileNotFoundError:
        if manifest_path.is_symlink():
            issues.append(_issue(Code.SCHEMA_INVALID, "manifest.json", "Optional manifest cannot be read"))
    except OSError:
        issues.append(_issue(Code.SCHEMA_INVALID, "manifest.json", "Optional manifest cannot be read"))
    else:
        try:
            manifest = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant,
                                  object_pairs_hook=_unique_object)
            candidate = manifest.get("material_id") if isinstance(manifest, dict) else None
            if not isinstance(candidate, str) or not _MATERIAL_ID.fullmatch(candidate):
                raise ValueError("Invalid material identifier")
            manifest_id = candidate
        except (UnicodeDecodeError, ValueError, RecursionError):
            issues.append(_issue(Code.SCHEMA_INVALID, "manifest.json", "Optional manifest requires strict JSON and a valid material_id"))
    if text is not None:
        first = next((line.strip() for line in text.lstrip("\ufeff").splitlines() if line.strip()), "")
        match = _MATERIAL_COMMENT.fullmatch(first)
        if match is None:
            issues.append(_issue(Code.MATERIAL_MISMATCH, name, "First nonempty line must be a complete material_id comment"))
        elif ((evidence is not None and match[1] != evidence["material_id"])
              or (manifest_id is not None and match[1] != manifest_id)):
            issues.append(_issue(Code.MATERIAL_MISMATCH, name, "Markdown material_id must match evidence and any present manifest"))
    if evidence is not None and manifest_id is not None and evidence["material_id"] != manifest_id:
        issues.append(_issue(Code.MATERIAL_MISMATCH, "manifest.json", "Manifest material_id must match evidence material_id"))
    return issues


def _number_tokens(text: str) -> list[tuple[Decimal | None, int, int]]:
    tokens = []
    for match in _NUMBER.finditer(text):
        raw = match.group()
        start = match.start()
        unsigned = raw[1:] if raw.startswith("-") else raw
        mantissa = re.split(r"[eE]", unsigned, maxsplit=1)[0]
        integer = mantissa.split(".", 1)[0]
        valid_commas = ("," not in integer or
                        re.fullmatch(r"[0-9]{1,3}(?:,[0-9]{3})+", integer) is not None)
        try:
            value = Decimal(raw.replace(",", "")) if valid_commas else None
        except InvalidOperation:
            value = None
        tokens.append((value, start, match.end()))
    return tokens


def _fact_numbers(fact: dict) -> dict[Decimal, set[str | None]]:
    """Map explicit values to their units without scale or interval inference."""
    raw = fact["value"]
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, dict):
        values = [raw["min"], raw["max"]]
    else:
        values = [] if raw is None else [raw]
    values = [Decimal(str(value)) for value in values]
    unit = fact["unit"]
    units = unit if isinstance(unit, list) else [unit]
    if any(item is not None and not item.strip() for item in units):
        raise ValueError("Empty unit cannot be paired")
    value_text = fact["value_text"] or ""
    text_tokens = _number_tokens(value_text)
    unit_spans: list[tuple[int, int]] = []
    for value, _, end in text_tokens:
        if value is None:
            continue
        for candidate_unit in units:
            if candidate_unit is not None:
                stop = _unit_end(value_text, end, candidate_unit)
                if stop is not None:
                    unit_spans.append((end, stop))
                    break
    textual = [value for value, start, _ in text_tokens
               if value is not None and not any(left <= start < right for left, right in unit_spans)]
    result: dict[Decimal, set[str | None]] = {}
    if isinstance(unit, list):
        paired = values or textual
        if len(paired) != len(unit):
            raise ValueError("Value and unit arrays must correspond")
        for value, item in zip(paired, unit):
            result.setdefault(value, set()).add(item)
        if any(value not in result for value in textual):
            raise ValueError("Textual values cannot be paired with unit array")
    else:
        for value in values + textual:
            result.setdefault(value, set()).add(unit)
    return result


def _unit_end(text: str, end: int, unit: str) -> int | None:
    start = end
    while start < len(text) and text[start].isspace():
        start += 1
    if not text.startswith(unit, start):
        return None
    stop = start + len(unit)
    # m must not match mg, m^2, m/s, or a longer identifier.
    if stop < len(text) and re.match(r"[A-Za-z0-9_/%^µμ°²³·]", text[stop]):
        return None
    return stop


def _sentences(text: str) -> list[str]:
    sentences = []
    cursor = 0
    while cursor < len(text):
        boundary = re.search(r"[。！？；\n]", text[cursor:])
        if boundary is None:
            sentences.append(text[cursor:])
            break
        end = cursor + boundary.end()
        if text[end - 1] != "\n":
            probe = end
            while probe < len(text) and text[probe].isspace():
                probe += 1
            while True:
                citation = _CITATION.match(text, probe)
                if citation is None:
                    break
                probe = citation.end()
                while probe < len(text) and text[probe].isspace():
                    probe += 1
            if probe > end and _CITATION.search(text[end:probe]):
                end = probe
        sentences.append(text[cursor:end])
        cursor = end
    return sentences


def _check_consistency(text: str, evidence: dict, name: str) -> tuple[list[Issue], int, int]:
    issues: list[Issue] = []
    facts = evidence["facts"]
    indexed: dict[str, dict] = {}
    for index, fact in enumerate(facts):
        if fact["fact_id"] in indexed:
            return [_issue(Code.SCHEMA_INVALID, f"evidence.json#/facts/{index}",
                           "Evidence fact identifiers must be unique")], 0, 0
        # The two locator arrays are read position by position, so a mismatch is
        # a broken citation even when both arrays are individually valid.
        if len(fact["page_number"]) != len(fact["block_id"]):
            issues.append(_issue(Code.SCHEMA_INVALID, f"evidence.json#/facts/{index}",
                                 "page_number and block_id must have the same length"))
        indexed[fact["fact_id"]] = fact
    numeric: dict[str, dict[Decimal, set[str | None]] | None] = {}
    for index, fact in enumerate(facts):
        try:
            numeric[fact["fact_id"]] = _fact_numbers(fact)
        except ValueError:
            numeric[fact["fact_id"]] = None
            issues.append(_issue(Code.SCHEMA_INVALID, f"evidence.json#/facts/{index}/unit",
                                 "Evidence values and units must have an unambiguous correspondence"))
    body = _consistency_body(text)
    markers = _CITATION.findall(body)
    citations = sum(marker in indexed for marker in markers)
    referenced = set(markers)
    for marker in dict.fromkeys(markers):
        if marker not in indexed:
            issues.append(_issue(Code.CITE_NOT_IN_EVIDENCE, "final.md#cite",
                                 "Body citation must resolve to an evidence fact"))
    unreferenced = 0
    for index, fact in enumerate(facts):
        if fact["core"] and fact["fact_id"] not in referenced:
            unreferenced += 1
            issues.append(_issue(Code.EVIDENCE_UNREFERENCED, f"evidence.json#/facts/{index}",
                                 "Core evidence fact must be cited in the body"))
    for index, sentence in enumerate(_sentences(body)):
        references = _CITATION.findall(sentence)
        if not references:
            continue
        known_references = [marker for marker in references if marker in indexed]
        if not known_references:
            continue
        if any(numeric[marker] is None for marker in known_references):
            continue
        candidates: dict[Decimal, set[str | None]] = {}
        for marker in known_references:
            for value, units in numeric[marker].items():
                candidates.setdefault(value, set()).update(units)
        sentence = _CITATION.sub("", sentence)
        tokens = _number_tokens(sentence)
        unit_spans: list[tuple[int, int]] = []
        matched_units: dict[int, bool] = {}
        for token_index, (value, _, end) in enumerate(tokens):
            units = candidates.get(value, set()) if value is not None else set()
            if not units or None in units:
                continue
            next_token = tokens[token_index + 1] if token_index + 1 < len(tokens) else None
            for unit in sorted(item for item in units if item is not None):
                stop = _unit_end(sentence, end, unit)
                span_start = end
                if stop is None and next_token is not None:
                    _, next_start, next_end = next_token
                    if sentence[end:next_start].strip() in {"-", "–", "—", "~", "～", "至"}:
                        stop = _unit_end(sentence, next_end, unit)
                        span_start = next_end
                if stop is not None:
                    unit_spans.append((span_start, stop))
                    matched_units[token_index] = True
                    break
        for token_index, (value, start, end) in enumerate(tokens):
            if any(left <= start < right for left, right in unit_spans):
                continue
            path = f"{name}#body/sentence/{index}/number/{token_index}"
            if value is None or value not in candidates:
                issues.append(_issue(Code.NUMBER_MISMATCH, path,
                                     "Sentence number must match a value in its cited evidence facts"))
                continue
            units = candidates[value]
            if None in units:
                continue
            matched = matched_units.get(token_index, False)
            for unit in sorted(units):
                if unit is None:
                    matched = True
                    break
                stop = _unit_end(sentence, end, unit)
                if stop is not None:
                    unit_spans.append((end, stop))
                    matched = True
                    break
                if token_index + 1 < len(tokens):
                    _, next_start, next_end = tokens[token_index + 1]
                    if sentence[end:next_start].strip() in {"-", "–", "—", "~", "～", "至"}:
                        stop = _unit_end(sentence, next_end, unit)
                        if stop is not None:
                            unit_spans.append((next_end, stop))
                            matched = True
                            break
            if not matched:
                issues.append(_issue(Code.UNIT_MISSING, path,
                                     "Matched sentence number must carry its corresponding evidence unit"))
    return issues, citations, unreferenced


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _read_artifact(run_dir: Path, name: str) -> tuple[object, list[Issue]]:
    filename = name + ".json"
    try:
        raw = (run_dir / filename).read_bytes()
    except FileNotFoundError:
        return None, [_issue(Code.ASSET_MISSING, filename, "Required JSON artifact is missing")]
    except OSError:
        return None, [_issue(Code.SCHEMA_INVALID, filename, "JSON artifact cannot be read")]
    try:
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant,
                             object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None, [_issue(Code.SCHEMA_INVALID, filename, "Artifact is not strict UTF-8 JSON")]
    try:
        found = schema_loader.validate_instance(name, payload)
    except PipelineError as exc:
        # Do not relay an exception message that could contain untrusted schema data.
        return None, [_issue(Code(exc.code), filename, "Artifact schema cannot be loaded")]
    issues = [Issue(i.code, "validate", filename + "#" + i.path, i.severity, i.message, i.hint) for i in found]
    if name == "evidence" and not any(issue.severity == "error" for issue in issues):
        try:
            payload = json.loads(raw.decode("utf-8"), parse_float=Decimal,
                                 parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, ValueError, RecursionError):
            return None, [_issue(Code.SCHEMA_INVALID, filename, "Artifact is not strict UTF-8 JSON")]
    return payload, issues


def _write_report(run_dir: Path, report: ValidationReport) -> None:
    payload = report.to_dict()
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=run_dir,
                                         prefix=".validation-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
        if schema_loader.validate_instance("validation", payload):
            raise PipelineError(Code.SCHEMA_INVALID, "Generated validation report failed schema self-check")
        os.replace(temporary, run_dir / "validation.json")
        temporary = None
        written = json.loads((run_dir / "validation.json").read_bytes().decode("utf-8"))
        if written != payload or schema_loader.validate_instance("validation", written):
            raise PipelineError(Code.SCHEMA_INVALID, "Written validation report failed schema self-check")
    except OSError:
        raise IoFailure("Validation report could not be written or read back") from None
    except (ValueError, UnicodeDecodeError):
        raise PipelineError(Code.SCHEMA_INVALID, "Validation report serialization failed") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                raise IoFailure("Validation temporary file could not be removed") from None


def _check_block_locators(evidence: dict, document_ir: dict) -> list[Issue]:
    """Every evidence block_id must exist in the IR it claims to point at.

    Without this the citation chain stops at "is a non-empty string": a fact
    could cite B999 and still look traceable.
    """
    blocks = document_ir.get("blocks")
    if not isinstance(blocks, list):
        return [_issue(Code.SCHEMA_INVALID, "document_ir.json#/blocks",
                       "Document IR must expose a blocks array")]
    known = {block.get("block_id") for block in blocks if isinstance(block, dict)}
    issues: list[Issue] = []
    for index, fact in enumerate(evidence["facts"]):
        for block_id in fact["block_id"]:
            if block_id not in known:
                issues.append(_issue(Code.SCHEMA_INVALID,
                                     f"evidence.json#/facts/{index}/block_id",
                                     "Evidence block_id must exist in document_ir.blocks"))
    return issues


def validate_run(run_dir: Path, settings: Settings) -> ValidationReport:
    """Run all implemented checks; missing inputs fail instead of being skipped."""
    issues: list[Issue] = []
    evidence_count = 0
    evidence = None
    document_ir = None
    for name in ("recognition", "document_ir", "evidence"):
        payload, found = _read_artifact(run_dir, name)
        issues.extend(found)
        if isinstance(payload, dict) and not any(i.severity == "error" for i in found):
            if name == "evidence":
                evidence_count = 1
                evidence = payload
            elif name == "document_ir":
                document_ir = payload
    han_chars = 0
    citations = 0
    unreferenced = 0
    markdown = None
    material_text = None
    name = "final.md"
    try:
        try:
            raw = (run_dir / name).read_bytes()
        except FileNotFoundError:
            # A dangling final symlink still exists; it is not permission to use draft.
            if (run_dir / name).is_symlink():
                raise
            name = "draft.md"
            raw = (run_dir / name).read_bytes()
        found, _, han_chars = _check_markdown(raw, settings, name)
        issues.extend(found)
        # Surrogate escaping preserves ASCII leak signatures even in a bad UTF-8 file.
        material_text = raw.decode("utf-8", errors="surrogateescape")
        issues.extend(_check_sensitive(material_text, name))
        try:
            markdown = raw.decode("utf-8")
        except UnicodeDecodeError:
            pass  # _check_markdown already emitted MD_BAD_ENCODING; no invented empty body.
    except FileNotFoundError:
        issues.append(_issue(Code.ASSET_MISSING, name, "Required Markdown artifact is missing"))
    except OSError:
        issues.append(_issue(Code.ASSET_MISSING, name, "Markdown artifact cannot be read"))
    issues.extend(_check_material(run_dir, material_text, evidence, name))
    if evidence is not None and document_ir is not None:
        issues.extend(_check_block_locators(evidence, document_ir))
    if markdown is not None and evidence is not None:
        found, citations, unreferenced = _check_consistency(markdown, evidence, name)
        issues.extend(found)
    enabled = [code.value for code in (
        Code.SCHEMA_INVALID, Code.SCHEMA_NOT_FOUND, Code.SCHEMA_VERSION_MISMATCH,
        Code.ASSET_MISSING, Code.MD_MISSING_SECTION, Code.MD_BODY_TOO_SHORT,
        Code.MD_BODY_TOO_LONG, Code.MD_BAD_ENCODING, Code.SECRET_LEAK, Code.ABS_PATH_LEAK,
        Code.CITE_NOT_IN_EVIDENCE, Code.EVIDENCE_UNREFERENCED, Code.NUMBER_MISMATCH,
        Code.UNIT_MISSING, Code.MATERIAL_MISMATCH,
    )]
    report = ValidationReport(
        valid=not any(i.severity == "error" for i in issues),
        schema_version=schema_loader.load_schema("validation")["schema_version"],
        checked_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        stats={"han_chars": han_chars, "citations": citations, "evidence_count": evidence_count,
               "unreferenced_evidence": unreferenced},
        issues=issues, checks={"enabled": enabled, "skipped": []},
    )
    _write_report(run_dir, report)
    return report
