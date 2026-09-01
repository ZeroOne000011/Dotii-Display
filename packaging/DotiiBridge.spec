# PyInstaller one-file build for the Dotii data bridge.
# Run from the project root; the build script does this automatically.

from pathlib import Path
import os
import json

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


ROOT = Path.cwd().resolve()
BRIDGE = ROOT / "bridge"
VERSION_FILE = os.environ.get("DOTII_VERSION_FILE")

firmware_root = ROOT / "firmware"
firmware_manifest = firmware_root / "flasher_args.json"
if not firmware_manifest.is_file():
    raise SystemExit("Prepare the shared firmware bundle before packaging: firmware/flasher_args.json is missing.")
firmware_payload = json.loads(firmware_manifest.read_text(encoding="utf-8"))
firmware_files = firmware_payload.get("flash_files")
if not isinstance(firmware_files, dict) or not firmware_files:
    raise SystemExit("firmware/flasher_args.json does not contain flash_files.")

datas = [
    (str(BRIDGE / "web"), "web"),
    (str(BRIDGE / "assets"), "assets"),
    (str(BRIDGE / "state.json"), "."),
    (str(firmware_manifest), "firmware"),
    (str(ROOT / "CMakeLists.txt"), "."),
    *collect_data_files("esptool"),
]
for relative_name in firmware_files.values():
    source = (firmware_root / str(relative_name)).resolve()
    try:
        relative = source.relative_to(firmware_root.resolve())
    except ValueError as error:
        raise SystemExit(f"Firmware image escapes build directory: {relative_name}") from error
    if not source.is_file():
        raise SystemExit(f"Firmware image is missing: {relative_name}")
    destination = Path("firmware") / relative.parent
    datas.append((str(source), str(destination)))
hiddenimports = [
    *collect_submodules("bleak"),
    *collect_submodules("esptool"),
]

a = Analysis(
    [str(BRIDGE / "codex_bridge.py")],
    pathex=[str(BRIDGE)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DotiiBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # The host always starts this backend with CREATE_NO_WINDOW. Keeping the
    # console bootloader here lets its --esptool child stream progress/errors
    # back to the management center instead of showing a PyInstaller dialog.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(BRIDGE / "assets" / "dotii.ico"),
    version=VERSION_FILE,
)
