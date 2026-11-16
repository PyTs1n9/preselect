"""Command-line entry point for the four competition materials."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline import (
    IDS,
    ModelClient,
    ModelConfig,
    evidence_contexts,
    generate_one,
    read_pages,
)


HERE = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create four source-grounded science articles")
    parser.add_argument("--input-dir", type=Path, default=HERE.parent.parent / "赛题")
    parser.add_argument("--output-dir", type=Path, default=HERE.parent)
    parser.add_argument("--log-dir", type=Path, default=HERE.parent / "run_logs")
    parser.add_argument("--ids", nargs="+", choices=IDS, default=list(IDS))
    parser.add_argument("--credentials-file", type=Path, help="TOML model configuration")
    parser.add_argument("--probe", action="store_true", help="Test the model API without generating")
    parser.add_argument(
        "--validate-sources",
        action="store_true",
        help="Check source PDFs and evidence quotes without calling a model",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = json.loads((HERE / "evidence.json").read_text(encoding="utf-8"))
    for material_id in args.ids:
        record = records[material_id]
        contexts = evidence_contexts(record, read_pages(args.input_dir / record["file"]))
        print(f"{material_id}: {len(contexts)} evidence cards verified")
    if args.validate_sources:
        return

    config = (
        ModelConfig.from_toml(args.credentials_file)
        if args.credentials_file
        else ModelConfig.from_environment()
    )
    client = ModelClient(config)
    if args.probe:
        response = client.complete("请简短回答。", "请只回复：连接成功")
        print(f"API probe passed; response length: {len(response)}")
        return
    for material_id in args.ids:
        result = generate_one(
            material_id,
            records[material_id],
            args.input_dir,
            args.output_dir,
            args.log_dir,
            client,
        )
        print(f"{material_id}: wrote {result['output']} ({result['body_character_count']} chars)")


if __name__ == "__main__":
    main()
