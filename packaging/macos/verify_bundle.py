"""Reject incomplete or mixed-architecture macOS candidates before signing."""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import struct
import subprocess
from pathlib import Path


ARM64 = 0x0100000C
THIN_MAGICS = {b"\xfe\xed\xfa\xce": ">", b"\xce\xfa\xed\xfe": "<",
               b"\xfe\xed\xfa\xcf": ">", b"\xcf\xfa\xed\xfe": "<"}
FAT_MAGICS = {b"\xca\xfe\xba\xbe": (">", 20), b"\xbe\xba\xfe\xca": ("<", 20),
              b"\xca\xfe\xba\xbf": (">", 32), b"\xbf\xba\xfe\xca": ("<", 32)}


def macho_cpus(path: Path) -> set[int]:
    """Read both thin and Universal Mach-O headers without executing a file."""
    with path.open("rb") as stream:
        magic = stream.read(4)
        if magic in THIN_MAGICS:
            header = stream.read(4)
            if len(header) != 4:
                raise ValueError(f"Truncated Mach-O header: {path.name}")
            return {struct.unpack(THIN_MAGICS[magic] + "I", header)[0]}
        if magic not in FAT_MAGICS:
            return set()
        endian, entry_size = FAT_MAGICS[magic]
        count_bytes = stream.read(4)
        if len(count_bytes) != 4:
            raise ValueError(f"Truncated Universal header: {path.name}")
        count = struct.unpack(endian + "I", count_bytes)[0]
        if not 0 < count <= 32:
            raise ValueError(f"Invalid Universal architecture count: {path.name}")
        cpus = set()
        for _ in range(count):
            entry = stream.read(entry_size)
            if len(entry) != entry_size:
                raise ValueError(f"Truncated Universal architecture: {path.name}")
            cpus.add(struct.unpack_from(endian + "I", entry)[0])
        return cpus


def macho_minimum_macos(path: Path) -> tuple[int, int, int]:
    with path.open("rb") as stream:
        magic = stream.read(4)
        if magic not in THIN_MAGICS:
            raise ValueError(f"Expected thin Mach-O: {path.name}")
        endian = THIN_MAGICS[magic]
        stream.seek(16)
        count_bytes = stream.read(4)
        if len(count_bytes) != 4:
            raise ValueError(f"Truncated Mach-O header: {path.name}")
        count = struct.unpack(endian + "I", count_bytes)[0]
        if count > 65536:
            raise ValueError(f"Invalid Mach-O load command count: {path.name}")
        stream.seek(32 if magic in (b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe") else 28)
        versions = []
        for _ in range(count):
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError(f"Truncated Mach-O load command: {path.name}")
            command, size = struct.unpack(endian + "II", header)
            if not 8 <= size <= 2 * 1024 * 1024:
                raise ValueError(f"Invalid Mach-O load command size: {path.name}")
            body = stream.read(size - 8)
            if len(body) != size - 8:
                raise ValueError(f"Truncated Mach-O load command body: {path.name}")
            if command == 0x32 and len(body) >= 16:  # LC_BUILD_VERSION
                platform, version = struct.unpack_from(endian + "II", body)
                if platform != 1:
                    raise ValueError(f"Non-macOS executable: {path.name}")
                versions.append(version)
            elif command == 0x24 and len(body) >= 8:  # LC_VERSION_MIN_MACOSX
                versions.append(struct.unpack_from(endian + "I", body)[0])
        if not versions:
            raise ValueError(f"Missing minimum macOS version: {path.name}")
        version = max(versions)
        return version >> 16, (version >> 8) & 255, version & 255


def verify_architectures(app: Path, maximum_macos: tuple[int, int, int] | None = None) -> int:
    count = 0
    root = app.resolve()
    for path in sorted(app.rglob("*")):
        if path.is_symlink():
            if not path.resolve().is_relative_to(root) or not path.exists():
                raise ValueError(f"Invalid bundle symlink: {path.relative_to(app)}")
            continue
        if path.is_file():
            cpus = macho_cpus(path)
            if cpus:
                count += 1
                if cpus != {ARM64}:
                    raise ValueError(f"Expected arm64 Mach-O: {path.relative_to(app)}")
                if maximum_macos and macho_minimum_macos(path) > maximum_macos:
                    raise ValueError(f"Requires newer macOS than declared: {path.relative_to(app)}")
    if not count:
        raise ValueError("No Mach-O files found in application")
    return count


def same_file(source: Path, bundled: Path) -> None:
    if not bundled.is_file() or source.read_bytes() != bundled.read_bytes():
        raise ValueError(f"Missing or mismatched bundled resource: {source.name}")


def verify_bundle(app: Path, source: Path) -> int:
    contents = app / "Contents"
    resources = contents / "Resources"
    runtime = resources / "DotiiBridgeRuntime" / "_internal"
    version = re.search(r'PROJECT_VER\s+"([^"]+)"', (source / "CMakeLists.txt").read_text())[1]
    with (contents / "Info.plist").open("rb") as stream:
        metadata = plistlib.load(stream)
    if any(metadata.get(key) != version for key in ("CFBundleVersion", "CFBundleShortVersionString")):
        raise ValueError("App version does not match source")
    for relative in ("MacOS/DotiiManagementCenter", "Resources/DotiiBridgeRuntime/DotiiBridge",
                     "Resources/tools/node/bin/node", "Resources/tools/ffmpeg/bin/ffmpeg"):
        if macho_cpus(contents / relative) != {ARM64}:
            raise ValueError(f"Missing arm64 executable: {relative}")
    for path in (source / "bridge" / "web").rglob("*"):
        if path.is_file():
            same_file(path, runtime / "web" / path.relative_to(source / "bridge" / "web"))
    firmware = source / "firmware"
    same_file(firmware / "flasher_args.json", runtime / "firmware" / "flasher_args.json")
    for relative in json.loads((firmware / "flasher_args.json").read_text())["flash_files"].values():
        if not (firmware / relative).resolve().is_relative_to(firmware.resolve()):
            raise ValueError("Firmware manifest escapes resource directory")
        same_file(firmware / relative, runtime / "firmware" / relative)
    tools = resources / "tools"
    expected = json.loads((source / "packaging/macos/tools-package.json").read_text())["dependencies"]["@openai/codex"]
    package = tools / "codex-cli/node_modules/@openai/codex"
    if json.loads((package / "package.json").read_text())["version"] != expected:
        raise ValueError("Bundled Codex version does not match fixed dependency")
    for relative in ("Node.js-LICENSE", "OpenAI-Codex-LICENSE", "FFmpeg-COPYING.LGPLv2.1"):
        if not (tools / "licenses" / relative).is_file():
            raise ValueError(f"Missing tool license: {relative}")
    node = tools / "node/bin/node"
    commands = [([str(node), "--version"], "v24.16.0"),
                ([str(node), str(package / "bin/codex.js"), "--version"], f"codex-cli {expected}")]
    for command, expected_output in commands:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=True)
        if result.stdout.strip() != expected_output:
            raise ValueError("Bundled tool version check failed")
    ffmpeg = subprocess.run([str(tools / "ffmpeg/bin/ffmpeg"), "-hide_banner", "-version"],
                            capture_output=True, text=True, timeout=20, check=True).stdout
    if "ffmpeg version 8.1.2" not in ffmpeg or "--disable-gpl" not in ffmpeg or "--disable-nonfree" not in ffmpeg:
        raise ValueError("Expected fixed LGPL-only FFmpeg build")
    minimum = tuple(int(part) for part in metadata["LSMinimumSystemVersion"].split("."))
    return verify_architectures(app, (minimum + (0, 0, 0))[:3])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    print(f"Bundle verified: {verify_bundle(args.app.resolve(), args.source.resolve())} arm64 Mach-O files")
