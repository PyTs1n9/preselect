"""PDF input inspection with optional pypdf support."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def load_pdf(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"page_count": 0, "pages": [], "warning": "pypdf is not installed"}
    reader = PdfReader(str(path))
    pages = [{"page_number": index + 1} for index in range(len(reader.pages))]
    return {"page_count": len(pages), "pages": pages}
