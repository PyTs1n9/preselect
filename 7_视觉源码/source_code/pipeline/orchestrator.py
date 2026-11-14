"""Registry-driven stage execution."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .contracts import RunContext, StageResult
from .errors import PipelineError
from .stages import discover_modules

# Artifacts that are not JSON. Everything else is written as <name>.json.
_TEXT_ARTIFACTS = {"draft": "draft.md", "draft_en": "draft_en.md", "final": "final.md"}


class Stage(str, Enum):
    recognize = "recognize"
    normalize = "normalize"
    evidence = "evidence"
    write = "write"
    validate = "validate"
    package = "package"


@dataclass(frozen=True)
class StageSpec:
    name: str
    fn: Callable[[RunContext, dict[str, Path]], Any]
    requires: tuple[str, ...]
    produces: tuple[str, ...]


STAGE_REGISTRY: dict[str, StageSpec] = {}


def register_stage(name: str, fn: Callable, requires: list[str], produces: list[str]) -> None:
    if not isinstance(name, str) or not name or not callable(fn):
        raise PipelineError("STAGE_INVALID", "Stage registration is invalid")
    if name in STAGE_REGISTRY:
        existing = STAGE_REGISTRY[name]
        if existing.fn is fn and existing.requires == tuple(requires) and existing.produces == tuple(produces):
            return
        raise PipelineError("STAGE_DUPLICATE", "Stage name is registered more than once")
    STAGE_REGISTRY[name] = StageSpec(name, fn, tuple(requires), tuple(produces))


def discover_stages() -> None:
    for module in discover_modules():
        module_name = module.__name__.rsplit(".", 1)[-1]
        metadata = {key: getattr(module, key, None) for key in ("STAGE_NAME", "run", "REQUIRES", "PRODUCES")}
        if not isinstance(metadata["STAGE_NAME"], str) or not callable(metadata["run"]):
            raise PipelineError("STAGE_INVALID", f"Stage metadata is invalid: {module_name}")
        if not isinstance(metadata["REQUIRES"], (list, tuple)) or not isinstance(metadata["PRODUCES"], (list, tuple)):
            raise PipelineError("STAGE_INVALID", f"Stage metadata is invalid: {module_name}")
        register_stage(metadata["STAGE_NAME"], metadata["run"], list(metadata["REQUIRES"]), list(metadata["PRODUCES"]))


def _execution_order(target: str) -> list[str]:
    if target == "all":
        selected = set(STAGE_REGISTRY)
    elif target in STAGE_REGISTRY:
        selected = {target}
    else:
        raise PipelineError("STAGE_NOT_REGISTERED", "Requested stage is not registered")
    if not selected:
        # I7: an unimplemented stage must fail, not silently exit 0.
        raise PipelineError("STAGE_NOT_REGISTERED", "No stages are registered")
    changed = True
    while changed:
        changed = False
        for name in tuple(selected):
            for dependency in STAGE_REGISTRY[name].requires:
                if dependency not in STAGE_REGISTRY:
                    raise PipelineError("STAGE_NOT_REGISTERED", "A required stage is not registered")
                if dependency not in selected:
                    selected.add(dependency)
                    changed = True
    # Registration order is alphabetical because discovery sorts module names.
    # Executing in that order would run evidence before recognize, so the
    # order is derived from `requires` with a deterministic tie-break.
    ordered: list[str] = []
    state: dict[str, int] = {}

    def visit(name: str) -> None:
        if state.get(name) == 1:
            raise PipelineError("STAGE_CYCLE", "Stage dependency cycle detected")
        if state.get(name) == 2:
            return
        state[name] = 1
        for dependency in STAGE_REGISTRY[name].requires:
            visit(dependency)
        state[name] = 2
        ordered.append(name)

    for name in STAGE_REGISTRY:
        if name in selected:
            visit(name)
    return ordered


def artifact_paths(ctx: RunContext, spec: StageSpec) -> dict[str, Path]:
    """Where a stage's declared artifacts live. Kept in one place so resume,
    cache and packaging cannot drift from each other."""
    return {name: ctx.run_dir / _TEXT_ARTIFACTS.get(name, f"{name}.json")
            for name in spec.produces}


def _resumable(ctx: RunContext, spec: StageSpec) -> bool:
    """I1 resume: a stage is skipped only when its manifest record succeeded and
    every declared artifact is still on disk and parseable."""
    try:
        manifest = json.loads((ctx.run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    records = {stage.get("name"): stage for stage in manifest.get("stages") or [] if isinstance(stage, dict)}
    record = records.get(spec.name)
    if not isinstance(record, dict) or record.get("status") != "ok":
        return False
    for name, path in artifact_paths(ctx, spec).items():
        if not path.is_file():
            return False
        if path.suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return False
    return True


def run_stages(ctx: RunContext, target: str, resume: bool = False) -> list[StageResult]:
    discover_stages()
    order = _execution_order(target.value if isinstance(target, Stage) else target)
    results: list[StageResult] = []
    artifacts = dict(ctx.artifacts)
    for name in order:
        spec = STAGE_REGISTRY[name]
        started = time.perf_counter()
        if resume and _resumable(ctx, spec):
            resumed = artifact_paths(ctx, spec)
            artifacts.update(resumed)
            results.append(StageResult(name, "skipped", 0.0, dict(resumed), []))
            continue
        try:
            output = spec.fn(ctx, dict(artifacts))
            duration = (time.perf_counter() - started) * 1000
            if isinstance(output, dict):
                output = StageResult(name, "ok", duration, {str(k): Path(v) for k, v in output.items()}, [])
            if not isinstance(output, StageResult) or output.name != name or output.status not in {"ok", "skipped"}:
                raise PipelineError("STAGE_INVALID", "Stage returned an invalid result")
            missing = [key for key in spec.produces if key not in output.artifacts]
            if missing:
                raise PipelineError("STAGE_INVALID", "Stage did not produce its declared artifacts")
            absent = [key for key, path in output.artifacts.items() if not path.is_file()]
            if absent:
                raise PipelineError("STAGE_INVALID", "Stage declared an artifact that was not written")
            artifacts.update(output.artifacts)
            results.append(StageResult(output.name, output.status, duration, dict(output.artifacts), list(output.issues)))
        except Exception as exc:
            duration = (time.perf_counter() - started) * 1000
            results.append(StageResult(name, "failed", duration, {}, []))
            if isinstance(exc, PipelineError):
                raise
            raise PipelineError("STAGE_FAILED", "Stage execution failed") from None
    return results
