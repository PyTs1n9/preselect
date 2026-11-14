"""A8 offline stage, persistence, asset-replacement and failure contracts.

被测对象是阶段函数与落盘/失败语义；模型边界已由 A7 单独验收，
替换外部依赖不属于"mock 掉被验证对象"。

The real stage functions, PDF renderer and Python schema validator execute.
Only the model provider is replaced, except the specified PNG-count fault
and an atomic-write filesystem fault. Business fixtures and skills are read-only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from pypdf import PdfWriter

from pipeline import orchestrator
from pipeline.contracts import ModelResult, PageInfo, PdfInfo, RunContext
from pipeline.errors import (
    AssetEmpty, AssetMissing, AuthMissing, Code, InvalidModelOutput, IoFailure,
    ModelRefused, ModelTimeout, PdfRenderFailed, PipelineError, TruncatedResponse,
    exit_code_for,
)
from pipeline.manifest import create_run_manifest
from pipeline.prompt_loader import load_prompt, load_skill
from pipeline.schema_loader import load_schema
from pipeline.settings import load_settings
from pipeline.stages import _shared, evidence, normalize, recognize

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "engineering"
STUB_PROMPTS = FIXTURES / "stub_prompts"
MODULES = (recognize, normalize, evidence)
ARTIFACTS = {"recognize": "recognition", "normalize": "document_ir", "evidence": "evidence"}


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_input(ctx, stage):
    name = {"normalize": "recognition", "evidence": "document_ir"}[stage]
    path = ctx.run_dir / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((FIXTURES / "run_ok" / f"{name}.json").read_bytes())
    return path


def _stage_record(ctx, name):
    return next(row for row in _json(ctx.run_dir / "manifest.json")["stages"] if row["name"] == name)


@pytest.fixture
def ctx(tmp_path):
    pdf = tmp_path / "engineering.pdf"
    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(width=72, height=72)
    with pdf.open("wb") as stream:
        writer.write(stream)
    settings = load_settings(cli_overrides={
        "vision.provider": "openai_codex",
        "vision.model": "offline-engineering-model",
        "vision.base_url": "https://example.test/v1?token=hidden",
        "vision.api_key": "sk-offline-engineering-only",
        "vision.timeout_s": 23.0,
        "paths.prompts_dir": str(STUB_PROMPTS.resolve()),
        "paths.skills_dir": str(ROOT / "skills"),
        "paths.result_dir": str(tmp_path / "runs"),
        "paths.fixtures_dir": str(FIXTURES.resolve()),
    }, require_secret=False)
    return RunContext("engineering-run", "engineering-material", tmp_path / "run", settings, {"source_pdf": pdf})


class FakeProvider:
    def __init__(self, factory):
        self.factory = factory
        self.closed = False
        self.requests = []

    def generate(self, request):
        assert not self.closed
        self.requests.append(request)
        self.factory.requests.append(request)
        payload = self.factory.outputs[request.stage]
        if isinstance(payload, Exception):
            raise payload
        return ModelResult(
            text=json.dumps(payload, ensure_ascii=False), parsed=payload, raw={},
            usage={}, truncated=False, latency_ms=1.25, attempts=1,
            request_hash="0" * 64, model="offline-engineering-model",
            turn_status="completed", error_message=None,
        )

    def close(self):
        assert not self.closed
        self.closed = True
        if self.factory.close_error is not None:
            raise self.factory.close_error


class FakeFactory:
    def __init__(self):
        self.outputs = {stage: _json(FIXTURES / "run_ok" / f"{artifact}.json") for stage, artifact in ARTIFACTS.items()}
        self.requests = []
        self.providers = []
        self.options = []
        self.close_error = None

    def __call__(self, name, **options):
        assert name == "openai_codex"
        self.options.append(options)
        provider = FakeProvider(self)
        self.providers.append(provider)
        return provider


@pytest.fixture
def fake(monkeypatch):
    factory = FakeFactory()
    for module in MODULES:
        monkeypatch.setattr(module, "get_provider", factory)
    monkeypatch.setattr(orchestrator, "STAGE_REGISTRY", {})
    return factory


def test_load_prompt_missing(tmp_path):
    with pytest.raises(AssetMissing) as caught:
        load_prompt("vision", tmp_path)
    assert caught.value.code == Code.ASSET_MISSING
    assert exit_code_for(caught.value) == 8


@pytest.mark.parametrize("content", [b"", b" \r\n\t", b"\xef\xbb\xbf \n"])
def test_load_prompt_empty(tmp_path, content):
    (tmp_path / "vision.md").write_bytes(content)
    with pytest.raises(AssetEmpty) as caught:
        load_prompt("vision", tmp_path)
    assert caught.value.code == Code.ASSET_EMPTY
    assert exit_code_for(caught.value) == 8


def test_load_prompt_decodes_bom_and_hashes_original_bytes(tmp_path):
    text = "<!-- version: 0.0.1-stub -->\n仅供工程测试。\n"
    raw = b"\xef\xbb\xbf" + text.encode("utf-8")
    path = tmp_path / "vision.md"
    path.write_bytes(raw)
    prompt = load_prompt("vision", tmp_path)
    assert prompt.text == text
    assert prompt.sha256 == hashlib.sha256(raw).hexdigest()
    assert prompt.path.resolve() == path.resolve()
    assert prompt.version == "0.0.1-stub"


def test_load_prompt_version_must_be_on_first_line(tmp_path):
    (tmp_path / "vision.md").write_text("工程测试\n<!-- version: 1.2.3 -->\n", encoding="utf-8")
    assert load_prompt("vision", tmp_path).version is None


def test_load_skill_missing_and_readonly_hash(tmp_path):
    with pytest.raises(AssetMissing) as caught:
        load_skill(tmp_path / "absent" / "SKILL.md")
    assert caught.value.code == Code.ASSET_MISSING
    assert exit_code_for(caught.value) == 8
    path = ROOT / "skills" / "pdf-vision" / "SKILL.md"
    raw = path.read_bytes()
    asset = load_skill(path)
    assert asset.sha256 == hashlib.sha256(raw).hexdigest()
    assert asset.text == raw.decode("utf-8-sig")
    assert path.read_bytes() == raw


@pytest.mark.parametrize("module", MODULES, ids=lambda module: module.STAGE_NAME)
def test_unversioned_prompt_warns_without_failing(ctx, fake, tmp_path, module):
    prompt_dir = tmp_path / "unversioned"
    prompt_dir.mkdir()
    name = "vision" if module is recognize else module.STAGE_NAME
    (prompt_dir / f"{name}.md").write_text("仅供工程测试：无版本注释。\n", encoding="utf-8")
    assert load_prompt(name, prompt_dir).version is None
    context = replace(ctx, settings=replace(ctx.settings, paths=replace(ctx.settings.paths, prompts_dir=prompt_dir)))
    if module is not recognize:
        _write_input(context, module.STAGE_NAME)
    output = module.run(context, {})
    assert output.status == "ok"
    assert any(issue.code == Code.ASSET_NO_VERSION and issue.severity == "warning" for issue in output.issues)
    record = _stage_record(context, module.STAGE_NAME)
    assert record["status"] == "ok"
    assert any(issue["code"] == "ASSET_NO_VERSION" and issue["severity"] == "warning" for issue in record["issues"])


def test_recognize_preserves_output_and_exact_request(ctx, fake):
    fake.outputs["recognize"] = {"raw_text": "工程测试原文", "pages": []}
    output = recognize.run(ctx, {})
    path = ctx.run_dir / "recognition.json"
    assert output.name == "recognize" and output.status == "ok"
    assert output.duration_ms == 0.0
    assert output.artifacts == {"recognition": path}
    assert _json(path) == fake.outputs["recognize"]
    assert list(_json(path)) == ["raw_text", "pages"]
    assert path.read_bytes().endswith(b"\n")
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")
    request, = fake.requests
    assert request.stage == "recognize" and request.prompt_name == "vision"
    assert request.prompt_text == (STUB_PROMPTS / "vision.md").read_text(encoding="utf-8")
    assert request.payload == {"material_id": ctx.material_id, "page_count": 2, "pages": [{"page_number": 1}, {"page_number": 2}]}
    assert request.json_schema == load_schema("recognition")
    assert request.images == [ctx.run_dir / "input" / f"page_{n:04d}.png" for n in (1, 2)]
    assert all(path.is_file() for path in request.images)
    assert all(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for path in request.images)
    skill = (ROOT / "skills" / "pdf-vision" / "SKILL.md").resolve()
    assert request.skill == ("pdf-vision", str(skill))
    assert skill.read_text(encoding="utf-8-sig") not in request.prompt_text
    assert request.timeout_s == ctx.settings.vision.timeout_s
    assert request.model == ctx.settings.vision.model
    assert fake.options == [{"settings": ctx.settings, "run_dir": ctx.run_dir}]
    assert len(fake.providers) == 1 and fake.providers[0].closed
    digest = hashlib.sha256(ctx.artifacts["source_pdf"].read_bytes()).hexdigest()
    assert (ctx.run_dir / "input" / "source.pdf.sha256").read_bytes() == (digest + "\n").encode("ascii")
    manifest = _json(ctx.run_dir / "manifest.json")
    assert manifest["input"] == {"path_name": "engineering.pdf", "sha256": digest, "page_count": 2}
    assert not (ctx.run_dir / "input" / "source.pdf").exists()
    assert not (ctx.run_dir / "validation.json").exists()
    record = _stage_record(ctx, "recognize")
    assert record["artifacts"] == {"recognition": "recognition.json"}
    assert record["model_calls"][0]["prompt_sha256"] == hashlib.sha256((STUB_PROMPTS / "vision.md").read_bytes()).hexdigest()
    assert record["model_calls"][0]["skill_sha256"] == hashlib.sha256(skill.read_bytes()).hexdigest()


@pytest.mark.parametrize("missing", ["prompt", "skill"])
def test_missing_assets_stop_before_pdf_or_provider(ctx, fake, tmp_path, missing):
    empty_dir = tmp_path / "empty-assets"
    empty_dir.mkdir()
    changes = {"prompts_dir" if missing == "prompt" else "skills_dir": empty_dir}
    context = replace(ctx, settings=replace(ctx.settings, paths=replace(ctx.settings.paths, **changes)), artifacts={"source_pdf": tmp_path / "also-absent.pdf"})
    with pytest.raises(AssetMissing) as caught:
        recognize.run(context, {})
    assert exit_code_for(caught.value) == 8
    assert not fake.providers and not fake.requests
    assert not list(context.run_dir.glob("input/page_*.png"))
    assert not (context.run_dir / "recognition.json").exists()
    assert not (context.run_dir / "validation.json").exists()
    record = _stage_record(context, "recognize")
    assert record["status"] == "failed"
    assert any(issue["code"] == "ASSET_MISSING" for issue in record["issues"])


def test_png_count_mismatch_fails_before_model(ctx, fake, monkeypatch):
    def mismatched_pdf(path, *, run_dir):
        assert path == ctx.artifacts["source_pdf"] and run_dir == ctx.run_dir
        image = run_dir / "input" / "page_0001.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"engineering-count-fault")
        pages = [PageInfo(n, 72, 72, 0, False, image if n == 1 else None) for n in (1, 2)]
        return PdfInfo(hashlib.sha256(path.read_bytes()).hexdigest(), 2, path.stat().st_size, False, pages)
    monkeypatch.setattr(recognize, "load_pdf", mismatched_pdf)
    with pytest.raises(PdfRenderFailed) as caught:
        recognize.run(ctx, {})
    assert exit_code_for(caught.value) == 3
    assert "2" in str(caught.value) and "1" in str(caught.value)
    assert not fake.providers and not fake.requests
    assert _stage_record(ctx, "recognize")["status"] == "failed"


def test_schema_failure_keeps_evidence_and_stops_next_stage(ctx, fake):
    fake.outputs["normalize"] = {"document_id": "MODEL-RAW-MUST-NOT-ENTER-ERROR"}
    with pytest.raises(PipelineError) as caught:
        orchestrator.run_stages(ctx, "all")
    assert caught.value.code == Code.SCHEMA_INVALID and exit_code_for(caught.value) == 4
    assert "MODEL-RAW-MUST-NOT-ENTER-ERROR" not in str(caught.value)
    assert _json(ctx.run_dir / "document_ir.json") == fake.outputs["normalize"]
    assert not (ctx.run_dir / "evidence.json").exists()
    assert not (ctx.run_dir / "validation.json").exists()
    assert [request.stage for request in fake.requests] == ["recognize", "normalize"]
    records = _json(ctx.run_dir / "manifest.json")["stages"]
    assert [(row["name"], row["status"]) for row in records] == [("recognize", "ok"), ("normalize", "failed")]
    assert records[1]["artifacts"] == {"document_ir": "document_ir.json"}
    assert any(issue["code"] == "SCHEMA_INVALID" and issue["severity"] == "error" for issue in records[1]["issues"])
    assert all(provider.closed for provider in fake.providers)


def test_run_all_uses_dependency_order_and_independent_providers(ctx, fake):
    outputs = orchestrator.run_stages(ctx, "all")
    assert [output.name for output in outputs] == ["recognize", "normalize", "evidence"]
    assert [request.stage for request in fake.requests] == ["recognize", "normalize", "evidence"]
    assert len(fake.providers) == 3 and all(provider.closed for provider in fake.providers)
    for output in outputs:
        assert output.status == "ok" and output.duration_ms >= 0
        assert all(path.is_file() for path in output.artifacts.values())
        assert _json(ctx.run_dir / f"{ARTIFACTS[output.name]}.json") == fake.outputs[output.name]
    for request, input_name in zip(fake.requests[1:], ("recognition", "document_ir")):
        assert request.images is None and request.skill is None
        assert request.payload == {input_name: _json(ctx.run_dir / f"{input_name}.json")}
        assert request.json_schema == load_schema(ARTIFACTS[request.stage])
        assert request.prompt_text == (STUB_PROMPTS / f"{request.stage}.md").read_text(encoding="utf-8")
    manifest = _json(ctx.run_dir / "manifest.json")
    assert set(manifest) == set(create_run_manifest(ctx.run_id, ctx.material_id, ctx.settings))
    assert manifest["assets"] == {"prompts": {}, "skills": {}, "schemas": {}}
    assert len(manifest["stages"]) == 3
    for record in manifest["stages"]:
        assert set(record) == {"name", "status", "started_at", "ended_at", "duration_ms", "artifacts", "issues", "model_calls"}
        assert record["status"] == "ok" and record["duration_ms"] >= 0
        assert record["started_at"] and record["ended_at"]
        assert record["model_calls"][0]["attempts"] == 1
        assert record["model_calls"][0]["prompt_version"] == "0.0.1-stub"
    assert not (ctx.run_dir / "validation.json").exists()


@pytest.mark.parametrize("module", (normalize, evidence), ids=lambda module: module.STAGE_NAME)
def test_text_stage_reads_disk_each_time_and_missing_input_fails(ctx, fake, module):
    path = _write_input(ctx, module.STAGE_NAME)
    first = module.run(ctx, {})
    assert first.status == "ok"
    changed = _json(path)
    if module is normalize:
        changed["raw_text"] = "磁盘上的新文本"
    else:
        changed["document_id"] = "changed-on-disk"
    path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    module.run(ctx, {})
    assert fake.requests[-1].payload == {path.stem: changed}
    assert fake.requests[-1].payload != fake.requests[-2].payload
    path.unlink()
    count = len(fake.requests)
    with pytest.raises(AssetMissing) as caught:
        module.run(ctx, {})
    assert exit_code_for(caught.value) == 8 and len(fake.requests) == count
    assert len(_json(ctx.run_dir / "manifest.json")["stages"]) == 1
    assert _stage_record(ctx, module.STAGE_NAME)["status"] == "failed"


@pytest.mark.parametrize("content", [b"not-json-MODEL-TEXT", b"\xff\xfeMODEL-TEXT"])
@pytest.mark.parametrize("module", (normalize, evidence), ids=lambda module: module.STAGE_NAME)
def test_malformed_input_json_fails_before_provider(ctx, fake, module, content):
    path = _write_input(ctx, module.STAGE_NAME)
    path.write_bytes(content)
    with pytest.raises(PipelineError) as caught:
        module.run(ctx, {})
    assert caught.value.code == Code.SCHEMA_INVALID and exit_code_for(caught.value) == 4
    assert "MODEL-TEXT" not in str(caught.value)
    assert not fake.providers
    assert _stage_record(ctx, module.STAGE_NAME)["status"] == "failed"


def test_prompt_directory_replacement_requires_zero_code_changes(ctx, fake, tmp_path):
    functions_before = tuple(module.run for module in MODULES)
    orchestrator.run_stages(ctx, "all")
    original = list(fake.requests)
    replacement = tmp_path / "replacement-prompts"
    replacement.mkdir()
    for name in ("vision", "normalize", "evidence"):
        (replacement / f"{name}.md").write_text(
            f"<!-- version: 0.0.2-stub -->\n仅供工程测试：另一份 {name} 文案。\n", encoding="utf-8")
    context = replace(ctx, settings=replace(ctx.settings, paths=replace(ctx.settings.paths, prompts_dir=replacement)))
    outputs = orchestrator.run_stages(context, "all")
    assert all(output.status == "ok" for output in outputs)
    assert tuple(module.run for module in MODULES) == functions_before
    for before, after in zip(original, fake.requests[3:]):
        expected_bytes = (replacement / f"{after.prompt_name}.md").read_bytes()
        expected_text = expected_bytes.decode("utf-8")
        assert before.prompt_text != after.prompt_text
        assert after.prompt_text == expected_text
        assert replace(before, prompt_text=after.prompt_text) == after
        call = _stage_record(context, after.stage)["model_calls"][0]
        assert call["prompt_sha256"] == hashlib.sha256(expected_bytes).hexdigest()
        assert call["prompt_version"] == "0.0.2-stub"
    assert len(_json(context.run_dir / "manifest.json")["stages"]) == 3


@pytest.mark.parametrize("module", MODULES, ids=lambda module: module.STAGE_NAME)
@pytest.mark.parametrize(("error_type", "expected_exit"), [
    (ModelTimeout, 5), (TruncatedResponse, 5), (InvalidModelOutput, 5),
    (ModelRefused, 5), (AuthMissing, 2),
])
def test_model_failures_propagate_unchanged_and_close(ctx, fake, module, error_type, expected_exit):
    failure = error_type("Injected model boundary failure")
    fake.outputs[module.STAGE_NAME] = failure
    if module is not recognize:
        _write_input(ctx, module.STAGE_NAME)
    with pytest.raises(error_type) as caught:
        module.run(ctx, {})
    assert caught.value is failure
    assert exit_code_for(caught.value) == expected_exit
    assert len(fake.providers) == 1 and fake.providers[0].closed
    assert not (ctx.run_dir / f"{ARTIFACTS[module.STAGE_NAME]}.json").exists()
    assert not (ctx.run_dir / "validation.json").exists()
    assert _stage_record(ctx, module.STAGE_NAME)["status"] == "failed"


def test_manifest_removes_urls_credentials_and_absolute_paths(ctx, fake):
    orchestrator.run_stages(ctx, "all")
    text = (ctx.run_dir / "manifest.json").read_text(encoding="utf-8")
    assert "https://" not in text and "sk-" not in text
    assert ctx.settings.vision.base_url not in text
    assert "hidden" not in text and "token=" not in text
    for path in (ctx.run_dir, ctx.artifacts["source_pdf"], ctx.settings.paths.prompts_dir, ctx.settings.paths.skills_dir, ctx.settings.paths.fixtures_dir):
        assert str(path) not in text and path.as_posix() not in text
    manifest = json.loads(text)
    assert manifest["config"]["vision.base_url"]["value"] == "example.test"
    for name in ("result_dir", "prompts_dir", "skills_dir", "fixtures_dir"):
        assert manifest["config"][f"paths.{name}"]["value"] == getattr(ctx.settings.paths, name).name


def test_write_json_atomic_has_exact_encoding_and_preserves_order(tmp_path):
    path = tmp_path / "nested" / "artifact.json"
    payload = {"z": "工程测试", "a": [2, 1]}
    _shared.write_json_atomic(path, payload)
    expected = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    assert path.read_bytes() == expected


def test_atomic_replace_failure_is_explicit_and_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "artifact.json"
    path.write_bytes(b"previous-evidence\n")
    def fail_replace(*args, **kwargs):
        raise OSError("injected replace failure")
    monkeypatch.setattr(_shared.os, "replace", fail_replace)
    with pytest.raises(IoFailure) as caught:
        _shared.write_json_atomic(path, {"value": 2})
    assert exit_code_for(caught.value) == 7
    assert path.read_bytes() == b"previous-evidence\n"


def test_invalid_utf8_prompt_fails_explicitly(tmp_path):
    (tmp_path / "vision.md").write_bytes(b"\xff\xfeINVALID-ASSET-CONTENT")
    with pytest.raises(IoFailure) as caught:
        load_prompt("vision", tmp_path)
    assert exit_code_for(caught.value) == 7
    assert "INVALID-ASSET-CONTENT" not in str(caught.value)


def test_prompt_names_cannot_escape_configured_directory(tmp_path):
    directory = tmp_path / "prompts"
    directory.mkdir()
    (tmp_path / "vision.md").write_text("仅供工程测试。\n", encoding="utf-8")
    with pytest.raises(AssetMissing):
        load_prompt("../vision", directory)
    with pytest.raises(AssetMissing):
        load_prompt("vision", directory)


def test_relative_run_directory_records_relative_artifacts(ctx, fake, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    context = replace(ctx, run_dir=Path("relative-run"))
    outputs = orchestrator.run_stages(context, "all")
    assert all(output.status == "ok" for output in outputs)
    records = _json(context.run_dir / "manifest.json")["stages"]
    assert [record["artifacts"] for record in records] == [
        {"recognition": "recognition.json"},
        {"document_ir": "document_ir.json"},
        {"evidence": "evidence.json"},
    ]
    assert not (tmp_path / "input").exists()


@pytest.mark.parametrize("private_path", ["C:/private/config/prompts", "/private/config/prompts", "//server/share/private/prompts"])
def test_record_stage_sanitizes_path_flavors_and_updates_in_place(ctx, private_path):
    context = replace(ctx, settings=replace(ctx.settings, paths=replace(ctx.settings.paths, prompts_dir=Path(private_path))))
    _shared.record_stage(context, "normalize", "running", "2026-11-14T00:00:00Z", None, 0.0, {}, [], [])
    _shared.record_stage(context, "normalize", "failed", "2026-11-14T00:00:00Z", "2026-11-14T00:00:01Z", 1000.0, {}, [], [])
    manifest = _json(context.run_dir / "manifest.json")
    assert manifest["config"]["paths.prompts_dir"]["value"] == "prompts"
    assert len(manifest["stages"]) == 1
    assert manifest["stages"][0]["status"] == "failed"
    assert manifest["stages"][0]["duration_ms"] == 1000.0


@pytest.mark.parametrize("module", MODULES, ids=lambda module: module.STAGE_NAME)
def test_close_failure_does_not_replace_primary_model_error(ctx, fake, module):
    failure = ModelTimeout("primary timeout")
    fake.outputs[module.STAGE_NAME] = failure
    fake.close_error = OSError("secondary close failure")
    if module is not recognize:
        _write_input(ctx, module.STAGE_NAME)
    with pytest.raises(ModelTimeout) as caught:
        module.run(ctx, {})
    assert caught.value is failure and exit_code_for(caught.value) == 5
    assert fake.providers[0].closed
    record = _stage_record(ctx, module.STAGE_NAME)
    assert record["status"] == "failed"
    assert record["model_calls"][0]["attempts"] is None


def test_corrupt_manifest_fails_without_replacing_existing_evidence(ctx, fake):
    ctx.run_dir.mkdir(parents=True)
    path = ctx.run_dir / "manifest.json"
    path.write_bytes(b"{invalid-manifest")
    with pytest.raises(IoFailure) as caught:
        normalize.run(ctx, {})
    assert exit_code_for(caught.value) == 7
    assert path.read_bytes() == b"{invalid-manifest"
    assert not fake.providers
