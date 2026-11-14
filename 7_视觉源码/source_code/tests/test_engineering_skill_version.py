"""I4 skill loading: version comes from frontmatter, never from line 1.

Agent Skills requires ``---`` on line 1, which rules out the prompt-style
``<!-- version: -->`` comment. These tests pin both accepted forms so a later
contract edit cannot silently drop one of them.
"""

from __future__ import annotations

import pytest

from pipeline.errors import AssetEmpty
from pipeline.prompt_loader import load_skill


def _write(tmp_path, text: str):
    path = tmp_path / "SKILL.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_frontmatter_version_is_read(tmp_path):
    path = _write(tmp_path, "---\nname: demo\ndescription: d\nversion: 1.2.3\n---\n\n# Body\n")
    assert load_skill(path).version == "1.2.3"


def test_metadata_version_is_read(tmp_path):
    path = _write(tmp_path, "---\nname: demo\nmetadata:\n  version: 2.0.0\n---\n\n# Body\n")
    assert load_skill(path).version == "2.0.0"


def test_html_comment_version_is_still_accepted(tmp_path):
    path = _write(tmp_path, "<!-- version: 0.9.0 -->\n\n# Body\n")
    assert load_skill(path).version == "0.9.0"


def test_missing_version_is_none_not_invented(tmp_path):
    path = _write(tmp_path, "---\nname: demo\ndescription: d\n---\n\n# Body\n")
    assert load_skill(path).version is None


def test_empty_skill_fails_instead_of_loading(tmp_path):
    path = _write(tmp_path, "   \n\n")
    with pytest.raises(AssetEmpty):
        load_skill(path)
