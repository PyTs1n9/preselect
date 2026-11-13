"""Adapter boundary for Codex or another vision-capable model."""

from typing import Any


class CodexAdapter:
    """Small provider-neutral interface used by the pipeline."""

    def __init__(self, model: str, **options: Any) -> None:
        self.model = model
        self.options = options

    def extract(self, pages: list[dict[str, Any]], prompt: str) -> dict[str, Any]:
        raise NotImplementedError("Configure a concrete vision adapter before extraction")
