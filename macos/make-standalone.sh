#!/bin/bash
#
# Build a standalone AG CuteMusic.app: everything in the bundle (Python, PyQt6, GStreamer from its own Python
# wheels, no Homebrew), so it can be copied to another Mac.
#
#   macos/make-standalone.sh [--universal | --intel]
#
#   --universal   (default) arm64 + x86_64: runs natively on both; Qt's wheels (one per architecture) are merged
#                 into a universal2 one (delocate-merge)
#   --intel       an x86_64 app, built under Rosetta: runs on Intel Macs, and on Apple Silicon through Rosetta
#
# Needs Python 3.10+ from python.org (its universal2 installer: Homebrew's is one architecture only) and
# Rosetta for --intel on Apple Silicon (softwareupdate --install-rosetta). The build venv is
# build/standalone-<mode>-env, the app dist/<mode>/AG CuteMusic.app, the log build/py2app-<mode>.log.
#
# Environment overrides:
#   PYTHON=<exe>            python.org python3 to build with (default: the newest /Library/Frameworks one, 3.10+)
#   SIGN_IDENTITY=<name>    the certificate to sign with (default: the first Developer ID Application one in the
#                           keychain, macos/sign-app.sh; ad hoc when there is none, or with SIGN_IDENTITY=-)
#   NOTARY_PROFILE=<name>   the notarytool keychain credentials to notarize with (macos/notarize-app.sh), when signed
#                           with Developer ID: default notarytool-password, the profile Blink's
#                           build_scripts/publish/04-notarize.sh uses; NOTARY_PROFILE=- to skip notarization
#
# Signed with Developer ID, it also makes dist/AG-CuteMusic[-intel].dmg (macos/make-dmg.sh, needs create-dmg from
# MacPorts): the image is notarized, its ticket stapled to it and to the app (dist/AG-CuteMusic[-intel].zip).
#
# GStreamer's wheels are copied into Contents/Resources/gstreamer as they are (their libraries find each other
# by relative paths), not packed by py2app, then trimmed (with Qt) by macos/prune_app.py; macos/standalone.py sets
# their environment up at start.

set -e

MODE=universal
case "$1" in
    ''|--universal) MODE=universal;;
    --intel) MODE=intel;;
    *) echo "usage: $0 [--universal | --intel]" >&2; exit 2;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"
APP_ID=$(sed -n "s/^APP_ID = '\(.*\)'/\1/p" cutemusic/__init__.py)
APP_NAME=$(sed -n "s/^APP_NAME = '\(.*\)'/\1/p" cutemusic/__init__.py)

if [ -z "$PYTHON" ]; then
    for version in 3.14 3.13 3.12 3.11 3.10; do
        candidate="/Library/Frameworks/Python.framework/Versions/$version/bin/python3"
        [ -x "$candidate" ] && PYTHON="$candidate" && break
    done
fi
if [ -z "$PYTHON" ] || ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2> /dev/null; then
    echo "Python 3.10 or newer from python.org is needed (https://www.python.org/downloads/macos/), or PYTHON=..." >&2
    exit 1
fi
PLATFORM=$("$PYTHON" -c 'import sysconfig; print(sysconfig.get_platform())')
case "$PLATFORM" in
    *universal2) ;;
    *) echo "$PYTHON is $PLATFORM, not universal2: use python.org's installer (not Homebrew's python)" >&2; exit 1;;
esac

if [ "$MODE" = intel ]; then
    RUN=(arch -x86_64)
    APP_ARCH=x86_64
    NEEDED="x86_64"
    if [ "$(uname -m)" = arm64 ] && ! arch -x86_64 /usr/bin/true 2> /dev/null; then
        echo "Rosetta is needed to build the Intel app here: softwareupdate --install-rosetta" >&2
        exit 1
    fi
else
    RUN=()
    APP_ARCH=universal2
    NEEDED="x86_64 arm64"
fi

VENV="$ROOT/build/standalone-$MODE-env"
DIST="$ROOT/dist/$MODE"
LOG="$ROOT/build/py2app-$MODE.log"
PY=("${RUN[@]}" "$VENV/bin/python3")
mkdir -p build

echo "== $MODE app, with $PYTHON ($("$PYTHON" -V 2>&1)), venv $VENV"
if [ ! -x "$VENV/bin/python3" ]; then
    "${RUN[@]}" "$PYTHON" -m venv "$VENV"
fi
"${PY[@]}" -m pip install --quiet --upgrade pip
"${PY[@]}" -m pip install --quiet --upgrade gstreamer-bundle 'PyQt6>=6.9,<6.12' py2app

# mutagen: pure Python (tags, covers, lyrics)
"${PY[@]}" -m pip install --quiet --upgrade mutagen
# certifi: the certificates for the online lookups (python.org's Python has none of its own)
"${PY[@]}" -m pip install --quiet --upgrade certifi
# PyObjC's MediaPlayer: the media keys through Now Playing (universal2 wheels)
"${PY[@]}" -m pip install --quiet --upgrade pyobjc-framework-MediaPlayer
# PyObjC's AVFoundation: the microphone permission asked for (mic.py), before GStreamer lists the inputs
"${PY[@]}" -m pip install --quiet --upgrade pyobjc-framework-AVFoundation
# zeroconf (libraries on the LAN, Bonjour), as AG CuteCams: its wheels are arm64 only; it runs without its compiled
# modules
"${PY[@]}" -m pip install --quiet --upgrade ifaddr
if [ "$MODE" = intel ]; then
    SKIP_CYTHON=1 "${PY[@]}" -m pip install --quiet --upgrade zeroconf
else
    "${PY[@]}" -m pip install --quiet --upgrade zeroconf
    "${PY[@]}" - <<'PYEOF'
import os, zeroconf
removed = 0
for directory, folders, names in os.walk(os.path.dirname(zeroconf.__file__)):
    for name in names:
        if name.endswith('.so'):
            os.remove(os.path.join(directory, name))
            removed += 1
print(f'  zeroconf: {removed} compiled modules removed (its Python ones run instead)')
PYEOF
fi

# Qt: one wheel per architecture on PyPI; universal: both merged into a universal2 one
if [ "$MODE" = universal ]; then
    "${PY[@]}" -m pip install --quiet --upgrade delocate
    QT_VERSION=$("${PY[@]}" -c 'import importlib.metadata as m; print(m.version("PyQt6-Qt6"))')
    QT="$ROOT/build/qt-$QT_VERSION"
    if ! ls "$QT"/merged/*universal2*.whl > /dev/null 2>&1; then
        rm -rf "$QT"
        for platform in macosx_11_0_arm64 macosx_11_0_x86_64; do
            "${PY[@]}" -m pip download --quiet --no-deps --only-binary=:all: --platform "$platform" \
                "PyQt6-Qt6==$QT_VERSION" -d "$QT/$platform"
        done
        "$VENV/bin/delocate-merge" "$QT"/macosx_11_0_arm64/*.whl "$QT"/macosx_11_0_x86_64/*.whl -w "$QT/merged"
    fi
    "${PY[@]}" -m pip install --quiet --force-reinstall --no-deps "$QT"/merged/*.whl
    echo "  Qt $QT_VERSION: universal2"
fi

echo "== GStreamer in the venv"
"${PY[@]}" -c "
import gi; gi.require_version('Gst', '1.0'); from gi.repository import Gst; Gst.init(None)
missing = [n for n in ('playbin', 'decodebin', 'audioconvert', 'audioresample', 'level', 'appsrc', 'appsink') if Gst.ElementFactory.find(n) is None]
print('  ' + Gst.version_string() + (', MISSING: ' + ', '.join(missing) if missing else ''))
"

echo "== cutemusic sources"
# every module compiles and every cutemusic module imported exists: a file not added to darcs stops the build here,
# not at the first launch of the app
"${PY[@]}" - "$ROOT/cutemusic" "$HERE/standalone.py" <<'PYEOF'
import ast, os, sys
package, script = sys.argv[1], sys.argv[2]
modules = {name[:-3] for name in os.listdir(package) if name.endswith('.py')}
errors = []
for path in [os.path.join(package, name + '.py') for name in sorted(modules)] + [script]:
    try:
        tree = ast.parse(open(path, 'rb').read(), path)
    except SyntaxError as error:
        errors.append(f'{path}:{error.lineno}: {error.msg}')
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module == 'cutemusic':
                names = [alias.name for alias in node.names]      # from cutemusic import x: x a module or a name
                wanted = [name for name in names if name not in modules and name not in ('APP_ID', 'APP_NAME', '__version__')]
                wanted = [] if all(name[0].isupper() or name.startswith('_') for name in wanted) else wanted
            elif node.level == 0 and (node.module or '').startswith('cutemusic.'):
                wanted = [node.module.split('.')[1]]
            elif node.level == 1:
                wanted = [node.module.split('.')[0]] if node.module else [alias.name for alias in node.names]
            else:
                continue
        elif isinstance(node, ast.Import):
            wanted = [alias.name.split('.')[1] for alias in node.names if alias.name.startswith('cutemusic.')]
        else:
            continue
        for name in wanted:
            if name not in modules:
                errors.append(f'{os.path.relpath(path, os.path.dirname(package))}:{node.lineno}: imports cutemusic.{name}, '
                              f'there is no cutemusic/{name}.py (not added to darcs?)')
if errors:
    sys.exit('\n'.join('  ' + error for error in errors))
print(f'  {len(modules)} modules: ok')
PYEOF

echo "== py2app (standalone, $APP_ARCH), log in $LOG"
"${PY[@]}" macos/make_icns.py meters build/app.icns       # the VU meters icon (cutemusic/appicon.py), as the Dock shows it
rm -rf "$DIST" "build/py2app-$MODE"
if ! STANDALONE=1 APP_ARCH="$APP_ARCH" APP_ICNS="$ROOT/build/app.icns" PYTHONPATH="$ROOT" \
        "${PY[@]}" macos/setup.py py2app --dist-dir "$DIST" --bdist-base "build/py2app-$MODE" > "$LOG" 2>&1; then
    tail -40 "$LOG" >&2
    echo "py2app failed, full log in $LOG" >&2
    exit 1
fi
APP="$DIST/$APP_NAME.app"
[ -d "$APP" ] || APP=$(ls -d "$DIST"/*.app | head -1)
RESOURCES="$APP/Contents/Resources"

echo "== GStreamer into the app"
SITE=$("${PY[@]}" -c 'import gstreamer_libs, os; print(os.path.dirname(os.path.dirname(gstreamer_libs.__file__)))')
mkdir -p "$RESOURCES/gstreamer"
for package in gstreamer_libs gstreamer_plugins gstreamer_plugins_libs gstreamer_plugins_restricted gstreamer_plugins_gpl \
               gstreamer_plugins_gpl_restricted gstreamer_python; do
    [ -d "$SITE/$package" ] && ditto "$SITE/$package" "$RESOURCES/gstreamer/$package"
done
find "$RESOURCES/gstreamer" -name __pycache__ -type d -prune -exec rm -rf {} +
# the window icon (cutemusic/../icons), next to the cutemusic package wherever py2app put it
CUTEMUSIC=$(find "$RESOURCES" -type d -name cutemusic -not -path '*/gstreamer/*' | head -1)
[ -n "$CUTEMUSIC" ] && ditto icons "$(dirname "$CUTEMUSIC")/icons"

echo "== trimming: the GStreamer plugins and Qt modules the app uses, and the libraries they load"
"${PY[@]}" macos/prune_app.py "$APP"

echo "== the Feed devices' C I/O (macos/feedio/): _feedio.dylib into the app's cutemusic package (hal.py loads it there)"
macos/feedio/build.sh
[ -n "$CUTEMUSIC" ] && ditto cutemusic/_feedio.dylib "$CUTEMUSIC/_feedio.dylib"
echo "== the AG Podcast Mixer driver (macos/driver/) into the app"
macos/driver/build.sh
ditto build/driver/AGPodcastMixer.driver "$RESOURCES/AGPodcastMixer.driver"

echo "== architectures (needed: $NEEDED)"
WRONG=0
while IFS= read -r -d '' file; do
    archs=$(lipo -archs "$file" 2> /dev/null) || continue
    for arch in $NEEDED; do
        case " $archs " in
            *" $arch "*) ;;
            *) [ $WRONG -lt 20 ] && echo "  no $arch: ${file#$APP/} ($archs)"; WRONG=$((WRONG + 1)); break;;
        esac
    done
done < <(find "$APP" -type f \( -name '*.so' -o -name '*.dylib' -o -perm -u+x -o -path '*.framework/Versions/*' \) -print0)
[ $WRONG -eq 0 ] && echo "  all binaries: $NEEDED" || echo "  $WRONG binaries without all of: $NEEDED"

if [ "$SIGN_IDENTITY" = - ] || { [ -z "$SIGN_IDENTITY" ] && ! security find-identity -v -p codesigning | grep -q 'Developer ID Application'; }; then
    echo "== signing (ad hoc: no Developer ID Application certificate)"
    codesign --force --deep --sign - "$APP" 2>&1 | tail -3 || true
    SIGNED=
else
    echo "== signing (Developer ID, hardened runtime)"
    SIGN_CACHE="$ROOT/build/sign-cache" macos/sign-app.sh "$APP" ${SIGN_IDENTITY:+"$SIGN_IDENTITY"}
    SIGNED=1
fi

echo "== self test"
EXE="$APP/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")"
SELFTEST=ok
# (a GStreamer variable left by another process, as the updater's shell does: standalone.py must ignore it)
GST_PYTHONPATH_1_0=/nonexistent AG_CUTEMUSIC_SELFTEST=1 "${RUN[@]}" "$EXE" || { echo "  SELF TEST FAILED: see above"; SELFTEST=; }

# the universal app is the default one: no label; the Intel one says so
[ "$MODE" = universal ] && PACKAGE="$ROOT/dist/AG-CuteMusic" || PACKAGE="$ROOT/dist/AG-CuteMusic-$MODE"
ZIP="$PACKAGE.zip"

# an app that does not start is not packaged: no image, no zip, not notarized (and none left from a build before)
if [ -z "$SELFTEST" ]; then
    rm -f "$PACKAGE.dmg" "$ZIP"
    echo
    echo "NOT PACKAGED: $APP failed its self test (above). To see it start: \"$EXE\"" >&2
    exit 1
fi
NOTARY_PROFILE=${NOTARY_PROFILE:-notarytool-password}
if [ -n "$SIGNED" ] && [ "$NOTARY_PROFILE" != - ] && [ -n "$SELFTEST" ]; then
    # one submission, Apple's recommendation: the outermost container, the disk image (its ticket covers the app,
    # stapled with it too, for the zip); the app alone without create-dmg
    DMG="$PACKAGE.dmg"
    if command -v create-dmg > /dev/null; then
        echo "== disk image"
        macos/make-dmg.sh "$APP" "$DMG"
        codesign --force --timestamp --sign "$(macos/sign-app.sh --identity ${SIGN_IDENTITY:+"$SIGN_IDENTITY"})" "$DMG"
        echo "== notarizing"
        macos/notarize-app.sh "$DMG" "$NOTARY_PROFILE" "$APP"
    else
        echo "== no disk image: create-dmg is needed for it (sudo port install create-dmg)"
        DMG=
        echo "== notarizing"
        macos/notarize-app.sh "$APP" "$NOTARY_PROFILE"
    fi
    rm -f "$ZIP"
    ditto -c -k --keepParent "$APP" "$ZIP"
    echo
    echo "Built, signed and notarized: $APP ($(du -sh "$APP" | cut -f1))"
    echo "To copy to other Macs: ${DMG:+$DMG ($(du -sh "$DMG" | cut -f1)), or }$ZIP ($(du -sh "$ZIP" | cut -f1))"
    # the finished image, signed and notarized: the one to drag the app to Applications from (not the window
    # make-dmg.sh showed while laying it out). NO_OPEN=1: not opened
    if [ -n "$DMG" ] && [ -z "${NO_OPEN:-}" ]; then
        # only this image attached: another volume of the same name (create-dmg's read-write image left behind, an
        # image of a build before) makes Finder ask twice whether to replace the app, and leave a "Preparing" bubble
        for volume in "/Volumes/$APP_NAME"*; do
            [ -d "$volume" ] && hdiutil detach "$volume" -force -quiet || true
        done
        echo "Opening $DMG: drag $APP_NAME to Applications from it"
        open "$DMG"
    fi
    exit 0
fi
echo
echo "Built: $APP ($(du -sh "$APP" | cut -f1))"
echo "To copy it to another Mac:"
echo "  ditto -c -k --keepParent \"$APP\" \"$ZIP\""
if [ -n "$SIGNED" ]; then
    echo "Signed but not notarized: Gatekeeper still blocks it"
fi
echo "There, after unzipping into /Applications (the app is not notarized):"
echo "  xattr -dr com.apple.quarantine \"/Applications/$APP_NAME.app\""
