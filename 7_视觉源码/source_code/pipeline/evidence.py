"""Helpers for attaching source evidence to extracted claims."""

from typing import Any


def attach_evidence(claim: dict[str, Any], page_number: int, **region: int) -> dict[str, Any]:
    result = dict(claim)
    result["evidence"] = {"page_number": page_number, "region": region}
    return result
