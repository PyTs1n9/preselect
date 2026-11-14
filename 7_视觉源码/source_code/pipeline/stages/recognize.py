"""Recognize PDF pages after validating the externally supplied I4 assets."""

from pathlib import Path

from ..contracts import ModelRequest, RunContext, StageResult
from ..errors import PdfRenderFailed
from ..pdf_input import load_pdf
from ..prompt_loader import load_prompt, load_skill
from ..schema_loader import load_schema
from ..vision_provider import get_provider
from ._shared import (generate_and_close, record_input, stage_record,
                      write_json_atomic, write_text_atomic)

STAGE_NAME = "recognize"
REQUIRES = []
PRODUCES = ["recognition"]


def run(ctx: RunContext, inputs: dict[str, Path]) -> StageResult:
    with stage_record(ctx, STAGE_NAME) as state:
        prompt = load_prompt("vision", ctx.settings.paths.prompts_dir)
        call = state.prompt("vision", prompt)
        skill = load_skill(ctx.settings.paths.skills_dir / "pdf-vision" / "SKILL.md")
        call["skill_sha256"] = skill.sha256

        source = ctx.artifacts["source_pdf"]
        info = load_pdf(source, run_dir=ctx.run_dir)
        write_text_atomic(ctx.run_dir / "input" / "source.pdf.sha256", info.sha256 + "\n")
        record_input(ctx, source, info)
        images = sorted((ctx.run_dir / "input").glob("page_*.png"))
        if len(images) != info.page_count:
            raise PdfRenderFailed(
                f"Rendered PNG count mismatch: expected {info.page_count}, actual {len(images)}"
            )
        request = ModelRequest(
            stage=STAGE_NAME, prompt_name="vision", prompt_text=prompt.text,
            payload={"material_id": ctx.material_id, "page_count": info.page_count,
                     "pages": [{"page_number": page.page_number} for page in info.pages]},
            json_schema=load_schema("recognition"), images=images,
            skill=("pdf-vision", str(skill.path)), timeout_s=ctx.settings.vision.timeout_s,
            model=ctx.settings.vision.model or None,
        )
        provider = get_provider(ctx.settings.vision.provider, settings=ctx.settings, run_dir=ctx.run_dir)
        result = generate_and_close(provider, request, call)
        path = ctx.run_dir / "recognition.json"
        write_json_atomic(path, result.parsed)
        state.artifacts["recognition"] = path
        state.validate("recognition", result.parsed)
    return StageResult(STAGE_NAME, "ok", 0.0, state.artifacts, state.issues)
