"""W0 repair boundary; bounded repair is implemented by A11."""

from pathlib import Path

from .contracts import RunContext, StageResult


def repair_draft(draft_path: Path, validation_path: Path, ctx: RunContext) -> StageResult:
    raise NotImplementedError("Draft repair is assigned to A11")
