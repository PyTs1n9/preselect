"""Extract evidence from the document IR read afresh from disk."""

from pathlib import Path

from ..contracts import ModelRequest, RunContext, StageResult
from ..errors import Code, PipelineError
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
            # The model cannot invent the identity it is asked to echo, so the
            # run's material_id is supplied instead of left to "unknown".
            payload={"document_ir": document_ir, "material_id": ctx.material_id},
            json_schema=load_schema("evidence"),
            timeout_s=ctx.settings.vision.timeout_s, model=ctx.settings.vision.model or None,
        )
        provider = get_provider(ctx.settings.vision.provider, settings=ctx.settings, run_dir=ctx.run_dir)
        result = generate_and_close(provider, request, call)
        parsed = result.parsed
        if isinstance(parsed, dict) and parsed.get("material_id") != ctx.material_id:
            # Fail here rather than one stage later as a cross-material mismatch.
            raise PipelineError(Code.MATERIAL_MISMATCH,
                                "Evidence material_id must equal the run material_id")
        path = ctx.run_dir / "evidence.json"
        write_json_atomic(path, parsed)
        state.artifacts["evidence"] = path
        state.validate("evidence", result.parsed)
    return StageResult(STAGE_NAME, "ok", 0.0, state.artifacts, state.issues)
