"""Local fixture provider for exercising the pipeline without model calls."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .contracts import ModelRequest, ModelResult
from .errors import AssetMissing, PipelineError


_PLACEHOLDER = re.compile(r"{{\s*([^{}]*?)\s*}}")
_MATERIAL_ID = re.compile(r"[A-Za-z0-9._-]+")


class ReplayProvider:
    def __init__(self, root: Path, *, material_id: str | None = None) -> None:
        self.root = Path(root)
        self.material_id = material_id

    def _value(self, name: str, payload: dict) -> str:
        if name == "material_id":
            value = payload.get("material_id", self.material_id)
            if (not isinstance(value, str) or not _MATERIAL_ID.fullmatch(value)
                    or (self.material_id is not None and value != self.material_id)):
                raise PipelineError("STAGE_FAILED", "Replay material_id cannot be resolved")
        elif name == "page_number":
            try:
                value = (payload["page_number"] if "page_number" in payload
                         else payload["pages"][0]["page_number"])
            except (KeyError, IndexError, TypeError):
                raise PipelineError("STAGE_FAILED", "Replay page_number cannot be resolved") from None
            if type(value) is not int or value < 1:
                raise PipelineError("STAGE_FAILED", "Replay page_number must be a positive integer")
        else:
            raise PipelineError("STAGE_FAILED", "Unknown replay placeholder")
        return str(value)

    def generate(self, request: ModelRequest) -> ModelResult:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", request.prompt_name):
            raise PipelineError("STAGE_FAILED", "Invalid replay prompt name")
        if not isinstance(request.payload, dict):
            raise PipelineError("STAGE_FAILED", "Replay payload must be an object")
        try:
            normalized = json.dumps(request.payload, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise PipelineError("STAGE_FAILED", "Replay payload cannot be normalized") from None

        for suffix in (".json", ".md"):
            try:
                text = (self.root / (request.prompt_name + suffix)).read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            except (OSError, UnicodeError):
                raise PipelineError("STAGE_FAILED", "Replay output cannot be read as UTF-8") from None
            break
        else:
            raise AssetMissing(f"Replay output is missing: {request.prompt_name}")

        text = _PLACEHOLDER.sub(lambda match: self._value(match[1], request.payload), text)
        if "{{" in text or "}}" in text:
            raise PipelineError("STAGE_FAILED", "Replay output has an unresolved placeholder")
        if not text.strip():
            raise PipelineError("STAGE_FAILED", "Replay output is empty")
        try:
            parsed = json.loads(text) if suffix == ".json" else None
        except (ValueError, RecursionError):
            raise PipelineError("STAGE_FAILED", "Replay output is not valid JSON") from None

        return ModelResult(
            text=text, parsed=parsed, model="replay", turn_status="completed",
            attempts=1, latency_ms=0.0, usage={}, raw={"replay": True},
            truncated=False, request_hash=hashlib.sha256(normalized).hexdigest(),
        )

    def close(self) -> None:
        pass
