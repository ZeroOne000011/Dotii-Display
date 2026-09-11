#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DOWNLOADS="$ROOT/.codx/macos-python-downloads"
OUTPUT="$ROOT/.codx/macos-python"
ARCHIVE="cpython-3.12.14+20260901-aarch64-apple-darwin-install_only_stripped.tar.gz"
SHA256="81a359f1cfadd4da11766534c5913791cea55f26e1bb902cacd2a531bb1e4b2b"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-aarch64-apple-darwin-install_only_stripped.tar.gz"

[[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] || {
  echo "This script requires Apple Silicon macOS" >&2
  exit 1
}

mkdir -p "$DOWNLOADS"
curl --fail --location --retry 3 "$URL" --output "$DOWNLOADS/$ARCHIVE"
ACTUAL="$(shasum -a 256 "$DOWNLOADS/$ARCHIVE" | awk '{print $1}')"
[[ "$ACTUAL" == "$SHA256" ]] || { echo "Python SHA-256 verification failed" >&2; exit 1; }

rm -rf "$OUTPUT"
tar -xzf "$DOWNLOADS/$ARCHIVE" -C "$ROOT/.codx"
/bin/mv "$ROOT/.codx/python" "$OUTPUT"
export PIP_CACHE_DIR="$ROOT/.codx/pip-cache"
"$OUTPUT/bin/python3" -m pip install \
  --requirement "$ROOT/packaging/macos/requirements.txt" \
  --disable-pip-version-check
"$OUTPUT/bin/python3" -m unittest discover -s "$ROOT/bridge/tests"
"$OUTPUT/bin/python3" -m compileall -q "$ROOT/bridge"
[[ "$(lipo -archs "$OUTPUT/bin/python3")" == "arm64" ]]
echo "$OUTPUT"
