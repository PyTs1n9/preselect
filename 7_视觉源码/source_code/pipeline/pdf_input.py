"""Deterministic PDF inspection and mandatory PNG page rendering."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .contracts import PageInfo, PdfInfo
from .errors import (
    ConfigError,
    IoFailure,
    PdfEmpty,
    PdfEncrypted,
    PdfRenderFailed,
    PdfTooLarge,
)
from .settings import load_settings


_MAX_FILE_SIZE = 20 * 1024 * 1024
_MAX_PAGE_COUNT = 50
_HASH_CHUNK_SIZE = 1024 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(_HASH_CHUNK_SIZE):
                digest.update(chunk)
    except OSError as exc:
        raise IoFailure(f"Unable to read PDF: {path.name}") from exc
    return digest.hexdigest()


def _render_required() -> bool:
    """Rendering is mandatory, not a capability-driven branch.

    The Codex SDK accepts ``LocalImageInput`` / ``ImageInput`` only; it has no
    PDF input type. Keeping this explicit stops a future reader from
    reintroducing a "skip rendering" path.
    """

    return True


def _default_run_dir(pdf_path: Path) -> Path:
    """Fallback for direct callers only; stages must pass ``run_dir``.

    Reading an environment variable here would create a second configuration
    channel outside ``load_settings`` (A2 forbids that) and silently disagree
    with I2.
    """

    if pdf_path.parent.name == "input":
        return pdf_path.parent.parent
    return pdf_path.parent


def _dpi() -> int:
    try:
        return load_settings(require_secret=False).vision.dpi
    except ConfigError:
        return 200


def _text_layer(page: Any) -> bool:
    try:
        text = page.extract_text()
    except Exception:
        return False
    return bool(text and text.strip())


def _render_pages(pdf_path: Path, page_infos: list[PageInfo], run_dir: Path, dpi: int) -> list[PageInfo]:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise PdfRenderFailed("pypdfium2 is required for PNG rendering") from exc

    output_dir = run_dir / "input"
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        document = pdfium.PdfDocument(str(pdf_path))
    except Exception as exc:
        raise PdfRenderFailed("Unable to open PDF for rendering") from exc

    rendered: list[PageInfo] = []
    try:
        for index, info in enumerate(page_infos):
            page = None
            bitmap = None
            render_path = output_dir / f"page_{info.page_number:04d}.png"
            try:
                page = document.get_page(index)
                bitmap = page.render(scale=dpi / 72.0)
                image = bitmap.to_pil()
                image.save(render_path, format="PNG")
            except Exception as exc:
                raise PdfRenderFailed(
                    f"Unable to render PDF page {info.page_number}"
                ) from exc
            finally:
                if bitmap is not None:
                    bitmap.close()
                if page is not None:
                    page.close()
            rendered.append(
                PageInfo(
                    page_number=info.page_number,
                    width_pt=info.width_pt,
                    height_pt=info.height_pt,
                    rotation=info.rotation,
                    has_text_layer=info.has_text_layer,
                    render_path=render_path,
                )
            )
    finally:
        document.close()
    return rendered


def load_pdf(path: Path, run_dir: Path | None = None) -> PdfInfo:
    """Inspect ``path`` and render every page to PNG.

    The Codex SDK cannot ingest PDF bytes, so rendering always happens.
    ``run_dir`` is where ``input/page_%04d.png`` is written; pipeline callers
    must pass ``ctx.run_dir`` so nothing lands next to the source PDF (I2).
    """

    pdf_path = Path(path)
    if not pdf_path.is_file():
        raise IoFailure(f"PDF file not found: {pdf_path.name}")
    try:
        file_size = pdf_path.stat().st_size
    except OSError as exc:
        raise IoFailure(f"Unable to stat PDF: {pdf_path.name}") from exc
    if file_size > _MAX_FILE_SIZE:
        raise PdfTooLarge("PDF exceeds the 20 MiB file limit")

    digest = _sha256_file(pdf_path)
    try:
        reader = PdfReader(str(pdf_path), strict=False)
    except Exception as exc:
        raise PdfRenderFailed("Unable to parse PDF") from exc
    if reader.is_encrypted:
        raise PdfEncrypted("Encrypted PDFs are not accepted")
    try:
        page_count = len(reader.pages)
    except Exception as exc:
        raise PdfRenderFailed("Unable to enumerate PDF pages") from exc
    if page_count == 0:
        raise PdfEmpty("PDF contains zero pages")
    if page_count > _MAX_PAGE_COUNT:
        raise PdfTooLarge("PDF exceeds the 50 page limit")

    pages: list[PageInfo] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            mediabox = page.mediabox
            width = float(mediabox.width)
            height = float(mediabox.height)
            rotation = int(page.get("/Rotate", 0) or 0) % 360
        except Exception as exc:
            raise PdfRenderFailed(f"Unable to inspect PDF page {number}") from exc
        pages.append(
            PageInfo(
                page_number=number,
                width_pt=width,
                height_pt=height,
                rotation=rotation,
                has_text_layer=_text_layer(page),
                render_path=None,
            )
        )

    if _render_required():
        pages = _render_pages(pdf_path, pages, Path(run_dir) if run_dir is not None else _default_run_dir(pdf_path), _dpi())
    return PdfInfo(
        sha256=digest,
        page_count=page_count,
        file_size=file_size,
        encrypted=False,
        pages=pages,
    )


__all__ = ["load_pdf"]
