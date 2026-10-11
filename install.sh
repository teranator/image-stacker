#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="${IMAGE_STACKER_INSTALL_ROOT:-$HOME/.local}"
APP_DIR="$INSTALL_ROOT/share/image-stacker"
BIN_DIR="$INSTALL_ROOT/bin"
APP_DIRS="$INSTALL_ROOT/share/applications"

if ! python3 -c 'import PyQt6' >/dev/null 2>&1; then
  echo "PyQt6 is required. On Arch Linux install it with: sudo pacman -S python-pyqt6" >&2
  exit 1
fi

mkdir -p "$APP_DIR" "$BIN_DIR" "$APP_DIRS"
mkdir -p "$APP_DIR/image_stacker"
install -m 644 "$PROJECT_DIR/src/image_stacker/__init__.py" "$APP_DIR/image_stacker/__init__.py"
install -m 644 "$PROJECT_DIR/src/image_stacker/__main__.py" "$APP_DIR/image_stacker/__main__.py"
install -m 644 "$PROJECT_DIR/src/image_stacker/main.py" "$APP_DIR/image_stacker/main.py"
cat > "$BIN_DIR/image-stacker" <<EOF
#!/usr/bin/env bash
exec python3 "$APP_DIR/image_stacker/main.py" "\$@"
EOF
chmod 755 "$BIN_DIR/image-stacker"

cat > "$APP_DIRS/image-stacker.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Image Stacker
Comment=Stack clipboard images vertically
Exec=$BIN_DIR/image-stacker
Icon=applications-graphics
Terminal=false
Categories=Graphics;Utility;
StartupNotify=true
EOF
chmod 644 "$APP_DIRS/image-stacker.desktop"

echo "Installed Image Stacker. Launch with: $BIN_DIR/image-stacker"
