"""Deterministic local JSON Schema loading and validation."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, SchemaError

from .errors import Code, Issue, PipelineError, SchemaNotFound, SchemaVersionMismatch

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

# Per-schema supported versions. A single global constant cannot work: I3 lets
# each schema evolve independently, so bumping validation must not invalidate
# recognition. Schemas absent from this map have no version gate yet — their
# field fragments are still owned by B and lack a schema_version field.
SUPPORTED_VERSIONS: dict[str, frozenset[str]] = {
    "validation": frozenset({"1.1.0"}),
    # B1 lands the first declared version of both model-output schemas.
    "recognition": frozenset({"1.0.0"}),
    "document_ir": frozenset({"1.0.0"}),
    "evidence": frozenset({"1.0.2"}),
    "table": frozenset({"1.0.0"}),
    "figure": frozenset({"1.0.0"}),
    "formula": frozenset({"1.0.0"}),
}

# Schemas sent to the model as structured-output contracts. The provider's
# strict mode rejects any object that leaves additionalProperties open, keeps a
# property out of required, or uses oneOf. Checking locally turns a nine-second
# round trip into an immediate SCHEMA_INVALID with the offending path.
MODEL_OUTPUT_SCHEMAS: frozenset[str] = frozenset({"recognition", "document_ir", "evidence", "table", "figure", "formula"})


def _strict_issues(node: object, path: str = "") -> list[str]:
    issues: list[str] = []
    if not isinstance(node, dict):
        return issues
    if node.get("type") == "object" or "properties" in node:
        if node.get("additionalProperties") is not False:
            issues.append(f"{path or '/'}: object requires additionalProperties=false")
        properties = node.get("properties")
        if isinstance(properties, dict):
            missing = sorted(set(properties) - set(node.get("required") or []))
            if missing:
                issues.append(f"{path or '/'}: required must list every property (missing: {', '.join(missing)})")
    if "oneOf" in node:
        issues.append(f"{path or '/'}: oneOf is not supported by strict output; use anyOf")
    for key, child in (node.get("properties") or {}).items():
        issues.extend(_strict_issues(child, f"{path}/properties/{key}"))
    if isinstance(node.get("items"), dict):
        issues.extend(_strict_issues(node["items"], f"{path}/items"))
    for key in ("anyOf", "allOf"):
        for index, child in enumerate(node.get(key) or []):
            issues.extend(_strict_issues(child, f"{path}/{key}/{index}"))
    return issues


def _schema_path(name: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise SchemaNotFound("Invalid schema name")
    return SCHEMA_DIR / f"{name}.schema.json"


def _read_schema_bytes(name: str) -> bytes:
    path = _schema_path(name)
    try:
        return path.read_bytes()
    except FileNotFoundError:
        raise SchemaNotFound() from None
    except OSError:
        raise PipelineError(Code.SCHEMA_INVALID, "Schema file cannot be read") from None


def _reject_external_refs(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"$ref", "$dynamicRef"} and (not isinstance(child, str) or not child.startswith("#")):
                raise PipelineError(Code.SCHEMA_INVALID, "External schema references are not allowed")
            _reject_external_refs(child)
    elif isinstance(value, list):
        for child in value:
            _reject_external_refs(child)


def load_schema(name: str) -> dict:
    try:
        schema = json.loads(_read_schema_bytes(name).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PipelineError(Code.SCHEMA_INVALID, "Schema is not valid JSON") from None
    if not isinstance(schema, dict):
        raise PipelineError(Code.SCHEMA_INVALID, "Schema must be an object")
    if schema.get("$schema") != SCHEMA_DIALECT:
        raise SchemaVersionMismatch("Unsupported schema dialect")
    declared = schema.get("schema_version")
    allowed = SUPPORTED_VERSIONS.get(name)
    if declared is not None and allowed is not None and declared not in allowed:
        raise SchemaVersionMismatch(f"Unsupported {name} schema version: {declared}")
    if not isinstance(schema.get("$id"), str) or not schema["$id"]:
        raise PipelineError(Code.SCHEMA_INVALID, "Schema requires an id")
    _reject_external_refs(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise PipelineError(Code.SCHEMA_INVALID, "Invalid schema definition") from None
    if name in MODEL_OUTPUT_SCHEMAS:
        problems = _strict_issues(schema)
        if problems:
            raise PipelineError(Code.SCHEMA_INVALID,
                                "Schema is not strict-output compatible: " + problems[0])
    return schema


def validate_instance(name: str, obj: object) -> list[Issue]:
    schema = load_schema(name)
    declared_version = schema.get("schema_version")
    if declared_version is not None and isinstance(obj, dict) and "schema_version" in obj and obj["schema_version"] != declared_version:
        return [Issue(Code.SCHEMA_VERSION_MISMATCH, "validate", "/schema_version", "error",
                      "Report schema_version does not match the loaded schema", "Use the frozen schema version")]
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    issues: list[Issue] = []
    for error in validator.iter_errors(obj):
        path = "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in error.absolute_path)
        issues.append(Issue(Code.SCHEMA_INVALID, "validate", path, "error",
                            f"Schema constraint failed: {error.validator}", "Fix the instance to match the schema"))
    return sorted(issues, key=lambda issue: (issue.path, issue.message))


def schema_sha256(name: str) -> str:
    return hashlib.sha256(_read_schema_bytes(name)).hexdigest()
