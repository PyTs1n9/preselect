"""Command-line entry point for the PDF vision pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.orchestrator import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract and validate structured data from a PDF.")
    parser.add_argument("pdf", type=Path, help="Path to the input PDF")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--output", type=Path, default=Path("result/runs"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_pipeline(args.pdf, args.config, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
