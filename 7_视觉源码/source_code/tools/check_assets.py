"""Read-only I4 audit: literal regex matches, not inferred runtime usage.

Only stdout is used (one JSON object). No pipeline modules are imported.
Exit codes follow I7: 0 aligned, 8 asset drift, 7 read error, 4 uncheckable
contract/source syntax. A dynamic load_prompt(name, ...) is resolved only when
the name comes from a literal dict such as ``_VISUAL_PROMPTS``; anything else
is reported as uncheckable rather than guessed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]
PROMPT_CALL = re.compile(
    r"\bload_prompt\s*\(\s*(['\"])(?P<name>[a-z][a-z0-9_]*)\1\s*[,)]"
)
# ``_VISUAL_PROMPTS = {"scan_page": "vision", ...}`` is a literal table, so the
# values are as binding as a direct call argument. Keys are page kinds, not
# prompt names, and are deliberately not collected.
VISUAL_PROMPTS = re.compile(r"(?ms)^_VISUAL_PROMPTS\s*=\s*\{(?P<body>[^}]*)\}")
DICT_VALUE = re.compile(
    r"[\"'][a-z][a-z0-9_]*[\"']\s*:\s*[\"'](?P<name>[a-z][a-z0-9_]*)[\"']"
)
SKILL_CALL = re.compile(r"\bload_skill\s*\(\s*(?P<argument>[^)\r\n]+)")
SKILL_PATH = re.compile(
    r"ctx\.settings\.paths\.skills_dir"
    r"(?:\s*/\s*(?:\"[^\"\r\n]+\"|'[^'\r\n]+'))+\s*"
)
QUOTED = re.compile(r"(['\"])(?P<value>[^'\"\r\n]+)\1")
VERSION = re.compile(r"<!-- version: [0-9]+\.[0-9]+\.[0-9]+ -->")
FRONTMATTER_VERSION = re.compile(r"^\s*(?:metadata\.)?version:\s*\S+")
I4_SECTION = re.compile(r"(?ms)^## 4\. I4\b.*?(?=^## |\Z)")
ASSET_PATH = re.compile(r"`(source_code/(?:prompts|skills)/[^`]+)`")
BUCKETS = ("missing", "extra", "doc_mismatch", "version_issues")


def check_assets(source_root: Path = SOURCE_ROOT) -> dict:
    report = {"ok": False, **{key: [] for key in BUCKETS}}
    project_root = source_root.parent

    def label(path: Path) -> str:
        return path.relative_to(project_root).as_posix()

    def issue(bucket: str, code: str, path: Path, message: str, **details) -> None:
        report[bucket].append(
            {"code": code, "path": label(path), "message": message, **details}
        )

    def read(path: Path, bucket: str = "doc_mismatch") -> str | None:
        try:
            return path.read_bytes().decode("utf-8")
        except FileNotFoundError:
            issue(bucket, "ASSET_MISSING", path, "Required audit input is missing")
        except (OSError, UnicodeError):
            # Do not echo exception text or file contents (credentials/host paths).
            issue(bucket, "IO_FAILURE", path, "Audit input is not readable UTF-8")
        return None

    def files(directory: Path, pattern: str) -> list[Path]:
        try:
            if not directory.is_dir():
                issue("doc_mismatch", "ASSET_MISSING", directory,
                      "Required audit directory is missing")
                return []
            return sorted(path for path in directory.rglob(pattern) if path.is_file())
        except OSError:
            issue("doc_mismatch", "IO_FAILURE", directory,
                  "Unable to enumerate audit directory")
            return []

    prompt_names: set[str] = set()
    skill_paths: set[str] = set()
    sources = {}
    for path in files(source_root / "pipeline", "*.py"):
        text = read(path)
        if text is None:
            continue
        sources[path] = text
        prompt_names.update(match["name"] for match in PROMPT_CALL.finditer(text))
        for table in VISUAL_PROMPTS.finditer(text):
            prompt_names.update(match["name"] for match in DICT_VALUE.finditer(table["body"]))
        for match in SKILL_CALL.finditer(text):
            prefix = text[text.rfind("\n", 0, match.start()) + 1:match.start()]
            if re.search(r"\bdef\s+$", prefix):
                continue
            argument = match["argument"].strip()
            line = text.count("\n", 0, match.start()) + 1
            if not SKILL_PATH.fullmatch(argument):
                issue("doc_mismatch", "SCHEMA_INVALID", path,
                      "load_skill path is outside the supported literal regex form",
                      line=line)
                continue
            parts = [item["value"] for item in QUOTED.finditer(argument)]
            relative = Path("skills", *parts)
            if relative.is_absolute() or ".." in relative.parts or ":" in relative.as_posix():
                issue("doc_mismatch", "SCHEMA_INVALID", path,
                      "load_skill path is not a safe source-relative path", line=line)
                continue
            skill_paths.add("source_code/" + relative.as_posix())

    prompt_dir = source_root / "prompts"
    prompt_files = files(prompt_dir, "*")
    actual_names = set()
    for path in prompt_files:
        relative = path.relative_to(prompt_dir).as_posix()
        actual_names.add(relative[:-3] if relative.endswith(".md") else relative)
        text = read(path, "version_issues")
        if text is not None and not VERSION.fullmatch(text.splitlines()[0] if text else ""):
            issue("version_issues", "ASSET_NO_VERSION", path,
                  "First line must be <!-- version: x.y.z -->", line=1)
    report["missing"] = sorted(prompt_names - actual_names)
    report["extra"] = sorted(actual_names - prompt_names)

    doc = project_root / "docs" / "接口冻结清单_A-B.md"
    text = read(doc)
    declared_prompts: set[str] = set()
    declared_skills: set[str] = set()
    if text is not None:
        section = I4_SECTION.search(text)
        if section is None:
            issue("doc_mismatch", "SCHEMA_INVALID", doc, "I4 section is missing")
        else:
            for row in section[0].splitlines():
                if row.lstrip().startswith("|"):
                    for asset in ASSET_PATH.findall(row):
                        if asset.startswith("source_code/prompts/") and asset.endswith(".md"):
                            declared_prompts.add(asset[len("source_code/prompts/"):-3])
                        elif asset.startswith("source_code/skills/"):
                            declared_skills.add(asset)
            if not declared_prompts or not declared_skills:
                issue("doc_mismatch", "SCHEMA_INVALID", doc,
                      "I4 asset table must declare prompt and Skill paths")
            for kind, declared, observed in (
                ("i4_vs_literal_prompt_calls", declared_prompts, prompt_names),
                ("i4_vs_prompt_files", declared_prompts, actual_names),
                ("i4_vs_literal_skill_paths", declared_skills, skill_paths),
            ):
                if declared != observed:
                    issue("doc_mismatch", "ASSET_MISSING", doc,
                          "I4 declaration differs from the observed set", kind=kind,
                          declared_only=sorted(declared - observed),
                          observed_only=sorted(observed - declared))

    loader = source_root / "pipeline" / "prompt_loader.py"
    loader_text = sources.get(loader)
    if loader_text is None:
        issue("doc_mismatch", "ASSET_MISSING", loader, "Prompt loader is missing")
    elif "_FRONTMATTER_VERSION" not in loader_text:
        issue("doc_mismatch", "SCHEMA_INVALID", loader,
              "load_skill does not parse a frontmatter version")
    for asset in sorted(skill_paths | declared_skills):
        path = project_root / asset
        text = read(path, "version_issues")
        if text is None:
            continue
        lines = text.splitlines()
        first = lines[0] if lines else ""
        if first == "---":
            # Agent Skills requires frontmatter on line 1, so the version has to
            # live inside that block; the loader reads it from there.
            if not any(FRONTMATTER_VERSION.match(line) for line in lines[1:]):
                issue("version_issues", "ASSET_NO_VERSION", path,
                      "SKILL.md frontmatter declares no version", line=1,
                      contract=label(doc))
        elif not VERSION.fullmatch(first):
            issue("version_issues", "ASSET_NO_VERSION", path,
                  "First line must be <!-- version: x.y.z -->", line=1)

    report["ok"] = all(not report[key] for key in BUCKETS)
    return report


def main() -> int:
    report = check_assets()
    # Avoid the Windows GBK console error without changing asset bytes.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    codes = {item.get("code") for key in ("doc_mismatch", "version_issues")
             for item in report[key]}
    if "IO_FAILURE" in codes:
        return 7
    if "SCHEMA_INVALID" in codes:
        return 4
    return 0 if report["ok"] else 8


if __name__ == "__main__":
    raise SystemExit(main())
