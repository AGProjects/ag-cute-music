#!/bin/bash
#
# Build the AG Podcast Mixer driver: build/driver/AGPodcastMixer.driver (arm64 + x86_64, macOS 13+).
#
#   macos/driver/build.sh [--trace] [--sign [<identity>]]
#
# --trace   libASPL's tracing of every HAL call to the system log (log stream --predicate 'process == "coreaudiod"')
# --sign    Developer ID, hardened runtime, timestamp (default: the first "Developer ID Application" certificate);
#           else ad hoc. make-standalone.sh does not need it: sign-app.sh signs the driver inside the app.
#
# Needs Xcode's command line tools and cmake (brew install cmake). The version is DRIVER_VERSION in
# CMakeLists.txt: raise it with every change, AG CuteMusic offers to update an installed driver older than its own.

set -e
cd "$(dirname "$0")"
HERE=$(pwd)
ROOT=$(cd ../.. && pwd)
BUILD="$ROOT/build/driver"
TRACE=OFF
SIGN=
while [ $# -gt 0 ]; do
    case "$1" in
        --trace) TRACE=ON;;
        --sign) SIGN=1; if [ -n "${2:-}" ] && [ "${2#--}" = "$2" ]; then IDENTITY="$2"; shift; fi;;
        *) echo "usage: $0 [--trace] [--sign [<identity>]]" >&2; exit 2;;
    esac
    shift
done
command -v cmake > /dev/null || { echo "cmake missing: brew install cmake" >&2; exit 1; }

cmake -S "$HERE" -B "$BUILD/cmake" -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_ARCHITECTURES="arm64;x86_64" \
      -DCMAKE_OSX_DEPLOYMENT_TARGET=13.0 -DAGPM_TRACE=$TRACE > "$BUILD.log" 2>&1 || { cat "$BUILD.log" >&2; exit 1; }
cmake --build "$BUILD/cmake" --config Release -j "$(sysctl -n hw.ncpu)" >> "$BUILD.log" 2>&1 || {
    tail -40 "$BUILD.log" >&2; echo "build failed, full log in $BUILD.log" >&2; exit 1; }

rm -rf "$BUILD/AGPodcastMixer.driver"
ditto "$BUILD/cmake/AGPodcastMixer.driver" "$BUILD/AGPodcastMixer.driver"
DRIVER="$BUILD/AGPodcastMixer.driver"
# readable by everyone: coreaudiod (user _coreaudiod) reads it, and configure_file copies Info.plist.in's mode
chmod -R u=rwX,go=rX "$DRIVER"
if [ -n "$SIGN" ]; then
    IDENTITY=$("$ROOT/macos/sign-app.sh" --identity ${IDENTITY:+"$IDENTITY"})
    [ -n "$IDENTITY" ] || { echo "no Developer ID Application certificate in the keychain" >&2; exit 1; }
    codesign --force --timestamp --options runtime --sign "$IDENTITY" "$DRIVER"
else
    codesign --force --sign - "$DRIVER"
fi
VERSION=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$DRIVER/Contents/Info.plist")
echo "built $DRIVER $VERSION ($(lipo -archs "$DRIVER/Contents/MacOS/AGPodcastMixer"))"
echo "try it: sudo macos/driver/install.sh   (or AG CuteMusic: Mixer -> Install AG Podcast Mixer driver...)"
