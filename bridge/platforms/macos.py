"""macOS platform scaffold.

This adapter intentionally exposes only safe, already-portable behavior. Login
items, CoreBluetooth provisioning, serial discovery, and signed app lifecycle
remain explicit follow-up work and are therefore reported as unavailable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .base import RunCommand


class MacOSPlatformAdapter:
    name = "macos"
    startup_available = False
    bluetooth_available = False
    serial_flash_available = False

    def runtime_folder(self) -> Path:
        return Path.home() / "Library" / "Application Support" / "Dotii"

    def hidden_creation_flags(self) -> int:
        return 0

    def startup_command(self, python_executable: Path, app_script: Path) -> str:
        raise NotImplementedError("macOS login startup requires the native app host and SMAppService")

    def packaged_startup_command(self, management_center: Path) -> str:
        raise NotImplementedError("macOS login startup requires the native app host and SMAppService")

    def current_startup_command(
        self,
        *,
        frozen: bool,
        executable: Path,
        app_script: Path,
        management_center: Path,
    ) -> str:
        raise NotImplementedError("macOS login startup requires the native app host and SMAppService")

    def startup_enabled(self) -> bool:
        return False

    def set_startup(self, enabled: bool, command: str) -> bool:
        if enabled:
            raise NotImplementedError("macOS login startup requires the native app host and SMAppService")
        return False

    def ffmpeg_candidates(self, runtime_folder: Path, tools_root: Path) -> list[Path]:
        return [
            runtime_folder / "tools" / "ffmpeg" / "bin" / "ffmpeg",
            tools_root / "ffmpeg" / "bin" / "ffmpeg",
        ]

    def codex_cli_candidates(self, runtime_folder: Path, application_root: Path) -> list[Path]:
        return [
            runtime_folder / "codex-cli" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
            application_root / "tools" / "codex-cli" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
            Path.home() / ".npm-global" / "lib" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
        ]

    def bundled_node_candidates(self, application_root: Path) -> list[Path]:
        return [application_root / "tools" / "node" / "bin" / "node"]

    def esptool_python_candidates(self) -> list[Path]:
        output = [Path(sys.executable)]
        configured = os.environ.get("IDF_PYTHON_ENV_PATH")
        if configured:
            output.append(Path(configured) / "bin" / "python")
        root = Path(os.environ.get("IDF_TOOLS_PATH", str(Path.home() / ".espressif"))) / "python_env"
        if root.is_dir():
            output.extend(sorted(root.glob("*/bin/python"), reverse=True))
        return output

    def scan_serial_ports(self, run: RunCommand) -> list[dict[str, object]]:
        return []

    def valid_serial_port(self, port: str) -> bool:
        return False
