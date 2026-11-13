"""Stage ordering follows ``requires``, not module-name order.

``discover_modules`` sorts stage modules alphabetically, so the registry is
inserted as evidence, normalize, recognize. Executing in that order would run
evidence before recognition ever produced anything, and ``--stage all`` would
look successful while doing the wrong thing.
"""

from __future__ import annotations

import pytest

from pipeline import orchestrator
from pipeline.errors import PipelineError


def _spec(name: str, requires: list[str]):
    return orchestrator.StageSpec(name, lambda ctx, inputs: None, tuple(requires), (name,))


def _registry():
    return {
        "evidence": _spec("evidence", ["normalize"]),
        "normalize": _spec("normalize", ["recognize"]),
        "recognize": _spec("recognize", []),
    }


def test_all_is_topologically_ordered(monkeypatch):
    monkeypatch.setattr(orchestrator, "STAGE_REGISTRY", _registry())
    assert orchestrator._execution_order("all") == ["recognize", "normalize", "evidence"]


def test_target_pulls_in_prerequisites_in_order(monkeypatch):
    monkeypatch.setattr(orchestrator, "STAGE_REGISTRY", _registry())
    assert orchestrator._execution_order("evidence") == ["recognize", "normalize", "evidence"]


def test_empty_registry_fails_instead_of_exiting_zero(monkeypatch):
    monkeypatch.setattr(orchestrator, "STAGE_REGISTRY", {})
    with pytest.raises(PipelineError) as caught:
        orchestrator._execution_order("all")
    assert caught.value.code == "STAGE_NOT_REGISTERED"


def test_unknown_dependency_is_rejected(monkeypatch):
    monkeypatch.setattr(orchestrator, "STAGE_REGISTRY", {"recognize": _spec("recognize", ["pdf"])})
    with pytest.raises(PipelineError) as caught:
        orchestrator._execution_order("recognize")
    assert caught.value.code == "STAGE_NOT_REGISTERED"


def test_dependency_cycle_is_rejected(monkeypatch):
    monkeypatch.setattr(orchestrator, "STAGE_REGISTRY", {"a": _spec("a", ["b"]), "b": _spec("b", ["a"])})
    with pytest.raises(PipelineError) as caught:
        orchestrator._execution_order("all")
    assert caught.value.code == "STAGE_CYCLE"
