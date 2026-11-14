"""A15 packaging gates and ZIP components; positive gate/archive checks are
explicitly component-level proof, not end-to-end package success.
"""

import hashlib
import json
import zlib
from dataclasses import replace
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

import main
from pipeline.contracts import PackageReport, ValidationReport
from pipeline.errors import Code, ConfigError, PipelineError, exit_code_for
from pipeline.manifest import utc_now
from pipeline.packaging import (
    MATERIAL_FILES,
    PackageFailure,
    _write_report,
    build_package,
    collect_entries,
    evaluate_gate,
    select_run,
    verify_zip,
    write_zip,
)
from pipeline.settings import PathsSettings, PipelineSettings, SecretValue, Settings, VisionSettings

FIXTURES = Path(__file__).parent / "fixtures" / "engineering"


def _settings(result_dir: Path):
    return Settings(
        vision=VisionSettings("openai_codex", "offline-package-test", "", "responses", 30.0, 0, SecretValue()),
        pipeline=PipelineSettings(2, True),
        paths=PathsSettings(result_dir, Path("prompts"), Path("skills"), Path("fixtures")),
    )


def _manifest(run_id: str, material_id: str, started_at: str,
              write_status: str = "ok", repair_status: str = "ok") -> dict:
    return {
        "run_id": run_id,
        "material_id": material_id,
        "started_at": started_at,
        "ended_at": started_at,
        "stages": [
            {"name": "write", "status": write_status},
            {"name": "repair", "status": repair_status},
        ],
    }


def _make_run(root: Path, material_id: str, run_id: str = "r1", *,
              fixture: str = "run_ok", started_at: str = "2026-11-14T09:00:00Z",
              write_status: str = "ok", repair_status: str = "ok", extra=None) -> Path:
    target = root / material_id / run_id
    target.parent.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copytree(FIXTURES / fixture, target)
    (target / "manifest.json").write_text(json.dumps(
        _manifest(run_id, material_id, started_at, write_status, repair_status),
        ensure_ascii=False, indent=2,
    ), encoding="utf-8")
    (target / "raw").mkdir(exist_ok=True)
    (target / "raw" / "recognize_1.json").write_text("{}", encoding="utf-8")
    if extra is not None:
        extra(target)
    return target


def _valid_report(skipped=None) -> ValidationReport:
    return ValidationReport(True, "1.1.0", "2026-11-14T09:00:00Z", {}, [],
                            {"enabled": [], "skipped": [] if skipped is None else skipped})


def _assert_no_outputs(root: Path) -> None:
    assert not list(root.glob("*.zip"))
    assert not (root / "package_report.json").exists()
    assert not list(root.glob(".package-*.tmp"))


def test_select_run_uses_unique_max_started_at_not_directory_order(tmp_path):
    root = tmp_path / "runs"
    _make_run(root, "M1", "older", started_at="2026-11-14T08:00:00Z")
    _make_run(root, "M1", "latest", started_at="2026-11-14T10:00:00Z")
    _make_run(root, "M1", "middle", started_at="2026-11-14T09:00:00Z")
    assert select_run(root, "M1").name == "latest"


def test_select_run_missing_started_at_fails(tmp_path):
    root = tmp_path / "runs"
    run = _make_run(root, "M1")
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    del manifest["started_at"]
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageFailure, match="started_at"):
        select_run(root, "M1")


def test_select_run_latest_timestamp_tie_fails(tmp_path):
    root = tmp_path / "runs"
    _make_run(root, "M1", "r1")
    _make_run(root, "M1", "r2")
    with pytest.raises(PackageFailure, match="ambiguous"):
        select_run(root, "M1")


def test_select_run_without_manifest_backed_directories_fails(tmp_path):
    (tmp_path / "runs" / "M1" / "r1").mkdir(parents=True)
    with pytest.raises(PackageFailure, match="No manifest-backed runs"):
        select_run(tmp_path / "runs", "M1")


def test_select_run_rejects_manifest_material_id_mismatch(tmp_path):
    root = tmp_path / "runs"
    run = _make_run(root, "M1")
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    manifest["material_id"] = "M2"
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageFailure, match="material_id mismatch"):
        select_run(root, "M1")


def test_cli_valid_run_is_rejected_for_live_skipped_checks(tmp_path, monkeypatch, capsys):
    root = tmp_path / "runs"
    _make_run(root, "M1")
    monkeypatch.setattr(main, "load_settings", lambda: _settings(root))
    output = tmp_path / "submission.zip"
    code = main.main(["package", "--materials", "M1", "--out", str(output)])
    payload = json.loads(capsys.readouterr().out)
    assert code == 7 and payload["error"]["code"] == "IO_FAILURE"
    assert "skipped" in payload["error"]["message"]
    _assert_no_outputs(tmp_path)


def test_cli_invalid_report_maps_to_schema_invalid_exit_four(tmp_path, monkeypatch, capsys):
    root = tmp_path / "runs"
    _make_run(root, "M2", fixture="run_missing_section")
    monkeypatch.setattr(main, "load_settings", lambda: _settings(root))
    output = tmp_path / "submission.zip"
    code = main.main(["package", "--materials", "M2", "--out", str(output)])
    payload = json.loads(capsys.readouterr().out)
    assert code == 4 and payload["error"]["code"] == "SCHEMA_INVALID"
    _assert_no_outputs(tmp_path)


def test_cli_missing_final_fails_before_validator_rewrites_report(tmp_path, monkeypatch, capsys):
    root = tmp_path / "runs"

    def remove_final(run):
        (run / "final.md").rename(run / "draft.md")

    run = _make_run(root, "M3", extra=remove_final)
    report_before = (run / "validation.json").read_bytes()
    monkeypatch.setattr(main, "load_settings", lambda: _settings(root))
    output = tmp_path / "submission.zip"
    code = main.main(["package", "--materials", "M3", "--out", str(output)])
    payload = json.loads(capsys.readouterr().out)
    assert code == 7 and payload["error"]["code"] == "IO_FAILURE"
    assert "final.md" in payload["error"]["message"]
    assert (run / "validation.json").read_bytes() == report_before
    _assert_no_outputs(tmp_path)


def test_cli_dangerous_env_file_fails_without_residual_outputs(tmp_path, monkeypatch, capsys):
    root = tmp_path / "runs"

    def add_fake_env(run):
        (run / ".env").write_text("LLM_API_KEY=sk-test\n", encoding="utf-8")

    _make_run(root, "M4", extra=add_fake_env)
    monkeypatch.setattr(main, "load_settings", lambda: _settings(root))
    output = tmp_path / "submission.zip"
    code = main.main(["package", "--materials", "M4", "--out", str(output)])
    payload = json.loads(capsys.readouterr().out)
    assert code == 7 and payload["error"]["code"] == "IO_FAILURE"
    assert "M4/.env" in payload["error"]["message"]
    assert "sk-test" not in payload["error"]["message"]
    _assert_no_outputs(tmp_path)


def test_evaluate_gate_accepts_empty_skipped_component_level_not_end_to_end():
    manifest = _manifest("r1", "M1", "2026-11-14T09:00:00Z")
    evaluate_gate("M1", manifest, _valid_report())
    print("组件级证明，非端到端")


def test_evaluate_gate_rejects_skipped_and_invalid_report_with_distinct_codes():
    manifest = _manifest("r1", "M1", "2026-11-14T09:00:00Z")
    with pytest.raises(PackageFailure, match="NUMBER_MISMATCH") as skipped:
        evaluate_gate("M1", manifest, _valid_report(["NUMBER_MISMATCH"]))
    assert exit_code_for(skipped.value) == 7
    invalid = ValidationReport(False, "1.1.0", "2026-11-14T09:00:00Z", {}, [],
                               {"enabled": [], "skipped": []})
    with pytest.raises(PipelineError) as failed:
        evaluate_gate("M1", manifest, invalid)
    assert failed.value.code == Code.SCHEMA_INVALID and exit_code_for(failed.value) == 4
    duplicate = _manifest("r1", "M1", "2026-11-14T09:00:00Z")
    duplicate["stages"].append({"name": "write", "status": "ok"})
    with pytest.raises(PackageFailure, match="Duplicate stage"):
        evaluate_gate("M1", duplicate, _valid_report())
    print("组件级证明，非端到端")


@pytest.mark.parametrize("stage,status", [
    ("write", "missing"), ("write", "failed"), ("write", "skipped"),
    ("repair", "missing"), ("repair", "failed"), ("repair", "skipped"),
])
def test_evaluate_gate_requires_write_and_repair_ok(stage, status):
    manifest = _manifest("r1", "M1", "2026-11-14T09:00:00Z")
    if status == "missing":
        manifest["stages"] = [item for item in manifest["stages"] if item["name"] != stage]
    else:
        next(item for item in manifest["stages"] if item["name"] == stage)["status"] = status
    with pytest.raises(PackageFailure, match=f"stage {stage} is not ok") as caught:
        evaluate_gate("M1", manifest, _valid_report())
    assert exit_code_for(caught.value) == 7


def test_layout_crc_and_readback_are_exact_component_level_not_end_to_end(tmp_path):
    runs = {}
    for material in ("M2", "M1"):
        run = tmp_path / "runs" / material / "r1"
        run.mkdir(parents=True)
        for filename in MATERIAL_FILES:
            (run / filename).write_bytes(f"{material}:{filename}\n".encode())
        (run / "input").mkdir()
        (run / "input" / "source.pdf.sha256").write_text("a" * 64 + "\n", encoding="ascii")
        runs[material] = run
    entries = collect_entries(runs)
    expected = sorted(f"materials/{material}/{filename}"
                      for material in ("M1", "M2") for filename in MATERIAL_FILES)
    assert [name for name, _ in entries] == expected
    archive_path = tmp_path / "component.zip"
    written = write_zip(entries, archive_path)
    sources = {}
    for name, source in entries:
        data = source.read_bytes()
        sources[name] = (len(data), zlib.crc32(data) & 0xFFFFFFFF, hashlib.sha256(data).hexdigest())
    verify_zip(archive_path, expected, sources)
    with ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        assert sorted(archive.namelist()) == expected
        assert not any(name.endswith(".pdf") or name.startswith("result/") for name in archive.namelist())
        for info in archive.infolist():
            assert info.compress_type == ZIP_DEFLATED
            assert (info.file_size, info.CRC) == written[info.filename]
    damaged = tmp_path / "damaged.zip"
    with ZipFile(archive_path) as source, ZipFile(damaged, "w", compression=ZIP_DEFLATED) as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == expected[0]:
                data += b"tamper"
            target.writestr(info.filename, data)
    with pytest.raises(PackageFailure):
        verify_zip(damaged, expected, sources)
    print("组件级证明，非端到端")


def test_deterministic_zip_bytes_for_same_component_inputs(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    for filename in MATERIAL_FILES:
        (run / filename).write_bytes(filename.encode())
    entries = collect_entries({"M1": run})
    one, two = tmp_path / "one.zip", tmp_path / "two.zip"
    write_zip(entries, one)
    write_zip(entries, two)
    assert hashlib.sha256(one.read_bytes()).digest() == hashlib.sha256(two.read_bytes()).digest()
    print("组件级证明，非端到端")


def test_package_report_writer_places_four_field_report_next_to_zip(tmp_path):
    package_report_path = tmp_path / "package_report.json"
    report = PackageReport(
        [{"name": "materials/M1/final.md", "size": 1, "crc32": 42}],
        ["M1/raw/recognize_1.json"], True, utc_now(),
    )
    _write_report(package_report_path, report)
    payload = json.loads(package_report_path.read_text(encoding="utf-8"))
    assert set(payload) == {"entries", "excluded", "verified", "built_at"}
    assert payload["excluded"] == ["M1/raw/recognize_1.json"]
    assert payload["entries"] == sorted(payload["entries"], key=lambda row: row["name"])
    assert payload["verified"] is True
    assert not list(tmp_path.glob(".package-*.tmp"))
    print("组件级证明，非端到端")


@pytest.mark.parametrize("filename", [".ENV", "paper.PDF", "private.KEY", "cert.pem"])
def test_dangerous_names_and_suffixes_are_case_insensitive(tmp_path, filename):
    from pipeline.packaging import _scan_run

    run = tmp_path / "run"
    run.mkdir()
    (run / filename).write_text("fake", encoding="utf-8")
    with pytest.raises(PackageFailure, match="Dangerous file rejected") as caught:
        _scan_run(run, "M1")
    assert exit_code_for(caught.value) == 7


@pytest.mark.parametrize("materials", [[], ["../M1"], ["."], ["M1", "M1"]])
def test_unsafe_empty_or_duplicate_material_ids_map_to_config_exit_two(tmp_path, materials):
    settings = _settings(tmp_path / "runs")
    with pytest.raises(ConfigError) as caught:
        build_package(materials, tmp_path / "submission.zip", settings)
    assert exit_code_for(caught.value) == 2
    assert not (tmp_path / "submission.zip").exists()
