#!/bin/bash
#
# Install AG CuteMusic on macOS, as AG CuteSync's macos/install.sh:
#
#   bash macos/install.sh
#
# Creates a virtualenv with PyQt6, mutagen and py2app that also sees Homebrew's python
# packages (gi, for GStreamer), builds the app bundle (macos/make-app.sh, alias mode: it runs
# the code of this checkout, so edits apply on the next launch) into ~/Applications, and adds an
# `ag-cute-music` command that starts it. Re-run it after moving this checkout.
#
# Needs: brew install python gstreamer pygobject3
#
# Environment overrides:
#   VENV=<dir>      virtualenv location (default ~/work/ag-cutemusic-env)
#   PYTHON=<exe>    python to build the venv with (default: Homebrew's python3)
#   APPDIR=<dir>    where the app goes (default ~/Applications)
#   BINDIR=<dir>    where the `ag-cute-music` command goes (default ~/.local/bin)

set -e

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
BREW_PYTHON=$( (brew --prefix 2>/dev/null || echo /opt/homebrew) | sed 's|$|/bin/python3|')
PYTHON="${PYTHON:-$BREW_PYTHON}"
VENV="${VENV:-$HOME/work/ag-cutemusic-env}"
APPDIR="${APPDIR:-$HOME/Applications}"
BINDIR="${BINDIR:-$HOME/.local/bin}"

if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2> /dev/null; then
    echo
    echo "Python 3.10 or newer is required (found: $("$PYTHON" -V 2>&1))."
    echo "brew install python, or set PYTHON=/path/to/python3."
    echo
    exit 1
fi
if ! "$PYTHON" -c "import gi; gi.require_version('Gst', '1.0'); from gi.repository import Gst" 2> /dev/null; then
    echo
    echo "$PYTHON has no GStreamer (gi): brew install gstreamer pygobject3"
    echo
    exit 1
fi

if [ ! -x "$VENV/bin/python3" ]; then
    echo "Creating virtualenv in $VENV..."
    mkdir -p "$(dirname "$VENV")"
    "$PYTHON" -m venv --system-site-packages "$VENV"
else
    echo "Reusing virtualenv in $VENV"
fi

"$VENV/bin/python3" -m pip install --upgrade pip
"$VENV/bin/python3" -m pip install --upgrade -r "$HERE/requirements.txt"
"$VENV/bin/python3" -c 'from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR; print("  ok       PyQt6 %s (Qt %s)" % (PYQT_VERSION_STR, QT_VERSION_STR))'
"$VENV/bin/python3" -c "import gi; gi.require_version('Gst', '1.0'); from gi.repository import Gst; Gst.init([]); print('  ok       ' + Gst.version_string())"

# --- App bundle: the Dock and the menu bar show "AG CuteMusic", not "Python"
PATH="$VENV/bin:$PATH" sh "$HERE/make-app.sh" "$@"
APP_SRC=$(ls -d "$REPO"/dist/*.app | head -1)
APP="$APPDIR/$(basename "$APP_SRC")"
mkdir -p "$APPDIR"
rm -rf "$APP"
mv "$APP_SRC" "$APP"
rmdir "$REPO/dist" 2> /dev/null || true
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "$APP" 2> /dev/null || true

# --- Command line: start the bundle's executable directly, so it still shows its own name
EXE="$APP/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")"
mkdir -p "$BINDIR"
cat > "$BINDIR/ag-cute-music" <<LAUNCHER
#!/bin/sh
exec "$EXE" "\$@"
LAUNCHER
chmod +x "$BINDIR/ag-cute-music"

echo
echo "AG CuteMusic installed: $APP"
echo "Start it from Launchpad/Finder, or:  ag-cute-music"
case ":$PATH:" in
    *":$BINDIR:"*) ;;
    *) echo "($BINDIR is not in PATH; add it, or run $BINDIR/ag-cute-music)";;
esac
echo "To keep it in the Dock: start it, right-click its Dock icon > Options > Keep in Dock"
echo
