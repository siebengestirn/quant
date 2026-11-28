"""Shared importlib loader for standalone offline test modules."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_test_module(name: str, path: Path) -> ModuleType:
    """Import a module from ``path`` and register it under ``name``."""
    module_spec = importlib.util.spec_from_file_location(name, path)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"no loader available for {name} at {path}")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[name] = module
    module_spec.loader.exec_module(module)
    return module
