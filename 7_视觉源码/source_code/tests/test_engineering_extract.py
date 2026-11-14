"""A19 tests run real text extraction, page rendering and schema validation.

Only the external model provider is replaced. PDFs are constructed in pytest's
temporary directory, without editing the read-only business fixtures.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import (DecodedStreamObject, DictionaryObject, NameObject,
                           NumberObject)

from pipeline.contracts import ModelResult, RunContext
from pipeline.errors import Code, PipelineError, exit_code_for
from pipeline.extract import (FIGURE_AREA_RATIO, MIN_TEXT_CHARS, TABLE_NUMERIC_RATIO,
                              build_blocks_from_text, check_table_shape, classify_page,
                              clean_text, page_numeric_tokens)
from pipeline.schema_loader import load_schema, validate_instance
from pipeline.settings import load_settings
from pipeline.stages import recognize


ROOT = Path(__file__).resolve().parents[1]
PROSE = "\n".join(["Readable engineering prose preserves the original words on each line."] * 5)
TABLE_TEXT = "First Second Third\n" + "\n".join(["1234 5678 9012"] * 20)


@pytest.mark.parametrize(("raw", "expected"), [
    ("ﬁ ﬂ ﬀ ﬃ ﬄ", "fi fl ff ffi ffl"),
    ("P h . D .", "Ph.D."),
    ("prefix P h . D . suffix", "prefix Ph.D. suffix"),
    ("inter-\nnational", "international"),
    ("inter- \r\n  national", "international"),
    ("  ordinary\t text   here  ", "ordinary text here"),
    ("  first line \n\n second line  ", "first line\n\nsecond line"),
    ("A B\nC D", "A B\nC D"),
    ("  \t\n", ""),
])
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected
    assert clean_text(clean_text(raw)) == expected


@pytest.mark.parametrize(("metrics", "kind"), [
    ((199, 0.9, True, 0.8), "scan_page"),
    ((200, 0.301, True, 0.8), "table_page"),
    ((200, 0.3, True, 0.8), "formula_page"),
    ((200, 0.3, False, 0.151), "text_page_with_figure"),
    ((200, 0.3, False, 0.15), "text_page"),
    ((3269, 0.028, False, 0.0), "text_page"),
])
def test_classify_page_thresholds_and_precedence(metrics, kind):
    assert MIN_TEXT_CHARS == 200
    assert TABLE_NUMERIC_RATIO == 0.30
    assert FIGURE_AREA_RATIO == 0.15
    actual, reasons = classify_page(*metrics)
    assert actual == kind
    assert reasons == [f"text_chars={metrics[0]}", f"numeric_ratio={metrics[1]:.3f}",
                       f"formula_marker={str(metrics[2]).lower()}",
                       f"image_area_ratio={metrics[3]:.3f}"]


def test_build_blocks_preserves_lines_and_continuous_ids():
    assert build_blocks_from_text("\n ﬁrst line \n\n inter-\nnational \n final\tline\n") == [
        {"block_id": "B001", "text": "first line", "region": None},
        {"block_id": "B002", "text": "international", "region": None},
        {"block_id": "B003", "text": "final line", "region": None},
    ]
    assert build_blocks_from_text("\n \n") == []


def test_page_numeric_tokens_keeps_duplicates_and_literal_precision():
    assert page_numeric_tokens("count 1,234; ratio 12%; loss -5.25; repeat 12%; value 0.80") == [
        "-5.25", "0.80", "12%", "12%", "1234"]
    assert page_numeric_tokens("no numeric tokens") == []


@pytest.mark.parametrize("table", [
    {"header": [], "rows": []},
    {"header": ["a", "b"], "rows": [["only one"]]},
    {"header": ["a"], "rows": [["one", "two"]]},
    {"header": ["a"], "rows": ["not a row"]},
])
def test_check_table_shape_rejects_invalid_rows(table):
    with pytest.raises(PipelineError) as caught:
        check_table_shape({"tables": [table]})
    assert caught.value.code == Code.SCHEMA_INVALID
    assert exit_code_for(caught.value) == 4


def test_check_table_shape_accepts_rectangular_and_empty_tables():
    check_table_shape({"tables": [{"header": ["a", "b"], "rows": [["", "value"], ["x", "y"]]}]})
    check_table_shape({"tables": [{"header": ["a"], "rows": []}]})
    check_table_shape({"tables": []})


def _pdf(path: Path, texts: list[str], *, image_sizes: dict[int, int] | None = None) -> Path:
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    }))
    for number, text in enumerate(texts, start=1):
        page = writer.add_blank_page(width=300, height=300)
        resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        commands = ["BT /F1 6 Tf 12 280 Td 10 TL"]
        for line in text.splitlines():
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"({escaped}) Tj T*")
        commands.append("ET")
        if image_sizes and number in image_sizes:
            raster = DecodedStreamObject()
            raster.set_data(bytes([80, 120, 160] * 4))
            raster.update({NameObject("/Type"): NameObject("/XObject"),
                           NameObject("/Subtype"): NameObject("/Image"),
                           NameObject("/Width"): NumberObject(2),
                           NameObject("/Height"): NumberObject(2),
                           NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                           NameObject("/BitsPerComponent"): NumberObject(8)})
            resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Img1"): writer._add_object(raster)})
            size = image_sizes[number]
            commands.append(f"q {size} 0 0 {size} 60 60 cm /Img1 Do Q")
        page[NameObject("/Resources")] = resources
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)
    return path


def _context(tmp_path, source):
    settings = load_settings(cli_overrides={
        "vision.provider": "openai_codex", "vision.model": "offline-extract-model",
        "vision.api_key": "offline-extract-test", "vision.base_url": "https://example.test/v1",
        "paths.prompts_dir": str(ROOT / "prompts"), "paths.skills_dir": str(ROOT / "skills"),
        "paths.result_dir": str(tmp_path / "runs"),
    }, require_secret=False)
    return RunContext("extract-run", "extract-material", tmp_path / "run", settings, {"source_pdf": source})


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _record(ctx):
    return next(stage for stage in _read(ctx.run_dir / "manifest.json")["stages"]
                if stage["name"] == "recognize")


class _Provider:
    def __init__(self, factory):
        self.factory = factory
        self.closed = False

    def generate(self, request):
        assert not self.closed
        self.factory.requests.append(request)
        payload = self.factory.output(request)
        return ModelResult(json.dumps(payload, ensure_ascii=False), payload, {}, {}, False,
                           1.0, 1, "0" * 64, "offline-extract-model", "completed")

    def close(self):
        assert not self.closed
        self.closed = True


class _Factory:
    def __init__(self, output):
        self.output = output
        self.requests = []
        self.providers = []

    def __call__(self, name, **options):
        assert name == "openai_codex"
        provider = _Provider(self)
        self.providers.append(provider)
        return provider


def _block(text, *, region=None):
    return {"block_id": "B001", "text": text, "region": region}


def _scan_output(text, *, region=None):
    return {"pages": [{"page_number": 1, "blocks": [_block(text, region=region)]}]}


def _assert_valid_recognition(ctx, count):
    payload = _read(ctx.run_dir / "recognition.json")
    assert validate_instance("recognition", payload) == []
    assert [page["page_number"] for page in payload["pages"]] == list(range(1, count + 1))
    return payload


def test_recognize_text_pdf_never_constructs_provider_and_finishes_under_five_seconds(tmp_path, monkeypatch):
    source = _pdf(tmp_path / "text.pdf", [PROSE] * 3)
    ctx = _context(tmp_path, source)
    creations = []

    def forbidden_provider(*args, **kwargs):
        creations.append(True)
        pytest.fail("A text page must not create a model provider")

    monkeypatch.setattr(recognize, "get_provider", forbidden_provider)
    started = time.perf_counter()
    result = recognize.run(ctx, {})
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0
    assert result.status == "ok" and not creations
    assert set(result.artifacts) == {"recognition", "page_text", "page_plan"}
    assert _record(ctx)["model_calls"] == []
    assert [page["kind"] for page in _read(ctx.run_dir / "page_plan.json")["pages"]] == ["text_page"] * 3
    payload = _assert_valid_recognition(ctx, 3)
    assert all(page["blocks"] == build_blocks_from_text(PROSE) for page in payload["pages"])
    text_pages = _read(ctx.run_dir / "page_text.json")["pages"]
    assert text_pages == [{"page_number": n, "text": PROSE, "chars": len(PROSE)} for n in range(1, 4)]
    assert len(list((ctx.run_dir / "input").glob("page_*.png"))) == 3


def test_recognize_blank_scan_calls_vision_once_and_adopts_page(tmp_path, monkeypatch):
    ctx = _context(tmp_path, _pdf(tmp_path / "scan.pdf", [""]))
    payload = _scan_output("Visible scanned words", region=[{"x0": 0.1, "y0": 0.2, "x1": 0.8, "y1": 0.9}])
    factory = _Factory(lambda request: payload)
    monkeypatch.setattr(recognize, "get_provider", factory)
    recognize.run(ctx, {})
    assert _assert_valid_recognition(ctx, 1) == payload
    assert len(factory.requests) == 1
    request = factory.requests[0]
    assert request.prompt_name == "vision"
    assert request.json_schema == load_schema("recognition")
    assert request.images == [ctx.run_dir / "input" / "page_0001.png"]
    assert factory.providers[0].closed
    assert _read(ctx.run_dir / "page_plan.json")["pages"][0]["kind"] == "scan_page"


@pytest.mark.parametrize(("source_text", "visual_text", "conflicts", "rejected"), [
    ("Reading 11", "Reading 12 13", 3, False),
    ("Reading 11 22", "Reading 33 44", 4, True),
    ("", "Reading 11 22 33 44", 4, True),
    ("Reading 11 11", "Reading 11", 0, False),
])
def test_recognize_numeric_conflicts_use_set_difference_and_strict_boundary(
        tmp_path, monkeypatch, source_text, visual_text, conflicts, rejected):
    ctx = _context(tmp_path, _pdf(tmp_path / "numbers.pdf", [source_text]))
    payload = _scan_output(visual_text)
    factory = _Factory(lambda request: payload)
    monkeypatch.setattr(recognize, "get_provider", factory)
    recognize.run(ctx, {})
    actual = _assert_valid_recognition(ctx, 1)["pages"][0]["blocks"]
    assert actual == (build_blocks_from_text(source_text) if rejected else payload["pages"][0]["blocks"])
    reasons = _read(ctx.run_dir / "page_plan.json")["pages"][0]["reasons"]
    assert (f"vision_rejected=数字冲突{conflicts}处" in reasons) is rejected
    # A missing skill version belongs to B's asset state, not to this check.
    warnings = [issue for issue in _record(ctx)["issues"]
                if issue["severity"] == "warning" and issue["code"] != "ASSET_NO_VERSION"]
    if conflicts:
        assert f"numeric_conflicts={conflicts}" in reasons
        assert any(issue["code"] == "NUMBER_MISMATCH" for issue in warnings)
    else:
        assert not warnings
    assert len(factory.requests) == 1 and factory.providers[0].closed


def _specialized(name, page_number=1):
    if name == "table":
        return {"page_number": page_number, "tables": [{"table_id": "T01", "caption": None,
                "header": ["First", "Second", "Third"], "rows": [["1234", "5678", "9012"]], "bbox": None}]}
    if name == "formula":
        return {"page_number": page_number, "formulas": [{"formula_id": "E01", "text": "R = k * C",
                "symbols": [{"symbol": "R", "meaning": None}], "bbox": None}]}
    return {"page_number": page_number, "figures": [{"figure_id": "G01", "caption": None,
            "description": "图中展示一个均匀的蓝灰色矩形区域，没有可辨认的坐标轴或读数。",
            "readouts": [], "bbox": [0.2, 0.2, 0.8, 0.8]}]}


@pytest.mark.parametrize(("name", "text", "kind", "image_sizes", "artifact"), [
    ("table", TABLE_TEXT, "table_page", None, "tables"),
    ("formula", PROSE + "\nR = k * C", "formula_page", None, "formulas"),
    ("figure", PROSE, "text_page_with_figure", {1: 180}, "figures"),
])
def test_specialized_routes_keep_text_blocks_and_write_valid_page_output(
        tmp_path, monkeypatch, name, text, kind, image_sizes, artifact):
    ctx = _context(tmp_path, _pdf(tmp_path / f"{name}.pdf", [text], image_sizes=image_sizes))
    payload = _specialized(name)
    factory = _Factory(lambda request: payload)
    monkeypatch.setattr(recognize, "get_provider", factory)
    result = recognize.run(ctx, {})
    assert set(result.artifacts) == {"recognition", "page_text", "page_plan", artifact}
    assert artifact not in recognize.PRODUCES
    assert _read(ctx.run_dir / f"{artifact}.json") == {"pages": [payload]}
    assert validate_instance(name, payload) == []
    assert _assert_valid_recognition(ctx, 1)["pages"][0]["blocks"] == build_blocks_from_text(text)
    plan = _read(ctx.run_dir / "page_plan.json")["pages"][0]
    assert plan["kind"] == kind
    assert not any(reason.startswith("vision_rejected") for reason in plan["reasons"])
    if name == "figure":
        assert "image_area_ratio=0.360" in plan["reasons"]
    assert len(factory.requests) == 1
    request = factory.requests[0]
    assert request.prompt_name == name and request.json_schema == load_schema(name)
    assert request.images == [ctx.run_dir / "input" / "page_0001.png"]
    assert factory.providers[0].closed
    record = _record(ctx)
    # Whether SKILL.md declares a version is B's content decision. When it is
    # absent the loader must surface a warning, and that warning is the only
    # issue this stage may add on its own.
    assert [issue for issue in record["issues"] if issue["code"] != "ASSET_NO_VERSION"] == []
    call = record["model_calls"][0]
    assert "skill_sha256" in call and "skill_version" in call
    if call["skill_version"] is None:
        assert any(issue["code"] == "ASSET_NO_VERSION" and issue["path"].endswith("SKILL.md")
                   for issue in record["issues"])


def test_small_embedded_image_does_not_trigger_vision(tmp_path, monkeypatch):
    ctx = _context(tmp_path, _pdf(tmp_path / "small-image.pdf", [PROSE], image_sizes={1: 10}))
    monkeypatch.setattr(recognize, "get_provider", lambda *args, **kwargs: pytest.fail("Small image triggered provider"))
    recognize.run(ctx, {})
    plan = _read(ctx.run_dir / "page_plan.json")["pages"][0]
    assert plan["kind"] == "text_page"
    assert "image_area_ratio=0.001" in plan["reasons"]
    assert _record(ctx)["model_calls"] == []


def test_table_shape_failure_is_explicit_and_provider_is_closed(tmp_path, monkeypatch):
    ctx = _context(tmp_path, _pdf(tmp_path / "bad-table.pdf", [TABLE_TEXT]))
    payload = _specialized("table")
    payload["tables"][0]["rows"] = [["1234"]]
    factory = _Factory(lambda request: payload)
    monkeypatch.setattr(recognize, "get_provider", factory)
    with pytest.raises(PipelineError) as caught:
        recognize.run(ctx, {})
    assert caught.value.code == Code.SCHEMA_INVALID and exit_code_for(caught.value) == 4
    assert _record(ctx)["status"] == "failed"
    assert factory.providers[0].closed


def test_specialized_wrong_page_fails_before_output_acceptance(tmp_path, monkeypatch):
    ctx = _context(tmp_path, _pdf(tmp_path / "wrong-page.pdf", [PROSE + "\nR = k * C"]))
    factory = _Factory(lambda request: _specialized("formula", page_number=2))
    monkeypatch.setattr(recognize, "get_provider", factory)
    with pytest.raises(PipelineError) as caught:
        recognize.run(ctx, {})
    assert caught.value.code == Code.MODEL_INVALID_JSON and exit_code_for(caught.value) == 5
    assert _record(ctx)["status"] == "failed"
    assert factory.providers[0].closed


def test_specialized_numeric_rejection_discards_untrusted_payload(tmp_path, monkeypatch):
    source_text = PROSE + "\nR = k * C 11 22"
    ctx = _context(tmp_path, _pdf(tmp_path / "rejected-formula.pdf", [source_text]))
    payload = _specialized("formula")
    payload["formulas"][0]["text"] = "R = 33 * 44"
    factory = _Factory(lambda request: payload)
    monkeypatch.setattr(recognize, "get_provider", factory)
    result = recognize.run(ctx, {})
    assert "formulas" not in result.artifacts
    assert not (ctx.run_dir / "formulas.json").exists()
    assert _assert_valid_recognition(ctx, 1)["pages"][0]["blocks"] == build_blocks_from_text(source_text)
    assert "vision_rejected=数字冲突4处" in _read(ctx.run_dir / "page_plan.json")["pages"][0]["reasons"]
