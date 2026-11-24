#!/bin/bash
#
# Sign an app bundle for distribution outside the App Store: Developer ID, hardened runtime, secure timestamp, as
# notarization (macos/notarize-app.sh) wants it.
#
#   macos/sign-app.sh <app> [<identity>]
#   macos/sign-app.sh --identity [<identity>]     prints the identity it would use
#
# <identity>: default the first "Developer ID Application" certificate in the keychain.
#
# Every Mach-O file is signed on its own (not codesign --deep: it misses libraries outside the standard places,
# like the GStreamer wheels in Contents/Resources), executables with macos/entitlements.plist, then the frameworks
# and the app. Each signature waits for a timestamp from Apple's server, so they are made SIGN_JOBS at a time
# (default 8); with SIGN_CACHE=<folder> (make-standalone.sh: build/sign-cache) a signed file is kept there, by the
# checksum of the unsigned one, the identity and the entitlements, and the next build copies it instead of
# signing again: only what changed is signed.

set -e

find_identity() {
    if [ -n "$1" ]; then
        echo "$1"
    else
        security find-identity -v -p codesigning | sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p' | head -1
    fi
}

# --identity [<identity>]: only print the identity it would sign with (make-standalone.sh signs the dmg with it)
if [ "${1:-}" = --identity ]; then
    find_identity "${2:-}"
    exit 0
fi

# --one <file>: sign one file (run in parallel by xargs below; IDENTITY, ENTITLEMENTS, SIGN_CACHE from the
# environment); prints "signed <file>", "cached <file>" or "FAILED <file>"
if [ "${1:-}" = --one ]; then
    file="$2"
    flags=()
    case "$(file -b "$file")" in
        *Mach-O*executable*) flags=(--entitlements "$ENTITLEMENTS");;
    esac
    key=
    if [ -n "$SIGN_CACHE" ]; then
        key=$( { shasum -a 256 < "$file"; echo "$IDENTITY ${flags[*]:+$(shasum -a 256 < "$ENTITLEMENTS")}"; } \
               | shasum -a 256 | cut -c1-40)
        if [ -f "$SIGN_CACHE/$key" ]; then
            cp "$SIGN_CACHE/$key" "$file"
            echo "cached $file"
            exit 0
        fi
    fi
    if output=$(codesign --force --timestamp --options runtime --sign "$IDENTITY" ${flags[@]:+"${flags[@]}"} "$file" 2>&1); then
        if [ -n "$key" ]; then
            cp "$file" "$SIGN_CACHE/$key.$$" && mv "$SIGN_CACHE/$key.$$" "$SIGN_CACHE/$key"
        fi
        echo "signed $file"
    else
        echo "$output" | sed 's/^/    /' >&2
        echo "FAILED $file"
    fi
    exit 0
fi

APP="${1%/}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTITLEMENTS="$HERE/entitlements.plist"
[ -d "$APP/Contents" ] || { echo "usage: $0 <app> [<identity>] | --identity [<identity>]" >&2; exit 2; }

IDENTITY=$(find_identity "${2:-}")
[ -n "$IDENTITY" ] || { echo "no Developer ID Application certificate in the keychain" >&2; exit 1; }
echo "  identity: $IDENTITY"
export IDENTITY ENTITLEMENTS SIGN_CACHE
[ -n "$SIGN_CACHE" ] && mkdir -p "$SIGN_CACHE"
JOBS=${SIGN_JOBS:-8}

progress() {         # progress <done> <total> <what>: one line rewritten in a terminal, every 25th otherwise
    if [ -t 1 ]; then
        printf '\r\033[K  %d/%d %s' "$1" "$2" "${3:0:100}"
    elif [ $(($1 % 25)) -eq 0 ] || [ "$1" -eq "$2" ]; then
        echo "  $1/$2"
    fi
}

# sign_all <stage> <path>...: SIGN_JOBS at a time, with progress; counts into SIGNED, CACHED, FAILED
SIGNED=0 CACHED=0 FAILED=0
sign_all() {
    local stage="$1" total=$(($# - 1)) done=0 result path
    shift
    [ $total -gt 0 ] || return 0
    while read -r result path; do
        done=$((done + 1))
        case "$result" in
            signed) SIGNED=$((SIGNED + 1));;
            cached) CACHED=$((CACHED + 1));;
            *) FAILED=$((FAILED + 1)); [ -t 1 ] && echo; echo "  FAILED: ${path#$APP/Contents/}";;
        esac
        progress $done $total "$stage: ${path#$APP/Contents/}"
    done < <(printf '%s\0' "$@" | xargs -0 -n 1 -P "$JOBS" "$0" --one)
    [ -t 1 ] && echo
    return 0
}

# the libraries and executables (framework binaries too: signed again with their framework), then the frameworks and
# the AG Podcast Mixer driver (a bundle, sealed like a framework)
MAIN="$APP/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")"
FILES=()
FRAMEWORK_LIST=()
while IFS= read -r -d '' file; do FILES+=("$file"); done < <(
    find "$APP/Contents" -type f ! -path "$MAIN" -print0 | xargs -0 -n 1000 file -F '|' 2> /dev/null \
    | sed -n 's/|.*Mach-O.*//p' | tr '\n' '\0')
while IFS= read -r -d '' framework; do FRAMEWORK_LIST+=("$framework"); done < <(
    find "$APP/Contents" -type d \( -name '*.framework' -o -name '*.driver' \) -print0)
echo "  ${#FILES[@]} libraries and executables, ${#FRAMEWORK_LIST[@]} frameworks, $JOBS at a time${SIGN_CACHE:+, cache $SIGN_CACHE}"
sign_all files "${FILES[@]}"
export SIGN_CACHE=      # a framework is a folder: signed each time (its binary comes from the cache above)
sign_all frameworks ${FRAMEWORK_LIST[@]:+"${FRAMEWORK_LIST[@]}"}
[ $FAILED -eq 0 ] || { echo "$FAILED signatures failed" >&2; exit 1; }
codesign --force --timestamp --options runtime --sign "$IDENTITY" --entitlements "$ENTITLEMENTS" "$APP"
echo "  $SIGNED signed, $CACHED from the cache, and the app"
codesign --verify --deep --strict "$APP"
echo "  codesign --verify: ok"
