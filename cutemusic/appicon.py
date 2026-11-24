"""The application's icon: one of the amplifier's needle VU meters (vufaces.AnalogFaces: the face lit from behind,
the scale, the needle, the PEAK lamp) in its black-bezelled window on a dark metal plate, the shape of a macOS icon.
render() draws it at any size with the needle where asked: the static icon (the needle at rest), the live Dock icon
(dockicon.py, moved by the music), the About box, the .icns (make_icns.py).
"""

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QLinearGradient, QPainter, QPen, QPixmap

from cutemusic.vufaces import AnalogFaces


__all__ = ['IconPainter', 'icon', 'pixmap', 'REST', 'PLAYING']

CANVAS = 512.0          # drawn in this square, scaled to the size wanted
ASPECT = 2.2            # the meter's window, its width to its height (the amplifier's faces: 2.6)
REST = AnalogFaces._needle_ratio(AnalogFaces.VU_MIN - 2)       # silence: the needles against their left stop
PLAYING = AnalogFaces._needle_ratio(-10.0)     # the About box's: music playing, left of VU (not across it)


class IconPainter(AnalogFaces):
    """AnalogFaces off a widget: the parts that do not move drawn once a size (static), the needles, the lamps and
    the glass over them each time (render)."""

    def __init__(self):
        self._static = {}

    def font(self):
        return QGuiApplication.font()

    @staticmethod
    def layout():
        margin = CANVAS * 0.098             # macOS's icon grid: the body 824 of 1024
        body = QRectF(margin, margin, CANVAS - 2 * margin, CANVAS - 2 * margin)
        width = body.width() * 0.84
        height = width / ASPECT             # one face, as the amplifier's: much wider than high
        window = QRectF(body.center().x() - width / 2, body.center().y() - height / 2 + body.height() * 0.02,
                        width, height)
        gap = height * 0.015
        return body, window, [window.adjusted(gap, gap, -gap, -gap)]

    def static(self, size):
        cached = self._static.get(size)
        if cached is not None:
            return cached
        body, window, faces = self.layout()
        image = QPixmap(size, size)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.scale(size / CANVAS, size / CANVAS)
        radius = body.width() * 0.225
        painter.setPen(Qt.PenStyle.NoPen)       # its shadow, under it
        painter.setBrush(QColor(0, 0, 0, 70))
        painter.drawRoundedRect(body.translated(0, CANVAS * 0.012), radius, radius)
        metal = QLinearGradient(body.topLeft(), body.bottomLeft())      # the amplifier's dark plate
        metal.setColorAt(0.0, QColor('#3d4046'))
        metal.setColorAt(0.5, QColor('#26282c'))
        metal.setColorAt(1.0, QColor('#17181b'))
        painter.setBrush(metal)
        painter.setPen(QPen(QColor(255, 255, 255, 40), CANVAS * 0.004))
        painter.drawRoundedRect(body, radius, radius)
        painter.setPen(QPen(QColor('#7d8188'), 1.4))      # the window's black bezel, the face lit from behind
        painter.setBrush(QColor('#14161a'))
        painter.drawRoundedRect(window.adjusted(-3, -3, 3, 3), 7, 7)
        self._draw_backlight(painter, faces * 2)       # (lit by one bulb, as each channel's half)
        self._draw_face(painter, faces[0], '')
        painter.end()
        self._static[size] = image
        return image

    def render(self, size, ratio=REST, peak=False):
        """The icon, size pixels square, the needle at ratio (0: -20 VU, 1: +3), the PEAK lamp lit or not."""
        body, window, faces = self.layout()
        image = QPixmap(self.static(size))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(size / CANVAS, size / CANVAS)
        self._draw_needle(painter, faces[0], ratio, peak)
        glare = QLinearGradient(window.topLeft(), window.bottomLeft())      # the glass over them
        glare.setColorAt(0.0, QColor(255, 255, 255, 46))
        glare.setColorAt(0.45, QColor(255, 255, 255, 8))
        glare.setColorAt(0.46, QColor(255, 255, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glare)
        painter.drawRoundedRect(window, 5, 5)
        painter.end()
        return image


_painter = []


def painter():
    if not _painter:
        _painter.append(IconPainter())
    return _painter[0]


def pixmap(size, ratio=REST):
    return painter().render(size, ratio)


def icon():
    """The static icon (the needle at rest), at the sizes the Dock and the windows use."""
    result = QIcon()
    for size in (32, 64, 128, 256, 512):
        result.addPixmap(pixmap(size))
    return result
