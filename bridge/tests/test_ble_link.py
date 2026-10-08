from __future__ import annotations

import sys
import unittest
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))

from ble_link import (  # noqa: E402
    PushThrottle,
    SnapshotAssembler,
    payload_revision,
    snapshot_packets,
)


def feed(assembler: SnapshotAssembler, packets: list[bytes]) -> tuple[bool, str]:
    result = (False, "ok")
    for packet in packets:
        result = assembler.accept(packet)
        if result[1] != "ok":
            return result
    return result


class SnapshotPacketTests(unittest.TestCase):
    def test_roundtrip_assembles_payload(self) -> None:
        payload = b'{"schema_version":1,"codex":{}}' + b"x" * 5000
        packets = snapshot_packets(payload, 42)
        self.assertEqual(packets[0][0], 1)
        self.assertEqual(packets[-1], b"\x03")
        assembler = SnapshotAssembler()
        ok, status = feed(assembler, packets)
        self.assertTrue(ok)
        self.assertEqual(status, "accepted")
        self.assertEqual(assembler.payload, payload)
        self.assertEqual(assembler.accepted_revision, 42)

    def test_header_layout(self) -> None:
        payload = b"hello"
        import struct, zlib

        packets = snapshot_packets(payload, 7)
        header = packets[0]
        self.assertEqual(len(header), 11)
        expected, crc, revision = struct.unpack("<HII", header[1:])
        self.assertEqual(expected, 5)
        self.assertEqual(crc, zlib.crc32(payload) & 0xFFFFFFFF)
        self.assertEqual(revision, 7)
        self.assertEqual(packets[1], b"\x02hello")

    def test_single_block_payload(self) -> None:
        assembler = SnapshotAssembler()
        ok, _ = feed(assembler, snapshot_packets(b"tiny", 1))
        self.assertTrue(ok)

    def test_rejects_empty_and_oversized(self) -> None:
        with self.assertRaises(ValueError):
            snapshot_packets(b"", 1)
        with self.assertRaises(ValueError):
            snapshot_packets(b"a" * (64 * 1024 + 1), 1)

    def test_chunk_boundaries(self) -> None:
        for chunk in (1, 7, 180, 512):
            payload = bytes(range(256)) * 5
            assembler = SnapshotAssembler()
            ok, _ = feed(assembler, snapshot_packets(payload, 3, chunk=chunk))
            self.assertTrue(ok, msg=f"chunk={chunk}")
            self.assertEqual(assembler.payload, payload)


class AssemblerRobustnessTests(unittest.TestCase):
    def test_bad_crc_requests_resend(self) -> None:
        payload = b"payload-body" * 40
        packets = snapshot_packets(payload, 5)
        packets[-2] = b"\x02corrupt!!"  # 篡改最后一个数据块（长度不变，CRC 失配）
        assembler = SnapshotAssembler()
        ok, status = feed(assembler, packets)
        self.assertFalse(ok)
        self.assertEqual(status, "resend")

    def test_missing_block_requests_resend(self) -> None:
        payload = b"z" * 600
        packets = snapshot_packets(payload, 9)
        assembler = SnapshotAssembler()
        dropped = [p for i, p in enumerate(packets) if i not in (3, 4)]
        ok, status = feed(assembler, dropped)
        self.assertFalse(ok)
        self.assertEqual(status, "resend")

    def test_revision_regression_is_stale(self) -> None:
        first = snapshot_packets(b'{"a":1}', 100)
        assembler = SnapshotAssembler()
        feed(assembler, first)
        ok, status = feed(assembler, snapshot_packets(b'{"a":2}', 99))
        self.assertFalse(ok)
        self.assertEqual(status, "stale")

    def test_same_revision_new_content_is_accepted(self) -> None:
        # 内容变化后 revision 由 CRC 决定，天然不同；相同 revision 只能是相同内容
        payload = b'{"b":2}'
        assembler = SnapshotAssembler()
        feed(assembler, snapshot_packets(payload, payload_revision(payload)))
        ok, _ = feed(assembler, snapshot_packets(payload, payload_revision(payload) + 1))
        self.assertTrue(ok)

    def test_header_without_start_resends(self) -> None:
        assembler = SnapshotAssembler()
        self.assertEqual(assembler.accept(b"\x02orphan"), (False, "resend"))
        self.assertEqual(assembler.accept(b"\x03tail"), (False, "resend"))

    def test_over_length_header_resends(self) -> None:
        import struct

        assembler = SnapshotAssembler()
        giant = struct.pack("<BHII", 1, 60_001, 0, 5)
        self.assertEqual(assembler.accept(giant), (False, "resend"))

    def test_unknown_packet_type_resends(self) -> None:
        assembler = SnapshotAssembler()
        assembler.accept(snapshot_packets(b"abc", 1)[0])
        self.assertEqual(assembler.accept(b"\x07junk"), (False, "resend"))


class ThrottleTests(unittest.TestCase):
    def test_throttle_limits_frequency_and_repeats(self) -> None:
        throttle = PushThrottle(interval=2.0)
        self.assertTrue(throttle.should_send(1, now=0.0))
        self.assertFalse(throttle.should_send(2, now=0.5))   # 间隔未到
        self.assertFalse(throttle.should_send(1, now=1.9))   # 间隔未到（同内容）
        self.assertTrue(throttle.should_send(2, now=2.5))    # 新内容 + 间隔已过
        self.assertFalse(throttle.should_send(2, now=3.0))   # 同内容短窗抑制
        self.assertTrue(throttle.should_send(2, now=40.0))   # 超过 30s 视为心跳补发

    def test_payload_revision_matches_crc(self) -> None:
        import zlib

        self.assertEqual(payload_revision(b"abc"), zlib.crc32(b"abc") & 0xFFFFFFFF)
        self.assertNotEqual(payload_revision(b"abc"), payload_revision(b"abd"))


if __name__ == "__main__":
    unittest.main()
