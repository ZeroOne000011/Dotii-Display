# PyInstaller one-file build for the Dotii tray host.
# Run from the project root; the build script does this automatically.

from pathlib import Path
import os


ROOT = Path.cwd().resolve()
BRIDGE = ROOT / "bridge"
VERSION_FILE = os.environ.get("DOTII_VERSION_FILE")

a = Analysis(
    [str(BRIDGE / "bridge_app.py")],
    pathex=[str(BRIDGE)],
    binaries=[],
    datas=[(str(BRIDGE / "assets" / "dotii.ico"), "assets")],
    hiddenimports=[],
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
    name="DotiiManagementCenter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(BRIDGE / "assets" / "dotii.ico"),
    version=VERSION_FILE,
)
