"""Render the SVG app icon into a Windows .ico (16 to 256 px): Qt draws it, Pillow writes the .ico.

    python windows/make_ico.py icons/ag-cute-music.svg build/app.ico
"""

import io
import sys

from PIL import Image
from PyQt6.QtCore import QBuffer, QIODevice, Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer

SIZES = (16, 24, 32, 48, 64, 128, 256)

svg, ico = sys.argv[1], sys.argv[2]
app = QGuiApplication(['make_ico'])
renderer = QSvgRenderer(svg)
if not renderer.isValid():
    sys.exit(f'cannot read {svg}')
images = []
for size in SIZES:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    images.append(Image.open(io.BytesIO(bytes(buffer.data()))).convert('RGBA'))
images[-1].save(ico, format='ICO', sizes=[(size, size) for size in SIZES], append_images=images[:-1])
print(f'{ico}: {", ".join(str(size) for size in SIZES)} px')
