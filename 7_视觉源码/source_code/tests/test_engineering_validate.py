"""A9/A14 deterministic validation contracts, without replacing the validator."""

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
        "<!-- material_id: engineering -->\n"
        "# 星辰\n\n## 引言\n前言。\n\n## 正文\n研究[F001]。\n\n"
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
        "document_ir.json": {
            "document_id": "engineering",
            "source": {"path_name": None, "page_count": None, "sha256": None},
            "pages": [], "blocks": [],
        },
        "evidence.json": {
            "material_id": "engineering",
            "facts": [{
                "fact_id": "F001", "core": True, "page_number": [1], "block_id": ["B001"],
                "quote": "工程文本", "value": None, "unit": None, "value_text": None,
                "region": None,
            }],
        },
    }
    for name, payload in artifacts.items():
        (run_dir / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (run_dir / "draft.md").write_bytes(_document().encode("utf-8"))
    return run_dir


def test_report_matches_persisted_schema_and_enables_consistency_checks(tmp_path, settings):
    run_dir = _make_run(tmp_path)
    report = validate_run(run_dir, settings)
    payload = json.loads((run_dir / "validation.json").read_text(encoding="utf-8"))
    assert report.valid is True
    assert report.issues == []
    assert report.schema_version == "1.1.0"
    assert report.stats["han_chars"] == 14
    assert report.stats["evidence_count"] == 1
    assert report.stats["citations"] == 1
    assert report.stats["unreferenced_evidence"] == 0
    assert set(report.stats) == {"han_chars", "citations", "evidence_count", "unreferenced_evidence"}
    assert set(report.checks) == {"enabled", "skipped"}
    assert report.checks["skipped"] == []
    assert len(report.checks["skipped"]) == 0
    assert not set(report.checks["enabled"]) & set(report.checks["skipped"])
    assert {
        "SCHEMA_INVALID", "ASSET_MISSING", "MD_MISSING_SECTION", "MD_BODY_TOO_SHORT",
        "MD_BODY_TOO_LONG", "MD_BAD_ENCODING", "SECRET_LEAK", "ABS_PATH_LEAK",
        "CITE_NOT_IN_EVIDENCE", "EVIDENCE_UNREFERENCED", "NUMBER_MISMATCH",
        "UNIT_MISSING", "MATERIAL_MISMATCH",
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
    assert report.checks["skipped"] == []
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
    assert report.checks["skipped"] == []
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


def test_strict_output_rules_are_checked_locally():
    """The provider rejects non-strict schemas; the loader must reject them first."""

    from pipeline.schema_loader import _strict_issues

    compliant = {"type": "object", "additionalProperties": False, "required": ["a"],
                 "properties": {"a": {"type": "string"}}}
    assert _strict_issues(compliant) == []
    assert any("additionalProperties" in issue
               for issue in _strict_issues({"type": "object", "properties": {"a": {"type": "string"}}}))
    assert any("missing: b" in issue
               for issue in _strict_issues({"type": "object", "additionalProperties": False,
                                            "required": ["a"], "properties": {"a": {}, "b": {}}}))
    assert any("oneOf" in issue for issue in _strict_issues({"oneOf": [{"type": "string"}]}))


@pytest.mark.parametrize("name", ["recognition", "document_ir", "evidence", "table", "figure", "formula"])
def test_model_output_schemas_are_strict_compatible(name):
    from pipeline.schema_loader import _strict_issues, load_schema

    assert _strict_issues(load_schema(name)) == []


CONSISTENCY_CODES = {Code.CITE_NOT_IN_EVIDENCE, Code.EVIDENCE_UNREFERENCED,
                     Code.NUMBER_MISMATCH, Code.UNIT_MISSING, Code.MATERIAL_MISMATCH}


def _fact(fact_id="F001", *, core=True, value=None, value_text=None, unit=None):
    return {"fact_id": fact_id, "core": core, "page_number": [1], "block_id": ["B001"],
            "quote": "工程原文", "value": value, "value_text": value_text, "unit": unit,
            "region": None}


def _consistency_run(path, body, facts=None):
    path.mkdir(parents=True, exist_ok=True)
    _make_run(path)
    evidence = {"material_id": "engineering", "facts": [_fact()] if facts is None else facts}
    (path / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
    (path / "draft.md").write_text(_document().replace("研究[F001]。", body), encoding="utf-8")
    return path


@pytest.mark.parametrize("header", [
    "", "<!-- material_id: another -->", "<!-- material_id: invalid id -->",
    "<!-- material_id: engineering --> trailing", "<!-- other: engineering -->",
])
def test_material_comment_must_be_complete_first_nonempty_line(tmp_path, settings, header):
    run = _make_run(tmp_path)
    text = _document().replace("<!-- material_id: engineering -->", header)
    (run / "draft.md").write_text(text, encoding="utf-8")
    report = validate_run(run, settings)
    assert _codes(report.issues) & CONSISTENCY_CODES == {Code.MATERIAL_MISMATCH}
    assert not report.valid and report.checks["skipped"] == []


def test_blank_lines_before_material_comment_are_allowed(tmp_path, settings):
    run = _make_run(tmp_path)
    (run / "draft.md").write_text("\n \t\n" + _document(), encoding="utf-8")
    report = validate_run(run, settings)
    assert report.valid and report.issues == []


@pytest.mark.parametrize(("manifest_id", "expected"), [
    ("engineering", None), ("another", Code.MATERIAL_MISMATCH),
])
def test_material_id_checks_optional_manifest_when_present(tmp_path, settings, manifest_id, expected):
    run = _make_run(tmp_path)
    (run / "manifest.json").write_text(json.dumps({"material_id": manifest_id}), encoding="utf-8")
    report = validate_run(run, settings)
    assert _codes(report.issues) & CONSISTENCY_CODES == (set() if expected is None else {expected})
    assert report.valid is (expected is None)


@pytest.mark.parametrize("raw", [
    b"{", b"\xff", b"[]", b"{}", b'{"material_id":null}', b'{"material_id":42}',
    b'{"material_id":""}', b'{"material_id":"invalid id"}',
    b'{"material_id":"engineering","material_id":"engineering"}',
    b'{"material_id":"engineering","extra":NaN}',
])
def test_bad_optional_manifest_fails_explicitly(tmp_path, settings, raw):
    run = _make_run(tmp_path)
    (run / "manifest.json").write_bytes(raw)
    report = validate_run(run, settings)
    assert Code.SCHEMA_INVALID in _codes(report.issues)
    assert any(issue.code == Code.SCHEMA_INVALID and "manifest.json" in issue.path for issue in report.issues)
    assert not report.valid and report.checks["skipped"] == []


def test_unreadable_manifest_is_not_treated_as_absent(tmp_path, settings):
    run = _make_run(tmp_path)
    (run / "manifest.json").mkdir()
    report = validate_run(run, settings)
    assert Code.SCHEMA_INVALID in _codes(report.issues)
    assert not report.valid


@pytest.mark.parametrize("extra", [
    "\n<!-- 999 [F999] -->", "\n```text\n999 [F999]\n```", "\n    999 [F999]",
    "\n`999 [F999]`", "\n[资料](https://example.test/999/[F999])",
    "\n[隐藏]: https://example.test/999/[F999]", "\n## 附录\n999 [F999]",
    "\n### 小标题 [F999]\n没有其他引用。",
])
def test_citation_scope_excludes_nonprose_content(tmp_path, settings, extra):
    run = _consistency_run(tmp_path, "研究[F001]。" + extra)
    report = validate_run(run, settings)
    assert _codes(report.issues) & CONSISTENCY_CODES == set()
    assert report.stats["citations"] == 1
    assert report.stats["unreferenced_evidence"] == 0


def test_title_and_entire_source_section_are_excluded(tmp_path, settings):
    run = _make_run(tmp_path)
    text = _document().replace("# 星辰", "# 星辰 [F999]").replace(
        "[1] 数据记录。", "[F999] page=999 block=B999 quote=999\n### 引言\n999 [F998]")
    (run / "draft.md").write_text(text, encoding="utf-8")
    report = validate_run(run, settings)
    assert _codes(report.issues) & CONSISTENCY_CODES == set()
    assert report.stats["citations"] == 1


def test_citations_in_all_body_sections_and_child_prose_count(tmp_path, settings):
    run = _make_run(tmp_path)
    text = _document().replace("前言。", "前言[F001]。").replace(
        "研究[F001]。", "研究[F001]。\n### 细节\n观察[F001]。").replace("结论。", "结论[F001]。")
    (run / "draft.md").write_text(text, encoding="utf-8")
    report = validate_run(run, settings)
    assert report.valid and report.issues == []
    assert report.stats["citations"] == 4


def test_unknown_reference_and_unreferenced_core_are_independent_errors(tmp_path, settings):
    facts = [_fact(), _fact("F002", core=False)]
    run = _consistency_run(tmp_path, "未知事实[F999]。", facts)
    report = validate_run(run, settings)
    assert _codes(report.issues) & CONSISTENCY_CODES == {Code.CITE_NOT_IN_EVIDENCE, Code.EVIDENCE_UNREFERENCED}
    assert report.stats["citations"] == 0
    assert report.stats["unreferenced_evidence"] == 1
    assert report.stats["evidence_count"] == 1
    assert all(issue.severity == "error" for issue in report.issues)


def test_repeated_valid_markers_count_but_unknown_markers_do_not(tmp_path, settings):
    run = _consistency_run(tmp_path, "事实[F001][F001]。未知[F999]。")
    report = validate_run(run, settings)
    assert _codes(report.issues) & CONSISTENCY_CODES == {Code.CITE_NOT_IN_EVIDENCE}
    assert report.stats["citations"] == 2
    assert report.stats["unreferenced_evidence"] == 0


@pytest.mark.parametrize("marker", ["[f001]", "[F01]", "[F 001]"])
def test_citation_tokens_require_exact_case_digits_and_brackets(tmp_path, settings, marker):
    run = _consistency_run(tmp_path, "标记" + marker + "。", [_fact(core=False)])
    report = validate_run(run, settings)
    assert _codes(report.issues) & CONSISTENCY_CODES == set()
    assert report.stats["citations"] == 0


@pytest.mark.parametrize("separator", ["。", "！", "？", "；", "\n"])
def test_numbers_in_uncited_sentences_are_outside_comparison(tmp_path, settings, separator):
    run = _consistency_run(tmp_path, "年份2024" + separator + "事实[F001]。")
    report = validate_run(run, settings)
    assert report.valid and report.issues == []


@pytest.mark.parametrize(("body", "fact"), [
    ("读数 92% [F001]。", _fact(value=92, value_text="92%", unit="%")),
    ("读数 -5.25 mg [F001]。", _fact(value=-5.25, unit="mg")),
    ("读数 +3 kg [F001]。", _fact(value=3, unit="kg")),
    ("读数 1,234.50kg [F001]。", _fact(value=1234.5, unit="kg")),
    ("读数 1.25e3 kg [F001]。", _fact(value=1250, unit="kg")),
    ("读数 80–90% [F001]。", _fact(value={"min": 80, "max": 90}, value_text="80–90%", unit="%")),
    ("读数 3 kg 与 4 m [F001]。", _fact(value=[3, 4], unit=["kg", "m"])),
    ("读数 7.50 mg [F001]。", _fact(value_text="7.5 mg", unit="mg")),
    ("读数 92 [F001]。", _fact(value=92)),
])
def test_numeric_values_and_units_match_without_invented_conversions(tmp_path, settings, body, fact):
    run = _consistency_run(tmp_path, body, [fact])
    report = validate_run(run, settings)
    assert report.valid and report.issues == []
    assert report.stats["citations"] == 1


@pytest.mark.parametrize(("body", "fact"), [
    ("读数 91% [F001]。", _fact(value=92, unit="%")),
    ("读数 192% [F001]。", _fact(value=92, unit="%")),
    ("读数 9.2% [F001]。", _fact(value=92, unit="%")),
    ("读数 0.92 [F001]。", _fact(value=92, unit="%")),
    ("读数 85% [F001]。", _fact(value={"min": 80, "max": 90}, unit="%")),
    ("读数 12 [F001]。", _fact()),
])
def test_unmatched_number_reports_number_only(tmp_path, settings, body, fact):
    run = _consistency_run(tmp_path, body, [fact])
    report = validate_run(run, settings)
    assert _codes(report.issues) & CONSISTENCY_CODES == {Code.NUMBER_MISMATCH}
    assert not report.valid


@pytest.mark.parametrize(("body", "fact"), [
    ("读数 92 [F001]。", _fact(value=92, unit="%")),
    ("读数 92 另处有百分号 % [F001]。", _fact(value=92, unit="%")),
    ("读数 5mg [F001]。", _fact(value=5, unit="m")),
    ("读数 5 M [F001]。", _fact(value=5, unit="m")),
    ("读数 3 m 与 4 kg [F001]。", _fact(value=[3, 4], unit=["kg", "m"])),
])
def test_matching_number_with_missing_or_wrong_unit_reports_unit_only(tmp_path, settings, body, fact):
    run = _consistency_run(tmp_path, body, [fact])
    report = validate_run(run, settings)
    assert _codes(report.issues) & CONSISTENCY_CODES == {Code.UNIT_MISSING}
    assert not report.valid


def test_number_lookup_is_limited_to_facts_cited_in_the_same_sentence(tmp_path, settings):
    facts = [_fact(value=12), _fact("F002", core=False, value=99)]
    run = _consistency_run(tmp_path, "读数 99 [F001]。", facts)
    report = validate_run(run, settings)
    assert _codes(report.issues) & CONSISTENCY_CODES == {Code.NUMBER_MISMATCH}


def test_multiple_cited_facts_can_supply_matching_value_and_unit(tmp_path, settings):
    run = _consistency_run(tmp_path, "读数 3 kg [F001][F002]。",
                           [_fact(value=3, unit="m"), _fact("F002", value=3, unit="kg")])
    report = validate_run(run, settings)
    assert report.valid and report.issues == []
    assert report.stats["citations"] == 2


def test_whitespace_after_sentence_punctuation_keeps_citation_with_preceding_numbers(tmp_path, settings):
    run = _consistency_run(tmp_path, "读数 92%。 \t[F001]", [_fact(value=91, unit="%")])
    report = validate_run(run, settings)
    assert report.stats["citations"] == 1
    assert _codes(report.issues) & CONSISTENCY_CODES == {Code.NUMBER_MISMATCH}


def test_repeated_unknown_citation_has_one_fixed_path_issue(tmp_path, settings):
    run = _consistency_run(tmp_path, "未知[F999] [F999]。", [_fact(core=False)])
    report = validate_run(run, settings)
    citations = [issue for issue in report.issues if issue.code == Code.CITE_NOT_IN_EVIDENCE]
    assert len(citations) == 1
    assert citations[0].path == "final.md#cite"
    assert report.stats["citations"] == 0


@pytest.mark.parametrize("body", ["范围 80-90% [F001]。", "范围 80%–90% [F001]。"])
def test_range_notation_accepts_ascii_hyphen_and_per_endpoint_percent(tmp_path, settings, body):
    run = _consistency_run(tmp_path, body,
                           [_fact(value={"min": 80, "max": 90}, value_text="80–90%", unit="%")])
    report = validate_run(run, settings)
    assert report.valid and report.issues == []
    assert report.stats["citations"] == 1


def test_invalid_thousands_grouping_does_not_match_fact_value(tmp_path, settings):
    run = _consistency_run(tmp_path, "计数 1,23 [F001]。", [_fact(value=123)])
    report = validate_run(run, settings)
    assert _codes(report.issues) & CONSISTENCY_CODES == {Code.NUMBER_MISMATCH}


def test_reference_style_citation_target_is_not_a_body_citation(tmp_path, settings):
    run = _consistency_run(tmp_path, "标签 [label][F001]。", [_fact(core=False)])
    report = validate_run(run, settings)
    assert _codes(report.issues) & CONSISTENCY_CODES == set()
    assert report.stats["citations"] == 0


def test_unpairable_value_and_unit_arrays_fail_as_structure_error(tmp_path, settings):
    run = _consistency_run(tmp_path, "读数 3 kg [F001]。", [_fact(value=[3, 4], unit=["kg"])])
    report = validate_run(run, settings)
    assert Code.SCHEMA_INVALID in _codes(report.issues)
    assert not report.valid and report.checks["skipped"] == []


def test_schema_validation_keeps_json_number_semantics_for_integer_valued_float(tmp_path, settings):
    run = _make_run(tmp_path)
    (run / "recognition.json").write_text(
        json.dumps({"pages": [{"page_number": 1.0, "blocks": []}]}), encoding="utf-8")
    report = validate_run(run, settings)
    assert not any(issue.code == Code.SCHEMA_INVALID and "recognition.json" in issue.path
                   for issue in report.issues)


def test_digits_inside_compound_unit_are_not_standalone_fact_values(tmp_path, settings):
    fact = _fact(value=5, value_text="5 m^2", unit="m^2")
    matching = _consistency_run(tmp_path / "matching", "面积 5 m^2 [F001]。", [fact])
    report = validate_run(matching, settings)
    assert report.valid and report.issues == []

    mismatching = _consistency_run(tmp_path / "mismatching", "面积 2 m^2 [F001]。", [fact])
    report = validate_run(mismatching, settings)
    assert _codes(report.issues) & CONSISTENCY_CODES == {Code.NUMBER_MISMATCH}
    assert not report.valid


def test_decimal_comparison_does_not_round_to_float_precision(tmp_path, settings):
    run = _consistency_run(tmp_path, "测量 1.0000000000000000001 [F001]。",
                           [_fact(value_text="1.0000000000000000000")])
    report = validate_run(run, settings)
    assert _codes(report.issues) & CONSISTENCY_CODES == {Code.NUMBER_MISMATCH}
    assert not report.valid


def test_english_comma_after_number_is_sentence_punctuation(tmp_path, settings):
    run = _consistency_run(tmp_path, "读数 92, [F001]。", [_fact(value=92)])
    report = validate_run(run, settings)
    assert report.valid and report.issues == []


def test_arbitrary_exponent_is_checked_and_han_prefix_cannot_hide_digits(tmp_path, settings):
    for index, body in enumerate(("表达 x^99 [F001]。", "读数-99 [F001]。")):
        run = _consistency_run(tmp_path / str(index), body)
        report = validate_run(run, settings)
        assert _codes(report.issues) & CONSISTENCY_CODES == {Code.NUMBER_MISMATCH}


def test_postpunctuation_citation_cannot_cross_section_boundary(tmp_path, settings):
    run = _make_run(tmp_path)
    text = _document().replace("前言。", "前言 999。").replace("研究[F001]。", "[F001]研究。")
    (run / "draft.md").write_text(text, encoding="utf-8")
    report = validate_run(run, settings)
    assert report.valid and report.issues == []


def test_new_issue_messages_are_fixed_and_never_echo_artifact_values(tmp_path, settings):
    signatures = []
    for index, suffix in enumerate(("alpha", "beta")):
        wrong_number = 9827364 + index
        matching_number = 7823641 + index
        facts = [_fact(value=matching_number, unit="mg"), _fact("F002")]
        facts[0]["quote"] = "private-quote-" + suffix
        run = _consistency_run(tmp_path / suffix,
                               f"读数 {wrong_number} [F001]。另一读数 {matching_number} [F001]。未知[F999]。", facts)
        text = (run / "draft.md").read_text(encoding="utf-8").replace(
            "material_id: engineering", "material_id: private-material-" + suffix)
        (run / "draft.md").write_text(text, encoding="utf-8")
        report = validate_run(run, settings)
        new = [issue for issue in report.issues if issue.code in CONSISTENCY_CODES]
        assert {issue.code for issue in new} == CONSISTENCY_CODES
        assert all(issue.severity == "error" for issue in new)
        serialized = json.dumps([issue.to_dict() for issue in new], ensure_ascii=False)
        for value in (str(wrong_number), str(matching_number), "private-quote-" + suffix,
                      "private-material-" + suffix):
            assert value not in serialized
        signatures.append(sorted((issue.code.value, issue.message, issue.hint) for issue in new))
    assert signatures[0] == signatures[1]
