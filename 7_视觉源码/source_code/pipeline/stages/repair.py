"""Repair-stage metadata and disk-based entry point."""

from pathlib import Path

from ..contracts import RunContext, StageResult
from ..repair import repair_draft

STAGE_NAME = "repair"
REQUIRES = ["write"]
PRODUCES = ["final"]


def run(ctx: RunContext, inputs: dict[str, Path]) -> StageResult:
    return repair_draft(ctx.run_dir / "draft.md", ctx.run_dir / "validation.json", ctx)
