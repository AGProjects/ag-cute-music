"""The Lava lamp view: a lamp of glass between a chrome cap and base, its wax in three parts of it, one a band of
the spectrum (BANDS): the lows' blobs revolve at the bottom, the mids' in the middle, the highs' at the top, two in
each, opposite, slowly and steadily as real wax; the music only makes them pulse, the louder their band the more
(a hard note at once); the pool at the bottom follows the lows, the light the music's loudness.
A click: the next colours (kept in the settings, lava/palette). The lyrics being sung under it.

The blobs are metaballs: their field computed on a small grid (numpy when there, else plain Python on a coarser
one), its edge made soft, the picture then scaled up smoothly into the glass.
"""

import math
import random
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PyQt6.QtWidgets import QWidget

from cutemusic import util

try:
    import numpy
except ImportError:
    numpy = None


__all__ = ['LavaLamp']


BACKGROUND = QColor('#101318')
ASPECT = (0.17 / 0.66) ** 2     # a column step against a row step, squared: the glass's half width to its height
# (wax at the bottom, wax at the top, the liquid at the top, the liquid at the bottom), by name
PALETTES = [('lava', '#ffb02e', '#ff3d6e', '#1b0a2c', '#4a1452'),
            ('ocean', '#5ef0ff', '#3d7bff', '#04121f', '#0b3550'),
            ('acid', '#d4ff3a', '#2ee88a', '#0d1a08', '#1f3d12'),
            ('mono', '#ffffff', '#b8c0cc', '#101318', '#2a303a')]


# the spectrum's bands the blobs follow: (Hz from, Hz to, dB of silence-ish, dB of loud) - music is softer up high
BANDS = [(35, 160, -62.0, -22.0), (300, 2000, -68.0, -30.0), (3000, 10000, -80.0, -42.0)]
BAND_SIZE = (1.2, 1.0, 0.8)     # the low blobs a little bigger, the high ones smaller


class _Blob:
    def __init__(self, rng, index, count):
        self.band = index % len(BANDS)
        pair = index // len(BANDS)
        self.angle = rng.uniform(0, math.tau) if pair == 0 else 0.0      # the second of a band: opposite the first
        self.pair = pair
        self.x, self.y = 0.0, (self.band + 0.5) / len(BANDS)         # 0 the bottom, 1 the top
        self.radius = rng.uniform(0.045, 0.07) * BAND_SIZE[self.band]       # of the glass's height
        self.phase = rng.uniform(0, math.tau)
        self.drift = rng.uniform(0.15, 0.35)


class LavaLamp(QWidget):
    FPS = 30
    FILL = 0.82         # the lamp's height, of the room it has (the dock's: more, little room round it)
    TURN = 0.125        # rad/s a blob goes round: about 50 s a turn, whatever the music
    BLOBS = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(160, 200)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('The lights on the base: the colours (a click elsewhere: the next ones)')
        rng = random.Random()
        self.blobs = [_Blob(rng, index, self.BLOBS) for index in range(self.BLOBS)]
        self.level = 0.0            # the music's loudness, 0..1, smoothed
        self.bands = [0.0] * len(BANDS)           # each band's strength, 0..1, smoothed
        self._band_targets = [0.0] * len(BANDS)
        self.kicks = [0.0] * len(BANDS)           # a hard note's bump on top, dying away fast
        self._level_target = 0.0
        self.received = 0.0
        index = util.settings().value('lava/palette', 0, type=int)
        self.palette_index = index if 0 <= index < len(PALETTES) else 0
        self.last = time.monotonic()
        self.clock = 0.0
        self.timer = QTimer(self, interval=1000 // self.FPS)
        self.timer.timeout.connect(self._tick)
        # not seen (its window minimized, on another desktop, or another page over it): the wax stands still, and
        # this looks once a second for when it is seen again
        self._watch = QTimer(self, interval=1000)
        self._watch.timeout.connect(self._look)
        from cutemusic.visuals import LyricsStrip
        self.lyrics = LyricsStrip(self)         # hidden when there are none: the lamp takes the room

    def show_track(self, track):
        self.lyrics.show_track(track)
        self._place_lyrics()
        self.update()

    def set_position(self, seconds, duration=0.0):
        self.lyrics.set_position(seconds, duration)

    def _lyrics_height(self):
        return self.lyrics.sizeHint().height() if not self.lyrics.isHidden() else 0

    def _place_lyrics(self):
        height = self._lyrics_height()
        self.lyrics.setGeometry(0, self.height() - height - 8, self.width(), height)

    def resizeEvent(self, event):
        self._place_lyrics()

    # From the player

    def set_levels(self, rms, peak):
        if not rms:
            self._level_target = 0.0
            return
        loudest = max(value for value in rms if not math.isnan(value)) if any(not math.isnan(v) for v in rms) else -90
        self._level_target = max(0.0, min(1.0, (loudest + 50) / 44))       # -50..-6 dBFS
        self.received = time.monotonic()

    def set_spectrum(self, magnitudes, rate):
        if not magnitudes or not rate:
            self._band_targets = [0.0] * len(BANDS)
            return
        width = rate / 2 / len(magnitudes)
        targets = []
        for low, high, quiet, loud in BANDS:
            first, last = max(0, int(low / width)), min(len(magnitudes) - 1, int(high / width))
            strongest = max(magnitudes[first:last + 1]) if last >= first else magnitudes[first]
            targets.append(max(0.0, min(1.0, (strongest - quiet) / (loud - quiet))))
        # a hard note: the band jumping up from one spectrum to the next (25 ms): a kick, the bass's the strongest
        for band, (before, now) in enumerate(zip(self._band_targets, targets)):
            if now - before > 0.1:
                self.kicks[band] = min(1.0, self.kicks[band] + (now - before) * (3.0 if band == 0 else 2.0))
        self._band_targets = targets

    # Showing

    def showEvent(self, event):
        self._run()

    def hideEvent(self, event):
        self.timer.stop()
        self._watch.stop()

    def _seen(self):
        from cutemusic.util import on_screen
        return on_screen(self)

    def _run(self):
        if self._seen():
            self._watch.stop()
            if not self.timer.isActive():
                self.last = time.monotonic()
                self.timer.start()
        else:
            self.timer.stop()
            self._watch.start()

    def _look(self):
        if not self.isVisible():        # hidden: showEvent starts it again
            self._watch.stop()
        elif self._seen():
            self._run()

    def _buttons(self, glass, base):
        """[(centre, radius, width)] of the colour switch's lights on the base, a palette each: round the cone, as
        a ring on it seen a little from above (the base's ovals): evenly apart round it, so the middle ones lower,
        the outer ones higher, nearer each other and narrower (turned away)."""
        count = len(PALETTES)
        at = 0.42                                           # how far down the base
        neck, foot = glass.width() * 0.42, base.width() / 2
        half = neck + (foot - neck) * at                    # the cone's half width there
        sag = glass.width() * 0.09 * (half / (glass.width() / 2)) * 0.55    # the ring's oval, half its height
        y = base.top() + base.height() * at
        radius = max(3.0, min(glass.width() * 0.05, base.height() * 0.09))
        spread = math.radians(36)                           # the outer lights this far round from the front
        lights = []
        for index in range(count):
            angle = spread * (index / (count - 1) * 2 - 1) if count > 1 else 0.0
            point = QPointF(glass.center().x() + half * math.sin(angle), y + sag * math.cos(angle))
            lights.append((point, radius, radius * (0.55 + 0.45 * math.cos(angle))))
        return lights

    def mouseReleaseEvent(self, event):
        """On a light of the base: its colours; elsewhere: the next ones."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        glass, cap, base = self._geometry()
        chosen = next((index for index, (point, radius, width) in enumerate(self._buttons(glass, base))
                       if math.hypot(event.position().x() - point.x(), event.position().y() - point.y()) <= radius * 2), None)
        self.palette_index = chosen if chosen is not None else (self.palette_index + 1) % len(PALETTES)
        util.settings().setValue('lava/palette', self.palette_index)
        self.update()

    # The wax

    def _tick(self):
        if not self._seen():
            self._run()
            return
        now = time.monotonic()
        dt, self.last = min(0.1, now - self.last), now
        self.clock += dt
        if now - self.received > 0.4:           # paused, stopped: it keeps revolving, slowly
            self._level_target = 0.0
            self._band_targets = [0.0] * len(BANDS)
        self.level += (self._level_target - self.level) * min(1.0, dt / 0.6)
        for band, target in enumerate(self._band_targets):     # up at once (the lows the fastest), down slower
            value = self.bands[band]
            attack = 0.02 if band == 0 else 0.04
            self.bands[band] = value + (target - value) * min(1.0, dt / (attack if target > value else 0.3))
            self.kicks[band] *= math.exp(-dt / 0.15)
        # each band's blobs revolve in its part of the lamp (the lows at the bottom): an ellipse round its middle,
        # slowly and steadily, as real wax (one turn in about 50 s); the music only makes them pulse
        firsts = {}
        for blob in self.blobs:
            if blob.pair == 0:
                blob.angle += dt * self.TURN * (1 if blob.band % 2 == 0 else -1)
                firsts[blob.band] = blob.angle
            else:
                blob.angle = firsts.get(blob.band, blob.angle) + math.pi
            middle = (blob.band + 0.5) / len(BANDS)
            height = 0.5 / len(BANDS) * 0.6
            blob.y = middle + math.sin(blob.angle) * height + math.sin(self.clock * blob.drift * 0.3 + blob.phase) * 0.015
            blob.x = math.cos(blob.angle) * 0.42 * self._width_at(blob.y)
        self.update()

    @staticmethod
    def _width_at(y):
        """The glass's half width at y (0 the bottom, 1 the top), as a part of its widest."""
        return 0.62 + 0.38 * math.sin(math.pi * min(1.0, (1 - y) * 0.85 + 0.05)) if y > 0 else 0.8

    def _grid(self, glass):
        """(columns, rows) the blobs are computed on: a cell every 2 pixels of the glass with numpy (at most 220
        across), coarser in plain Python (at most 40 across); the picture is then scaled up smoothly."""
        ratio = self.devicePixelRatioF()
        if numpy is not None:
            columns = int(max(40, min(220, glass.width() * ratio / 2)))
        else:
            columns = int(max(24, min(40, glass.width() * ratio / 6)))
        return columns, int(columns * glass.height() / max(1.0, glass.width()))

    def _field_image(self, glass):
        """The blobs as an image (the grid's size): their colour, transparent outside, a soft edge."""
        columns, rows = self._grid(glass)
        bottom, top, liquid_top, liquid_bottom = (QColor(color) for color in PALETTES[self.palette_index][1:])
        # a pool at the bottom (the wax resting there) and the blobs; distances as on the glass: its columns span
        # -1..1 of its width, its rows 0..1 of its height (about 3.9 times the half width)
        kick = (0.55, 0.3, 0.25)        # how much a hard note swells each band's blobs, over their band's level
        blobs = [(0.0, -0.04, 0.1 * (1 + 0.25 * self.bands[0] + 0.35 * self.kicks[0]), 0.7)]
        blobs += [(blob.x, blob.y, blob.radius * (1 + 0.4 * self.bands[blob.band] + kick[blob.band] * self.kicks[blob.band]), 1.0)
                  for blob in self.blobs]
        if numpy is not None:
            xs = numpy.linspace(-1, 1, columns)[None, :]
            ys = numpy.linspace(1, 0, rows)[:, None]
            field = numpy.zeros((rows, columns))
            for x, y, radius, weight in blobs:
                field += weight * radius ** 2 / ((xs - x) ** 2 * ASPECT + (ys - y) ** 2 + 1e-4)
            alpha = numpy.clip((field - 1.0) / 0.3, 0, 1)
            shade = numpy.clip((field - 1.0) / 3.0, 0, 1) * 0.18      # the inside a little brighter: lit through
            mix = numpy.repeat(ys, columns, axis=1)
            image = numpy.empty((rows, columns, 4), numpy.uint8)
            for channel, (low, high) in enumerate(((bottom.red(), top.red()), (bottom.green(), top.green()),
                                                     (bottom.blue(), top.blue()))):
                value = low + (high - low) * mix
                image[:, :, channel] = numpy.clip(value + (255 - value) * shade, 0, 255).astype(numpy.uint8)
            image[:, :, 3] = (alpha * 255).astype(numpy.uint8)
            data = image.tobytes()
        else:
            data = bytearray(columns * rows * 4)
            for row in range(rows):
                y = 1 - row / (rows - 1)
                red = bottom.red() + (top.red() - bottom.red()) * y
                green = bottom.green() + (top.green() - bottom.green()) * y
                blue = bottom.blue() + (top.blue() - bottom.blue()) * y
                near = [(bx, (y - by) ** 2, weight * radius ** 2) for bx, by, radius, weight in blobs if abs(y - by) < radius * 3]
                for column in range(columns):
                    x = column / (columns - 1) * 2 - 1
                    field = 0.0
                    for bx, dy2, strength in near:
                        field += strength / ((x - bx) ** 2 * ASPECT + dy2 + 1e-4)
                    if field <= 1.0:
                        continue
                    shade = min(1.0, max(0.0, (field - 1.0) / 3.0)) * 0.18
                    at = (row * columns + column) * 4
                    data[at] = int(red + (255 - red) * shade)
                    data[at + 1] = int(green + (255 - green) * shade)
                    data[at + 2] = int(blue + (255 - blue) * shade)
                    data[at + 3] = int(min(1.0, (field - 1.0) / 0.3) * 255)
            data = bytes(data)
        image = QImage(data, columns, rows, columns * 4, QImage.Format.Format_RGBA8888)
        return image.copy()         # its own copy of the bytes

    # Drawing

    def _geometry(self):
        """The glass's rectangle (its widest), the cap's and the base's: the lamp in the room the lyrics leave."""
        room = self.height() - self._lyrics_height() - (16 if self._lyrics_height() else 0)
        height = room * self.FILL
        width = min(self.width() * 0.7, height * 0.34)
        height = min(height, width / 0.34)
        left, top = (self.width() - width) / 2, (room - height) / 2
        cap = QRectF(left + width * 0.22, top, width * 0.56, height * 0.1)
        glass = QRectF(left, cap.bottom(), width, height * 0.66)
        base = QRectF(left - width * 0.06, glass.bottom(), width * 1.12, height * 0.24)
        return glass, cap, base

    def _glass_path(self, glass):
        """The glass: narrow at the top, widest low, narrowing a little into the base."""
        top_half, low_half, bottom_half = glass.width() * 0.28, glass.width() * 0.5, glass.width() * 0.4
        centre = glass.center().x()
        widest = glass.top() + glass.height() * 0.78
        path = QPainterPath(QPointF(centre - top_half, glass.top()))
        path.cubicTo(QPointF(centre - top_half, glass.top() + glass.height() * 0.3),
                     QPointF(centre - low_half, glass.top() + glass.height() * 0.55), QPointF(centre - low_half, widest))
        path.quadTo(QPointF(centre - low_half, glass.bottom()), QPointF(centre - bottom_half, glass.bottom()))
        path.lineTo(QPointF(centre + bottom_half, glass.bottom()))
        path.quadTo(QPointF(centre + low_half, glass.bottom()), QPointF(centre + low_half, widest))
        path.cubicTo(QPointF(centre + low_half, glass.top() + glass.height() * 0.55),
                     QPointF(centre + top_half, glass.top() + glass.height() * 0.3), QPointF(centre + top_half, glass.top()))
        path.closeSubpath()
        return path

    # Drawing: what does not move (the room's glow, the glass's shading, the chrome, the lights) drawn once a
    # size and colours into two layers, under and over; a frame paints the liquid and the wax between them

    _layers = None
    _layers_key = None

    def paintEvent(self, event):
        glass, cap, base = self._geometry()
        key = (self.width(), self.height(), self.devicePixelRatioF(), self.palette_index, self._lyrics_height())
        if self._layers is None or self._layers_key != key:
            self._layers, self._layers_key = self._draw_layers(glass, cap, base), key
        under, over = self._layers
        name, bottom, top, liquid_top, liquid_bottom = PALETTES[self.palette_index]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(0, 0, under)
        path = self._glass_path(glass)
        # the liquid, lit from the bulb under it, brighter with the music
        liquid = QLinearGradient(0, glass.top(), 0, glass.bottom())
        liquid.setColorAt(0.0, QColor(liquid_top))
        liquid.setColorAt(1.0, QColor(liquid_bottom).lighter(100 + int(40 * self.level)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(liquid)
        painter.drawPath(path)
        # the wax, inside the glass
        painter.save()
        painter.setClipPath(path)
        painter.drawImage(QRectF(glass.left(), glass.top(), glass.width(), glass.height()), self._field_image(glass))
        painter.restore()
        painter.drawPixmap(0, 0, over)

    def _draw_layers(self, glass, cap, base):
        """(under, over): the room (its colour, the lamp's glow) under the glass; the glass's shading, the cap, the
        base and its lights over it."""
        ratio = self.devicePixelRatioF()
        name, bottom, top, liquid_top, liquid_bottom = PALETTES[self.palette_index]
        level = 0.4         # the light the layers are drawn with: the music's middle
        under = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
        under.setDevicePixelRatio(ratio)
        under.fill(BACKGROUND)
        painter = QPainter(under)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        glow = QRadialGradient(glass.center(), glass.height() * 0.9)       # its light on the room
        color = QColor(bottom)
        color.setAlpha(int(28 + 50 * level))
        glow.setColorAt(0.0, color)
        color.setAlpha(0)
        glow.setColorAt(1.0, color)
        painter.fillRect(QRectF(0, 0, self.width(), self.height()), glow)
        painter.end()
        over = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
        over.setDevicePixelRatio(ratio)
        over.fill(Qt.GlobalColor.transparent)
        painter = QPainter(over)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = self._glass_path(glass)
        centre = glass.center().x()
        squash = glass.width() * 0.09        # the ovals of the round parts' ends, seen a little from above
        # the glass, round: darker towards its sides, a broad soft reflection on the left, a thin sharp one, a rim
        painter.setPen(Qt.PenStyle.NoPen)
        body = QLinearGradient(glass.left(), 0, glass.right(), 0)
        for stop, alpha in ((0.0, 190), (0.12, 80), (0.35, 0), (0.7, 0), (0.88, 90), (1.0, 200)):
            body.setColorAt(stop, QColor(0, 0, 0, alpha))
        painter.setBrush(body)
        painter.drawPath(path)
        shine = QLinearGradient(glass.left(), 0, glass.right(), 0)
        for stop, alpha in ((0.12, 0), (0.18, 40), (0.25, 14), (0.3, 0), (0.78, 0), (0.81, 40), (0.83, 0)):
            shine.setColorAt(stop, QColor(255, 255, 255, alpha))
        painter.setBrush(shine)
        painter.drawPath(path)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1.0))
        painter.drawPath(path)
        warm = QColor(bottom)           # the wax's light, caught by the metal
        # the cap: a cone, its flat top an oval, chrome lit from the left, the wax's colour low on it
        cap_top, cap_bottom = cap.width() * 0.32, glass.width() * 0.3
        cone = QPainterPath(QPointF(centre - cap_top, cap.top()))
        cone.lineTo(QPointF(centre - cap_bottom, cap.bottom()))
        cone.arcTo(QRectF(centre - cap_bottom, cap.bottom() - squash / 2, cap_bottom * 2, squash), 180, 180)
        cone.lineTo(QPointF(centre + cap_top, cap.top()))
        cone.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._chrome(QRectF(centre - cap_bottom, cap.top(), cap_bottom * 2, cap.height())))
        painter.drawPath(cone)
        tint = QLinearGradient(0, cap.top(), 0, cap.bottom() + squash / 2)
        tint.setColorAt(0.5, QColor(warm.red(), warm.green(), warm.blue(), 0))
        tint.setColorAt(1.0, QColor(warm.red(), warm.green(), warm.blue(), int(60 + 50 * level)))
        painter.setBrush(tint)
        painter.drawPath(cone)
        face = QLinearGradient(centre - cap_top, 0, centre + cap_top, 0)
        face.setColorAt(0.0, QColor('#c9ccd1'))
        face.setColorAt(0.5, QColor('#f4f5f7'))
        face.setColorAt(1.0, QColor('#9da1a8'))
        painter.setBrush(face)
        painter.drawEllipse(QRectF(centre - cap_top, cap.top() - squash * 0.35, cap_top * 2, squash * 0.7))
        # its shadow on the table
        shadow = QRadialGradient(QPointF(centre, base.bottom()), base.width() * 0.75)
        shadow.setColorAt(0.0, QColor(0, 0, 0, 150))
        shadow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(shadow)
        painter.drawEllipse(QRectF(base.left() - base.width() * 0.2, base.bottom() - squash * 0.9, base.width() * 1.4, squash * 2.2))
        # the base: anthracite plastic (satin: a soft sheen, no mirror), a cone widening down, round at its bottom,
        # the wax's light on it
        neck, foot = glass.width() * 0.42, base.width() / 2
        flare = QPainterPath(QPointF(centre - neck, base.top()))
        flare.lineTo(QPointF(centre - foot, base.bottom()))
        flare.arcTo(QRectF(centre - foot, base.bottom() - squash * 0.75, foot * 2, squash * 1.5), 180, 180)
        flare.lineTo(QPointF(centre + neck, base.top()))
        flare.closeSubpath()
        painter.setBrush(self._plastic(base))
        painter.drawPath(flare)
        tint = QLinearGradient(0, base.top(), 0, base.bottom())
        tint.setColorAt(0.0, QColor(warm.red(), warm.green(), warm.blue(), int(40 + 40 * level)))
        tint.setColorAt(0.45, QColor(warm.red(), warm.green(), warm.blue(), 0))
        painter.setBrush(tint)
        painter.drawPath(flare)
        # the collar the glass stands in: a ring, its ends ovals
        collar_height = base.height() * 0.1
        collar = QPainterPath(QPointF(centre - neck * 1.02, base.top()))
        collar.arcTo(QRectF(centre - neck * 1.02, base.top() - squash * 0.35, neck * 2.04, squash * 0.7), 180, 180)
        collar.lineTo(QPointF(centre + neck * 1.02, base.top() + collar_height))
        collar.arcTo(QRectF(centre - neck * 1.02, base.top() + collar_height - squash * 0.35, neck * 2.04, squash * 0.7), 0, -180)
        collar.closeSubpath()
        painter.setBrush(self._chrome(QRectF(centre - neck * 1.02, base.top(), neck * 2.04, collar_height), dark=True))
        painter.drawPath(collar)
        # the colour switch: a push key a palette, as an old radio's: dark plastic, lit from above, a lamp window
        # in its wax's colour; the chosen one pressed in, its lamp lit and glowing; the others up, their lamps dim
        for index, (point, radius, width) in enumerate(self._buttons(glass, base)):
            color = QColor(PALETTES[index][1])
            chosen = index == self.palette_index
            key_width, key_height = width * 2.1, radius * 1.7
            travel = radius * 0.22 if chosen else 0.0          # pressed in: lower, its shadow nearly gone
            key = QRectF(point.x() - key_width / 2, point.y() - key_height / 2 + travel, key_width, key_height)
            corner = radius * 0.35
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 60 if chosen else 120))       # its shadow on the base
            painter.drawRoundedRect(key.translated(0, radius * (0.12 if chosen else 0.35)), corner, corner)
            plastic = QLinearGradient(0, key.top(), 0, key.bottom())
            for stop, shade in ((0.0, '#5a6069' if not chosen else '#3a3f46'), (0.12, '#3a3f46'), (0.85, '#22262b'), (1.0, '#14161a')):
                plastic.setColorAt(stop, QColor(shade))
            painter.setBrush(plastic)
            painter.drawRoundedRect(key, corner, corner)
            window = QRectF(key.left() + key_width * 0.22, key.top() + key_height * 0.28, key_width * 0.56, key_height * 0.3)
            if chosen:
                halo = QRadialGradient(window.center(), key_width)
                halo.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 120))
                halo.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
                painter.setBrush(halo)
                painter.drawEllipse(window.center(), key_width, key_width)
                lamp = QLinearGradient(0, window.top(), 0, window.bottom())
                lamp.setColorAt(0.0, color.lighter(160))
                lamp.setColorAt(1.0, color)
            else:
                dim = color.darker(230)
                lamp = QLinearGradient(0, window.top(), 0, window.bottom())
                lamp.setColorAt(0.0, dim.lighter(130))
                lamp.setColorAt(1.0, dim)
            painter.setBrush(lamp)
            painter.drawRoundedRect(window, window.height() / 2, window.height() / 2)
        painter.end()
        return under, over

    @staticmethod
    def _plastic(rect):
        """Anthracite plastic, round: dark at its sides, a broad soft sheen left of the middle."""
        gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        for stop, color in ((0.0, '#121417'), (0.2, '#2c3036'), (0.36, '#4a5058'), (0.5, '#3a3f46'),
                            (0.75, '#24282d'), (1.0, '#0e1012')):
            gradient.setColorAt(stop, QColor(color))
        return gradient

    @staticmethod
    def _chrome(rect, dark=False):
        gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        stops = ((0.0, '#3a3d42'), (0.18, '#d9dce0'), (0.32, '#ffffff'), (0.55, '#8d9198'), (0.8, '#c3c6cb'), (1.0, '#2b2e33'))
        for stop, color in stops:
            gradient.setColorAt(stop, QColor(color).darker(140) if dark else QColor(color))
        return gradient
