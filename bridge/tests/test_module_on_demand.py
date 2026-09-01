from __future__ import annotations

import sys
import tempfile
import unittest
import json
import re
from pathlib import Path
from unittest import mock

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))

import bambu_client  # noqa: E402
import codex_app_server  # noqa: E402
import codex_bridge  # noqa: E402
from bambu_client import BambuConfigStore, BambuService  # noqa: E402
from codex_bridge import (  # noqa: E402
    CodexCheckStore,
    CUSTOM_DEFAULTS,
    resolve_ffmpeg,
    validate_custom_config,
    validate_module_config,
)


class ModuleOnDemandTests(unittest.TestCase):
    def test_codex_check_result_is_persisted_for_the_next_page_load(self):
        result = {
            "ok": True,
            "checked_at_epoch": 1788256800,
            "account": {"logged_in": True, "email": "user@example.com", "plan_type": "plus"},
            "checks": {"rate_limits": True, "usage": True, "threads": True},
        }
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            path = Path(temporary) / "codex-check.json"
            CodexCheckStore(path).write(result)

            self.assertEqual(CodexCheckStore(path).read(), result)

    def test_frontend_restores_saved_codex_check_from_overview(self):
        script = (BRIDGE / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("overview.codex_check", script)

    def test_codex_check_copy_matches_requested_text(self):
        html = (BRIDGE / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("检查本机 Codex CLI、App Server、登录账户、额度、用量和任务读取是否正常。", html)
        self.assertNotIn("手动检查本机 Codex CLI", html)

    def test_first_run_defaults_keep_codex_and_bambu_off(self):
        self.assertEqual(
            validate_module_config(None),
            {"codex": False, "bambu": False, "dotii": True},
        )

    def test_first_run_defaults_enable_custom_and_dotii_pages(self):
        self.assertTrue(CUSTOM_DEFAULTS["enabled"])
        self.assertTrue(validate_custom_config(None)["enabled"])
        self.assertTrue(validate_custom_config({})["enabled"])

    def test_frontend_overview_references_only_existing_status_elements(self):
        html = (BRIDGE / "web" / "index.html").read_text(encoding="utf-8")
        script = (BRIDGE / "web" / "app.js").read_text(encoding="utf-8")
        ids = set(re.findall(r'id="([^"]+)"', html))
        refs = set()
        for pattern in (
            r'byId\("([^"]+)"\)',
            r'setText\("([^"]+)"',
            r'setMarkdown\("([^"]+)"',
            r'setMarkdownMessages\("([^"]+)"',
        ):
            refs.update(re.findall(pattern, script))
        self.assertEqual(refs - ids, set())

    def test_dotii_disabled_content_is_hidden_even_with_grid_layout(self):
        styles = (BRIDGE / "web" / "styles.css").read_text(encoding="utf-8")
        script = (BRIDGE / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn(".dotii-content[hidden]", styles)
        self.assertIn('byId("dotii-content").hidden = !enabled;', script)

    def test_dotii_animation_manifest_points_to_packaged_assets(self):
        manifest_path = BRIDGE / "web" / "assets" / "expressions" / "animations.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        animations = manifest["animations"]
        self.assertEqual(len(animations), 9)
        for animation in animations:
            asset = BRIDGE / "web" / animation["src"].lstrip("/")
            self.assertTrue(asset.is_file(), animation["id"])

    def test_bundled_codex_app_server_command_uses_bundled_node(self):
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            folder = Path(temporary)
            script = (
                folder
                / "tools"
                / "codex-cli"
                / "node_modules"
                / "@openai"
                / "codex"
                / "bin"
                / "codex.js"
            )
            script.parent.mkdir(parents=True)
            script.write_text("", encoding="utf-8")
            node = folder / "tools" / "node" / "node.exe"
            node.parent.mkdir(parents=True)
            node.touch()

            with (
                mock.patch.object(codex_app_server, "application_root", return_value=folder),
                mock.patch.object(codex_app_server.shutil, "which") as which,
            ):
                command = codex_app_server._resolve_codex_command(None, folder / "runtime")

            which.assert_not_called()
            self.assertEqual(command, [str(node), str(script)])

    def test_bundled_ffmpeg_is_preferred_over_path(self):
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            folder = Path(temporary)
            bundled = folder / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
            bundled.parent.mkdir(parents=True)
            bundled.touch()

            with (
                mock.patch.object(codex_bridge, "tools_root", return_value=folder / "tools"),
                mock.patch.object(codex_bridge.shutil, "which") as which,
            ):
                resolved = resolve_ffmpeg(folder / "runtime")

            which.assert_not_called()
            self.assertEqual(resolved, str(bundled.resolve()))

    def test_bambu_snapshot_does_not_check_ffmpeg_until_rtsps_path_runs(self):
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            config = BambuConfigStore(Path(temporary) / "bambu.json")
            config.write(
                {
                    "host": "192.0.2.10",
                    "access_code": "12345678",
                    "serial": "01P00A000000000",
                    "camera_enabled": True,
                }
            )
            ffmpeg = mock.Mock(return_value=None)
            service = BambuService(config, lambda: True, ffmpeg)
            snapshot = service.snapshot()

            self.assertFalse(snapshot["connected"])
            self.assertFalse(snapshot["camera_available"])
            ffmpeg.assert_not_called()

    def test_disabling_bambu_closes_mqtt_and_camera_runtime(self):
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            config = BambuConfigStore(Path(temporary) / "bambu.json")
            config.write({"host": "192.0.2.10", "serial": "01P00A000000000", "access_code": "12345678"})
            enabled = {"value": True}
            service = BambuService(config, lambda: enabled["value"], lambda: None)
            mqtt = mock.Mock()
            camera_process = mock.Mock()
            camera_process.poll.return_value = None
            service._mqtt = mqtt
            service._camera_process = camera_process
            enabled["value"] = False

            service.set_enabled(False)

            mqtt.close.assert_called_once()
            camera_process.terminate.assert_called_once()

    def test_frontend_removes_dependency_panels_and_maintenance_actions(self):
        html = (BRIDGE / "web" / "index.html").read_text(encoding="utf-8")
        script = (BRIDGE / "web" / "app.js").read_text(encoding="utf-8")
        backend = (BRIDGE / "codex_bridge.py").read_text(encoding="utf-8")

        self.assertNotIn("Codex 连接", html)
        self.assertNotIn("Bambu 模块与相机依赖", html)
        self.assertNotIn("install-dependency", html + script)
        self.assertNotIn("reinstall-dependency", html + script)
        self.assertNotIn("/api/v1/admin/codex/detect", backend)
        self.assertIn("Codex 运行检测", html)
        self.assertIn("/api/v1/admin/codex/check", backend)
        self.assertIn("/api/v1/admin/codex/check", script)
        self.assertNotIn("/api/v1/admin/dependencies/install", backend)
        self.assertNotIn("/api/v1/admin/bambu/ffmpeg/install", backend)
        self.assertIn("保存并连接", html)
        self.assertIn("相机画面暂不可用", html)
        self.assertIn("resolve_ffmpeg", backend)


if __name__ == "__main__":
    unittest.main()
