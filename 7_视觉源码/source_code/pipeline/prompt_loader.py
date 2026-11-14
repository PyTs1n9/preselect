"""I4 read-only asset loading; production assets have no built-in fallback."""

import re
from pathlib import Path

from .contracts import PromptAsset, SkillAsset
from .errors import AssetEmpty, AssetMissing, IoFailure
from .manifest import file_sha256


def _read_asset(path: Path) -> tuple[str, str]:
    label = f"{path.parent.name}/{path.name}"
    try:
        text = path.read_bytes().decode("utf-8-sig")
        digest = file_sha256(path)
    except FileNotFoundError:
        raise AssetMissing(f"Required asset is missing: {label}") from None
    except (OSError, UnicodeError):
        raise IoFailure(f"Unable to read UTF-8 asset: {label}") from None
    return text, digest


def load_prompt(name: str, base_dir: Path) -> PromptAsset:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise AssetMissing("Invalid prompt name")
    try:
        base = Path(base_dir).resolve()
        path = (base / f"{name}.md").resolve()
    except OSError:
        raise IoFailure("Unable to resolve prompt path") from None
    if not path.is_relative_to(base):
        raise AssetMissing("Prompt must be inside the configured prompt directory")
    text, digest = _read_asset(path)
    if not text.strip():
        raise AssetEmpty(f"Required prompt is empty: {base.name}/{name}.md")
    version = re.fullmatch(r"<!--\s*version:\s*(\S+)\s*-->", text.splitlines()[0])
    return PromptAsset(text, digest, path, version.group(1) if version else None)


def load_skill(path: Path) -> SkillAsset:
    try:
        path = Path(path).resolve()
    except OSError:
        raise IoFailure("Unable to resolve skill path") from None
    text, digest = _read_asset(path)
    return SkillAsset(text, digest, path)
