import asyncio
import json
import os
import sys
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))

from bluetooth_bridge import (
    BluetoothBridge,
    _bluetooth_error,
    _configuration_packets,
    _configuration_payload,
    _is_stale_windows_pairing_error,
)
from platforms.macos import MacOSPlatformAdapter


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

    def test_existing_device_token_can_authorize_management_token_migration(self):
        body = _configuration_payload(
            ssid="Studio",
            password="secret",
            bridge_url="http://host/api/v1/snapshot",
            bridge_token="new-management-token-1234",
            current_token="current-device-token-1234",
        )
        payload = json.loads(body)

        self.assertEqual(payload["auth"], "current-device-token-1234")
        self.assertEqual(payload["bridge_token"], "new-management-token-1234")

    def test_rejects_invalid_current_device_token(self):
        with self.assertRaisesRegex(ValueError, "当前设备令牌"):
            _configuration_payload(
                ssid="Studio",
                password="",
                bridge_url="http://host/api/v1/snapshot",
                bridge_token="new-management-token-1234",
                current_token="short",
            )

    def test_macos_permission_errors_have_actionable_states(self):
        denied = Exception("not authorized")
        denied.reason = SimpleNamespace(name="DENIED_BY_USER")
        powered_off = Exception("powered off")
        powered_off.reason = SimpleNamespace(name="POWERED_OFF")

        self.assertEqual(_bluetooth_error(denied)[0], "denied")
        self.assertIn("隐私与安全性", _bluetooth_error(denied)[1])
        self.assertEqual(_bluetooth_error(powered_off)[0], "bluetooth_off")
        self.assertEqual(_bluetooth_error(TimeoutError())[0], "timeout")
        stale = Exception('CBErrorDomain Code=14 "Peer removed pairing information"')
        self.assertEqual(_bluetooth_error(stale)[0], "stale_pairing")
        self.assertIn("系统设置 > 蓝牙", _bluetooth_error(stale)[1])
        missing = Exception("Device with address 00000000-0000-0000-0000-000000000000 was not found")
        self.assertEqual(_bluetooth_error(missing)[0], "not_found")
        self.assertNotIn("00000000", _bluetooth_error(missing)[1])
        generic = Exception("Connection failed for 243E23AE-4A99-406C-B317-18F1BD7B4CBE")
        self.assertNotIn("243E23AE", _bluetooth_error(generic)[1])

    def test_corebluetooth_identifier_is_saved_privately_and_restored(self):
        identifier = "243E23AE-4A99-406C-B317-18F1BD7B4CBE"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            bridge = BluetoothBridge(path, platform_adapter=MacOSPlatformAdapter())
            bridge._save_last_address(identifier)
            restored = BluetoothBridge(path, platform_adapter=MacOSPlatformAdapter())

            self.assertEqual(restored.last_address, identifier)
            if os.name == "posix":
                self.assertEqual(path.joinpath("bluetooth.json").stat().st_mode & 0o777, 0o600)


class MacOSBluetoothTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_keeps_corebluetooth_uuid_for_reconnect(self):
        identifier = "243E23AE-4A99-406C-B317-18F1BD7B4CBE"
        device = SimpleNamespace(address=identifier, name="Dotii")
        advertisement = SimpleNamespace(
            local_name="Dotii", service_uuids=[], rssi=-42,
        )
        scan_options = {}

        class Scanner:
            @staticmethod
            async def discover(**kwargs):
                scan_options.update(kwargs)
                return {identifier: (device, advertisement)}

        with tempfile.TemporaryDirectory() as folder:
            bridge = BluetoothBridge(Path(folder), platform_adapter=MacOSPlatformAdapter())
            bridge._bleak = lambda: (None, Scanner)
            devices = await bridge._discover()

        self.assertEqual(devices, [{"address": identifier, "name": "Dotii", "rssi": -42}])
        self.assertIs(bridge._ble_devices[identifier], device)
        self.assertEqual(scan_options, {"timeout": 10.0, "return_adv": True})

    async def test_scan_reads_status_on_the_discovery_event_loop(self):
        identifier = "243E23AE-4A99-406C-B317-18F1BD7B4CBE"
        loop_seen = {}
        with tempfile.TemporaryDirectory() as folder:
            bridge = BluetoothBridge(Path(folder), platform_adapter=MacOSPlatformAdapter())

            async def discover():
                loop_seen["discover"] = asyncio.get_running_loop()
                return [{"address": identifier, "name": "Dotii", "rssi": -42}]

            async def read_status(address):
                self.assertEqual(address, identifier)
                loop_seen["status"] = asyncio.get_running_loop()
                return {"name": "Dotii", "wifi": True}

            bridge._discover = discover
            bridge._read_status = read_status
            devices, selected, status, error = await bridge._discover_with_status()

        self.assertEqual(devices[0]["name"], "Dotii")
        self.assertEqual(selected, identifier)
        self.assertEqual(status["name"], "Dotii")
        self.assertIsNone(error)
        self.assertIs(loop_seen["discover"], loop_seen["status"])

    def test_monitor_restores_registered_device_in_management_list(self):
        identifier = "243E23AE-4A99-406C-B317-18F1BD7B4CBE"
        with tempfile.TemporaryDirectory() as folder:
            bridge = BluetoothBridge(Path(folder), platform_adapter=MacOSPlatformAdapter())
            bridge.last_address = identifier
            bridge.dependency_ready = lambda: True

            async def read_status(address):
                self.assertEqual(address, identifier)
                return {"name": "Dotii", "wifi": True, "bridge": "已连接"}

            bridge._read_status = read_status
            bridge.stop_event.set()
            bridge._monitor()

        self.assertTrue(bridge.device_connected)
        self.assertEqual(bridge.device_status["name"], "Dotii")
        self.assertEqual(bridge.devices, [{
            "address": identifier,
            "name": "Dotii",
            "rssi": None,
            "remembered": True,
        }])

    def test_scan_keeps_status_read_permission_failure(self):
        identifier = "243E23AE-4A99-406C-B317-18F1BD7B4CBE"
        denied = Exception("not authorized")
        denied.reason = SimpleNamespace(name="DENIED_BY_USER")
        with tempfile.TemporaryDirectory() as folder:
            bridge = BluetoothBridge(Path(folder), platform_adapter=MacOSPlatformAdapter())

            async def discover_with_status():
                return ([{"address": identifier, "name": "Dotii", "rssi": -42}], identifier, {}, denied)

            bridge._discover_with_status = discover_with_status
            bridge._scan()

        self.assertEqual(bridge.permission_state, "denied")
        self.assertEqual(bridge.connection_state, "denied")
        self.assertIn("隐私与安全性", bridge.connection_detail)

    def test_monitor_keeps_recognized_device_during_transient_disconnect(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge = BluetoothBridge(Path(folder), platform_adapter=MacOSPlatformAdapter())
            bridge.last_address = "243E23AE-4A99-406C-B317-18F1BD7B4CBE"
            bridge.device_status = {"name": "Dotii", "wifi": True}
            bridge.device_connected = True
            bridge.dependency_ready = lambda: True

            async def read_status(_):
                raise TimeoutError()

            bridge._read_status = read_status
            bridge.stop_event.set()
            bridge._monitor()

        self.assertFalse(bridge.device_connected)
        self.assertEqual(bridge.device_status["name"], "Dotii")
        self.assertEqual(bridge.connection_state, "timeout")
        self.assertEqual(bridge.devices[0]["name"], "Dotii")
        self.assertTrue(bridge.devices[0]["remembered"])

    def test_monitor_does_not_replace_stale_pairing_with_uuid_lookup_error(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge = BluetoothBridge(Path(folder), platform_adapter=MacOSPlatformAdapter())
            bridge.last_address = "243E23AE-4A99-406C-B317-18F1BD7B4CBE"
            bridge.connection_state = "stale_pairing"
            bridge.connection_detail = "请在系统设置 > 蓝牙中忽略 Dotii"
            bridge.dependency_ready = lambda: True

            async def read_status(_):
                raise RuntimeError(f"Device with address {bridge.last_address} was not found")

            bridge._read_status = read_status
            bridge.stop_event.set()
            bridge._monitor()

        self.assertEqual(bridge.connection_state, "stale_pairing")
        self.assertNotIn(bridge.last_address, bridge.connection_detail)

    def test_macos_start_requests_one_automatic_identification_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge = BluetoothBridge(Path(folder), platform_adapter=MacOSPlatformAdapter())
            bridge.stop_event.set()
            bridge.dependency_ready = lambda: True
            bridge._start_worker = mock.Mock(return_value=True)

            bridge.start()
            bridge.monitor.join(timeout=1)

        bridge._start_worker.assert_called_once_with(
            bridge._scan, name="dotii-ble-startup-scan"
        )
        self.assertEqual(bridge.operation_detail, "正在自动识别附近的 Dotii")

    async def test_configure_uses_corebluetooth_auto_pairing(self):
        identifier = "243E23AE-4A99-406C-B317-18F1BD7B4CBE"
        native_device = object()
        created = {}

        class Client:
            def __init__(self, target, **kwargs):
                created.update({"target": target, **kwargs})
                self.notification = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def start_notify(self, uuid, callback):
                self.notification = callback

            async def write_gatt_char(self, uuid, packet, response):
                if packet == b"\x03":
                    self.notification(None, bytearray(b'{"ok":true,"state":"restarting"}'))

        with tempfile.TemporaryDirectory() as folder:
            bridge = BluetoothBridge(Path(folder), platform_adapter=MacOSPlatformAdapter())
            bridge._bleak = lambda: (Client, None)
            bridge._ble_devices[identifier] = native_device
            result = await bridge._configure_async(identifier, b"{}")

        self.assertTrue(result["ok"])
        self.assertEqual(created["target"], identifier)
        self.assertFalse(created["pair"])
        self.assertEqual(created["timeout"], 45.0)


class BluetoothPairingRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_macos_does_not_attempt_windows_unpair_recovery(self):
        class FakeClient:
            unpair_calls = 0

            def __init__(self, target, **kwargs):
                self.target = target
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def start_notify(self, *_):
                raise OSError("Could not start notify on 0011: Unreachable")

            async def unpair(self):
                type(self).unpair_calls += 1

        service = BluetoothBridge.__new__(BluetoothBridge)
        service.lock = threading.RLock()
        service.platform = SimpleNamespace(name="macos", bluetooth_pair_on_connect=False)
        service._ble_devices = {"UUID": "native-device"}
        service._bleak = lambda: (FakeClient, object)

        with self.assertRaisesRegex(OSError, "Unreachable"):
            await service._configure_async("UUID", b"{}")

        self.assertEqual(FakeClient.unpair_calls, 0)

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
        service.platform = SimpleNamespace(name="windows", bluetooth_pair_on_connect=True)
        service._ble_devices = {"AA:BB": "native-device"}
        service._bleak = lambda: (FakeClient, object)
        service.operation_detail = ""
        service.updated_at_epoch = 0
        body = _configuration_payload(
            ssid="Studio",
            password="secret",
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
        self.assertTrue(
            _is_stale_windows_pairing_error(
                OSError("Could not start notify on 0011: Unreachable")
            )
        )
        self.assertFalse(_is_stale_windows_pairing_error(OSError("Device disconnected")))


if __name__ == "__main__":
    unittest.main()
