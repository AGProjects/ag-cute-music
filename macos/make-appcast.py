#!/usr/bin/env python3
"""The appcast for the app's updates (cutemusic/updater.py): dist/AGCuteMusicAppcast.xml, uploaded by macos/upload.sh
after the disk images.

    macos/make-appcast.py

One <item>, the version built (dist/<mode>/AG CuteMusic.app), its notes from debian/changelog, an <enclosure> per
disk image there (dist/AG-CuteMusic.dmg: universal2, dist/AG-CuteMusic-intel.dmg: x86_64) with its length and
sha256. Sparkle's format, so Blink's tools read it too; ag:arch and ag:sha256 are ours.
"""

import email.utils
import hashlib
import html
import os
import plistlib
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, 'dist')
BASE_URL = os.environ.get('APPCAST_BASE_URL', 'https://download.ag-projects.com/Cute/')
APPCAST = os.path.join(DIST, 'AGCuteMusicAppcast.xml')
IMAGES = [('AG-CuteMusic.dmg', 'universal', 'universal2'), ('AG-CuteMusic-intel.dmg', 'intel', 'x86_64')]


def app_info(mode):
    folder = os.path.join(DIST, mode)
    apps = [name for name in os.listdir(folder) if name.endswith('.app')] if os.path.isdir(folder) else []
    if not apps:
        return None
    with open(os.path.join(folder, apps[0], 'Contents', 'Info.plist'), 'rb') as file:
        return plistlib.load(file)


def notes(version):
    """The debian/changelog entry of version, as HTML (its bullets a list)."""
    text = open(os.path.join(ROOT, 'debian', 'changelog'), encoding='utf-8').read()
    match = re.search(rf'^\S+ \({re.escape(version)}\).*?\n(.*?)\n -- ', text, re.M | re.S)
    if not match:
        return ''
    items, current = [], None
    for line in match.group(1).splitlines():
        if line.strip().startswith('* '):
            current = line.strip()[2:]
            items.append(current)
        elif line.strip() and items:
            items[-1] += ' ' + line.strip()
    return '<ul>' + ''.join(f'<li>{html.escape(item)}</li>' for item in items) + '</ul>'


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as file:
        for chunk in iter(lambda: file.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    enclosures, version, minimum = [], None, None
    for image, mode, arch in IMAGES:
        path = os.path.join(DIST, image)
        info = app_info(mode)
        if not os.path.isfile(path) or info is None:
            continue
        built = info['CFBundleShortVersionString']
        if version is not None and built != version:
            sys.exit(f'{image} is {built}, the other image {version}: build both again')
        version, minimum = built, info.get('LSMinimumSystemVersion', '')
        enclosures.append(f'      <enclosure url="{BASE_URL}{image}" length="{os.path.getsize(path)}" '
                          f'type="application/octet-stream" sparkle:os="macos" ag:arch="{arch}" '
                          f'ag:sha256="{sha256(path)}"/>')
    if not enclosures:
        sys.exit(f'no disk image in {DIST}: macos/make-standalone.sh first')
    description = notes(version)
    if not description:
        print(f'warning: no debian/changelog entry for {version}: no release notes', file=sys.stderr)
    xml = f'''<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle" xmlns:ag="https://ag-projects.com/xml-namespaces/cute">
  <channel>
    <title>AG CuteMusic</title>
    <link>{BASE_URL}AGCuteMusicAppcast.xml</link>
    <item>
      <title>AG CuteMusic {version}</title>
      <pubDate>{email.utils.formatdate(time.time())}</pubDate>
      <sparkle:version>{version}</sparkle:version>
      <sparkle:shortVersionString>{version}</sparkle:shortVersionString>
      <sparkle:minimumSystemVersion>{minimum}</sparkle:minimumSystemVersion>
      <description><![CDATA[{description}]]></description>
{chr(10).join(enclosures)}
    </item>
  </channel>
</rss>
'''
    with open(APPCAST, 'w', encoding='utf-8') as file:
        file.write(xml)
    print(f'{APPCAST}: {version}, {len(enclosures)} image(s)')


if __name__ == '__main__':
    main()
