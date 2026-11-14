"""A14b offline CLI failure evidence; only SDK and filesystem boundaries are replaced.

The production CLI, registry, providers, adapter, renderer, stages, validator and
exit-code mapping all execute. The missing run-id entry point is a strict xfail,
not a successful input-change check. Source fixtures and content assets are read-only.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import openai_codex
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import main
from pipeline.errors import Code, IoFailure, ModelTimeout, PipelineError, exit_code_for
from pipeline.settings import _ENV_ALIASES, _ENV_NAMES, load_settings
from pipeline.stages import _shared
from pipeline.validate import count_han


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "engineering"
STUB_PROMPTS = FIXTURES / "stub_prompts"
OFFLINE_KEY = "a14b-offline-placeholder"
CORE_SENTENCE = "核心事实是样本保持稳定"
RAW_FAILURE_KEYS = {
    "stage", "attempt", "turn_status", "error_type", "error_message",
    "final_response", "items", "usage", "duration_ms", "timeout_s",
}


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _hashes(path: Path):
    return {item.relative_to(path).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
            for item in path.rglob("*") if item.is_file()}


def _evidence(tmp_path: Path, case: str, expected_code, actual_code, exit_code, **details):
    payload = {"case": case, "expected_code": expected_code, "actual_code": actual_code,
               "exit_code": exit_code, **details}
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("A14B_EVIDENCE " + json.dumps({"evidence_path": str(path), **payload}, ensure_ascii=True))


@pytest.fixture
def offline(tmp_path, monkeypatch):
    # Configure the real loader; no inherited credential or dotenv is consumed.
    for name in set(_ENV_NAMES.values()) | {alias for aliases in _ENV_ALIASES.values() for alias in aliases}:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_API_KEY", OFFLINE_KEY)
    monkeypatch.chdir(tmp_path)
    config = _json(ROOT / "config.json")
    config["vision"].update(provider="openai_codex", model="offline-a14b", base_url="",
                            timeout_s=30, max_retries=0, dpi=72)
    config["pipeline"].update(max_repair_attempts=2, body_min_han=2500, body_max_han=4000)
    config["paths"].update(result_dir=str(tmp_path / "runs"), prompts_dir=str(STUB_PROMPTS),
                           skills_dir=str(ROOT / "skills"), fixtures_dir=str(FIXTURES))
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    settings = load_settings(config_path=config_path)
    assert settings.vision.api_key.get_secret_value() == OFFLINE_KEY
    assert settings.pipeline.max_repair_attempts == 2
    return SimpleNamespace(config=config_path, settings=settings, root=tmp_path)


def _pdf(path: Path, *, text=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    if text:
        font = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }))
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        prose = "Readable prose preserves all original words in the engineering document."
        commands = ["BT /F1 6 Tf 10 280 Td 10 TL"]
        commands.extend(f"({prose}) Tj T*" for _ in range(5))
        commands.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)
    return path


class _OfflineSdk:
    """The sole model substitute: the object returned by external Codex()."""

    def __init__(self, response):
        self.response = response
        self.calls = []
        self.clients = []
        self.prompts = {name: (STUB_PROMPTS / f"{name}.md").read_text(encoding="utf-8")
                        for name in ("vision", "normalize", "evidence", "writer", "translate", "repair")}

    def __call__(self, **kwargs):
        owner = self
        client = SimpleNamespace(closed=False)

        def close():
            client.closed = True

        def thread_start(**options):
            assert options["ephemeral"] is True
            assert options["sandbox"] == openai_codex.Sandbox.read_only

            def turn(inputs, **turn_options):
                text = next(item.text for item in inputs if isinstance(item, openai_codex.TextInput))
                names = [name for name, prompt in owner.prompts.items() if text.startswith(prompt)]
                assert len(names) == 1
                name = names[0]
                payload_text = text[len(owner.prompts[name]):].strip()
                payload = json.loads(payload_text) if payload_text else {}
                call = {"prompt_name": name, "payload": payload, "output_schema": turn_options.get("output_schema")}

                def run():
                    owner.calls.append(call)
                    response = owner.response(name, payload)
                    if isinstance(response, BaseException):
                        raise response
                    assert isinstance(response, str)
                    call["response"] = response
                    return SimpleNamespace(id="offline-turn", status="completed", error=None,
                                           duration_ms=1.0, usage={}, items=[{"text": response}],
                                           final_response=response, started_at=None, completed_at=None)

                return SimpleNamespace(run=run, interrupt=lambda: None)

            return SimpleNamespace(turn=turn)

        client.close = close
        client.thread_start = thread_start
        self.clients.append(client)
        return client


def _cli_run(offline, capsys, *, output_name="runs", material="fault", text_pdf=False):
    source = _pdf(offline.root / f"{output_name}.pdf", text=text_pdf)
    output = offline.root / output_name
    code = main.main(["run", str(source), "--config", str(offline.config),
                      "--output", str(output), "--material-id", material, "--stage", "all"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    candidates = list((output / material).iterdir())
    assert len(candidates) == 1
    run_dir = candidates[0]
    (run_dir / "cli-result.json").write_text(json.dumps({"exit_code": code, "output": payload},
                                                       ensure_ascii=False, indent=2), encoding="utf-8")
    return code, payload, run_dir


def _failed_recognize(run_dir, expected_code):
    manifest = _json(run_dir / "manifest.json")
    assert len(manifest["stages"]) == 1
    stage = manifest["stages"][0]
    assert stage["name"] == "recognize" and stage["status"] == "failed"
    if stage["model_calls"]:
        assert stage["model_calls"][-1]["error_code"] == expected_code
    for filename in ("recognition.json", "document_ir.json", "evidence.json", "draft_en.md", "draft.md", "final.md"):
        assert not (run_dir / filename).exists()
    return stage


def test_timeout_records_attempt_and_audit_failure_preserves_primary(offline, tmp_path, monkeypatch, capsys):
    synthetic_endpoint = "https://audit.invalid/v1"
    synthetic_url = "https://other.invalid/turn"
    windows_path = "C:/audit/private.txt"
    posix_path = "/home/a14b/private.txt"
    monkeypatch.setenv("LLM_BASE_URL", synthetic_endpoint)
    failure = TimeoutError("Synthetic SDK timeout\ncredential " + OFFLINE_KEY
                           + "\nendpoint " + synthetic_endpoint + "\nURL " + synthetic_url
                           + "\nWindows " + windows_path + "\n位置" + posix_path)
    sdk = _OfflineSdk(lambda name, payload: failure)
    monkeypatch.setattr(openai_codex, "Codex", sdk)
    code, output, run_dir = _cli_run(offline, capsys)
    assert code == 5 == exit_code_for(ModelTimeout())
    assert output["error"]["code"] == "MODEL_TIMEOUT"
    _failed_recognize(run_dir, "MODEL_TIMEOUT")
    raw_path = run_dir / "raw" / "recognize_1.json"
    raw = _json(raw_path)
    assert set(raw) == RAW_FAILURE_KEYS
    assert raw["stage"] == "recognize" and raw["attempt"] == 1
    assert raw["turn_status"] == "failed" and raw["error_type"] == "TimeoutError"
    assert raw["final_response"] is None and raw["items"] == [] and raw["usage"] == {}
    assert 0 <= raw["duration_ms"] and raw["timeout_s"] == 30
    assert isinstance(raw["error_message"], str) and len(raw["error_message"]) <= 200
    serialized = raw_path.read_text(encoding="utf-8")
    for protected in (OFFLINE_KEY, synthetic_endpoint, synthetic_url, windows_path, posix_path):
        assert protected not in serialized, "Failed attempt audit exposed synthetic protected content"
    assert len(sdk.calls) == 1 and all(client.closed for client in sdk.clients)

    # Fail only the actual audit write for a second CLI run. The original SDK
    # TimeoutError remains the primary error and receives an explanatory note.
    second_failure = TimeoutError("Synthetic SDK timeout with audit storage failure")
    sdk.response = lambda name, payload: second_failure
    original_write = Path.write_text
    blocked = []

    def fail_audit(path, *args, **kwargs):
        if path.name == "recognize_1.json" and path.parent.name == "raw" and "audit-failure" in path.parts:
            blocked.append(path)
            raise OSError("Injected audit storage failure")
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_audit)
    second_code, second_output, second_dir = _cli_run(offline, capsys, output_name="audit-failure")
    assert second_code == 5 and second_output["error"]["code"] == "MODEL_TIMEOUT"
    assert len(blocked) == 1 and not blocked[0].exists()
    assert any("audit" in note.lower() or "persist" in note.lower()
               for note in getattr(second_failure, "__notes__", []))
    _failed_recognize(second_dir, "MODEL_TIMEOUT")
    assert all(client.closed for client in sdk.clients)
    _evidence(tmp_path, "model_timeout", "MODEL_TIMEOUT", output["error"]["code"], code,
              run_dir=str(run_dir), raw_paths=[str(raw_path)], audit_write_failure={
                  "run_dir": str(second_dir), "actual_code": second_output["error"]["code"],
                  "exit_code": second_code, "original_exception_noted": True,
                  "attempted_raw_path": str(blocked[0]), "raw_exists": blocked[0].exists()})


def test_invalid_json_preserves_response_through_cli(offline, tmp_path, monkeypatch, capsys):
    truncated = '{"pages":['
    sdk = _OfflineSdk(lambda name, payload: truncated)
    monkeypatch.setattr(openai_codex, "Codex", sdk)
    code, output, run_dir = _cli_run(offline, capsys)
    assert code == 5 and output["error"]["code"] == "MODEL_INVALID_JSON"
    _failed_recognize(run_dir, "MODEL_INVALID_JSON")
    raw_path = run_dir / "raw" / "recognize_1.json"
    raw = _json(raw_path)
    assert raw["final_response"] == truncated
    assert raw["items"] == [{"text": truncated}] and raw["status"] == "completed"
    assert len(sdk.calls) == 1 and all(client.closed for client in sdk.clients)
    _evidence(tmp_path, "invalid_json", "MODEL_INVALID_JSON", output["error"]["code"], code,
              run_dir=str(run_dir), raw_paths=[str(raw_path)], original_response=truncated)


def test_fake_citation_validate_command_is_a_failed_gate(offline, tmp_path, capsys):
    source = FIXTURES / "run_ok"
    before = _hashes(source)
    run_dir = offline.settings.paths.result_dir / "run_ok" / "fake-citation"
    run_dir.parent.mkdir(parents=True)
    shutil.copytree(source, run_dir)
    final = run_dir / "final.md"
    original = final.read_text(encoding="utf-8")
    assert "[F001]" in original and "## 正文" in original
    final.write_text(original.replace("## 正文", "## 正文\n不存在的事实引用[F009]。", 1), encoding="utf-8")
    code = main.main(["validate", "--run-id", "run_ok/fake-citation"])
    output = json.loads(capsys.readouterr().out)
    actual = sorted({issue["code"] for issue in output["issues"]})
    assert code == 4 and output["valid"] is False
    assert actual == ["CITE_NOT_IN_EVIDENCE"]
    assert any(issue["code"] == "CITE_NOT_IN_EVIDENCE" and issue["severity"] == "error"
               for issue in output["issues"])
    assert _json(run_dir / "validation.json") == output
    assert _hashes(source) == before
    _evidence(tmp_path, "fake_citation", "CITE_NOT_IN_EVIDENCE", actual, code,
              run_dir=str(run_dir), raw_paths=[], validation_path=str(run_dir / "validation.json"))


@pytest.mark.xfail(strict=True, raises=SystemExit, reason="无 --run-id/断点重跑，A14b blocker")
def test_input_change_requires_missing_run_reuse_entrypoint(tmp_path, capsys):
    # Probe the actual parser. Future entry-point support produces strict XPASS,
    # requiring replacement with an implemented input-hash mismatch assertion.
    try:
        arguments = main.build_parser().parse_args(
            ["run", "changed.pdf", "--run-id", "existing-run", "--stage", "recognize"])
    finally:
        error = sys.exc_info()[1]
        captured = capsys.readouterr()
        _evidence(tmp_path, "input_change_blocker", None, None,
                  error.code if isinstance(error, SystemExit) else 0,
                  parser_error=captured.err.strip(), exception_type=type(error).__name__ if error else None,
                  raw_paths=[], blocker="无 --run-id/断点重跑，A14b blocker")
    assert arguments.run_id == "existing-run"


def _long_document():
    base = ("<!-- material_id: run_ok -->\n# 工程报告\n# 引言\n" + CORE_SENTENCE + "[F001]。\n"
            "# 正文\n正文占位。\n# 总结\n总结。\n# 来源\n[F001] page=1 block=B001 quote=核心事实是样本保持稳定\n")
    text = base.replace("正文占位", "文" * (4200 - count_han(base) + count_han("正文占位")))
    assert count_han(text) == 4200
    return text


def test_overlong_writing_exhausts_real_repair_without_losing_core(offline, tmp_path, monkeypatch, capsys):
    long_text = _long_document()
    evidence = {"material_id": "run_ok", "facts": [{"fact_id": "F001", "core": True,
                "page_number": [1], "block_id": ["B001"], "quote": CORE_SENTENCE,
                "value": None, "value_text": None, "unit": None, "region": None}]}
    structured = {
        "vision": {"pages": [{"page_number": 1, "blocks": [{"block_id": "B001", "text": CORE_SENTENCE, "region": None}]}]},
        "normalize": {"document_id": "run_ok", "source": {"path_name": None, "page_count": 1, "sha256": None},
                      "pages": [{"page_number": 1}],
                      "blocks": [{"block_id": "B001", "page_number": 1, "text": CORE_SENTENCE, "region": None}]},
        "evidence": evidence,
    }

    def response(name, payload):
        return json.dumps(structured[name], ensure_ascii=False) if name in structured else long_text

    sdk = _OfflineSdk(response)
    monkeypatch.setattr(openai_codex, "Codex", sdk)
    code, output, run_dir = _cli_run(offline, capsys, material="run_ok")
    assert code == 6 == exit_code_for(PipelineError(Code.REPAIR_EXHAUSTED))
    assert output["error"]["code"] == "REPAIR_EXHAUSTED"
    repairs = [call for call in sdk.calls if call["prompt_name"] == "repair"]
    assert len(repairs) == offline.settings.pipeline.max_repair_attempts == 2
    assert [call["prompt_name"] for call in sdk.calls] == ["vision", "normalize", "evidence", "writer", "translate", "repair", "repair"]
    for call in repairs:
        assert {issue["code"] for issue in call["payload"]["issues"]} == {"MD_BODY_TOO_LONG"}
        assert CORE_SENTENCE in call["payload"]["draft"] and "[F001]" in call["payload"]["draft"]
        assert CORE_SENTENCE in call["response"] and "[F001]" in call["response"]
        assert count_han(call["response"]) == 4200
    draft = (run_dir / "draft.md").read_text(encoding="utf-8")
    assert draft == long_text and CORE_SENTENCE in draft and "[F001]" in draft
    assert not (run_dir / "final.md").exists()
    validation = _json(run_dir / "validation.json")
    assert validation["valid"] is False and validation["stats"]["han_chars"] == 4200
    assert {issue["code"] for issue in validation["issues"]} == {"MD_BODY_TOO_LONG"}
    assert validation["stats"]["unreferenced_evidence"] == 0
    manifest = _json(run_dir / "manifest.json")
    assert [(stage["name"], stage["status"]) for stage in manifest["stages"]] == [
        ("recognize", "ok"), ("normalize", "ok"), ("evidence", "ok"), ("write", "ok"), ("repair", "failed")]
    assert len(manifest["stages"][-1]["model_calls"]) == 2
    audit_paths = []
    for attempt in range(1, len(repairs) + 1):
        request_path = run_dir / "raw" / f"repair_{attempt}_request.json"
        diff_path = run_dir / "raw" / f"repair_{attempt}_diff.md"
        assert _json(request_path)["codes"] == ["MD_BODY_TOO_LONG"]
        assert _json(request_path)["attempt"] == attempt
        assert diff_path.read_text(encoding="utf-8") == ""
        audit_paths.extend([str(request_path), str(diff_path)])
    assert all(client.closed for client in sdk.clients)
    _evidence(tmp_path, "overlong_repair", "REPAIR_EXHAUSTED", output["error"]["code"], code,
              run_dir=str(run_dir), raw_paths=audit_paths, trigger_codes=["MD_BODY_TOO_LONG"],
              repair_calls=len(repairs), max_repair_attempts=offline.settings.pipeline.max_repair_attempts,
              han_chars=validation["stats"]["han_chars"], core_sentence_and_citation_preserved=True)


def test_artifact_replace_failure_stops_real_cli_without_partial_output(offline, tmp_path, monkeypatch, capsys):
    original_replace = _shared.os.replace
    failures = []

    def fail_recognition(source, destination):
        destination = Path(destination)
        if destination.name == "recognition.json" and offline.root in destination.parents:
            failures.append(destination)
            raise OSError("Injected recognition replace failure")
        return original_replace(source, destination)

    monkeypatch.setattr(_shared.os, "replace", fail_recognition)
    code, output, run_dir = _cli_run(offline, capsys, text_pdf=True)
    assert code == 7 == exit_code_for(IoFailure())
    assert output["error"]["code"] == "IO_FAILURE"
    stage = _failed_recognize(run_dir, "IO_FAILURE")
    assert failures == [run_dir / "recognition.json"]
    assert not list(run_dir.glob(".recognition.json.*.tmp"))
    assert not list(run_dir.glob("*.tmp"))
    assert stage["model_calls"] == [] and "recognition" not in stage["artifacts"]
    assert _json(run_dir / "page_plan.json")["pages"][0]["kind"] == "text_page"
    _evidence(tmp_path, "artifact_write_failure", "IO_FAILURE", output["error"]["code"], code,
              run_dir=str(run_dir), raw_paths=[], failed_target=str(failures[0]),
              temporary_artifacts=[], subsequent_stages=[])
