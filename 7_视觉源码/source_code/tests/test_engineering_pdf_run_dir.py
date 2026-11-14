"""Rendering must target the run directory, never the source PDF's folder.

``load_pdf`` originally inferred the output directory from the PDF location or
an undocumented ``PIPELINE_RUN_DIR`` environment variable. Both violate I2 and
A2 (a single configuration entry point). Stages now pass ``ctx.run_dir``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pypdfium2")

from pypdf import PdfWriter  # noqa: E402 - after importorskip

from pipeline.pdf_input import load_pdf  # noqa: E402


def _blank_pdf(path: Path) -> Path:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with path.open("wb") as stream:
        writer.write(stream)
    return path


def test_render_targets_explicit_run_dir(tmp_path):
    pdf = _blank_pdf(tmp_path / "paper.pdf")
    run_dir = tmp_path / "run"

    info = load_pdf(pdf, run_dir=run_dir, dpi=200)

    expected = run_dir / "input" / "page_0001.png"
    assert info.page_count == 1
    assert info.pages[0].render_path == expected
    assert expected.is_file()
    assert not (tmp_path / "input").exists(), "nothing may be written next to the source PDF"


def test_environment_variable_is_not_a_run_dir_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_RUN_DIR", str(tmp_path / "elsewhere"))
    pdf = _blank_pdf(tmp_path / "paper.pdf")
    run_dir = tmp_path / "run"

    info = load_pdf(pdf, run_dir=run_dir, dpi=200)

    assert info.pages[0].render_path == run_dir / "input" / "page_0001.png"
    assert not (tmp_path / "elsewhere").exists()
