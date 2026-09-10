from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))

from platforms.macos import MacOSPlatformAdapter  # noqa: E402
from platforms.windows import WindowsPlatformAdapter, parse_serial_ports  # noqa: E402
from bluetooth_bridge import BluetoothBridge  # noqa: E402


class WindowsPlatformAdapterTests(unittest.TestCase):
    def test_windows_capabilities_remain_available(self) -> None:
        adapter = WindowsPlatformAdapter()
        self.assertTrue(adapter.startup_available)
        self.assertTrue(adapter.bluetooth_available)
        self.assertTrue(adapter.serial_flash_available)

    def test_runtime_folder_keeps_existing_windows_location(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            with mock.patch.dict("os.environ", {"LOCALAPPDATA": temporary}, clear=False):
                self.assertEqual(
                    WindowsPlatformAdapter().runtime_folder(),
                    Path(temporary) / "StateDisplay",
                )

    def test_packaged_startup_keeps_management_center_entry(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            executable = Path(temporary) / "DotiiManagementCenter.exe"
            command = WindowsPlatformAdapter().packaged_startup_command(executable)
            self.assertEqual(command, f'"{executable.resolve()}" --startup')
            self.assertNotIn("DotiiBridge.exe", command)

    def test_serial_payload_stays_sorted_with_dotii_first(self) -> None:
        payload = json.dumps([
            {"DeviceID": "COM8", "Name": "Other", "PNPDeviceID": "USB\\VID_1234&PID_5678"},
            {"DeviceID": "COM17", "Name": "Dotii", "PNPDeviceID": "USB\\VID_303A&PID_1001"},
        ])
        ports = parse_serial_ports(payload)
        self.assertEqual([item["port"] for item in ports], ["COM17", "COM8"])
        self.assertTrue(ports[0]["dotii"])


class MacOSPlatformScaffoldTests(unittest.TestCase):
    def test_scaffold_uses_application_support_and_unix_tools(self) -> None:
        adapter = MacOSPlatformAdapter()
        self.assertEqual(adapter.runtime_folder().name, "Dotii")
        self.assertEqual(adapter.runtime_folder().parent.name, "Application Support")
        self.assertEqual(adapter.bundled_node_candidates(Path("/app"))[0].name, "node")
        self.assertEqual(adapter.ffmpeg_candidates(Path("/runtime"), Path("/tools"))[0].name, "ffmpeg")

    def test_unimplemented_services_are_not_reported_as_available(self) -> None:
        adapter = MacOSPlatformAdapter()
        self.assertFalse(adapter.startup_available)
        self.assertFalse(adapter.bluetooth_available)
        self.assertFalse(adapter.serial_flash_available)
        self.assertFalse(adapter.startup_enabled())
        self.assertEqual(adapter.scan_serial_ports(lambda *args, **kwargs: None), [])

    def test_bluetooth_service_stays_disabled_until_macos_support_exists(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            service = BluetoothBridge(Path(temporary), platform_adapter=MacOSPlatformAdapter())
            service.start()

            self.assertFalse(service.snapshot()["available"])
            self.assertIsNone(service.monitor)
            with self.assertRaisesRegex(ValueError, "尚未支持"):
                service.start_scan()


if __name__ == "__main__":
    unittest.main()
