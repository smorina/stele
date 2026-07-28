#!/usr/bin/env bash
#
# Build and smoke-test the native-architecture portable Stele.app bundle.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${STELE_DIST_DIR:-$ROOT/dist}"
WORK_DIR="${STELE_BUILD_DIR:-$ROOT/build/pyinstaller}"
ARCH="$(uname -m)"

[[ "$(uname -s)" == "Darwin" ]] || {
  echo "error: the macOS app must be built on macOS" >&2
  exit 1
}

case "$ARCH" in
  arm64)
    DEFAULT_MINIMUM="13.0"
    ;;
  x86_64)
    DEFAULT_MINIMUM="14.0"
    ;;
  *)
    echo "error: unsupported macOS architecture: $ARCH" >&2
    exit 1
    ;;
esac

export STELE_MACOS_MIN_VERSION="${STELE_MACOS_MIN_VERSION:-$DEFAULT_MINIMUM}"
export MACOSX_DEPLOYMENT_TARGET="$STELE_MACOS_MIN_VERSION"
export PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-$WORK_DIR/config}"

rm -rf "$DIST_DIR/Stele" "$DIST_DIR/Stele.app" "$WORK_DIR"
mkdir -p "$DIST_DIR" "$WORK_DIR" "$PYINSTALLER_CONFIG_DIR"

uv run --frozen pyinstaller \
  --noconfirm \
  --clean \
  --distpath "$DIST_DIR" \
  --workpath "$WORK_DIR" \
  "$ROOT/packaging/stele-macos.spec"

APP="$DIST_DIR/Stele.app"
EXECUTABLE="$APP/Contents/MacOS/Stele"
[[ -x "$EXECUTABLE" ]] || {
  echo "error: PyInstaller did not create $EXECUTABLE" >&2
  exit 1
}

if ! file "$EXECUTABLE" | grep -q "$ARCH"; then
  echo "error: bundled executable is not for $ARCH: $(file "$EXECUTABLE")" >&2
  exit 1
fi

SMOKE_HOME="$(mktemp -d "${TMPDIR:-/tmp}/stele-app-smoke.XXXXXX")"
cleanup() {
  rm -rf "$SMOKE_HOME"
}
trap cleanup EXIT

uv run --frozen python \
  "$ROOT/packaging/smoke-packaged-app.py" \
  "$EXECUTABLE" \
  "$SMOKE_HOME"

codesign --verify --deep --strict "$APP"
echo "Built portable $ARCH app: $APP"
