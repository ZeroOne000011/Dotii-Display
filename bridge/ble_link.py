"""BLE 数据通道协议（快照分包推送）.

管理中心（central）通过 SNAPSHOT 特征 NOTIFY 推送完整快照 JSON，设备
（peripheral）校验后走与 Wi-Fi 相同的 parse_snapshot 路径。分包格式：

    [0x01][总长 LE16][CRC32 LE32][revision LE32]   头
    [0x02][块 ≤ chunk] × N                          数据
    [0x03]                                           尾

SYNC 特征（设备 → 管理中心）控制包：``[0x00]`` 心跳、``[0x01]`` 请求重发。
revision 单调递增，设备拒绝低于已接受值的完整快照（防重连乱序覆盖）。
"""
from __future__ import annotations

import struct
import threading
import zlib

MAX_PAYLOAD_BYTES = 60_000  # 头部长度域为 u16，留出裕量
DEFAULT_CHUNK = 180
HEADER = 0x01
BLOCK = 0x02
TAIL = 0x03
SYNC_HEARTBEAT = b"\x00"
SYNC_RESEND = b"\x01"


def payload_revision(payload: bytes) -> int:
    """内容 revision：CRC32，内容不变则 revision 不变（幂等推送判据）。"""
    return zlib.crc32(payload) & 0xFFFFFFFF


def snapshot_packets(payload: bytes, revision: int, chunk: int = DEFAULT_CHUNK) -> list[bytes]:
    """把快照打包为 头 + 块×N + 尾 的发送序列。"""
    if not isinstance(payload, (bytes, bytearray)) or len(payload) == 0:
        raise ValueError("快照不能为空")
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("快照超过 64KB 上限")
    if not 1 <= chunk <= 512:
        raise ValueError("分包大小无效")
    if not 0 <= revision <= 0xFFFFFFFF:
        raise ValueError("revision 超出范围")
    body = bytes(payload)
    packets = [struct.pack("<BHII", HEADER, len(body), zlib.crc32(body) & 0xFFFFFFFF, revision)]
    packets.extend(bytes((BLOCK,)) + body[offset:offset + chunk]
                   for offset in range(0, len(body), chunk))
    packets.append(bytes((TAIL,)))
    return packets


class SnapshotAssembler:
    """设备侧重组逻辑的对偶实现（协议回环测试与参考实现）。"""

    def __init__(self, max_bytes: int = MAX_PAYLOAD_BYTES) -> None:
        self._max_bytes = max_bytes
        self._accepted_revision = 0
        self.payload = b""
        self._reset()

    def _reset(self) -> None:
        self._expected = 0
        self._crc = 0
        self._revision = 0
        self._buffer = bytearray()
        self._started = False

    @property
    def accepted_revision(self) -> int:
        return self._accepted_revision

    def accept(self, packet: bytes) -> tuple[bool, str]:
        """喂入一个包；返回 (是否产出完整快照, 状态说明)。

        状态说明取值：ok（继续）、accepted（完整快照就绪，见 ``payload``）、
        stale（revision 回退，忽略）、resend（校验失败，应请求重发）。
        """
        if not isinstance(packet, (bytes, bytearray)) or len(packet) == 0:
            return False, "resend"
        data = bytes(packet)
        kind = data[0]
        if kind == HEADER:
            if len(data) != 1 + 2 + 4 + 4:
                return False, "resend"
            expected, crc, revision = struct.unpack("<HII", data[1:11])
            if expected == 0 or expected > self._max_bytes:
                return False, "resend"
            if revision <= self._accepted_revision:
                self._reset()
                return False, "stale"
            self._reset()
            self._expected, self._crc, self._revision = expected, crc, revision
            self._started = True
            return False, "ok"
        if not self._started:
            return False, "resend"
        if kind == BLOCK:
            if len(data) < 2:
                return False, "resend"
            self._buffer.extend(data[1:])
            if len(self._buffer) > self._max_bytes:
                self._reset()
                return False, "resend"
            return False, "ok"
        if kind == TAIL:
            complete = self._started and len(self._buffer) == self._expected
            valid = complete and (zlib.crc32(bytes(self._buffer)) & 0xFFFFFFFF) == self._crc
            if valid:
                self.payload = bytes(self._buffer)
                self._accepted_revision = self._revision
                result: tuple[bool, str] = (True, "accepted")
            else:
                result = (False, "resend")
            self._reset()
            return result
        return False, "resend"

class PushThrottle:
    """推送节流：内容 revision 变化且距上次推送超过间隔才放行。"""

    def __init__(self, interval: float = 2.0) -> None:
        import time

        self._interval = interval
        self._time = time.monotonic
        self._last_sent_at = float('-inf')
        self._last_revision: int | None = None
        self._lock = threading.Lock()

    def should_send(self, revision: int, now: float | None = None) -> bool:
        with self._lock:
            current = self._time() if now is None else now
            if revision == self._last_revision and current - self._last_sent_at < 30.0:
                return False
            if current - self._last_sent_at < self._interval:
                return False
            self._last_sent_at = current
            self._last_revision = revision
            return True
