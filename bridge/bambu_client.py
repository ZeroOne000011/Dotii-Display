"""Local Bambu Lab printer integration for the State Display bridge.

The public Bambu LAN protocol is not formally supported by Bambu Lab.  This
module deliberately keeps the integration local: MQTT telemetry and commands
use the printer's LAN access code, while the optional P1/A1 camera stream is
decoded into the RGB565 frame consumed by the ESP32.
"""

from __future__ import annotations

import ipaddress
import copy
import json
import logging
import queue
import socket
import ssl
import struct
import subprocess
import threading
import time
import zlib
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit, urlunsplit


MQTT_PORT = 8883
CAMERA_PORT = 6000
CAMERA_WIDTH = 320
CAMERA_HEIGHT = 180
CAMERA_FRAME_SIZE = CAMERA_WIDTH * CAMERA_HEIGHT * 2
CAMERA_STALL_SECONDS = 15.0


def _camera_jpegs(buffer: bytearray) -> list[bytes]:
    """Remove and return every complete JPEG currently buffered."""
    frames: list[bytes] = []
    while True:
        start = buffer.find(b"\xff\xd8")
        if start < 0:
            if len(buffer) > 1024 * 1024:
                buffer.clear()
            return frames
        if start:
            del buffer[:start]
        end = buffer.find(b"\xff\xd9", 2)
        if end < 0:
            if len(buffer) > 4 * 1024 * 1024:
                raise ValueError("RTSPS 相机帧过大")
            return frames
        frames.append(bytes(buffer[:end + 2]))
        del buffer[:end + 2]


def _private_host(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("打印机 IP 必须是文本")
    value = value.strip()
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError("请输入有效的打印机局域网 IP") from error
    if (address.version != 4 or not address.is_private or address.is_loopback or
            address.is_link_local or address.is_multicast or address.is_unspecified):
        raise ValueError("打印机必须使用局域网 IPv4 地址")
    return value


def _text(value: Any, maximum: int, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是文本")
    value = value.strip()
    if len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{field} 太长")
    return value


class BambuConfigStore:
    DEFAULTS = {
        "host": "",
        "serial": "",
        "access_code": "",
        "name": "Bambu Lab",
        "camera_enabled": True,
    }

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        if not path.exists():
            self.write(dict(self.DEFAULTS))

    def read(self) -> dict[str, Any]:
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
        return {**self.DEFAULTS, **data}

    def public(self) -> dict[str, Any]:
        config = self.read()
        return {
            "host": config["host"],
            "serial": config["serial"],
            "name": config["name"],
            "camera_enabled": config["camera_enabled"],
            "has_access_code": bool(config["access_code"]),
            "configured": bool(config["host"] and config["serial"] and config["access_code"]),
        }

    def validate(self, candidate: dict[str, Any], preserve_secret: bool = False) -> dict[str, Any]:
        current = self.read()
        host_raw = candidate.get("host", current["host"])
        host = _private_host(host_raw) if host_raw else ""
        serial = _text(candidate.get("serial", current["serial"]), 32, "序列号").upper()
        name = _text(candidate.get("name", current["name"]), 40, "名称") or "Bambu Lab"
        secret = candidate.get("access_code", "" if not preserve_secret else current["access_code"])
        if preserve_secret and secret == "":
            secret = current["access_code"]
        secret = _text(secret, 64, "访问码")
        camera_enabled = candidate.get("camera_enabled", current["camera_enabled"])
        if not isinstance(camera_enabled, bool):
            raise ValueError("camera_enabled 必须是布尔值")
        if any((host, serial, secret)) and not all((host, serial, secret)):
            raise ValueError("打印机 IP、序列号和局域网访问码需要同时填写")
        return {"host": host, "serial": serial, "access_code": secret, "name": name, "camera_enabled": camera_enabled}

    def write(self, candidate: dict[str, Any], preserve_secret: bool = False) -> dict[str, Any]:
        validated = self.validate(candidate, preserve_secret=preserve_secret)
        payload = json.dumps(validated, ensure_ascii=False, indent=2)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with self._lock:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(self.path)
        return self.public()


def _remaining_length(value: int) -> bytes:
    output = bytearray()
    while True:
        byte = value % 128
        value //= 128
        if value:
            byte |= 0x80
        output.append(byte)
        if not value:
            return bytes(output)


def _mqtt_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack("!H", len(encoded)) + encoded


def _packet(packet_type: int, payload: bytes, flags: int = 0) -> bytes:
    return bytes([(packet_type << 4) | flags]) + _remaining_length(len(payload)) + payload


def _merge(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


def _state_name(raw: Any) -> tuple[str, str]:
    value = str(raw or "").upper()
    mapping = {
        "RUNNING": ("printing", "打印中"), "PAUSE": ("paused", "已暂停"),
        "FINISH": ("completed", "已完成"), "FAILED": ("fault", "故障"),
        "IDLE": ("idle", "空闲"), "PREPARE": ("preparing", "准备中"),
        "SLICING": ("preparing", "准备中"), "INIT": ("preparing", "准备中"),
        "CREATED": ("preparing", "准备中"), "CANCEL": ("cancelling", "取消中"),
        "CANCELLING": ("cancelling", "取消中"),
    }
    return mapping.get(value, ("unknown", "未知状态"))


def _number(value: Any, default: float = 0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    return int(_number(value, default))


def _filament(report: dict[str, Any]) -> dict[str, Any]:
    """Normalize the active AMS or external-spool material across firmware shapes."""
    container = report.get("ams")
    if isinstance(container, dict):
        units = container.get("ams")
        tray_now = container.get("tray_now", report.get("tray_now"))
    else:
        units = container
        tray_now = report.get("tray_now")
    units = units if isinstance(units, list) else []
    active_number = _integer(tray_now, -1)
    selected: dict[str, Any] | None = None

    for unit in units:
        if not isinstance(unit, dict):
            continue
        unit_id = _integer(unit.get("id"), -1)
        trays = unit.get("tray")
        if not isinstance(trays, list):
            continue
        for tray in trays:
            if not isinstance(tray, dict):
                continue
            tray_id = _integer(tray.get("id"), -1)
            global_id = unit_id * 4 + tray_id if 0 <= unit_id < 4 and 0 <= tray_id < 4 else -1
            if active_number == global_id or (unit_id == 0 and active_number == tray_id):
                selected = tray
                break
        if selected is not None:
            break

    # 254 is used by several firmware families for the external/virtual spool.
    if selected is None and (active_number == 254 or not units):
        virtual = report.get("vt_tray")
        if not isinstance(virtual, dict):
            virtual = report.get("vir_slot")
        if isinstance(virtual, list):
            virtual = next((item for item in virtual if isinstance(item, dict) and item.get("active")), None)
        if isinstance(virtual, dict):
            selected = virtual

    if selected is None:
        return {"text": "--", "type": "", "brand": "", "color": "", "remaining_percent": None}
    kind = str(selected.get("tray_type") or selected.get("type") or "").strip()[:24]
    brand = str(selected.get("tray_sub_brands") or selected.get("brand") or "").strip()[:31]
    color = str(selected.get("tray_color") or selected.get("color") or "").strip()[:8]
    remaining_raw = selected.get("remain")
    remaining = None if remaining_raw in (None, "", -1, "-1") else max(0, min(100, _integer(remaining_raw)))
    text = kind or "--"
    if remaining is not None and text != "--":
        text = f"{text} · {remaining}%"
    return {"text": text[:31], "type": kind, "brand": brand, "color": color, "remaining_percent": remaining}


class BambuService:
    def __init__(self, config: BambuConfigStore, module_enabled: Any,
                 ffmpeg_command: Callable[[], str | None] | None = None) -> None:
        self.config_store = config
        self.module_enabled = module_enabled
        self.ffmpeg_command = ffmpeg_command or (lambda: None)
        self._stop = threading.Event()
        self._mqtt_wake = threading.Event()
        self._camera_wake = threading.Event()
        self._commands: queue.Queue[str] = queue.Queue(maxsize=8)
        self._lock = threading.Lock()
        self._mqtt: socket.socket | None = None
        self._report: dict[str, Any] = {}
        self._connected = False
        self._last_report = 0.0
        self._last_error = ""
        self._camera_error = ""
        self._camera_jpeg = b""
        self._camera_rgb565 = b""
        self._camera_revision = 0
        self._camera_updated_at = 0.0
        self._camera_process: subprocess.Popen[bytes] | None = None
        self._thread = threading.Thread(target=self._run, name="bambu-mqtt", daemon=True)
        self._camera_thread = threading.Thread(target=self._run_camera, name="bambu-camera", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._camera_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._mqtt_wake.set()
        self._camera_wake.set()
        self._close_mqtt()
        self._close_camera_process()
        self._thread.join(timeout=3)
        self._camera_thread.join(timeout=3)

    def reconfigure(self) -> None:
        with self._lock:
            self._report = {}
            self._connected = False
            self._last_report = 0.0
            self._last_error = ""
            self._camera_error = ""
            self._camera_jpeg = b""
            self._camera_rgb565 = b""
            self._camera_revision = 0
            self._camera_updated_at = 0.0
        self._close_mqtt()
        self._close_camera_process()
        self._mqtt_wake.set()
        self._camera_wake.set()

    def set_enabled(self, enabled: bool) -> None:
        """Wake workers and close live connections immediately after a toggle."""
        if not enabled:
            self._close_mqtt()
            self._close_camera_process()
        self.reconfigure()

    def _enabled(self) -> bool:
        try:
            return bool(self.module_enabled())
        except Exception:
            return False

    def _configuration(self) -> dict[str, Any] | None:
        config = self.config_store.read()
        if not self._enabled() or not all(config.get(key) for key in ("host", "serial", "access_code")):
            return None
        return config

    def _close_mqtt(self) -> None:
        sock = self._mqtt
        self._mqtt = None
        if sock:
            try:
                sock.close()
            except OSError:
                pass

    def _close_camera_process(self) -> None:
        with self._lock:
            process = self._camera_process
            self._camera_process = None
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    @staticmethod
    def _tls_socket(host: str, port: int, timeout: float) -> ssl.SSLSocket:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection((host, port), timeout=timeout)
        return context.wrap_socket(raw, server_hostname=host)

    @staticmethod
    def _recv_exact(sock: socket.socket, count: int) -> bytes:
        output = bytearray()
        while len(output) < count:
            chunk = sock.recv(count - len(output))
            if not chunk:
                raise ConnectionError("连接已关闭")
            output.extend(chunk)
        return bytes(output)

    def _recv_packet(self, sock: socket.socket) -> tuple[int, int, bytes]:
        first = self._recv_exact(sock, 1)[0]
        multiplier = 1
        length = 0
        while True:
            byte = self._recv_exact(sock, 1)[0]
            length += (byte & 127) * multiplier
            if not byte & 128:
                break
            multiplier *= 128
            if multiplier > 128 ** 3:
                raise ValueError("MQTT 数据包长度无效")
        return first >> 4, first & 15, self._recv_exact(sock, length)

    def _connect(self, config: dict[str, Any]) -> socket.socket:
        sock = self._tls_socket(config["host"], MQTT_PORT, 8)
        sock.settimeout(3)
        client_id = f"state-display-{int(time.time()) & 0xFFFF:04x}"
        variable = _mqtt_string("MQTT") + bytes([4, 0xC2]) + struct.pack("!H", 30)
        payload = _mqtt_string(client_id) + _mqtt_string("bblp") + _mqtt_string(config["access_code"])
        sock.sendall(_packet(1, variable + payload))
        packet_type, _, response = self._recv_packet(sock)
        if packet_type != 2 or len(response) < 2 or response[1] != 0:
            raise ConnectionError("打印机拒绝 MQTT 登录，请检查开发者模式和访问码")
        topic = f"device/{config['serial']}/report"
        sock.sendall(_packet(8, struct.pack("!H", 1) + _mqtt_string(topic) + b"\x00", flags=2))
        packet_type, _, _ = self._recv_packet(sock)
        if packet_type != 9:
            raise ConnectionError("打印机未确认 MQTT 订阅")
        self._publish(sock, config, {"pushing": {"sequence_id": "0", "command": "pushall"}})
        return sock

    def _publish(self, sock: socket.socket, config: dict[str, Any], body: dict[str, Any]) -> None:
        topic = f"device/{config['serial']}/request"
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        sock.sendall(_packet(3, _mqtt_string(topic) + payload))

    def _publish_command(self, sock: socket.socket, config: dict[str, Any], command: str) -> None:
        sequence = str(int(time.time() * 1000) % 1_000_000)
        body = {"print": {"sequence_id": sequence, "command": command}}
        self._publish(sock, config, body)

    def _handle_publish(self, flags: int, payload: bytes) -> None:
        if len(payload) < 2:
            return
        topic_length = struct.unpack("!H", payload[:2])[0]
        offset = 2 + topic_length
        if flags & 6:
            offset += 2
        try:
            message = json.loads(payload[offset:].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        report = message.get("print")
        if isinstance(report, dict):
            with self._lock:
                _merge(self._report, report)
                self._last_report = time.time()
                self._connected = True
                self._last_error = ""

    def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            config = self._configuration()
            if config is None:
                self._mqtt_wake.wait(2)
                self._mqtt_wake.clear()
                continue
            try:
                sock = self._connect(config)
                self._mqtt = sock
                with self._lock:
                    self._connected = True
                    self._last_error = ""
                delay = 1.0
                last_ping = time.monotonic()
                while not self._stop.is_set() and config == self._configuration():
                    try:
                        command = self._commands.get_nowait()
                        self._publish_command(sock, config, command)
                    except queue.Empty:
                        pass
                    if time.monotonic() - last_ping > 15:
                        sock.sendall(_packet(12, b""))
                        last_ping = time.monotonic()
                    try:
                        packet_type, flags, payload = self._recv_packet(sock)
                    except socket.timeout:
                        continue
                    if packet_type == 3:
                        qos = (flags >> 1) & 3
                        if qos == 1 and len(payload) >= 4:
                            topic_length = struct.unpack("!H", payload[:2])[0]
                            packet_id_offset = 2 + topic_length
                            if packet_id_offset + 2 <= len(payload):
                                sock.sendall(_packet(4, payload[packet_id_offset:packet_id_offset + 2]))
                        self._handle_publish(flags, payload)
            except (OSError, ValueError, ConnectionError, ssl.SSLError) as error:
                with self._lock:
                    self._connected = False
                    self._last_error = str(error)[:160]
                if not self._stop.is_set():
                    logging.warning("Bambu LAN connection: %s", error)
            finally:
                self._close_mqtt()
            self._mqtt_wake.wait(delay)
            self._mqtt_wake.clear()
            delay = min(delay * 2, 30)

    @staticmethod
    def _camera_auth(access_code: str) -> bytes:
        username = b"bblp".ljust(32, b"\0")
        password = access_code.encode("utf-8")[:32].ljust(32, b"\0")
        return struct.pack("<IIII", 0x40, 0x3000, 0, 0) + username + password

    @staticmethod
    def _to_rgb565(jpeg: bytes) -> bytes:
        try:
            from PIL import Image, ImageOps
        except ImportError as error:
            raise RuntimeError("电脑缺少 Pillow，无法转换打印机相机画面") from error
        import io
        with Image.open(io.BytesIO(jpeg)) as source:
            image = ImageOps.fit(source.convert("RGB"), (CAMERA_WIDTH, CAMERA_HEIGHT))
            output = bytearray(CAMERA_FRAME_SIZE)
            for index, (red, green, blue) in enumerate(image.getdata()):
                value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
                struct.pack_into("<H", output, index * 2, value)
            return bytes(output)

    @staticmethod
    def _rtsp_input(config: dict[str, Any], report: dict[str, Any]) -> str:
        ipcam = report.get("ipcam")
        raw = ipcam.get("rtsp_url") if isinstance(ipcam, dict) else ""
        if not isinstance(raw, str) or not raw:
            return ""
        parsed = urlsplit(raw)
        if parsed.scheme != "rtsps" or parsed.hostname != config["host"] or (parsed.port or 322) != 322:
            return ""
        netloc = f"bblp:{quote(config['access_code'], safe='')}@{parsed.hostname}:{parsed.port or 322}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))

    def _publish_camera_frame(self, jpeg: bytes) -> None:
        if not (jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")):
            raise ValueError("相机返回的 JPEG 帧无效")
        rgb565 = self._to_rgb565(jpeg)
        revision = zlib.crc32(rgb565) & 0xFFFFFFFF
        with self._lock:
            self._camera_jpeg = jpeg
            self._camera_rgb565 = rgb565
            self._camera_revision = revision or 1
            self._camera_updated_at = time.time()
            self._camera_error = ""

    def _run_rtsp_camera(self, config: dict[str, Any], url: str) -> None:
        ffmpeg = self.ffmpeg_command()
        if not ffmpeg:
            raise RuntimeError("该打印机使用 RTSPS 相机，但电脑未安装 FFmpeg")
        command = [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-rtsp_transport", "tcp", "-timeout", "8000000", "-i", url,
            "-vf", "fps=0.5,scale=320:180:force_original_aspect_ratio=increase,crop=320:180",
            "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
        ]
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        with self._lock:
            self._camera_process = process
        buffer = bytearray()
        chunks: queue.Queue[bytes] = queue.Queue(maxsize=16)

        def read_stdout() -> None:
            assert process.stdout is not None
            try:
                while True:
                    chunk = process.stdout.read(4096)
                    chunks.put(chunk)
                    if not chunk:
                        return
            except OSError:
                chunks.put(b"")

        reader: threading.Thread | None = None
        try:
            if process.stdout is None:
                raise RuntimeError("无法读取 RTSPS 相机输出")
            reader = threading.Thread(target=read_stdout, name="bambu-camera-pipe", daemon=True)
            reader.start()
            last_frame_at = time.monotonic()
            while not self._stop.is_set() and config == self._configuration():
                try:
                    chunk = chunks.get(timeout=1)
                except queue.Empty:
                    chunk = None
                if time.monotonic() - last_frame_at >= CAMERA_STALL_SECONDS:
                    raise TimeoutError("RTSPS 相机超过 15 秒没有新画面")
                if chunk is None:
                    if process.poll() is not None:
                        raise ConnectionError(f"RTSPS 相机进程已退出（{process.poll()}）")
                    continue
                if not chunk:
                    raise ConnectionError(f"RTSPS 相机进程已退出（{process.poll()}）")
                buffer.extend(chunk)
                for jpeg in _camera_jpegs(buffer):
                    self._publish_camera_frame(jpeg)
                    last_frame_at = time.monotonic()
        finally:
            with self._lock:
                if self._camera_process is process:
                    self._camera_process = None
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
            if reader is not None:
                reader.join(timeout=1)

    def _run_legacy_camera(self, config: dict[str, Any]) -> None:
        sock: socket.socket | None = None
        try:
            sock = self._tls_socket(config["host"], CAMERA_PORT, 8)
            sock.settimeout(8)
            sock.sendall(self._camera_auth(config["access_code"]))
            while not self._stop.is_set() and config == self._configuration():
                header = self._recv_exact(sock, 16)
                size = int.from_bytes(header[:3], "little")
                if size < 100 or size > 4 * 1024 * 1024:
                    raise ValueError("旧式相机帧长度无效")
                jpeg = self._recv_exact(sock, size)
                self._publish_camera_frame(jpeg)
        finally:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass

    def _run_camera(self) -> None:
        while not self._stop.is_set():
            config = self._configuration()
            if config is None or not config.get("camera_enabled"):
                with self._lock:
                    self._camera_jpeg = b""
                    self._camera_rgb565 = b""
                    self._camera_revision = 0
                    self._camera_updated_at = 0.0
                self._camera_wake.wait(3)
                self._camera_wake.clear()
                continue
            try:
                with self._lock:
                    report = copy.deepcopy(self._report)
                rtsp_url = self._rtsp_input(config, report)
                if rtsp_url:
                    self._run_rtsp_camera(config, rtsp_url)
                else:
                    self._run_legacy_camera(config)
            except (OSError, ValueError, ConnectionError, RuntimeError, ssl.SSLError) as error:
                error_text = str(error)[:160]
                with self._lock:
                    changed = error_text != self._camera_error
                    self._camera_error = error_text
                if changed:
                    logging.warning("Bambu camera: %s", error_text)
            self._stop.wait(5)

    def command(self, action: str) -> dict[str, Any]:
        snapshot = self.snapshot()
        permitted = {
            "pause": {"printing"}, "resume": {"paused"},
            "stop": {"printing", "paused", "preparing"},
        }
        if action not in permitted:
            raise ValueError("不支持的 Bambu 指令")
        if not snapshot["connected"]:
            raise ValueError("打印机未连接")
        if snapshot["status"] not in permitted[action]:
            raise ValueError("当前打印状态不允许执行该操作")
        try:
            self._commands.put_nowait(action)
        except queue.Full as error:
            raise ValueError("指令队列繁忙，请稍后重试") from error
        return {"accepted": True, "action": action}

    def get_camera_jpeg(self) -> bytes:
        with self._lock:
            return self._camera_jpeg

    def get_camera_rgb565(self) -> bytes:
        with self._lock:
            return self._camera_rgb565

    def snapshot(self) -> dict[str, Any]:
        config = self.config_store.public()
        enabled = self._enabled()
        with self._lock:
            report = copy.deepcopy(self._report)
            connected = self._connected and bool(self._last_report) and time.time() - self._last_report < 30
            last_report = self._last_report
            error = self._last_error
            camera_error = self._camera_error
            camera_revision = self._camera_revision
            camera_updated_at = self._camera_updated_at
            camera_available = bool(self._camera_rgb565) and time.time() - camera_updated_at < 15
        status, status_text = _state_name(report.get("gcode_state")) if connected else ("offline", "离线")
        remaining = max(0, _integer(report.get("mc_remaining_time")))
        progress = max(0, min(100, _integer(report.get("mc_percent"))))
        filament = _filament(report)
        service_state = "disabled" if not enabled else "needs_configuration" if not config["configured"] else "online" if connected else "error" if error else "connecting"
        camera_enabled = bool(config.get("camera_enabled"))
        return {
            "module_enabled": enabled,
            "configuration_complete": config["configured"],
            "service_state": service_state,
            "configured": config["configured"], "connected": connected,
            "source": "lan_mqtt", "name": config["name"],
            "status": status, "status_text": status_text,
            "progress": progress, "remaining_minutes": remaining,
            "finish_epoch": int(time.time() + remaining * 60) if connected and remaining else 0,
            "updated_at_epoch": int(last_report),
            "filename": str(report.get("subtask_name") or report.get("gcode_file") or "--")[:95],
            "filament": filament["text"], "filament_type": filament["type"],
            "filament_brand": filament["brand"], "filament_color": filament["color"],
            "filament_remaining_percent": filament["remaining_percent"],
            "layer_current": max(0, _integer(report.get("layer_num"))),
            "layer_total": max(0, _integer(report.get("total_layer_num"))),
            "nozzle_temperature": _number(report.get("nozzle_temper")),
            "bed_temperature": _number(report.get("bed_temper")),
            "wifi_signal": str(report.get("wifi_signal") or "--")[:15],
            "camera_available": camera_available,
            "camera_revision": camera_revision,
            "camera_width": CAMERA_WIDTH, "camera_height": CAMERA_HEIGHT,
            "camera_size": CAMERA_FRAME_SIZE if camera_available else 0,
            "camera_enabled": camera_enabled,
            "camera_updated_at_epoch": int(camera_updated_at),
            "commandable": connected and status in {"printing", "paused", "preparing"},
            "last_error": error, "camera_error": camera_error,
        }
