"""Deterministic A9 checks. Unimplemented business checks remain visible in reports."""

from __future__ import annotations

import html
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
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


def _check_sensitive(text: str, path: str) -> list[Issue]:
    """Scan original text, including metadata, code, comments and link targets."""
    issues: list[Issue] = []
    secret = re.search(
        r'''(?ix)(?<![a-z0-9_])sk-[a-z0-9_-]*
        | (?<![a-z0-9_])["']?(?:[a-z0-9]+_)*(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|password|secret)["']?\s*[:=]\s*["']?[^\s"',;}\]]+
        | (?<![a-z0-9_])bearer\s+[^\s"']+''', text,
    )
    if secret:
        issues.append(_issue(Code.SECRET_LEAK, path, "Credential-shaped text detected", "Remove credentials from the artifact"))
    # Ordinary web URLs can contain /home/ without naming a local directory.
    local_text = re.sub(r"https?://[^\s<>\"')]+", "", text, flags=re.IGNORECASE)
    if re.search(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]|/(?:Users|home)/", local_text):
        issues.append(_issue(Code.ABS_PATH_LEAK, path, "Local absolute path detected", "Use a portable relative reference"))
    return issues


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
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None, [_issue(Code.SCHEMA_INVALID, filename, "Artifact is not strict UTF-8 JSON")]
    try:
        found = schema_loader.validate_instance(name, payload)
    except PipelineError as exc:
        # Do not relay an exception message that could contain untrusted schema data.
        return None, [_issue(Code(exc.code), filename, "Artifact schema cannot be loaded")]
    issues = [Issue(i.code, "validate", filename + "#" + i.path, i.severity, i.message, i.hint) for i in found]
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


def validate_run(run_dir: Path, settings: Settings) -> ValidationReport:
    """Validate this run locally; valid applies only to checks.enabled.

    The current evidence schema describes one evidence object, so evidence_count
    is one for a schema-valid object, zero otherwise. Citation statistics remain
    zero until B supplies their contract. The five deferred codes are explicit
    in checks.skipped and must not be treated as having passed by consumers.
    """
    issues: list[Issue] = []
    evidence_count = 0
    for name in ("recognition", "document_ir", "evidence"):
        payload, found = _read_artifact(run_dir, name)
        issues.extend(found)
        if name == "evidence" and isinstance(payload, dict) and not any(i.severity == "error" for i in found):
            evidence_count = 1
    han_chars = 0
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
        issues.extend(_check_sensitive(raw.decode("utf-8", errors="surrogateescape"), name))
    except FileNotFoundError:
        issues.append(_issue(Code.ASSET_MISSING, name, "Required Markdown artifact is missing"))
    except OSError:
        issues.append(_issue(Code.ASSET_MISSING, name, "Markdown artifact cannot be read"))
    enabled = [code.value for code in (
        Code.SCHEMA_INVALID, Code.SCHEMA_NOT_FOUND, Code.SCHEMA_VERSION_MISMATCH,
        Code.ASSET_MISSING, Code.MD_MISSING_SECTION, Code.MD_BODY_TOO_SHORT,
        Code.MD_BODY_TOO_LONG, Code.MD_BAD_ENCODING, Code.SECRET_LEAK, Code.ABS_PATH_LEAK,
    )]
    skipped = [code.value for code in (
        Code.CITE_NOT_IN_EVIDENCE, Code.EVIDENCE_UNREFERENCED, Code.NUMBER_MISMATCH,
        Code.UNIT_MISSING, Code.MATERIAL_MISMATCH,
    )]
    report = ValidationReport(
        valid=not any(i.severity == "error" for i in issues),
        schema_version=schema_loader.load_schema("validation")["schema_version"],
        checked_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        stats={"han_chars": han_chars, "citations": 0, "evidence_count": evidence_count, "unreferenced_evidence": 0},
        issues=issues, checks={"enabled": enabled, "skipped": skipped},
    )
    _write_report(run_dir, report)
    return report
