"""I1 resume: a stage is skipped only on positive evidence, never on absence.

The three cases below are the whole contract: skip requires a successful
manifest record *and* a parseable artifact; a missing artifact or a failed
record must execute the stage again instead of trusting the old directory.
"""

from __future__ import annotations

import json

from pipeline import orchestrator
from pipeline.contracts import RunContext
from pipeline.settings import load_settings


def _context(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    return RunContext("r1", "m1", run_dir, load_settings(require_secret=False), {}), run_dir


def _stage(run_dir, calls):
    def stage_fn(ctx, inputs):
        calls["count"] += 1
        artifact = run_dir / "alpha.json"
        artifact.write_text('{"ok": true}\n', encoding="utf-8")
        (run_dir / "manifest.json").write_text(
            json.dumps({"stages": [{"name": "alpha", "status": "ok"}]}), encoding="utf-8")
        return {"alpha": artifact}

    return orchestrator.StageSpec("alpha", stage_fn, (), ("alpha",))


def _install(monkeypatch, spec):
    monkeypatch.setattr(orchestrator, "STAGE_REGISTRY", {"alpha": spec})
    monkeypatch.setattr(orchestrator, "discover_stages", lambda: None)


def test_resume_skips_only_when_manifest_and_artifact_are_intact(tmp_path, monkeypatch):
    ctx, run_dir = _context(tmp_path)
    calls = {"count": 0}
    _install(monkeypatch, _stage(run_dir, calls))

    first = orchestrator.run_stages(ctx, "alpha", resume=False)
    assert calls["count"] == 1 and first[0].status == "ok"

    second = orchestrator.run_stages(ctx, "alpha", resume=True)
    assert calls["count"] == 1, "resume must not execute a stage twice"
    assert second[0].status == "skipped"
    assert second[0].artifacts == {"alpha": run_dir / "alpha.json"}

    (run_dir / "alpha.json").unlink()
    third = orchestrator.run_stages(ctx, "alpha", resume=True)
    assert calls["count"] == 2, "a missing artifact must force a re-run"
    assert third[0].status == "ok"


def test_resume_reruns_when_manifest_records_failure(tmp_path, monkeypatch):
    ctx, run_dir = _context(tmp_path)
    calls = {"count": 0}
    _install(monkeypatch, _stage(run_dir, calls))
    (run_dir / "alpha.json").write_text('{"ok": true}\n', encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps({"stages": [{"name": "alpha", "status": "failed"}]}), encoding="utf-8")

    result = orchestrator.run_stages(ctx, "alpha", resume=True)

    assert calls["count"] == 1 and result[0].status == "ok"


def test_resume_reruns_when_artifact_is_not_parseable(tmp_path, monkeypatch):
    ctx, run_dir = _context(tmp_path)
    calls = {"count": 0}
    _install(monkeypatch, _stage(run_dir, calls))
    (run_dir / "alpha.json").write_text("{not json", encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps({"stages": [{"name": "alpha", "status": "ok"}]}), encoding="utf-8")

    result = orchestrator.run_stages(ctx, "alpha", resume=True)

    assert calls["count"] == 1 and result[0].status == "ok"
