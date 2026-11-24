#!/bin/bash

# Build the Debian package of AG CuteMusic: dist/ag-cute-music_<version>_all.deb
#
#   ./makedeb.sh [distro] [rebuild|install]
#
#   distro    adds it to the version, as autopackager does (0.1.0 -> 0.1.0bookworm), only in the
#             build tree; none or sid: as in debian/changelog
#   rebuild   builds again in the tree of the last run (no clean, binary only)
#   install   builds, then installs the package with its dependencies (sudo apt install)

set -e

cd "$(dirname "$(realpath "$0")")"

distro="${1:-}"
mode="${2:-}"
package=ag-cute-music

# fail early if the code uses syntax this Python does not have (e.g. 3.12 f-strings on
# bookworm's 3.11); run inside the target distro's chroot
python3 - <<'PYTHON'
import os, sys
errors = 0
for directory in ('cutemusic', 'bin'):
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not (name.endswith('.py') or directory == 'bin') or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding='utf-8') as f:
                compile(f.read(), path, 'exec')
        except SyntaxError as e:
            print(f'{e.filename}:{e.lineno}: {e.msg}', file=sys.stderr)
            errors += 1
if errors:
    sys.exit(f'{errors} file(s) do not compile with Python {sys.version.split()[0]}')
PYTHON

suffix() {
    # the distro in the changelog version, only in the build tree - never in the source repo
    if [ -n "$distro" ] && [ "$distro" != "sid" ]; then
        sed -i "s/) unstable/$distro) $distro/" debian/changelog
        head -1 debian/changelog
    fi
}

if [ "$mode" = "rebuild" ]; then
    cd dist/$package-*/
    debuild --no-sign -nc -b
    exit 0
fi

# the tools: mk-build-deps needs equivs, debuild comes with devscripts, dpkg-parsechangelog with dpkg-dev
missing=""
for tool in equivs devscripts dpkg-dev lintian; do
    dpkg-query -W -f='${Status}' "$tool" 2>/dev/null | grep -q "ok installed" || missing="$missing $tool"
done
if [ -n "$missing" ]; then
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends $missing
fi

# the build dependencies (debian/control), as a package removed again after
sudo mk-build-deps --install --root-cmd sudo --remove \
    --tool 'apt-get -y --no-install-recommends' debian/control
rm -f $package-build-deps_*

# a clean copy of the tree to build in: dist/ag-cute-music-<version>
version=$(dpkg-parsechangelog -S Version)
rm -rf dist
mkdir -p dist/$package-$version
tar -c --exclude=./_darcs --exclude=./dist --exclude=./build --exclude='__pycache__' --exclude='*.pyc' \
    --exclude=./macos/build --exclude=./macos/dist --exclude='*~' . | tar -x -C dist/$package-$version
cd dist/$package-$version
suffix

debuild --no-sign

cd ..
deb=$(ls -t ${package}_*_all.deb | head -1)
echo
echo "built: dist/$deb"
lintian --no-tag-display-limit "$deb" 2>/dev/null | head -20 || true

if [ "$mode" = "install" ]; then
    # apt installs its dependencies too (python3-pyqt6, python3-mutagen, GStreamer...)
    sudo apt-get install -y "./$deb"
fi

cat <<INSTRUCTIONS

Install with its dependencies:

  sudo apt install ./dist/$deb
INSTRUCTIONS
