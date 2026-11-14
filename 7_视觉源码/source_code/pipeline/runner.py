"""Shared execution of registered stages for a single run."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from .contracts import RunContext
from .orchestrator import run_stages


def execute_run(pdf: Path, material_id: str, output: Path, settings, stage: str) -> dict:
    run_id = uuid4().hex
    run_dir = output / material_id / run_id
    # I2 requires the layout even when a stage fails early; stages only fill it.
    for name in ("input", "raw", "logs"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)
    # The source PDF never enters the run directory, so it is passed to stages
    # as an artifact path instead of being copied.
    context = RunContext(run_id, material_id, run_dir, settings, {"source_pdf": pdf})
    results = run_stages(context, stage)
    return {"run_id": run_id, "run_dir": str(run_dir), "stages": [result.__dict__ for result in results]}
