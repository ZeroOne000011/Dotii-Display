"""Locate application resources in both source and frozen Windows builds."""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """Return whether the bridge is running from a PyInstaller executable."""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """Return the read-only root containing bundled data files."""
    if is_frozen():
        extracted = getattr(sys, "_MEIPASS", None)
        if extracted:
            return Path(extracted).resolve()
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def application_root() -> Path:
    """Return the directory used as the process working root."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def tools_root() -> Path:
    """Return the optional application-private external tools directory."""
    return application_root() / "tools"


def project_root() -> Path:
    """Return the source or packaged root containing firmware build files."""
    return bundle_root() if is_frozen() else application_root()


def resource_path(*parts: str) -> Path:
    return bundle_root().joinpath(*parts)


def sibling_executable(name: str) -> Path:
    """Return a packaged sibling executable, or its source-tree equivalent."""
    if is_frozen():
        return application_root() / name
    return bundle_root() / name
