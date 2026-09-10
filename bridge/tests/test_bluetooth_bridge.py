import json
import threading
import unittest
import zlib
from types import SimpleNamespace

from bluetooth_bridge import (
    BluetoothBridge,
    _configuration_packets,
    _configuration_payload,
    _is_stale_windows_pairing_error,
)


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


class BluetoothPairingRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_with_fresh_windows_pairing_after_unreachable_notify(self):
        class FakeClient:
            notify_attempts = 0
            unpair_calls = 0
            configured_packets: list[bytes] = []

            def __init__(self, target, **kwargs):
                self.target = target
                self.kwargs = kwargs
                self.is_connected = False
                self.notification = None

            async def __aenter__(self):
                self.is_connected = True
                return self

            async def __aexit__(self, *_):
                self.is_connected = False

            async def start_notify(self, _, callback):
                type(self).notify_attempts += 1
                if type(self).notify_attempts == 1:
                    raise OSError("Could not start notify on 0011: Unreachable")
                self.notification = callback

            async def write_gatt_char(self, _, packet, response):
                self.assert_write_response = response
                type(self).configured_packets.append(bytes(packet))
                if packet == b"\x03" and self.notification is not None:
                    self.notification(None, bytearray(b'{"ok":true,"state":"restarting"}'))

            async def read_gatt_char(self, _):
                return bytearray(b'{"ok":true,"state":"restarting"}')

            async def unpair(self):
                type(self).unpair_calls += 1

            async def disconnect(self):
                self.is_connected = False

        service = BluetoothBridge.__new__(BluetoothBridge)
        service.lock = threading.RLock()
        service.platform = SimpleNamespace(name="windows")
        service._ble_devices = {"AA:BB": "native-device"}
        service._bleak = lambda: (FakeClient, object)
        service.operation_detail = ""
        service.updated_at_epoch = 0
        body = _configuration_payload(
            ssid="Studio", password="secret",
            bridge_url="http://192.168.1.2:8787/api/v1/snapshot",
            bridge_token="0123456789abcdef0123456789abcdef",
        )

        response = await service._configure_async("AA:BB", body)

        self.assertTrue(response["ok"])
        self.assertEqual(FakeClient.unpair_calls, 1)
        self.assertEqual(FakeClient.notify_attempts, 2)
        self.assertEqual(FakeClient.configured_packets, _configuration_packets(body))
        self.assertIn("自动重新配对", service.operation_detail)

    def test_only_matches_the_observed_stale_pairing_error(self):
        self.assertTrue(_is_stale_windows_pairing_error(
            OSError("Could not start notify on 0011: Unreachable")
        ))
        self.assertFalse(_is_stale_windows_pairing_error(OSError("Device disconnected")))


if __name__ == "__main__":
    unittest.main()
