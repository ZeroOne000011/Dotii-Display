from __future__ import annotations

import sys
import unittest
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))

from bambu_client import BambuService, _camera_jpegs, _filament, _state_name  # noqa: E402


class BambuMaterialTests(unittest.TestCase):
    def test_nested_ams_active_tray(self) -> None:
        report = {
            "ams": {
                "tray_now": "1",
                "ams": [{
                    "id": "0",
                    "tray": [
                        {"id": "0", "tray_type": "PETG", "tray_color": "000000FF", "remain": 90},
                        {"id": "1", "tray_type": "PLA", "tray_color": "FFFFFFFF", "remain": 27},
                    ],
                }],
            }
        }
        result = _filament(report)
        self.assertEqual(result["text"], "PLA · 27%")
        self.assertEqual(result["color"], "FFFFFFFF")
        self.assertEqual(result["remaining_percent"], 27)

    def test_global_tray_number_across_multiple_ams(self) -> None:
        report = {
            "ams": {
                "tray_now": "5",
                "ams": [
                    {"id": "0", "tray": [{"id": "1", "tray_type": "PLA"}]},
                    {"id": "1", "tray": [{"id": "1", "tray_type": "ABS", "remain": 52}]},
                ],
            }
        }
        self.assertEqual(_filament(report)["text"], "ABS · 52%")

    def test_legacy_ams_list_and_top_level_tray(self) -> None:
        report = {
            "tray_now": "2",
            "ams": [{"id": "0", "tray": [{"id": "2", "tray_type": "PETG-CF", "remain": -1}]}],
        }
        result = _filament(report)
        self.assertEqual(result["text"], "PETG-CF")
        self.assertIsNone(result["remaining_percent"])

    def test_virtual_tray(self) -> None:
        report = {
            "ams": {"tray_now": "254", "ams": []},
            "vt_tray": {"tray_type": "TPU", "tray_color": "FF0000FF", "remain": 61},
        }
        self.assertEqual(_filament(report)["text"], "TPU · 61%")


class BambuProtocolTests(unittest.TestCase):
    def test_unknown_job_state_is_not_offline(self) -> None:
        self.assertEqual(_state_name("FUTURE_STATE"), ("unknown", "未知状态"))

    def test_accepts_printer_owned_rtsps_url(self) -> None:
        config = {"host": "192.168.1.20", "access_code": "12345678"}
        report = {"ipcam": {"rtsp_url": "rtsps://192.168.1.20:322/streaming/live/1"}}
        value = BambuService._rtsp_input(config, report)
        self.assertEqual(value, "rtsps://bblp:12345678@192.168.1.20:322/streaming/live/1")

    def test_rejects_rtsp_redirect_to_another_host(self) -> None:
        config = {"host": "192.168.1.20", "access_code": "12345678"}
        report = {"ipcam": {"rtsp_url": "rtsps://192.168.1.99:322/streaming/live/1"}}
        self.assertEqual(BambuService._rtsp_input(config, report), "")

    def test_extracts_all_complete_jpegs_and_keeps_partial_tail(self) -> None:
        first = b"\xff\xd8one\xff\xd9"
        second = b"\xff\xd8two\xff\xd9"
        buffer = bytearray(b"noise" + first + second + b"\xff\xd8partial")
        self.assertEqual(_camera_jpegs(buffer), [first, second])
        self.assertEqual(buffer, bytearray(b"\xff\xd8partial"))


if __name__ == "__main__":
    unittest.main()
