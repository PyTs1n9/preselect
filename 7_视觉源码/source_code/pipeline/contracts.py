"""W0 cross-layer contracts; business stages exchange artifact paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import Issue
from .settings import Settings


@dataclass(frozen=True)
class ModelRequest:
    stage: str
    prompt_name: str
    prompt_text: str
    payload: dict[str, Any]
    json_schema: dict | None = None
    images: list[Path] | None = None
    skill: tuple[str, str] | None = None
    timeout_s: float | None = None
    model: str | None = None


@dataclass(frozen=True)
class ModelResult:
    text: str
    parsed: Any
    raw: dict
    usage: dict
    truncated: bool
    latency_ms: float
    attempts: int
    request_hash: str
    model: str
    turn_status: str
    error_message: str | None = None


@dataclass(frozen=True)
class PromptAsset:
    text: str
    sha256: str
    path: Path
    version: str | None


@dataclass(frozen=True)
class SkillAsset:
    text: str
    sha256: str
    path: Path


@dataclass(frozen=True)
class RunContext:
    run_id: str
    material_id: str
    run_dir: Path
    settings: Settings
    artifacts: dict[str, Path]


@dataclass(frozen=True)
class StageResult:
    name: str
    status: str
    duration_ms: float
    artifacts: dict[str, Path]
    issues: list[Issue]


@dataclass(frozen=True)
class PageInfo:
    page_number: int
    width_pt: float
    height_pt: float
    rotation: int
    has_text_layer: bool
    render_path: Path | None


@dataclass(frozen=True)
class PdfInfo:
    sha256: str
    page_count: int
    file_size: int
    encrypted: bool
    pages: list[PageInfo]


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    schema_version: str
    checked_at: str
    stats: dict[str, int]
    issues: list[Issue]
    checks: dict[str, list[str]]

    def to_dict(self) -> dict:
        return {"valid": self.valid, "schema_version": self.schema_version,
                "checked_at": self.checked_at, "stats": dict(self.stats),
                "issues": [issue.to_dict() for issue in self.issues],
                "checks": {key: list(value) for key, value in self.checks.items()}}


@dataclass(frozen=True)
class PackageReport:
    entries: list[dict]
    excluded: list[str]
    verified: bool
    built_at: str
