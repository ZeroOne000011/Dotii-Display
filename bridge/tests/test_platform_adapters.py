from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))

from platforms.macos import MacOSPlatformAdapter, parse_serial_ports as parse_macos_ports  # noqa: E402
from platforms.windows import WindowsPlatformAdapter, parse_serial_ports  # noqa: E402
from bluetooth_bridge import BluetoothBridge  # noqa: E402
import runtime_paths  # noqa: E402


class WindowsPlatformAdapterTests(unittest.TestCase):
    def test_windows_capabilities_remain_available(self) -> None:
        adapter = WindowsPlatformAdapter()
        self.assertTrue(adapter.startup_available)
        self.assertTrue(adapter.bluetooth_available)
        self.assertTrue(adapter.bluetooth_pair_on_connect)
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


class MacOSPlatformAdapterTests(unittest.TestCase):
    def test_scaffold_uses_application_support_and_unix_tools(self) -> None:
        adapter = MacOSPlatformAdapter()
        self.assertEqual(adapter.runtime_folder().name, "Dotii")
        self.assertEqual(adapter.runtime_folder().parent.name, "Application Support")
        self.assertEqual(adapter.bundled_node_candidates(Path("/app"))[0].name, "node")
        self.assertEqual(adapter.ffmpeg_candidates(Path("/runtime"), Path("/tools"))[0].name, "ffmpeg")

    def test_absolute_runtime_override_is_limited_to_explicit_development_runs(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            with mock.patch.dict("os.environ", {"DOTII_RUNTIME_DIR": temporary}, clear=False):
                self.assertEqual(MacOSPlatformAdapter().runtime_folder(), Path(temporary))
        with mock.patch.dict("os.environ", {"DOTII_RUNTIME_DIR": "relative"}, clear=False):
            self.assertEqual(MacOSPlatformAdapter().runtime_folder().name, "Dotii")

    def test_finder_safe_tool_candidates_include_standard_mac_locations(self) -> None:
        adapter = MacOSPlatformAdapter()
        codex = adapter.codex_cli_candidates(Path("/runtime"), Path("/app"))
        node = adapter.bundled_node_candidates(Path("/app"))
        ffmpeg = adapter.ffmpeg_candidates(Path("/runtime"), Path("/tools"))

        self.assertIn(Path("/opt/homebrew/bin/codex"), codex)
        self.assertIn(Path("/opt/homebrew/bin/node"), node)
        self.assertIn(Path("/opt/homebrew/bin/ffmpeg"), ffmpeg)
        self.assertLess(codex.index(Path("/app/tools/codex/bin/codex")), codex.index(Path("/opt/homebrew/bin/codex")))

    def test_app_bundle_separates_resources_and_helper_executables(self) -> None:
        executable = Path("/Applications/Dotii Management Center.app/Contents/Resources/DotiiBridgeRuntime/DotiiBridge")
        with (
            mock.patch.object(runtime_paths.sys, "platform", "darwin"),
            mock.patch.object(runtime_paths.sys, "executable", str(executable)),
            mock.patch.object(runtime_paths.sys, "frozen", True, create=True),
        ):
            self.assertEqual(
                runtime_paths.application_root(),
                Path("/Applications/Dotii Management Center.app/Contents/Resources"),
            )
            self.assertEqual(
                runtime_paths.sibling_executable("DotiiManagementCenter"),
                Path("/Applications/Dotii Management Center.app/Contents/MacOS/DotiiManagementCenter"),
            )

    def test_platform_capabilities_match_implemented_macos_boundaries(self) -> None:
        adapter = MacOSPlatformAdapter()
        self.assertFalse(adapter.startup_available)
        self.assertTrue(adapter.bluetooth_available)
        self.assertFalse(adapter.bluetooth_pair_on_connect)
        self.assertTrue(adapter.serial_flash_available)
        self.assertFalse(adapter.startup_enabled())

    def test_packaged_login_item_uses_native_host_cli(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            macos = Path(temporary) / "Dotii.app" / "Contents" / "MacOS"
            macos.mkdir(parents=True)
            bridge = macos.parent / "Resources" / "DotiiBridgeRuntime" / "DotiiBridge"
            bridge.parent.mkdir(parents=True)
            bridge.touch()
            host = macos / "DotiiManagementCenter"
            host.touch()
            responses = [
                mock.Mock(returncode=0, stdout="enabled\n", stderr=""),
                mock.Mock(returncode=0, stdout="enabled\n", stderr=""),
            ]
            with (
                mock.patch.object(sys, "executable", str(bridge)),
                mock.patch("platforms.macos.subprocess.run", side_effect=responses) as run,
            ):
                adapter = MacOSPlatformAdapter()
                command = adapter.current_startup_command(
                    frozen=True,
                    executable=bridge,
                    app_script=Path("unused"),
                    management_center=host,
                )
                self.assertTrue(adapter.set_startup(True, command))

            self.assertEqual(run.call_args_list[0].args[0], [str(host.resolve()), "--login-item", "enable"])
            self.assertEqual(run.call_args_list[1].args[0], [str(host.resolve()), "--login-item", "status"])

    def test_macos_serial_ports_use_callout_devices_and_dotii_vid_pid(self) -> None:
        ports = parse_macos_ports([
            SimpleNamespace(
                device="/dev/tty.usbmodem2101", description="TTY twin",
                vid=0x303A, pid=0x1001,
            ),
            SimpleNamespace(
                device="/dev/cu.usbserial-other", description="Other USB",
                vid=0x1234, pid=0x5678,
            ),
            SimpleNamespace(
                device="/dev/cu.usbmodem2101", description="Dotii ESP32-S3",
                vid=0x303A, pid=0x1001,
            ),
        ])

        self.assertEqual([item["port"] for item in ports], [
            "/dev/cu.usbmodem2101", "/dev/cu.usbserial-other",
        ])
        self.assertTrue(ports[0]["dotii"])
        self.assertFalse(ports[1]["dotii"])
        self.assertEqual(ports[0]["pnp_id"], "USB VID:PID=303A:1001")

    def test_macos_serial_validation_rejects_tty_and_path_traversal(self) -> None:
        adapter = MacOSPlatformAdapter()
        self.assertTrue(adapter.valid_serial_port("/dev/cu.usbmodem2101"))
        self.assertFalse(adapter.valid_serial_port("/dev/tty.usbmodem2101"))
        self.assertFalse(adapter.valid_serial_port("/dev/cu../disk0"))
        self.assertFalse(adapter.valid_serial_port("COM6"))

    def test_bluetooth_service_is_available_with_corebluetooth(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            service = BluetoothBridge(Path(temporary), platform_adapter=MacOSPlatformAdapter())
            snapshot = service.snapshot()

            self.assertTrue(snapshot["available"])
            self.assertTrue(snapshot["dependency_ready"])
            self.assertEqual(snapshot["permission_state"], "unknown")


if __name__ == "__main__":
    unittest.main()
