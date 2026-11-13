"""Coordinate PDF loading, vision extraction, normalization, and validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .manifest import create_run_manifest
from .pdf_input import load_pdf
from .validate import validate_document


def run_pipeline(pdf_path: Path, config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    pdf = load_pdf(pdf_path)
    manifest = create_run_manifest(pdf_path, config)
    document = {
        "document_id": manifest["run_id"],
        "source": {"path": str(pdf_path), "page_count": pdf["page_count"]},
        "pages": pdf["pages"],
        "blocks": [],
    }
    validation = validate_document(document)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_dir = output_dir / manifest["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "document_ir.json").write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"run_id": manifest["run_id"], "validation": validation}
