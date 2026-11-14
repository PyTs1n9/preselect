"""Write-stage metadata and disk-based entry point."""

from pathlib import Path

from ..contracts import RunContext, StageResult
from ..writer import write_draft

STAGE_NAME = "write"
REQUIRES = ["evidence"]
PRODUCES = ["draft", "draft_en"]


def run(ctx: RunContext, inputs: dict[str, Path]) -> StageResult:
    return write_draft(ctx.run_dir / "evidence.json", ctx)
