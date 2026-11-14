"""Stage-local I/O and audit helpers; this module is not a stage."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from ..contracts import ModelRequest, ModelResult, PdfInfo, PromptAsset, RunContext
from ..errors import AssetMissing, Code, IoFailure, Issue, PipelineError, redact
from ..manifest import create_run_manifest, utc_now, write_manifest
from ..schema_loader import validate_instance
from ..vision_provider import VisionProvider


@contextmanager
def _atomic_target(path: Path):
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
        yield temporary
        os.replace(temporary, path)
    except OSError:
        raise IoFailure(f"Unable to write artifact: {path.name}") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                raise IoFailure(f"Unable to remove temporary artifact: {path.name}") from None


def write_text_atomic(path: Path, text: str) -> None:
    with _atomic_target(path) as temporary:
        temporary.write_text(text, encoding="utf-8", newline="\n")


def write_json_atomic(path: Path, payload: object) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    write_text_atomic(path, text)


def _safe_text(ctx: RunContext, text: str) -> str:
    return redact(text, (ctx.settings.vision.api_key.get_secret_value() or "",))


def _redact_values(ctx: RunContext, value):
    if isinstance(value, dict):
        return {_safe_text(ctx, key): _redact_values(ctx, child) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact_values(ctx, child) for child in value]
    return _safe_text(ctx, value) if isinstance(value, str) else value


def _config_value(value):
    if isinstance(value, dict):
        return {key: _config_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_config_value(child) for child in value]
    if isinstance(value, str):
        if PureWindowsPath(value).is_absolute():
            return PureWindowsPath(value).name
        if PurePosixPath(value).is_absolute():
            return PurePosixPath(value).name
    return value


def _read_manifest(ctx: RunContext) -> dict:
    path = ctx.run_dir / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return create_run_manifest(ctx.run_id, ctx.material_id, ctx.settings)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise IoFailure("Unable to read manifest.json") from None
    if (not isinstance(manifest, dict) or not isinstance(manifest.get("stages"), list)
            or not isinstance(manifest.get("config"), dict)
            or any(not isinstance(stage, dict) or "name" not in stage for stage in manifest["stages"])):
        raise IoFailure("Invalid manifest.json structure")
    return manifest


def _write_manifest(ctx: RunContext, manifest: dict) -> None:
    config = manifest["config"]
    try:
        endpoint = config["vision.base_url"]["value"]
        # Repeated stage updates read a host already stripped on first write.
        if "://" in endpoint:
            config["vision.base_url"]["value"] = urlsplit(endpoint).netloc.rsplit("@", 1)[-1]
    except (KeyError, TypeError, ValueError):
        raise IoFailure("Invalid manifest base URL configuration") from None
    manifest["config"] = _config_value(config)
    safe_manifest = _redact_values(ctx, manifest)
    with _atomic_target(ctx.run_dir / "manifest.json") as temporary:
        # Reuse the existing manifest serializer, but replace atomically.
        write_manifest(temporary, safe_manifest)
        with temporary.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write("\n")


def record_stage(ctx, name, status, started_at, ended_at, duration_ms, artifacts, issues, model_calls):
    manifest = _read_manifest(ctx)
    try:
        relative_artifacts = {key: Path(path).resolve().relative_to(ctx.run_dir.resolve()).as_posix()
                              for key, path in artifacts.items()}
    except (OSError, ValueError):
        raise IoFailure("Stage artifact must be inside its run directory") from None
    entry = {"name": name, "status": status, "started_at": started_at,
             "ended_at": ended_at, "duration_ms": duration_ms,
             "artifacts": relative_artifacts, "issues": [issue.to_dict() for issue in issues],
             "model_calls": model_calls}
    for index, previous in enumerate(manifest["stages"]):
        if previous["name"] == name:
            manifest["stages"][index] = entry
            break
    else:
        manifest["stages"].append(entry)
    _write_manifest(ctx, manifest)


def record_input(ctx: RunContext, source: Path, info: PdfInfo) -> None:
    manifest = _read_manifest(ctx)
    manifest["input"] = {"path_name": source.name, "sha256": info.sha256,
                         "page_count": info.page_count}
    _write_manifest(ctx, manifest)


def read_json_input(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise AssetMissing(f"Required artifact cannot be read: {path.name}") from None
    except UnicodeError:
        raise PipelineError(Code.SCHEMA_INVALID, f"Invalid UTF-8 JSON: {path.name}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise PipelineError(Code.SCHEMA_INVALID, f"Invalid JSON: {path.name}") from None


def generate_and_close(provider: VisionProvider, request: ModelRequest, call: dict) -> ModelResult:
    failure = None
    try:
        # Failed adapter calls do not expose their retry count; do not invent it.
        call["attempts"] = None
        result = provider.generate(request)
        call["attempts"] = result.attempts
        return result
    except Exception as exc:
        failure = exc
        raise
    finally:
        try:
            provider.close()
        except Exception as close_failure:
            if failure is not None:
                raise failure from close_failure
            raise


class StageRecord:
    def __init__(self, ctx: RunContext, name: str):
        self.ctx = ctx
        self.name = name
        self.started_at = utc_now()
        self.started = time.perf_counter()
        self.artifacts: dict[str, Path] = {}
        self.issues: list[Issue] = []
        self.model_calls: list[dict] = []

    def prompt(self, name: str, prompt: PromptAsset) -> dict:
        call = {"prompt_name": name, "prompt_sha256": prompt.sha256,
                "prompt_version": prompt.version, "attempts": 0}
        self.model_calls.append(call)
        if prompt.version is None:
            self.issues.append(Issue(Code.ASSET_NO_VERSION, self.name, f"{name}.md",
                                     "warning", "Prompt has no first-line version comment"))
        return call

    def validate(self, schema_name: str, parsed: object) -> None:
        issues = validate_instance(schema_name, parsed)
        self.issues.extend(issues)
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            message = "; ".join(f"{issue.path or '/'}:{issue.code.value}" for issue in errors)
            raise PipelineError(Code.SCHEMA_INVALID, _safe_text(self.ctx, message))

    def record(self, status: str) -> None:
        record_stage(self.ctx, self.name, status, self.started_at,
                     None if status == "running" else utc_now(),
                     (time.perf_counter() - self.started) * 1000.0,
                     self.artifacts, self.issues, self.model_calls)

    def failure(self, exc: Exception) -> None:
        if isinstance(exc, PipelineError):
            code = exc.code.value if isinstance(exc.code, Code) else exc.code
            if self.model_calls:
                self.model_calls[-1]["error_code"] = code
            # AUTH_MISSING / IO_FAILURE are run errors, not I6 Issue codes.
            if code in Code._value2member_map_ and not any(
                    issue.severity == "error" and issue.code == code for issue in self.issues):
                self.issues.append(Issue(Code(code), self.name, "", "error", str(code)))


@contextmanager
def stage_record(ctx: RunContext, name: str):
    state = StageRecord(ctx, name)
    try:
        state.record("running")
        yield state
        state.record("ok")
    except Exception as exc:
        state.failure(exc)
        try:
            state.record("failed")
        except Exception as record_failure:
            # A secondary journal failure must not change the original I7 exit.
            raise exc from record_failure
        raise
