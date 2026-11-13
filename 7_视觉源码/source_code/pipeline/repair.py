"""Bounded repair loop for schema-invalid model output."""

from typing import Any, Callable


def repair(value: dict[str, Any], validate: Callable[[dict[str, Any]], dict[str, Any]], max_attempts: int = 2) -> dict[str, Any]:
    current = value
    for _ in range(max_attempts + 1):
        result = validate(current)
        if result.get("valid"):
            return current
    raise ValueError("Unable to repair document within the configured attempt limit")
