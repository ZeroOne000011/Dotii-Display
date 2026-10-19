"""Required vendored headers must survive a clean source checkout."""

import hashlib
import json
from pathlib import Path
import unittest


class FirmwareDependencyTests(unittest.TestCase):
    def test_zlib_config_header_matches_the_locked_component(self):
        component = Path(__file__).resolve().parents[2] / "managed_components/espressif__zlib"
        manifest = json.loads((component / "CHECKSUMS.json").read_text(encoding="utf-8"))
        expected = next(item for item in manifest["files"] if item["path"] == "zlib/zconf.h")
        header = (component / expected["path"]).read_bytes()
        self.assertEqual(len(header), expected["size"])
        self.assertEqual(hashlib.sha256(header).hexdigest(), expected["hash"])


if __name__ == "__main__":
    unittest.main()
