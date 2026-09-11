#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON_ROOT="$ROOT/.codx/macos-python"
if [[ ! -x "$PYTHON_ROOT/bin/python3" ]]; then
  PYTHON_ROOT="$ROOT/.codx/macos-env"
fi
PYTHON="$PYTHON_ROOT/bin/python3"
PYINSTALLER="$PYTHON_ROOT/bin/pyinstaller"
WORK="$ROOT/.codx/macos-build"
OUTPUT="$ROOT/.codx/macos-output"
DERIVED="$WORK/DerivedData"
TOOLS_SOURCE=""
SIGN_IDENTITY="-"
NOTARY_PROFILE=""
CREATE_DMG=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tools-dir) TOOLS_SOURCE="$2"; shift 2 ;;
    --sign) SIGN_IDENTITY="$2"; shift 2 ;;
    --notary-profile) NOTARY_PROFILE="$2"; shift 2 ;;
    --dmg) CREATE_DMG=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

[[ -x "$PYTHON" && -x "$PYINSTALLER" ]] || {
  echo "Missing .codx/macos-python or fallback .codx/macos-env build environment" >&2
  exit 1
}

VERSION="$($PYTHON -c 'import re,pathlib; text=pathlib.Path("CMakeLists.txt").read_text(); print(re.search(r"PROJECT_VER\s+\"([^\"]+)", text).group(1))')"
PLIST_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$ROOT/packaging/macos/Info.plist")"
[[ "$VERSION" == "$PLIST_VERSION" ]] || {
  echo "Version mismatch: CMake=$VERSION Info.plist=$PLIST_VERSION" >&2
  exit 1
}

rm -rf "$WORK" "$OUTPUT"
mkdir -p "$WORK/pyinstaller-dist" "$WORK/pyinstaller-work" "$OUTPUT"
export PYINSTALLER_CONFIG_DIR="$WORK/pyinstaller-cache"

cd "$ROOT"
"$PYINSTALLER" --noconfirm --clean \
  --distpath "$WORK/pyinstaller-dist" \
  --workpath "$WORK/pyinstaller-work" \
  "$ROOT/packaging/macos/DotiiBridge.spec"

xcodebuild \
  -project "$ROOT/macos/DotiiManagementCenter/DotiiManagementCenter.xcodeproj" \
  -scheme DotiiManagementCenter \
  -configuration Release \
  -derivedDataPath "$DERIVED" \
  ARCHS=arm64 ONLY_ACTIVE_ARCH=YES CODE_SIGNING_ALLOWED=NO \
  build

SOURCE_APP="$DERIVED/Build/Products/Release/DotiiManagementCenter.app"
APP="$OUTPUT/DotiiManagementCenter-$VERSION.app"
/usr/bin/ditto "$SOURCE_APP" "$APP"
/bin/mkdir -p "$APP/Contents/Resources"
/usr/bin/ditto "$WORK/pyinstaller-dist/DotiiBridge" "$APP/Contents/Resources/DotiiBridgeRuntime"
/bin/chmod 755 "$APP/Contents/Resources/DotiiBridgeRuntime/DotiiBridge"

BUNDLED_WEB="$APP/Contents/Resources/DotiiBridgeRuntime/_internal/web"
for asset in index.html app.js styles.css markdown.js; do
  /usr/bin/cmp -s "$ROOT/bridge/web/$asset" "$BUNDLED_WEB/$asset" || {
    echo "Bundled web asset mismatch: $asset" >&2
    exit 1
  }
done

if [[ -n "$TOOLS_SOURCE" ]]; then
  [[ -d "$TOOLS_SOURCE" ]] || { echo "Tools directory not found: $TOOLS_SOURCE" >&2; exit 1; }
  mkdir -p "$APP/Contents/Resources/tools"
  /usr/bin/ditto "$TOOLS_SOURCE" "$APP/Contents/Resources/tools"
fi

"$PYTHON" "$ROOT/packaging/macos/collect_python_licenses.py" "$APP/Contents/Resources/licenses"

/usr/bin/strip -S "$APP/Contents/MacOS/DotiiManagementCenter"

SIGN_ARGS=(--force --sign "$SIGN_IDENTITY")
if [[ "$SIGN_IDENTITY" != "-" ]]; then
  SIGN_ARGS+=(--options runtime --timestamp)
fi
/usr/bin/codesign "${SIGN_ARGS[@]}" "$APP/Contents/Resources/DotiiBridgeRuntime/DotiiBridge"
/usr/bin/codesign --deep "${SIGN_ARGS[@]}" "$APP"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP"

HOST_ARCH="$(/usr/bin/lipo -archs "$APP/Contents/MacOS/DotiiManagementCenter")"
BRIDGE_ARCH="$(/usr/bin/lipo -archs "$APP/Contents/Resources/DotiiBridgeRuntime/DotiiBridge")"
[[ "$HOST_ARCH" == "arm64" && "$BRIDGE_ARCH" == "arm64" ]] || {
  echo "Unexpected architecture: host=$HOST_ARCH bridge=$BRIDGE_ARCH" >&2
  exit 1
}

if [[ -n "$NOTARY_PROFILE" ]]; then
  [[ "$SIGN_IDENTITY" != "-" ]] || { echo "Notarization requires a Developer ID signature" >&2; exit 1; }
  ZIP="$WORK/DotiiManagementCenter-$VERSION-notarization.zip"
  /usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"
  xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$APP"
  /usr/bin/codesign --verify --deep --strict --verbose=2 "$APP"
  /usr/sbin/spctl --assess --type execute --verbose=2 "$APP"
fi

if [[ "$CREATE_DMG" -eq 1 ]]; then
  DMG="$OUTPUT/DotiiManagementCenter-macOS-arm64-$VERSION.dmg"
  DMG_ROOT="$WORK/dmg-root"
  mkdir -p "$DMG_ROOT"
  /usr/bin/ditto "$APP" "$DMG_ROOT/$(basename "$APP")"
  /bin/ln -s /Applications "$DMG_ROOT/Applications"
  hdiutil create -volname "Dotii 管理中心" -srcfolder "$DMG_ROOT" -ov -format UDZO "$DMG"
  (
    cd "$OUTPUT"
    shasum -a 256 "$(basename "$DMG")" > "$(basename "$DMG").sha256"
  )
fi

echo "$APP"
