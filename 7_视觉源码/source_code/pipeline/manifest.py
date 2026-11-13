"""I8 manifest shape and safe deterministic serialization."""

from __future__ import annotations

import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

from .settings import Settings


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sdk_version() -> str | None:
    try:
        from openai_codex import __version__ as version
    except ImportError:
        return None
    return str(version)


def create_run_manifest(run_id: str, material_id: str, settings: Settings) -> dict:
    return {
        "run_id": run_id, "material_id": material_id, "started_at": utc_now(), "ended_at": None,
        "python": platform.python_version(), "platform": platform.platform(),
        "input": {"path_name": None, "sha256": None, "page_count": None},
        "code": {"git_commit": None, "files": {}},
        "assets": {"prompts": {}, "skills": {}, "schemas": {}},
        "config": settings.describe(),
        "model": {"sdk": "openai-codex", "sdk_version": _sdk_version(),
                   "binary_version": None, "provider": settings.vision.provider,
                   "model": settings.vision.model, "wire_api": settings.vision.wire_api,
                   "sandbox": "read-only", "ephemeral": True,
                   "max_retries": settings.vision.max_retries},
        "calls": {}, "stages": [], "deps": {}, "reproducibility": "not-bitwise",
        "cache": {"enabled": settings.pipeline.keep_intermediate, "key": None, "hit": False},
    }


def write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
