"""Write a draft from persisted evidence and the configured writer asset."""

from pathlib import Path

from .contracts import ModelRequest, RunContext, StageResult
from .errors import InvalidModelOutput, IoFailure, redact
from .prompt_loader import load_prompt
from .stages._shared import generate_and_close, read_json_input, stage_record, write_text_atomic
from .vision_provider import get_provider


def _draft_payload(evidence: dict) -> dict:
    return {"evidence": evidence}


def write_draft(evidence_path: Path, ctx: RunContext) -> StageResult:
    with stage_record(ctx, "write") as state:
        prompt = load_prompt("writer", ctx.settings.paths.prompts_dir)
        call = state.prompt("writer", prompt)
        evidence = read_json_input(evidence_path)
        request = ModelRequest(
            stage="write", prompt_name="writer", prompt_text=prompt.text,
            payload=_draft_payload(evidence), json_schema=None, images=None, skill=None,
            timeout_s=ctx.settings.vision.timeout_s, model=ctx.settings.vision.model or None,
        )
        provider = get_provider(ctx.settings.vision.provider, settings=ctx.settings, run_dir=ctx.run_dir)
        result = generate_and_close(provider, request, call)
        if not isinstance(result.text, str) or not result.text.strip():
            raise InvalidModelOutput("Writer returned no Markdown text")
        text = redact(result.text, (ctx.settings.vision.api_key.get_secret_value() or "",))
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        draft_path = ctx.run_dir / "draft.md"
        write_text_atomic(draft_path, text)
        if not draft_path.is_file():
            raise IoFailure("Draft artifact was not written")
        state.artifacts["draft"] = draft_path
    return StageResult("write", "ok", 0.0, state.artifacts, state.issues)
