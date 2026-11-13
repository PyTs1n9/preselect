"""Vision provider abstraction and provider selection.

Only one provider exists by design: the Codex SDK. The protocol stays here so
stages depend on an interface rather than the concrete adapter.
"""

from __future__ import annotations

from typing import Any, Protocol

from .contracts import ModelRequest, ModelResult
from .errors import ConfigError


class VisionProvider(Protocol):
    def generate(self, request: ModelRequest) -> ModelResult: ...

    def close(self) -> None: ...


def get_provider(name: str, model: str | None = None, **options: Any) -> VisionProvider:
    if name in {"openai_codex", "codex"}:
        from .codex_adapter import CodexAdapter

        return CodexAdapter(model=model, **options)
    raise ConfigError(
        f"Unsupported vision provider: {name}",
        hint="Only 'openai_codex' is supported; this project drives Codex via the SDK.",
    )


__all__ = ["VisionProvider", "get_provider"]
