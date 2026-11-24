#!/bin/bash
#
# A drag-to-Applications disk image of the app, laid out like Blink's (build_scripts/publish/06-dmg.sh).
#
#   macos/make-dmg.sh <app> <dmg>
#
# Needs create-dmg (sudo port install create-dmg). dmg-background.png is drawn 1:1 in window points, anchored
# top-left; the --icon coordinates are icon centres:
#
#   white box      x  14..457   y  14..257
#   arrow          x 180..290   y ~152..195
#   rule           y ~461
#   "AG Projects"  y ~477

set -euo pipefail

APP="${1%/}"
DMG="$2"
[ -d "$APP/Contents" ] && [ -n "$DMG" ] || { echo "usage: $0 <app> <dmg>" >&2; exit 2; }
command -v create-dmg > /dev/null || { echo "create-dmg is needed: sudo port install create-dmg" >&2; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME=$(basename "$APP")

WINDOW_W=475
WINDOW_H=520
ICON_SIZE=64
COL_L=130          # left column, left of the arrow
COL_R=378          # right column, right of the arrow
ROW_TOP=172        # on the arrow's axis, inside the white box
ROW_BOTTOM=350     # below the box, above the rule

# no volume of that name attached (an image of a build before, opened to install from): create-dmg's would be
# "<name> 1", and Finder then has two volumes of the app
for volume in "/Volumes/${NAME%.app}"*; do
    [ -d "$volume" ] && hdiutil detach "$volume" -force -quiet || true
done

# staging from scratch: a stale .DS_Store would be baked into the image, and Finder honours it over create-dmg
STAGING="$(dirname "$DMG")/dmg-staging"
rm -rf "$STAGING" "$DMG" "$(dirname "$DMG")"/rw.*.dmg
mkdir -p "$STAGING"
ditto "$APP" "$STAGING/$NAME"
cp "$HERE/../README" "$STAGING/README.txt"

# create-dmg mounts a temporary read-write image and opens it in Finder to lay out the icons, then ejects it and
# converts it: that window is not the image to install from (a copy started from it is cut off when it is
# ejected, and Finder keeps its "Preparing to copy" bubble). make-standalone.sh opens the finished image once it
# is signed and notarized.
echo "  laying out the image: the Finder window that opens now closes by itself, do not copy from it"
create-dmg \
    --volname "${NAME%.app}" \
    --background "$HERE/dmg-background.png" \
    --window-pos 200 120 \
    --window-size $WINDOW_W $WINDOW_H \
    --icon-size $ICON_SIZE \
    --icon "$NAME"      $COL_L $ROW_TOP \
    --app-drop-link     $COL_R $ROW_TOP \
    --icon "README.txt" $COL_L $ROW_BOTTOM \
    --no-internet-enable \
    "$DMG" "$STAGING" > /dev/null
rm -rf "$STAGING"
echo "  $DMG ($(du -sh "$DMG" | cut -f1))"
