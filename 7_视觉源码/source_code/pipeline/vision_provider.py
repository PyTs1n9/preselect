"""Vision provider abstraction and provider selection.

Production uses the Codex SDK; offline demos use local replay fixtures.
Stages depend on the protocol rather than either concrete adapter.
"""

from __future__ import annotations

from typing import Any, Protocol

from .contracts import ModelRequest, ModelResult
from .errors import ConfigError


class VisionProvider(Protocol):
    def generate(self, request: ModelRequest) -> ModelResult: ...

    def close(self) -> None: ...


def get_provider(name: str, model: str | None = None, **options: Any) -> VisionProvider:
    if name == "replay":
        import json
        from pathlib import Path

        from .errors import PipelineError
        from .replay_provider import ReplayProvider

        settings = options.get("settings")
        if settings is None:
            raise ConfigError("Replay provider requires settings")
        material_id = None
        if options.get("run_dir") is not None:
            try:
                manifest = json.loads((Path(options["run_dir"]) / "manifest.json").read_text(encoding="utf-8"))
                material_id = manifest["material_id"]
            except (OSError, UnicodeError, ValueError, KeyError, TypeError):
                raise PipelineError("STAGE_FAILED", "Replay run material_id cannot be read") from None
            if not isinstance(material_id, str) or not material_id:
                raise PipelineError("STAGE_FAILED", "Replay run material_id is invalid")
        return ReplayProvider(settings.paths.fixtures_dir, material_id=material_id)
    if name in {"openai_codex", "codex"}:
        from .codex_adapter import CodexAdapter

        return CodexAdapter(model=model, **options)
    raise ConfigError(
        f"Unsupported vision provider: {name}",
        hint="Only 'openai_codex' is supported; this project drives Codex via the SDK.",
    )


__all__ = ["VisionProvider", "get_provider"]
