#!/bin/bash
#
# A new release of the Mac app: the version raised, its changelog entry written, the app built, then uploaded.
#
#   macos/release.sh [<version>]      default: the last number of __version__ raised (1.1.0 -> 1.1.1)
#
# 1. __version__ in cutemusic/__init__.py set to <version> (it also starts the Windows build on GitHub, once pushed)
# 2. a debian/changelog entry for it, "Bug fixes", opened in $VISUAL / $EDITOR (default: TextEdit, waited for): its
#    bullets are the release notes the update dialog shows (make-appcast.py)
# 3. macos/make-standalone.sh: the universal app, signed, notarized, its disk image
# 4. the appcast made and checked, shown, then macos/upload.sh after a yes: the image, the zip, the appcast
#
# Run again after a failed build or an upload not done: when __version__ is not the live appcast's yet, it is kept (no
# second bump) with its entry, opened again.

set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
INIT=cutemusic/__init__.py
CHANGELOG=debian/changelog
PACKAGE=$(sed -n '1s/^\([^ ]*\) .*/\1/p' "$CHANGELOG")
CURRENT=$(sed -n "s/^__version__ = '\(.*\)'/\1/p" "$INIT")
TOP=$(sed -n '1s/^[^ ]* (\([^)]*\)).*/\1/p' "$CHANGELOG")

# the version out there: the live appcast's (none yet: the current one counts as released)
LIVE=$(curl -fsS --max-time 15 https://download.ag-projects.com/Cute/AGCuteMusicAppcast.xml 2> /dev/null \
       | sed -n 's:.*<sparkle\:shortVersionString>\(.*\)</sparkle\:shortVersionString>.*:\1:p' | head -1)
if [ -n "${1:-}" ]; then
    VERSION="$1"
elif [ -n "$LIVE" ] && [ "$LIVE" != "$CURRENT" ] && [ "$TOP" = "$CURRENT" ]; then
    VERSION="$CURRENT"          # not released yet (a build that failed, an upload not done): go on with it
    echo "== $CURRENT is not out yet ($LIVE is): going on with it"
else
    VERSION=$(echo "$CURRENT" | awk -F. -v OFS=. '{ $NF = $NF + 1; print }')
fi
echo "$VERSION" | grep -Eq '^[0-9]+(\.[0-9]+)*$' || { echo "not a version: $VERSION" >&2; exit 2; }

# 1. the version
if [ "$VERSION" != "$CURRENT" ]; then
    sed -i '' "s/^__version__ = '.*'/__version__ = '$VERSION'/" "$INIT"
    echo "== $INIT: $CURRENT -> $VERSION"
else
    echo "== $INIT: $VERSION already"
fi

# 2. the changelog entry
if [ "$TOP" != "$VERSION" ]; then
    DATE=$(LC_ALL=C date '+%a, %d %b %Y %H:%M:%S %z')
    {
        printf '%s (%s) unstable; urgency=medium\n\n  * Bug fixes\n\n -- AG Projects <support@ag-projects.com>  %s\n\n' \
            "$PACKAGE" "$VERSION" "$DATE"
        cat "$CHANGELOG"
    } > "$CHANGELOG.new"
    cat "$CHANGELOG.new" > "$CHANGELOG"
    rm -f "$CHANGELOG.new"
    echo "== $CHANGELOG: entry for $VERSION added (Bug fixes)"
fi
echo "== the release notes: edit the entry at the top of $ROOT/$CHANGELOG, save and close it"
if [ -n "${VISUAL:-${EDITOR:-}}" ]; then
    ${VISUAL:-$EDITOR} "$CHANGELOG"
else
    open -W -n -e "$CHANGELOG"        # TextEdit, waited for until it quits
fi
# the version as the entry says it now: changed there (1.1.11 -> 1.2.0), the app is built as that one
EDITED=$(sed -n '1s/^[^ ]* (\([^)]*\)).*/\1/p' "$CHANGELOG")
if [ "$EDITED" != "$VERSION" ]; then
    echo "$EDITED" | grep -Eq '^[0-9]+(\.[0-9]+)*$' || { echo "not a version at the top of $CHANGELOG: $EDITED" >&2; exit 2; }
    echo "== the entry is for $EDITED now: $INIT: $VERSION -> $EDITED"
    VERSION="$EDITED"
    sed -i '' "s/^__version__ = '.*'/__version__ = '$VERSION'/" "$INIT"
fi
echo
sed -n '1,/^ -- /p' "$CHANGELOG" | sed 's/^/    /'
echo
read -r -p "Build $VERSION with these notes? [Y/n] " answer
case "$answer" in [nN]*) echo "stopped: macos/release.sh $VERSION to go on"; exit 1;; esac

# 3. the app
NO_OPEN=1 macos/make-standalone.sh

# 4. the appcast, then the upload
macos/make-appcast.py
echo
echo "== $ROOT/dist/AGCuteMusicAppcast.xml:"
sed 's/^/    /' dist/AGCuteMusicAppcast.xml
echo
read -r -p "Upload $VERSION (macos/upload.sh)? [y/N] " answer
case "$answer" in
    [yY]*) macos/upload.sh;;
    *) echo "not uploaded: macos/upload.sh when ready";;
esac
echo "record and push the version and changelog with darcs (the Windows build starts from the version bump)"
