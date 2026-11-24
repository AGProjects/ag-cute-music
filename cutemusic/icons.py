"""The transport icons, drawn (not font glyphs: ⏮ ⏭ come from a fallback font whose bar does not line up with its
triangles): previous, play, pause, next, in the palette's text colour, sharp at any scale."""

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QApplication


__all__ = ['transport_icon', 'line_icon', 'set_line_icon', 'refresh_line_icons']


SIZE = 24       # the icon's box; the shapes share one height so they line up across the buttons


def _triangle(path, left, right, top, bottom, pointing_right=True):
    middle = (top + bottom) / 2
    if pointing_right:
        path.moveTo(left, top)
        path.lineTo(right, middle)
        path.lineTo(left, bottom)
    else:
        path.moveTo(right, top)
        path.lineTo(left, middle)
        path.lineTo(right, bottom)
    path.closeSubpath()


def _path(name):
    path = QPainterPath()
    top, bottom = 6.0, 18.0             # the same height for all
    if name == 'play':
        _triangle(path, 8.0, 18.5, 5.0, 19.0)
    elif name == 'pause':
        path.addRoundedRect(QRectF(7.0, 5.0, 3.6, 14.0), 0.8, 0.8)
        path.addRoundedRect(QRectF(13.4, 5.0, 3.6, 14.0), 0.8, 0.8)
    elif name == 'next':
        _triangle(path, 4.5, 11.5, top, bottom)
        _triangle(path, 11.5, 18.5, top, bottom)
        path.addRect(QRectF(18.5, top, 2.2, bottom - top))     # the bar exactly as tall as the triangles, touching them
    elif name == 'previous':
        path.addRect(QRectF(3.3, top, 2.2, bottom - top))
        _triangle(path, 5.5, 12.5, top, bottom, pointing_right=False)
        _triangle(path, 12.5, 19.5, top, bottom, pointing_right=False)
    return path


def transport_icon(name, color=None):
    color = QColor(color) if color is not None else QApplication.palette().buttonText().color()
    icon = QIcon()
    for scale in (1, 2, 3):
        pixmap = QPixmap(SIZE * scale, SIZE * scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(scale, scale)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPath(_path(name).simplified())
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        icon.addPixmap(pixmap)
    return icon


# Line icons for the segmented buttons (library modes, views, VU styles): stroked, in a colour, sharp at any scale

def _line_path(name):
    path = QPainterPath()
    if name == 'genre':             # a music note
        path.addEllipse(QPointF(8.0, 17.0), 3.0, 2.4)
        path.moveTo(11.0, 17.0)
        path.lineTo(11.0, 5.0)
        path.lineTo(18.0, 7.0)
        path.lineTo(18.0, 10.0)
        path.lineTo(11.0, 8.0)
    elif name == 'year':            # a calendar
        path.addRoundedRect(QRectF(4.5, 6.0, 15.0, 13.5), 2.0, 2.0)
        path.moveTo(4.5, 10.0)
        path.lineTo(19.5, 10.0)
        for x in (8.5, 15.5):
            path.moveTo(x, 4.0)
            path.lineTo(x, 7.5)
    elif name == 'artist':          # a person
        path.addEllipse(QPointF(12.0, 8.5), 3.5, 3.5)
        path.moveTo(5.0, 19.5)
        path.cubicTo(5.5, 14.0, 18.5, 14.0, 19.0, 19.5)
    elif name == 'stopwatch':       # a chronograph: its case, the crown and its push button, a hand
        path.addEllipse(QPointF(12.0, 13.5), 7.0, 7.0)
        path.moveTo(10.0, 4.0)
        path.lineTo(14.0, 4.0)
        path.moveTo(12.0, 4.0)
        path.lineTo(12.0, 6.5)
        path.moveTo(17.3, 7.2)
        path.lineTo(18.8, 5.7)
        path.moveTo(12.0, 13.5)
        path.lineTo(12.0, 9.5)
    elif name == 'album':           # a disc
        path.addEllipse(QPointF(12.0, 12.0), 7.5, 7.5)
        path.addEllipse(QPointF(12.0, 12.0), 2.0, 2.0)
    elif name == 'speaker':         # the output device
        path.moveTo(4.5, 9.5)
        path.lineTo(8.0, 9.5)
        path.lineTo(12.5, 5.5)
        path.lineTo(12.5, 18.5)
        path.lineTo(8.0, 14.5)
        path.lineTo(4.5, 14.5)
        path.closeSubpath()
        path.arcMoveTo(QRectF(10.5, 8.0, 8.0, 8.0), 50.0)
        path.arcTo(QRectF(10.5, 8.0, 8.0, 8.0), 50.0, -100.0)
        path.arcMoveTo(QRectF(9.0, 4.5, 13.0, 15.0), 55.0)
        path.arcTo(QRectF(9.0, 4.5, 13.0, 15.0), 55.0, -110.0)
    elif name == 'muted':           # the speaker, crossed out
        path.moveTo(4.5, 9.5)
        path.lineTo(8.0, 9.5)
        path.lineTo(12.5, 5.5)
        path.lineTo(12.5, 18.5)
        path.lineTo(8.0, 14.5)
        path.lineTo(4.5, 14.5)
        path.closeSubpath()
        path.moveTo(15.0, 9.5)
        path.lineTo(20.0, 14.5)
        path.moveTo(20.0, 9.5)
        path.lineTo(15.0, 14.5)
    elif name == 'computer':        # a screen on a stand: where the library is
        path.addRoundedRect(QRectF(4.0, 5.0, 16.0, 11.0), 1.5, 1.5)
        path.moveTo(12.0, 16.0)
        path.lineTo(12.0, 19.0)
        path.moveTo(8.0, 19.5)
        path.lineTo(16.0, 19.5)
    elif name == 'folder':
        path.moveTo(4.0, 7.0)
        path.lineTo(9.5, 7.0)
        path.lineTo(11.0, 9.0)
        path.lineTo(20.0, 9.0)
        path.lineTo(20.0, 18.5)
        path.lineTo(4.0, 18.5)
        path.closeSubpath()
    elif name == 'cover':           # a record in its sleeve
        path.addRoundedRect(QRectF(4.0, 4.0, 16.0, 16.0), 1.5, 1.5)
        path.addEllipse(QPointF(12.0, 12.0), 5.0, 5.0)
        path.addEllipse(QPointF(12.0, 12.0), 1.2, 1.2)
    elif name == 'lyrics':          # lines of text
        for y, right in ((7.0, 19.0), (11.0, 16.0), (15.0, 19.0), (19.0, 13.0)):
            path.moveTo(5.0, y)
            path.lineTo(right, y)
    elif name == 'vu':              # a gauge
        path.arcMoveTo(QRectF(4.0, 6.0, 16.0, 16.0), 160.0)
        path.arcTo(QRectF(4.0, 6.0, 16.0, 16.0), 160.0, -140.0)
        path.moveTo(12.0, 14.0)
        path.lineTo(16.0, 9.0)
    elif name == 'lava':            # a lava lamp: its glass between a cap and a base, a blob in it
        path.moveTo(10.0, 4.0)
        path.lineTo(14.0, 4.0)
        path.cubicTo(14.0, 9.0, 16.5, 11.0, 16.5, 15.0)
        path.lineTo(7.5, 15.0)
        path.cubicTo(7.5, 11.0, 10.0, 9.0, 10.0, 4.0)
        path.moveTo(8.0, 15.0)
        path.lineTo(6.5, 20.0)
        path.lineTo(17.5, 20.0)
        path.lineTo(16.0, 15.0)
        path.addEllipse(QPointF(12.0, 11.5), 1.6, 1.6)
    elif name == 'amp':             # a knob, its pointer and scale
        path.addEllipse(QPointF(12.0, 13.0), 5.0, 5.0)
        path.moveTo(12.0, 13.0)
        path.lineTo(14.8, 10.2)
        path.arcMoveTo(QRectF(3.5, 4.5, 17.0, 17.0), 225.0)
        path.arcTo(QRectF(3.5, 4.5, 17.0, 17.0), 225.0, -270.0)
    elif name == 'mixer':           # three faders, their caps at different heights
        for x, cap in ((7.0, 9.0), (12.0, 15.0), (17.0, 11.0)):
            path.moveTo(x, 4.5)
            path.lineTo(x, 19.5)
            path.addRect(QRectF(x - 2.0, cap - 1.0, 4.0, 2.0))
    elif name == 'bars':            # horizontal level bars
        for y, right in ((9.0, 19.0), (15.0, 14.0)):
            path.moveTo(5.0, y)
            path.lineTo(right, y)
    elif name == 'needles':         # an analog meter's face and needle
        path.addRoundedRect(QRectF(4.0, 6.0, 16.0, 12.0), 2.0, 2.0)
        path.moveTo(9.0, 16.0)
        path.lineTo(15.5, 10.0)
    elif name == 'leds':            # a stack of segments
        for y in (5.0, 9.0, 13.0, 17.0):
            path.addRect(QRectF(9.0, y, 6.0, 2.5))
    # the mixer's knobs (around MUSIC's SOURCE and MICROPHONE's INPUT): what a position is
    elif name == 'globe':           # a browser
        path.addEllipse(QPointF(12.0, 12.0), 7.5, 7.5)
        path.addEllipse(QPointF(12.0, 12.0), 3.2, 7.5)
        path.moveTo(4.5, 12.0)
        path.lineTo(19.5, 12.0)
    elif name == 'chat':            # a messenger: a speech bubble
        path.moveTo(5.0, 6.0)
        path.lineTo(19.0, 6.0)
        path.lineTo(19.0, 15.0)
        path.lineTo(11.0, 15.0)
        path.lineTo(7.0, 19.0)
        path.lineTo(7.5, 15.0)
        path.lineTo(5.0, 15.0)
        path.closeSubpath()
    elif name == 'video':           # a video player: a screen with a play triangle
        path.addRoundedRect(QRectF(4.0, 6.0, 16.0, 12.0), 1.5, 1.5)
        path.moveTo(10.5, 9.5)
        path.lineTo(14.5, 12.0)
        path.lineTo(10.5, 14.5)
        path.closeSubpath()
    elif name == 'window':          # any other app: a window
        path.addRoundedRect(QRectF(4.5, 5.5, 15.0, 13.0), 1.5, 1.5)
        path.moveTo(4.5, 9.0)
        path.lineTo(19.5, 9.0)
    elif name == 'laptop':          # the built-in microphone
        path.addRoundedRect(QRectF(6.0, 6.0, 12.0, 9.0), 1.0, 1.0)
        path.moveTo(3.5, 18.0)
        path.lineTo(20.5, 18.0)
        path.lineTo(19.0, 15.5)
        path.lineTo(5.0, 15.5)
        path.closeSubpath()
    elif name == 'mic':             # a microphone (USB, plugged in)
        path.addRoundedRect(QRectF(9.5, 3.5, 5.0, 10.0), 2.5, 2.5)
        path.arcMoveTo(QRectF(6.5, 6.0, 11.0, 10.5), 180.0)
        path.arcTo(QRectF(6.5, 6.0, 11.0, 10.5), 180.0, 180.0)
        path.moveTo(12.0, 16.5)
        path.lineTo(12.0, 20.0)
        path.moveTo(9.0, 20.0)
        path.lineTo(15.0, 20.0)
    elif name == 'headset':         # a headset (Bluetooth, AirPods)
        path.arcMoveTo(QRectF(5.0, 4.5, 14.0, 14.0), 180.0)
        path.arcTo(QRectF(5.0, 4.5, 14.0, 14.0), 180.0, -180.0)
        path.addRoundedRect(QRectF(4.5, 11.5, 3.5, 6.0), 1.2, 1.2)
        path.addRoundedRect(QRectF(16.0, 11.5, 3.5, 6.0), 1.2, 1.2)
        path.moveTo(17.5, 17.5)
        path.cubicTo(17.5, 20.0, 15.0, 20.5, 12.5, 20.5)
    elif name == 'fullscreen':      # full screen: two arrows on the diagonal, pointing out to the corners
        for tip, middle, side in (((19.5, 4.5), (13.5, 10.5), 1), ((4.5, 19.5), (10.5, 13.5), -1)):
            path.moveTo(*middle)
            path.lineTo(*tip)
            path.moveTo(tip[0] - side * 4.5, tip[1])        # the arrowhead, its two arms along the axes
            path.lineTo(*tip)
            path.lineTo(tip[0], tip[1] + side * 4.5)
    elif name == 'windowed':        # leave full screen: two arrows on the diagonal, pointing in to the middle
        for corner, tip, side in (((19.5, 4.5), (13.5, 10.5), 1), ((4.5, 19.5), (10.5, 13.5), -1)):
            path.moveTo(*corner)
            path.lineTo(*tip)
            path.moveTo(tip[0], tip[1] - side * 4.5)        # the arrowhead, its two arms along the axes
            path.lineTo(*tip)
            path.lineTo(tip[0] + side * 4.5, tip[1])
    elif name == 'bluetooth':       # a Bluetooth device: its rune
        path.moveTo(7.5, 8.0)
        path.lineTo(16.5, 16.0)
        path.lineTo(12.0, 20.0)
        path.lineTo(12.0, 4.0)
        path.lineTo(16.5, 8.0)
        path.lineTo(7.5, 16.0)
    elif name == 'default':         # the system's default output: a circling arrow (it follows the system) round a dot
        ring = QRectF(5.0, 5.0, 14.0, 14.0)
        path.arcMoveTo(ring, 60.0)
        path.arcTo(ring, 60.0, 290.0)
        path.moveTo(19.6, 7.4)          # the arrowhead at its end (the arc's end near 350 degrees)
        path.lineTo(18.9, 11.6)
        path.lineTo(15.2, 9.6)
        path.addEllipse(QRectF(10.6, 10.6, 2.8, 2.8))
    elif name == 'usb':             # a USB device: its trident
        path.moveTo(12.0, 20.0)
        path.lineTo(12.0, 6.5)
        path.moveTo(9.5, 7.5)           # the arrowhead
        path.lineTo(12.0, 3.5)
        path.lineTo(14.5, 7.5)
        path.closeSubpath()
        path.moveTo(12.0, 15.5)         # the left arm, a ring at its end
        path.lineTo(7.5, 12.5)
        path.lineTo(7.5, 10.8)
        path.addEllipse(QPointF(7.5, 9.6), 1.4, 1.4)
        path.moveTo(12.0, 13.0)         # the right arm, a square at its end
        path.lineTo(16.5, 10.5)
        path.lineTo(16.5, 9.2)
        path.addRect(QRectF(15.3, 6.8, 2.4, 2.4))
        path.addEllipse(QPointF(12.0, 20.0), 1.5, 1.5)
    elif name == 'more':            # more of them (a menu): three dots
        for x in (6.5, 12.0, 17.5):
            path.addEllipse(QRectF(x - 1.4, 10.6, 2.8, 2.8))
    elif name == 'wave':            # a virtual device (BlackHole, Teams, an app's): a sound wave
        path.moveTo(4.0, 12.0)
        for x, y in ((6.5, 7.0), (9.0, 17.0), (11.5, 5.0), (14.0, 19.0), (16.5, 8.0), (19.0, 15.0), (20.5, 12.0)):
            path.lineTo(x, y)
    elif name == 'spectrum':        # bars of different heights
        for x, top in ((5.5, 13.0), (9.0, 7.0), (12.5, 10.0), (16.0, 5.0), (19.5, 12.0)):
            path.moveTo(x, 19.0)
            path.lineTo(x, top)
    return path


_FILLED = {'leds'}


def line_path(name):
    """A line icon's path, in a 24 x 24 box: for drawing it oneself (the mixer's knobs)."""
    return _line_path(name)


def line_icon(name, color=None):
    color = QColor(color) if color is not None else QApplication.palette().buttonText().color()
    icon = QIcon()
    for scale in (1, 2, 3):
        pixmap = QPixmap(SIZE * scale, SIZE * scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(scale, scale)
        if name in _FILLED:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
        else:
            from PyQt6.QtGui import QPen
            painter.setPen(QPen(color, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(_line_path(name))
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        icon.addPixmap(pixmap)
    return icon


def set_line_icon(button, name, color=None):
    """The icon on button, again in the text colour when the theme changes (refresh_line_icons)."""
    button.setProperty('line_icon', name)
    button.setProperty('line_icon_color', QColor(color).name() if color is not None else '')
    button.setIcon(line_icon(name, color))


def refresh_line_icons(widget):
    from PyQt6.QtWidgets import QAbstractButton
    for button in widget.findChildren(QAbstractButton):
        name = button.property('line_icon')
        if name:
            button.setIcon(line_icon(name, button.property('line_icon_color') or None))
