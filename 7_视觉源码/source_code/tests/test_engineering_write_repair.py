"""A11 offline contracts: real stages, I/O and validator; only providers are fake.

Final-read tracing delegates every read to pathlib. The final-write fault test
corrupts an artifact deliberately, while the real validator decides failure.
No production prompts, schemas, settings or validator rules are modified.
"""

import difflib
import hashlib
import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest
from pypdf import PdfWriter

from pipeline import orchestrator, repair, writer
from pipeline.contracts import ModelResult, RunContext
from pipeline.errors import Code, ModelTimeout, PipelineError, exit_code_for
from pipeline.settings import load_settings
from pipeline.stages import evidence, normalize, recognize

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "engineering"
STUB_PROMPTS = FIXTURES / "stub_prompts"
SHORT = "<!-- material_id: run_ok -->\n# 工程验证\n\n## 引言\n简述[F001]。\n\n## 正文\n短稿。\n\n## 总结\n总结。\n\n## 来源\n合成证据。\n"
LONG = SHORT.replace("短稿。", "工程测试合成正文" * 380)
FAKE_KEY = "sk-offline-write-repair-only"


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _prepare(ctx, text):
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    for name in ("recognition", "document_ir", "evidence"):
        (ctx.run_dir / f"{name}.json").write_bytes((FIXTURES / "run_ok" / f"{name}.json").read_bytes())
    (ctx.run_dir / "draft.md").write_text(text, encoding="utf-8", newline="\n")


def _run_repair(ctx):
    return repair.repair_draft(ctx.run_dir / "draft.md", ctx.run_dir / "validation.json", ctx)


def _record(ctx, name):
    return next(row for row in _json(ctx.run_dir / "manifest.json")["stages"] if row["name"] == name)


def _evidence_listing(ctx, label):
    print(label)
    print(sorted(p.relative_to(ctx.run_dir).as_posix() for p in ctx.run_dir.rglob("*") if p.is_file()))
    print("final_exists =", (ctx.run_dir / "final.md").is_file())


@pytest.fixture
def ctx(tmp_path):
    pdf = tmp_path / "engineering.pdf"
    document = PdfWriter()
    document.add_blank_page(width=72, height=72)
    with pdf.open("wb") as stream:
        document.write(stream)
    settings = load_settings(cli_overrides={
        "vision.provider": "openai_codex", "vision.model": "offline-engineering-model",
        "vision.base_url": "https://example.test/v1", "vision.api_key": FAKE_KEY,
        "paths.prompts_dir": str(STUB_PROMPTS.resolve()),
        "paths.skills_dir": str(ROOT / "skills"),
        "paths.result_dir": str(tmp_path / "runs"),
    }, require_secret=False)
    return RunContext("write-repair-run", "run_ok", tmp_path / "run", settings, {"source_pdf": pdf})


class FakeProvider:
    def __init__(self, factory):
        self.factory = factory
        self.closed = False

    def generate(self, request):
        assert not self.closed
        self.factory.requests.append(request)
        if request.stage in {"write", "repair"}:
            assert request.json_schema is None
            text = (self.factory.write_text if request.stage == "write"
                    else self.factory.revisions.pop(0))
            if isinstance(text, Exception):
                raise text
            parsed = None
        else:
            if request.stage == "recognize":
                # Page-batched: answer exactly the pages this request carried.
                parsed = {"pages": [{"page_number": page["page_number"], "blocks": []}
                                    for page in request.payload["pages"]]}
            else:
                artifact = {"normalize": "document_ir", "evidence": "evidence"}[request.stage]
                parsed = _json(FIXTURES / "run_ok" / f"{artifact}.json")
            text = json.dumps(parsed, ensure_ascii=False)
        return ModelResult(text=text, parsed=parsed, raw={}, usage={}, truncated=False,
                           latency_ms=1.0, attempts=1,
                           request_hash=hashlib.sha256(request.stage.encode()).hexdigest(),
                           model="offline-engineering-model", turn_status="completed")

    def close(self):
        assert not self.closed
        self.closed = True


class FakeFactory:
    def __init__(self):
        self.requests = []
        self.providers = []
        self.write_text = SHORT
        self.revisions = [LONG]

    def __call__(self, name, **options):
        assert name == "openai_codex"
        provider = FakeProvider(self)
        self.providers.append(provider)
        return provider


@pytest.fixture
def fake(monkeypatch):
    factory = FakeFactory()
    for module in (recognize, normalize, evidence, writer, repair):
        monkeypatch.setattr(module, "get_provider", factory)
    monkeypatch.setattr(orchestrator, "STAGE_REGISTRY", {})
    return factory


@pytest.fixture
def validation_reads(monkeypatch):
    reads = []
    original = Path.read_bytes

    def traced(path):
        if path.name in {"draft.md", "final.md"}:
            reads.append((path.name, path.is_file()))
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", traced)
    return reads


def _assert_valid_final(ctx, validation_reads):
    report = _json(ctx.run_dir / "validation.json")
    assert report["valid"] is True and report["issues"] == []
    assert validation_reads[-1] == ("final.md", True)
    # I6 has no top-level artifact path. Trace the real final-file read instead.
    assert "path" not in report
    assert set(report["checks"]["skipped"]) == set()


def test_writer_reads_only_evidence_and_writes_plain_lf(ctx, fake):
    ctx.run_dir.mkdir()
    source = ctx.run_dir / "evidence.json"
    source.write_bytes((FIXTURES / "run_ok" / "evidence.json").read_bytes())
    fake.write_text = LONG.replace("\n", "\r\n")
    result = writer.write_draft(source, ctx)
    assert list(inspect.signature(writer._draft_payload).parameters) == ["evidence"]
    draft_request, translate_request = fake.requests
    assert draft_request.payload == {"evidence": _json(source)}
    assert not {"recognition", "source_pdf"}.intersection(draft_request.payload)
    assert draft_request.images is None and draft_request.skill is None and draft_request.json_schema is None
    assert draft_request.prompt_name == "writer"
    assert draft_request.prompt_text == (STUB_PROMPTS / "writer.md").read_text(encoding="utf-8")
    assert translate_request.payload == {"draft_en": LONG}
    assert translate_request.prompt_name == "translate"
    assert translate_request.prompt_text == (STUB_PROMPTS / "translate.md").read_text(encoding="utf-8")
    assert result.name == "write" and result.status == "ok"
    assert result.artifacts == {"draft_en": ctx.run_dir / "draft_en.md", "draft": ctx.run_dir / "draft.md"}
    assert result.artifacts["draft_en"].read_bytes() == LONG.encode("utf-8")
    assert result.artifacts["draft"].read_bytes() == LONG.encode("utf-8")
    assert not (ctx.run_dir / "recognition.json").exists()
    assert len(fake.providers) == 2 and all(provider.closed for provider in fake.providers)
    assert len(_json(ctx.run_dir / "manifest.json")["stages"]) == 1


@pytest.mark.parametrize("content,code", [(None, Code.ASSET_MISSING), ("", Code.ASSET_EMPTY), (" \n\t", Code.ASSET_EMPTY)])
def test_writer_missing_or_blank_prompt_fails_before_provider(ctx, fake, tmp_path, content, code):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    if content is not None:
        (prompts / "writer.md").write_text(content, encoding="utf-8")
    ctx = replace(ctx, settings=replace(ctx.settings, paths=replace(ctx.settings.paths, prompts_dir=prompts)))
    with pytest.raises(PipelineError) as caught:
        writer.write_draft(ctx.run_dir / "evidence.json", ctx)
    assert caught.value.code == code and exit_code_for(caught.value) == 8
    assert fake.providers == [] and not (ctx.run_dir / "draft.md").exists()
    assert _record(ctx, "write")["status"] == "failed"


def test_translate_prompt_missing_stops_after_english_draft(ctx, fake, tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "writer.md").write_text((STUB_PROMPTS / "writer.md").read_text(encoding="utf-8"), encoding="utf-8")
    ctx = replace(ctx, settings=replace(ctx.settings, paths=replace(ctx.settings.paths, prompts_dir=prompts)))
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    source = ctx.run_dir / "evidence.json"
    source.write_bytes((FIXTURES / "run_ok" / "evidence.json").read_bytes())
    with pytest.raises(PipelineError) as caught:
        writer.write_draft(source, ctx)
    assert caught.value.code == Code.ASSET_MISSING and exit_code_for(caught.value) == 8
    assert (ctx.run_dir / "draft_en.md").is_file()
    assert not (ctx.run_dir / "draft.md").exists()
    assert len(fake.providers) == 1 and fake.providers[0].closed
    assert _record(ctx, "write")["status"] == "failed"


def test_unversioned_writer_warns_without_failing(ctx, fake, tmp_path):
    _prepare(ctx, SHORT)
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "writer.md").write_text("Engineering test prompt.\n", encoding="utf-8")
    (prompts / "translate.md").write_text((STUB_PROMPTS / "translate.md").read_text(encoding="utf-8"), encoding="utf-8")
    ctx = replace(ctx, settings=replace(ctx.settings, paths=replace(ctx.settings.paths, prompts_dir=prompts)))
    result = writer.write_draft(ctx.run_dir / "evidence.json", ctx)
    assert result.status == "ok"
    assert any(issue.code == Code.ASSET_NO_VERSION and issue.severity == "warning" for issue in result.issues)


def test_zero_rounds_publish_and_validate_final_without_prompt(ctx, fake, tmp_path, validation_reads):
    _prepare(ctx, LONG)
    ctx = replace(ctx, settings=replace(ctx.settings, paths=replace(ctx.settings.paths, prompts_dir=tmp_path / "absent")))
    result = _run_repair(ctx)
    assert result.artifacts == {"final": ctx.run_dir / "final.md"}
    assert (ctx.run_dir / "final.md").read_text(encoding="utf-8") == LONG
    assert fake.providers == [] and not list(ctx.run_dir.glob("raw/repair_*"))
    assert _record(ctx, "repair")["model_calls"] == []
    _assert_valid_final(ctx, validation_reads)
    _evidence_listing(ctx, "zero rounds")


def test_one_round_keeps_request_diff_and_revalidates_final(ctx, fake, validation_reads):
    _prepare(ctx, SHORT)
    fake.revisions = [LONG.replace("\n", "\r\n")]
    result = _run_repair(ctx)
    assert result.status == "ok" and (ctx.run_dir / "final.md").read_text(encoding="utf-8") == LONG
    assert (ctx.run_dir / "draft.md").read_text(encoding="utf-8") == LONG
    request, = fake.requests
    assert request.payload["draft"] == SHORT
    assert set(request.payload) == {"draft", "issues", "constraints"}
    assert request.payload["constraints"] == {"only_codes": ["MD_BODY_TOO_SHORT"],
                                               "preserve_unlisted_text": True, "no_new_facts": True}
    assert all(set(issue) == {"code", "path", "message", "hint"} for issue in request.payload["issues"])
    assert request.images is None and request.skill is None and request.json_schema is None
    audit = _json(ctx.run_dir / "raw" / "repair_1_request.json")
    assert set(audit) == {"stage", "attempt", "prompt_name", "prompt_sha256", "prompt_version",
                          "codes", "model", "attempts", "request_hash"}
    assert audit["stage"] == "repair" and audit["attempt"] == audit["attempts"] == 1
    assert audit["codes"] == ["MD_BODY_TOO_SHORT"] and audit["prompt_version"] == "0.0.1-stub"
    assert audit["prompt_sha256"] == hashlib.sha256((STUB_PROMPTS / "repair.md").read_bytes()).hexdigest()
    assert audit["request_hash"] == hashlib.sha256(b"repair").hexdigest()
    expected_diff = "\n".join(difflib.unified_diff(SHORT.splitlines(), LONG.splitlines(),
                             fromfile="draft.md (before repair 1)", tofile="draft.md (after repair 1)", lineterm="")) + "\n"
    assert (ctx.run_dir / "raw" / "repair_1_diff.md").read_bytes() == expected_diff.encode()
    assert len(fake.providers) == 1 and fake.providers[0].closed
    assert len(_record(ctx, "repair")["model_calls"]) == 1
    _assert_valid_final(ctx, validation_reads)
    _evidence_listing(ctx, "one round")


def test_exhaustion_uses_configured_three_rounds_and_preserves_draft(ctx, fake):
    _prepare(ctx, SHORT)
    ctx = replace(ctx, settings=replace(ctx.settings, pipeline=replace(ctx.settings.pipeline, max_repair_attempts=3)))
    revisions = [SHORT, SHORT.replace("短稿", "仍是短稿"), SHORT.replace("短稿", "最后短稿")]
    fake.revisions = list(revisions)
    with pytest.raises(PipelineError) as caught:
        _run_repair(ctx)
    assert caught.value.code == Code.REPAIR_EXHAUSTED and exit_code_for(caught.value) == 6
    assert len(fake.providers) == 3 and all(p.closed for p in fake.providers)
    assert [request.payload["draft"] for request in fake.requests] == [SHORT, revisions[0], revisions[1]]
    assert (ctx.run_dir / "draft.md").read_text(encoding="utf-8") == revisions[-1]
    assert not (ctx.run_dir / "final.md").exists()
    assert len(list((ctx.run_dir / "raw").iterdir())) == 6
    assert (ctx.run_dir / "raw" / "repair_1_diff.md").read_bytes() == b""
    for attempt in range(1, 4):
        assert _json(ctx.run_dir / "raw" / f"repair_{attempt}_request.json")["attempt"] == attempt
        assert (ctx.run_dir / "raw" / f"repair_{attempt}_diff.md").is_file()
    assert _json(ctx.run_dir / "validation.json")["valid"] is False
    assert len(_record(ctx, "repair")["model_calls"]) == 3
    assert _record(ctx, "repair")["status"] == "failed"
    _evidence_listing(ctx, "three rounds exhausted")


def test_zero_budget_fails_without_prompt_or_raw(ctx, fake, tmp_path):
    _prepare(ctx, SHORT)
    settings = replace(ctx.settings, pipeline=replace(ctx.settings.pipeline, max_repair_attempts=0),
                       paths=replace(ctx.settings.paths, prompts_dir=tmp_path / "absent"))
    with pytest.raises(PipelineError) as caught:
        _run_repair(replace(ctx, settings=settings))
    assert caught.value.code == Code.REPAIR_EXHAUSTED and exit_code_for(caught.value) == 6
    assert fake.providers == [] and not list(ctx.run_dir.glob("raw/*"))
    assert not (ctx.run_dir / "final.md").exists()


def test_added_number_keeps_audit_but_does_not_overwrite_draft(ctx, fake):
    _prepare(ctx, SHORT)
    fake.revisions = [LONG + "\n新增测量值 987654。\n"]
    with pytest.raises(PipelineError) as caught:
        _run_repair(ctx)
    assert caught.value.code == Code.REPAIR_INTRODUCED_FACT and exit_code_for(caught.value) == 6
    assert (ctx.run_dir / "draft.md").read_text(encoding="utf-8") == SHORT
    assert not (ctx.run_dir / "final.md").exists()
    assert (ctx.run_dir / "raw" / "repair_1_request.json").is_file()
    assert "987654" in (ctx.run_dir / "raw" / "repair_1_diff.md").read_text(encoding="utf-8")
    assert len(fake.providers) == 1 and fake.providers[0].closed


def test_credentials_never_enter_requests_or_artifacts(ctx, fake):
    _prepare(ctx, SHORT)
    fake.write_text = SHORT + "\n" + FAKE_KEY + "\n"
    writer.write_draft(ctx.run_dir / "evidence.json", ctx)
    fake.revisions = [LONG + "\n" + FAKE_KEY + "\n"]
    _run_repair(ctx)
    for request in fake.requests:
        assert FAKE_KEY not in request.prompt_text
        assert FAKE_KEY not in json.dumps(request.payload)
    for path in ctx.run_dir.rglob("*"):
        if path.is_file():
            assert FAKE_KEY.encode() not in path.read_bytes()
    assert "prompt_text" not in _json(ctx.run_dir / "raw" / "repair_1_request.json")


@pytest.mark.parametrize("rounds", [0, 1])
def test_all_five_stages_execute_in_order_and_close_providers(ctx, fake, rounds, validation_reads):
    fake.write_text = LONG if rounds == 0 else SHORT
    outputs = orchestrator.run_stages(ctx, "all")
    names = ["recognize", "normalize", "evidence", "write", "repair"]
    assert [output.name for output in outputs] == names
    assert all(output.status == "ok" for output in outputs)
    assert all(path.is_file() for output in outputs for path in output.artifacts.values())
    assert [request.stage for request in fake.requests] == ["recognize", "normalize", "evidence", "write", "write"] + ["repair"] * rounds
    assert len(fake.providers) == 5 + rounds and all(p.closed for p in fake.providers)
    manifest = _json(ctx.run_dir / "manifest.json")
    assert [stage["name"] for stage in manifest["stages"]] == names
    assert all(stage["status"] == "ok" for stage in manifest["stages"])
    assert len(_record(ctx, "repair")["model_calls"]) == rounds
    assert len(list(ctx.run_dir.glob("raw/*"))) == rounds * 2
    assert (ctx.run_dir / "final.md").read_text(encoding="utf-8") == LONG
    _assert_valid_final(ctx, validation_reads)
    _evidence_listing(ctx, f"five stages, repair rounds = {rounds}")


@pytest.mark.parametrize("diff,quote,accepted", [
    ("+数值 1,234 和 1.5% 与 2.75", "1234 1.5% 2.75", True),
    ("+数值 1234", "1,234", True),
    ("+数值 1234", "12345", False),
    ("+数值 1.2", "1.23", False),
    ("+数值 1.5%", "1.5", False),
    ("+1. 列表\n+2) 列表\n+# 3.2 标题\n+- 项目\n+* 项目\n++ 项目", "合成内容", True),
    (" 既有 987654\n-删除 987654\n+补充说明\n+++ draft.md (after repair 99)", "合成内容", True),
])
def test_number_guard_uses_added_lines_structure_and_numeric_boundaries(diff, quote, accepted):
    evidence_object = {"quote": quote}
    if accepted:
        repair._check_added_numbers(diff, evidence_object)
    else:
        with pytest.raises(PipelineError) as caught:
            repair._check_added_numbers(diff, evidence_object)
        assert caught.value.code == Code.REPAIR_INTRODUCED_FACT


@pytest.mark.parametrize("initial", [SHORT, LONG], ids=["one-round", "zero-round"])
def test_final_revalidation_failure_removes_final_using_real_validator(ctx, fake, monkeypatch, initial):
    _prepare(ctx, initial)
    original = repair.write_text_atomic

    def corrupt_final(path, text):
        original(path, SHORT if path.name == "final.md" else text)

    monkeypatch.setattr(repair, "write_text_atomic", corrupt_final)
    with pytest.raises(PipelineError) as caught:
        _run_repair(ctx)
    assert caught.value.code == Code.REPAIR_EXHAUSTED and exit_code_for(caught.value) == 6
    assert str(caught.value) == "Final validation failed after repair"
    assert not (ctx.run_dir / "final.md").exists()
    report = _json(ctx.run_dir / "validation.json")
    assert report["valid"] is False
    assert any(issue["path"].startswith("final.md") for issue in report["issues"])
    assert _record(ctx, "repair")["status"] == "failed"


@pytest.mark.parametrize("content,code", [(None, Code.ASSET_MISSING), (" \n", Code.ASSET_EMPTY)])
def test_repair_asset_failure_is_explicit_without_provider(ctx, fake, tmp_path, content, code):
    _prepare(ctx, SHORT)
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    if content is not None:
        (prompts / "repair.md").write_text(content, encoding="utf-8")
    ctx = replace(ctx, settings=replace(ctx.settings, paths=replace(ctx.settings.paths, prompts_dir=prompts)))
    with pytest.raises(PipelineError) as caught:
        _run_repair(ctx)
    assert caught.value.code == code and exit_code_for(caught.value) == 8
    assert fake.providers == [] and not (ctx.run_dir / "final.md").exists()


def test_model_failure_propagates_and_closes_provider(ctx, fake):
    _prepare(ctx, SHORT)
    failure = ModelTimeout("Offline timeout")
    fake.revisions = [failure]
    with pytest.raises(ModelTimeout) as caught:
        _run_repair(ctx)
    assert caught.value is failure and exit_code_for(caught.value) == 5
    assert len(fake.providers) == 1 and fake.providers[0].closed
    assert not (ctx.run_dir / "final.md").exists()
    assert _record(ctx, "repair")["status"] == "failed"
