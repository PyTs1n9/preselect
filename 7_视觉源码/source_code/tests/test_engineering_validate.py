"""A9 rule tests; business JSON mappings remain owned by the frozen contract."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from pipeline.contracts import ValidationReport
from pipeline.errors import Code, Issue, PipelineError
from pipeline.schema_loader import validate_instance
from pipeline.settings import load_settings
from pipeline.validate import (
    _check_markdown,
    _check_sensitive,
    _write_report,
    count_han,
    validate_run,
)


def _codes(issues):
    return {issue.code for issue in issues}


def _document():
    # Fourteen visible Han characters after removing the entire source section.
    return (
        "# 星辰\n\n## 引言\n前言。\n\n## 正文\n研究。\n\n"
        "## 总结\n结论。\n\n## 来源\n[1] 数据记录。\n"
    )


@pytest.fixture
def settings():
    resolved = load_settings(require_secret=False)
    return replace(
        resolved,
        pipeline=replace(resolved.pipeline, body_min_han=1, body_max_han=100),
    )


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        pytest.param("中文，。！？ＡＢ12abc", 2, id="punctuation-and-latin"),
        pytest.param("# 标题\n**正文** _汉字_ ~~删除~~", 8, id="visible-formatting"),
        pytest.param(
            "[链接](https://example.test/中文) ![图示](assets/文字.png)",
            4,
            id="links-and-image-alt",
        ),
        pytest.param(
            "`代码` 和 <span title='隐藏'>文字</span><!-- 注释 -->",
            5,
            id="inline-code-and-html",
        ),
        pytest.param("前文\n```python\n隐藏汉字\n```\n后文", 4, id="fenced-code"),
        pytest.param(
            "保留\n## 来源\n资料文本\n### 附录\n更多资料\n## 继续\n后续",
            6,
            id="source-subsections-and-following-section",
        ),
        pytest.param("汉\U00020000\U0002a700\U00030000", 4, id="supplementary-han"),
        pytest.param(
            "| 项目 | 数量 |\n| --- | --- |\n| 中文 | 英文 |",
            8,
            id="table-visible-text",
        ),
    ],
)
def test_count_han_visible_markdown(markdown, expected):
    assert count_han(markdown) == expected


def test_non_html_angle_brackets_keep_visible_han():
    assert count_han("正文<研究结果>") == 6
    assert count_han('<span title="隐藏>属性">正文</span>') == 2


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_markdown_valid_sections_and_encoding(settings, newline):
    raw = _document().replace("\n", newline).encode("utf-8")
    issues, body, han_chars = _check_markdown(raw, settings, "draft.md")
    assert issues == []
    assert han_chars == 14
    assert "研究" in body
    assert "数据记录" not in body


@pytest.mark.parametrize("heading", ["# 星辰", "## 引言", "## 正文", "## 总结", "## 来源"])
def test_missing_required_heading_fails(settings, heading):
    raw = _document().replace(heading, "").encode("utf-8")
    issues, _, _ = _check_markdown(raw, settings, "draft.md")
    assert Code.MD_MISSING_SECTION in _codes(issues)


def test_empty_section_is_not_present_content(settings):
    raw = _document().replace("前言。", "  \n").encode("utf-8")
    issues, _, _ = _check_markdown(raw, settings, "draft.md")
    assert Code.MD_MISSING_SECTION in _codes(issues)


def test_empty_nested_blockquote_does_not_supply_section_content(settings):
    raw = _document().replace("[1] 数据记录。", ">>").encode("utf-8")
    issues, _, _ = _check_markdown(raw, settings, "draft.md")
    assert Code.MD_MISSING_SECTION in _codes(issues)


def test_heading_in_code_does_not_satisfy_section(settings):
    markdown = _document().replace("## 引言", "```text\n## 引言\n```")
    issues, _, _ = _check_markdown(markdown.encode("utf-8"), settings, "draft.md")
    assert Code.MD_MISSING_SECTION in _codes(issues)


def test_plain_heading_word_does_not_satisfy_section(settings):
    markdown = _document().replace("## 引言", "引言")
    issues, _, _ = _check_markdown(markdown.encode("utf-8"), settings, "draft.md")
    assert Code.MD_MISSING_SECTION in _codes(issues)


@pytest.mark.parametrize("problem", ["invalid-utf8", "bom", "mixed-newlines"])
def test_bad_markdown_encoding_becomes_issue(settings, problem):
    raw = _document().encode("utf-8")
    if problem == "invalid-utf8":
        raw += b"\xff"
    elif problem == "bom":
        raw = b"\xef\xbb\xbf" + raw
    else:
        raw = raw.replace(b"\n", b"\r\n", 1)
    issues, _, _ = _check_markdown(raw, settings, "draft.md")
    assert Code.MD_BAD_ENCODING in _codes(issues)


@pytest.mark.parametrize(
    ("minimum", "maximum", "expected"),
    [
        pytest.param(14, 14, None, id="inclusive-boundaries"),
        pytest.param(15, 100, Code.MD_BODY_TOO_SHORT, id="configured-minimum"),
        pytest.param(1, 13, Code.MD_BODY_TOO_LONG, id="configured-maximum"),
    ],
)
def test_length_uses_settings_and_visible_han(settings, minimum, maximum, expected):
    configured = replace(
        settings,
        pipeline=replace(settings.pipeline, body_min_han=minimum, body_max_han=maximum),
    )
    issues, _, han_chars = _check_markdown(_document().encode("utf-8"), configured, "draft.md")
    assert han_chars == 14
    if expected is None:
        assert issues == []
    else:
        assert expected in _codes(issues)


@pytest.mark.parametrize("shape", ["sk-prefix", "api-key", "quoted-api-key"])
def test_secret_issue_does_not_echo_credential(shape):
    token = "a" * 28 + "012345"
    if shape == "sk-prefix":
        token = "sk-" + token
        text = "credential: " + token
    elif shape == "api-key":
        text = "API_KEY=" + token
    else:
        text = json.dumps({"api_key": token})
    issues = _check_sensitive(text, "draft.md")
    assert Code.SECRET_LEAK in _codes(issues)
    output = json.dumps([issue.to_dict() for issue in issues], ensure_ascii=False)
    if token in output:
        pytest.fail("Credential content leaked into validation output", pytrace=False)


@pytest.mark.parametrize("shape", ["sk-prefix", "api-key", "bearer"])
def test_credentials_adjacent_to_chinese_are_detected(shape):
    token = "a" * 30
    if shape == "sk-prefix":
        token = "sk-" + token
        text = "密钥" + token
    elif shape == "api-key":
        text = "密钥API_KEY=" + token
    else:
        text = "凭据Bearer " + token
    issues = _check_sensitive(text, "draft.md")
    assert Code.SECRET_LEAK in _codes(issues)
    if token in json.dumps([issue.to_dict() for issue in issues]):
        pytest.fail("Credential content leaked into validation output", pytrace=False)


@pytest.mark.parametrize(
    "path",
    [r"D:\research\input.pdf", "/Users/research/input.pdf", "/home/research/input.pdf"],
    ids=["windows", "macos", "linux"],
)
def test_absolute_path_leak(path):
    issues = _check_sensitive("文件位置：" + path, "draft.md")
    assert Code.ABS_PATH_LEAK in _codes(issues)


def test_relative_paths_and_web_links_are_allowed():
    assert _check_sensitive("assets/page.png https://example.test/paper", "draft.md") == []


def _make_run(run_dir):
    artifacts = {
        "recognition.json": {"pages": []},
        "document_ir.json": {"document_id": "engineering", "source": {}, "pages": [], "blocks": []},
        "evidence.json": {
            "material_id": "engineering",
            "facts": [{
                "fact_id": "F001", "core": True, "page_number": [1], "block_id": ["B001"],
                "quote": "工程文本", "value": None, "unit": None,
            }],
        },
    }
    for name, payload in artifacts.items():
        (run_dir / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (run_dir / "draft.md").write_bytes(_document().encode("utf-8"))
    return run_dir


def test_report_matches_persisted_schema_and_declares_skipped_checks(tmp_path, settings):
    run_dir = _make_run(tmp_path)
    report = validate_run(run_dir, settings)
    payload = json.loads((run_dir / "validation.json").read_text(encoding="utf-8"))
    assert report.valid is True
    assert report.issues == []
    assert report.schema_version == "1.1.0"
    assert report.stats["han_chars"] == 14
    assert report.stats["evidence_count"] == 1
    assert report.stats["citations"] == 0
    assert report.stats["unreferenced_evidence"] == 0
    assert set(report.stats) == {"han_chars", "citations", "evidence_count", "unreferenced_evidence"}
    assert set(report.checks) == {"enabled", "skipped"}
    assert set(report.checks["skipped"]) == {
        "CITE_NOT_IN_EVIDENCE", "EVIDENCE_UNREFERENCED", "NUMBER_MISMATCH",
        "UNIT_MISSING", "MATERIAL_MISMATCH",
    }
    assert len(report.checks["skipped"]) == 5
    assert not set(report.checks["enabled"]) & set(report.checks["skipped"])
    assert {
        "SCHEMA_INVALID", "ASSET_MISSING", "MD_MISSING_SECTION", "MD_BODY_TOO_SHORT",
        "MD_BODY_TOO_LONG", "MD_BAD_ENCODING", "SECRET_LEAK", "ABS_PATH_LEAK",
    } <= set(report.checks["enabled"])
    assert payload == report.to_dict()
    assert validate_instance("validation", payload) == []


@pytest.mark.parametrize("name", ["recognition.json", "document_ir.json", "evidence.json"])
@pytest.mark.parametrize("problem", ["missing", "malformed", "wrong-shape", "invalid-utf8"])
def test_each_bad_json_becomes_reported_error(tmp_path, settings, name, problem):
    run_dir = _make_run(tmp_path)
    artifact = run_dir / name
    if problem == "missing":
        artifact.unlink()
    elif problem == "malformed":
        artifact.write_bytes(b"{")
    elif problem == "wrong-shape":
        artifact.write_bytes(b"{}")
    else:
        artifact.write_bytes(b"\xff")
    report = validate_run(run_dir, settings)
    assert report.valid is False
    assert _codes(report.issues) & {Code.SCHEMA_INVALID, Code.ASSET_MISSING}
    assert any(name in issue.path for issue in report.issues)
    assert report.stats["evidence_count"] == (0 if name == "evidence.json" else 1)
    payload = json.loads((run_dir / "validation.json").read_text(encoding="utf-8"))
    assert validate_instance("validation", payload) == []


def test_final_is_preferred_over_invalid_draft(tmp_path, settings):
    run_dir = _make_run(tmp_path)
    (run_dir / "draft.md").write_bytes(b"\xff")
    (run_dir / "final.md").write_bytes(_document().encode("utf-8"))
    report = validate_run(run_dir, settings)
    assert report.valid is True
    assert report.issues == []


def test_invalid_final_cannot_fall_back_to_valid_draft(tmp_path, settings):
    run_dir = _make_run(tmp_path)
    (run_dir / "final.md").write_bytes(b"\xff")
    report = validate_run(run_dir, settings)
    assert report.valid is False
    assert Code.MD_BAD_ENCODING in _codes(report.issues)
    assert any("final.md" in issue.path for issue in report.issues)


def test_directory_named_final_cannot_fall_back_to_draft(tmp_path, settings):
    run_dir = _make_run(tmp_path)
    (run_dir / "final.md").mkdir()
    report = validate_run(run_dir, settings)
    assert report.valid is False
    assert any(issue.code == Code.ASSET_MISSING and issue.path == "final.md" for issue in report.issues)


def test_missing_markdown_returns_failed_report(tmp_path, settings):
    run_dir = _make_run(tmp_path)
    (run_dir / "draft.md").unlink()
    report = validate_run(run_dir, settings)
    assert report.valid is False
    assert Code.ASSET_MISSING in _codes(report.issues)
    assert (run_dir / "validation.json").is_file()


@pytest.mark.parametrize("location", ["code", "comment", "source", "metadata", "link-target"])
def test_secret_scan_covers_original_markdown_without_echoing_secret(tmp_path, settings, location):
    run_dir = _make_run(tmp_path)
    credential = "sk-" + "a" * 30
    if location == "code":
        markdown = "```text\n" + credential + "\n```\n" + _document()
    elif location == "comment":
        markdown = "<!-- " + credential + " -->\n" + _document()
    elif location == "metadata":
        markdown = "---\nnote: " + credential + "\n---\n" + _document()
    elif location == "link-target":
        markdown = _document() + "[资料](https://example.test/" + credential + ")\n"
    else:
        markdown = _document() + credential + "\n"
    (run_dir / "draft.md").write_bytes(markdown.encode("utf-8"))
    report = validate_run(run_dir, settings)
    assert report.valid is False
    assert Code.SECRET_LEAK in _codes(report.issues)
    payload_text = (run_dir / "validation.json").read_text(encoding="utf-8")
    if credential in payload_text:
        pytest.fail("Credential content leaked into persisted validation output", pytrace=False)


def test_unwritable_report_target_raises_pipeline_error(tmp_path, settings):
    run_dir = _make_run(tmp_path)
    (run_dir / "validation.json").mkdir()
    with pytest.raises(PipelineError) as caught:
        validate_run(run_dir, settings)
    assert caught.value.code == "IO_FAILURE"


def test_validation_preserves_input_artifacts(tmp_path, settings):
    run_dir = _make_run(tmp_path)
    originals = {path.name: path.read_bytes() for path in run_dir.iterdir()}
    validate_run(run_dir, settings)
    assert {name: (run_dir / name).read_bytes() for name in originals} == originals


@pytest.mark.parametrize(
    "raw",
    [
        b'{"pages": [], "extra": NaN}',
        b'{"pages": [], "extra": Infinity}',
        b'{"pages": [], "extra": -Infinity}',
        b'{"pages": [], "pages": []}',
    ],
    ids=["nan", "positive-infinity", "negative-infinity", "duplicate-key"],
)
def test_nonstandard_json_cannot_pass_permissive_schema(tmp_path, settings, raw):
    run_dir = _make_run(tmp_path)
    (run_dir / "recognition.json").write_bytes(raw)
    report = validate_run(run_dir, settings)
    assert report.valid is False
    assert any(issue.code == Code.SCHEMA_INVALID and "recognition.json" in issue.path for issue in report.issues)


def test_warning_is_preserved_in_valid_written_report(tmp_path, settings):
    base = validate_run(_make_run(tmp_path), settings)
    warning = Issue(Code.SCHEMA_INVALID, "validate", "recognition.json", "warning", "Engineering warning")
    report = ValidationReport(
        valid=True,
        schema_version=base.schema_version,
        checked_at=base.checked_at,
        stats=base.stats,
        issues=[warning],
        checks=base.checks,
    )
    _write_report(tmp_path, report)
    payload = json.loads((tmp_path / "validation.json").read_text(encoding="utf-8"))
    assert payload["valid"] is True
    assert payload["issues"] == [warning.to_dict()]
    assert validate_instance("validation", payload) == []


@pytest.mark.parametrize("invalid_case", ["true-with-error", "false-without-error"])
def test_report_selfcheck_rejects_invalid_validity_without_replacing_output(tmp_path, settings, invalid_case):
    base = validate_run(_make_run(tmp_path), settings)
    original = (tmp_path / "validation.json").read_bytes()
    if invalid_case == "true-with-error":
        issues = [Issue(Code.SCHEMA_INVALID, "validate", "recognition.json", "error", "Engineering error")]
        report = replace(base, valid=True, issues=issues)
    else:
        report = replace(base, valid=False, issues=[])
    with pytest.raises(PipelineError) as caught:
        _write_report(tmp_path, report)
    assert caught.value.code == Code.SCHEMA_INVALID
    assert (tmp_path / "validation.json").read_bytes() == original
    assert list(tmp_path.glob(".validation-*.tmp")) == []
