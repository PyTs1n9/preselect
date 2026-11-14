"""Recognize PDF pages in batches and merge them into one recognition artifact."""

from pathlib import Path

from ..contracts import ModelRequest, RunContext, StageResult
from ..errors import Code, PdfRenderFailed, PipelineError
from ..pdf_input import load_pdf
from ..prompt_loader import load_prompt, load_skill
from ..schema_loader import load_schema
from ..vision_provider import get_provider
from ._shared import (generate_and_close, record_input, stage_record,
                      write_json_atomic, write_text_atomic)

STAGE_NAME = "recognize"
REQUIRES = []
PRODUCES = ["recognition"]

# Measured against MED-04: one dense page is ~4.8k characters of verbatim text
# and takes ~100s; a two-page turn already exceeded a 300s cap. One page per
# turn is the only configuration proven to finish, at the cost of more calls.
BATCH_PAGES = 1


def _batch_failure(message: str) -> PipelineError:
    return PipelineError(Code.MODEL_INVALID_JSON, message)


def _check_batch(parsed: object, expected: list[int]) -> list[dict]:
    """Reject a batch that does not cover exactly the pages it was given."""

    if not isinstance(parsed, dict):
        raise _batch_failure("Recognition output is not a JSON object")
    pages = parsed.get("pages")
    if not isinstance(pages, list):
        raise _batch_failure("Recognition output has no pages array")
    numbers: list[int] = []
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("page_number"), int):
            raise _batch_failure("Recognition page has no integer page_number")
        numbers.append(page["page_number"])
    if sorted(numbers) != expected:
        raise _batch_failure(
            f"Recognition page coverage mismatch: expected {len(expected)} pages, got {len(numbers)}")
    return sorted(pages, key=lambda page: page["page_number"])


def run(ctx: RunContext, inputs: dict[str, Path]) -> StageResult:
    with stage_record(ctx, STAGE_NAME) as state:
        prompt = load_prompt("vision", ctx.settings.paths.prompts_dir)
        skill = load_skill(ctx.settings.paths.skills_dir / "pdf-vision" / "SKILL.md")

        source = ctx.artifacts["source_pdf"]
        info = load_pdf(source, run_dir=ctx.run_dir)
        write_text_atomic(ctx.run_dir / "input" / "source.pdf.sha256", info.sha256 + "\n")
        record_input(ctx, source, info)
        images = sorted((ctx.run_dir / "input").glob("page_*.png"))
        if len(images) != info.page_count:
            raise PdfRenderFailed(
                f"Rendered PNG count mismatch: expected {info.page_count}, actual {len(images)}")
        rendered = {page.page_number: ctx.run_dir / "input" / f"page_{page.page_number:04d}.png"
                    for page in info.pages}

        merged: list[dict] = []
        numbers = sorted(rendered)
        for start in range(0, len(numbers), BATCH_PAGES):
            batch = numbers[start:start + BATCH_PAGES]
            call = state.prompt("vision", prompt)
            call["skill_sha256"] = skill.sha256
            request = ModelRequest(
                stage=STAGE_NAME, prompt_name="vision", prompt_text=prompt.text,
                payload={"material_id": ctx.material_id, "page_count": info.page_count,
                         "pages": [{"page_number": number} for number in batch]},
                json_schema=load_schema("recognition"),
                images=[rendered[number] for number in batch],
                skill=("pdf-vision", str(skill.path)), timeout_s=ctx.settings.vision.timeout_s,
                model=ctx.settings.vision.model or None,
            )
            provider = get_provider(ctx.settings.vision.provider, settings=ctx.settings,
                                    run_dir=ctx.run_dir)
            result = generate_and_close(provider, request, call)
            merged.extend(_check_batch(result.parsed, batch))

        payload = {"pages": merged}
        path = ctx.run_dir / "recognition.json"
        write_json_atomic(path, payload)
        state.artifacts["recognition"] = path
        state.validate("recognition", payload)
    return StageResult(STAGE_NAME, "ok", 0.0, state.artifacts, state.issues)
