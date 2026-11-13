"""Normalize provider responses into the document IR shape."""

from typing import Any


def normalize_response(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": raw.get("document_id", ""),
        "source": raw.get("source", {}),
        "pages": raw.get("pages", []),
        "blocks": raw.get("blocks", []),
    }
