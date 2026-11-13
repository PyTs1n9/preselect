"""CLI gate behaviour: a failed validation must not exit 0.

I7 reserves non-zero for failure, but ``validate`` used to print
``valid=false`` and return 0, which let packaging and CI read a failed report
as success. These tests pin the corrected mapping.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

import main
from pipeline.errors import AuthMissing, Code, exit_code_for
from pipeline.settings import load_settings

FIXTURES = Path(__file__).parent / "fixtures" / "engineering"


def _settings(result_dir: Path):
    resolved = load_settings(require_secret=False)
    return replace(resolved, paths=replace(resolved.paths, result_dir=result_dir))


@pytest.mark.parametrize(
    ("case", "expected_exit", "expected_valid"),
    [
        pytest.param("run_ok", 0, True, id="valid-report"),
        pytest.param("run_missing_section", 4, False, id="failed-report"),
        pytest.param("run_too_short", 4, False, id="short-body"),
    ],
)
def test_validate_exit_code_follows_report(tmp_path, monkeypatch, capsys, case, expected_exit, expected_valid):
    shutil.copytree(FIXTURES / case, tmp_path / "run1")
    monkeypatch.setattr(main, "load_settings", lambda: _settings(tmp_path))

    exit_code = main.main(["validate", "--run-id", "run1"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is expected_valid
    assert exit_code == expected_exit


def test_failed_report_is_still_printed_before_exit(tmp_path, monkeypatch, capsys):
    shutil.copytree(FIXTURES / "run_missing_section", tmp_path / "run1")
    monkeypatch.setattr(main, "load_settings", lambda: _settings(tmp_path))

    assert main.main(["validate", "--run-id", "run1"]) == 4

    payload = json.loads(capsys.readouterr().out)
    assert payload["issues"], "a failing run must report its issues, not just the exit code"
    assert (tmp_path / "run1" / "validation.json").is_file()


def test_run_creates_i2_layout_and_passes_source_pdf(tmp_path, monkeypatch):
    captured: dict = {}

    def fake_run_stages(context, stage):
        captured["context"] = context
        captured["stage"] = stage
        return []

    monkeypatch.setattr(main, "run_stages", fake_run_stages)
    monkeypatch.setattr(main, "load_settings", lambda **_: _settings(tmp_path))
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    exit_code = main.main(["run", str(pdf), "--output", str(tmp_path / "runs"), "--stage", "recognize"])

    context = captured["context"]
    assert exit_code == 0
    assert captured["stage"] == "recognize"
    assert context.artifacts["source_pdf"] == pdf
    assert context.run_dir == tmp_path / "runs" / "paper" / context.run_id
    for name in ("input", "raw", "logs"):
        assert (context.run_dir / name).is_dir(), f"I2 layout requires {name}/ before the first stage"


def test_auth_missing_is_a_run_failure_with_exit_two():
    exc = AuthMissing("no credential")
    assert exc.code == "AUTH_MISSING"
    assert exit_code_for(exc) == 2


def test_code_enum_stays_frozen_at_thirty():
    # AUTH_MISSING is a run-failure code, not an I6 validation code. Adding it
    # to the enum would silently break the frozen 30-code table (W0 gate).
    assert len(list(Code)) == 30
