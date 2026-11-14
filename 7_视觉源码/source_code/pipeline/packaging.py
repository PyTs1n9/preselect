"""Deterministic, gated submission packaging with byte-level ZIP readback."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from shutil import copyfileobj
from zipfile import BadZipFile, LargeZipFile, ZIP_DEFLATED, ZipFile, ZipInfo

from .contracts import PackageReport, ValidationReport
from .errors import Code, ConfigError, IoFailure, PipelineError
from .manifest import utc_now
from .settings import Settings
from .validate import validate_run

MATERIAL_FILES = ("final.md", "validation.json", "manifest.json")
REQUIRED_STAGES = ("write", "repair")
REPORT_NAME = "package_report.json"
DANGEROUS_NAMES = frozenset({".env"})
DANGEROUS_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".pdf", ".zip")
_MATERIAL_ID = re.compile(r"[A-Za-z0-9._-]+")


class PackageFailure(IoFailure):
    """A package gate or archive verification failed (I7 exit 7)."""


def _valid_material_id(material_id: str) -> bool:
    return (isinstance(material_id, str) and material_id not in {".", ".."}
            and _MATERIAL_ID.fullmatch(material_id) is not None)


def _parse_started_at(value: object, material_id: str) -> datetime:
    if not isinstance(value, str):
        raise PackageFailure(f"Run for {material_id} has no valid started_at")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise PackageFailure(f"Run for {material_id} has no valid started_at") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise PackageFailure(f"Run for {material_id} started_at is not UTC")
    return parsed.astimezone(timezone.utc)


def select_run(result_dir: Path, material_id: str) -> Path:
    """Select the unique run with the latest valid UTC manifest timestamp."""
    if not _valid_material_id(material_id):
        raise ConfigError("Unsafe material_id")
    material_dir = result_dir / material_id
    if material_dir.is_symlink() or not material_dir.is_dir():
        raise PackageFailure(f"No run directory for {material_id}")
    candidates = []
    try:
        children = sorted(material_dir.iterdir(), key=lambda path: path.name)
    except OSError:
        raise PackageFailure(f"Unable to list runs for {material_id}") from None
    for run_dir in children:
        if run_dir.is_symlink():
            raise PackageFailure(f"Unsafe run directory for {material_id}/{run_dir.name}")
        if not run_dir.is_dir():
            continue
        manifest_path = run_dir / "manifest.json"
        if manifest_path.is_symlink():
            raise PackageFailure(f"Unsafe manifest file for {material_id}/{run_dir.name}")
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise PackageFailure(f"Invalid manifest for {material_id}/{run_dir.name}") from None
        if not isinstance(manifest, dict) or manifest.get("material_id") != material_id:
            raise PackageFailure(f"Manifest material_id mismatch for {material_id}/{run_dir.name}")
        manifest_run_id = manifest.get("run_id")
        if manifest_run_id is not None and manifest_run_id != run_dir.name:
            raise PackageFailure(f"Manifest run_id mismatch for {material_id}/{run_dir.name}")
        try:
            started = _parse_started_at(manifest.get("started_at"), material_id)
        except PackageFailure as exc:
            raise PackageFailure(f"{exc.message} for {material_id}/{run_dir.name}") from None
        candidates.append((started, run_dir))
    if not candidates:
        raise PackageFailure(f"No manifest-backed runs for {material_id}")
    latest = max(started for started, _ in candidates)
    matches = [run_dir for started, run_dir in candidates if started == latest]
    if len(matches) != 1:
        locations = ", ".join(f"{material_id}/{run_dir.name}" for run_dir in matches)
        raise PackageFailure(f"Latest run timestamp is ambiguous for {locations}")
    return matches[0]


def evaluate_gate(material_id: str, manifest: dict, report: ValidationReport) -> None:
    """Apply the frozen I6 and stage-completion gates in their required order."""
    if report.valid is not True:
        raise PipelineError(Code.SCHEMA_INVALID, f"Validation failed for {material_id}")
    checks = report.checks
    if not isinstance(checks, dict) or "skipped" not in checks or not isinstance(checks["skipped"], list):
        raise PackageFailure(f"Validation checks are unreadable for {material_id}")
    skipped = checks["skipped"]
    if skipped:
        codes = ", ".join(sorted(str(code) for code in skipped))
        raise PackageFailure(f"Validation checks skipped for {material_id}: {codes}")
    stages = manifest.get("stages") if isinstance(manifest, dict) else None
    if not isinstance(stages, list):
        raise PackageFailure(f"Manifest stages are invalid for {material_id}")
    by_name = {}
    for stage in stages:
        if not isinstance(stage, dict) or not isinstance(stage.get("name"), str):
            continue
        name = stage["name"]
        if name in by_name:
            raise PackageFailure(f"Duplicate stage record {name} for {material_id}")
        by_name[name] = stage
    for name in REQUIRED_STAGES:
        if name not in by_name or by_name[name].get("status") != "ok":
            raise PackageFailure(f"Required stage {name} is not ok for {material_id}")


def _is_dangerous(path: Path) -> bool:
    name = path.name.casefold()
    return name in {value.casefold() for value in DANGEROUS_NAMES} or name.endswith(DANGEROUS_SUFFIXES)


def _scan_run(run_dir: Path, material_id: str) -> list[Path]:
    try:
        paths = sorted(run_dir.rglob("*"), key=lambda path: path.relative_to(run_dir).as_posix())
    except OSError:
        raise PackageFailure(f"Unable to scan run files for {material_id}") from None
    files = []
    for path in paths:
        relative = path.relative_to(run_dir).as_posix()
        if path.is_symlink():
            raise PackageFailure(f"Symlink is not allowed: {material_id}/{relative}")
        try:
            is_file = path.is_file()
        except OSError:
            raise PackageFailure(f"Unable to inspect {material_id}/{relative}") from None
        if not is_file:
            continue
        if _is_dangerous(path):
            raise PackageFailure(f"Dangerous file rejected: {material_id}/{relative}")
        files.append(path)
    return files


def collect_entries(runs: dict[str, Path]) -> list[tuple[str, Path]]:
    entries = []
    for material_id, run_dir in runs.items():
        if not _valid_material_id(material_id):
            raise ConfigError("Unsafe material_id")
        for filename in MATERIAL_FILES:
            source = run_dir / filename
            try:
                if not source.is_file() or source.stat().st_size <= 0:
                    raise PackageFailure(f"Required file is missing or empty: {material_id}/{filename}")
            except OSError:
                raise PackageFailure(f"Unable to read {material_id}/{filename}") from None
            entries.append((f"materials/{material_id}/{filename}", source))
    names = [name for name, _ in entries]
    if len(names) != len(set(names)):
        raise PackageFailure("Duplicate ZIP entry name")
    return sorted(entries, key=lambda entry: entry[0])


def write_zip(entries: list[tuple[str, Path]], target: Path) -> dict[str, tuple[int, int]]:
    """Write deterministic ZIP entries and return their uncompressed size/CRC."""
    names = [name for name, _ in entries]
    if len(names) != len(set(names)):
        raise PackageFailure("Duplicate ZIP entry name")
    try:
        with ZipFile(target, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for name, source in sorted(entries, key=lambda entry: entry[0]):
                if name.startswith("/") or "\\" in name or any(part in {"", ".", ".."} for part in name.split("/")):
                    raise PackageFailure("Unsafe ZIP entry name")
                info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                info.create_system = 3
                with source.open("rb") as input_stream, archive.open(info, "w") as output_stream:
                    copyfileobj(input_stream, output_stream, length=1024 * 1024)
        with ZipFile(target, "r") as archive:
            return {info.filename: (info.file_size, info.CRC) for info in archive.infolist()}
    except PackageFailure:
        raise
    except (OSError, ValueError, RuntimeError, BadZipFile, LargeZipFile, EOFError):
        raise PackageFailure("Unable to write package ZIP") from None


def verify_zip(path: Path, expected: list[str], sources: dict[str, tuple[int, int, str]]) -> None:
    """Verify archive integrity, exact names, source hashes, CRCs, and sizes."""
    try:
        with ZipFile(path, "r") as archive:
            if archive.testzip() is not None:
                raise PackageFailure("ZIP CRC test failed")
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or sorted(names) != sorted(expected):
                raise PackageFailure("ZIP entry set does not match expected set")
            if set(sources) != set(expected):
                raise PackageFailure("Source entry set does not match expected set")
            for info in infos:
                size_expected, crc_expected, sha_expected = sources[info.filename]
                data = archive.read(info)
                if (info.file_size != size_expected or info.CRC != crc_expected
                        or len(data) != size_expected or zlib.crc32(data) & 0xFFFFFFFF != crc_expected
                        or hashlib.sha256(data).hexdigest() != sha_expected):
                    raise PackageFailure(f"ZIP entry readback mismatch: {info.filename}")
    except PackageFailure:
        raise
    except (OSError, ValueError, BadZipFile, LargeZipFile, EOFError, KeyError):
        raise PackageFailure("Unable to verify package ZIP") from None


def _source_signatures(entries: list[tuple[str, Path]]) -> dict[str, tuple[int, int, str]]:
    signatures = {}
    try:
        for name, path in entries:
            digest = hashlib.sha256()
            crc = 0
            size = 0
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    size += len(chunk)
                    crc = zlib.crc32(chunk, crc)
                    digest.update(chunk)
            signatures[name] = (size, crc & 0xFFFFFFFF, digest.hexdigest())
    except OSError:
        raise PackageFailure("Unable to read package source") from None
    return signatures


def _write_report(path: Path, report: PackageReport) -> None:
    temporary = None
    failure = None
    payload = {"entries": report.entries, "excluded": report.excluded,
               "verified": report.verified, "built_at": report.built_at}
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=".package-report-",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
        temporary = None
    except (OSError, ValueError, TypeError):
        failure = PackageFailure("Unable to write package report")
        raise failure from None
    finally:
        if temporary is not None:
            try:
                _remove_generated(temporary, "temporary package report")
            except PackageFailure:
                if failure is None:
                    raise


def _remove_generated(path: Path, description: str) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        raise PackageFailure(f"Unable to remove {description}") from None


def _excluded_files(runs: dict[str, Path]) -> list[str]:
    packaged = {filename for filename in MATERIAL_FILES}
    excluded = []
    for material_id, run_dir in runs.items():
        for path in _scan_run(run_dir, material_id):
            relative = path.relative_to(run_dir).as_posix()
            if relative not in packaged:
                excluded.append(f"{material_id}/{relative}")
    return sorted(set(excluded))


def _verify_final_archive(path: Path, expected: list[str]) -> None:
    try:
        with ZipFile(path, "r") as archive:
            if archive.testzip() is not None:
                raise PackageFailure("Final ZIP CRC test failed")
            names = [info.filename for info in archive.infolist()]
            if len(names) != len(set(names)) or sorted(names) != sorted(expected):
                raise PackageFailure("Final ZIP entry set does not match expected set")
    except PackageFailure:
        raise
    except (OSError, ValueError, BadZipFile, EOFError):
        raise PackageFailure("Unable to reopen final package ZIP") from None


def _write_archive_atomically(entries: list[tuple[str, Path]], out_path: Path) -> dict[str, tuple[int, int, str]]:
    temporary = None
    published = False
    expected = [name for name, _ in entries]
    signatures = _source_signatures(entries)
    try:
        with tempfile.NamedTemporaryFile(dir=out_path.parent, prefix=".package-",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
        written = write_zip(entries, temporary)
        if set(written) != set(expected):
            raise PackageFailure("Written ZIP entry set does not match expected set")
        verified_sources = {name: (size, crc, signatures[name][2])
                            for name, (size, crc) in written.items()}
        verify_zip(temporary, expected, verified_sources)
        os.replace(temporary, out_path)
        temporary = None
        published = True
        _verify_final_archive(out_path, expected)
        return {name: (size, crc, signatures[name][2]) for name, (size, crc) in written.items()}
    except PackageFailure:
        if published:
            try:
                _remove_generated(out_path, "published package ZIP")
            except PackageFailure:
                pass
        raise
    except OSError:
        if published:
            try:
                _remove_generated(out_path, "published package ZIP")
            except PackageFailure:
                pass
        raise PackageFailure("Unable to publish package ZIP") from None
    finally:
        if temporary is not None:
            try:
                _remove_generated(temporary, "temporary package ZIP")
            except PackageFailure:
                pass


def build_package(materials: list[str], out_path: Path, settings: Settings) -> PackageReport:
    normalized = [material for material in materials if material]
    if not normalized or any(not _valid_material_id(material) for material in normalized):
        raise ConfigError("materials must contain safe material_id path segments")
    if len(normalized) != len(set(normalized)):
        raise ConfigError("Duplicate material_id")

    runs: dict[str, Path] = {}
    for material_id in normalized:
        run_dir = select_run(settings.paths.result_dir, material_id)
        final_path = run_dir / "final.md"
        try:
            if not final_path.is_file() or final_path.stat().st_size <= 0:
                raise PackageFailure(f"Required final file is missing or empty: {material_id}/final.md")
        except OSError:
            raise PackageFailure(f"Unable to inspect {material_id}/final.md") from None
        _scan_run(run_dir, material_id)
        try:
            report = validate_run(run_dir, settings)
        except Exception:
            raise PackageFailure(f"Unable to validate material {material_id}") from None
        try:
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise PackageFailure(f"Unable to read manifest for {material_id}") from None
        evaluate_gate(material_id, manifest, report)
        runs[material_id] = run_dir

    entries = collect_entries(runs)
    excluded = _excluded_files(runs)
    out_path = Path(out_path)
    report_path = out_path.parent / REPORT_NAME
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.name == REPORT_NAME or out_path.exists() or report_path.exists():
            raise PackageFailure("Output ZIP or package report already exists")
        for run_dir in runs.values():
            resolved_run = run_dir.resolve()
            if out_path.resolve().is_relative_to(resolved_run) or report_path.resolve().is_relative_to(resolved_run):
                raise PackageFailure("Package output cannot be inside a selected run")
    except PackageFailure:
        raise
    except OSError:
        raise PackageFailure("Unable to prepare package output directory") from None

    metadata = _write_archive_atomically(entries, out_path)
    package_entries = [{"name": name, "size": size, "crc32": crc}
                       for name, (size, crc, _) in sorted(metadata.items())]
    package_report = PackageReport(package_entries, excluded, True, utc_now())
    try:
        _write_report(report_path, package_report)
    except Exception:
        for path, description in ((out_path, "package ZIP after report failure"),
                                  (report_path, "package report after report failure")):
            try:
                _remove_generated(path, description)
            except PackageFailure:
                pass
        raise
    return package_report
