"""Schema validation for normalized document artifacts."""

from typing import Any


def validate_document(document: dict[str, Any]) -> dict[str, Any]:
    required = ("document_id", "source", "pages", "blocks")
    errors = [f"missing field: {name}" for name in required if name not in document]
    return {"valid": not errors, "errors": errors}
