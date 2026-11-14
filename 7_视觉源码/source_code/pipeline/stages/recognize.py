"""Prefer cleaned PDF text; request vision only for pages needing it."""

import math
import re
from pathlib import Path

from pypdf import PdfReader
from pypdf.generic import ContentStream

from ..contracts import ModelRequest, RunContext, StageResult
from ..errors import Code, Issue, PdfRenderFailed, PipelineError
from ..extract import (build_blocks_from_text, check_table_shape, classify_page,
                       clean_text, page_numeric_tokens)
from ..pdf_input import load_pdf
from ..prompt_loader import load_prompt, load_skill
from ..schema_loader import load_schema
from ..vision_provider import get_provider
from ._shared import (generate_and_close, record_input, stage_record,
                      write_json_atomic, write_text_atomic)

STAGE_NAME = "recognize"
REQUIRES = []
PRODUCES = ["recognition", "page_text", "page_plan"]

# Measured against MED-04: one dense page is ~4.8k characters of verbatim text
# and takes ~100s; a two-page turn already exceeded a 300s cap. Each page
# requiring vision gets one call, the only configuration proven to finish,
# at the cost of more calls.

_VISUAL_PROMPTS = {"scan_page": "vision", "table_page": "table",
                   "formula_page": "formula", "text_page_with_figure": "figure"}
_FORMULA_MARKERS = re.compile(r"[=∑√∫±^_]")
_MAX_NUMERIC_CONFLICTS = 3


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
        if not isinstance(page, dict) or type(page.get("page_number")) is not int:
            raise _batch_failure("Recognition page has no integer page_number")
        numbers.append(page["page_number"])
    if sorted(numbers) != expected:
        raise _batch_failure(
            f"Recognition page coverage mismatch: expected {len(expected)} pages, got {len(numbers)}")
    return sorted(pages, key=lambda page: page["page_number"])


def _image_area_ratio(page) -> tuple[float, bool]:
    """Estimate placed image area in PDF points, including nested Form XObjects.

    The determinant of the current graphics transform gives image area: image
    pixels are painted into a unit square. Overlap and clipping are not removed.
    An unreadable graphics stream explicitly yields the card's unknown marker.
    """

    def determinant(matrix):
        if len(matrix) != 6:
            raise ValueError("Invalid graphics matrix")
        a, b, c, d, _, _ = map(float, matrix)
        result = a * d - b * c
        if not math.isfinite(result):
            raise ValueError("Non-finite graphics matrix")
        return result

    def placed_area(stream, resources, scale=1.0, depth=0):
        if depth > 32:
            raise ValueError("Nested image resources exceed inspection limit")
        content = stream if isinstance(stream, ContentStream) else ContentStream(stream, page.pdf)
        stack = []
        area = 0.0
        for operands, operator in content.operations:
            if operator == b"q":
                stack.append(scale)
            elif operator == b"Q":
                scale = stack.pop()
            elif operator == b"cm":
                scale *= determinant(operands)
            elif operator == b"INLINE IMAGE":
                area += abs(scale)
            elif operator == b"Do":
                obj = resources["/XObject"][operands[0]].get_object()
                if obj.get("/Subtype") == "/Image":
                    area += abs(scale)
                elif obj.get("/Subtype") == "/Form":
                    matrix = obj.get("/Matrix", [1, 0, 0, 1, 0, 0])
                    area += placed_area(obj, obj.get("/Resources", resources),
                                        scale * determinant(matrix), depth + 1)
                else:
                    raise ValueError("Unsupported graphical XObject")
        if stack:
            raise ValueError("Unbalanced graphics state")
        return area

    try:
        page_area = float(page.mediabox.width) * float(page.mediabox.height)
        if not math.isfinite(page_area) or page_area <= 0:
            raise ValueError("Invalid page area")
        contents = page.get_contents()
        area = 0.0 if contents is None else placed_area(contents, page.get("/Resources", {}))
        ratio = area / page_area
        if not math.isfinite(ratio):
            raise ValueError("Invalid image area")
        return ratio, True
    except Exception:
        # Only area estimation may degrade; text extraction below must fail.
        return 0.0, False


def _plan_pages(source: Path, page_count: int) -> tuple[dict, dict]:
    texts, plans = [], []
    try:
        with source.open("rb") as stream:
            reader = PdfReader(stream)
            if len(reader.pages) != page_count:
                raise PdfRenderFailed("Text extraction page count mismatch")
            for number, page in enumerate(reader.pages, 1):
                try:
                    raw = page.extract_text() or ""
                except Exception:
                    raise PdfRenderFailed(f"Unable to extract text from PDF page {number}") from None
                cleaned = clean_text(raw)
                chars = len(cleaned)
                numeric_ratio = sum(char.isdigit() for char in cleaned) / chars if chars else 0.0
                image_ratio, area_known = _image_area_ratio(page)
                kind, reasons = classify_page(chars, numeric_ratio,
                                              bool(_FORMULA_MARKERS.search(raw)), image_ratio)
                if not area_known:
                    reasons.append("image_area=unknown")
                texts.append({"page_number": number, "text": cleaned, "chars": chars})
                plans.append({"page_number": number, "kind": kind, "reasons": reasons})
    except PipelineError:
        raise
    except Exception:
        raise PdfRenderFailed("Unable to read PDF text layer") from None
    return {"pages": texts}, {"pages": plans}


def _visual_text(name: str, payload: dict) -> str:
    """Compare visible text only; IDs, page numbers and coordinates are metadata."""
    parts = []
    if name == "vision":
        parts = [block["text"] for page in payload["pages"] for block in page["blocks"]]
    elif name == "table":
        for table in payload["tables"]:
            parts.extend([table["caption"] or "", *table["header"]])
            parts.extend(cell for row in table["rows"] for cell in row)
    elif name == "formula":
        for formula in payload["formulas"]:
            parts.append(formula["text"])
            for symbol in formula["symbols"]:
                parts.extend([symbol["symbol"], symbol["meaning"] or ""])
    elif name == "figure":
        for figure in payload["figures"]:
            parts.extend([figure["caption"] or "", figure["description"]])
            for readout in figure["readouts"]:
                parts.extend([readout["label"], readout["value_text"]])
    return "\n".join(parts)


def run(ctx: RunContext, inputs: dict[str, Path]) -> StageResult:
    with stage_record(ctx, STAGE_NAME) as state:
        source = ctx.artifacts["source_pdf"]
        info = load_pdf(source, run_dir=ctx.run_dir, dpi=ctx.settings.vision.dpi,
                        settings=ctx.settings)
        write_text_atomic(ctx.run_dir / "input" / "source.pdf.sha256", info.sha256 + "\n")
        record_input(ctx, source, info)
        images = sorted((ctx.run_dir / "input").glob("page_*.png"))
        if len(images) != info.page_count:
            raise PdfRenderFailed(
                f"Rendered PNG count mismatch: expected {info.page_count}, actual {len(images)}")
        rendered = {page.page_number: ctx.run_dir / "input" / f"page_{page.page_number:04d}.png"
                    for page in info.pages}

        page_text, page_plan = _plan_pages(source, info.page_count)
        for name, payload in (("page_text", page_text), ("page_plan", page_plan)):
            path = ctx.run_dir / f"{name}.json"
            write_json_atomic(path, payload)
            state.artifacts[name] = path

        merged: list[dict] = []
        supplements: dict[str, list[dict]] = {}
        for text_page, plan in zip(page_text["pages"], page_plan["pages"]):
            number = plan["page_number"]
            text = text_page["text"]
            recognized = {"page_number": number, "blocks": build_blocks_from_text(text)}
            if plan["kind"] == "text_page":
                merged.append(recognized)
                continue

            name = _VISUAL_PROMPTS[plan["kind"]]
            prompt = load_prompt(name, ctx.settings.paths.prompts_dir)
            skill = load_skill(ctx.settings.paths.skills_dir / "pdf-vision" / "SKILL.md")
            schema_name = "recognition" if name == "vision" else name
            schema = load_schema(schema_name)
            call = state.prompt(name, prompt)
            call["skill_sha256"] = skill.sha256
            request_payload = {"material_id": ctx.material_id, "page_count": info.page_count,
                               "pages": [{"page_number": number}]}
            if name != "vision":
                request_payload["page_number"] = number
            request = ModelRequest(
                stage=STAGE_NAME, prompt_name=name, prompt_text=prompt.text,
                payload=request_payload, json_schema=schema, images=[rendered[number]],
                skill=("pdf-vision", str(skill.path)), timeout_s=ctx.settings.vision.timeout_s,
                model=ctx.settings.vision.model or None,
            )
            provider = get_provider(ctx.settings.vision.provider, settings=ctx.settings,
                                    run_dir=ctx.run_dir)
            result = generate_and_close(provider, request, call)
            visual = result.parsed
            if name == "vision":
                candidate = _check_batch(visual, [number])[0]
            state.validate(schema_name, visual)
            if name != "vision" and (type(visual["page_number"]) is not int
                                     or visual["page_number"] != number):
                raise _batch_failure("Visual output page_number does not match the requested page")
            if name == "table":
                check_table_shape(visual)

            conflicts = len(set(page_numeric_tokens(text)) ^ set(page_numeric_tokens(_visual_text(name, visual))))
            if conflicts:
                plan["reasons"].append(f"numeric_conflicts={conflicts}")
                state.issues.append(Issue(
                    Code.NUMBER_MISMATCH, STAGE_NAME, f"page_plan.json#/pages/{number - 1}",
                    "warning", f"Page {number}: visual and text-layer numeric sets differ by {conflicts} tokens",
                    "Use the text layer as the numeric reference; inspect page_plan.json",
                ))
            rejected = conflicts > _MAX_NUMERIC_CONFLICTS
            if rejected:
                plan["reasons"].append(f"vision_rejected=数字冲突{conflicts}处")
            elif name == "vision":
                recognized = candidate
            else:
                artifact = {"table": "tables", "formula": "formulas", "figure": "figures"}[name]
                supplements.setdefault(artifact, []).append(visual)
                path = ctx.run_dir / f"{artifact}.json"
                write_json_atomic(path, {"pages": supplements[artifact]})
                state.artifacts[artifact] = path
            write_json_atomic(state.artifacts["page_plan"], page_plan)
            merged.append(recognized)

        payload = {"pages": _check_batch({"pages": merged}, list(range(1, info.page_count + 1)))}
        path = ctx.run_dir / "recognition.json"
        write_json_atomic(path, payload)
        state.artifacts["recognition"] = path
        state.validate("recognition", payload)
    return StageResult(STAGE_NAME, "ok", 0.0, state.artifacts, state.issues)
