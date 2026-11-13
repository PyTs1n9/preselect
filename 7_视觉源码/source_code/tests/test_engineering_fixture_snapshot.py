"""Committed fixture reports must match what the current checker produces.

The A9 acceptance one-liners run ``validate_run`` directly on the fixtures and
therefore rewrite ``validation.json`` (``checked_at`` changes on every run).
That leaves the tree dirty and lets a code change drift away from the
committed snapshot without any test noticing.

This test regenerates each fixture inside ``tmp_path`` and compares the report
with the committed file, ignoring only ``checked_at``. It never writes to the
repository.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pipeline.settings import load_settings
from pipeline.validate import validate_run

FIXTURES = Path(__file__).parent / "fixtures" / "engineering"
CASES = sorted(path.name for path in FIXTURES.iterdir() if path.is_dir())


@pytest.mark.parametrize("case", CASES)
def test_committed_report_matches_current_checker(tmp_path, case):
    committed = json.loads((FIXTURES / case / "validation.json").read_text(encoding="utf-8"))
    target = tmp_path / case
    shutil.copytree(FIXTURES / case, target)

    generated = validate_run(target, load_settings(require_secret=False)).to_dict()

    committed.pop("checked_at")
    generated.pop("checked_at")
    assert generated == committed
