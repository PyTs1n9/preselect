"""I10 consumes B's quality fixtures through temporary, independently validated copies."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from pipeline.settings import load_settings
from pipeline.validate import validate_run


QUALITY = Path(__file__).resolve().parents[1] / "fixtures" / "quality"
REQUIRED_CASES = {"fake_citation", "evidence_unreferenced", "number_mismatch",
                  "unit_missing", "material_mismatch"}
CASES = sorted(path.name for path in QUALITY.iterdir() if path.is_dir()) if QUALITY.is_dir() else []


def _hashes(directory: Path) -> dict[str, str]:
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


def test_quality_fixture_inventory_is_complete():
    assert QUALITY.is_dir(), "B quality fixture directory is missing"
    assert REQUIRED_CASES <= set(CASES), "A required quality fixture is missing"


@pytest.mark.parametrize("case_name", CASES or ["missing-quality-fixtures"])
def test_quality_fixture_contract(case_name, tmp_path):
    source = QUALITY / case_name
    assert source.is_dir(), "Quality fixture is missing"
    before = _hashes(source)
    expectation = json.loads((source / "expect.json").read_text(encoding="utf-8"))
    assert expectation["case"] == case_name
    assert isinstance(expectation["expect_codes"], list) and expectation["expect_codes"]
    assert isinstance(expectation["must_not_codes"], list)
    assert expectation["severity"] == "error"
    run = tmp_path / case_name
    shutil.copytree(source, run)
    try:
        report = validate_run(run, load_settings(require_secret=False))
        actual = sorted({issue.code.value for issue in report.issues})
        expected = sorted(expectation["expect_codes"])
        print(f"{case_name} -> expected={expected} -> actual={actual} -> skipped={report.checks['skipped']}")
        assert set(expected) <= set(actual)
        assert not set(expectation["must_not_codes"]) & set(actual)
        assert report.checks["skipped"] == []
        assert report.valid is False
        for code in expected:
            assert any(issue.code.value == code and issue.severity == "error" for issue in report.issues)
        assert all(issue.severity == "error" for issue in report.issues)
    finally:
        assert _hashes(source) == before, "Reading a quality fixture must not modify its files"
