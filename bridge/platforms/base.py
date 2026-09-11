"""Platform boundary used by the Dotii management-center backend.

Business modules depend on this protocol instead of importing operating-system
APIs directly.  A platform implementation owns writable paths, login startup,
external-tool layouts, serial-port discovery, and subprocess presentation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Protocol


RunCommand = Callable[..., subprocess.CompletedProcess[str]]
WhichCommand = Callable[[str], str | None]


class PlatformAdapter(Protocol):
    """Operating-system services required by the shared Python backend."""

    name: str
    startup_available: bool
    bluetooth_available: bool
    bluetooth_pair_on_connect: bool
    serial_flash_available: bool

    def runtime_folder(self) -> Path: ...

    def hidden_creation_flags(self) -> int: ...

    def startup_command(self, python_executable: Path, app_script: Path) -> str: ...

    def packaged_startup_command(self, management_center: Path) -> str: ...

    def current_startup_command(
        self,
        *,
        frozen: bool,
        executable: Path,
        app_script: Path,
        management_center: Path,
    ) -> str: ...

    def startup_enabled(self) -> bool: ...

    def set_startup(self, enabled: bool, command: str) -> bool: ...

    def ffmpeg_candidates(self, runtime_folder: Path, tools_root: Path) -> list[Path]: ...

    def codex_cli_candidates(self, runtime_folder: Path, application_root: Path) -> list[Path]: ...

    def bundled_node_candidates(self, application_root: Path) -> list[Path]: ...

    def esptool_python_candidates(self) -> list[Path]: ...

    def scan_serial_ports(self, run: RunCommand) -> list[dict[str, object]]: ...

    def valid_serial_port(self, port: str) -> bool: ...
