"""Command-line entry point for the registry-driven PDF pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from pipeline.contracts import RunContext
from pipeline.errors import IoFailure, PipelineError, exit_code_for
from pipeline.orchestrator import run_stages
from pipeline.packaging import build_package
from pipeline.settings import load_settings
from pipeline.validate import validate_run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PDF vision pipeline")
    subs = parser.add_subparsers(dest="command", required=True)
    run = subs.add_parser("run", help="run registered pipeline stages")
    run.add_argument("pdf", type=Path)
    run.add_argument("--config", type=Path, default=Path("config.json"))
    run.add_argument("--output", type=Path, default=Path("result/runs"))
    run.add_argument("--material-id", default=None)
    run.add_argument("--stage", default="all")
    doctor = subs.add_parser("doctor", help="show resolved configuration")
    doctor.add_argument("--offline", action="store_true")
    validate = subs.add_parser("validate", help="validate an existing run")
    validate.add_argument("--run-id", required=True)
    package = subs.add_parser("package", help="build a submission package")
    package.add_argument("--materials", nargs="+", required=True)
    package.add_argument("--out", required=True, type=Path)
    return parser


def _run(args: argparse.Namespace) -> dict:
    settings = load_settings(config_path=args.config)
    run_id = uuid4().hex
    material_id = args.material_id or args.pdf.stem
    run_dir = args.output / material_id / run_id
    # I2 requires the layout even when a stage fails early; stages only fill it.
    for name in ("input", "raw", "logs"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)
    # The source PDF never enters the run directory, so it is passed to stages
    # as an artifact path instead of being copied.
    context = RunContext(run_id, material_id, run_dir, settings, {"source_pdf": args.pdf})
    results = run_stages(context, args.stage)
    return {"run_id": run_id, "run_dir": str(run_dir), "stages": [result.__dict__ for result in results]}


def _doctor(args: argparse.Namespace) -> dict:
    settings = load_settings(require_secret=not args.offline)
    return {"config": settings.describe(), "warnings": settings.unknown_keys()}


def _resolve_run_dir(result_dir: Path, run_id: str) -> Path:
    """Accept both ``<run_id>`` and ``<material_id>/<run_id>`` per I2 layout."""

    direct = result_dir / run_id
    if direct.is_dir():
        return direct
    matches = sorted(path for path in result_dir.glob(f"*/{run_id}") if path.is_dir())
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise IoFailure(f"Run not found: {run_id}")
    raise PipelineError("RUN_AMBIGUOUS", f"Multiple runs match: {run_id}")


def _validate(args: argparse.Namespace) -> tuple[dict, int]:
    """Return the report and the gate exit code.

    I7 has no dedicated "validation failed" bucket; 4 is the closest frozen
    meaning (structure validation failed and is not repairable). Returning 0
    when ``valid`` is false would let packaging and CI read a failed report as
    success, so the report is still printed but the process fails.
    """

    settings = load_settings()
    run_dir = _resolve_run_dir(settings.paths.result_dir, args.run_id)
    report = validate_run(run_dir, settings)
    return report.to_dict(), 0 if report.valid else 4


def _package(args: argparse.Namespace) -> dict:
    settings = load_settings()
    materials = [item for group in args.materials for item in group.split(",") if item]
    report = build_package(materials, args.out, settings)
    return {"entries": report.entries, "excluded": report.excluded, "verified": report.verified, "built_at": report.built_at}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        handlers = {"run": _run, "doctor": _doctor, "validate": _validate, "package": _package}
        result = handlers[args.command](args)
        code = 0
        if isinstance(result, tuple):
            result, code = result
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return code
    except Exception as exc:
        code = getattr(exc, "code", "UNCLASSIFIED")
        code = getattr(code, "value", code)
        message = getattr(exc, "message", "Operation failed")
        print(json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False))
        return exit_code_for(exc)


if __name__ == "__main__":
    raise SystemExit(main())
