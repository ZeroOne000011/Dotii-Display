from __future__ import annotations
import json
import html
import re
import sys
from dotii_test_support import temporary_directory
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from codex_bridge import BridgeHandler
from connection_settings import ConnectionSettings
from localization import localize_admin, system_text


class ConnectionApiTests(unittest.TestCase):
    def setUp(self):
        self.folder = temporary_directory()
        self.server = ThreadingHTTPServer(("127.0.0.1",0),BridgeHandler)
        self.server.connections = ConnectionSettings(Path(self.folder.name)/"connection.json")
        self.server.usb = mock.Mock()
        self.server.token = "test-device-token"
        self.server.wifi_seen = {}
        self.worker = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.worker.join();self.folder.cleanup()

    def post(self,path,data,content_type="application/json"):
        request=Request(self.base+path,data=json.dumps(data).encode(),headers={"Content-Type":content_type})
        return json.load(urlopen(request))

    def test_language_change_resets_legacy_override_and_persists(self):
        self.server.connections.write({"device_language":"zh-CN"})
        result=self.post("/api/v1/admin/connection",{"mode":"usb","language":"en"})
        self.assertEqual(result["connection_settings"]["mode"],"usb")
        self.assertEqual(result["connection_settings"]["language"],"en")
        self.assertEqual(result["connection_settings"]["device_language"],"follow")
        reopened=ConnectionSettings(self.server.connections.path)
        self.assertEqual(reopened.device()["language"],"en")
        self.post("/api/v1/admin/connection",{"language":"zh-CN","device_language":"en"})
        self.assertEqual(reopened.device()["language"],"zh-CN")

    def test_invalid_mode_and_private_device_identity_cannot_be_overwritten(self):
        for changes in ({"mode":"bad"},{"device_id":"another-device"},{"token":"secret"}):
            with self.assertRaises(HTTPError) as error:self.post("/api/v1/admin/connection",changes)
            self.assertEqual(error.exception.code,400)
        self.assertEqual(self.server.connections.read()["mode"],"auto")

    def test_network_setup_is_only_queued_and_password_is_not_in_response(self):
        response=self.post("/api/v1/admin/usb/configure",{"ssid":"network","password":"test-password","preserve_password":False})
        self.server.usb.configure.assert_called_once_with("network","test-password",False)
        self.assertNotIn("test-password",json.dumps(response))

    def test_admin_requires_loopback_and_json_content_type(self):
        with mock.patch("codex_bridge._is_loopback",return_value=False):
            with self.assertRaises(HTTPError) as error:self.post("/api/v1/admin/connection",{"mode":"usb"})
            self.assertEqual(error.exception.code,403)
        with self.assertRaises(HTTPError) as error:self.post("/api/v1/admin/connection",{"mode":"usb"},"text/plain")
        self.assertEqual(error.exception.code,400)
        self.assertEqual(self.server.connections.read()["mode"],"auto")

    def test_wifi_heartbeat_requires_device_token(self):
        with self.assertRaises(HTTPError) as error:urlopen(self.base+"/api/v1/device/ping")
        self.assertEqual(error.exception.code,401)
        request=Request(self.base+"/api/v1/device/ping",headers={"X-Bridge-Token":self.server.token,"X-Dotii-Device":"test-device"})
        self.assertTrue(json.load(urlopen(request))["ok"])
        self.assertIn("test-device",self.server.wifi_seen)

    def test_duplicate_flash_does_not_resume_usb_during_maintenance(self):
        self.server.usb.flash_lock=threading.Lock()
        self.server.firmware=mock.Mock(operation_state="running")
        with self.assertRaises(HTTPError) as error:
            self.post("/api/v1/admin/firmware/flash",{"port":"COM3","confirmation":"FLASH_DOTII"})
        self.assertEqual(error.exception.code,409)
        self.server.usb.pause.assert_not_called()
        self.server.usb.resume.assert_not_called()


class LocalizationTests(unittest.TestCase):
    def test_codex_diagnostics_are_translatable(self):
        self.assertEqual(system_text("本机私有"), "Local private install")
        self.assertEqual(system_text("部分只读接口检测失败：usage: timeout"),
                         "Some read-only checks failed: usage: timeout")
        self.assertNotRegex(system_text("Codex CLI、App Server、账户、额度、用量与任务列表均可正常读取"),
                            r"[\u4e00-\u9fff]")
    def test_all_static_web_labels_have_translations(self):
        web=Path(__file__).resolve().parents[1]/"web"
        catalog=json.loads((web/"translations.json").read_text(encoding="utf-8"))
        source=(web/"app.js").read_text(encoding="utf-8")
        keys=re.findall(r'\bt\("([^"\n]+)"\)',source)
        keys += [html.unescape(key) for key in re.findall(r'data-i18n="([^"]+)"',(web/"index.html").read_text(encoding="utf-8"))]
        missing=sorted({key for key in keys if re.search('[\u4e00-\u9fff]',key) and key not in catalog})
        self.assertEqual(missing,[])

    def test_system_labels_translate_but_user_content_and_names_remain_original(self):
        value={"reason":"Codex 正在工作","custom":{"title":"工作中","body":"自定义"},"task":{"title":"工作中","last_user_message":"失败"},"filename":"打印中.3mf"}
        actual=localize_admin(value)
        self.assertEqual(actual["reason"],"Codex is working")
        self.assertEqual(actual["custom"],value["custom"])
        self.assertEqual(actual["task"],value["task"])
        self.assertEqual(actual["filename"],value["filename"])

    def test_formatted_errors_keep_relevant_values(self):
        self.assertEqual(system_text("Wi-Fi 密码不能超过 64 字节"),"Wi-Fi password must be no longer than 64 bytes")
        self.assertEqual(system_text("自定义画面必须为 434312 字节 RGB565"),"Custom image must contain 434312 bytes of RGB565 data")
