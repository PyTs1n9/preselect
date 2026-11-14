"""I8 manifest shape and safe deterministic serialization."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from .schema_loader import SCHEMA_DIR
from .settings import Settings

_DEPENDENCIES = ("jsonschema", "pypdf", "python-dotenv", "openai-codex", "pypdfium2", "pillow")


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


def asset_digests(settings: Settings) -> dict:
    """Hash every loadable asset so a B-side edit is visible in the manifest.

    I8 requires this for prompts, skills and schemas. Missing directories yield
    empty maps instead of raising: the run still fails later with ASSET_MISSING
    at the point of use, which keeps the diagnosis at the failing stage.
    """
    prompts: dict[str, str] = {}
    prompts_dir = settings.paths.prompts_dir
    if prompts_dir.is_dir():
        for path in sorted(prompts_dir.glob("*.md")):
            prompts[path.stem] = file_sha256(path)
    skills: dict[str, str] = {}
    skills_dir = settings.paths.skills_dir
    if skills_dir.is_dir():
        for path in sorted(skills_dir.glob("*/SKILL.md")):
            skills[path.parent.name] = file_sha256(path)
    schemas: dict[str, str] = {}
    if SCHEMA_DIR.is_dir():
        for path in sorted(SCHEMA_DIR.glob("*.json")):
            schemas[path.name.removesuffix(".schema.json")] = file_sha256(path)
    return {"prompts": prompts, "skills": skills, "schemas": schemas}


def _git_commit(source_root: Path) -> str | None:
    try:
        completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source_root,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    commit = completed.stdout.strip()
    return commit if completed.returncode == 0 and commit else None


def code_provenance() -> dict:
    """Record the commit and per-file hashes; the hashes survive a stripped clone."""
    source_root = Path(__file__).resolve().parents[1]
    files: dict[str, str] = {}
    for path in sorted((source_root / "pipeline").rglob("*.py")):
        files[path.relative_to(source_root).as_posix()] = file_sha256(path)
    return {"git_commit": _git_commit(source_root), "files": files}


def dependency_versions() -> dict:
    versions: dict[str, str] = {}
    for name in _DEPENDENCIES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return versions


def cache_key(manifest: dict) -> str | None:
    """A12 key: input + model + every asset digest. Any change must miss."""
    input_sha = (manifest.get("input") or {}).get("sha256")
    if not input_sha:
        return None
    model = manifest.get("model") or {}
    parts = [input_sha, str(model.get("model") or ""), str(model.get("wire_api") or "")]
    assets = manifest.get("assets") or {}
    for group in ("prompts", "skills", "schemas"):
        digests = assets.get(group) or {}
        for name in sorted(digests):
            parts.append(f"{group}:{name}:{digests[name]}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def enrich_manifest(manifest: dict, settings: Settings) -> dict:
    """Fill the provenance blocks exactly once, when the run file is created."""
    manifest["assets"] = asset_digests(settings)
    manifest["code"] = code_provenance()
    manifest["deps"] = dependency_versions()
    manifest["cache"]["key"] = cache_key(manifest)
    return manifest


def call_counts(manifest: dict) -> dict:
    """I8 calls map: stage name to the number of model calls it made."""
    counts: dict[str, int] = {}
    for stage in manifest.get("stages") or []:
        if not isinstance(stage, dict) or "name" not in stage:
            continue
        counts[str(stage["name"])] = len(stage.get("model_calls") or [])
    return counts
