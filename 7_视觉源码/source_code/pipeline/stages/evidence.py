"""Extract evidence from the document IR read afresh from disk."""

from pathlib import Path

from ..contracts import ModelRequest, RunContext, StageResult
from ..prompt_loader import load_prompt
from ..schema_loader import load_schema
from ..vision_provider import get_provider
from ._shared import generate_and_close, read_json_input, stage_record, write_json_atomic

STAGE_NAME = "evidence"
REQUIRES = ["normalize"]
PRODUCES = ["evidence"]


def run(ctx: RunContext, inputs: dict[str, Path]) -> StageResult:
    with stage_record(ctx, STAGE_NAME) as state:
        prompt = load_prompt("evidence", ctx.settings.paths.prompts_dir)
        call = state.prompt("evidence", prompt)
        document_ir = read_json_input(ctx.run_dir / "document_ir.json")
        request = ModelRequest(
            stage=STAGE_NAME, prompt_name="evidence", prompt_text=prompt.text,
            payload={"document_ir": document_ir}, json_schema=load_schema("evidence"),
            timeout_s=ctx.settings.vision.timeout_s, model=ctx.settings.vision.model or None,
        )
        provider = get_provider(ctx.settings.vision.provider, settings=ctx.settings, run_dir=ctx.run_dir)
        result = generate_and_close(provider, request, call)
        path = ctx.run_dir / "evidence.json"
        write_json_atomic(path, result.parsed)
        state.artifacts["evidence"] = path
        state.validate("evidence", result.parsed)
    return StageResult(STAGE_NAME, "ok", 0.0, state.artifacts, state.issues)
