#!/bin/sh
# Build the macOS app bundle in dist/, as AG CuteSync's macos/make-app.sh, in py2app's alias
# mode: the bundle runs the cutemusic package from this checkout and PyQt6 and GStreamer from the
# venv (edits take effect on the next launch); not usable on another Mac.
#
#   macos/make-app.sh
#
# No standalone bundle: GStreamer and its plugins come from Homebrew (gi), which py2app does
# not carry. Needs py2app, PyQt6 and gi (GStreamer) in the same python3 (macos/install.sh).
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
APP_ID=$(sed -n "s/^APP_ID = '\(.*\)'/\1/p" cutemusic/__init__.py)
ICON="$ROOT/icons/$APP_ID.svg"
case "$1" in
    ''|--alias|-A) ;;
    --full) echo "no standalone bundle: GStreamer comes from Homebrew; building the alias bundle" >&2;;
    *) echo "usage: $0" >&2; exit 2;;
esac
python3 -c 'import py2app' 2>/dev/null || { echo "py2app missing: pip3 install py2app" >&2; exit 1; }
python3 -c 'import PyQt6.QtWidgets' 2>/dev/null || { echo "PyQt6 missing: pip3 install PyQt6" >&2; exit 1; }
python3 -c "import gi; gi.require_version('Gst', '1.0'); from gi.repository import Gst" 2>/dev/null || {
    echo "GStreamer (gi) missing: brew install gstreamer pygobject3, and a venv with --system-site-packages (macos/install.sh)" >&2; exit 1; }
mkdir -p build
python3 macos/make_icns.py meters build/app.icns       # the VU meters icon (cutemusic/appicon.py), as the Dock shows it
rm -rf build/bdist* build/py2app dist
LOG=build/py2app.log
echo "running py2app (alias mode), log in $LOG"
if ! APP_ICNS="$ROOT/build/app.icns" \
        python3 macos/setup.py py2app -A --dist-dir dist --bdist-base build/py2app >"$LOG" 2>&1; then
    tail -40 "$LOG" >&2
    echo "py2app failed, full log in $LOG" >&2
    exit 1
fi
APP=$(ls -d dist/*.app)
echo "built $APP (alias mode, uses $(python3 -c 'import sys; print(sys.prefix)'))"
echo "install: mv \"$APP\" /Applications/   then start it and right-click its Dock icon > Options > Keep in Dock"
