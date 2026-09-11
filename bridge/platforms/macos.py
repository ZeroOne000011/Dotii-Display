"""macOS implementation of portable management-center services.

The packaged native host owns login items and application lifecycle. Resource,
external-tool, USB serial, and CoreBluetooth services are shared by source and
packaged application modes while keeping macOS behavior platform-scoped.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from .base import RunCommand


DOTII_USB_VID = 0x303A
DOTII_USB_PID = 0x1001
PORT_PATTERN = re.compile(r"^/dev/cu\.[A-Za-z0-9._-]{1,96}$")


def parse_serial_ports(ports: Iterable[Any]) -> list[dict[str, object]]:
    """Normalize pyserial port records without exposing USB serial numbers."""
    output: list[dict[str, object]] = []
    for item in ports:
        port = str(getattr(item, "device", "") or "")
        if not PORT_PATTERN.fullmatch(port):
            continue
        vid = getattr(item, "vid", None)
        pid = getattr(item, "pid", None)
        dotii = vid == DOTII_USB_VID and pid == DOTII_USB_PID
        description = str(getattr(item, "description", "") or port)
        identity = ""
        if isinstance(vid, int) and isinstance(pid, int):
            identity = f"USB VID:PID={vid:04X}:{pid:04X}"
        output.append({
            "port": port,
            "name": description[:120],
            "pnp_id": identity,
            "dotii": dotii,
        })
    output.sort(key=lambda item: (not bool(item["dotii"]), str(item["port"]).lower()))
    return output


class MacOSPlatformAdapter:
    name = "macos"
    bluetooth_available = True
    bluetooth_pair_on_connect = False
    serial_flash_available = True

    def runtime_folder(self) -> Path:
        testing_override = os.environ.get("DOTII_RUNTIME_DIR")
        if testing_override and Path(testing_override).is_absolute():
            return Path(testing_override)
        return Path.home() / "Library" / "Application Support" / "Dotii"

    def _login_item_executable(self) -> Path | None:
        executable = Path(sys.executable).resolve()
        for parent in executable.parents:
            if parent.name == "Contents" and parent.parent.suffix.lower() == ".app":
                candidate = parent / "MacOS" / "DotiiManagementCenter"
                return candidate if candidate.is_file() else None
        return None

    @property
    def startup_available(self) -> bool:
        return self._login_item_executable() is not None

    def hidden_creation_flags(self) -> int:
        return 0

    def startup_command(self, python_executable: Path, app_script: Path) -> str:
        raise NotImplementedError("macOS login startup requires the native app host and SMAppService")

    def packaged_startup_command(self, management_center: Path) -> str:
        if not management_center.is_file():
            raise OSError("当前安装包缺少 macOS 菜单栏宿主")
        return str(management_center.resolve())

    def current_startup_command(
        self,
        *,
        frozen: bool,
        executable: Path,
        app_script: Path,
        management_center: Path,
    ) -> str:
        del executable, app_script
        if not frozen:
            raise OSError("请从正式 Dotii 管理中心应用中设置登录启动")
        return self.packaged_startup_command(management_center)

    def startup_enabled(self) -> bool:
        executable = self._login_item_executable()
        if executable is None:
            return False
        try:
            result = subprocess.run(
                [str(executable), "--login-item", "status"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0 and result.stdout.strip() == "enabled"

    def set_startup(self, enabled: bool, command: str) -> bool:
        executable = Path(command) if enabled and command else self._login_item_executable()
        if executable is None or not executable.is_file():
            raise OSError("请从正式 Dotii 管理中心应用中设置登录启动")
        action = "enable" if enabled else "disable"
        try:
            result = subprocess.run(
                [str(executable), "--login-item", action],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise OSError(f"无法更新 macOS 登录启动：{error}") from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:200] or "系统服务返回失败"
            raise OSError(f"无法更新 macOS 登录启动：{detail}")
        return self.startup_enabled()

    def ffmpeg_candidates(self, runtime_folder: Path, tools_root: Path) -> list[Path]:
        return [
            runtime_folder / "tools" / "ffmpeg" / "bin" / "ffmpeg",
            tools_root / "ffmpeg" / "bin" / "ffmpeg",
            Path("/opt/homebrew/bin/ffmpeg"),
            Path("/usr/local/bin/ffmpeg"),
        ]

    def codex_cli_candidates(self, runtime_folder: Path, application_root: Path) -> list[Path]:
        return [
            runtime_folder / "codex-cli" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
            application_root / "tools" / "codex-cli" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
            application_root / "tools" / "codex" / "bin" / "codex",
            Path.home() / ".npm-global" / "lib" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
            Path.home() / ".npm-global" / "bin" / "codex",
            Path.home() / ".local" / "bin" / "codex",
            Path("/opt/homebrew/bin/codex"),
            Path("/usr/local/bin/codex"),
        ]

    def bundled_node_candidates(self, application_root: Path) -> list[Path]:
        return [
            application_root / "tools" / "node" / "bin" / "node",
            Path.home() / ".local" / "bin" / "node",
            Path("/opt/homebrew/bin/node"),
            Path("/usr/local/bin/node"),
        ]

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
        del run
        try:
            from serial.tools import list_ports
        except ImportError:
            return []
        try:
            return parse_serial_ports(list_ports.comports())
        except (OSError, TypeError, ValueError):
            return []

    def valid_serial_port(self, port: str) -> bool:
        return bool(PORT_PATTERN.fullmatch(port))
