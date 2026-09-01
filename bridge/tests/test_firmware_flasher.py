from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))

from firmware_flasher import (  # noqa: E402
    FirmwareFlasher,
    _firmware_root,
    _parse_ports,
    _project_version,
    _stub_data_ready,
)


class FirmwareFlasherTests(unittest.TestCase):
    def test_dotii_usb_port_is_prioritized(self) -> None:
        payload = json.dumps([
            {"DeviceID": "COM8", "Name": "Other", "PNPDeviceID": "USB\\VID_1234&PID_5678"},
            {"DeviceID": "COM17", "Name": "USB Serial", "PNPDeviceID": "USB\\VID_303A&PID_1001&MI_00"},
        ])
        ports = _parse_ports(payload)
        self.assertEqual(ports[0]["port"], "COM17")
        self.assertTrue(ports[0]["dotii"])
        self.assertFalse(ports[1]["dotii"])

    def test_invalid_port_names_are_ignored(self) -> None:
        payload = json.dumps({"DeviceID": "LPT1", "Name": "Printer", "PNPDeviceID": ""})
        self.assertEqual(_parse_ports(payload), [])

    def test_fallback_version_comes_from_project_cmake(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "CMakeLists.txt").write_text(
                'set(PROJECT_VER "1.0.0")\nproject(state_display)\n', encoding="utf-8"
            )
            self.assertEqual(_project_version(root), "1.0.0")

    def test_package_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "build").mkdir()
            (root / "outside.bin").write_bytes(b"x")
            (root / "build" / "flasher_args.json").write_text(
                json.dumps({"flash_files": {"0x10000": "../outside.bin"}}), encoding="utf-8"
            )
            flasher = FirmwareFlasher(root, root / "runtime", run=lambda *args, **kwargs: type("R", (), {"returncode": 1})())
            self.assertFalse(flasher.package["ready"])

    def test_source_and_exe_share_formal_firmware_layout(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            firmware = root / "firmware"
            firmware.mkdir()
            (firmware / "flasher_args.json").write_text(
                json.dumps({"flash_files": {"0x10000": "state_display.bin"}}), encoding="utf-8"
            )
            (firmware / "state_display.bin").write_bytes(b"firmware")
            self.assertEqual(_firmware_root(root), firmware.resolve())

            flasher = FirmwareFlasher(
                root,
                root / "runtime",
                run=lambda *args, **kwargs: type("R", (), {"returncode": 1})(),
            )
            self.assertEqual(flasher.build_root, firmware.resolve())
            self.assertTrue(flasher.package["ready"])

    def test_esptool_requires_esp32s3_stub_data(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertFalse(_stub_data_ready(root))
            stub = root / "targets" / "stub_flasher" / "2" / "esp32s3.json"
            stub.parent.mkdir(parents=True)
            stub.write_text("{}", encoding="ascii")
            self.assertTrue(_stub_data_ready(root))


if __name__ == "__main__":
    unittest.main()
