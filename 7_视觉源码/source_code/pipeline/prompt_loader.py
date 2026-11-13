"""W0 content-asset loader boundary; asset behavior is implemented by A8."""

from pathlib import Path

from .contracts import PromptAsset, SkillAsset


def load_prompt(name: str, base_dir: Path) -> PromptAsset:
    raise NotImplementedError("Prompt loading is assigned to A8")


def load_skill(path: Path) -> SkillAsset:
    raise NotImplementedError("Skill loading is assigned to A8")
