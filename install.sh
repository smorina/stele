#!/bin/sh
# Install Stele from an extracted GitHub release. No system Python or admin rights.
set -eu

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -f "$SOURCE_DIR/uv.lock" ] || [ ! -d "$SOURCE_DIR/src/stele" ]; then
  echo "This installer must stay beside the extracted Stele release files." >&2
  exit 1
fi

OS_NAME=$(uname -s)
ARCH_NAME=$(uname -m)
case "$OS_NAME:$ARCH_NAME" in
  Darwin:arm64|Darwin:x86_64|Linux:x86_64) ;;
  Linux:aarch64|Linux:arm64)
    echo "Stele cannot install on Linux ARM: gdstk has no compatible binary wheel." >&2
    exit 1
    ;;
  *)
    echo "Unsupported platform: $OS_NAME $ARCH_NAME" >&2
    exit 1
    ;;
esac

if [ "$OS_NAME" = "Darwin" ]; then
  MAC_MAJOR=$(sw_vers -productVersion | cut -d. -f1)
  if [ "$ARCH_NAME" = "arm64" ] && [ "$MAC_MAJOR" -lt 13 ]; then
    echo "Stele on Apple Silicon requires macOS 13 or newer." >&2
    exit 1
  fi
  if [ "$ARCH_NAME" = "x86_64" ] && [ "$MAC_MAJOR" -lt 14 ]; then
    echo "Stele on Intel requires macOS 14 or newer." >&2
    exit 1
  fi
fi

STELE_ROOT=${STELE_HOME:-"$HOME/.stele"}
APP_DIR="$STELE_ROOT/app"
STAGE_DIR="$STELE_ROOT/app.new"
OLD_DIR="$STELE_ROOT/app.old"
mkdir -p "$STELE_ROOT"

if command -v uv >/dev/null 2>&1; then
  UV_BIN=$(command -v uv)
else
  echo "Installing the uv runtime (no administrator password needed)…"
  UV_INSTALLER="$STELE_ROOT/uv-installer.sh"
  curl -LsSf https://astral.sh/uv/install.sh -o "$UV_INSTALLER"
  sh "$UV_INSTALLER"
  UV_BIN="$HOME/.local/bin/uv"
fi

rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"
for item in src assets profiles pyproject.toml uv.lock README.md LICENSE; do
  cp -R "$SOURCE_DIR/$item" "$STAGE_DIR/"
done

rm -rf "$OLD_DIR"
if [ -d "$APP_DIR" ]; then
  mv "$APP_DIR" "$OLD_DIR"
fi
mv "$STAGE_DIR" "$APP_DIR"

if ! (
  cd "$APP_DIR"
  "$UV_BIN" sync --frozen --no-dev
  STELE_HOME="$STELE_ROOT" .venv/bin/stele doctor
); then
  echo "Installation check failed; restoring the previous Stele app." >&2
  rm -rf "$APP_DIR"
  if [ -d "$OLD_DIR" ]; then
    mv "$OLD_DIR" "$APP_DIR"
  fi
  exit 1
fi

case "$OS_NAME" in
  Darwin)
    LAUNCHER_DIR="$HOME/Applications"
    LAUNCHER="$LAUNCHER_DIR/Stele.command"
    mkdir -p "$LAUNCHER_DIR"
    {
      echo '#!/bin/sh'
      printf 'exec "%s/.venv/bin/stele" ui\n' "$APP_DIR"
    } > "$LAUNCHER"
    chmod +x "$LAUNCHER"
    ;;
  Linux)
    LAUNCHER_DIR="$HOME/.local/share/applications"
    LAUNCHER="$LAUNCHER_DIR/stele.desktop"
    mkdir -p "$LAUNCHER_DIR"
    {
      echo '[Desktop Entry]'
      echo 'Type=Application'
      echo 'Name=Stele'
      echo 'Comment=PDF to verified photomask'
      printf 'Exec=%s/.venv/bin/stele ui\n' "$APP_DIR"
      echo 'Terminal=false'
      echo 'Categories=Graphics;Utility;'
    } > "$LAUNCHER"
    chmod +x "$LAUNCHER"
    ;;
esac

echo
echo "Stele is installed."
echo "Launcher: $LAUNCHER"
echo "Your documents and completed runs stay in: $STELE_ROOT/runs"
