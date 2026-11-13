"""A1 · Codex runtime and provider capability probe.

Standalone diagnostic. It answers two questions with machine-readable output:

  1. Is the local Codex runtime usable through the official ``openai-codex`` SDK?
  2. What does the underlying provider/gateway actually support?

No PDF parsing and no business logic lives here. The only model path is
``Codex -> Codex app-server -> provider``; the SDK has no PDF input type, so
``decision.vision_path`` is pinned to ``image_png``.

Exit codes follow the frozen I7 contract (``docs/接口冻结清单_A-B.md`` §7), which
supersedes the A1 card wording:

  0  probe finished and the provider served a turn
  2  SDK import failed, configuration is unusable, or no credential path works
  5  Codex runtime could not start / initialize, or the provider rejected turns

The A1 card says "unauthenticated -> exit 8"; I7 reserves 8 for ``ASSET_*`` and
maps ``AUTH_MISSING`` to 2, so 2 is used here.

The secret is never printed: it is passed to the app-server child process through
``CodexConfig.env`` only, and every captured message is scrubbed against it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import zlib
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any, Callable

PROVIDER_ID = "llm"
SECRET_ENV = "LLM_API_KEY"
RUNTIME_BIN_PACKAGE = "openai-codex-cli-bin"

PROBE_TIMEOUT_S = 60.0
TOTAL_BUDGET_S = 300.0

SKILL_NAME = "pdf-vision"
SKILL_PATH = Path(__file__).resolve().parent / "skills" / SKILL_NAME / "SKILL.md"
CAPABILITIES_PATH = Path(__file__).resolve().with_name("provider_capabilities.json")

LONG_OUTPUT_PROMPT = (
    "Write 900 words of plain prose describing a rainy harbour at dawn. "
    "Do not use lists, headings, or code fences."
)
INTERRUPT_PROMPT = "Count from 1 to 400, one number per line, with no other text."


class ProbeFailure(Exception):
    """A single probe failed; ``error_class`` carries the classification."""

    def __init__(self, error_class: str, detail: str = "") -> None:
        super().__init__(error_class)
        self.error_class = error_class
        self.detail = detail


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _secret() -> str:
    from pipeline.settings import load_settings

    settings = load_settings(require_secret=False)
    value = settings.vision.api_key.get_secret_value() if settings.vision.api_key else ""
    return value or ""


def _scrub(text: str, secret: str) -> str:
    if secret:
        text = text.replace(secret, "***")
    return text[:400]


def _classify(exc: BaseException, secret: str) -> str:
    """Map an exception to a stable, greppable error class.

    Deliberately ordered and explicit: a single ``except Exception`` that hides
    the real cause is exactly what this probe exists to avoid.
    """
    name = type(exc).__name__
    message = _scrub(str(exc), secret)
    lowered = message.lower()

    if isinstance(exc, ImportError):
        return "ImportError"
    if isinstance(exc, FileNotFoundError):
        return "FileNotFoundError"
    if isinstance(exc, TimeoutError):
        return "TimeoutError"
    if "responses_feature_not_supported" in lowered:
        return "ProviderFeatureUnsupported"
    if "unsupported model" in lowered:
        return "ProviderModelUnsupported"
    if "no longer supported" in lowered and "wire_api" in lowered:
        return "WireApiUnsupportedByRuntime"
    if "401" in lowered or "unauthorized" in lowered or "invalid api key" in lowered:
        return "AuthRejected"
    if "closed stdout" in lowered:
        return "TransportClosedError"
    if name in {
        "CodexError",
        "JsonRpcError",
        "CodexRpcError",
        "ParseError",
        "InvalidRequestError",
        "MethodNotFoundError",
        "InvalidParamsError",
        "InternalRpcError",
        "ServerBusyError",
        "RetryLimitExceededError",
    }:
        return name
    return name


def _minimal_png() -> bytes:
    """1x1 RGBA PNG built in memory, so no image library is required."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\x00\x00\xff", 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", pixels)
        + chunk(b"IEND", b"")
    )


def _base_url_host(base_url: str) -> str:
    if not base_url:
        return ""
    return urllib.parse.urlsplit(base_url).netloc


def _config_overrides(base_url: str, wire_api: str) -> tuple[str, ...]:
    if not base_url:
        return ()
    if '"' in base_url or "\n" in base_url:
        raise ProbeFailure("InvalidBaseUrl", "base_url contains characters unsafe for TOML")
    return (
        f'model_provider="{PROVIDER_ID}"',
        f'model_providers.{PROVIDER_ID}.name="LLM"',
        f'model_providers.{PROVIDER_ID}.base_url="{base_url}"',
        f'model_providers.{PROVIDER_ID}.env_key="{SECRET_ENV}"',
        f'model_providers.{PROVIDER_ID}.wire_api="{wire_api}"',
    )


def _path_codex_version() -> str | None:
    """Reference only: the pinned runtime wins, the PATH binary does not decide."""

    binary = shutil.which("codex")
    if not binary:
        return None
    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = (completed.stdout or completed.stderr or "").strip().splitlines()
    return line[-1][:120] if line else None


def _pinned_runtime_version(codex: Any) -> str | None:
    try:
        server_info = getattr(codex.metadata, "server_info", None)
        reported = getattr(server_info, "version", None) if server_info else None
        if reported:
            return str(reported).split()[0]
    except (AttributeError, TypeError):
        pass
    try:
        return package_version(RUNTIME_BIN_PACKAGE)
    except PackageNotFoundError:
        return None


# --------------------------------------------------------------------------- #
# probes
# --------------------------------------------------------------------------- #


def probe_structured_output(codex: Any, model: str, secret: str) -> dict[str, Any]:
    from openai_codex import Sandbox, TextInput

    schema = {
        "type": "object",
        "properties": {"status": {"type": "string"}, "value": {"type": "integer"}},
        "required": ["status", "value"],
        "additionalProperties": False,
    }
    thread = codex.thread_start(
        sandbox=Sandbox.read_only, ephemeral=True, model=model, model_provider=PROVIDER_ID
    )
    result = thread.turn(
        [TextInput(text='Reply with JSON only: {"status":"ok","value":7}')],
        output_schema=schema,
    ).run()
    text = result.final_response or ""
    result_payload = {"output_schema_ok": True, "parsed_ok": False}
    try:
        parsed = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return result_payload
    result_payload["parsed_ok"] = isinstance(parsed, dict) and "status" in parsed
    return result_payload


def probe_local_image(codex: Any, model: str, secret: str) -> bool:
    from openai_codex import LocalImageInput, Sandbox, TextInput

    with tempfile.TemporaryDirectory(prefix="a1-vision-") as directory:
        image_path = Path(directory) / "probe.png"
        image_path.write_bytes(_minimal_png())
        thread = codex.thread_start(
            sandbox=Sandbox.read_only, ephemeral=True, model=model, model_provider=PROVIDER_ID
        )
        thread.turn(
            [
                LocalImageInput(path=str(image_path)),
                TextInput(text="Describe the image in at most five words."),
            ]
        ).run()
    return True


def probe_skill_input(codex: Any, model: str, secret: str) -> bool:
    from openai_codex import Sandbox, SkillInput, TextInput

    if not SKILL_PATH.is_file():
        raise ProbeFailure("ASSET_MISSING", f"skill not found: {SKILL_PATH.name}")
    thread = codex.thread_start(
        sandbox=Sandbox.read_only, ephemeral=True, model=model, model_provider=PROVIDER_ID
    )
    thread.turn(
        [
            SkillInput(name=SKILL_NAME, path=str(SKILL_PATH)),
            TextInput(text="Reply with the word ok."),
        ]
    ).run()
    return True


def probe_interrupt(codex: Any, model: str, secret: str) -> dict[str, Any]:
    from openai_codex import Sandbox, TextInput

    thread = codex.thread_start(
        sandbox=Sandbox.read_only, ephemeral=True, model=model, model_provider=PROVIDER_ID
    )
    turn = thread.turn([TextInput(text=INTERRUPT_PROMPT)])
    time.sleep(1.0)
    started = time.perf_counter()
    turn.interrupt()
    try:
        result = turn.run()
    except Exception as exc:  # noqa: BLE001 - interrupt races with completion
        return {
            "supported": False,
            "latency_ms": round((time.perf_counter() - started) * 1000.0),
            "error_class": _classify(exc, secret),
        }
    latency = round((time.perf_counter() - started) * 1000.0)
    status = str(getattr(result.status, "value", result.status))
    return {"supported": status == "interrupted", "latency_ms": latency, "status": status}


def probe_truncation(codex: Any, model: str, secret: str) -> dict[str, Any]:
    from openai_codex import Sandbox, TextInput

    thread = codex.thread_start(
        sandbox=Sandbox.read_only, ephemeral=True, model=model, model_provider=PROVIDER_ID
    )
    result = thread.turn([TextInput(text=LONG_OUTPUT_PROMPT)]).run()
    status = str(getattr(result.status, "value", result.status))
    return {
        "status_observable": status in {"completed", "interrupted", "failed"},
        "usage_observable": result.usage is not None,
        "duration_ms_present": result.duration_ms is not None,
        "status": status,
    }


def probe_account(codex: Any) -> dict[str, Any]:
    response = codex.account()
    account = getattr(response, "account", None)
    root = getattr(account, "root", None) if account is not None else None
    account_type = getattr(root, "type", None)
    return {"ok": account_type is not None, "account_class": account_type or "AUTH_MISSING"}


def probe_models(codex: Any, model: str) -> list[str]:
    response = codex.models()
    return [item.model for item in response.data]


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe the Codex runtime and provider capabilities.")
    parser.add_argument("--output", default=str(CAPABILITIES_PATH), help="capability list path")
    args = parser.parse_args(argv)

    started_at = time.monotonic()

    try:
        import openai_codex
        from openai_codex import Codex, CodexConfig
    except ImportError as exc:
        print(f"ImportError: {exc}", file=sys.stderr)
        return 2

    from pipeline.settings import load_settings

    settings = load_settings(require_secret=False)
    base_url = settings.vision.base_url or ""
    model = settings.vision.model or ""
    wire_api = settings.vision.wire_api or "responses"
    secret = _secret()

    payload: dict[str, Any] = {
        "probed_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "sdk": {
            "version": getattr(openai_codex, "__version__", None),
            "pinned_runtime_version": None,
            "path_codex_version": _path_codex_version(),
        },
        "runtime": {"started": False, "initialize_ok": False},
        "auth": {"ok": False, "account_class": "AUTH_MISSING"},
        "provider": {
            "model_provider": PROVIDER_ID,
            "base_url_host": _base_url_host(base_url),
            "wire_api": wire_api,
            "ok": False,
            "error_class": None,
        },
        "models_visible": [],
        "structured_output": {"output_schema_ok": False, "parsed_ok": False},
        "vision": {"local_image": False, "skill_input": False},
        "interrupt": {"supported": False, "latency_ms": None},
        "truncation": {"status_observable": False, "usage_observable": False},
        "decision": {"vision_path": "image_png", "skill_transport": "prompt_text"},
    }

    try:
        overrides = _config_overrides(base_url, wire_api)
    except ProbeFailure as failure:
        print(f"CONFIG_INVALID {failure.error_class}: {failure.detail}", file=sys.stderr)
        return 2

    config = CodexConfig(config_overrides=overrides, env={SECRET_ENV: secret} if secret else None)
    try:
        codex = Codex(config=config)
    except Exception as exc:  # noqa: BLE001 - runtime startup has heterogeneous failures
        payload["provider"]["error_class"] = _classify(exc, secret)
        _write(args.output, payload)
        print(f"RUNTIME_START_FAILED {payload['provider']['error_class']}", file=sys.stderr)
        print(_scrub(str(exc), secret), file=sys.stderr)
        return 5

    try:
        payload["runtime"]["started"] = True
        payload["sdk"]["pinned_runtime_version"] = _pinned_runtime_version(codex)
        payload["runtime"]["initialize_ok"] = payload["sdk"]["pinned_runtime_version"] is not None

        # 2 · account state. Never attempts a login.
        try:
            payload["auth"] = probe_account(codex)
        except Exception as exc:  # noqa: BLE001
            payload["auth"] = {"ok": False, "account_class": _classify(exc, secret)}

        # 3 · visible models
        try:
            payload["models_visible"] = probe_models(codex, model)
        except Exception as exc:  # noqa: BLE001
            payload["provider"]["error_class"] = payload["provider"]["error_class"] or _classify(
                exc, secret
            )

        # 4 · minimal provider turn
        remaining = TOTAL_BUDGET_S - (time.monotonic() - started_at)
        if remaining <= 0:
            payload["provider"]["error_class"] = "BudgetExhausted"
        else:
            try:
                from openai_codex import Sandbox, TextInput

                thread = codex.thread_start(
                    sandbox=Sandbox.read_only,
                    ephemeral=True,
                    model=model,
                    model_provider=PROVIDER_ID,
                )
                thread.turn([TextInput(text="Reply with the single word: ok")]).run()
                payload["provider"]["ok"] = True
            except Exception as exc:  # noqa: BLE001
                payload["provider"]["error_class"] = _classify(exc, secret)

        provider_ok = bool(payload["provider"]["ok"])

        def guarded(name: str, call: Callable[[], Any]) -> Any:
            nonlocal payload
            if not provider_ok:
                return None
            if TOTAL_BUDGET_S - (time.monotonic() - started_at) <= 0:
                return None
            try:
                return call()
            except Exception:  # noqa: BLE001 - recorded per probe below
                raise

        if provider_ok:
            for key, function in (
                ("structured_output", lambda: probe_structured_output(codex, model, secret)),
                ("vision_local_image", lambda: probe_local_image(codex, model, secret)),
                ("vision_skill_input", lambda: probe_skill_input(codex, model, secret)),
                ("interrupt", lambda: probe_interrupt(codex, model, secret)),
                ("truncation", lambda: probe_truncation(codex, model, secret)),
            ):
                try:
                    outcome = guarded(key, function)
                except ProbeFailure as failure:
                    if key == "vision_skill_input":
                        payload["decision"]["skill_transport"] = "prompt_text"
                    print(f"PROBE_{key.upper()}_FAILED {failure.error_class}", file=sys.stderr)
                    continue
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"PROBE_{key.upper()}_FAILED {_classify(exc, secret)}: "
                        f"{_scrub(str(exc), secret)}",
                        file=sys.stderr,
                    )
                    continue
                if outcome is None:
                    continue
                if key == "structured_output":
                    payload["structured_output"] = outcome
                elif key == "vision_local_image":
                    payload["vision"]["local_image"] = bool(outcome)
                elif key == "vision_skill_input":
                    payload["vision"]["skill_input"] = bool(outcome)
                    payload["decision"]["skill_transport"] = (
                        "skill_input" if outcome else "prompt_text"
                    )
                elif key == "interrupt":
                    payload["interrupt"] = {
                        "supported": bool(outcome.get("supported")),
                        "latency_ms": outcome.get("latency_ms"),
                    }
                elif key == "truncation":
                    payload["truncation"] = {
                        "status_observable": bool(outcome.get("status_observable")),
                        "usage_observable": bool(outcome.get("usage_observable")),
                    }
    finally:
        codex.close()

    _write(args.output, payload)

    print(f"runtime.started={payload['runtime']['started']}")
    print(f"provider.ok={payload['provider']['ok']} error_class={payload['provider']['error_class']}")
    print(f"models_visible={len(payload['models_visible'])}")
    print(f"decision={json.dumps(payload['decision'], ensure_ascii=False)}")

    if not payload["provider"]["ok"]:
        return 5
    if not payload["auth"]["ok"] and not secret:
        print("AUTH_MISSING", file=sys.stderr)
        return 2
    return 0


def _write(path: str, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    raise SystemExit(main())
