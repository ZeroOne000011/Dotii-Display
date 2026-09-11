"""Locate application resources in source and packaged builds."""

from __future__ import annotations

import sys
from pathlib import Path

from platforms import current_platform


def is_frozen() -> bool:
    """Return whether the bridge is running from a PyInstaller executable."""
    return bool(getattr(sys, "frozen", False))


def macos_app_contents(executable: Path | None = None) -> Path | None:
    """Return the enclosing ``.app/Contents`` directory when one exists."""
    candidate = (executable or Path(sys.executable)).resolve()
    for parent in candidate.parents:
        if parent.name != "Contents":
            continue
        if parent.parent.suffix.lower() == ".app":
            return parent
    return None


def application_resources_root() -> Path:
    """Return persistent read-only resources shipped with the application."""
    if is_frozen() and sys.platform == "darwin":
        contents = macos_app_contents()
        if contents is not None:
            return contents / "Resources"
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def auxiliary_executables_root() -> Path:
    """Return the directory containing packaged helper executables."""
    if is_frozen() and sys.platform == "darwin":
        contents = macos_app_contents()
        if contents is not None:
            return contents / "MacOS"
    return Path(sys.executable).resolve().parent if is_frozen() else Path(__file__).resolve().parent


def bundle_root() -> Path:
    """Return the read-only root containing bundled data files."""
    if is_frozen():
        extracted = getattr(sys, "_MEIPASS", None)
        if extracted:
            return Path(extracted).resolve()
        return application_resources_root()
    return Path(__file__).resolve().parent


def application_root() -> Path:
    """Return the directory used as the process working root."""
    if is_frozen():
        return application_resources_root()
    return Path(__file__).resolve().parent.parent


def tools_root() -> Path:
    """Return the optional application-private external tools directory."""
    return application_root() / "tools"


def runtime_folder() -> Path:
    """Return the active platform's writable application-data directory."""
    return current_platform().runtime_folder()


def project_root() -> Path:
    """Return the source or packaged root containing firmware build files."""
    return bundle_root() if is_frozen() else application_root()


def resource_path(*parts: str) -> Path:
    return bundle_root().joinpath(*parts)


def sibling_executable(name: str) -> Path:
    """Return a packaged sibling executable, or its source-tree equivalent."""
    if is_frozen():
        return auxiliary_executables_root() / name
    return bundle_root() / name
