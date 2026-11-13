"""Run manifest creation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def create_run_manifest(pdf_path: Path, config: dict) -> dict:
    return {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8],
        "input": str(pdf_path),
        "config_version": config.get("version", 1),
    }
