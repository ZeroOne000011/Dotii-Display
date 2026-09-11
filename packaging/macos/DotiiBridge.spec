# PyInstaller one-folder build for the arm64 macOS Dotii backend.

from pathlib import Path
import json

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


ROOT = Path.cwd().resolve()
BRIDGE = ROOT / "bridge"
firmware_root = ROOT / "firmware"
firmware_manifest = firmware_root / "flasher_args.json"
if not firmware_manifest.is_file():
    raise SystemExit("firmware/flasher_args.json is missing")
firmware_payload = json.loads(firmware_manifest.read_text(encoding="utf-8"))
firmware_files = firmware_payload.get("flash_files")
if not isinstance(firmware_files, dict) or not firmware_files:
    raise SystemExit("firmware/flasher_args.json does not contain flash_files")

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
    relative = source.relative_to(firmware_root.resolve())
    if not source.is_file():
        raise SystemExit(f"Firmware image is missing: {relative_name}")
    datas.append((str(source), str(Path("firmware") / relative.parent)))

a = Analysis(
    [str(BRIDGE / "codex_bridge.py")],
    pathex=[str(BRIDGE)],
    binaries=[],
    datas=datas,
    hiddenimports=[*collect_submodules("bleak"), *collect_submodules("esptool")],
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
    [],
    exclude_binaries=True,
    name="DotiiBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DotiiBridge",
)
