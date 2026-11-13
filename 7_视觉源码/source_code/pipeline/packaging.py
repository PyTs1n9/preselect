"""W0 package boundary; ZIP building is implemented by A15."""

from pathlib import Path

from .contracts import PackageReport
from .settings import Settings


def build_package(materials: list[str], out_path: Path, settings: Settings) -> PackageReport:
    raise NotImplementedError("Package building is assigned to A15")
