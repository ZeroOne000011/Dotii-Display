"""BLE provisioning and recovery channel behind the active platform boundary."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path
from typing import Any, Callable

from platforms import PlatformAdapter, current_platform
from runtime_paths import is_frozen


SERVICE_UUID = "7b4e0001-4db4-4c72-a729-ea5187241a43"
COMMAND_UUID = "7b4e0002-4db4-4c72-a729-ea5187241a43"
RESPONSE_UUID = "7b4e0003-4db4-4c72-a729-ea5187241a43"
STATUS_UUID = "7b4e0004-4db4-4c72-a729-ea5187241a43"
MAX_CONFIG_BYTES = 1024
BLUETOOTH_AVAILABILITY_STATES = {"denied", "bluetooth_off", "unavailable"}
COREBLUETOOTH_IDENTIFIER_PATTERN = re.compile(
    r"\b[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\b"
)


def _is_stale_windows_pairing_error(error: BaseException) -> bool:
    message = str(error).casefold()
    return "could not start notify" in message and "unreachable" in message


def _bluetooth_error(error: Exception) -> tuple[str, str]:
    """Return a stable availability state and a user-facing diagnostic."""
    message = str(error)
    if "peer removed pairing information" in message.casefold():
        return (
            "stale_pairing",
            "Dotii 已重置配对，但这台 Mac 仍保留旧记录；请在系统设置 > 蓝牙中忽略 Dotii，然后重新扫描",
        )
    if "device with address" in message.casefold() and "was not found" in message.casefold():
        return "not_found", "暂时无法重新连接已识别的 Dotii，请点击“扫描 Dotii”重试"
    if isinstance(error, TimeoutError):
        return "timeout", "等待蓝牙连接或系统配对确认超时，请确认 Dotii 在附近后重试"
    reason = str(getattr(getattr(error, "reason", None), "name", "")).upper()
    if reason == "POWERED_OFF":
        return "bluetooth_off", "蓝牙已关闭，请先在系统设置中打开蓝牙"
    if reason in {"DENIED_BY_USER", "DENIED_BY_SYSTEM", "DENIED_BY_UNKNOWN"}:
        return (
            "denied",
            "蓝牙权限已被拒绝，请在系统设置 > 隐私与安全性 > 蓝牙中允许 Dotii 管理中心",
        )
    if reason in {"NO_BLUETOOTH", "NO_BLE_CENTRAL_ROLE"}:
        return "unavailable", "当前 Mac 没有可用的低功耗蓝牙功能"
    if reason == "UNKNOWN":
        return "unavailable", "macOS 暂时无法使用蓝牙，请检查系统蓝牙与隐私设置"
    public_message = COREBLUETOOTH_IDENTIFIER_PATTERN.sub("本机蓝牙标识", message)
    return "error", public_message[:170] or "未知蓝牙错误"


def _hidden_creation_flags() -> int:
    return current_platform().hidden_creation_flags()


def _configuration_payload(
    *,
    ssid: Any,
    password: Any,
    bridge_url: str,
    bridge_token: str,
    current_token: Any = "",
) -> bytes:
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32:
        raise ValueError("Wi-Fi 名称必须为 1–32 字节")
    if not isinstance(password, str) or len(password.encode("utf-8")) > 64:
        raise ValueError("Wi-Fi 密码不能超过 64 字节")
    if not isinstance(bridge_url, str) or not bridge_url.startswith(("http://", "https://")):
        raise ValueError("管理中心地址无效")
    if len(bridge_url.encode("utf-8")) >= 256:
        raise ValueError("管理中心地址过长")
    if not isinstance(bridge_token, str) or not 16 <= len(bridge_token) <= 64:
        raise ValueError("设备访问令牌无效")
    if not isinstance(current_token, str):
        raise ValueError("当前设备令牌无效")
    if current_token and not 16 <= len(current_token) <= 64:
        raise ValueError("当前设备令牌无效")
    body = json.dumps(
        {
            "v": 1,
            "op": "configure",
            "auth": current_token or bridge_token,
            "ssid": ssid,
            "password": password,
            "bridge_url": bridge_url,
            "bridge_token": bridge_token,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(body) > MAX_CONFIG_BYTES:
        raise ValueError("蓝牙配置数据过长")
    return body


def _configuration_packets(body: bytes, chunk_size: int = 180) -> list[bytes]:
    if not 1 <= len(body) <= MAX_CONFIG_BYTES or not 1 <= chunk_size <= 240:
        raise ValueError("invalid Bluetooth packet size")
    checksum = zlib.crc32(body) & 0xFFFFFFFF
    packets = [bytes((1, len(body) & 0xFF, len(body) >> 8)) + checksum.to_bytes(4, "little")]
    packets.extend(bytes((2,)) + body[offset : offset + chunk_size]
                   for offset in range(0, len(body), chunk_size))
    packets.append(bytes((3,)))
    return packets


class BluetoothBridge:
    def __init__(
        self,
        runtime_folder: Path,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        platform_adapter: PlatformAdapter | None = None,
    ) -> None:
        self.runtime_folder = runtime_folder
        self.dependencies = runtime_folder / "bluetooth-deps"
        self.settings_path = runtime_folder / "bluetooth.json"
        self.runner = runner
        self.platform = platform_adapter or current_platform()
        self.lock = threading.RLock()
        self.operation_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.monitor: threading.Thread | None = None
        self.devices: list[dict[str, Any]] = []
        self._ble_devices: dict[str, Any] = {}
        self.last_address = self._load_last_address()
        self.device_status: dict[str, Any] = {}
        self.device_connected = False
        self.connection_state = "idle"
        self.connection_detail = ""
        self.operation_state = "idle"
        self.operation_detail = "尚未扫描 Dotii"
        self.operation_log = ""
        self.updated_at_epoch = 0
        self.permission_state = "unknown" if self.platform.name == "macos" else "not_applicable"
        self._add_dependency_path()

    def _add_dependency_path(self) -> None:
        path = str(self.dependencies)
        if path not in sys.path:
            sys.path.insert(0, path)
        importlib.invalidate_caches()

    def _load_last_address(self) -> str:
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
            address = payload.get("address") if isinstance(payload, dict) else ""
            return str(address)[:80] if isinstance(address, str) else ""
        except (OSError, ValueError, json.JSONDecodeError):
            return ""

    def _save_last_address(self, address: str) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"address": address}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(self.settings_path)

    def _bleak(self) -> tuple[Any, Any]:
        self._add_dependency_path()
        from bleak import BleakClient, BleakScanner  # type: ignore[import-not-found]
        return BleakClient, BleakScanner

    def dependency_ready(self) -> bool:
        if not self.platform.bluetooth_available:
            return False
        try:
            self._bleak()
            return True
        except (ImportError, OSError):
            return False

    def start(self) -> None:
        if not self.platform.bluetooth_available:
            return
        if self.monitor is None or not self.monitor.is_alive():
            self.monitor = threading.Thread(target=self._monitor, name="dotii-ble-monitor", daemon=True)
            self.monitor.start()
        if self.platform.name == "macos" and not self.last_address and self.dependency_ready():
            if self._start_worker(self._scan, name="dotii-ble-startup-scan"):
                with self.lock:
                    self.operation_detail = "正在自动识别附近的 Dotii"

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "available": self.platform.bluetooth_available,
                "dependency_ready": self.dependency_ready(),
                "permission_state": self.permission_state,
                "devices": [dict(item) for item in self.devices],
                "last_address": self.last_address,
                "device_status": dict(self.device_status),
                "device_connected": self.device_connected,
                "connection_state": self.connection_state,
                "connection_detail": self.connection_detail,
                "operation_state": self.operation_state,
                "operation_detail": self.operation_detail,
                "operation_log": self.operation_log,
                "updated_at_epoch": self.updated_at_epoch,
            }

    def _start_worker(self, target: Callable[..., None], *arguments: Any, name: str) -> bool:
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                return False
            self.operation_state = "running"
            self.operation_detail = "正在处理蓝牙连接"
            self.operation_log = ""
            self.updated_at_epoch = int(time.time())
            self.worker = threading.Thread(target=target, args=arguments, name=name, daemon=True)
            self.worker.start()
            return True

    def start_install(self) -> bool:
        if not self.platform.bluetooth_available:
            raise OSError("当前平台尚未支持 Dotii 蓝牙配网")
        if is_frozen():
            if self.dependency_ready():
                return False
            raise OSError("当前安装包缺少蓝牙组件，请重新安装 Dotii 管理中心")
        return self._start_worker(self._install, name="dotii-ble-installer")

    def _install(self) -> None:
        state = "error"
        detail = "蓝牙组件安装失败"
        output = ""
        try:
            self.dependencies.mkdir(parents=True, exist_ok=True)
            result = self.runner(
                [sys.executable, "-m", "pip", "install", "--target", str(self.dependencies),
                 "--upgrade", "bleak>=1.0,<2"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", timeout=600,
                creationflags=_hidden_creation_flags(), check=False,
            )
            output = result.stdout or ""
            if result.returncode != 0:
                raise OSError(f"pip 退出码 {result.returncode}")
            self._add_dependency_path()
            if not self.dependency_ready():
                raise OSError("安装结束，但蓝牙组件仍无法加载")
            state = "success"
            detail = "蓝牙组件已就绪"
        except (OSError, subprocess.SubprocessError) as error:
            detail = f"蓝牙组件安装失败：{error}"
        finally:
            lines = [line.strip() for line in output.replace("\r", "").split("\n") if line.strip()]
            with self.lock:
                self.operation_state = state
                self.operation_detail = detail[:240]
                self.operation_log = "\n".join(lines[-30:])[-5000:]
                self.updated_at_epoch = int(time.time())

    def start_scan(self) -> bool:
        if not self.platform.bluetooth_available:
            raise ValueError("当前平台尚未支持 Dotii 蓝牙配网")
        if not self.dependency_ready():
            raise ValueError("请先安装蓝牙连接组件")
        return self._start_worker(self._scan, name="dotii-ble-scan")

    async def _discover(self) -> list[dict[str, Any]]:
        _, scanner = self._bleak()
        scan_timeout = 10.0 if self.platform.name == "macos" else 5.0
        discovered = await scanner.discover(timeout=scan_timeout, return_adv=True)
        output: list[dict[str, Any]] = []
        native_devices: dict[str, Any] = {}
        pairs = discovered.values() if isinstance(discovered, dict) else ((item, None) for item in discovered)
        for device, advertisement in pairs:
            name = str(getattr(advertisement, "local_name", "") or getattr(device, "name", "") or "")
            services = [str(value).lower() for value in getattr(advertisement, "service_uuids", [])]
            if name.lower().startswith("dotii") or SERVICE_UUID in services:
                address = str(device.address)[:80]
                native_devices[address] = device
                output.append({
                    "address": address,
                    "name": name[:80] or "Dotii",
                    "rssi": int(getattr(advertisement, "rssi", -127)),
                })
        output.sort(key=lambda item: item["rssi"], reverse=True)
        with self.lock:
            self._ble_devices = native_devices
        return output

    async def _discover_with_status(
        self,
    ) -> tuple[list[dict[str, Any]], str, dict[str, Any], Exception | None]:
        """Discover and connect on one event loop for CoreBluetooth objects."""
        devices = await self._discover()
        known_addresses = {item["address"] for item in devices}
        with self.lock:
            remembered_address = self.last_address
        selected_address = remembered_address if remembered_address in known_addresses else (
            devices[0]["address"] if devices else ""
        )
        if not selected_address:
            return devices, "", {}, None
        try:
            status = await self._read_status(selected_address)
            return devices, selected_address, status, None
        except Exception as error:  # Bleak exposes backend-specific failures.
            return devices, selected_address, {}, error

    def _scan(self) -> None:
        try:
            with self.operation_lock:
                devices, selected_address, status, connection_error = asyncio.run(
                    self._discover_with_status()
                )
            with self.lock:
                if self.platform.name == "macos":
                    # CoreBluetooth BLEDevice objects belong to the discovery
                    # event loop. Persist only the stable UUID across workers.
                    self._ble_devices = {}
                self.devices = devices
                self.device_status = status
                self.device_connected = bool(status)
                self.connection_state = "connected" if status else "idle"
                self.connection_detail = ""
                if selected_address and selected_address != self.last_address:
                    self.last_address = selected_address
            scan_permission_state = "allowed"
            if connection_error is not None:
                connection_state, connection_detail = _bluetooth_error(connection_error)
                if connection_state in BLUETOOTH_AVAILABILITY_STATES:
                    scan_permission_state = connection_state
                with self.lock:
                    self.connection_state = connection_state
                    self.connection_detail = connection_detail
            if selected_address:
                self._save_last_address(selected_address)
            with self.lock:
                self.permission_state = scan_permission_state
                self.operation_state = "success"
                self.operation_detail = (
                    f"发现 {len(devices)} 台 Dotii，已读取当前设备状态"
                    if devices and self.device_connected
                    else f"发现 {len(devices)} 台 Dotii；{self.connection_detail or '状态连接暂未完成'}"
                    if devices
                    else "未发现附近的 Dotii"
                )
                self.updated_at_epoch = int(time.time())
        except Exception as error:  # BLE backends expose platform-specific exceptions.
            permission_state, detail = _bluetooth_error(error)
            with self.lock:
                self.permission_state = permission_state
                self.operation_state = "error"
                self.operation_detail = f"蓝牙扫描失败：{detail}"
                self.updated_at_epoch = int(time.time())

    def start_configure(
        self,
        *,
        address: Any,
        ssid: Any,
        password: Any,
        bridge_url: str,
        bridge_token: str,
        current_token: Any = "",
    ) -> bool:
        if not self.platform.bluetooth_available:
            raise ValueError("当前平台尚未支持 Dotii 蓝牙配网")
        if not self.dependency_ready():
            raise ValueError("请先安装蓝牙连接组件")
        if not isinstance(address, str) or not address or len(address) > 80:
            raise ValueError("请选择 Dotii")
        known = {item["address"] for item in self.devices}
        if address not in known and address != self.last_address:
            raise ValueError("请重新扫描并选择 Dotii")
        body = _configuration_payload(
            ssid=ssid,
            password=password,
            bridge_url=bridge_url,
            bridge_token=bridge_token,
            current_token=current_token,
        )
        return self._start_worker(self._configure, address, body, name="dotii-ble-configure")

    async def _configure_once(self, client_class: Any, target: Any, body: bytes) -> dict[str, Any]:
        response_event = asyncio.Event()
        response: dict[str, Any] = {}

        def notification(_: Any, data: bytearray) -> None:
            nonlocal response
            try:
                payload = json.loads(bytes(data).decode("utf-8"))
                if isinstance(payload, dict):
                    response = payload
                    if payload.get("state") == "restarting" or payload.get("ok") is False:
                        response_event.set()
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass

        # Windows requests pairing during connect. CoreBluetooth has no explicit
        # pairing API and prompts when the encrypted characteristic is accessed.
        async with client_class(
            target,
            timeout=45.0 if self.platform.name == "macos" else 30.0,
            pair=self.platform.bluetooth_pair_on_connect,
        ) as client:
            await client.start_notify(RESPONSE_UUID, notification)
            for packet in _configuration_packets(body):
                await client.write_gatt_char(COMMAND_UUID, packet, response=True)
            try:
                await asyncio.wait_for(response_event.wait(), timeout=8.0)
            except TimeoutError:
                raw = await client.read_gatt_char(RESPONSE_UUID)
                response = json.loads(bytes(raw).decode("utf-8"))
            return response

    async def _configure_async(self, address: str, body: bytes) -> dict[str, Any]:
        client_class, _ = self._bleak()
        with self.lock:
            target = address if self.platform.name == "macos" else self._ble_devices.get(address, address)

        try:
            return await self._configure_once(client_class, target, body)
        except Exception as error:
            if self.platform.name != "windows" or not _is_stale_windows_pairing_error(error):
                raise

            with self.lock:
                self.operation_detail = "检测到旧蓝牙配对，正在自动重新配对"
                self.updated_at_epoch = int(time.time())

            recovery_client = client_class(target, timeout=15.0)
            try:
                await recovery_client.unpair()
            except Exception as recovery_error:
                raise OSError(
                    f"检测到失效的 Windows 蓝牙配对，但自动清除失败：{recovery_error}"
                ) from error
            finally:
                if getattr(recovery_client, "is_connected", False):
                    await recovery_client.disconnect()

            await asyncio.sleep(0.75)
            return await self._configure_once(client_class, target, body)

    def _configure(self, address: str, body: bytes) -> None:
        try:
            with self.operation_lock:
                response = asyncio.run(self._configure_async(address, body))
            if response.get("ok") is not True:
                raise OSError(f"Dotii 拒绝配置：{response.get('error', 'unknown')}" )
            self.last_address = address
            self._save_last_address(address)
            with self.lock:
                self.permission_state = "allowed"
                self.operation_state = "success"
                self.operation_detail = "配置已保存，Dotii 正在重启并连接局域网"
                self.device_connected = False
                self.connection_state = "restarting"
                self.connection_detail = "Dotii 正在重启"
                self.updated_at_epoch = int(time.time())
        except Exception as error:
            permission_state, detail = _bluetooth_error(error)
            with self.lock:
                if permission_state in BLUETOOTH_AVAILABILITY_STATES:
                    self.permission_state = permission_state
                self.operation_state = "error"
                self.operation_detail = f"蓝牙配置失败：{detail}"
                self.updated_at_epoch = int(time.time())

    async def _read_status(self, address: str) -> dict[str, Any]:
        client_class, _ = self._bleak()
        with self.lock:
            target = self._ble_devices.get(address, address)
        async with client_class(target, timeout=10.0) as client:
            raw = await client.read_gatt_char(STATUS_UUID)
            payload = json.loads(bytes(raw).decode("utf-8"))
            return payload if isinstance(payload, dict) else {}

    def _monitor(self) -> None:
        first_run = True
        while first_run or not self.stop_event.wait(15):
            first_run = False
            if not self.last_address or not self.dependency_ready() or not self.operation_lock.acquire(False):
                continue
            try:
                status = asyncio.run(self._read_status(self.last_address))
                with self.lock:
                    self.permission_state = "allowed"
                    self.device_status = status
                    self.device_connected = bool(status)
                    self.connection_state = "connected" if status else "error"
                    self.connection_detail = "" if status else "设备状态响应为空"
                    if status and not any(
                        item.get("address") == self.last_address for item in self.devices
                    ):
                        self.devices.append({
                            "address": self.last_address,
                            "name": str(status.get("name") or "Dotii")[:80],
                            "rssi": None,
                            "remembered": True,
                        })
            except Exception as error:
                permission_state, detail = _bluetooth_error(error)
                with self.lock:
                    if permission_state in BLUETOOTH_AVAILABILITY_STATES:
                        self.permission_state = permission_state
                    self.device_connected = False
                    if (
                        self.connection_state != "stale_pairing"
                        or permission_state in BLUETOOTH_AVAILABILITY_STATES
                    ):
                        self.connection_state = permission_state
                        self.connection_detail = detail
                    if not any(
                        item.get("address") == self.last_address for item in self.devices
                    ):
                        self.devices.append({
                            "address": self.last_address,
                            "name": str(self.device_status.get("name") or "Dotii")[:80],
                            "rssi": None,
                            "remembered": True,
                        })
            finally:
                self.operation_lock.release()

    def stop(self) -> None:
        self.stop_event.set()
