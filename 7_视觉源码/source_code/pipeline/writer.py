"""W0 writing boundary; model-backed writing is implemented by A11."""

from pathlib import Path

from .contracts import RunContext, StageResult


def write_draft(evidence_path: Path, ctx: RunContext) -> StageResult:
    raise NotImplementedError("Draft writing is assigned to A11")
