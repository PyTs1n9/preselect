"""Write stage: an English draft from evidence, then a Chinese translation."""

from pathlib import Path

from .contracts import ModelRequest, RunContext, StageResult
from .errors import InvalidModelOutput, IoFailure, redact
from .prompt_loader import load_prompt
from .stages._shared import generate_and_close, read_json_input, stage_record, write_text_atomic
from .vision_provider import get_provider


def _draft_payload(evidence: dict) -> dict:
    return {"evidence": evidence}


def _translation_payload(draft_en: str) -> dict:
    # The translator sees only the English draft: re-sending evidence invites
    # it to re-introduce facts that the draft deliberately left out.
    return {"draft_en": draft_en}


def _markdown(result, ctx: RunContext, message: str) -> str:
    if not isinstance(result.text, str) or not result.text.strip():
        raise InvalidModelOutput(message)
    text = redact(result.text, (ctx.settings.vision.api_key.get_secret_value() or "",))
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _run_stage_call(ctx: RunContext, state, name: str, prompt, payload: dict) -> object:
    call = state.prompt(name, prompt)
    request = ModelRequest(
        stage="write", prompt_name=name, prompt_text=prompt.text,
        payload=payload, json_schema=None, images=None, skill=None,
        timeout_s=ctx.settings.vision.timeout_s, model=ctx.settings.vision.model or None,
    )
    provider = get_provider(ctx.settings.vision.provider, settings=ctx.settings, run_dir=ctx.run_dir)
    return generate_and_close(provider, request, call)


def write_draft(evidence_path: Path, ctx: RunContext) -> StageResult:
    with stage_record(ctx, "write") as state:
        # I4: assets are validated before any work, so a missing prompt fails
        # before the evidence file is even read.
        writer_prompt = load_prompt("writer", ctx.settings.paths.prompts_dir)
        evidence = read_json_input(evidence_path)

        english = _markdown(
            _run_stage_call(ctx, state, "writer", writer_prompt, _draft_payload(evidence)),
            ctx, "Writer returned no Markdown text")
        draft_en_path = ctx.run_dir / "draft_en.md"
        write_text_atomic(draft_en_path, english)
        if not draft_en_path.is_file():
            raise IoFailure("English draft artifact was not written")
        state.artifacts["draft_en"] = draft_en_path

        translate_prompt = load_prompt("translate", ctx.settings.paths.prompts_dir)
        chinese = _markdown(
            _run_stage_call(ctx, state, "translate", translate_prompt, _translation_payload(english)),
            ctx, "Translator returned no Markdown text")
        draft_path = ctx.run_dir / "draft.md"
        write_text_atomic(draft_path, chinese)
        if not draft_path.is_file():
            raise IoFailure("Draft artifact was not written")
        state.artifacts["draft"] = draft_path
    return StageResult("write", "ok", 0.0, state.artifacts, state.issues)
