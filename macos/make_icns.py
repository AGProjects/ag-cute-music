"""Render the app icon into a macOS .icns (iconutil needs an .iconset of PNGs). From AG CuteSync.

    make_icns.py meters build/app.icns       the amplifier's VU meters (cutemusic/appicon.py), as the Dock shows it
    make_icns.py icon.svg build/app.icns     an SVG"""
import os
import shutil
import subprocess
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer

svg, icns = sys.argv[1], sys.argv[2]
app = QGuiApplication(['make_icns'])
meters = svg == 'meters'
if meters:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from cutemusic import appicon
renderer = None if meters else QSvgRenderer(svg)
iconset = os.path.splitext(icns)[0] + '.iconset'
shutil.rmtree(iconset, ignore_errors=True)
os.makedirs(iconset)
for size in (16, 32, 128, 256, 512):
    for scale in (1, 2):
        px = size * scale
        if meters:
            img = appicon.pixmap(px).toImage()
            img.save(os.path.join(iconset, f'icon_{size}x{size}{"@2x" if scale == 2 else ""}.png'))
            continue
        img = QImage(px, px, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        renderer.render(p)
        p.end()
        img.save(os.path.join(iconset, f'icon_{size}x{size}{"@2x" if scale == 2 else ""}.png'))
subprocess.run(['iconutil', '-c', 'icns', '-o', icns, iconset], check=True)
shutil.rmtree(iconset)
