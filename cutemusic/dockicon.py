"""The Dock icon a live VU meter (macOS): the app icon's needle (appicon.py: one of the amplifier's meters) moved by
the music as the amplifier's are, a mass on a spring following the louder channel, its 0 VU following the music's
loudness (a trim pot turned by itself). The parts that do not move are drawn once; the needle, the PEAK lamp and the
glass a frame at a time while it moves. At rest (silence, stopped) the timer stops.

Only macOS: its Dock shows the application's icon as it is set; Linux docks take theirs from the .desktop file."""

import math
import sys
import time

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from cutemusic import appicon
from cutemusic.vufaces import AnalogFaces


__all__ = ['DockIcon', 'available']


def available():
    return sys.platform == 'darwin'


class DockIcon(QObject):
    SIZE = 256              # the Dock's largest (128 pt at 2x)
    FPS = 20
    # as the amplifier's needles (amp.py)
    SPRING, DAMPING = 16.0, 0.78
    HEADROOM = 3.0
    TRIM_UP, TRIM_DOWN = 1.0, 10.0
    TRIM_RANGE = (-24.0, 0.0)
    FLOOR = -70.0
    STALE = 0.3             # s without levels: silence

    def __init__(self, parent=None):
        super().__init__(parent)
        self.static = QApplication.windowIcon()
        self.enabled = False
        self.level = self.FLOOR
        self.received = 0.0
        self.reference = -18.0
        self.rest = appicon.REST
        self.position, self.speed = self.rest, 0.0
        self.drawn = None
        self.last = time.monotonic()
        self.timer = QTimer(self, interval=1000 // self.FPS)
        self.timer.timeout.connect(self._tick)

    def set_enabled(self, on):
        on = bool(on)
        if on == self.enabled:
            return
        self.enabled = on
        if on:
            self.drawn = None
            self.last = time.monotonic()
            self.timer.start()
        else:
            self.timer.stop()
            QApplication.setWindowIcon(self.static)

    def set_levels(self, rms, peak):
        if not self.enabled:
            return
        values = [v for v in rms[:2] if not math.isnan(v)]
        self.level = max(values) if values else self.FLOOR
        self.received = time.monotonic()
        if not self.timer.isActive():
            self.last = time.monotonic()
            self.timer.start()

    def _tick(self):
        now = time.monotonic()
        elapsed, self.last = min(now - self.last, 0.1), now
        level = self.level if now - self.received < self.STALE else self.FLOOR
        if level > -40:     # not in the silences and fades: they would turn it all the way up
            target = min(self.TRIM_RANGE[1], max(self.TRIM_RANGE[0], level + self.HEADROOM))
            speed = self.TRIM_UP if target > self.reference else self.TRIM_DOWN
            self.reference += (target - self.reference) * min(1.0, elapsed / speed)
        goal = AnalogFaces._needle_ratio(level - self.reference) if level > self.FLOOR else self.rest
        steps = max(1, math.ceil(elapsed / 0.004))
        dt = elapsed / steps
        for _ in range(steps):
            self.speed += (self.SPRING ** 2 * (goal - self.position) - 2 * self.DAMPING * self.SPRING * self.speed) * dt
            self.position += self.speed * dt
        if self.drawn is None or abs(self.position - self.drawn) > 0.003:
            self._draw()
        elif level <= self.FLOOR and abs(self.speed) < 0.002:
            self.timer.stop()       # at rest: the levels start it again

    def _draw(self):
        self.drawn = self.position
        ratio = max(-0.02, min(1.03, self.position))
        peak = ratio > AnalogFaces._needle_ratio(AnalogFaces.PEAK_VU)
        QApplication.setWindowIcon(QIcon(appicon.painter().render(self.SIZE, ratio, peak)))
