"""Vision provider abstraction and provider selection."""

from typing import Any, Protocol


class VisionProvider(Protocol):
    def extract(self, pages: list[dict[str, Any]], prompt: str) -> dict[str, Any]: ...


def get_provider(name: str, model: str, **options: Any) -> VisionProvider:
    if name == "openai":
        from .codex_adapter import CodexAdapter

        return CodexAdapter(model, **options)
    raise ValueError(f"Unsupported vision provider: {name}")
