"""Run the real offline chain and check replay failures without an SDK or network."""

from __future__ import annotations

import hashlib
import json
import re
import socket
from pathlib import Path

import pytest
from pypdf import PdfWriter

import main
from pipeline.codex_adapter import CodexAdapter
from pipeline.contracts import ModelRequest
from pipeline.errors import AssetMissing, ConfigError, PipelineError, exit_code_for
from pipeline.prompt_loader import load_prompt
from pipeline.replay_provider import ReplayProvider
from pipeline.schema_loader import validate_instance
from pipeline.settings import load_settings
from pipeline.stages.recognize import _visual_text
from pipeline.vision_provider import get_provider


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "demo"
OUTPUTS = FIXTURES / "model_outputs"
BANNER = "模型输出为回放桩，仅验证链路，不代表识别质量"
STAGES = ["recognize", "normalize", "evidence", "write", "repair"]


def _request(name, payload):
    return ModelRequest(stage="recognize", prompt_name=name, prompt_text="", payload=payload)


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_demo_runs_real_stages_without_codex_or_network(tmp_path, monkeypatch, capsys):
    forbidden_calls = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError("Offline demo attempted Codex construction or network access")

    monkeypatch.setattr(CodexAdapter, "__init__", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    # CLI overrides must keep the demo offline even with a production provider in the environment.
    monkeypatch.setenv("VISION_PROVIDER", "openai_codex")
    pdf = tmp_path / "scan.pdf"
    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(width=72, height=72)
    writer.write(pdf)

    code = main.main(["demo", "--pdf", str(pdf), "--material-id", "DEMO-CUSTOM",
                      "--output", str(tmp_path / "runs")])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert captured.err.strip() == BANNER
    assert not forbidden_calls
    assert set(payload) == {"run_id", "run_dir", "stages", "valid", "han_chars", "issues"}
    assert payload["valid"] is True and payload["issues"] == []
    assert 2500 <= payload["han_chars"] <= 4000
    assert [stage["name"] for stage in payload["stages"]] == STAGES
    assert all(stage["status"] == "ok" for stage in payload["stages"])
    run = Path(payload["run_dir"])
    assert run == tmp_path / "runs" / "DEMO-CUSTOM" / payload["run_id"]
    validation = _json(run / "validation.json")
    assert validation["valid"] is True and validation["checks"]["skipped"] == []
    assert validation["stats"]["han_chars"] == payload["han_chars"]
    assert (run / "final.md").read_text(encoding="utf-8").splitlines()[0] == "<!-- material_id: DEMO-CUSTOM -->"
    assert _json(run / "evidence.json")["material_id"] == "DEMO-CUSTOM"
    recognition = _json(run / "recognition.json")
    assert [page["page_number"] for page in recognition["pages"]] == [1, 2]
    assert all(page["blocks"][0]["text"] == "仅用于离线回放的合成文本。" for page in recognition["pages"])
    manifest = _json(run / "manifest.json")
    assert [stage["name"] for stage in manifest["stages"]] == STAGES
    assert len(manifest["stages"][0]["model_calls"]) == 2
    assert all(call["prompt_version"] == "0.0.0" for stage in manifest["stages"]
               for call in stage["model_calls"])
    assert len(list((run / "input").glob("page_*.png"))) == 2
    assert (run / "logs").is_dir() and (run / "raw").is_dir()


def test_replay_hash_uses_canonical_original_payload_and_complete_result(tmp_path):
    (tmp_path / "sample.json").write_text('{"material_id":"{{material_id}}"}', encoding="utf-8")
    provider = ReplayProvider(tmp_path)
    payload = {"other": {"b": 2, "a": "中文"}, "material_id": "DEMO-HASH"}
    result = provider.generate(_request("sample", payload))
    reordered = provider.generate(_request("sample", {"material_id": "DEMO-HASH", "other": {"a": "中文", "b": 2}}))
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert result.request_hash == reordered.request_hash == hashlib.sha256(normalized).hexdigest()
    assert result.parsed == {"material_id": "DEMO-HASH"}
    assert result.model == "replay" and result.turn_status == "completed"
    assert result.attempts == 1 and result.latency_ms == 0.0
    assert result.usage == {} and result.raw == {"replay": True}
    assert result.truncated is False and result.error_message is None
    provider.close()


@pytest.mark.parametrize("payload, expected", [
    ({"page_number": 2, "pages": [{"page_number": 1}]}, 2),
    ({"pages": [{"page_number": 3}]}, 3),
])
def test_page_placeholder_uses_explicit_number_then_first_page(payload, expected):
    result = ReplayProvider(OUTPUTS).generate(_request("vision", payload))
    assert result.parsed["pages"][0]["page_number"] == expected


@pytest.mark.parametrize("name", ["vision", "formula", "figure", "table"])
def test_visual_replays_satisfy_schema_and_text_consumer(name):
    result = ReplayProvider(OUTPUTS).generate(_request(name, {"page_number": 2}))
    assert validate_instance("recognition" if name == "vision" else name, result.parsed) == []
    visible = _visual_text(name, result.parsed)
    assert visible.strip() and not re.search(r"\d", visible)


def test_markdown_replay_binds_run_material_without_a_payload_identifier():
    result = ReplayProvider(OUTPUTS, material_id="DEMO-BOUND").generate(_request("translate", {"draft_en": "sample"}))
    assert result.parsed is None
    assert result.text.splitlines()[0] == "<!-- material_id: DEMO-BOUND -->"


@pytest.mark.parametrize("text,payload", [
    ("{{material_id}}", {}),
    ("{{material_id}}", {"material_id": ""}),
    ("{{material_id}}", {"material_id": None}),
    ("{{page_number}}", {}),
    ("{{page_number}}", {"pages": []}),
    ("{{page_number}}", {"pages": [{}]}),
    ("{{page_number}}", {"pages": None}),
    ("{{page_number}}", {"page_number": None, "pages": [{"page_number": 1}]}),
    ("{{page_number}}", {"page_number": True}),
    ("{{page_number}}", {"page_number": 0}),
    ("{{unknown}}", {}),
    ("{{broken", {}),
    ("   \n", {}),
])
def test_unresolved_or_empty_replay_fails_closed(tmp_path, text, payload):
    (tmp_path / "sample.md").write_text(text, encoding="utf-8")
    with pytest.raises(PipelineError) as caught:
        ReplayProvider(tmp_path).generate(_request("sample", payload))
    assert caught.value.code == "STAGE_FAILED"
    assert exit_code_for(caught.value) == 1


def test_missing_replay_is_asset_missing(tmp_path):
    with pytest.raises(AssetMissing) as caught:
        ReplayProvider(tmp_path).generate(_request("absent", {}))
    assert exit_code_for(caught.value) == 8


def test_bad_json_does_not_fall_back_to_markdown(tmp_path):
    (tmp_path / "sample.json").write_text("{invalid", encoding="utf-8")
    (tmp_path / "sample.md").write_text("must not be used", encoding="utf-8")
    with pytest.raises(PipelineError) as caught:
        ReplayProvider(tmp_path).generate(_request("sample", {}))
    assert caught.value.code == "STAGE_FAILED"


def test_replay_provider_uses_settings_root_and_manifest_material(tmp_path):
    (tmp_path / "manifest.json").write_text('{"material_id":"DEMO-FACTORY"}', encoding="utf-8")
    settings = load_settings(cli_overrides={"paths.fixtures_dir": OUTPUTS}, require_secret=False)
    result = get_provider("replay", settings=settings, run_dir=tmp_path).generate(_request("evidence", {"document_ir": {}}))
    assert result.parsed["material_id"] == "DEMO-FACTORY"
    assert [fact["fact_id"] for fact in result.parsed["facts"]] == ["F001"]
    with pytest.raises(ConfigError):
        get_provider("unknown-offline-provider", settings=settings)


def test_demo_assets_have_exact_versions_and_safe_prose():
    for name in ("vision", "normalize", "evidence", "writer", "translate", "repair"):
        path = FIXTURES / "prompts" / f"{name}.md"
        assert path.read_bytes().splitlines()[0] == b"<!-- version: 0.0.0 -->"
        assert load_prompt(name, FIXTURES / "prompts").version == "0.0.0"
    skill = (FIXTURES / "skills" / "pdf-vision" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.splitlines()[0] == "<!-- version: 0.0.0 -->" and "不参与真实识别" in skill
    for path in OUTPUTS.iterdir():
        text = path.read_text(encoding="utf-8")
        assert "sk-" not in text and not re.search(r"[A-Za-z]:[\\/]|/Users/|/home/", text)
        if path.suffix == ".md":
            prose = re.sub(r"<!--[\s\S]*?-->|\[F001\]", "", text)
            assert not re.search(r"\d", prose)
