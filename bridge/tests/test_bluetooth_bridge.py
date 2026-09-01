import json
import unittest
import zlib

from bluetooth_bridge import _configuration_packets, _configuration_payload


class BluetoothPacketTests(unittest.TestCase):
    def test_packet_round_trip(self):
        body = _configuration_payload(
            ssid="Studio", password="secret", bridge_url="http://192.168.1.2:8787/api/v1/snapshot",
            bridge_token="0123456789abcdef0123456789abcdef",
        )
        packets = _configuration_packets(body, 11)
        self.assertEqual(packets[0][0], 1)
        self.assertEqual(int.from_bytes(packets[0][1:3], "little"), len(body))
        self.assertEqual(int.from_bytes(packets[0][3:7], "little"), zlib.crc32(body))
        self.assertEqual(b"".join(packet[1:] for packet in packets[1:-1]), body)
        self.assertEqual(packets[-1], b"\x03")
        self.assertNotIn(" ", json.loads(body)["bridge_url"])

    def test_rejects_long_wifi_name(self):
        with self.assertRaisesRegex(ValueError, "Wi-Fi"):
            _configuration_payload(
                ssid="x" * 33, password="", bridge_url="http://host/api/v1/snapshot",
                bridge_token="0123456789abcdef",
            )

    def test_rejects_short_token(self):
        with self.assertRaisesRegex(ValueError, "令牌"):
            _configuration_payload(
                ssid="Studio", password="", bridge_url="http://host/api/v1/snapshot", bridge_token="short",
            )


if __name__ == "__main__":
    unittest.main()

