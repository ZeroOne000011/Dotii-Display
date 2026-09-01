"""Windows BLE provisioning and recovery channel for Dotii."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path
from typing import Any, Callable

from runtime_paths import is_frozen


SERVICE_UUID = "7b4e0001-4db4-4c72-a729-ea5187241a43"
COMMAND_UUID = "7b4e0002-4db4-4c72-a729-ea5187241a43"
RESPONSE_UUID = "7b4e0003-4db4-4c72-a729-ea5187241a43"
STATUS_UUID = "7b4e0004-4db4-4c72-a729-ea5187241a43"
MAX_CONFIG_BYTES = 1024


def _hidden_creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _configuration_payload(
    *, ssid: Any, password: Any, bridge_url: str, bridge_token: str
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
    body = json.dumps(
        {
            "v": 1,
            "op": "configure",
            "auth": bridge_token,
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
    ) -> None:
        self.runtime_folder = runtime_folder
        self.dependencies = runtime_folder / "bluetooth-deps"
        self.settings_path = runtime_folder / "bluetooth.json"
        self.runner = runner
        self.lock = threading.RLock()
        self.operation_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.monitor: threading.Thread | None = None
        self.devices: list[dict[str, Any]] = []
        self._ble_devices: dict[str, Any] = {}
        self.last_address = self._load_last_address()
        self.device_status: dict[str, Any] = {}
        self.operation_state = "idle"
        self.operation_detail = "尚未扫描 Dotii"
        self.operation_log = ""
        self.updated_at_epoch = 0
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
        self.settings_path.write_text(
            json.dumps({"address": address}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _bleak(self) -> tuple[Any, Any]:
        self._add_dependency_path()
        from bleak import BleakClient, BleakScanner  # type: ignore[import-not-found]
        return BleakClient, BleakScanner

    def dependency_ready(self) -> bool:
        try:
            self._bleak()
            return True
        except (ImportError, OSError):
            return False

    def start(self) -> None:
        if self.monitor is None or not self.monitor.is_alive():
            self.monitor = threading.Thread(target=self._monitor, name="dotii-ble-monitor", daemon=True)
            self.monitor.start()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "dependency_ready": self.dependency_ready(),
                "devices": [dict(item) for item in self.devices],
                "last_address": self.last_address,
                "device_status": dict(self.device_status),
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
        if is_frozen():
            if self.dependency_ready():
                return False
            raise OSError("当前安装包缺少 Windows 蓝牙组件，请重新安装 Dotii 管理中心")
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
        if not self.dependency_ready():
            raise ValueError("请先安装蓝牙连接组件")
        return self._start_worker(self._scan, name="dotii-ble-scan")

    async def _discover(self) -> list[dict[str, Any]]:
        _, scanner = self._bleak()
        discovered = await scanner.discover(timeout=5.0, return_adv=True)
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

    def _scan(self) -> None:
        try:
            with self.operation_lock:
                devices = asyncio.run(self._discover())
            with self.lock:
                self.devices = devices
                self.operation_state = "success"
                self.operation_detail = f"发现 {len(devices)} 台 Dotii" if devices else "未发现附近的 Dotii"
                self.updated_at_epoch = int(time.time())
        except Exception as error:  # BLE backends expose platform-specific exceptions.
            with self.lock:
                self.operation_state = "error"
                self.operation_detail = f"蓝牙扫描失败：{str(error)[:160]}"
                self.updated_at_epoch = int(time.time())

    def start_configure(
        self, *, address: Any, ssid: Any, password: Any, bridge_url: str, bridge_token: str
    ) -> bool:
        if not self.dependency_ready():
            raise ValueError("请先安装蓝牙连接组件")
        if not isinstance(address, str) or not address or len(address) > 80:
            raise ValueError("请选择 Dotii")
        known = {item["address"] for item in self.devices}
        if address not in known and address != self.last_address:
            raise ValueError("请重新扫描并选择 Dotii")
        body = _configuration_payload(
            ssid=ssid, password=password, bridge_url=bridge_url, bridge_token=bridge_token
        )
        return self._start_worker(self._configure, address, body, name="dotii-ble-configure")

    async def _configure_async(self, address: str, body: bytes) -> dict[str, Any]:
        client_class, _ = self._bleak()
        with self.lock:
            target = self._ble_devices.get(address, address)
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

        # On Windows, pairing as part of the initial connection is materially
        # more reliable than connecting first and invoking PairAsync later.
        # The encrypted characteristics remain the final authorization check.
        async with client_class(target, timeout=30.0, pair=True) as client:
            await client.start_notify(RESPONSE_UUID, notification)
            for packet in _configuration_packets(body):
                await client.write_gatt_char(COMMAND_UUID, packet, response=True)
            try:
                await asyncio.wait_for(response_event.wait(), timeout=8.0)
            except TimeoutError:
                raw = await client.read_gatt_char(RESPONSE_UUID)
                response = json.loads(bytes(raw).decode("utf-8"))
            return response

    def _configure(self, address: str, body: bytes) -> None:
        try:
            with self.operation_lock:
                response = asyncio.run(self._configure_async(address, body))
            if response.get("ok") is not True:
                raise OSError(f"Dotii 拒绝配置：{response.get('error', 'unknown')}" )
            self.last_address = address
            self._save_last_address(address)
            with self.lock:
                self.operation_state = "success"
                self.operation_detail = "配置已保存，Dotii 正在重启并连接局域网"
                self.updated_at_epoch = int(time.time())
        except Exception as error:
            with self.lock:
                self.operation_state = "error"
                self.operation_detail = f"蓝牙配置失败：{str(error)[:170]}"
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
        while not self.stop_event.wait(15):
            if not self.last_address or not self.dependency_ready() or not self.operation_lock.acquire(False):
                continue
            try:
                status = asyncio.run(self._read_status(self.last_address))
                with self.lock:
                    self.device_status = status
            except Exception:
                with self.lock:
                    self.device_status = {}
            finally:
                self.operation_lock.release()

    def stop(self) -> None:
        self.stop_event.set()
