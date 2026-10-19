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
from platforms.macos import MacOSPlatformAdapter
from platforms.windows import WindowsPlatformAdapter


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

    def test_control_rejection_is_returned_in_next_snapshot_without_exposing_error(self):
        server = SimpleNamespace(token="local-test-token", bambu=mock.Mock(), connections=mock.Mock())
        server.bambu.command.side_effect = ValueError("private diagnostic")
        server.snapshot = lambda: {}
        server.connections.device.return_value = {}
        bridge = UsbBridge(server)
        incoming = encode_frame({"event":"bambu_command","action":"pause","command_id":7})
        incoming += encode_frame({"id":1,"ok":True})
        bridge.serial = mock.Mock(in_waiting=len(incoming))
        bridge.serial.read.side_effect = [incoming, encode_frame({"id":2,"ok":True})]
        bridge._exchange("snapshot")
        bridge._sync()
        sent = FrameDecoder().feed(bridge.serial.write.call_args.args[0])[0]
        self.assertEqual(sent["bambu_command_result"], {"command_id":7,"action":"pause","ok":False})
        self.assertNotIn("private diagnostic", json.dumps(sent))

    def test_identified_control_event_is_not_executed_twice(self):
        server = SimpleNamespace(token="local-test-token", bambu=mock.Mock())
        server.bambu.command.return_value = {"accepted": True}
        bridge = UsbBridge(server)
        event = encode_frame({"event":"bambu_command","action":"stop","command_id":8})
        incoming = event + event + encode_frame({"id":1,"ok":True})
        bridge.serial = mock.Mock(in_waiting=len(incoming))
        bridge.serial.read.return_value = incoming
        bridge._exchange("snapshot")
        server.bambu.command.assert_called_once_with("stop")
        self.assertEqual(bridge.command_result, {"command_id":8,"action":"stop","ok":True})

    def test_malformed_control_does_not_crash_or_control_the_printer(self):
        server = SimpleNamespace(token="local-test-token", bambu=mock.Mock())
        bridge = UsbBridge(server)
        incoming = encode_frame({"event":"bambu_command","action":{},"command_id":9})
        incoming += encode_frame({"id":1,"ok":True})
        bridge.serial = mock.Mock(in_waiting=len(incoming))
        bridge.serial.read.return_value = incoming
        bridge._exchange("snapshot")
        server.bambu.command.assert_not_called()
        self.assertFalse(bridge.command_result["ok"])

    def test_provision_requires_active_usb_and_valid_utf8_lengths(self):
        bridge = UsbBridge(SimpleNamespace())
        with self.assertRaises(ValueError): bridge.configure("网络", "password")
        bridge.update(state="connected")
        with self.assertRaises(ValueError): bridge.configure("网"*11, "password")
        bridge.configure("网络", "password")
        self.assertEqual(bridge.snapshot()["provision_state"], "writing")
        self.assertNotIn("password", json.dumps(bridge.snapshot()))
        with self.assertRaises(ValueError): bridge.configure("network", "password")

    def test_platform_port_filter_and_control_lines_before_open(self):
        for adapter, valid, invalid in (
            (MacOSPlatformAdapter(), "/dev/cu.usbmodem101", "/dev/tty.usbmodem101"),
            (WindowsPlatformAdapter(), "COM6", "/dev/cu.usbmodem101"),
        ):
            with self.subTest(platform=adapter.name):
                server = SimpleNamespace(connections=mock.Mock())
                server.connections.read.return_value = {"port": "", "device_id": ""}
                server.connections.device.return_value = {}
                bridge = UsbBridge(server, platform_adapter=adapter)
                ports = [SimpleNamespace(device=p, vid=0x303A, pid=0x1001) for p in (invalid, valid)]
                ports.append(SimpleNamespace(device=valid + "2", vid=0x1234, pid=0x1001))
                opened = []
                connection = SimpleNamespace(dtr=True, rts=True, port=None)
                connection.open = lambda: opened.append((connection.port, connection.dtr, connection.rts))
                connection.close = mock.Mock()

                def disconnected(*args, **kwargs):
                    bridge.stop_event.set()
                    raise OSError("device removed")

                with mock.patch("serial.tools.list_ports.comports", return_value=ports), \
                     mock.patch("serial.Serial", return_value=connection), \
                     mock.patch.object(bridge, "_exchange", side_effect=disconnected):
                    bridge._run()
                self.assertEqual(opened, [(valid, adapter.serial_data_dtr_on_open, False)])
                self.assertFalse(connection.dtr)
                connection.close.assert_called_once()
                self.assertTrue(bridge.released.is_set())
                self.assertEqual(bridge.snapshot()["detail"], "usb_connection_failed")

    def test_pause_during_port_selection_cannot_open_serial(self):
        server = SimpleNamespace(connections=mock.Mock())
        server.connections.read.return_value = {"port": "", "device_id": ""}
        bridge = UsbBridge(server, platform_adapter=MacOSPlatformAdapter())

        def pause_during_selection():
            bridge.pause()
            bridge.stop_event.set()
            return [SimpleNamespace(device="/dev/cu.usbmodem101", vid=0x303A, pid=0x1001)]

        with mock.patch("serial.tools.list_ports.comports", side_effect=pause_during_selection), \
             mock.patch("serial.Serial") as serial_open:
            bridge._run()
        serial_open.assert_not_called()
        self.assertTrue(bridge.released.is_set())


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
