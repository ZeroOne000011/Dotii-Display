"""Safe, bridge-managed flashing for packaged Dotii ESP32-S3 firmware."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from runtime_paths import is_frozen


DOTII_USB_VID = "303A"
DOTII_USB_PID = "1001"
PORT_PATTERN = re.compile(r"^COM(?:[1-9]|[1-9][0-9]|[12][0-9]{2})$", re.IGNORECASE)
PROGRESS_PATTERN = re.compile(r"(?:Writing|Hash of data verified|Hard resetting|Stub running).*?(\d+(?:\.\d+)?)%?", re.I)
PROJECT_VERSION_PATTERN = re.compile(
    r'^\s*set\s*\(\s*PROJECT_VER\s+"?([^"\s\)]+)', re.MULTILINE
)


def _hidden_creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _project_version(project_root: Path) -> str:
    """Read the firmware fallback version from the single CMake source of truth."""
    try:
        cmake = (project_root / "CMakeLists.txt").read_text(encoding="utf-8")
    except OSError:
        return ""
    match = PROJECT_VERSION_PATTERN.search(cmake)
    return match.group(1)[:48] if match else ""


def _firmware_root(project_root: Path) -> Path:
    """Resolve the shared VBS/EXE firmware bundle, with a build-tree fallback."""
    for folder_name in ("firmware", "build"):
        candidate = (project_root / folder_name).resolve()
        if (candidate / "flasher_args.json").is_file():
            return candidate
    return (project_root / "firmware").resolve()


def _stub_data_ready(esptool_root: Path) -> bool:
    """Return whether esptool ships the ESP32-S3 flasher stub it will execute."""
    stub_root = esptool_root / "targets" / "stub_flasher"
    return any((stub_root / version / "esp32s3.json").is_file() for version in ("2", "1"))


def _local_esptool_ready() -> bool:
    try:
        import esptool
    except ImportError:
        return False
    package_file = getattr(esptool, "__file__", "")
    return bool(package_file) and _stub_data_ready(Path(package_file).resolve().parent)


def _parse_ports(payload: str) -> list[dict[str, Any]]:
    try:
        raw = json.loads(payload or "[]")
    except json.JSONDecodeError:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    output: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        port = str(item.get("DeviceID") or "").upper()
        pnp = str(item.get("PNPDeviceID") or "").upper()
        if not PORT_PATTERN.fullmatch(port):
            continue
        output.append({
            "port": port,
            "name": str(item.get("Name") or port)[:120],
            "pnp_id": pnp[:180],
            "dotii": f"VID_{DOTII_USB_VID}" in pnp and f"PID_{DOTII_USB_PID}" in pnp,
        })
    output.sort(key=lambda item: (not item["dotii"], int(item["port"][3:])))
    return output


class FirmwareFlasher:
    def __init__(
        self,
        project_root: Path,
        runtime_folder: Path,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    ) -> None:
        self.project_root = project_root.resolve()
        self.build_root = _firmware_root(self.project_root)
        self.runtime_folder = runtime_folder
        self.run = run
        self.popen = popen
        self.lock = threading.RLock()
        self.operation_state = "idle"
        self.operation_detail = "尚未开始烧录"
        self.progress = 0
        self.operation_log = ""
        self.started_at_epoch = 0
        self.updated_at_epoch = 0
        self.port = ""
        self.worker: threading.Thread | None = None
        self.process: subprocess.Popen[str] | None = None
        self._ports: list[dict[str, Any]] = []
        self._ports_checked_at = 0.0
        self.tool_python = self._find_esptool_python()
        self.package = self._load_package()

    def _python_candidates(self) -> list[Path]:
        output = [Path(sys.executable)]
        configured = os.environ.get("IDF_PYTHON_ENV_PATH")
        if configured:
            output.append(Path(configured) / "Scripts" / "python.exe")
        configured_tools = os.environ.get("IDF_TOOLS_PATH")
        if configured_tools:
            tools_python = Path(configured_tools) / "python"
            if tools_python.is_dir():
                output.extend(sorted(tools_python.glob("v*/venv/Scripts/python.exe"), reverse=True))
        # The VBS developer entry is normally launched outside an activated
        # ESP-IDF shell. Discover official installer layouts without baking a
        # user-specific path into the project.
        search_roots = [
            Path.home() / ".espressif" / "python_env",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Espressif" / "python_env",
            Path(os.environ.get("ProgramData", "")) / "Espressif" / "python_env",
        ]
        system_drive = os.environ.get("SystemDrive")
        if system_drive:
            search_roots.append(Path(f"{system_drive}\\Espressif") / "tools" / "python")
        for root in search_roots:
            if not root.is_dir():
                continue
            output.extend(sorted(root.glob("*/venv/Scripts/python.exe"), reverse=True))
            output.extend(sorted(root.glob("*/Scripts/python.exe"), reverse=True))
        unique: list[Path] = []
        for candidate in output:
            resolved = candidate.resolve() if candidate.exists() else candidate
            if resolved not in unique:
                unique.append(resolved)
        return unique

    def _esptool_command(self, *arguments: str) -> list[str]:
        if is_frozen():
            return [self.tool_python, "--esptool", *arguments]
        return [self.tool_python, "-m", "esptool", *arguments]

    def _find_esptool_python(self) -> str:
        if is_frozen():
            return str(Path(sys.executable).resolve()) if _local_esptool_ready() else ""
        probe = (
            "import pathlib,esptool;"
            "p=pathlib.Path(esptool.__file__).resolve().parent;"
            "s=p/'targets'/'stub_flasher';"
            "raise SystemExit(0 if any((s/v/'esp32s3.json').is_file() for v in ('2','1')) else 1)"
        )
        for candidate in self._python_candidates():
            if not candidate.is_file():
                continue
            try:
                result = self.run(
                    [str(candidate), "-c", probe],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5, creationflags=_hidden_creation_flags(), check=False,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode == 0:
                return str(candidate)
        return ""

    def _load_package(self) -> dict[str, Any]:
        manifest_path = self.build_root / "flasher_args.json"
        result: dict[str, Any] = {
            "ready": False, "version": "", "chip": "esp32s3", "files": [],
            "app_sha256": "", "app_size": 0, "error": "",
        }
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            flash_files = payload.get("flash_files")
            if not isinstance(flash_files, dict) or not flash_files:
                raise ValueError("烧录清单缺少 flash_files")
            files: list[dict[str, Any]] = []
            for raw_offset, relative in flash_files.items():
                offset = int(str(raw_offset), 0)
                target = (self.build_root / str(relative)).resolve()
                target.relative_to(self.build_root)
                if not target.is_file():
                    raise FileNotFoundError(f"缺少固件文件：{relative}")
                files.append({
                    "offset": f"0x{offset:X}", "path": str(target), "name": target.name,
                    "size": target.stat().st_size, "sha256": _sha256(target),
                })
            files.sort(key=lambda item: int(item["offset"], 0))
            app = next((item for item in files if item["offset"] == "0x10000"), None)
            if app is None:
                raise ValueError("烧录清单缺少应用镜像")
            version = _project_version(self.project_root)
            if self.tool_python:
                info = self.run(
                    self._esptool_command("image-info", app["path"]),
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", timeout=15,
                    creationflags=_hidden_creation_flags(), check=False,
                )
                match = re.search(r"App version:\s*([^\r\n]+)", info.stdout or "")
                if match:
                    version = match.group(1).strip()[:48]
            result.update({
                "ready": True, "version": version, "files": files,
                "app_sha256": app["sha256"], "app_size": app["size"],
            })
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as error:
            result["error"] = str(error)[:240]
        return result

    def refresh(self) -> None:
        self.tool_python = self._find_esptool_python()
        self.package = self._load_package()
        self.scan_ports(force=True)

    def scan_ports(self, *, force: bool = False) -> list[dict[str, Any]]:
        with self.lock:
            if not force and time.monotonic() - self._ports_checked_at < 3:
                return [dict(item) for item in self._ports]
        command = (
            "Get-CimInstance Win32_SerialPort | "
            "Select-Object DeviceID,Name,PNPDeviceID | ConvertTo-Json -Compress"
        )
        try:
            powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or "powershell.exe"
            result = self.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8-sig", errors="replace", timeout=8,
                creationflags=_hidden_creation_flags(), check=False,
            )
            ports = _parse_ports(result.stdout) if result.returncode == 0 else []
        except (OSError, subprocess.SubprocessError):
            ports = []
        with self.lock:
            self._ports = ports
            self._ports_checked_at = time.monotonic()
            return [dict(item) for item in ports]

    def snapshot(self) -> dict[str, Any]:
        ports = self.scan_ports()
        with self.lock:
            package = {key: value for key, value in self.package.items() if key != "files"}
            package["files"] = [
                {key: value for key, value in item.items() if key != "path"}
                for item in self.package.get("files", [])
            ]
            return {
                "tool_ready": bool(self.tool_python),
                "package": package,
                "ports": ports,
                "operation_state": self.operation_state,
                "operation_detail": self.operation_detail,
                "progress": self.progress,
                "operation_log": self.operation_log,
                "port": self.port,
                "started_at_epoch": self.started_at_epoch,
                "updated_at_epoch": self.updated_at_epoch,
            }

    def start_flash(self, port: Any) -> bool:
        if not isinstance(port, str) or not PORT_PATTERN.fullmatch(port.upper()):
            raise ValueError("请选择有效的串口")
        port = port.upper()
        ports = self.scan_ports(force=True)
        selected = next((item for item in ports if item["port"] == port), None)
        if selected is None:
            raise ValueError("所选串口当前不可用")
        if not selected["dotii"]:
            raise ValueError("所选设备不是已识别的 Dotii ESP32-S3")
        if not self.tool_python:
            raise ValueError("未找到 esptool 烧录组件")
        if not self.package.get("ready"):
            raise ValueError(self.package.get("error") or "固件包不可用")
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                return False
            self.operation_state = "running"
            self.operation_detail = "正在准备 Dotii"
            self.progress = 1
            self.operation_log = ""
            self.port = port
            self.started_at_epoch = int(time.time())
            self.updated_at_epoch = self.started_at_epoch
            self.worker = threading.Thread(target=self._flash, args=(port,), name="dotii-flasher", daemon=True)
            self.worker.start()
            return True

    def _set_progress(self, progress: int, detail: str, lines: list[str]) -> None:
        with self.lock:
            self.progress = max(self.progress, min(99, progress))
            self.operation_detail = detail[:160]
            self.operation_log = "\n".join(lines[-30:])[-5000:]
            self.updated_at_epoch = int(time.time())

    def _flash(self, port: str) -> None:
        lines: list[str] = []
        files = self.package["files"]
        command = self._esptool_command(
            "--chip", "esp32s3", "--port", port,
            "--baud", "460800", "--before", "default-reset", "--after", "hard-reset",
            "write-flash", "--flash-mode", "dio", "--flash-freq", "80m", "--flash-size", "16MB",
        )
        for item in files:
            command.extend((item["offset"], item["path"]))
        state = "error"
        detail = "烧录失败"
        try:
            process = self.popen(
                command, cwd=str(self.build_root), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", bufsize=1,
                creationflags=_hidden_creation_flags(),
            )
            with self.lock:
                self.process = process
            if process.stdout is None:
                raise OSError("无法读取烧录进度")
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                # Paths and command-line secrets are never included in the user-facing log.
                public_line = re.sub(r"[A-Za-z]:\\[^\s]+", "<firmware>", line)
                lines.append(public_line[:300])
                lower = line.lower()
                if "connecting" in lower:
                    self._set_progress(8, "正在连接 Dotii", lines)
                elif "chip is esp32-s3" in lower or "chip type" in lower:
                    self._set_progress(12, "已识别 ESP32-S3", lines)
                elif "writing at" in lower:
                    percentages = re.findall(r"(\d+(?:\.\d+)?)%", line)
                    value = float(percentages[-1]) if percentages else 0
                    self._set_progress(15 + int(value * 0.75), "正在写入固件", lines)
                elif "hash of data verified" in lower:
                    self._set_progress(94, "正在校验固件", lines)
                elif "hard resetting" in lower:
                    self._set_progress(98, "正在重新启动 Dotii", lines)
            returncode = process.wait()
            if returncode != 0:
                raise OSError(f"esptool 退出码 {returncode}")
            state = "success"
            detail = "烧录完成，Dotii 已重新启动"
        except (OSError, subprocess.SubprocessError) as error:
            lines.append(str(error)[:300])
            detail = f"烧录失败：{error}"
        finally:
            with self.lock:
                self.process = None
                self.operation_state = state
                self.operation_detail = detail[:200]
                self.operation_log = "\n".join(lines[-30:])[-5000:]
                self.progress = 100 if state == "success" else self.progress
                self.updated_at_epoch = int(time.time())

    def stop(self) -> None:
        with self.lock:
            process = self.process
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
