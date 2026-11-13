"""Stable validation issues and pipeline failure categories."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Code(str, Enum):
    SCHEMA_INVALID = "SCHEMA_INVALID"
    SCHEMA_NOT_FOUND = "SCHEMA_NOT_FOUND"
    SCHEMA_VERSION_MISMATCH = "SCHEMA_VERSION_MISMATCH"
    ASSET_MISSING = "ASSET_MISSING"
    ASSET_EMPTY = "ASSET_EMPTY"
    ASSET_NO_VERSION = "ASSET_NO_VERSION"
    PDF_ENCRYPTED = "PDF_ENCRYPTED"
    PDF_EMPTY = "PDF_EMPTY"
    PDF_TOO_LARGE = "PDF_TOO_LARGE"
    PDF_RENDER_FAILED = "PDF_RENDER_FAILED"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_TRUNCATED = "MODEL_TRUNCATED"
    MODEL_INVALID_JSON = "MODEL_INVALID_JSON"
    MODEL_REFUSED = "MODEL_REFUSED"
    IR_MISSING_PAGE = "IR_MISSING_PAGE"
    IR_MISSING_BLOCK_ID = "IR_MISSING_BLOCK_ID"
    IR_PAGE_OUT_OF_RANGE = "IR_PAGE_OUT_OF_RANGE"
    MD_MISSING_SECTION = "MD_MISSING_SECTION"
    MD_BODY_TOO_SHORT = "MD_BODY_TOO_SHORT"
    MD_BODY_TOO_LONG = "MD_BODY_TOO_LONG"
    MD_BAD_ENCODING = "MD_BAD_ENCODING"
    CITE_NOT_IN_EVIDENCE = "CITE_NOT_IN_EVIDENCE"
    EVIDENCE_UNREFERENCED = "EVIDENCE_UNREFERENCED"
    NUMBER_MISMATCH = "NUMBER_MISMATCH"
    UNIT_MISSING = "UNIT_MISSING"
    MATERIAL_MISMATCH = "MATERIAL_MISMATCH"
    SECRET_LEAK = "SECRET_LEAK"
    ABS_PATH_LEAK = "ABS_PATH_LEAK"
    REPAIR_EXHAUSTED = "REPAIR_EXHAUSTED"
    REPAIR_INTRODUCED_FACT = "REPAIR_INTRODUCED_FACT"


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Remove credential-shaped text before it reaches logs or exceptions."""
    value = str(text)
    for secret in secrets:
        if secret:
            value = value.replace(secret, "***")
    value = re.sub(r"sk-[A-Za-z0-9_-]+", "***", value)
    value = re.sub(r"(?i)(bearer\s+)\S+", r"\1***", value)
    value = re.sub(r"(?i)((?:api[_-]?key|token|password)\s*[=:]\s*)[^\s,;&]+", r"\1***", value)
    return re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1***@", value)


@dataclass(frozen=True)
class Issue:
    code: Code
    stage: str
    path: str
    severity: str
    message: str
    hint: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", Code(self.code))
        if self.severity not in {"error", "warning"}:
            raise ValueError("Issue severity must be error or warning")
        for name in ("stage", "path", "message", "hint"):
            object.__setattr__(self, name, redact(getattr(self, name)))

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code.value,
            "stage": self.stage,
            "path": self.path,
            "severity": self.severity,
            "message": self.message,
            "hint": self.hint,
        }


class PipelineError(Exception):
    def __init__(self, code: Code | str, message: str | None = None, hint: str | None = None) -> None:
        self.code = code
        self.message = redact(message or (code.value if isinstance(code, Code) else code))
        self.hint = redact(hint or "")
        super().__init__(self.message)


class ConfigError(PipelineError):
    def __init__(self, message: str = "Configuration error", hint: str | None = None) -> None:
        super().__init__("CONFIG_ERROR", message, hint)


class AuthMissing(PipelineError):
    """Codex has no usable credential.

    Not a ``Code`` enum member: those 30 codes describe validation issues,
    while this is a run-failure class. I7 maps credential/configuration
    failures to exit code 2, so the A1 card's original "exit 8" is wrong
    (8 is reserved for ASSET_*).
    """

    def __init__(self, message: str = "Codex authentication is unavailable", hint: str | None = None) -> None:
        super().__init__("AUTH_MISSING", message, hint)


class SchemaNotFound(PipelineError):
    def __init__(self, message: str = "Schema file not found", hint: str | None = None) -> None:
        super().__init__(Code.SCHEMA_NOT_FOUND, message, hint)


class SchemaVersionMismatch(PipelineError):
    def __init__(self, message: str = "Schema version mismatch", hint: str | None = None) -> None:
        super().__init__(Code.SCHEMA_VERSION_MISMATCH, message, hint)


class AssetMissing(PipelineError):
    def __init__(self, message: str = "Required asset is missing", hint: str | None = None) -> None:
        super().__init__(Code.ASSET_MISSING, message, hint)


class AssetEmpty(PipelineError):
    def __init__(self, message: str = "Required asset is empty", hint: str | None = None) -> None:
        super().__init__(Code.ASSET_EMPTY, message, hint)


class ModelTimeout(PipelineError):
    def __init__(self, message: str = "Model request timed out", hint: str | None = None) -> None:
        super().__init__(Code.MODEL_TIMEOUT, message, hint)


class TruncatedResponse(PipelineError):
    def __init__(self, message: str = "Model response was truncated", hint: str | None = None) -> None:
        super().__init__(Code.MODEL_TRUNCATED, message, hint)


class InvalidModelOutput(PipelineError):
    def __init__(self, message: str = "Model output is invalid", hint: str | None = None) -> None:
        super().__init__(Code.MODEL_INVALID_JSON, message, hint)


class ModelRefused(PipelineError):
    def __init__(self, message: str = "Model refused the request", hint: str | None = None) -> None:
        super().__init__(Code.MODEL_REFUSED, message, hint)


class PdfEncrypted(PipelineError):
    def __init__(self, message: str = "PDF is encrypted", hint: str | None = None) -> None:
        super().__init__(Code.PDF_ENCRYPTED, message, hint)


class PdfEmpty(PipelineError):
    def __init__(self, message: str = "PDF has no pages", hint: str | None = None) -> None:
        super().__init__(Code.PDF_EMPTY, message, hint)


class PdfTooLarge(PipelineError):
    def __init__(self, message: str = "PDF exceeds configured limits", hint: str | None = None) -> None:
        super().__init__(Code.PDF_TOO_LARGE, message, hint)


class PdfRenderFailed(PipelineError):
    def __init__(self, message: str = "PDF page rendering failed", hint: str | None = None) -> None:
        super().__init__(Code.PDF_RENDER_FAILED, message, hint)


class IoFailure(PipelineError):
    def __init__(self, message: str = "I/O operation failed", hint: str | None = None) -> None:
        super().__init__("IO_FAILURE", message, hint)


def exit_code_for(exc: BaseException) -> int:
    if isinstance(exc, AuthMissing):
        return 2
    if isinstance(exc, ConfigError):
        return 2
    if not isinstance(exc, PipelineError):
        return 1
    code = exc.code.value if isinstance(exc.code, Code) else str(exc.code)
    if code.startswith("PDF_"):
        return 3
    if code.startswith("SCHEMA_") or code.startswith("IR_"):
        return 4
    if code.startswith("MODEL_"):
        return 5
    if code.startswith("REPAIR_"):
        return 6
    if code.startswith("ASSET_"):
        return 8
    if code == "IO_FAILURE":
        return 7
    return 1
