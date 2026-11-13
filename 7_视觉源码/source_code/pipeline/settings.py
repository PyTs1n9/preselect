"""The single configuration entry point for the vision pipeline.

Configuration is merged in this order (highest precedence first): CLI
overrides, process environment, ``.env``, JSON configuration, and built-in
defaults.  This module deliberately keeps secret handling local so callers can
inspect configuration without ever printing the secret value.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping
from types import MappingProxyType

from .errors import ConfigError, redact


@dataclass(frozen=True)
class SecretValue:
    """A small, dependency-free secret container with a masked representation."""

    _value: str | None = field(default=None, repr=False, compare=True)

    def __repr__(self) -> str:
        return "***"

    def __str__(self) -> str:
        return "***"

    @property
    def is_set(self) -> bool:
        return bool(self._value)

    def get_secret_value(self) -> str | None:
        """Return the value for the model adapter, never for display or logging."""

        return self._value


@dataclass(frozen=True)
class VisionSettings:
    provider: str
    model: str
    base_url: str
    wire_api: str
    timeout_s: float
    max_retries: int
    api_key: SecretValue
    dpi: int = 150
    effort: str = "medium"


@dataclass(frozen=True)
class PipelineSettings:
    max_repair_attempts: int
    keep_intermediate: bool
    body_min_han: int = 2500
    body_max_han: int = 4000


@dataclass(frozen=True)
class PathsSettings:
    result_dir: Path
    prompts_dir: Path
    skills_dir: Path
    fixtures_dir: Path


@dataclass(frozen=True)
class Settings:
    """Fully resolved, immutable pipeline settings."""

    vision: VisionSettings
    pipeline: PipelineSettings
    paths: PathsSettings
    _source_map: tuple[tuple[str, str], ...] = field(default=(), repr=False, compare=False)
    _unknown: tuple[str, ...] = field(default=(), repr=False, compare=False)

    def describe(self) -> dict[str, dict[str, Any]]:
        """Return manifest-compatible values and their source layer."""

        values: dict[str, Any] = {
            "vision.provider": self.vision.provider,
            "vision.model": self.vision.model,
            "vision.base_url": self.vision.base_url,
            "vision.wire_api": self.vision.wire_api,
            "vision.timeout_s": self.vision.timeout_s,
            "vision.max_retries": self.vision.max_retries,
            "vision.api_key": self.vision.api_key,
            "vision.dpi": self.vision.dpi,
            "vision.effort": self.vision.effort,
            "pipeline.max_repair_attempts": self.pipeline.max_repair_attempts,
            "pipeline.keep_intermediate": self.pipeline.keep_intermediate,
            "pipeline.body_min_han": self.pipeline.body_min_han,
            "pipeline.body_max_han": self.pipeline.body_max_han,
            "paths.result_dir": self.paths.result_dir,
            "paths.prompts_dir": self.paths.prompts_dir,
            "paths.skills_dir": self.paths.skills_dir,
            "paths.fixtures_dir": self.paths.fixtures_dir,
        }
        sources = dict(self._source_map)
        described: dict[str, dict[str, Any]] = {}
        for name, value in values.items():
            if isinstance(value, SecretValue):
                safe_value: Any = "***"
            elif isinstance(value, Path):
                safe_value = value.as_posix()
            else:
                safe_value = value
            if isinstance(safe_value, str):
                safe_value = redact(safe_value, (self.vision.api_key.get_secret_value() or "",))
            described[name] = {"value": safe_value, "source": sources[name]}
        return described

    def unknown_keys(self) -> list[str]:
        """Return unrecognized keys found in ``config.json``."""

        return [redact(name, (self.vision.api_key.get_secret_value() or "",)) for name in self._unknown]


_DEFAULTS: dict[str, Any] = {
    "vision.provider": "openai_codex",
    "vision.model": "gpt-4.1-mini",
    "vision.base_url": "",
    "vision.wire_api": "responses",
    "vision.timeout_s": 120.0,
    "vision.max_retries": 2,
    "vision.api_key": None,
    "vision.dpi": 200,
    "vision.effort": "medium",
    "pipeline.max_repair_attempts": 2,
    "pipeline.keep_intermediate": True,
    "pipeline.body_min_han": 2500,
    "pipeline.body_max_han": 4000,
    "paths.result_dir": "result/runs",
    "paths.prompts_dir": "prompts",
    "paths.skills_dir": "skills",
    "paths.fixtures_dir": "fixtures/quality",
}

_ENV_NAMES: dict[str, str] = {
    "vision.provider": "VISION_PROVIDER",
    "vision.model": "LLM_MODEL",
    "vision.base_url": "LLM_BASE_URL",
    "vision.wire_api": "VISION_WIRE_API",
    "vision.effort": "VISION_EFFORT",
    "vision.timeout_s": "VISION_TIMEOUT_S",
    "vision.max_retries": "VISION_MAX_RETRIES",
    "vision.api_key": "LLM_API_KEY",
    "vision.dpi": "VISION_DPI",
    "pipeline.max_repair_attempts": "PIPELINE_MAX_REPAIR_ATTEMPTS",
    "pipeline.keep_intermediate": "PIPELINE_KEEP_INTERMEDIATE",
    "pipeline.body_min_han": "PIPELINE_BODY_MIN_HAN",
    "pipeline.body_max_han": "PIPELINE_BODY_MAX_HAN",
    "paths.result_dir": "PATHS_RESULT_DIR",
    "paths.prompts_dir": "PATHS_PROMPTS_DIR",
    "paths.skills_dir": "PATHS_SKILLS_DIR",
    "paths.fixtures_dir": "PATHS_FIXTURES_DIR",
}

_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "vision.model": ("VISION_MODEL",),
    "vision.api_key": ("OPENAI_API_KEY", "VISION_API_KEY"),
    "paths.result_dir": ("RESULT_DIR",),
    "paths.prompts_dir": ("PROMPTS_DIR",),
    "paths.skills_dir": ("SKILLS_DIR",),
    "paths.fixtures_dir": ("FIXTURES_DIR",),
}

_CLI_ALIASES: dict[str, str] = {
    "provider": "vision.provider",
    "model": "vision.model",
    "wire_api": "vision.wire_api",
    "effort": "vision.effort",
    "base_url": "vision.base_url",
    "timeout_s": "vision.timeout_s",
    "max_retries": "vision.max_retries",
    "dpi": "vision.dpi",
    "vision_dpi": "vision.dpi",
    "body_min_han": "pipeline.body_min_han",
    "body_max_han": "pipeline.body_max_han",
    "pipeline_body_min_han": "pipeline.body_min_han",
    "pipeline_body_max_han": "pipeline.body_max_han",
    "api_key": "vision.api_key",
    "openai_api_key": "vision.api_key",
    "vision_provider": "vision.provider",
    "vision_model": "vision.model",
    "vision_wire_api": "vision.wire_api",
    "vision_effort": "vision.effort",
    "vision_base_url": "vision.base_url",
    "vision_timeout_s": "vision.timeout_s",
    "vision_max_retries": "vision.max_retries",
    "pipeline_max_repair_attempts": "pipeline.max_repair_attempts",
    "pipeline_keep_intermediate": "pipeline.keep_intermediate",
    "result_dir": "paths.result_dir",
    "prompts_dir": "paths.prompts_dir",
    "skills_dir": "paths.skills_dir",
    "fixtures_dir": "paths.fixtures_dir",
    "paths_result_dir": "paths.result_dir",
    "paths_prompts_dir": "paths.prompts_dir",
    "paths_skills_dir": "paths.skills_dir",
    "paths_fixtures_dir": "paths.fixtures_dir",
}

_CASTERS: dict[str, Callable[[Any], Any]] = {
    "vision.provider": str,
    "vision.model": str,
    "vision.wire_api": str,
    "vision.effort": str,
    "vision.base_url": str,
    "vision.timeout_s": float,
    "vision.max_retries": int,
    "vision.dpi": int,
    "pipeline.max_repair_attempts": int,
    "pipeline.keep_intermediate": bool,
    "pipeline.body_min_han": int,
    "pipeline.body_max_han": int,
    "paths.result_dir": Path,
    "paths.prompts_dir": Path,
    "paths.skills_dir": Path,
    "paths.fixtures_dir": Path,
}

_KNOWN_FIELDS = tuple(_DEFAULTS)
_KNOWN_SECTIONS: dict[str, set[str]] = {
    "vision": {name.split(".", 1)[1] for name in _KNOWN_FIELDS if name.startswith("vision.")},
    "pipeline": {name.split(".", 1)[1] for name in _KNOWN_FIELDS if name.startswith("pipeline.")},
    "paths": {name.split(".", 1)[1] for name in _KNOWN_FIELDS if name.startswith("paths.")},
}


_DEFAULTS = MappingProxyType(_DEFAULTS)
_ENV_NAMES = MappingProxyType(_ENV_NAMES)
_ENV_ALIASES = MappingProxyType(_ENV_ALIASES)
_CLI_ALIASES = MappingProxyType(_CLI_ALIASES)
_CASTERS = MappingProxyType(_CASTERS)
_KNOWN_SECTIONS = MappingProxyType({k: frozenset(v) for k, v in _KNOWN_SECTIONS.items()})


def _default_config_path() -> Path:
    requested = Path("config.json")
    if requested.exists():
        return requested
    package_config = Path(__file__).resolve().parents[1] / "config.json"
    return package_config if package_config.exists() else requested


def _read_json_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError("Configuration file not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError("Unable to read configuration file") from None
    if not isinstance(data, dict):
        raise ConfigError("Configuration root must be an object")
    return data


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise ConfigError("Unable to read dotenv file") from None
    for line_number, line in enumerate(lines, 1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("export "):
            text = text[7:].lstrip()
        if "=" not in text:
            raise ConfigError(f"Invalid dotenv entry at line {line_number}")
        key, value = text.split("=", 1)
        key = key.strip()
        if not key:
            raise ConfigError(f"Invalid dotenv entry at line {line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _flatten_mapping(values: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in values.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            flattened.update(_flatten_mapping(value, name))
        else:
            flattened[name] = value
    return flattened


def _canonical_cli_overrides(overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    if not overrides:
        return {}
    flattened = _flatten_mapping(overrides)
    canonical: dict[str, Any] = {}
    for name, value in flattened.items():
        if value is None:
            continue
        canonical_name = name if name in _DEFAULTS else _CLI_ALIASES.get(name)
        if canonical_name is not None:
            canonical[canonical_name] = value
    return canonical


def _config_values(config: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    values: dict[str, Any] = {}
    unknown: list[str] = []
    if "version" in config:
        values["version"] = config["version"]
    for section, fields in _KNOWN_SECTIONS.items():
        raw_section = config.get(section, {})
        if not isinstance(raw_section, Mapping):
            raise ConfigError(f"Configuration section {section} must be an object")
        for key, value in raw_section.items():
            canonical = f"{section}.{key}"
            if key in fields:
                values[canonical] = value
            else:
                unknown.extend(_unknown_leaf_keys(canonical, value))
    for key, value in config.items():
        if key not in {"version", *(_KNOWN_SECTIONS.keys())}:
            unknown.extend(_unknown_leaf_keys(str(key), value))
    return values, sorted(set(unknown))


def _unknown_leaf_keys(prefix: str, value: Any) -> list[str]:
    if isinstance(value, Mapping):
        leaves: list[str] = []
        for key, child in value.items():
            leaves.extend(_unknown_leaf_keys(f"{prefix}.{key}", child))
        return leaves or [prefix]
    return [prefix]


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError


def _convert(name: str, value: Any) -> Any:
    if name == "vision.api_key":
        if value is None:
            return None
        return str(value)
    if value is None and name == "vision.base_url":
        return ""
    caster = _CASTERS[name]
    try:
        if caster in (int, float) and isinstance(value, bool):
            raise ValueError
        if caster is int and isinstance(value, float) and not value.is_integer():
            raise ValueError
        if caster is str and not isinstance(value, str):
            raise ValueError
        converted = _parse_bool(value) if caster is bool else caster(value)
    except (TypeError, ValueError, OSError, OverflowError):
        raise ConfigError(f"Invalid value for {name}") from None
    if isinstance(converted, str) and not converted.strip() and name not in {
        "vision.base_url",
        "vision.model",
    }:
        raise ConfigError(f"Invalid empty value for {name}")
    if isinstance(converted, float) and not math.isfinite(converted):
        raise ConfigError(f"Invalid non-finite value for {name}")
    if name in {"vision.dpi", "vision.timeout_s"} and converted <= 0:
        raise ConfigError(f"Expected positive value for {name}")
    if isinstance(converted, (int, float)) and converted < 0 and name in {
        "vision.timeout_s",
        "vision.max_retries",
        "pipeline.max_repair_attempts",
        "pipeline.body_min_han",
        "pipeline.body_max_han",
    }:
        raise ConfigError(f"Invalid negative value for {name}")
    return converted


def load_settings(
    cli_overrides: dict | None = None,
    config_path: Path | None = None,
    require_secret: bool = False,
) -> Settings:
    """Load settings using the fixed CLI > env > dotenv > config > default order.

    ``require_secret=False`` permits offline inspection and doctor output.  A
    caller that will perform a model request must pass ``require_secret=True``;
    that mode fails immediately when the configured API key is absent.
    """

    path = Path(config_path) if config_path is not None else _default_config_path()
    config = _read_json_config(path)
    config_values, unknown = _config_values(config)
    dotenv_path = path.parent / ".env"
    if not dotenv_path.is_file() and Path(".env").is_file():
        dotenv_path = Path(".env")
    dotenv_values = _read_dotenv(dotenv_path)
    cli_values = _canonical_cli_overrides(cli_overrides)

    resolved: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for name in _KNOWN_FIELDS:
        env_name = _ENV_NAMES[name]
        env_candidates = (env_name, *_ENV_ALIASES.get(name, ()))
        if name in cli_values:
            value, source = cli_values[name], "cli"
        else:
            for candidate in env_candidates:
                if candidate in os.environ:
                    value, source = os.environ[candidate], "env"
                    break
            else:
                for candidate in env_candidates:
                    if candidate in dotenv_values:
                        value, source = dotenv_values[candidate], "dotenv"
                        break
                else:
                    if name in config_values:
                        value, source = config_values[name], "config"
                    else:
                        value, source = _DEFAULTS[name], "default"
        resolved[name] = _convert(name, value)
        sources[name] = source

    secret = SecretValue(resolved["vision.api_key"])
    if resolved["pipeline.body_min_han"] > resolved["pipeline.body_max_han"]:
        raise ConfigError("pipeline.body_min_han must not exceed body_max_han")
    if require_secret and not secret.is_set:
        raise ConfigError("Missing required secret: LLM_API_KEY (value=***)")

    return Settings(
        vision=VisionSettings(
            provider=resolved["vision.provider"],
            model=resolved["vision.model"],
            base_url=resolved["vision.base_url"],
            wire_api=resolved["vision.wire_api"],
            timeout_s=resolved["vision.timeout_s"],
            max_retries=resolved["vision.max_retries"],
            api_key=secret,
            dpi=resolved["vision.dpi"],
            effort=resolved["vision.effort"],
        ),
        pipeline=PipelineSettings(
            max_repair_attempts=resolved["pipeline.max_repair_attempts"],
            keep_intermediate=resolved["pipeline.keep_intermediate"],
            body_min_han=resolved["pipeline.body_min_han"],
            body_max_han=resolved["pipeline.body_max_han"],
        ),
        paths=PathsSettings(
            result_dir=resolved["paths.result_dir"],
            prompts_dir=resolved["paths.prompts_dir"],
            skills_dir=resolved["paths.skills_dir"],
            fixtures_dir=resolved["paths.fixtures_dir"],
        ),
        _source_map=tuple(sources.items()),
        _unknown=tuple(unknown),
    )


__all__ = [
    "ConfigError",
    "PathsSettings",
    "PipelineSettings",
    "SecretValue",
    "Settings",
    "VisionSettings",
    "load_settings",
]
