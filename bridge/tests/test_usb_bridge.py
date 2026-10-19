from __future__ import annotations
import json
import struct
import sys
from dotii_test_support import temporary_directory
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from usb_bridge import FrameDecoder, UsbBridge, encode_frame, MAX_FRAME
from connection_settings import ConnectionSettings


class UsbProtocolTests(unittest.TestCase):
    def test_fragmentation_noise_and_concatenated_frames(self):
        decoder = FrameDecoder()
        wire = b"boot log\r\nDT" + encode_frame({"id": 1, "title": "中文内容"}) + encode_frame({"id": 2})
        actual = []
        for index in range(0, len(wire), 3):
            actual.extend(decoder.feed(wire[index:index+3]))
        self.assertEqual(actual, [{"id": 1, "title": "中文内容"}, {"id": 2}])

    def test_corrupt_crc_and_oversized_headers_resynchronize(self):
        corrupt = bytearray(encode_frame({"id": 1}))
        corrupt[-1] ^= 1
        wire = b"DTU1" + struct.pack("<II", MAX_FRAME + 1, 0) + corrupt + encode_frame({"id": 2})
        self.assertEqual(FrameDecoder().feed(wire), [{"id": 2}])

    def test_oversize_json_rejected_before_write(self):
        with self.assertRaises(ValueError):
            encode_frame({"data": "x" * MAX_FRAME})

    def test_foreign_response_never_completes_request(self):
        bridge = UsbBridge(SimpleNamespace(token="local-test-token"))
        response = encode_frame({"id": 999, "ok": True}) + encode_frame({"id": 1, "ok": True, "product": "Dotii"})
        bridge.serial = mock.Mock(in_waiting=len(response))
        bridge.serial.read.side_effect = [response]
        self.assertEqual(bridge._exchange("hello")["product"], "Dotii")

    def test_images_use_exact_offsets_and_whole_image_crc(self):
        bridge = UsbBridge(SimpleNamespace())
        bridge._exchange = mock.Mock()
        body = bytes(range(256)) * 40
        bridge._image("custom", 11, body)
        calls = bridge._exchange.call_args_list
        self.assertEqual([c.kwargs["offset"] for c in calls], [0,4096,8192])
        self.assertTrue(all(c.kwargs["crc"] == zlib.crc32(body) for c in calls))
        self.assertTrue(all(c.kwargs["total"] == len(body) for c in calls))

    def test_binding_failure_is_not_reported_as_success(self):
        bridge = UsbBridge(SimpleNamespace(token="local-test-token"))
        reply = encode_frame({"id":1,"ok":False,"error":"binding_mismatch"})
        bridge.serial = mock.Mock(in_waiting=len(reply))
        bridge.serial.read.return_value = reply
        with self.assertRaisesRegex(ValueError, "binding_mismatch"):
            bridge._exchange("hello")

    def test_control_event_after_ack_in_same_read_is_not_lost(self):
        server = SimpleNamespace(token="local-test-token", bambu=mock.Mock())
        bridge = UsbBridge(server)
        reply = encode_frame({"id":1,"ok":True}) + encode_frame({"event":"bambu_command","action":"pause"})
        bridge.serial = mock.Mock(in_waiting=len(reply))
        bridge.serial.read.return_value = reply
        self.assertTrue(bridge._exchange("snapshot")["ok"])
        server.bambu.command.assert_called_once_with("pause")

    def test_provision_requires_active_usb_and_valid_utf8_lengths(self):
        bridge = UsbBridge(SimpleNamespace())
        with self.assertRaises(ValueError): bridge.configure("网络", "password")
        bridge.update(state="connected")
        with self.assertRaises(ValueError): bridge.configure("网"*11, "password")
        bridge.configure("网络", "password")
        self.assertEqual(bridge.snapshot()["provision_state"], "writing")
        self.assertNotIn("password", json.dumps(bridge.snapshot()))
        with self.assertRaises(ValueError): bridge.configure("network", "password")


class ConnectionSettingsTests(unittest.TestCase):
    def test_roundtrip_unified_language_and_old_defaults(self):
        with temporary_directory() as folder:
            settings = ConnectionSettings(Path(folder)/"connection.json")
            self.assertEqual(settings.device(), {"language":"zh-CN","connection_mode":"auto"})
            settings.write({"language":"en", "device_language":"zh-CN", "mode":"usb"})
            self.assertEqual(settings.device(), {"language":"en","connection_mode":"usb"})
            settings.write({"device_language":"follow"})
            self.assertEqual(settings.device()["language"], "en")

    def test_invalid_modes_and_fields_do_not_overwrite_configuration(self):
        with temporary_directory() as folder:
            settings = ConnectionSettings(Path(folder)/"connection.json")
            settings.write({"language":"en"})
            for changes in ({"mode":"bluetooth"},{"language":"fr"},{"token":"secret"}):
                with self.assertRaises(ValueError): settings.write(changes)
            self.assertEqual(settings.read()["language"], "en")


if __name__ == "__main__": unittest.main()
