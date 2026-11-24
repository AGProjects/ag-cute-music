#!/bin/bash
#
# Install (or with -u remove) the AG Podcast Mixer driver and restart coreaudiod. The same script AG CuteMusic runs,
# with the administrator's password, from its menu (cutemusic/driver.py), there with the driver in its bundle.
#
#   sudo macos/driver/install.sh [<driver>]      default: build/driver/AGPodcastMixer.driver (build.sh)
#   sudo macos/driver/install.sh -u

set -e
DEST=/Library/Audio/Plug-Ins/HAL/AGPodcastMixer.driver
[ "$(id -u)" -eq 0 ] || { echo "run it as root: sudo $0 $*" >&2; exit 1; }

restart_coreaudiod() {
    killall coreaudiod          # launchd starts it again (launchctl kickstart is refused with SIP on)
}

if [ "${1:-}" = -u ]; then
    rm -rf "$DEST"
    restart_coreaudiod
    echo "removed $DEST"
    exit 0
fi

SOURCE="${1:-$(cd "$(dirname "$0")/../.." && pwd)/build/driver/AGPodcastMixer.driver}"
SOURCE="${SOURCE%/}"
[ -f "$SOURCE/Contents/Info.plist" ] || { echo "no driver at $SOURCE (macos/driver/build.sh builds it)" >&2; exit 1; }
mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST.new"
ditto "$SOURCE" "$DEST.new"
chown -R root:wheel "$DEST.new"
chmod -R u=rwX,go=rX "$DEST.new"      # coreaudiod (_coreaudiod) must read it
rm -rf "$DEST"
mv "$DEST.new" "$DEST"
restart_coreaudiod
echo "installed $DEST $(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$DEST/Contents/Info.plist")"
