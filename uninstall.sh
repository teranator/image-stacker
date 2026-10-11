#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="${IMAGE_STACKER_INSTALL_ROOT:-$HOME/.local}"
rm -rf "$INSTALL_ROOT/share/image-stacker"
rm -f "$INSTALL_ROOT/bin/image-stacker"
rm -f "$INSTALL_ROOT/share/applications/image-stacker.desktop"
echo "Uninstalled Image Stacker."
