#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PYTHON_ROOT="$ROOT/.codex/macos-python"
if [[ ! -x "$PYTHON_ROOT/bin/python3" ]]; then
  PYTHON_ROOT="$ROOT/.codex/macos-env"
fi
PYTHON="$PYTHON_ROOT/bin/python3"
PYINSTALLER="$PYTHON_ROOT/bin/pyinstaller"
WORK="$ROOT/.codex/macos-build"
OUTPUT="$ROOT/.codex/macos-output"
DERIVED="$WORK/DerivedData"
TOOLS_SOURCE="$ROOT/.codex/macos-tools"
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
  echo "Missing .codex/macos-python or fallback .codex/macos-env build environment" >&2
  exit 1
}
[[ -d "$TOOLS_SOURCE" ]] || { echo "Prepare macOS tools or pass --tools-dir" >&2; exit 1; }

VERSION="$($PYTHON -c 'import re,pathlib; text=pathlib.Path("CMakeLists.txt").read_text(); print(re.search(r"PROJECT_VER\s+\"([^\"]+)", text).group(1))')"
PLIST_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$ROOT/packaging/macos/Info.plist")"
[[ "$VERSION" == "$PLIST_VERSION" ]] || {
  echo "Version mismatch: CMake=$VERSION Info.plist=$PLIST_VERSION" >&2
  exit 1
}

rm -rf "$WORK" "$OUTPUT"
mkdir -p "$WORK/pyinstaller-dist" "$WORK/pyinstaller-work" "$OUTPUT"
# Never leave a failed or partially signed candidate in the output directory.
cleanup_failed_candidate() {
  result=$?
  if [[ "$result" -ne 0 ]]; then
    rm -rf "$OUTPUT" "$WORK/dmg-root"
  fi
}
trap cleanup_failed_candidate EXIT
export PYINSTALLER_CONFIG_DIR="$WORK/pyinstaller-cache"
export TMPDIR="$WORK/tmp"
export CLANG_MODULE_CACHE_PATH="$WORK/clang-cache"
export SWIFT_MODULECACHE_PATH="$WORK/swift-cache"
mkdir -p "$TMPDIR"

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

mkdir -p "$APP/Contents/Resources/tools"
/usr/bin/ditto "$TOOLS_SOURCE" "$APP/Contents/Resources/tools"

# Dotii only reads App Server account/thread APIs and never starts agent turns
# or shell tools. The optional Codex zsh binary targets macOS 15; omit it so
# this read-only integration keeps the Node runtime's macOS 13.5 minimum.
rm -rf "$APP/Contents/Resources/tools/codex-cli/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/codex-resources/zsh"

"$PYTHON" "$ROOT/packaging/macos/collect_python_licenses.py" "$APP/Contents/Resources/licenses"

for locale in en zh_CN; do
  mkdir -p "$APP/Contents/Resources/$locale.lproj"
  cp "$ROOT/packaging/macos/$locale.lproj/InfoPlist.strings" "$APP/Contents/Resources/$locale.lproj/InfoPlist.strings"
done

"$PYTHON" -B "$ROOT/packaging/macos/verify_bundle.py" "$APP"

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
