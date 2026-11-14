"""Normalize the persisted recognition artifact using its configured prompt."""

from pathlib import Path

from ..contracts import ModelRequest, RunContext, StageResult
from ..prompt_loader import load_prompt
from ..schema_loader import load_schema
from ..vision_provider import get_provider
from ._shared import generate_and_close, read_json_input, stage_record, write_json_atomic

STAGE_NAME = "normalize"
REQUIRES = ["recognize"]
PRODUCES = ["document_ir"]


def run(ctx: RunContext, inputs: dict[str, Path]) -> StageResult:
    with stage_record(ctx, STAGE_NAME) as state:
        prompt = load_prompt("normalize", ctx.settings.paths.prompts_dir)
        call = state.prompt("normalize", prompt)
        recognition = read_json_input(ctx.run_dir / "recognition.json")
        request = ModelRequest(
            stage=STAGE_NAME, prompt_name="normalize", prompt_text=prompt.text,
            payload={"recognition": recognition}, json_schema=load_schema("document_ir"),
            timeout_s=ctx.settings.vision.timeout_s, model=ctx.settings.vision.model or None,
        )
        provider = get_provider(ctx.settings.vision.provider, settings=ctx.settings, run_dir=ctx.run_dir)
        result = generate_and_close(provider, request, call)
        path = ctx.run_dir / "document_ir.json"
        write_json_atomic(path, result.parsed)
        state.artifacts["document_ir"] = path
        state.validate("document_ir", result.parsed)
    return StageResult(STAGE_NAME, "ok", 0.0, state.artifacts, state.issues)
