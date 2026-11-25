"""The right side: the cover, the lyrics, the VU meter and the amplifier (amp.py), one at a time, in turn by hand or every few seconds."""

import html
import math
import os
import sys
import time

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPen, QResizeEvent, QTextCursor
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QTextBrowser,
                             QToolButton, QVBoxLayout, QWidget)

from cutemusic.util import on_screen
from cutemusic import util
from cutemusic.library import parse_lrc, read_artist_image, read_cover, read_lyrics
from cutemusic.icons import set_line_icon
from cutemusic.style import make_segmented
from cutemusic.vufaces import AnalogFaces


__all__ = ['VisualPane']


PAGES = [('cover', 'Cover'), ('lyrics', 'Lyrics'), ('vu', 'VU meter'), ('amp', 'Amplifier'), ('mixer', 'Mixer')]     # (the lava lamp: a VU meter style)
BACKGROUND = QColor('#101318')
TEXT = QColor('#e8eaf0')
DIM = QColor('#8a90a0')


class CoverView(QWidget):
    """The album cover (else the artist's picture), as large as fits, with the title, artist and album under it
    (captions; the docked column has them elsewhere)."""

    def __init__(self, parent=None, captions=True):
        super().__init__(parent)
        self.image = None
        self.lines = []
        self.disc = ('', '')        # the artist and the album: written on the disc drawn when there is no cover
        self.captions = captions
        self.margin = 24 if captions else 10
        self.setMinimumSize(200, 200)

    def show_track(self, track, lines=None):
        """track's cover and captions (title, artist, album); lines: other captions (an album or artist selected)."""
        self.image = None
        if track is not None:
            data, path = read_cover(track)
            path = path or (None if data else read_artist_image(track))
            image = QImage()
            if data:
                image.loadFromData(data)
            elif path:
                image.load(path)
            self.image = image if not image.isNull() else None
            album = track.get('album') or ''
            if track.get('year'):
                album += f' ({track["year"]})'
            self.lines = list(lines) if lines else [track.display_title, track.get('artist') or track.album_artist, album]
            self.disc = (track.get('artist') or track.album_artist or '', track.get('album') or '')
        else:
            self.lines = []
            self.disc = ('', '')
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), BACKGROUND)
        font = QFont(self.font())
        line = font.pointSizeF() * 2.2
        text_height = int(line * 3.4) if self.lines and self.captions else 0
        margin = self.margin
        area = QRectF(self.rect()).adjusted(margin, margin, -margin, -margin - text_height)
        side = max(0.0, min(area.width(), area.height()))
        square = QRectF(area.center().x() - side / 2, area.top() + (area.height() - side) / 2, side, side)
        if self.image is not None:
            size = self.image.size().scaled(int(side), int(side), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF(square.center().x() - size.width() / 2, square.center().y() - size.height() / 2, size.width(), size.height())
            painter.drawImage(target, self.image)
        elif self.lines:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#1c2029'))
            painter.drawRoundedRect(square, 12, 12)
            painter.setBrush(QColor('#06070a'))
            painter.drawEllipse(square.center(), side * 0.36, side * 0.36)
            painter.setBrush(QColor('#e2683c'))
            painter.drawEllipse(square.center(), side * 0.12, side * 0.12)
            self._paint_disc_text(painter, square.center(), side)
        if not self.lines:
            painter.setPen(DIM)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'Nothing playing')
            return
        if not self.captions:
            return
        top = square.bottom() + 12
        for index, text in enumerate(self.lines):
            font.setBold(index == 0)
            font.setPointSizeF(self.font().pointSizeF() * (1.4 if index == 0 else 1.05))
            painter.setFont(font)
            painter.setPen(TEXT if index == 0 else DIM)
            rect = QRectF(12, top, self.width() - 24, line)
            painter.drawText(rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, int(rect.width())))
            top += line


    def _paint_disc_text(self, painter, centre, side):
        """The artist and, under it, the album, on the disc below its label: each line as wide as the disc lets it."""
        radius = side * 0.36
        font = QFont(self.font())
        for text, offset, size, bold, color in ((self.disc[0], 0.170, 0.050, True, TEXT),
                                                (self.disc[1], 0.240, 0.042, False, DIM)):
            if not text:
                continue
            font.setPixelSize(max(7, int(side * size)))
            font.setBold(bold)
            painter.setFont(font)
            painter.setPen(color)
            y = centre.y() + side * offset
            height = painter.fontMetrics().height()
            dy = max(abs(y - height / 2 - centre.y()), abs(y + height / 2 - centre.y()))
            width = 2 * math.sqrt(max(0.0, radius ** 2 - dy ** 2)) - side * 0.04
            if width < 20:
                continue
            rect = QRectF(centre.x() - width / 2, y - height / 2, width, height)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                             painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, int(width)))


_CODECS = {'MP3': 'MP3', 'FLAC': 'FLAC', 'OggVorbis': 'Ogg Vorbis', 'OggOpus': 'Opus', 'OggFLAC': 'Ogg FLAC',
           'OggSpeex': 'Speex', 'WAVE': 'WAV (PCM)', 'AIFF': 'AIFF (PCM)', 'AAC': 'AAC', 'MonkeysAudio': "Monkey's Audio",
           'WavPack': 'WavPack', 'ASF': 'WMA', 'TrueAudio': 'TTA', 'DSF': 'DSD', 'DSDIFF': 'DSD'}


def file_info(path):
    """The file's technical data, one line: codec, sample rate and bit depth, channels, bit rate, size; '' when it
    cannot be read (no mutagen, not a file)."""
    try:
        import mutagen
        audio = mutagen.File(path)
    except Exception:
        audio = None
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    if audio is None or getattr(audio, 'info', None) is None:
        return os.path.splitext(path)[1].lstrip('.').upper() + (' \u00b7 ' + _size(size) if size else '')
    info = audio.info
    kind = type(audio).__name__
    codec = _CODECS.get(kind, kind)
    if kind == 'MP4':       # AAC or ALAC by its codec
        name = str(getattr(info, 'codec', '') or '')
        codec = 'ALAC' if name.startswith('alac') else ('AAC' if name.startswith('mp4a') else (name.upper() or 'MP4'))
    if kind == 'MP3':
        layer, mode = getattr(info, 'layer', 3), getattr(info, 'bitrate_mode', None)
        codec = 'MP%d' % layer
        mode = str(mode).rsplit('.', 1)[-1] if mode is not None else ''
        if mode in ('VBR', 'ABR', 'CBR'):
            codec += ' ' + mode
    parts = [codec]
    rate, bits = getattr(info, 'sample_rate', 0), getattr(info, 'bits_per_sample', 0)
    if rate:
        khz = rate / 1000
        parts.append(('%g kHz' % khz) + (f' / {bits}-bit' if bits else ''))
    channels = getattr(info, 'channels', 0)
    if channels:
        parts.append({1: 'mono', 2: 'stereo'}.get(channels, f'{channels} channels'))
    bitrate = getattr(info, 'bitrate', 0)
    if bitrate:
        parts.append(f'{round(bitrate / 1000)} kbit/s')
    if size:
        parts.append(_size(size))
    return ' \u00b7 '.join(parts)


def _size(size):
    return f'{size / 1e6:.1f} MB' if size >= 1e6 else f'{max(1, round(size / 1e3))} KB'


class SongInfo(QLabel):
    """Under every page (cover, lyrics, VU meter, amplifier, mixer): the song's technical data (file_info), small.
    Shown or not (the amplifier's menu: Show file info; ui.conf visuals/file_info, on at first): its line's room
    kept either way, and with no song, so the pages above it (the amplifier) never move."""

    _labels = []

    @staticmethod
    def enabled():
        return util.settings().value('visuals/file_info', True, type=bool)

    @classmethod
    def set_enabled(cls, on):
        util.settings().setValue('visuals/file_info', bool(on))
        for label in cls._labels:
            label._apply()

    def __init__(self, parent=None):
        super().__init__(parent)
        SongInfo._labels.append(self)
        self.destroyed.connect(lambda: SongInfo._labels.remove(self) if self in SongInfo._labels else None)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(f'QLabel {{ background: {BACKGROUND.name()}; color: {DIM.name()}; padding: 3px 8px 5px 8px; }}')
        font = self.font()
        font.setPointSizeF(font.pointSizeF() * 0.8)
        self.setFont(font)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setFixedHeight(self.fontMetrics().height() + 8)        # (the padding: 3 + 5)
        self.info, self.path = '', ''

    def show_track(self, track):
        self.path = (track.get('path') if track is not None else None) or ''
        self.info = file_info(self.path) if self.path else ''
        self._apply()

    def _apply(self):
        on = self.enabled()
        self.setText(self.info if on else '')
        self.setToolTip(self.path if on else '')


class LyricsView(QTextBrowser):
    """The lyrics; synced ones (.lrc) follow the song, the line sung highlighted and kept in the middle."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.synced = None
        self.current = -1
        self.setOpenLinks(False)
        self.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.setStyleSheet(f'QTextBrowser {{ background: {BACKGROUND.name()}; color: {TEXT.name()}; padding: 18px; }}')

    def show_track(self, track):
        self.synced, self.current = None, -1
        text = read_lyrics(track) if track is not None else None
        if track is None:
            self._plain('')
        elif not text:
            self._plain(f'No lyrics for {track.display_title}\n\n'
                        '(in its tags, or an .lrc or .txt file of the same name next to it)')
        else:
            self.synced = parse_lrc(text)
            if self.synced:
                self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)     # it follows the song
                self._render()
            else:
                self._plain(text)

    def _plain(self, text):
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHtml('<div style="text-align:center; font-size:large; line-height:150%">'
                     + html.escape(text).replace('\n', '<br>') + '</div>')
        self._margins(4)

    def _margins(self, size):
        frame = self.document().rootFrame()
        layout = frame.frameFormat()
        layout.setTopMargin(size)
        layout.setBottomMargin(size)
        frame.setFrameFormat(layout)

    def _render(self):
        rows = []
        for index, (seconds, words) in enumerate(self.synced):
            style = f'color:{TEXT.name()}; font-weight:bold; font-size:x-large' if index == self.current else f'color:{DIM.name()}'
            rows.append(f'<p style="{style}; margin:6px"><a name="l{index}"></a>{html.escape(words) or "&nbsp;"}</p>')
        self.setHtml('<div style="text-align:center; font-size:large">' + ''.join(rows) + '</div>')
        self._margins(self.viewport().height() / 2)        # the first and last lines can be in the middle too

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self):
        # laid out again for this size: the space around the lines, the line sung in the middle
        if self.synced:
            self.current = -2
            self.set_position(self.seconds)

    seconds = 0.0

    def set_position(self, seconds):
        self.seconds = seconds
        if not self.synced or not self.isVisible() and self.current != -2:
            return
        current = -1
        for index, (start, words) in enumerate(self.synced):
            if start <= seconds + 0.15:
                current = index
            else:
                break
        if current != self.current:
            self.current = current
            self._render()
            if current >= 0:
                block = self.document().findBlockByNumber(current)
                cursor = QTextCursor(block)
                rect = self.cursorRect(cursor)
                bar = self.verticalScrollBar()
                bar.setValue(bar.value() + rect.center().y() - self.viewport().height() // 2)


VU_STYLES = [('bars', 'Bars'), ('needles', 'Needles'), ('leds', 'LEDs'), ('spectrum', 'Spectrum')]


class VUMeter(AnalogFaces, QWidget):
    """Left and right: the RMS level with VU-like ballistics (fast up, ~300 ms down) and the peak held for a moment,
    drawn as bars (-60..0 dBFS), analog needles (VU, 0 VU following the music's loudness) or LED ladders. A click shows the next style."""

    FLOOR = -60.0
    HOLD = 1.2          # s a peak is held
    RELEASE = 0.30      # s for the level to fall by 1/e
    # the needles' 0 VU follows the music (a trim pot turned by itself): its loudness HEADROOM under 0 VU, so they
    # swing around -3 and the loud parts go into the red, whatever the master's level. Up in TRIM_UP s (a loud part
    # after a quiet one does not pin them), down in TRIM_DOWN s (a quiet passage stays quiet), within TRIM_RANGE dBFS:
    # up to 0, or a loud master (RMS -8..-4 dBFS) pins it at the top and its beats peg the needles past +3
    TRIM_UP = 1.0
    TRIM_DOWN = 10.0
    HEADROOM = 3.0
    TRIM_RANGE = (-24.0, 0.0)
    STALE = 0.3         # s without levels (paused, stalled): back to silence
    style_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, 200)
        self.rms = [self.FLOOR, self.FLOOR]
        self.peaks = [(self.FLOOR, 0.0), (self.FLOOR, 0.0)]
        self.target = [self.FLOOR, self.FLOOR]
        self.last = time.monotonic()
        self.style = 'bars'
        self.reference = -18.0      # dBFS at 0 VU
        self.received = 0.0         # when levels last came
        self.spectrum = None        # (magnitudes dB, sample rate): the last FFT (player.py)
        self.spectrum_received = 0.0
        self.bars = []              # the spectrum's bars as drawn: dB, falling slowly
        self.bar_peaks = []         # (dB, since)
        self.needles = [0.0, 0.0]   # the needles (0..1 of their scale) and their speed: a mass on a spring
        self.peak_lit = [0.0, 0.0]  # the needles' PEAK lamps: when a needle was last past PEAK_VU
        self.needle_speeds = [0.0, 0.0]
        self.timer = QTimer(self, interval=16)
        self.timer.timeout.connect(self._tick)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('Click: the next style')

    def set_style(self, style):
        self.style = style
        self.timer.setInterval(33 if style == 'needles' else 16)        # needles: 30 a second (amp.NEEDLE_FRAME)
        self.update()
        self.style_changed.emit(style)

    def content_height(self, width, available):
        """The height the style draws in at width, at most available: what the style's shapes need (the LEDs about
        half the width), so what is under them (the lyrics) comes close."""
        if self.style == 'bars':
            needed = 260
        elif self.style == 'needles':
            needed = self._needles_window(width)[0].height() + 2 * self._needles_margin(width)
        elif self.style == 'spectrum':     # a band: about a third as tall as wide, at most 280 px
            needed = min(280.0, width * 0.35)
        else:           # the LEDs: about half as tall as wide, not the whole height (the docked column sets its own)
            needed = width * 0.55
        return int(min(available, max(160, needed + 2 * self._frame(width)[1])))

    def next_style(self):
        names = [name for name, label in VU_STYLES]
        self.set_style(names[(names.index(self.style) + 1) % len(names)])

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.next_style()

    def showEvent(self, event):
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()

    def _wake(self):
        """The timer again (stopped when all came to rest, paused or stopped, or off screen): levels are coming."""
        if not self.timer.isActive() and on_screen(self):
            self.last = time.monotonic()
            self.timer.start()

    def set_levels(self, rms, peak):
        self._wake()
        if not rms:
            self.target = [self.FLOOR, self.FLOOR]
            return
        self.received = time.monotonic()
        if len(rms) == 1:
            rms, peak = rms * 2, peak * 2
        now = time.monotonic()
        self.target = [max(self.FLOOR, v) if not math.isnan(v) else self.FLOOR for v in rms[:2]]
        for channel, value in enumerate(peak[:2]):
            value = self.FLOOR if math.isnan(value) else max(self.FLOOR, value)
            if value >= self.peaks[channel][0] or now - self.peaks[channel][1] > self.HOLD:
                self.peaks[channel] = (value, now)

    def set_spectrum(self, magnitudes, rate):
        self._wake()
        if magnitudes and rate:
            self.spectrum = (magnitudes, rate)
            self.spectrum_received = time.monotonic()
        else:
            self.spectrum = None

    # the spectrum: bars on a log frequency scale, tilted +3 dB an octave about 1 kHz (music's energy falls with the
    # frequency: level bars for "balanced" music), DB_LOW..DB_HIGH from bottom to top
    FREQUENCIES = (30.0, 16000.0)
    TILT = 3.0
    DB_LOW, DB_HIGH = -72.0, -12.0
    LABELS = ((50, '50'), (100, '100'), (200, '200'), (500, '500'), (1000, '1k'), (2000, '2k'), (5000, '5k'), (10000, '10k'))

    def _bar_count(self):
        return int(max(16, min(48, (self._aw() - 24) / 14)))

    def _spectrum_bars(self, count):
        """The last spectrum grouped into count bars (dB, tilted): the strongest band in each, or the one at its centre
        where a bar is narrower than a band (the low ones)."""
        if self.spectrum is None or time.monotonic() - self.spectrum_received > self.STALE:
            return [self.DB_LOW] * count
        magnitudes, rate = self.spectrum
        width = rate / 2 / len(magnitudes)          # Hz a band
        low, high = (math.log2(f) for f in self.FREQUENCIES)
        bars = []
        for index in range(count):
            f0, f1 = 2 ** (low + (high - low) * index / count), 2 ** (low + (high - low) * (index + 1) / count)
            i0, i1 = f0 / width, f1 / width
            if i1 - i0 < 1:
                centre = min(len(magnitudes) - 1.001, max(0.0, (i0 + i1) / 2 - 0.5))
                below = int(centre)
                db = magnitudes[below] + (magnitudes[below + 1] - magnitudes[below]) * (centre - below)
            else:
                db = max(magnitudes[int(i0):min(len(magnitudes), int(math.ceil(i1)))])
            bars.append(db + self.TILT * math.log2(math.sqrt(f0 * f1) / 1000))
        return bars

    def _tick_spectrum(self, now, elapsed):
        count = self._bar_count()
        target = self._spectrum_bars(count)
        if len(self.bars) != count:
            self.bars = [self.DB_LOW] * count
            self.bar_peaks = [(self.DB_LOW, now)] * count
        fall = math.exp(-elapsed / self.RELEASE)
        changed = False
        for index, value in enumerate(target):
            value = max(self.DB_LOW, value)
            old = self.bars[index]
            new = value if value > old else value + (old - value) * fall
            if abs(new - old) > 0.05:
                changed = True
            self.bars[index] = new
            peak, since = self.bar_peaks[index]
            if new >= peak:
                self.bar_peaks[index] = (new, now)
            elif now - since > self.HOLD * 0.5 and peak > self.DB_LOW:
                self.bar_peaks[index] = (max(self.DB_LOW, peak - 24 * elapsed), since)
                changed = True
        return changed

    def _tick(self):
        if not on_screen(self):         # minimized, covered: nothing computed nor drawn (woken by _wake, the window's watch)
            self.timer.stop()
            return
        now = time.monotonic()
        elapsed, self.last = now - self.last, now
        busy = False
        if self.style == 'spectrum' and self._tick_spectrum(now, elapsed):
            self.update()
            busy = True
        fall = math.exp(-elapsed / self.RELEASE)
        if now - self.received > self.STALE:
            self.target = [self.FLOOR, self.FLOOR]
        changed = False
        for channel in range(2):
            target = self.target[channel]
            value = self.rms[channel]
            new = target if target > value else target + (value - target) * fall
            if abs(new - value) > 0.01:
                changed = True
            self.rms[channel] = new
            level, since = self.peaks[channel]
            if now - since > self.HOLD and level > self.FLOOR:
                self.peaks[channel] = (max(self.FLOOR, level - 30 * elapsed), since)
                changed = True
        loudness = max(self.rms)
        if loudness > -40:      # not in the silences and fades: they would turn it all the way up
            target = min(self.TRIM_RANGE[1], max(self.TRIM_RANGE[0], loudness + self.HEADROOM))
            speed = self.TRIM_UP if target > self.reference else self.TRIM_DOWN
            self.reference += (target - self.reference) * min(1.0, elapsed / speed)
        silent = all(target <= self.FLOOR for target in self.target)
        if self.style == 'needles':     # only the needles move: their window redrawn, nothing else of the widget
            if self._tick_needles(elapsed) or any(now - lit < 0.6 for lit in self.peak_lit):
                window, stacked = self._needles_window(self.width(), self.height())
                self.update(window.adjusted(-4, -4, 4, 4).toAlignedRect())
            elif silent and not busy and not changed:
                self.timer.stop()       # at rest: woken by the next levels (set_levels, set_spectrum)
            return
        if changed:
            self.update()
        elif silent and not busy:
            self.timer.stop()           # at rest: woken by the next levels (set_levels, set_spectrum)

    # the needles move as a VU meter's movement (as the amplifier's, amp.py): a mass on a spring following the
    # latest level, there in about 300 ms, a hair past it and back; not the bars' smoothed level
    SPRING, DAMPING = 16.0, 0.78

    def _tick_needles(self, elapsed):
        steps = max(1, math.ceil(min(elapsed, 0.1) / 0.004))       # small steps: the spring stays stable
        dt = min(elapsed, 0.1) / steps
        moved = False
        for channel in range(2):
            level = self.target[channel]
            goal = self._needle_ratio(level - self.reference) if level > self.FLOOR else self._needle_ratio(self.VU_MIN - 2)
            position, speed = self.needles[channel], self.needle_speeds[channel]
            for _ in range(steps):
                speed += (self.SPRING ** 2 * (goal - position) - 2 * self.DAMPING * self.SPRING * speed) * dt
                position += speed * dt
            if abs(position - self.needles[channel]) > 0.0004 or abs(speed) > 0.002:
                moved = True
            self.needles[channel], self.needle_speeds[channel] = position, speed
            if position >= self._needle_ratio(self.PEAK_VU):      # the PEAK lamp: the needle well into the red
                self.peak_lit[channel] = time.monotonic()
        return moved

    def _ratio(self, db):
        return (min(0.0, max(self.FLOOR, db)) - self.FLOOR) / -self.FLOOR

    def paintEvent(self, event):
        painter = QPainter(self)
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() * 0.85)
        if self.style == 'spectrum':
            self._paint_spectrum_cached(painter, font)
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), BACKGROUND)
        painter.setFont(font)
        left, top = self._frame()
        painter.translate(left, top)        # room around the meter (the needles keep their own)
        getattr(self, f'_paint_{self.style}', self._paint_bars)(painter)

    # room around the meters, as around the needles (NEEDLES_BORDER): not edge to edge; none in the amplifier's
    # meter window (framed False), which is their frame
    framed = True

    def _frame(self, width=None):
        if not self.framed or self.style == 'needles':
            return 0.0, 0.0
        width = self.width() if width is None else width
        return max(12.0, width * self.NEEDLES_BORDER), max(12.0, width * 0.05)

    def _aw(self):
        return self.width() - 2 * self._frame()[0]

    def _ah(self):
        return self.height() - 2 * self._frame()[1]

    @staticmethod
    def _colors(gradient, start, end):
        for db, color in ((-60, '#2e8f6c'), (-18, '#8fe3c0'), (-9, '#ffcf7a'), (-3, '#e2683c'), (0, '#ff3b30')):
            gradient.setColorAt((db + 60) / 60, QColor(color))

    def _paint_bars(self, painter):
        margin = 36
        compact = self._ah() < 220       # short (the amplifier's meter window): the scale only, under the bars
        bar = min(46.0, (self._ah() - (56 if compact else 120)) / (2.8 if compact else 3))
        total = bar * 2 + bar * 0.8
        top = (self._ah() - total) / 2
        width = self._aw() - 2 * margin
        gradient = QLinearGradient(margin, 0, margin + width, 0)
        self._colors(gradient, margin, margin + width)
        for channel, label in enumerate(('L', 'R')):
            rect = QRectF(margin, top + channel * bar * 1.8, width, bar)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#1c2029'))
            painter.drawRoundedRect(rect, 4, 4)
            if compact:         # the amplifier's: a row of LEDs, as the LED ladders (lit, the peak, the others dark)
                self._led_row(painter, rect, channel)
            else:
                painter.setBrush(gradient)
                painter.drawRoundedRect(QRectF(rect.left(), rect.top(), width * self._ratio(self.rms[channel]), rect.height()), 4, 4)
            if self.peaks[channel][0] > self.FLOOR and not compact:
                x = rect.left() + width * self._ratio(self.peaks[channel][0])
                painter.fillRect(QRectF(x - 2, rect.top(), 3, rect.height()), QColor('#ffffff'))
            painter.setPen(DIM)
            painter.drawText(QRectF(rect.left() - 28, rect.top(), 22, rect.height()), Qt.AlignmentFlag.AlignCenter, label)
        painter.setPen(QPen(DIM, 1))
        for db in (-60, -50, -40, -30, -20, -12, -6, -3, 0):
            x = margin + width * self._ratio(db)
            painter.drawLine(QPointF(x, top - 6), QPointF(x, top - 2))
            painter.drawText(QRectF(x - 20, top + total + 6, 40, 20), Qt.AlignmentFlag.AlignCenter, str(db))
        if compact:
            return
        values = ' / '.join(f'{v:.0f}' if v > self.FLOOR else '-∞' for v in self.rms)
        painter.drawText(QRectF(0, top + total + 34, self._aw(), 20), Qt.AlignmentFlag.AlignCenter, f'{values} dBFS RMS')

    def _led_row(self, painter, rect, channel):
        segments = 40
        step = rect.width() / segments
        lit = self._ratio(self.rms[channel]) * segments
        peak = int(self._ratio(self.peaks[channel][0]) * segments - 0.001) if self.peaks[channel][0] > self.FLOOR else -1
        for index in range(segments):
            db = self.FLOOR + (index + 1) * -self.FLOOR / segments
            color = QColor('#5cdc9e') if db <= -18 else QColor('#ffcc55') if db <= -6 else QColor('#f4574a')
            if not (index < lit or index == peak):
                color = QColor(color.red() * 22 // 100, color.green() * 22 // 100, color.blue() * 22 // 100)
            painter.fillRect(QRectF(rect.left() + index * step + 1, rect.top() + 1, step - 2, rect.height() - 2), color)

    # the needles: the amplifier's meters (vufaces.py), one face for both channels lit from behind, as big as the
    # widget allows at the face's proportions; side by side, or stacked in a narrow column (the dock)
    NEEDLES_MARGIN = 12         # at the least, around the window
    NEEDLES_BORDER = 0.08       # of the width, at each side (the window not edge to edge: room around it)
    NEEDLES_STACKED = 520       # px wide, under it: stacked

    def _needles_margin(self, width):
        return max(self.NEEDLES_MARGIN, width * self.NEEDLES_BORDER)

    def _needles_window(self, width, height=None):
        margin = self._needles_margin(width)
        stacked = width < self.NEEDLES_STACKED
        # the window's width to its height: two faces (side by side or stacked) and the bezel's thin gaps
        aspect = self.FACE_ASPECT / 2 * 0.97 if stacked else 2 * self.FACE_ASPECT / 0.97
        window_width = width - 2 * margin
        if height is not None:
            window_width = min(window_width, (height - 2 * margin) * aspect)
        window_height = window_width / aspect
        left = (width - window_width) / 2
        top = ((height if height is not None else window_height + 2 * margin) - window_height) / 2
        return QRectF(left, top, window_width, window_height), stacked

    _needles_cache = None       # (key, the meter window drawn without its needles): its faces drawn once, not each frame

    def _paint_needles(self, painter):
        window, stacked = self._needles_window(self.width(), self.height())
        faces = self._face_rects(window, stacked)
        ratio = self.devicePixelRatioF()
        bounds = window.adjusted(-4, -4, 4, 4).toAlignedRect()
        key = (bounds.x(), bounds.y(), bounds.width(), bounds.height(), stacked, ratio)
        if self._needles_cache is None or self._needles_cache[0] != key:
            image = QImage(int(bounds.width() * ratio), int(bounds.height() * ratio), QImage.Format.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(ratio)
            image.fill(Qt.GlobalColor.transparent)
            cached = QPainter(image)
            cached.setRenderHint(QPainter.RenderHint.Antialiasing)
            cached.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            cached.setFont(painter.font())
            cached.translate(-bounds.x(), -bounds.y())
            self._draw_meter_window(cached, window, faces)
            cached.end()
            self._needles_cache = (key, image)
        painter.drawImage(bounds.topLeft(), self._needles_cache[1])
        for channel, face in enumerate(faces):
            self._draw_needle(painter, face, self.needles[channel], time.monotonic() - self.peak_lit[channel] < 0.35)

    def _paint_leds(self, painter):
        segments = 30
        margin = 12
        height = self._ah() - 2 * margin - 20
        step = height / segments
        width = min(90.0, self._aw() / 6)
        centre = self._aw() / 2
        for channel, label in enumerate(('L', 'R')):
            left = centre + (-width - 30 if channel == 0 else 30)
            lit = self._ratio(self.rms[channel]) * segments
            peak = int(self._ratio(self.peaks[channel][0]) * segments - 0.001) if self.peaks[channel][0] > self.FLOOR else -1
            for index in range(segments):
                db = self.FLOOR + (index + 1) * -self.FLOOR / segments
                # lit: bright but soft LED colours (between the pastel ones and full saturation); off: the same, dark
                color = QColor('#5cdc9e') if db <= -18 else QColor('#ffcc55') if db <= -6 else QColor('#f4574a')
                if not (index < lit or index == peak):
                    color = QColor(color.red() * 22 // 100, color.green() * 22 // 100, color.blue() * 22 // 100)
                rect = QRectF(left, margin + height - (index + 1) * step + 1.5, width, step - 3)
                painter.fillRect(rect, color)
            painter.setPen(DIM)
            painter.drawText(QRectF(left, margin + height + 2, width, 18), Qt.AlignmentFlag.AlignCenter, label)
        painter.setPen(DIM)
        line = painter.fontMetrics().height() * 0.9
        last = None         # short (the dock): the numbers that would overlap the one above left out
        for db in (0, -6, -12, -18, -30, -40, -50, -60):
            y = margin + height - self._ratio(db) * height
            if last is not None and y - last < line:
                continue
            painter.drawText(QRectF(centre - 24, y - 8, 48, 16), Qt.AlignmentFlag.AlignCenter, str(db))
            last = y


    def _spectrum_geometry(self, count):
        """(side, label size, labels shown, top, bottom, step, gap) of count bars, in the frame's coordinates."""
        side = 12.0
        label = max(6.0, min(11.0, self._ah() * 0.05))
        labels = self._ah() >= 120
        top, bottom = 10.0, self._ah() - (label * 2.2 if labels else 8.0)
        step = (self._aw() - 2 * side) / count
        return side, label, labels, top, bottom, step, max(1.0, step * 0.18)

    # the spectrum drawn from two pictures made once (per size, count of bars and font): the background (the empty
    # slots, the frequencies) and every bar lit full height; each frame the background, then of each bar the part of
    # the lit one up to its level (a copy of pixels, no gradient filled, no antialiasing), and the peaks
    _spectrum_cache = None

    def _paint_spectrum_cached(self, painter, font):
        count = len(self.bars) or self._bar_count()
        left, top = self._frame()
        ratio = self.devicePixelRatioF()
        key = (self.width(), self.height(), ratio, count, left, top, font.key())
        if self._spectrum_cache is None or self._spectrum_cache[0] != key:
            pictures = []
            for mode in ('empty', 'full'):
                image = QImage(max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio)),
                               QImage.Format.Format_ARGB32_Premultiplied)
                image.setDevicePixelRatio(ratio)
                image.fill(BACKGROUND if mode == 'empty' else Qt.GlobalColor.transparent)
                cached = QPainter(image)
                cached.setRenderHint(QPainter.RenderHint.Antialiasing)
                cached.setRenderHint(QPainter.RenderHint.TextAntialiasing)
                cached.setFont(font)
                cached.translate(left, top)
                self._paint_spectrum(cached, count=count, mode=mode)
                cached.end()
                pictures.append(image)
            self._spectrum_cache = (key, *pictures)
        _, back, lit = self._spectrum_cache
        painter.drawImage(QPointF(0, 0), back)
        bars = self.bars or [self.DB_LOW] * count
        peaks = self.bar_peaks or [(self.DB_LOW, 0.0)] * count
        side, _, _, slot_top, bottom, step, gap = self._spectrum_geometry(count)
        span = self.DB_HIGH - self.DB_LOW
        height = bottom - slot_top
        width = step - gap
        white = QColor('#e8eaf0')
        for index in range(count):
            x = left + side + index * step + gap / 2
            level = bottom - max(0.0, min(1.0, (bars[index] - self.DB_LOW) / span)) * height
            if level < bottom:
                target = QRectF(x, top + level, width, bottom - level)
                painter.drawImage(target, lit, QRectF(x * ratio, (top + level) * ratio, width * ratio, (bottom - level) * ratio))
            peak = bottom - max(0.0, min(1.0, (peaks[index][0] - self.DB_LOW) / span)) * height
            if peak < bottom - 1:
                painter.fillRect(QRectF(x, top + peak - 1, width, 2), white)

    def _paint_spectrum(self, painter, count=None, mode='live'):
        """mode 'live': as it is now; 'empty': the slots and the frequencies only; 'full': only the bars, all lit."""
        count = count or len(self.bars) or self._bar_count()
        bars = [self.DB_HIGH] * count if mode == 'full' else (self.bars if mode == 'live' and self.bars else [self.DB_LOW] * count)
        peaks = self.bar_peaks if mode == 'live' and self.bar_peaks else [(self.DB_LOW, 0.0)] * count
        side, label, labels, top, bottom, step, gap = self._spectrum_geometry(count)
        height = bottom - top
        # the LED colours (_paint_leds) up the height: green, then amber, red at the top
        gradient = QLinearGradient(0, bottom, 0, top)
        gradient.setColorAt(0.0, QColor('#2e8f6c'))
        gradient.setColorAt(0.55, QColor('#5cdc9e'))
        gradient.setColorAt(0.8, QColor('#ffcc55'))
        gradient.setColorAt(1.0, QColor('#f4574a'))
        brush = QBrush(gradient)
        span = self.DB_HIGH - self.DB_LOW

        def y(db):
            return bottom - max(0.0, min(1.0, (db - self.DB_LOW) / span)) * height
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(count):
            x = side + index * step + gap / 2
            if mode != 'full':
                painter.fillRect(QRectF(x, top, step - gap, height), QColor('#1a1e26'))     # the empty slot
            level = y(bars[index])
            if level < bottom:
                painter.fillRect(QRectF(x, level, step - gap, bottom - level), brush)
            peak = y(peaks[index][0])
            if peak < bottom - 1:
                painter.fillRect(QRectF(x, peak - 1, step - gap, 2), QColor('#e8eaf0'))
        if not labels or mode == 'full':
            return
        font = QFont(painter.font())
        font.setPixelSize(round(label))
        painter.setFont(font)
        painter.setPen(DIM)
        low, high = (math.log2(f) for f in self.FREQUENCIES)
        width = self._aw() - 2 * side
        for frequency, text in self.LABELS:
            x = side + (math.log2(frequency) - low) / (high - low) * width
            painter.drawText(QRectF(x - label * 2, bottom + label * 0.4, label * 4, label * 1.6), Qt.AlignmentFlag.AlignCenter, text)


class LyricsStrip(QWidget):
    """A few lines of the lyrics, the one sung in the middle, under the VU meter. Synced lyrics by their time
    stamps; plain ones by where the song is (the line at the same fraction of the text: approximate). Hidden when
    there are none. A- and A+ at its bottom right: its font smaller or bigger (ui.conf: visuals/lyrics_scale, the
    same for every strip; at first 1.5 times the window's font)."""

    LINES = 3
    BEFORE = None               # lines shown before the one sung (None: as many as after it)
    SCALE = 1.5                 # the font: this many times the window's, at first
    SCALES = (0.6, 4.0)         # the least and the most
    STEP = 1.12
    _strips = []                # every strip: a change shown in all of them

    def __init__(self, parent=None):
        super().__init__(parent)
        LyricsStrip._strips.append(self)
        self.destroyed.connect(lambda: LyricsStrip._strips.remove(self) if self in LyricsStrip._strips else None)
        self.buttons = []
        for text, factor, tip in (('A−', 1 / self.STEP, 'Smaller lyrics'), ('A+', self.STEP, 'Bigger lyrics')):
            button = QToolButton(self)
            button.setText(text)
            button.setToolTip(tip)
            button.setAutoRaise(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(f'QToolButton {{ color: {DIM.name()}; border: none; padding: 1px 4px; }}'
                                 f'QToolButton:hover {{ color: {TEXT.name()}; }}')
            button.clicked.connect(lambda checked=False, factor=factor: self._rescale(factor))
            self.buttons.append(button)
        self.timed = None       # [(seconds, line)]
        self.plain = None       # [line]
        self.duration = 0.0
        self.current = -1
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), BACKGROUND)
        self.setPalette(palette)
        self.hide()

    @classmethod
    def scale(cls):
        return util.settings().value('visuals/lyrics_scale', cls.SCALE, type=float)

    def _font(self):
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() * self.scale())
        return font

    def _rescale(self, factor):
        scale = max(self.SCALES[0], min(self.SCALES[1], self.scale() * factor))
        util.settings().setValue('visuals/lyrics_scale', scale)
        for strip in LyricsStrip._strips:       # each laid out again where it is (its height follows its font)
            strip.updateGeometry()
            parent = strip.parentWidget()
            if parent is not None:
                QApplication.sendEvent(parent, QResizeEvent(parent.size(), parent.size()))
                parent.update()
            strip.update()

    def resizeEvent(self, event):
        x = self.width() - 6
        for button in reversed(self.buttons):     # bottom right: A- A+
            button.adjustSize()
            x -= button.width()
            button.move(x, self.height() - button.height() - 4)

    def sizeHint(self):
        from PyQt6.QtCore import QSize
        from PyQt6.QtGui import QFontMetrics
        return QSize(200, int(QFontMetrics(self._font()).height() * (self.LINES * 1.6 + 1)))

    def show_track(self, track):
        self.timed = self.plain = None
        self.current = -1
        text = read_lyrics(track) if track is not None else None
        if text:
            self.timed = parse_lrc(text)
            if not self.timed:
                self.plain = [line.strip() for line in text.splitlines() if line.strip()]
        self.duration = (track.get('duration') or 0.0) if track is not None else 0.0
        self.setVisible(bool(self.timed or self.plain))
        self.update()

    def set_position(self, seconds, duration=0.0):
        if duration:
            self.duration = duration
        current = -1
        if self.timed:
            for index, (start, words) in enumerate(self.timed):
                if start > seconds + 0.15:
                    break
                current = index
        elif self.plain and self.duration:
            current = min(len(self.plain) - 1, int(seconds / self.duration * len(self.plain)))
        # the song over (its last second), or its last line sung and held as long as a line lasts: no lyrics left
        ended = bool(self.duration) and seconds >= self.duration - 1.0
        if self.timed and current == len(self.timed) - 1:
            starts = [start for start, words in self.timed]
            gaps = sorted(b - a for a, b in zip(starts, starts[1:]) if b > a)
            line = gaps[len(gaps) // 2] if gaps else 5.0
            ended = ended or seconds > starts[-1] + min(10.0, max(4.0, line * 1.5))
        if ended:
            current = len(self._lines())        # past the last line: none of them in the rows (paintEvent)
        if current != self.current:
            self.current = current
            self.update()

    def _lines(self):
        return [words for start, words in self.timed] if self.timed else (self.plain or [])

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), BACKGROUND)
        lines = self._lines()
        if not lines:
            return
        # the lines over A- A+, not across them (a narrow strip, the dock's: they would run into the last line)
        below = max((button.sizeHint().height() for button in self.buttons), default=0) + 4
        height = max(1.0, self.height() - below) / self.LINES
        middle = self.LINES // 2 if self.BEFORE is None else self.BEFORE
        for row in range(self.LINES):
            index = self.current + row - middle
            if self.current >= len(lines) or not 0 <= index < len(lines):
                continue
            font = self._font()
            if row == middle:
                font.setPointSizeF(font.pointSizeF() * 1.3)
                font.setBold(True)
            painter.setFont(font)
            painter.setPen(TEXT if row == middle else DIM)
            rect = QRectF(16, row * height, self.width() - 32, height)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                             painter.fontMetrics().elidedText(lines[index] or '♪', Qt.TextElideMode.ElideRight, int(rect.width())))


class _Body(QWidget):
    """The meter with the lyrics right under it, the two centred together in the height (each style at its own height)."""

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), BACKGROUND)
        self.setPalette(palette)

    def resizeEvent(self, event):
        self.arrange()

    def arrange(self):
        meter, lyrics = getattr(self.page, 'meter', None), getattr(self.page, 'lyrics', None)
        if meter is None or lyrics is None:
            return
        width, height = self.width(), self.height()
        below = lyrics.sizeHint().height() if lyrics.isVisibleTo(self) else 0
        drawn = meter.content_height(width, height - below)
        top = max(0, (height - drawn - below) // 2)
        meter.setGeometry(0, top, width, drawn)
        lyrics.setGeometry(0, top + drawn, width, below)
        lava = getattr(self.page, 'lava', None)
        if lava is not None and lava.isVisibleTo(self):        # the lava lamp: the whole page, its own lyrics in it
            lava.setGeometry(0, 0, width, height)
            lava.raise_()


class VUPage(QWidget):
    """The VU meter, the lyrics being sung under it, and its styles; the last of them the lava lamp (the whole page,
    ui.conf: visuals/vu_lava)."""

    STYLES = VU_STYLES + [('lava', 'Lava lamp')]

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.body = _Body(self)
        self.meter = VUMeter(self.body)
        self.lyrics = LyricsStrip(self.body)
        from cutemusic.lava import LavaLamp
        self.lava = LavaLamp(self.body)
        self.lava.hide()
        layout.addWidget(self.body, 1)
        bar = QWidget()
        bar.setAutoFillBackground(True)
        palette = bar.palette()
        palette.setColor(bar.backgroundRole(), BACKGROUND)
        # the style buttons are drawn from the window and text colours: dark ones here whatever the theme (a light
        # theme's dark text on this dark bar left them invisible)
        palette.setColor(palette.ColorRole.Window, BACKGROUND)
        palette.setColor(palette.ColorRole.ButtonText, TEXT)
        palette.setColor(palette.ColorRole.WindowText, TEXT)
        bar.setPalette(palette)
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 4, 8, 8)
        row.setSpacing(0)
        row.addStretch(1)
        self.buttons = QButtonGroup(self)
        buttons = []
        small = QFont(self.font())
        small.setPointSizeF(small.pointSizeF() * 0.85)      # a tad smaller than the view's (Cover, Lyrics, VU meter)
        for index, (name, label) in enumerate(self.STYLES):
            button = QPushButton(label)
            button.setFont(small)
            set_line_icon(button, name, TEXT)        # the bar is dark whatever the theme
            button.setIconSize(QSize(14, 14))
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedWidth(max(64, button.fontMetrics().horizontalAdvance(label) + 26 + 18))
            self.buttons.addButton(button, index)
            row.addWidget(button)
            buttons.append(button)
        make_segmented('vu-styles', *buttons)
        row.addStretch(1)
        layout.addWidget(bar)
        self.style_bar = bar        # (hidden in full screen)
        self.buttons.idClicked.connect(self._clicked)
        self.meter.style_changed.connect(self._style_changed)
        style = util.settings().value('visuals/vu_style', 'bars', type=str)
        self.meter.set_style(style if style in dict(VU_STYLES) else 'bars')
        self.show_lava(util.settings().value('visuals/vu_lava', False, type=bool))

    def _clicked(self, index):
        name = self.STYLES[index][0]
        if name == 'lava':
            self.show_lava(True)
        else:
            self.show_lava(False)
            self.meter.set_style(name)

    def show_lava(self, on):
        self.lava.setVisible(on)
        util.settings().setValue('visuals/vu_lava', bool(on))
        self.body.arrange()
        self._style_changed('lava' if on else self.meter.style)

    def _style_changed(self, style):
        if style != 'lava' and self.lava.isVisibleTo(self):
            return      # (the meter's style changed under the lamp: the lamp's pill stays checked)
        self.buttons.button([name for name, label in self.STYLES].index(style)).setChecked(True)
        util.settings().setValue('visuals/vu_style', style)
        self.body.arrange()

    def show_track(self, track):
        self.lyrics.show_track(track)
        self.body.arrange()


class VisualPane(QWidget):
    """The cover, the lyrics, the VU meter (the lava lamp one of its styles) or the amplifier."""

    page_changed = pyqtSignal(str)
    browser_wanted = pyqtSignal()       # the Browser pill: the music browser shown again
    windowed_wanted = pyqtSignal()      # the restore button (in full screen): back to a window
    full_screen_wanted = pyqtSignal()   # the full screen button (in a window)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 6, 6)
        self.bar = QWidget()        # the views' buttons (hidden in full screen while the mouse rests)
        bar = QHBoxLayout(self.bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(0)
        bar.addStretch(1)
        # Browser: a pill of its own left of Cover, there only while the music browser is hidden (set_browser_shown)
        self.browser_pill = QPushButton('Browser')
        set_line_icon(self.browser_pill, 'folder')
        self.browser_pill.setIconSize(QSize(16, 16))
        self.browser_pill.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.browser_pill.setToolTip('Show the music browser (Ctrl+B)')
        make_segmented('browser', self.browser_pill)
        self.browser_pill.clicked.connect(self.browser_wanted)
        self.browser_pill.hide()
        bar.addWidget(self.browser_pill)
        self.browser_gap = QWidget()
        self.browser_gap.setFixedWidth(10)
        self.browser_gap.hide()
        bar.addWidget(self.browser_gap)
        self.buttons = QButtonGroup(self)
        self.views = {}
        self.stack = QStackedWidget()
        from cutemusic.amp import AmpView
        from cutemusic.mixer import MixerView
        self.cover, self.lyrics, self.vu_page, self.amp = CoverView(), LyricsView(), VUPage(), AmpView()
        self.song_info = SongInfo()      # under the pages, whichever is shown
        self.mixer = MixerView()
        self.lava = self.vu_page.lava
        self.vu = self.vu_page.meter
        buttons = []
        for index, ((key, label), view) in enumerate(zip(PAGES, (self.cover, self.lyrics, self.vu_page, self.amp, self.mixer))):
            button = QPushButton(label)
            set_line_icon(button, key)
            button.setIconSize(QSize(16, 16))
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.buttons.addButton(button, index)
            bar.addWidget(button)
            buttons.append(button)
            self.stack.addWidget(view)
        make_segmented('pages', *buttons)
        bar.addStretch(1)
        # full screen and back (as View -> Full screen, Esc): at the right edge of the pages' row, its arrows out in a
        # window, in in full screen (the row shown there while the mouse moves, as the pages' buttons)
        self.full_screen_button = QToolButton()
        set_line_icon(self.full_screen_button, 'fullscreen', DIM)
        self.full_screen_button.setIconSize(QSize(20, 20))
        self.full_screen_button.setToolTip('Full screen (Ctrl+F)' if sys.platform != 'darwin' else 'Full screen (Cmd+F)')
        self.full_screen_button.setAutoRaise(True)
        self.full_screen_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.full_screen_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.full_screen_button.setStyleSheet('QToolButton { border: none; padding: 2px; }')
        self.full_screen = False
        self.full_screen_button.clicked.connect(lambda: self.windowed_wanted.emit() if self.full_screen else self.full_screen_wanted.emit())
        bar.addWidget(self.full_screen_button)
        # as wide at the left end: the pages' buttons stay centred
        self.full_screen_balance = QWidget()
        self.full_screen_balance.setFixedWidth(self.full_screen_button.sizeHint().width())
        bar.insertWidget(0, self.full_screen_balance)
        layout.addWidget(self.bar)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.song_info)
        self.buttons.idClicked.connect(self.show_page)
        settings = util.settings()
        page = settings.value('visuals/page', 'amp', type=str)        # the first time: the amplifier
        if page == 'lava':      # (a page once, a VU meter style now)
            page = 'vu'
            self.vu_page.show_lava(True)
        self.show_page(next((i for i, (key, label) in enumerate(PAGES) if key == page), 0))

    def set_full_screen(self, on):
        """The full screen button as it is: arrows in (back to a window) in full screen, out in a window."""
        self.full_screen = bool(on)
        set_line_icon(self.full_screen_button, 'windowed' if on else 'fullscreen', DIM)
        self.full_screen_button.setToolTip('Leave full screen (Esc)' if on else
                                           'Full screen (Ctrl+F)' if sys.platform != 'darwin' else 'Full screen (Cmd+F)')

    def set_browser_shown(self, shown):
        """The music browser shown (no pill) or hidden (the pill, to bring it back)."""
        self.browser_pill.setVisible(not shown)
        self.browser_gap.setVisible(not shown)

    def show_page(self, index):
        self.stack.setCurrentIndex(index)
        self.buttons.button(index).setChecked(True)
        util.settings().setValue('visuals/page', PAGES[index][0])
        self.page_changed.emit(PAGES[index][0])

    def next_page(self):
        self.show_page((self.stack.currentIndex() + 1) % len(PAGES))

    def show_track(self, track):
        self.cover.show_track(track)
        self.lyrics.show_track(track)
        self.song_info.show_track(track)
        self.vu_page.show_track(track)
        self.amp.show_track(track)
        self.lava.show_track(track)

    def set_position(self, seconds, duration=0.0):
        self.lyrics.set_position(seconds)
        self.vu_page.lyrics.set_position(seconds, duration)
        self.amp.set_position(seconds, duration)
        self.lava.set_position(seconds, duration)

    def set_levels(self, rms, peak):
        self.vu.set_levels(rms, peak)
        self._music_levels = (time.monotonic(), rms, peak) if rms else None
        self._feed_amp()
        self.mixer.set_music_levels(rms, peak)
        self.lava.set_levels(rms, peak)

    # The amplifier's meters: what it plays, the music and, the mixer on, what the mixer plays there too (the mics
    # heard with TO AMPLIFIER, Blink's call, an app's sound heard through the mixer), summed as their powers add up

    HEARD_FRESH = 0.3       # s: a source's last levels count this long (then it is taken as gone quiet)

    _music_levels = None
    _heard = None

    def set_heard_levels(self, key, rms):
        """A mixer input's levels as played on the amplifier's output (rms dB per channel); [] not heard there."""
        if self._heard is None:
            self._heard = {}
        if rms:
            self._heard[key] = (time.monotonic(), rms)
        elif self._heard.pop(key, None) is None:
            return
        self._feed_amp()

    def _feed_amp(self):
        now = time.monotonic()
        sources = []        # (rms, peak), dB per channel
        if self._music_levels is not None:
            sources.append((self._music_levels[1], self._music_levels[2]))
        for key, (when, rms) in list((self._heard or {}).items()):
            if now - when > self.HEARD_FRESH:
                del self._heard[key]
            else:
                sources.append((rms, rms))
        if not sources:
            self.amp.set_levels([], [])
            return
        if len(sources) == 1:
            self.amp.set_levels(*sources[0])
            return

        def pair(values):
            values = [v if isinstance(v, (int, float)) and not math.isnan(v) else -200.0 for v in values[:2]]
            return values * 2 if len(values) == 1 else values

        rms = [10 * math.log10(max(1e-20, sum(10 ** (pair(r)[c] / 10) for r, p in sources))) for c in (0, 1)]
        peak = [max(pair(p)[c] for r, p in sources) for c in (0, 1)]
        self.amp.set_levels(rms, peak)
