"""Codex SDK boundary: read-only thread per call, audited raw output.

The only model path in this project is:

    CodexAdapter -> openai-codex SDK -> Codex app-server -> Codex Agent
                 -> pdf-vision Skill -> reviewer model

No HTTP client is used here. PDF pages must already be PNG files; the SDK has
no PDF input type.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from .contracts import ModelRequest, ModelResult
from .errors import (
    AuthMissing,
    ConfigError,
    InvalidModelOutput,
    ModelRefused,
    ModelTimeout,
    PipelineError,
    TruncatedResponse,
    redact,
)
from .settings import Settings, load_settings

PROVIDER_ID = "llm"
SECRET_ENV = "LLM_API_KEY"


def _active_secret(settings: Settings) -> str:
    """The key value handed to the app-server child process, never to a log.

    ``env_key`` in the provider config only names the variable; the value must
    exist in the environment of the spawned app-server. ``CodexConfig.env`` is
    passed to ``subprocess.Popen``, so the secret never touches ``os.environ``
    and is never written to disk.
    """

    try:
        value = settings.vision.api_key.get_secret_value()
    except AttributeError:
        return ""
    return value or ""


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(child) for child in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _jsonable(model_dump(mode="json"))
        except TypeError:
            return _jsonable(model_dump())
    return str(value)


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "stage")
    return cleaned.strip("._") or "stage"


def _status_name(status: Any) -> str:
    return str(getattr(status, "value", None) or status or "unknown")


class CodexAdapter:
    """Wraps one Codex app-server process for a single pipeline run."""

    def __init__(
        self,
        model: str | None = None,
        settings: Settings | None = None,
        run_dir: Path | None = None,
        **options: Any,
    ) -> None:
        self._settings = settings or load_settings(require_secret=False)
        self._model = model or self._settings.vision.model or ""
        self._run_dir = Path(run_dir) if run_dir is not None else self._settings.paths.result_dir
        self._timeout_s = float(options.get("timeout_s", self._settings.vision.timeout_s))
        self._max_retries = int(options.get("max_retries", self._settings.vision.max_retries))
        self._secret = _active_secret(self._settings)
        self._codex: Any | None = None
        self._closed = False
        self._interrupt_error: str | None = None

    # -- lifecycle ---------------------------------------------------------

    def _ensure_codex(self) -> Any:
        if self._codex is not None:
            return self._codex
        try:
            from openai_codex import Codex, CodexConfig
        except ImportError as exc:
            raise ConfigError(
                "openai-codex is required: pip install openai-codex",
                hint="This project uses the Codex SDK, not the openai HTTP SDK.",
            ) from exc
        self._codex = Codex(
            config=CodexConfig(
                config_overrides=self._config_overrides(),
                env={SECRET_ENV: self._secret} if self._secret else None,
            )
        )
        return self._codex

    def _config_overrides(self) -> tuple[str, ...]:
        base_url = (self._settings.vision.base_url or "").strip()
        if not base_url:
            return ()
        if '"' in base_url or "\n" in base_url:
            raise ConfigError("vision.base_url contains characters unsafe for TOML")
        wire_api = (self._settings.vision.wire_api or "responses").strip()
        return (
            f'model_provider="{PROVIDER_ID}"',
            f'model_providers.{PROVIDER_ID}.name="LLM"',
            f'model_providers.{PROVIDER_ID}.base_url="{base_url}"',
            f'model_providers.{PROVIDER_ID}.env_key="{SECRET_ENV}"',
            f'model_providers.{PROVIDER_ID}.wire_api="{wire_api}"',
        )

    def close(self) -> None:
        if self._codex is None or self._closed:
            self._closed = True
            return
        try:
            self._codex.close()
        finally:
            self._closed = True
            self._codex = None

    def __enter__(self) -> "CodexAdapter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- request shaping ---------------------------------------------------

    def _request_hash(self, request: ModelRequest) -> str:
        payload = {
            "stage": request.stage,
            "prompt_name": request.prompt_name,
            "prompt_text": request.prompt_text,
            "payload": request.payload,
            "json_schema": request.json_schema,
            "images": [str(path) for path in request.images or []],
            "skill": list(request.skill) if request.skill else None,
            "timeout_s": request.timeout_s,
            "model": request.model,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _build_inputs(self, request: ModelRequest) -> list[Any]:
        from openai_codex import LocalImageInput, SkillInput, TextInput

        items: list[Any] = []
        if request.skill is not None:
            name, path = request.skill
            items.append(SkillInput(name=name, path=str(path)))
        for image in request.images or []:
            path = Path(image)
            if not path.is_file():
                raise InvalidModelOutput(f"Image input not found: {path.name}")
            items.append(LocalImageInput(path=str(path)))
        text = request.prompt_text
        if request.payload:
            payload_text = json.dumps(request.payload, ensure_ascii=False, sort_keys=True, default=str)
            text = f"{text}\n\n{payload_text}"
        items.append(TextInput(text=text))
        return items

    # -- execution ---------------------------------------------------------

    def _raw_path(self, request: ModelRequest, attempt: int) -> Path:
        return self._run_dir / "raw" / f"{_safe_component(request.stage)}_{attempt}.json"

    def _write_raw(self, request: ModelRequest, attempt: int, result: Any) -> dict:
        payload = {
            "turn_id": getattr(result, "id", None),
            "status": _status_name(getattr(result, "status", None)),
            "error": _jsonable(getattr(result, "error", None)),
            "duration_ms": getattr(result, "duration_ms", None),
            "started_at": getattr(result, "started_at", None),
            "completed_at": getattr(result, "completed_at", None),
            "usage": _jsonable(getattr(result, "usage", None)),
            "items": [_jsonable(item) for item in (getattr(result, "items", None) or [])],
            "final_response": getattr(result, "final_response", None),
        }
        text = redact(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), (self._secret,)
        )
        path = self._raw_path(request, attempt)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            parsed = json.loads(text)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PipelineError("IO_FAILURE", "Unable to persist raw model response") from exc
        return parsed if isinstance(parsed, dict) else {"response": parsed}

    def _interrupt_safely(self, turn: Any) -> None:
        # The timer may fire after the turn already completed; that race is
        # expected, so the failure is recorded rather than re-raised here.
        try:
            turn.interrupt()
        except Exception as exc:  # noqa: BLE001 - interrupt is best-effort cleanup
            self._interrupt_error = redact(f"{type(exc).__name__}: {exc}", (self._secret,))[:200]

    def _run_turn(self, request: ModelRequest, inputs: list[Any], timeout_s: float, attempt: int) -> tuple[Any, dict]:
        from openai_codex import Sandbox

        overrides = self._config_overrides()
        start_kwargs: dict[str, Any] = {"sandbox": Sandbox.read_only, "ephemeral": True}
        model = request.model or self._model
        if model:
            start_kwargs["model"] = model
        if overrides:
            start_kwargs["model_provider"] = PROVIDER_ID
        thread = self._ensure_codex().thread_start(**start_kwargs)
        kwargs: dict[str, Any] = {}
        if request.json_schema is not None:
            kwargs["output_schema"] = request.json_schema
        turn = thread.turn(inputs, **kwargs)
        timer = threading.Timer(timeout_s, self._interrupt_safely, args=(turn,))
        timer.daemon = True
        timer.start()
        turn_started = time.perf_counter()
        try:
            result = turn.run()
        except Exception as exc:
            # No TurnResult exists: record the failed attempt, not a fabricated response.
            try:
                message = redact(str(exc), (self._secret, self._settings.vision.base_url or ""))
                message = re.sub(r"https?://[^\s<>\"']+", "[endpoint]", message, flags=re.IGNORECASE)
                # Paths may contain spaces, so redact their remaining line as well.
                message = re.sub(r"(?i)(?:[a-z]:[\\/]|\\|/)[^\r\n]*",
                                 "[path]", message)
                payload = {
                    "stage": request.stage, "attempt": attempt, "turn_status": "failed",
                    "error_type": type(exc).__name__, "error_message": message[:200],
                    "final_response": None, "items": [], "usage": {},
                    "duration_ms": (time.perf_counter() - turn_started) * 1000.0,
                    "timeout_s": timeout_s,
                }
                path = self._raw_path(request, attempt)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                # Audit failure must not replace the original SDK exception or its mapping.
                exc.add_note("Unable to persist failed model attempt audit")
            raise
        finally:
            timer.cancel()
        return result, self._write_raw(request, attempt, result)

    def _classify(self, exc: BaseException) -> PipelineError:
        message = redact(f"{type(exc).__name__}: {exc}", (self._secret,))[:200]
        lowered = message.lower()
        if "timeout" in lowered or "timed out" in lowered:
            return ModelTimeout(message)
        # Word boundaries keep "author" or "authors" from being read as auth.
        # A missing provider secret surfaces as a plain RuntimeError from the
        # runtime, so the credential wording is matched explicitly: I7 maps
        # AUTH_MISSING to exit 2, while a generic MODEL_API_ERROR would map to 5.
        if re.search(
            r"\b(auth|login|unauthorized)\b|401|missing environment variable|"
            r"no api key|not logged in|invalid api key",
            lowered,
        ):
            return AuthMissing(
                f"Codex authentication unavailable: {message}",
                hint="Provide LLM_API_KEY / LLM_BASE_URL, or configure a working Codex provider.",
            )
        return PipelineError("MODEL_API_ERROR", message)

    def generate(self, request: ModelRequest) -> ModelResult:
        try:
            from openai_codex import is_retryable_error
        except ImportError as exc:
            raise ConfigError("openai-codex is required: pip install openai-codex") from exc

        request_hash = self._request_hash(request)
        timeout_s = float(request.timeout_s if request.timeout_s is not None else self._timeout_s)
        inputs = self._build_inputs(request)
        started = time.perf_counter()
        last_error: BaseException | None = None

        for attempt in range(1, self._max_retries + 2):
            self._interrupt_error = None
            try:
                result, raw = self._run_turn(request, inputs, timeout_s, attempt)
                status = _status_name(getattr(result, "status", None))
                error_obj = getattr(result, "error", None)
                error_message = (
                    redact(str(getattr(error_obj, "message", "") or ""), (self._secret,)) or None
                )

                if status == "interrupted":
                    raise TruncatedResponse(
                        f"Codex turn interrupted after {timeout_s}s"
                        + (f" (interrupt error: {self._interrupt_error})" if self._interrupt_error else "")
                    )
                if status == "failed":
                    lowered = (error_message or "").lower()
                    if "refus" in lowered or "declin" in lowered:
                        raise ModelRefused(error_message or "Codex refused the request")
                    raise InvalidModelOutput(error_message or "Codex turn failed")

                text = getattr(result, "final_response", None) or ""
                if not text.strip():
                    raise InvalidModelOutput("Codex turn completed without a final response")

                parsed: Any = None
                if request.json_schema is not None:
                    try:
                        parsed = json.loads(text)
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise InvalidModelOutput(
                            f"Structured response is not valid JSON: {redact(text[:200])}"
                        ) from exc

                usage = _jsonable(getattr(result, "usage", None))
                duration = getattr(result, "duration_ms", None)
                return ModelResult(
                    text=text,
                    parsed=parsed,
                    raw=raw,
                    usage=usage if isinstance(usage, dict) else {},
                    truncated=False,
                    latency_ms=float(duration) if isinstance(duration, (int, float)) else (time.perf_counter() - started) * 1000.0,
                    attempts=attempt,
                    request_hash=request_hash,
                    model=request.model or self._model or "codex-default",
                    turn_status=status,
                    error_message=error_message,
                )
            except (InvalidModelOutput, ModelRefused, TruncatedResponse, ConfigError, PipelineError):
                raise
            except Exception as exc:  # noqa: BLE001 - SDK raises heterogeneous types
                last_error = exc
                if is_retryable_error(exc) and attempt <= self._max_retries:
                    time.sleep(min(0.25 * (2 ** (attempt - 1)), 2.0))
                    continue
                raise self._classify(exc) from exc

        raise self._classify(last_error) if last_error is not None else PipelineError(
            "MODEL_API_ERROR", "Codex turn failed without an exception"
        )


__all__ = ["CodexAdapter"]
