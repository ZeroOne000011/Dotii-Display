"""Keep firmware, API and package metadata on the same release version."""

import sys
from dotii_test_support import temporary_directory
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bridge"))

from codex_bridge import APP_VERSION, BridgeHandler
from firmware_flasher import _project_version
from runtime_paths import project_version


class VersionTests(unittest.TestCase):
    def test_api_and_firmware_share_the_cmake_version(self):
        version = project_version(ROOT)
        self.assertEqual(version, "1.2")
        self.assertEqual(APP_VERSION, version)
        self.assertEqual(_project_version(ROOT), version)
        self.assertEqual(BridgeHandler.server_version, f"StateDisplayBridge/{version}")

    def test_version_reader_preserves_legacy_and_missing_input_behavior(self):
        with temporary_directory() as temporary:
            root = Path(temporary)
            self.assertEqual(project_version(root), "")
            for value in ("1.2", "1.2.0", "1.2.0-dev"):
                (root / "CMakeLists.txt").write_text(f'set(PROJECT_VER "{value}")\n', encoding="utf-8")
                self.assertEqual(project_version(root), value)


if __name__ == "__main__":
    unittest.main()
