"""Bounded Markdown repair with per-attempt evidence and deterministic checks."""

import difflib
import json
import re
from pathlib import Path

from .contracts import ModelRequest, RunContext, StageResult
from .errors import AssetMissing, Code, InvalidModelOutput, IoFailure, PipelineError, redact
from .prompt_loader import load_prompt
from .stages._shared import (
    generate_and_close, read_json_input, stage_record, write_json_atomic, write_text_atomic,
)
from .validate import validate_run
from .vision_provider import get_provider


_NUMBER = re.compile(r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")


def _safe_text(text: str, ctx: RunContext) -> str:
    return redact(text, (ctx.settings.vision.api_key.get_secret_value() or "",))


def _read_draft(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        raise AssetMissing("Required artifact is missing: draft.md") from None
    except (OSError, UnicodeError):
        raise IoFailure("Unable to read UTF-8 draft.md") from None


def _remove_final(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        raise IoFailure("Unable to remove failed final.md") from None


def _content_line(line: str) -> str:
    line = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", line)
    if re.match(r"^\s*#{1,6}\s+", line):
        line = re.sub(r"^\s*#{1,6}\s+", "", line)
        line = re.sub(r"^\d+(?:\.\d+)*[.)、]?\s+", "", line)
    return line


def _check_added_numbers(diff: str, evidence: dict) -> None:
    evidence_text = _NUMBER.sub(
        lambda match: match.group().replace(",", ""), json.dumps(evidence, ensure_ascii=False)
    )
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for match in _NUMBER.finditer(_content_line(line[1:])):
            token = match.group().replace(",", "")
            if re.search(r"(?<![\w.,])" + re.escape(token) + r"(?![\w.,%])", evidence_text) is None:
                raise PipelineError(Code.REPAIR_INTRODUCED_FACT,
                                    "Repair added a number absent from evidence")


def _finish(ctx: RunContext, text: str, state) -> None:
    final_path = ctx.run_dir / "final.md"
    write_text_atomic(final_path, text)
    report = validate_run(ctx.run_dir, ctx.settings)
    if not report.valid:
        _remove_final(final_path)
        raise PipelineError(Code.REPAIR_EXHAUSTED, "Final validation failed after repair")
    state.artifacts["final"] = final_path


def repair_draft(draft_path: Path, validation_path: Path, ctx: RunContext) -> StageResult:
    with stage_record(ctx, "repair") as state:
        try:
            report = validate_run(ctx.run_dir, ctx.settings)
            if not validation_path.is_file():
                raise IoFailure("Validation report was not written")
            if report.valid:
                _finish(ctx, _read_draft(draft_path), state)
            else:
                max_attempts = ctx.settings.pipeline.max_repair_attempts
                if max_attempts == 0:
                    raise PipelineError(Code.REPAIR_EXHAUSTED, "Repair attempt limit is zero")
                prompt = load_prompt("repair", ctx.settings.paths.prompts_dir)
                evidence = read_json_input(ctx.run_dir / "evidence.json")
                for attempt in range(1, max_attempts + 1):
                    draft = _read_draft(draft_path)
                    errors = [issue for issue in report.issues if issue.severity == "error"]
                    codes = sorted({issue.code.value for issue in errors})
                    payload = {
                        "draft": _safe_text(draft, ctx),
                        "issues": [{
                            "code": issue.code.value, "path": issue.path,
                            "message": _safe_text(issue.message, ctx), "hint": _safe_text(issue.hint, ctx),
                        } for issue in errors],
                        "constraints": {"only_codes": codes, "preserve_unlisted_text": True,
                                        "no_new_facts": True},
                    }
                    call = state.prompt("repair", prompt)
                    request = ModelRequest(
                        stage="repair", prompt_name="repair", prompt_text=prompt.text,
                        payload=payload, json_schema=None, images=None, skill=None,
                        timeout_s=ctx.settings.vision.timeout_s, model=ctx.settings.vision.model or None,
                    )
                    provider = get_provider(ctx.settings.vision.provider, settings=ctx.settings,
                                            run_dir=ctx.run_dir)
                    result = generate_and_close(provider, request, call)
                    if not isinstance(result.text, str) or not result.text.strip():
                        raise InvalidModelOutput("Repair returned no Markdown text")
                    revision = _safe_text(result.text, ctx).replace("\r\n", "\n").replace("\r", "\n")
                    diff = "\n".join(difflib.unified_diff(
                        draft.splitlines(), revision.splitlines(),
                        fromfile=f"draft.md (before repair {attempt})",
                        tofile=f"draft.md (after repair {attempt})", lineterm="",
                    ))
                    write_text_atomic(ctx.run_dir / "raw" / f"repair_{attempt}_diff.md",
                                      _safe_text(diff + "\n" if diff else "", ctx))
                    metadata = {
                        "stage": "repair", "attempt": attempt, "prompt_name": "repair",
                        "prompt_sha256": prompt.sha256, "prompt_version": prompt.version,
                        "codes": codes, "model": result.model, "attempts": result.attempts,
                        "request_hash": result.request_hash,
                    }
                    write_json_atomic(ctx.run_dir / "raw" / f"repair_{attempt}_request.json", {
                        key: _safe_text(value, ctx) if isinstance(value, str) else value
                        for key, value in metadata.items()
                    })
                    _check_added_numbers(diff, evidence)
                    write_text_atomic(draft_path, revision)
                    report = validate_run(ctx.run_dir, ctx.settings)
                    if report.valid:
                        _finish(ctx, revision, state)
                        break
                else:
                    raise PipelineError(Code.REPAIR_EXHAUSTED, "Repair attempt limit exhausted")
        except Exception:
            _remove_final(ctx.run_dir / "final.md")
            raise
    return StageResult("repair", "ok", 0.0, state.artifacts, state.issues)
