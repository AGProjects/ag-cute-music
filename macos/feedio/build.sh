#!/bin/bash
#
# Build cutemusic/_feedio.dylib (arm64 + x86_64, macOS 13+): the C I/O on the AG Podcast Mixer driver's hidden Feed
# devices (feedio.c), loaded by cutemusic/hal.py. make-standalone.sh runs it; run it once by hand for a checkout.

set -e
cd "$(dirname "$0")"
ROOT=$(cd ../.. && pwd)
clang -O2 -Wall -Wextra -dynamiclib -arch arm64 -arch x86_64 -mmacosx-version-min=13.0 -headerpad_max_install_names \
    -framework CoreAudio -install_name @loader_path/_feedio.dylib \
    -o "$ROOT/cutemusic/_feedio.dylib" feedio.c
echo "built $ROOT/cutemusic/_feedio.dylib"
