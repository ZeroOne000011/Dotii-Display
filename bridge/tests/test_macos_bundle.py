from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

from dotii_test_support import temporary_directory

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packaging" / "macos"))
from verify_bundle import ARM64, macho_cpus, macho_minimum_macos, same_file, verify_architectures


class MacOSBundleTests(unittest.TestCase):
    def test_minimum_os_in_nested_library_must_match_declared_support(self):
        with temporary_directory() as folder:
            path = Path(folder) / "node"
            path.write_bytes(struct.pack("<IIIIIIII", 0xFEEDFACF, ARM64, 0, 2, 1, 24, 0, 0)
                             + struct.pack("<IIIIII", 0x32, 24, 1, 0x000D0500, 0x000F0000, 0))
            self.assertEqual(macho_minimum_macos(path), (13, 5, 0))
            self.assertEqual(verify_architectures(Path(folder), (13, 5, 0)), 1)
            with self.assertRaisesRegex(ValueError, "Requires newer macOS"):
                verify_architectures(Path(folder), (13, 0, 0))

    def test_ios_arm64_binary_is_not_a_macos_dependency(self):
        with temporary_directory() as folder:
            path = Path(folder) / "library"
            path.write_bytes(struct.pack("<IIIIIIII", 0xFEEDFACF, ARM64, 0, 2, 1, 24, 0, 0)
                             + struct.pack("<IIIIII", 0x32, 24, 2, 0x000D0000, 0x000F0000, 0))
            with self.assertRaisesRegex(ValueError, "Non-macOS"):
                verify_architectures(Path(folder), (13, 5, 0))

    def test_nested_non_arm64_library_rejects_candidate(self):
        with temporary_directory() as folder:
            app = Path(folder) / "Dotii.app"
            library = app / "Contents/Resources/runtime/nested/library.dylib"
            library.parent.mkdir(parents=True)
            library.write_bytes(struct.pack("<II", 0xFEEDFACF, ARM64))
            self.assertEqual(verify_architectures(app), 1)
            library.write_bytes(struct.pack("<II", 0xFEEDFACF, 0x01000007))
            with self.assertRaisesRegex(ValueError, "Expected arm64"):
                verify_architectures(app)

    def test_universal_headers_are_not_mistaken_for_thin_arm64(self):
        with temporary_directory() as folder:
            path = Path(folder) / "universal"
            path.write_bytes(struct.pack(">II", 0xCAFEBABE, 2)
                             + struct.pack(">IIIII", ARM64, 0, 0, 0, 0)
                             + struct.pack(">IIIII", 0x01000007, 0, 0, 0, 0))
            self.assertEqual(macho_cpus(path), {ARM64, 0x01000007})
            with self.assertRaisesRegex(ValueError, "Expected arm64"):
                verify_architectures(Path(folder))

    def test_truncated_headers_and_external_symlinks_fail(self):
        with temporary_directory() as folder:
            app = Path(folder) / "app"
            app.mkdir()
            path = app / "library"
            path.write_bytes(b"\xcf\xfa\xed\xfe")
            with self.assertRaisesRegex(ValueError, "Truncated"):
                verify_architectures(app)
            path.unlink()
            if sys.platform != "win32":
                path.symlink_to(Path(folder) / "outside")
                with self.assertRaisesRegex(ValueError, "symlink"):
                    verify_architectures(app)

    def test_stale_or_missing_resources_fail(self):
        with temporary_directory() as folder:
            source, bundled = Path(folder) / "source", Path(folder) / "bundled"
            source.write_bytes(b"current firmware or web resource")
            with self.assertRaises(ValueError):
                same_file(source, bundled)
            bundled.write_bytes(b"previous version")
            with self.assertRaises(ValueError):
                same_file(source, bundled)
            bundled.write_bytes(source.read_bytes())
            same_file(source, bundled)
