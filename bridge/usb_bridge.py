"""Dotii USB transport. One owner per port; bounded CRC framed messages.

Wire: DTU1 + little-endian uint32 JSON byte length + uint32 CRC32 + UTF-8 JSON.
Requests have id/op/token; responses echo id. No credentials enter diagnostics.
Images are acknowledged 4096-byte chunks with ordered offsets and final CRC.
"""
from __future__ import annotations

import base64
import json
import struct
import threading
import time
import zlib
from collections import deque

MAGIC = b"DTU1"
MAX_FRAME = 2 * 1024 * 1024


def encode_frame(payload):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if not 0 < len(body) <= MAX_FRAME:
        raise ValueError("USB frame too large")
    return MAGIC + struct.pack("<II", len(body), zlib.crc32(body)) + body


class FrameDecoder:
    def __init__(self):
        self.buffer = bytearray()
        self.started = 0.0

    def feed(self, data):
        if self.buffer and time.monotonic() - self.started > 8:
            self.buffer.clear()
        if not self.buffer:
            self.started = time.monotonic()
        self.buffer.extend(data)
        output = []
        while True:
            pos = self.buffer.find(MAGIC)
            if pos < 0:
                self.buffer[:] = self.buffer[-3:]
                break
            if pos:
                del self.buffer[:pos]
            if len(self.buffer) < 12:
                break
            length, crc = struct.unpack_from("<II", self.buffer, 4)
            if not 0 < length <= MAX_FRAME:
                del self.buffer[:4]
                continue
            if len(self.buffer) < 12 + length:
                break
            body = bytes(self.buffer[12:12 + length])
            if zlib.crc32(body) != crc:
                del self.buffer[:4]
                continue
            del self.buffer[:12 + length]
            try:
                item = json.loads(body)
                if isinstance(item, dict):
                    output.append(item)
            except (ValueError, UnicodeDecodeError):
                pass
        return output


class UsbBridge:
    def __init__(self, server):
        self.server = server
        self.lock = threading.RLock()
        self.flash_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.paused = threading.Event()
        self.released = threading.Event()
        self.released.set()
        self.worker = None
        self.serial = None
        self.seq = 0
        self.decoder = FrameDecoder()
        self.jobs = deque()
        self.state = {"state": "disconnected", "port": "", "device_id": "", "firmware": "", "last_sync": 0, "wifi": False, "wifi_bridge": False, "detail": "", "provision_state": "idle"}

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def start(self):
        self.worker = threading.Thread(target=self._run, daemon=True, name="dotii-usb")
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        if self.worker:
            self.worker.join(timeout=8)

    def pause(self):
        self.paused.set()
        if not self.released.wait(8):
            self.paused.clear()
            raise ValueError("USB connection is busy; retry firmware update")

    def resume(self):
        self.paused.clear()

    def configure(self, ssid, password, preserve_password=False):
        if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32 or not isinstance(password, str) or len(password.encode("utf-8")) > 64:
            raise ValueError("Invalid Wi-Fi name or password")
        with self.lock:
            if self.state["state"] != "connected" or self.jobs:
                raise ValueError("Connect Dotii USB before configuring Wi-Fi")
            if not isinstance(preserve_password, bool):
                raise ValueError("Invalid password option")
            self.jobs.append((ssid, password, preserve_password))
            self.state["provision_state"] = "writing"

    def _exchange(self, op, **payload):
        self.seq += 1
        seq = self.seq
        packet = {"id": seq, "op": op, "token": self.server.token, **payload}
        self.serial.write(encode_frame(packet))
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if self.stop_event.is_set() or self.paused.is_set():
                raise OSError("interrupted")
            reply = None
            for item in self.decoder.feed(self.serial.read(max(1, min(self.serial.in_waiting, 65536)))):
                if item.get("event") == "bambu_command":
                    # Never replay a command after a disconnect or retry it automatically.
                    try:
                        self.server.bambu.command(item.get("action"))
                    except (OSError, ValueError):
                        pass
                elif item.get("id") == seq:
                    reply = item
            # A control event can share one USB read with the ACK. Consume all
            # decoded frames before returning, or that command would be lost.
            if reply is not None:
                if not reply.get("ok"):
                    raise ValueError(str(reply.get("error", "device_error")))
                return reply
        raise TimeoutError("firmware_update_required")

    def _image(self, kind, revision, body):
        crc = zlib.crc32(body)
        for offset in range(0, len(body), 4096):
            self._exchange("image", kind=kind, revision=revision, offset=offset, total=len(body), crc=crc,
                           data=base64.b64encode(body[offset:offset + 4096]).decode("ascii"))

    def _sync(self):
        snapshot = self.server.snapshot()
        reply = self._exchange("snapshot", snapshot=snapshot, epoch=int(time.time()), **self.server.connections.device())
        self.update(state="connected", last_sync=int(time.time()), wifi=reply.get("wifi", False), wifi_bridge=reply.get("wifi_bridge", False),
                    ssid=reply.get("ssid", ""), device_language=reply.get("language"), device_mode=reply.get("mode"),
                    uptime=reply.get("uptime", 0), reset_reason=reply.get("reset_reason"), free_internal=reply.get("free_internal"),
                    custom_revision=reply.get("custom_revision", 0), camera_revision=reply.get("camera_revision", 0))
        return snapshot

    def _run(self):
        try:
            import serial
            from serial.tools import list_ports
        except ImportError:
            self.update(state="unavailable", detail="usb_component_missing")
            return
        while not self.stop_event.is_set():
            if self.paused.is_set():
                self.update(state="maintenance")
                self.stop_event.wait(.2)
                continue
            try:
                ports = [p for p in list_ports.comports() if p.vid == 0x303A and p.pid == 0x1001]
                settings = self.server.connections.read()
                if settings["port"]:
                    ports = [p for p in ports if p.device == settings["port"]]
                if not ports:
                    self.update(state="disconnected", detail="usb_not_found", port="")
                    self.stop_event.wait(2)
                    continue
                if len(ports) != 1:
                    self.update(state="select_device", detail="multiple_usb_devices")
                    self.stop_event.wait(2)
                    continue
                self.released.clear()
                # Prevent DTR/RTS transitions from resetting the running device.
                connection = serial.Serial(port=None, baudrate=115200, timeout=.1, write_timeout=3)
                connection.dtr = False
                connection.rts = False
                connection.port = ports[0].device
                connection.open()
                self.serial = connection
                self.decoder = FrameDecoder()
                self.update(state="connecting", port=ports[0].device, detail="")
                reply = self._exchange("hello", **self.server.connections.device())
                device_id = reply.get("device_id", "")
                if not device_id or reply.get("product") != "Dotii" or reply.get("protocol") != 1:
                    raise ValueError("unsupported_device")
                if settings["device_id"] and settings["device_id"] != device_id:
                    raise ValueError("different_device")
                if not settings["device_id"]:
                    self.server.connections.write({"device_id": device_id})
                self.update(device_id=device_id, firmware=reply.get("firmware", ""))
                revisions = {}
                while not self.stop_event.is_set() and not self.paused.is_set():
                    with self.lock:
                        job = self.jobs.popleft() if self.jobs else None
                    if job:
                        ssid, password, preserve_password = job
                        url = self.server.device_url()
                        self._exchange("provision", ssid=ssid, password=password, preserve_password=preserve_password, bridge_url=url)
                        self.update(provision_state="connecting")
                        break  # firmware restarts; reconnect without retaining the password
                    snapshot = self._sync()
                    if self.server.connections.read()["mode"] != "wifi":
                        custom = snapshot.get("custom", {})
                        bambu = snapshot.get("bambu", {})
                        for kind, available, revision in (
                            ("custom", custom.get("image_available"), custom.get("image_revision", 0)),
                            ("camera", bambu.get("camera_available"), bambu.get("camera_revision", 0)),
                        ):
                            if available and revisions.get(kind) != revision:
                                try:
                                    body = self.server.assets.read_render() if kind == "custom" else self.server.bambu.get_camera_rgb565()
                                    if body:
                                        self._image(kind, revision, body)
                                        revisions[kind] = revision
                                        self._sync()
                                except FileNotFoundError:
                                    pass
                    status = self.snapshot()
                    if status["provision_state"] == "connecting" and status["wifi"]:
                        self.update(provision_state="ready" if status["wifi_bridge"] else "wifi_connected")
                    self.stop_event.wait(1)
            except (OSError, ValueError) as error:
                code = str(error)
                safe = code if code in {"firmware_update_required", "binding_mismatch", "different_device", "unsupported_device"} else "usb_connection_failed"
                self.update(state="error", detail=safe)
            finally:
                if self.serial:
                    self.serial.close()
                    self.serial = None
                self.released.set()
                with self.lock:
                    self.jobs.clear()
            self.stop_event.wait(2)
