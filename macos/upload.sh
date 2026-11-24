#!/bin/bash
#
# Upload the notarized disk images (make-standalone.sh) to the AG Projects download server, as Blink's
# build_scripts/publish/08-upload.sh does.
#
#   macos/upload.sh
#
# Uploads all of a release: dist/AG-CuteMusic.dmg (universal) and dist/AG-CuteMusic-intel.dmg, whichever exist,
# after checking they are notarized (stapled ticket), their .zip, then the appcast for them (make-appcast.py: the
# running apps' updates), after checking the built app is the version in cutemusic/__init__.py.
#
# Environment overrides:
#   UPLOAD_TO=<host:folder>   default download:/var/www/download/Cute/ (download: the ssh host alias
#                             Blink uses)

set -e

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST="$(cd "$HERE/.." && pwd)/dist"
UPLOAD_TO="${UPLOAD_TO:-download:/var/www/download/Cute/}"

VERSION=$(sed -n "s/^__version__ = '\(.*\)'/\1/p" "$HERE/../cutemusic/__init__.py")

# everything make-standalone.sh left for this version: the disk images (notarized), the zips of the apps, and the
# appcast for them (make-appcast.py: the running apps' updates), uploaded last: the apps out there see the new version
# only once its files are there
FILES=()
for image in "$DIST/AG-CuteMusic.dmg" "$DIST/AG-CuteMusic-intel.dmg"; do
    [ -f "$image" ] || continue
    if ! xcrun stapler validate -q "$image"; then
        echo "$image is not notarized: build it with make-standalone.sh (signed, notarized)" >&2
        exit 1
    fi
    FILES+=("$image")
    zip="${image%.dmg}.zip"
    [ -f "$zip" ] && FILES+=("$zip")
done
[ ${#FILES[@]} -gt 0 ] || { echo "no disk image in $DIST: macos/make-standalone.sh first" >&2; exit 1; }

"$HERE/make-appcast.py"
BUILT=$(sed -n 's:.*<sparkle\:shortVersionString>\(.*\)</sparkle\:shortVersionString>.*:\1:p' "$DIST/AGCuteMusicAppcast.xml")
if [ "$BUILT" != "$VERSION" ]; then
    echo "the built app is $BUILT, cutemusic/__init__.py says $VERSION: macos/make-standalone.sh again" >&2
    exit 1
fi

echo "uploading $VERSION to $UPLOAD_TO:"
printf '  %s\n' "${FILES[@]##*/}" AGCuteMusicAppcast.xml
ssh "${UPLOAD_TO%%:*}" mkdir -p "${UPLOAD_TO#*:}"
# a dropped connection resumes where it stopped (the part kept in .rsync-partial/ there, the file served untouched
# until complete), up to 5 tries; the appcast only once all the rest is there
send() {
    local try
    for try in 1 2 3 4 5; do
        rsync -avz --partial-dir=.rsync-partial --progress "$@" "$UPLOAD_TO" && return 0
        echo "upload interrupted (try $try of 5): again in 10 s" >&2
        sleep 10
    done
    echo "upload failed: the appcast is not uploaded, the apps out there still see the version before" >&2
    exit 1
}
send "${FILES[@]}"
send "$DIST/AGCuteMusicAppcast.xml"
echo "uploaded: https://download.ag-projects.com/Cute/ (the apps see $VERSION at their next check)"
