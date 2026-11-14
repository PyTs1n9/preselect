"""Produce a deterministic acceptance report for the current source checkout."""

from __future__ import annotations

import importlib.util
import json
import platform
import re
import subprocess
import sys
from hashlib import sha256
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SOURCE_ROOT.parent
REPO_ROOT = PROJECT_ROOT.parent
MATERIALS = PROJECT_ROOT / "materials"
QUALITY = SOURCE_ROOT / "fixtures" / "quality"
REQUIRED_QUALITY_CASES = {"fake_citation", "evidence_unreferenced", "number_mismatch",
                          "unit_missing", "material_mismatch"}


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _tail(value: str, limit: int = 2000) -> str:
    return value[-limit:]


def _git_state() -> tuple[dict, dict]:
    head_result = _run(["git", "rev-parse", "HEAD"], REPO_ROOT)
    head = head_result.stdout.strip() if head_result.returncode == 0 else None
    status_result = _run(["git", "status", "--porcelain=v1"], REPO_ROOT)
    tracked_dirty: list[str] = []
    untracked: list[str] = []
    if status_result.returncode == 0:
        for line in status_result.stdout.splitlines():
            if not line:
                continue
            path = line[3:] if len(line) >= 4 else line
            if line.startswith("??"):
                untracked.append(path)
            else:
                tracked_dirty.append(path)
    else:
        tracked_dirty.append("<git status failed>")
    clean = {
        "ok": status_result.returncode == 0 and not tracked_dirty and not untracked,
        "tracked_dirty": tracked_dirty,
        "untracked": untracked,
        "tracked_dirty_count": len(tracked_dirty),
        "untracked_count": len(untracked),
        "nonzero_count": len(tracked_dirty) + len(untracked),
        "status_returncode": status_result.returncode,
        "status_stderr": _tail(status_result.stderr),
    }
    return {
        "ok": head_result.returncode == 0 and head is not None,
        "head": head,
        "returncode": head_result.returncode,
        "stderr": _tail(head_result.stderr),
    }, clean


def _pytest() -> dict:
    result = _run([sys.executable, "-m", "pytest", "tests", "-q"], SOURCE_ROOT)
    output = result.stdout + result.stderr
    passed_match = re.search(r"(\d+)\s+passed\b", output)
    failed_match = re.search(r"(\d+)\s+failed\b", output)
    xfailed_match = re.search(r"(\d+)\s+xfailed\b", output)
    error_match = re.search(r"(\d+)\s+errors?\b", output)
    errors = int(error_match.group(1)) if error_match else int("ERROR collecting" in output)
    passed = int(passed_match.group(1)) if passed_match else 0
    failed = int(failed_match.group(1)) if failed_match else 0
    xfailed = int(xfailed_match.group(1)) if xfailed_match else 0
    return {
        "ok": result.returncode == 0 and failed == 0 and errors == 0,
        "returncode": result.returncode,
        "passed": passed,
        "failed": failed,
        "xfailed": xfailed,
        "errors": errors,
        "stdout_tail": _tail(result.stdout),
        "stderr_tail": _tail(result.stderr),
    }


def _demo() -> dict:
    pdf = Path("../materials/ges_docs_MED-04.pdf")
    result = _run(
        [sys.executable, "tools/demo.py", "--pdf", str(pdf)],
        SOURCE_ROOT,
    )
    payload = None
    parse_error = None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        parse_error = str(exc)
    valid = payload.get("valid") if isinstance(payload, dict) else None
    han_chars = payload.get("han_chars") if isinstance(payload, dict) else None
    ok = (
        result.returncode == 0
        and valid is True
        and isinstance(han_chars, int)
        and 2500 <= han_chars <= 4000
    )
    return {
        "ok": ok,
        "returncode": result.returncode,
        "valid": valid,
        "han_chars": han_chars,
        "parse_error": parse_error,
        "stdout_tail": _tail(result.stdout),
        "stderr_tail": _tail(result.stderr),
    }


def _materials() -> dict:
    files = sorted(MATERIALS.glob("*.pdf")) if MATERIALS.is_dir() else []
    records = []
    errors = []
    for path in files:
        try:
            digest = sha256(path.read_bytes()).hexdigest()
            records.append({"name": path.name, "bytes": path.stat().st_size, "sha256": digest})
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")
    return {
        "ok": len(files) == 4 and not errors,
        "directory": str(MATERIALS),
        "count": len(files),
        "files": records,
        "errors": errors,
    }


def _interpreter() -> dict:
    """Name the interpreter that produced this report.

    A run under the wrong Python reports a collection error that looks like a
    code defect. Recording the executable and whether the SDK resolves turns
    that into a one-line diagnosis without changing any verdict.
    """
    return {
        "executable": sys.executable,
        "version": platform.python_version(),
        "prefix": sys.prefix,
        "sdk_importable": importlib.util.find_spec("openai_codex") is not None,
    }


def _quality_fixtures() -> dict:
    """I10: missing or malformed B fixtures turn this gate red, not green."""
    cases = sorted(path.name for path in QUALITY.iterdir() if path.is_dir()) if QUALITY.is_dir() else []
    missing = sorted(REQUIRED_QUALITY_CASES - set(cases))
    invalid: list[str] = []
    for case in cases:
        try:
            payload = json.loads((QUALITY / case / "expect.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            invalid.append(f"{case}: {exc}")
            continue
        if not isinstance(payload.get("expect_codes"), list) or not payload["expect_codes"]:
            invalid.append(f"{case}: expect_codes must be a non-empty list")
    return {"ok": not missing and not invalid, "cases": cases,
            "missing": missing, "invalid": invalid}


def main() -> int:
    head, clean = _git_state()
    pytest = _pytest()
    demo = _demo()
    materials = _materials()
    quality = _quality_fixtures()
    report = {
        "head": head,
        "clean": clean,
        "interpreter": _interpreter(),
        "pytest": pytest,
        "demo": demo,
        "materials": materials,
        "quality_fixtures": quality,
    }
    checks = [("head", head["ok"]), ("clean", clean["ok"]), ("pytest", pytest["ok"]),
              ("demo", demo["ok"]), ("materials", materials["ok"]),
              ("quality_fixtures", quality["ok"])]
    ok = all(passed for _, passed in checks)
    failed = [name for name, passed in checks if not passed]
    print(f"verify: {'PASS' if ok else 'FAIL'}; failed={','.join(failed) if failed else 'none'}")
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
