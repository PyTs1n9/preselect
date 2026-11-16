"""Verify that model logs match the source PDFs and raw generated articles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline import IDS, ModelConfig, sha256


HERE = Path(__file__).resolve().parent


def verify(
    input_dir: Path,
    raw_dir: Path,
    final_dir: Path,
    log_dir: Path,
    credential_file: Path | None = None,
) -> dict:
    records = json.loads((HERE / "evidence.json").read_text(encoding="utf-8"))
    sensitive: list[str] = []
    if credential_file:
        config = ModelConfig.from_toml(credential_file)
        sensitive = [config.api_key] + [
            value for value in (config.headers or {}).values() if len(value) >= 12
        ]
    results = {}
    for material_id in IDS:
        source = input_dir / records[material_id]["file"]
        raw = raw_dir / f"{material_id}.md"
        final = final_dir / f"{material_id}.md"
        log_path = log_dir / f"{material_id}.json"
        if not all(path.is_file() for path in (source, raw, final, log_path)):
            results[material_id] = {"complete": False}
            continue
        log_text = log_path.read_text(encoding="utf-8")
        log = json.loads(log_text)
        results[material_id] = {
            "complete": True,
            "source_hash_matches": log.get("source_sha256") == sha256(source),
            "raw_output_hash_matches": log.get("output_sha256") == sha256(raw),
            "final_has_editorial_changes": sha256(raw) != sha256(final),
            "credential_leak_detected": any(value in log_text for value in sensitive),
        }
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify raw model run provenance")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--final-dir", type=Path, required=True)
    parser.add_argument("--log-dir", type=Path, required=True)
    parser.add_argument("--credentials-file", type=Path)
    args = parser.parse_args()
    result = verify(
        args.input_dir, args.raw_dir, args.final_dir, args.log_dir,
        args.credentials_file,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if any(
        not item.get("complete")
        or not item.get("source_hash_matches")
        or not item.get("raw_output_hash_matches")
        or item.get("credential_leak_detected")
        for item in result.values()
    ):
        sys.exit(1)


if __name__ == "__main__":
    main()
