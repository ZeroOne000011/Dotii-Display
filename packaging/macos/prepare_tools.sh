#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DOWNLOADS="$ROOT/.codx/macos-tools-downloads"
BUILD="$ROOT/.codx/macos-tools-build"
OUTPUT="$ROOT/.codx/macos-tools"
NODE_VERSION="24.16.0"
FFMPEG_VERSION="8.1.2"
FFMPEG_SHA256="464beb5e7bf0c311e68b45ae2f04e9cc2af88851abb4082231742a74d97b524c"
CODEX_LICENSE_SHA256="d17f227e4df5da1600391338865ce0f3055211760a36688f816941d58232d8dc"
NODE_ARCHIVE="node-v$NODE_VERSION-darwin-arm64.tar.xz"
FFMPEG_ARCHIVE="ffmpeg-$FFMPEG_VERSION.tar.xz"

[[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] || {
  echo "This script requires Apple Silicon macOS" >&2
  exit 1
}

mkdir -p "$DOWNLOADS"
rm -rf "$BUILD" "$OUTPUT"
mkdir -p "$BUILD" "$OUTPUT/licenses"

curl --fail --location --retry 3 \
  "https://nodejs.org/download/release/v$NODE_VERSION/$NODE_ARCHIVE" \
  --output "$DOWNLOADS/$NODE_ARCHIVE"
curl --fail --location --retry 3 \
  "https://nodejs.org/download/release/v$NODE_VERSION/SHASUMS256.txt" \
  --output "$DOWNLOADS/node-SHASUMS256.txt"
NODE_EXPECTED="$(awk -v name="$NODE_ARCHIVE" '$2 == name {print $1}' "$DOWNLOADS/node-SHASUMS256.txt")"
NODE_ACTUAL="$(shasum -a 256 "$DOWNLOADS/$NODE_ARCHIVE" | awk '{print $1}')"
[[ -n "$NODE_EXPECTED" && "$NODE_ACTUAL" == "$NODE_EXPECTED" ]] || {
  echo "Node.js SHA-256 verification failed" >&2
  exit 1
}

tar -xJf "$DOWNLOADS/$NODE_ARCHIVE" -C "$BUILD"
/usr/bin/ditto "$BUILD/node-v$NODE_VERSION-darwin-arm64" "$OUTPUT/node"
/bin/cp "$OUTPUT/node/LICENSE" "$OUTPUT/licenses/Node.js-LICENSE"

mkdir -p "$OUTPUT/codex-cli"
/bin/cp "$ROOT/packaging/macos/tools-package.json" "$OUTPUT/codex-cli/package.json"
"$OUTPUT/node/bin/node" "$OUTPUT/node/lib/node_modules/npm/bin/npm-cli.js" \
  install --prefix "$OUTPUT/codex-cli" --omit=dev --ignore-scripts --no-audit --no-fund
CODEX_SCRIPT="$OUTPUT/codex-cli/node_modules/@openai/codex/bin/codex.js"
[[ -f "$CODEX_SCRIPT" ]] || { echo "Codex CLI entry point is missing" >&2; exit 1; }
"$OUTPUT/node/bin/node" "$CODEX_SCRIPT" --version | grep -F "0.151.0"
CODEX_LICENSE="$OUTPUT/codex-cli/node_modules/@openai/codex/LICENSE"
if [[ -f "$CODEX_LICENSE" ]]; then
  /bin/cp "$CODEX_LICENSE" "$OUTPUT/licenses/OpenAI-Codex-LICENSE"
else
  curl --fail --location --retry 3 \
    "https://raw.githubusercontent.com/openai/codex/rust-v0.151.0/LICENSE" \
    --output "$OUTPUT/licenses/OpenAI-Codex-LICENSE"
fi
CODEX_LICENSE_ACTUAL="$(shasum -a 256 "$OUTPUT/licenses/OpenAI-Codex-LICENSE" | awk '{print $1}')"
[[ "$CODEX_LICENSE_ACTUAL" == "$CODEX_LICENSE_SHA256" ]] || {
  echo "Codex license SHA-256 verification failed" >&2
  exit 1
}

curl --fail --location --retry 3 \
  "https://ffmpeg.org/releases/$FFMPEG_ARCHIVE" \
  --output "$DOWNLOADS/$FFMPEG_ARCHIVE"
FFMPEG_ACTUAL="$(shasum -a 256 "$DOWNLOADS/$FFMPEG_ARCHIVE" | awk '{print $1}')"
[[ "$FFMPEG_ACTUAL" == "$FFMPEG_SHA256" ]] || {
  echo "FFmpeg SHA-256 verification failed" >&2
  exit 1
}
tar -xJf "$DOWNLOADS/$FFMPEG_ARCHIVE" -C "$BUILD"
FFMPEG_SOURCE="$BUILD/ffmpeg-$FFMPEG_VERSION"
FFMPEG_PREFIX="$BUILD/ffmpeg-install"
(
  cd "$FFMPEG_SOURCE"
  MACOSX_DEPLOYMENT_TARGET=13.0 ./configure \
    --prefix=/ \
    --arch=arm64 \
    --cc=clang \
    --disable-debug \
    --disable-doc \
    --disable-ffplay \
    --disable-ffprobe \
    --disable-gpl \
    --disable-nonfree \
    --disable-shared \
    --enable-static \
    --enable-ffmpeg \
    --enable-securetransport \
    --extra-cflags="-mmacosx-version-min=13.0" \
    --extra-ldflags="-mmacosx-version-min=13.0"
  make -j"$(sysctl -n hw.logicalcpu)"
  make DESTDIR="$FFMPEG_PREFIX" install
)
mkdir -p "$OUTPUT/ffmpeg/bin"
/bin/cp "$FFMPEG_PREFIX/bin/ffmpeg" "$OUTPUT/ffmpeg/bin/ffmpeg"
/bin/cp "$FFMPEG_SOURCE/COPYING.LGPLv2.1" "$OUTPUT/licenses/FFmpeg-COPYING.LGPLv2.1"

[[ "$(lipo -archs "$OUTPUT/node/bin/node")" == "arm64" ]]
[[ "$(lipo -archs "$OUTPUT/ffmpeg/bin/ffmpeg")" == "arm64" ]]
"$OUTPUT/ffmpeg/bin/ffmpeg" -hide_banner -version | grep -F "ffmpeg version $FFMPEG_VERSION"
"$OUTPUT/ffmpeg/bin/ffmpeg" -hide_banner -protocols | grep -E '^  tls$'
"$OUTPUT/ffmpeg/bin/ffmpeg" -hide_banner -demuxers | grep -E '^ D +rtsp '
"$OUTPUT/ffmpeg/bin/ffmpeg" -hide_banner -decoders | grep -E '^ V.* h264 +'
"$OUTPUT/ffmpeg/bin/ffmpeg" -hide_banner -encoders | grep -E '^ V[^ ]* mjpeg +'
for filter in fps scale crop; do
  "$OUTPUT/ffmpeg/bin/ffmpeg" -hide_banner -filters | grep -E "^ .. ${filter} +"
done

echo "$OUTPUT"
