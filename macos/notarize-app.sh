#!/bin/bash
#
# Notarize a Developer ID signed app (macos/sign-app.sh) or disk image with Apple and staple the ticket to it.
#
#   macos/notarize-app.sh <app or dmg> <profile> [<app>...]
#
# The ticket of a disk image covers the code inside it too: the apps after <profile>, the ones the image was made
# from, are stapled with it as well (one submission for both).
#
# <profile>: notarytool credentials in the keychain, stored once with
#   xcrun notarytool store-credentials <profile> --apple-id <apple id> --team-id <team id> --password <app-specific password>

set -e

APP="${1%/}"
NOTARY_PROFILE="$2"
shift 2 || true
{ [ -d "$APP/Contents" ] || [ "${APP%.dmg}" != "$APP" ]; } && [ -n "$NOTARY_PROFILE" ] || { echo "usage: $0 <app or dmg> <profile>" >&2; exit 2; }

if [ "${APP%.dmg}" != "$APP" ]; then
    ZIP="$APP"          # a disk image goes as it is
else
    ZIP="$(dirname "$APP")/$(basename "$APP" .app)-notarize.zip"
    ditto -c -k --keepParent "$APP" "$ZIP"
fi
echo "  notarizing (profile $NOTARY_PROFILE), this takes a few minutes"
# straight to the terminal, as Blink's 04-notarize.sh does: notarytool shows its upload progress and the
# "Current status: In Progress..." wait only on a terminal; script(1) gives it one and keeps a copy to check
LOG=$(mktemp -t notarize)
script -q "$LOG" xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait || true
OUTPUT=$(tr '\r' '\n' < "$LOG")
rm -f "$LOG"
[ "$ZIP" = "$APP" ] || rm -f "$ZIP"
if ! echo "$OUTPUT" | grep -q 'status: Accepted'; then
    ID=$(echo "$OUTPUT" | sed -n 's/^ *id: //p' | head -1)
    [ -n "$ID" ] && xcrun notarytool log "$ID" --keychain-profile "$NOTARY_PROFILE"
    echo "notarization failed" >&2
    exit 1
fi
xcrun stapler staple "$APP"
if [ "${APP%.dmg}" != "$APP" ]; then
    spctl --assess --type open --context context:primary-signature --verbose "$APP"
else
    spctl --assess --type execute --verbose "$APP"
fi
for other in "$@"; do
    xcrun stapler staple "${other%/}"
    spctl --assess --type execute --verbose "${other%/}"
done
