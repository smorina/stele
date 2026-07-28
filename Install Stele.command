#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
"$SCRIPT_DIR/install.sh"
echo
echo "Press Return to open Stele."
read -r _
exec "$HOME/.stele/app/.venv/bin/stele" ui
