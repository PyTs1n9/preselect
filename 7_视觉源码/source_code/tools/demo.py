"""Run local replay outputs outside the frozen production CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Direct script execution starts with tools/ on sys.path, not the source root.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.errors import exit_code_for
from pipeline.runner import execute_run
from pipeline.settings import load_settings
from pipeline.validate import validate_run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local replay demo (non-production)")
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--material-id", default="DEMO-01")
    parser.add_argument("--output", type=Path, default=Path("result/demo"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print("模型输出为回放桩，仅验证链路，不代表识别质量", file=sys.stderr)
        fixtures = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "demo"
        settings = load_settings(cli_overrides={
            "vision.provider": "replay",
            "paths.prompts_dir": fixtures / "prompts",
            "paths.skills_dir": fixtures / "skills",
            "paths.fixtures_dir": fixtures / "model_outputs",
        }, require_secret=False)
        result = execute_run(args.pdf, args.material_id, args.output, settings, "all")
        report = validate_run(Path(result["run_dir"]), settings)
        result.update(valid=report.valid, han_chars=report.stats["han_chars"],
                      issues=[issue.to_dict() for issue in report.issues])
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if report.valid else 4
    except Exception as exc:
        code = getattr(exc, "code", "UNCLASSIFIED")
        code = getattr(code, "value", code)
        message = getattr(exc, "message", "Operation failed")
        print(json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False))
        return exit_code_for(exc)


if __name__ == "__main__":
    raise SystemExit(main())
