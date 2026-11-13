"""Automatic discovery helpers for stage modules in this package."""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType


def discover_modules() -> list[ModuleType]:
    package_name = __name__
    modules: list[ModuleType] = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda item: item.name):
        module = importlib.import_module(f"{package_name}.{info.name}")
        if hasattr(module, "STAGE_NAME"):
            modules.append(module)
    return modules
