"""The Amplifier view: a retro integrated amplifier's front panel. Two backlit VU meters (the output: the level
after the tone, the balance and the volume, as an amplifier's own meters) and four knobs that do what they say:
volume (the window's volume, 0..10; the 11 past it is not a position but a link, for the curious), bass and treble (+/-12 dB, equalizer-3bands' 100 Hz and 11 kHz bands) and balance
(player.py). Bass, treble and balance are kept in the settings (amp/...), and the front plate (shiny or dark
aluminium, the panel's context menu).

Beside the meter window: left of it the meter lever (the window's style), right of it the output selector, a
rotary switch with a position for each output device (System default first, then the devices as the Output
device menu lists them): a click on a position turns it there, a click on its cap one position on, the wheel over
it a position a notch.

A knob: a click on its edge or its scale turns it there, and it follows the mouse around while held; dragged up (or
right) from its middle to turn it up, the Shift key for fine steps; the mouse wheel anywhere in its column of
the knobs' row; a double click back to the middle (not the volume). The wheel elsewhere on the panel turns the volume.
"""

import math
import random
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (QColor, QConicalGradient, QDesktopServices, QFont, QFontMetricsF, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPixmap, QRadialGradient)
from PyQt6.QtWidgets import QMenu, QToolButton, QToolTip, QWidget

from cutemusic import util
from cutemusic.icons import line_path
from cutemusic.vufaces import AnalogFaces


__all__ = ['AmpView']


BACKGROUND = QColor('#101318')
# the meter window's styles: its own amber needles, or the VU meter page's (visuals.VUMeter) drawn in it, those
# that fit a wide and short window (not the LEDs: they need the height). The lever's positions, top down
WHY_11 = 'https://www.youtube.com/watch?v=uMSV4OteqBE'       # why the volume goes to 11
METERS = [('analog', 'ANALOG'), ('bars', 'LED BAR'), ('spectrum', 'SPECTRUM')]
# the front plate (amp/plate, the panel's context menu and View -> Amplifier plate): as the theme (the default: dark
# with a dark theme, aluminium with a light one), shiny brushed aluminium, or dark (black anodised) aluminium with
# light lettering
PLATES = [('theme', 'Follow theme'), ('aluminium', 'Aluminium plate'), ('dark', 'Dark plate')]
# the knobs' caps (amp/knobs, the same menus): spun aluminium (the default), black anodised, or as the plate is
KNOBS = [('plate', 'Follow plate'), ('aluminium', 'Aluminium knobs'), ('dark', 'Dark knobs')]
_PLATE_COLORS = {
    'aluminium': {'metal': ('#e4e6e9', '#c9ccd1', '#aeb2b8'), 'grain': (40, 22), 'edges': (140, 90),
                  'ink': '#2b2e33', 'print': '#4a4e55', 'red': (179, 38, 30), 'escutcheon': ('#f2f3f5', '#9da1a8'),
                  'rim': '#6d7178', 'screw': ('#b9bcc2', '#55595f')},
    'dark': {'metal': ('#34373c', '#25272b', '#18191c'), 'grain': (14, 30), 'edges': (45, 140),
             'ink': '#d8dbdf', 'print': '#a4a8af', 'red': (232, 72, 60), 'escutcheon': ('#4b4e54', '#1d1f22'),
             'rim': '#0b0c0e', 'screw': ('#8c9097', '#2a2c30')},
}



OTHER_OUTPUTS = '\u2026'     # OUTPUT's fifth position with no other output played on: a menu of them

class _Knob:
    """A knob's value and range; where it is drawn is set by the view's layout."""

    SWEEP = 135.0       # degrees each side of the top

    def __init__(self, key, label, low, high, value, step, default=None, ends=('', ''), limit=None):
        self.key, self.label = key, label
        self.low, self.high, self.value, self.step = low, high, value, step
        self.limit = high if limit is None else limit       # the most it turns to (the volume: 10 of a scale to 11)
        self.default = default
        self.ends = ends
        self.centre = QPointF()
        self.radius = 0.0
        self.enabled = True

    def ratio(self):
        return (self.value - self.low) / (self.high - self.low)

    def angle(self, ratio=None):
        """Degrees clockwise from the top."""
        return -self.SWEEP + 2 * self.SWEEP * (self.ratio() if ratio is None else ratio)

    def set(self, value):
        value = round(max(self.low, min(self.limit, value)) / self.step) * self.step
        if abs(value - self.value) < 1e-9:
            return False
        self.value = value
        return True

    def contains(self, point):
        """point on the knob or its scale."""
        return self.enabled and self.distance(point) <= self.radius * 1.6

    def distance(self, point):
        return math.hypot(point.x() - self.centre.x(), point.y() - self.centre.y())

    def ratio_at(self, point, last):
        """The ratio (0..1) the knob points to at point; in the gap at the bottom, the end nearer last."""
        angle = math.degrees(math.atan2(point.x() - self.centre.x(), self.centre.y() - point.y()))   # clockwise from the top
        if abs(angle) <= self.SWEEP:
            return (angle + self.SWEEP) / (2 * self.SWEEP)
        return 1.0 if last >= 0.5 else 0.0

    def text(self):
        if self.key == 'volume':
            return f'Volume {self.value / 10:.1f}' + (' (max)' if self.value == 100 else '')
        if self.key == 'balance':
            if abs(self.value) < 0.025:
                return 'Balance: centre'
            return f'Balance: {abs(self.value) * 100:.0f}% {"right" if self.value > 0 else "left"}'
        return f'{self.label.title()} {self.value:+.1f} dB'


class _Selector(_Knob):
    """The output selector: a rotary switch, a position an output (None the system's default), STEP degrees apart,
    centred on the top."""

    STEP = 36.0

    def __init__(self):
        super().__init__('output', 'OUTPUT', 0, 1, 0, 1)
        self.names = [None]
        self.set_names([None], None)

    def set_names(self, names, current):
        self.names = list(names)
        count = len(self.names)
        self.high = max(1, count - 1)
        self.limit = self.high
        # its positions over the volume knob's sweep (135 degrees each side of the top), from min to max as it
        # (fewer than five: STEP apart, centred on the top)
        self.SWEEP = 135.0 if count >= 5 else min(135.0, self.STEP * (count - 1) / 2)
        self.value = self.names.index(current) if current in self.names else 0

    def ratio(self):
        return 0.5 if len(self.names) < 2 else super().ratio()

    def position_at(self, point):
        """The position nearest to where point is around the knob."""
        angle = math.degrees(math.atan2(point.x() - self.centre.x(), self.centre.y() - point.y()))
        return min(range(len(self.names)), key=lambda index: abs(self.position_angle(index) - angle))

    def position_angle(self, index):
        return self.angle(0.5 if len(self.names) < 2 else index / self.high)

    def current(self):
        return self.names[int(self.value)]

    @staticmethod
    def title(name):
        return 'Other outputs' if name == OTHER_OUTPUTS else (name or 'System default')


class _StyleKnob(_Selector):
    """The meter window's style (METERS): a rotary switch as the others, left of the window, its three positions on
    its left (the first up, as the lever's were top down), each named past its tick."""

    def set_names(self, names, current):
        super().set_names(names, current)
        self.SWEEP = 35.0           # degrees each side of its middle position, the left: its names close together

    def angle(self, ratio=None):
        ratio = self.ratio() if ratio is None else ratio
        return -90.0 + self.SWEEP - 2 * self.SWEEP * ratio      # (clockwise from the top: up left to down left)

    @staticmethod
    def title(name):
        return dict(METERS).get(name, name or '')


def draw_knob(painter, knob):
    """A knob as the amplifier's (its skirt, cap and pointer), at knob.centre, knob.radius: for the mixer too."""
    AmpView._draw_knob(None, painter, knob)


class AmpView(AnalogFaces, QWidget):
    """volume_changed(0..100), tone_changed(bass dB, treble dB), balance_changed(-1..1): turned by hand;
    output_chosen(name, '' the system's default): the output selector turned; outputs_wanted: shown, its positions
    to be brought up to date (set_outputs)."""

    volume_changed = pyqtSignal(int)
    extra_changed = pyqtSignal(float)       # dB over the top (the knob between 10 and 11), 0 under it
    tone_changed = pyqtSignal(float, float)
    balance_changed = pyqtSignal(float)
    output_chosen = pyqtSignal(str)
    mute_toggled = pyqtSignal(bool)
    outputs_wanted = pyqtSignal()

    # 0 VU follows the music (a trim pot turned by itself, as the VU page's needles): its loudness HEADROOM under
    # 0 VU, up in TRIM_UP s, down in TRIM_DOWN s, within TRIM_RANGE dBFS; the needles show the music's level, as
    # the mixer's do: VOLUME does not move them
    HEADROOM = 3.0
    TRIM_UP, TRIM_DOWN = 1.0, 10.0
    TRIM_RANGE = (-24.0, 0.0)
    TRIM_FLOOR = -40.0      # quieter than this (silences, fades): the trim left where it is (they would turn it all the way up)
    REFERENCE = -18.0       # 0 VU at first
    FLOOR = -70.0
    # the needles: a mass on a spring (as a VU meter's movement: there in about 300 ms, a hair past it, back),
    # its natural frequency (rad/s) and damping ratio; the level they follow is the latest, as it comes
    SPRING, DAMPING = 16.0, 0.78
    STALE = 0.3
    EXTRA = 6.0             # dB at 11
    ASPECT = 2.8            # the panel's width to its height
    VOLUME_SIZE = 1.4       # the volume knob, this much bigger than the others (as big as fits under the meters)
    web = False             # drawn for the web page (web.amp_picture): no mute; the selector's marks and name,
                            # its positions being the page's, drawn by the page

    @classmethod
    def extra_db(cls, value):
        return max(0.0, value - 100) / 10 * cls.EXTRA

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 200)
        self.setMouseTracking(True)
        settings = util.settings()
        self.volume = _Knob('volume', 'VOLUME', 0, 110, 80, 1, ends=('0', '11'), limit=100)     # to 10; the 11 a link
        self.bass = _Knob('bass', 'BASS', -12.0, 12.0, settings.value('amp/bass', 0.0, type=float), 0.5, 0.0, ('−', '+'))
        self.treble = _Knob('treble', 'TREBLE', -12.0, 12.0, settings.value('amp/treble', 0.0, type=float), 0.5, 0.0, ('−', '+'))
        self.balance = _Knob('balance', 'BALANCE', -1.0, 1.0, settings.value('amp/balance', 0.0, type=float), 0.05, 0.0, ('L', 'R'))
        self.knobs = [self.balance, self.bass, self.treble, self.volume]       # left to right
        self.selector = _Selector()     # not one of the knobs' row: beside the meter window
        self.style_knob = _StyleKnob()      # the meter window's style, left of it (as the selector is right of it)
        # what switches it: the slide lever (at first) or the style knob (the amplifier's menu: Meter switch)
        self.meter_switch = util.settings().value('amp/meter_switch', 'lever', type=str)
        if self.meter_switch not in ('lever', 'knob'):
            self.meter_switch = 'lever'
        self.style_knob.set_names([key for key, label in METERS], 'analog')
        self._output_timer = QTimer(self, singleShot=True, interval=400)
        self._output_timer.timeout.connect(lambda: self.output_chosen.emit(self.selector.current() or ''))
        self.rms = [self.FLOOR, self.FLOOR]         # the music's level (before the volume), smoothed: the trim's
        self.needles = [0.0, 0.0]                   # where the needles are (0..1 of the scale), and how fast
        self.speeds = [0.0, 0.0]
        self.reference = self.REFERENCE                 # dBFS at 0 VU
        self.target = [self.FLOOR, self.FLOOR]
        self.peak = [self.FLOOR, self.FLOOR]
        self.peak_lit = [0.0, 0.0]
        self.received = 0.0
        self.last = time.monotonic()
        self._drag = None           # (knob, start point, start value)
        self._panel = None          # the static parts, drawn once a size
        self.output_icons = {}      # {output name, None the default: a line icon's name}, around OUTPUT
        self._panel_key = None      # (width, height, pixel ratio) it was drawn for
        self._wheel_rest, self._wheel_knob_last = 0.0, None
        self._layout = None
        self.timer = QTimer(self, interval=16)
        self.timer.timeout.connect(self._tick)
        # the meter window's style: a lever on the panel (its positions: METERS); the VU meter page's styles drawn in
        # the window
        from cutemusic.visuals import LyricsStrip, VUMeter
        self.lyrics = LyricsStrip(self)     # the lyrics being sung, under the amplifier (as the VU meter page's)
        # its size: a share of the room it has (amp/size), − and + in the top left corner, Ctrl+wheel over it
        self.amp_size = min(1.0, max(self.SIZES[0], settings.value('amp/size', 1.0, type=float)))
        self.size_buttons = []
        for text, tip, step in (('−', 'Smaller amplifier (Ctrl+wheel)', -1), ('+', 'Bigger amplifier (Ctrl+wheel)', 1)):
            button = QToolButton(self)
            button.setText(text)
            button.setToolTip(tip)
            button.setAutoRaise(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            # as the lyrics' A- A+ (visuals.LyricsStrip) at the bottom right, here at the top left, a little bigger
            from cutemusic.visuals import DIM, TEXT
            font = QFont(self.font())
            font.setPointSizeF(font.pointSizeF() * 1.05)
            font.setBold(True)
            button.setFont(font)
            button.setStyleSheet(f'QToolButton {{ color: {DIM.name()}; border: none; padding: 1px 2px; }}'
                                 f'QToolButton:hover {{ color: {TEXT.name()}; }}  QToolButton:disabled {{ color: #3a3f48; }}')
            button.adjustSize()
            button.clicked.connect(lambda checked=False, step=step: self.resize_by(step))
            self.size_buttons.append(button)
        self._size_buttons_enabled()

        class _Meter(VUMeter):          # its style is the lever's, not a click's; the window is its frame
            framed = False

            def mouseReleaseEvent(self, event):
                event.ignore()
        self.meter = _Meter(self)
        self.meter.setMinimumSize(0, 0)
        self.meter.setCursor(Qt.CursorShape.ArrowCursor)
        self.meter.setToolTip('')
        self.meter.hide()
        self._lever = None      # (pivot, the positions' points, hit rectangle): set by the layout
        self._mute = None       # the mute button's rectangle: set by the layout
        self.muted = False
        self._why = None        # the 11, a link: its rectangle, set when the panel is drawn
        self._lever_angle = None
        plate = settings.value('amp/plate', 'theme', type=str)
        self.plate = plate if plate in dict(PLATES) else 'theme'
        knobs = settings.value('amp/knobs', 'aluminium', type=str)
        self.knob_style = knobs if knobs in dict(KNOBS) else 'aluminium'     # (self.knobs: the knobs themselves)
        style = settings.value('amp/meter', 'analog', type=str)
        self.set_meter_style(style if style in [key for key, label in METERS] else 'analog')

    def set_plate(self, plate):
        """The front plate: 'theme', 'aluminium' or 'dark' (PLATES), kept in the settings."""
        if plate not in dict(PLATES):
            return
        self.plate = plate
        util.settings().setValue('amp/plate', plate)
        self._panel = None
        self.update()

    def set_knobs(self, knobs):
        """The knobs' caps: 'plate', 'aluminium' or 'dark' (KNOBS), kept in the settings."""
        if knobs not in dict(KNOBS):
            return
        self.knob_style = knobs
        util.settings().setValue('amp/knobs', knobs)
        self._panel = None
        self.update()

    def dark_knobs(self):
        if self.knob_style == 'plate':
            return self._colors() is _PLATE_COLORS['dark']
        return self.knob_style == 'dark'

    def _colors(self):
        plate = self.plate
        if plate == 'theme':        # the theme's windows dark or light
            plate = 'dark' if self.palette().color(self.palette().ColorRole.Window).lightness() < 128 else 'aluminium'
        return _PLATE_COLORS[plate]

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == event.Type.PaletteChange and self.plate == 'theme':
            self._panel = None
            self.update()

    SIZES = [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]     # of the room it has

    def resize_by(self, step):
        """A size smaller (-1) or bigger (+1), kept in the settings."""
        index = min(range(len(self.SIZES)), key=lambda i: abs(self.SIZES[i] - self.amp_size))
        index = max(0, min(len(self.SIZES) - 1, index + step))
        if self.SIZES[index] == self.amp_size:
            return
        self.amp_size = self.SIZES[index]
        util.settings().setValue('amp/size', self.amp_size)
        self._size_buttons_enabled()
        self._panel = None
        self._compute_layout()
        self.update()

    def _size_buttons_enabled(self):
        self.size_buttons[0].setEnabled(self.amp_size > self.SIZES[0])
        self.size_buttons[1].setEnabled(self.amp_size < self.SIZES[-1])

    def contextMenuEvent(self, event):
        self._plate_menu(event.globalPos())

    def _plate_menu(self, position):
        """The plates (PLATES), the one used ticked: the panel's context menu and the menu button after the model."""
        menu = QMenu(self)
        for key, label in PLATES:
            action = menu.addAction(label, lambda key=key: self.set_plate(key))
            action.setCheckable(True)
            action.setChecked(key == self.plate)
        menu.addSeparator()
        for key, label in KNOBS:
            action = menu.addAction(label, lambda key=key: self.set_knobs(key))
            action.setCheckable(True)
            action.setChecked(key == self.knob_style)
        menu.addSeparator()
        for key, label in (('lever', 'Meter switch: lever'), ('knob', 'Meter switch: knob')):
            action = menu.addAction(label, lambda key=key: self.set_meter_switch(key))
            action.setCheckable(True)
            action.setChecked(key == self.meter_switch)
        menu.addSeparator()
        from cutemusic.visuals import SongInfo      # (the song's technical data under the visuals' pages)
        action = menu.addAction('Show file info', lambda: SongInfo.set_enabled(not SongInfo.enabled()))
        action.setCheckable(True)
        action.setChecked(SongInfo.enabled())
        menu.exec(position)

    def set_meter_style(self, style):
        self.meter_style = style
        if hasattr(self, 'style_knob') and style in self.style_knob.names:
            self.style_knob.value = self.style_knob.names.index(style)
            self._panel = None      # (its tick: the chosen one longer)
        util.settings().setValue('amp/meter', style)
        if style != 'analog':
            self.meter.set_style(style)
        self.meter.setVisible(style != 'analog')
        self.update()

    # From the window

    def show_track(self, track):
        self.lyrics.show_track(track)       # hidden when there are none: their room kept (the amplifier does not move)
        self._panel = None
        self._compute_layout()
        self.update()

    def set_position(self, seconds, duration=0.0):
        self.lyrics.set_position(seconds, duration)

    def set_spectrum(self, magnitudes, rate):
        self.meter.set_spectrum(magnitudes, rate)

    def set_volume(self, value):
        """The slider's (0..100)."""
        if self.volume.set(value):
            self.update()

    def set_output_icons(self, icons):
        """{output name (None the default): a line icon's name (icons.line_path)}: shown past each position's tick."""
        if icons != self.output_icons:
            self.output_icons = dict(icons)
            self._panel = None
            self.update()

    default_output_name = None      # the device the system's default output is now (macOS), for its tooltip
    unavailable_outputs = frozenset()      # positions kept for outputs not there now (the headphones unplugged): grey
    other_outputs = ()      # the outputs past the fixed positions: the last position's menu
    usb_outputs = ()        # the USB outputs, the last used first: their position's menu when more than one

    def set_outputs(self, names, current, unavailable=(), others=(), usb=()):
        """The output selector's positions: the devices' names (the system's default first, added here), the one
        played on (None the default); unavailable: names among them not there now, shown grey and skipped; others:
        the outputs the last position's menu offers (it the one of them played on, else OTHER_OUTPUTS); usb: the USB
        outputs, the last used first (their position the one played on, else the last used; a menu when more than
        one, none for a single one)."""
        names = [None] + list(names)
        unavailable = frozenset(unavailable)
        others = tuple(others)
        usb = tuple(usb)
        if names == self.selector.names and current == self.selector.current() and unavailable == self.unavailable_outputs \
                and others == self.other_outputs and usb == self.usb_outputs:
            return
        self.unavailable_outputs = unavailable
        self.other_outputs = others
        self.usb_outputs = usb
        self.selector.set_names(names, current if current not in unavailable else None)
        self._panel = None
        self.update()

    def set_muted(self, muted):
        if muted != self.muted:
            self.muted = muted
            if self._mute is not None:
                self.update(self._mute.adjusted(-8, -8, 8, 8).toAlignedRect())

    def _on_mute(self, point):
        return self._mute is not None and self._mute.adjusted(-3, -3, 3, 3).contains(point)

    def set_output(self, name):
        """The output played on now (None the default): the selector turned to it, its positions as they are."""
        selector = self.selector
        if self._output_timer.isActive():       # turned by hand, not switched yet: it stays where it was turned
            return
        if name is not None and name not in selector.names:     # one of a menu's (USB, other outputs): its position shows it
            usb, other = name in self.usb_outputs, name in self.other_outputs
            for index, there in enumerate(selector.names):
                if (usb and there in self.usb_outputs) or (other and (there in self.other_outputs or there == OTHER_OUTPUTS)):
                    selector.names[index] = name
                    self._panel = None
                    self.update()
                    break
        index = selector.names.index(name) if name in selector.names else 0
        if index != int(selector.value):
            selector.value = index
            self._panel = None
            self.update()

    def set_tone_available(self, available):
        for knob in (self.bass, self.treble, self.balance):
            knob.enabled = available
        self.setToolTip('' if available else 'Bass, treble and balance need gst-plugins-good (equalizer, audiofx)')
        self._panel = None
        self.update()

    def _wake(self):
        """The timer again (stopped when the needles came to rest, paused or stopped): levels are coming."""
        if self.isVisible() and not self.timer.isActive():
            self.last = time.monotonic()
            self.timer.start()

    def set_levels(self, rms, peak):
        self.meter.set_levels(rms, peak)
        self._wake()
        if not rms:
            self.target = [self.FLOOR, self.FLOOR]
            return
        if len(rms) == 1:
            rms, peak = rms * 2, peak * 2
        self.received = time.monotonic()
        # the volume, cubic (player.set_volume), and the EXTRA past 10
        self.target = [self.FLOOR if math.isnan(v) else max(self.FLOOR, v) for v in rms[:2]]

    # Turning the knobs

    def _turn(self, knob, value):
        if not knob.set(value):
            return
        if knob is self.volume:
            self.volume_changed.emit(int(min(100, knob.value)))
            self.extra_changed.emit(self.extra_db(knob.value))
        elif knob is self.balance:
            util.settings().setValue('amp/balance', knob.value)
            self.balance_changed.emit(float(knob.value))
        else:
            util.settings().setValue(f'amp/{knob.key}', knob.value)
            self.tone_changed.emit(float(self.bass.value), float(self.treble.value))
        self.update()

    def _knob_at(self, point):
        return next((knob for knob in self.knobs if knob.contains(point)), None)

    CLICK_RING = 0.45       # a press farther than this (of the radius) from a knob's centre turns it to there

    def _on_plate_button(self, point):
        return getattr(self, '_plate_button', None) is not None and self._plate_button.contains(point)

    def mousePressEvent(self, event):
        """A click on a knob's edge or scale turns it to the scale's mark nearest where it was clicked (the volume, at most to 10: 11 is turned
        to, not jumped to), then it follows the mouse around; on its middle, it is dragged up and down."""
        if event.button() == Qt.MouseButton.LeftButton and self._on_why(event.position()):
            QDesktopServices.openUrl(QUrl(WHY_11))
            return
        if event.button() == Qt.MouseButton.LeftButton and self._on_plate_button(event.position()):
            button = self._plate_button
            self._plate_menu(self.mapToGlobal(QPointF(button.right(), button.bottom()).toPoint()))
            return
        if event.button() == Qt.MouseButton.LeftButton and self._on_lever(event.position()):
            knob, point = self.style_knob, event.position()
            pivot, points, hit = self._lever
            if self.meter_switch == 'lever':        # the position nearest, up and down
                index = min(range(len(points)), key=lambda i: abs(points[i].y() - point.y()))
            elif knob.distance(point) <= knob.radius * self.CLICK_RING:       # on its cap: the next one, round
                index = (int(knob.value) + 1) % len(METERS)
            else:       # a position, its tick or its name: the nearest
                index = min(range(len(points)), key=lambda i: math.hypot(points[i].x() - point.x(), points[i].y() - point.y()))
            self.set_meter_style(METERS[index][0])
            QToolTip.showText(event.globalPosition().toPoint(), f'Meter: {METERS[index][1].title()}', self)
            return
        if event.button() == Qt.MouseButton.LeftButton and self._on_mute(event.position()):
            self.set_muted(not self.muted)
            self.mute_toggled.emit(self.muted)
            return
        if event.button() == Qt.MouseButton.LeftButton and self._on_selector(event.position()):
            selector = self.selector
            if selector.distance(event.position()) > selector.radius * self.CLICK_RING:
                icon = self._output_icon_at(event.position())
                index = icon if icon is not None else selector.position_at(event.position())
                if self.other_outputs is not None and index == len(selector.names) - 1 and self._has_other_position():
                    self._other_menu(event.globalPosition().toPoint())      # the last one: the other outputs, a menu
                    return
                if self._is_usb_menu(selector.names[index]):
                    self._usb_menu(event.globalPosition().toPoint())        # USB, more than one: a menu of them
                    return
            else:       # on its cap: a position on (the grey ones, and the menu's with none chosen, skipped)
                index = int(selector.value)
                for _ in selector.names:
                    index = (index + 1) % len(selector.names)
                    if not self._unchoosable(selector.names[index]):
                        break
            if selector.names[index] in self.unavailable_outputs:
                QToolTip.showText(event.globalPosition().toPoint(), f'{_Selector.title(selector.names[index])}: not connected', self)
                return
            self._choose_output(index, event)
            return
        knob = self._knob_at(event.position())
        if knob is None or event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position()
        around = knob.distance(point) > knob.radius * self.CLICK_RING
        if around:      # to the scale's mark nearest the click (its ticks: _draw_scale's), dragged on from there
            marks = 11 if knob is self.volume else 10
            ratio = round(knob.ratio_at(point, knob.ratio()) * marks) / marks
            value = knob.low + ratio * (knob.high - knob.low)
            if knob is self.volume and knob.value <= 100:
                value = min(100, value)
            self._turn(knob, value)
        self._drag = (knob, point, knob.value, around)
        self._show_value(knob, event)

    def mouseMoveEvent(self, event):
        if self._drag is None:
            over = self._knob_at(event.position()) or self._on_lever(event.position()) or self._on_why(event.position()) \
                or self._on_selector(event.position()) or self._on_mute(event.position()) or self._on_plate_button(event.position())
            self.setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.ArrowCursor)
            if self._on_why(event.position()):
                QToolTip.showText(event.globalPosition().toPoint(), WHY_11, self)
            elif self._on_plate_button(event.position()):
                QToolTip.showText(event.globalPosition().toPoint(), 'Front plate', self)
            elif self._on_mute(event.position()):
                QToolTip.showText(event.globalPosition().toPoint(), 'Unmute' if self.muted else 'Mute', self)
            elif self._on_lever(event.position()):
                QToolTip.showText(event.globalPosition().toPoint(),
                                  f'Meter: {dict(METERS)[self.meter_style].title()} (click: another one)', self)
            elif self._on_selector(event.position()):
                selector = self.selector
                if selector.distance(event.position()) > selector.radius * self.CLICK_RING:
                    icon = self._output_icon_at(event.position())
                    name = selector.names[icon if icon is not None else selector.position_at(event.position())]
                    text = _Selector.title(name)
                    if name is None and self.default_output_name:       # the default: which device it is now
                        text += f' ({self.default_output_name})'
                    if self._is_usb_menu(name):
                        text = f'{name} (USB) (click: choose one of {len(self.usb_outputs)})'
                    elif name == OTHER_OUTPUTS or (name in self.other_outputs and name == selector.names[-1]):
                        text = ('Other outputs' if name == OTHER_OUTPUTS else f'{name} (other outputs)') + \
                            f' (click: choose one of {len(self.other_outputs)})'
                    elif name in self.unavailable_outputs:
                        text += ': not connected'
                    else:
                        text += '' if name == selector.current() else ' (click: play on it)'
                else:
                    text = f'Output: {_Selector.title(selector.current())} (click: the next one)'
                QToolTip.showText(event.globalPosition().toPoint(), text, self)
            return
        knob, start, value, around = self._drag
        point = event.position()
        if around and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier and \
                knob.distance(point) > knob.radius * self.CLICK_RING:
            self._turn(knob, knob.low + knob.ratio_at(point, knob.ratio()) * (knob.high - knob.low))
        else:       # up and down (Shift: fine), from where it is now
            if around:
                self._drag = knob, point, knob.value, False
                start, value = point, knob.value
            moved = (start.y() - point.y()) + (point.x() - start.x())
            travel = 220.0 * (5 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
            self._turn(knob, value + moved / travel * (knob.high - knob.low))
        self._show_value(knob, event)

    def mouseReleaseEvent(self, event):
        self._drag = None

    def _on_why(self, point):
        return self._why is not None and self._why.adjusted(-4, -4, 4, 4).contains(point)

    def _on_lever(self, point):
        return self._lever is not None and self._lever[2].contains(point)

    def _on_selector(self, point):
        if self._layout is None:
            return False
        return self.selector.distance(point) <= self.selector.radius * 1.5 or self._output_icon_at(point) is not None

    def _output_icon_at(self, point):
        """The position of the output whose icon (or letter) past its tick is under point (_draw_selector_scale:
        at ICON_RING radii, ICON_SIZE big), else None: hovered, its output named; clicked, played on."""
        selector = self.selector
        if self.web or not self.output_icons:
            return None
        for index, name in enumerate(selector.names):
            if not self.output_icons.get(name):
                continue
            angle = math.radians(selector.position_angle(index) - 90)
            where = selector.centre + QPointF(math.cos(angle), math.sin(angle)) * selector.radius * self.ICON_RING
            if math.hypot(point.x() - where.x(), point.y() - where.y()) <= selector.radius * self.ICON_SIZE * 0.6:
                return index
        return None

    def _has_other_position(self):
        return bool(self.selector.names) and (self.selector.names[-1] == OTHER_OUTPUTS or self.selector.names[-1] in self.other_outputs)

    def _is_usb_menu(self, name):
        return name in self.usb_outputs and len(self.usb_outputs) > 1

    def _usb_menu(self, position):
        """The USB position, more than one plugged in: them, the last used first, the one played on ticked."""
        menu = QMenu(self)
        current = self.selector.current()
        for name in self.usb_outputs:
            action = menu.addAction(name, lambda name=name: self.output_chosen.emit(name))
            action.setCheckable(True)
            action.setChecked(name == current)
        menu.exec(position)

    def _unchoosable(self, name):
        return name in self.unavailable_outputs or name == OTHER_OUTPUTS

    def _other_menu(self, position):
        """The last position: the outputs past the fixed ones, the one played on ticked; chosen, played on."""
        menu = QMenu(self)
        if not self.other_outputs:
            menu.addAction('No other outputs').setEnabled(False)
        current = self.selector.current()
        for name in self.other_outputs:
            action = menu.addAction(name, lambda name=name: self.output_chosen.emit(name))
            action.setCheckable(True)
            action.setChecked(name == current)
        menu.exec(position)

    def _choose_output(self, index, event, step=0):
        """index; step (the wheel: its direction) past the grey positions (and the menu's with none chosen), else
        they are not chosen."""
        selector = self.selector
        index = max(0, min(len(selector.names) - 1, index))
        while self._unchoosable(selector.names[index]):
            if not step or not 0 <= index + step < len(selector.names):
                return
            index += step
        if index != int(selector.value):
            selector.value = index
            self._panel = None          # the name under it
            self.update()
            # switched once it rests (the wheel turned through several positions: one switch, to the last), from
            # the event loop
            self._output_timer.start()
        QToolTip.showText(event.globalPosition().toPoint(), f'Output: {_Selector.title(selector.current())}', self)

    def mouseDoubleClickEvent(self, event):
        knob = self._knob_at(event.position())
        if knob is not None and knob.default is not None:
            self._turn(knob, knob.default)
            self._show_value(knob, event)

    def _wheel_knob(self, point):
        """The knob the wheel turns at point: anywhere in the knobs' row (under the meters), the one whose column it
        is in; elsewhere, the volume."""
        if self._layout is None:
            return self.volume
        inner, window = self._layout['inner'], self._layout['window']
        if not (inner.left() <= point.x() <= inner.right() and window.bottom() < point.y() <= inner.bottom()):
            return self.volume
        knob = min(self.knobs, key=lambda knob: abs(knob.centre.x() - point.x()))
        return knob if knob.enabled else None

    def wheelEvent(self, event):
        event.accept()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:      # the amplifier's size, a notch a size
            delta = event.angleDelta().y() or event.angleDelta().x()
            if event.inverted():
                delta = -delta
            self._size_rest = getattr(self, '_size_rest', 0.0) + delta / 120
            steps = int(self._size_rest)
            if steps:
                self._size_rest -= steps
                self.resize_by(steps)
            return
        if self._on_selector(event.position()):     # a notch a position: up turns it clockwise
            delta = event.angleDelta().y() or event.angleDelta().x()
            if event.inverted():
                delta = -delta
            self._selector_rest = getattr(self, '_selector_rest', 0.0) + delta / 120
            steps = int(self._selector_rest)
            if steps:
                self._selector_rest -= steps
                self._choose_output(int(self.selector.value) + steps, event, step=1 if steps > 0 else -1)
            return
        if self._on_lever(event.position()):        # a notch a position: up the one above
            delta = event.angleDelta().y() or event.angleDelta().x()
            if event.inverted():
                delta = -delta
            self._lever_rest = getattr(self, '_lever_rest', 0.0) + delta / 120
            steps = int(self._lever_rest)
            if steps:
                self._lever_rest -= steps
                keys = [key for key, label in METERS]
                index = max(0, min(len(keys) - 1, keys.index(self.meter_style) - steps))
                self.set_meter_style(keys[index])
            return
        knob = self._wheel_knob(event.position())
        if knob is None:
            return
        delta = event.angleDelta().y() or event.angleDelta().x()
        if event.inverted():
            delta = -delta
        # a notch (120) a step (5 for the volume); a trackpad's small deltas added up until they make one
        if knob is not self._wheel_knob_last:
            self._wheel_rest, self._wheel_knob_last = 0.0, knob
        self._wheel_rest += delta / 120 * (5 if knob is self.volume else 1)
        steps = int(self._wheel_rest)
        if not steps:
            return
        self._wheel_rest -= steps
        self._turn(knob, knob.value + steps * knob.step)
        self._show_value(knob, event)

    def _show_value(self, knob, event):
        QToolTip.showText(event.globalPosition().toPoint(), knob.text(), self)

    # The needles

    def showEvent(self, event):
        self.last = time.monotonic()
        self.timer.start()
        self.outputs_wanted.emit()

    def hideEvent(self, event):
        self.timer.stop()

    def _tick(self):
        now = time.monotonic()
        elapsed, self.last = now - self.last, now
        if now - self.received > self.STALE:
            self.target = [self.FLOOR, self.FLOOR]
        follow = 1 - math.exp(-elapsed / 0.3)
        for channel in range(2):
            self.rms[channel] += (self.target[channel] - self.rms[channel]) * follow
        loudness = max(self.rms)
        if loudness > self.TRIM_FLOOR:      # not in the silences and fades: they would turn it all the way up
            target = min(self.TRIM_RANGE[1], max(self.TRIM_RANGE[0], loudness + self.HEADROOM))
            speed = self.TRIM_UP if target > self.reference else self.TRIM_DOWN
            self.reference += (target - self.reference) * min(1.0, elapsed / speed)
        silent = all(target <= self.FLOOR for target in self.target)
        settled = silent and all(abs(rms - self.FLOOR) < 0.5 for rms in self.rms)
        if self.meter_style != 'analog':        # the meter widget draws itself: nothing here to redraw
            if settled:
                self.timer.stop()               # at rest: woken by the next levels (set_levels)
            return
        moved = False
        steps = max(1, math.ceil(min(elapsed, 0.1) / 0.004))       # small steps: the spring stays stable
        dt = min(elapsed, 0.1) / steps
        for channel in range(2):
            level = self.target[channel]
            goal = self._needle_ratio(level - self.reference) if level > self.FLOOR else self._needle_ratio(self.VU_MIN - 2)
            position, speed = self.needles[channel], self.speeds[channel]
            for _ in range(steps):
                speed += (self.SPRING ** 2 * (goal - position) - 2 * self.DAMPING * self.SPRING * speed) * dt
                position += speed * dt
            if abs(position - self.needles[channel]) > 0.0004 or abs(speed) > 0.002:
                moved = True
            self.needles[channel], self.speeds[channel] = position, speed
            if position >= self._needle_ratio(self.PEAK_VU):      # the PEAK lamp: the needle well into the red
                self.peak_lit[channel] = now
        lit = any(now - lit < 0.6 for lit in self.peak_lit)
        if settled and not moved and not lit:
            self.timer.stop()                   # at rest: woken by the next levels (set_levels)
            return
        if moved or lit:
            if self._layout is not None:        # only the meter window: the panel and the knobs stay as they are
                self.update(self._layout['window'].adjusted(-2, -2, 2, 2).toAlignedRect())
            else:
                self.update()

    # Drawing

    def resizeEvent(self, event):
        self._panel = None
        self._compute_layout()

    def _compute_layout(self):
        margin = 14.0
        hint = self.fontMetrics().height() * 0.6     # a little room under the amplifier (no hint line)
        gap = round(self.fontMetrics().height() * 1.6)     # the least room between the amplifier and the lyrics
        # the lyrics' room: as tall as they are at their first size, whatever their size now (A- A+ do not move
        # the amplifier); in it, the strip as tall as its font wants, at most all under the amplifier
        from cutemusic.visuals import LyricsStrip
        room = round(self.fontMetrics().height() * LyricsStrip.SCALE * (LyricsStrip.LINES * 1.6 + 1))
        lyrics = room + gap      # kept with or without lyrics: the amplifier stays put from song to song
        below = lyrics + hint
        width = min(self.width() - 2 * margin, (self.height() - 2 * margin - below) * self.ASPECT) * self.amp_size
        height = width / self.ASPECT
        x = 6       # top left (the top right: full screen's restore button)
        for button in self.size_buttons:
            button.move(x, 4)
            x += button.width()
        plate = QRectF((self.width() - width) / 2, (self.height() - height - below) / 2, width, height)
        # the lyrics centred in the room between the amplifier and the bottom (the play bar under it)
        space = self.height() - plate.bottom() - 4
        strip = min(self.lyrics.sizeHint().height(), space)
        self.lyrics.setGeometry(0, round(plate.bottom() + 4 + (space - strip) / 2), self.width(), round(strip))
        cheek = 0.0         # (no wooden cheeks: the aluminium plate the whole width)
        inner = plate.adjusted(cheek, 0, -cheek, 0)
        # the frame: from the left knob's scale to the right one's. In it, the meter window centred, the lever left of
        # it, the output selector right of it, a step and a half apart; as much room each side (the wider's)
        radius = min(inner.height() * 0.1, inner.width() * 0.06)
        first, last = (inner.left() + inner.width() * (index + 0.5) / len(self.knobs) for index in (0, len(self.knobs) - 1))
        frame_left, frame_right = first - radius * 1.34, last + radius * 1.34
        meter_height = inner.height() * 0.31
        span = meter_height / (len(METERS) - 1 + 1.2)        # the lever's step: its plate as tall as the window
        from PyQt6.QtGui import QFontMetricsF
        names = QFontMetricsF(self._lever_font(inner.height()))
        lever_width = span * self.PLATE + span * self.LEVER_NAMES + max(names.horizontalAdvance(label) for key, label in METERS)
        selector_radius = meter_height * 0.22
        selector_width = max(selector_radius * 2.8, inner.height() * 0.34)      # room for the output's name under it
        between = span * 1.5
        # the mute button right of the selector: the line (lever, window, selector, mute) ends at the frame's right,
        # its left a little in from the plate's edge
        mute_size = selector_radius * 1.1
        mute_room = between * 0.5 + mute_size
        line_left = inner.left() + inner.height() * 0.08
        meter_width = min(inner.width() * 0.56, frame_right - line_left - 2 * between - lever_width - selector_width - mute_room)
        line = lever_width + between + meter_width + between + selector_width + mute_room
        left = (line_left + frame_right - line) / 2 + lever_width + between
        left += inner.height() * 0.04       # the whole line a tad right
        lever_x = left - between - span * self.PLATE / 2      # the lever's plate's middle
        window = QRectF(left, inner.top() + inner.height() * 0.185, meter_width, meter_height)
        self.selector.radius = selector_radius
        self.selector.centre = QPointF(window.right() + between + selector_width / 2, window.center().y())
        self._selector_width = selector_width
        mute_x = window.right() + between + selector_width + between * 0.5 + mute_size / 2
        self._mute = QRectF(mute_x - mute_size / 2, window.center().y() - mute_size / 2, mute_size, mute_size)
        # one face across the window (no border between the channels): each channel's half, for its scale and needle;
        # a thin black bezel around it
        meters = self._face_rects(window)
        # the knobs: their scales' ticks clear of the meter window, their names well above the plate's bottom edge
        radius = min(inner.height() * 0.1, inner.width() * 0.06)
        row = inner.top() + inner.height() * 0.73
        for index, knob in enumerate(self.knobs):
            knob.centre = QPointF(inner.left() + inner.width() * (index + 0.5) / len(self.knobs), row)
            knob.radius = radius * (self.VOLUME_SIZE if knob is self.volume else 1.0)
        self._layout = {'plate': plate, 'inner': inner, 'window': window, 'meters': meters, 'cheek': cheek}
        self.meter.setGeometry(window.adjusted(4, 4, -4, -4).toRect())
        if self.meter_switch == 'lever':
            # the lever, left of the meter window: its pivot, its three positions up and down, its labels left of them
            x = lever_x
            # aligned with the meter window: the plate (the positions, 0.6 of a step more at each end) as tall as it,
            # centred on it
            pivot = QPointF(x, window.center().y())
            points = [QPointF(x, pivot.y() + (index - (len(METERS) - 1) / 2) * span) for index in range(len(METERS))]
            hit = QRectF(x - lever_width + span * self.PLATE / 2, points[0].y() - span * 0.5, lever_width + span * 0.15, span * len(METERS))
            self._lever = (pivot, points, hit)
            return
        # the style knob, left of the meter window (where the lever is): as big as the output selector, its
        # positions' names left of their ticks
        knob = self.style_knob
        knob.radius = selector_radius
        knob.centre = QPointF(left - between * 0.6 - selector_radius * 1.4, window.center().y())
        points = []
        for index in range(len(METERS)):
            angle = math.radians(knob.position_angle(index) - 90)
            # its name clear of its tick (the tick ends at 1.38 radii): its right end out at 1.62, a small gap more
            points.append(knob.centre + QPointF(math.cos(angle), math.sin(angle)) * selector_radius * 1.62
                          - QPointF(selector_radius * 0.07, 0))
        hit = QRectF(knob.centre.x() - lever_width - selector_radius * 0.5, knob.centre.y() - selector_radius * 1.8,
                     lever_width + selector_radius * 2.0, selector_radius * 3.6)
        self._lever = (knob.centre, points, hit)

    def _draw_panel(self):
        """The plate, the meter window and faces, the lettering, the knobs' scales: what does not move."""
        self._compute_layout()
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(BACKGROUND)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        layout = self._layout
        plate, inner, window = layout['plate'], layout['inner'], layout['window']
        # a shadow under the amplifier
        painter.setPen(Qt.PenStyle.NoPen)
        for spread, alpha in ((10, 18), (6, 30), (3, 45)):
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(plate.adjusted(-spread, -spread / 2, spread, spread * 1.4), 10 + spread, 10 + spread)
        # brushed aluminium (shiny, or dark)
        colors = self._colors()
        metal = QLinearGradient(0, inner.top(), 0, inner.bottom())
        for stop, color in zip((0.0, 0.5, 1.0), colors['metal']):
            metal.setColorAt(stop, QColor(color))
        light_grain, dark_grain = colors['grain']
        # its corners just rounded, as a plate cut and filed by hand: not sharp, hardly seen as round
        corner = max(1.5, inner.height() * 0.007)
        face = QPainterPath()
        face.addRoundedRect(inner, corner, corner)
        painter.save()
        painter.setClipPath(face)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(metal)
        painter.drawRect(inner)
        brush = random.Random(3)
        y = inner.top()
        while y < inner.bottom():
            light = brush.random() < 0.5
            painter.setPen(QPen(QColor(255, 255, 255, brush.randint(light_grain // 4, light_grain)) if light else QColor(0, 0, 0, brush.randint(dark_grain // 4, dark_grain)), 0.7))
            painter.drawLine(QPointF(inner.left(), y), QPointF(inner.right(), y))
            y += brush.uniform(0.8, 2.0)
        painter.setPen(QPen(QColor(255, 255, 255, colors['edges'][0]), 1))
        painter.drawLine(inner.topLeft() + QPointF(0, 0.5), inner.topRight() + QPointF(0, 0.5))
        painter.setPen(QPen(QColor(0, 0, 0, colors['edges'][1]), 1))
        painter.drawLine(inner.bottomLeft() - QPointF(0, 0.5), inner.bottomRight() - QPointF(0, 0.5))
        painter.restore()
        # lettering
        unit = inner.height()
        painter.setFont(self._font(unit * 0.05, spacing=-2))     # the maker's name: tight, as a logo
        painter.setPen(QColor(colors['print']))
        top = QRectF(inner.left() + unit * 0.08, inner.top() + unit * 0.03, inner.width() - unit * 0.16, unit * 0.09)
        painter.drawText(top, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, 'AG Projects')
        painter.setFont(self._font(unit * 0.034, spacing=14))     # the model: smaller, the maker leads
        # after it the menu button: the plates' menu (as the panel's context menu, not to be guessed)
        side = unit * 0.03
        gap = unit * 0.025
        painter.drawText(top.adjusted(0, 0, -(side + gap), 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         'STEREO CLASS-CUTE AMPLIFIER')
        middle = QPointF(top.right() - side / 2, top.center().y())
        painter.save()      # three lines, as mobile apps draw a menu button
        painter.setPen(QPen(QColor(colors['print']), max(1.5, side * 0.1), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for offset in (-side * 0.25, 0.0, side * 0.25):
            painter.drawLine(middle + QPointF(-side * 0.32, offset), middle + QPointF(side * 0.32, offset))
        painter.restore()
        self._plate_button = QRectF(middle.x() - side * 1.2, top.top(), side * 2.4, top.height())
        self._draw_meter_window(painter, window, layout['meters'])
        self._draw_lever_plate(painter, unit)
        self._draw_selector_scale(painter, unit)
        # the knobs' scales and names
        for knob in self.knobs:
            self._draw_scale(painter, knob, unit)
        painter.end()
        self._panel = pixmap

    def _draw_scale(self, painter, knob, unit):
        centre, radius = knob.centre, knob.radius
        dim = 1.0 if knob.enabled else 0.45
        colors = self._colors()
        color = QColor(colors['ink'])
        color.setAlpha(int(255 * dim))
        painter.setPen(QPen(color, 1.3))
        volume = knob is self.volume
        marks = 11 if volume else 10         # the volume: 0..10, then 11
        red = QColor(*colors['red'], int(255 * dim))

        def at(ratio, r):
            angle = math.radians(knob.angle(ratio) - 90)
            return QPointF(centre.x() + r * math.cos(angle), centre.y() + r * math.sin(angle))
        for index in range(marks + 1):        # the volume: 0..10 and the 11's mark (red, as the 11)
            ratio = index / marks
            long = index in ((0, 5, 10, 11) if volume else (0, 5, 10))
            painter.setPen(QPen(red if volume and index == 11 else color, 1.3))
            painter.drawLine(at(ratio, radius * 1.18), at(ratio, radius * (1.34 if long else 1.27)))
        painter.setFont(self._font(unit * 0.04, bold=True))
        labels = [(0.0, knob.ends[0], color), (1.0, knob.ends[1], red if volume else color)]
        if volume:
            labels.append((10 / 11, '10', color))
        for ratio, text, pen in labels:
            point = at(ratio, radius * 1.72)
            painter.setPen(pen)
            painter.drawText(QRectF(point.x() - unit * 0.05, point.y() - unit * 0.03, unit * 0.1, unit * 0.06),
                             Qt.AlignmentFlag.AlignCenter, text)
        if volume:      # the 11 a link, for the curious: why it goes to 11 (This Is Spinal Tap)
            point = at(1.0, radius * 1.72)
            self._why = QRectF(point.x() - unit * 0.03, point.y() - unit * 0.03, unit * 0.06, unit * 0.06)
        if knob.default is not None:        # the middle: a dot
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(centre.x(), centre.y() - radius * 1.45), radius * 0.05, radius * 0.05)
        if volume:      # the big knob needs no name
            return
        painter.setPen(color)
        # the knob's name: medium, not bold, a whole pixel size on a whole-pixel box (crisp, not smeared)
        font = self._font(unit * 0.032, spacing=16, weight=QFont.Weight.Medium)
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        painter.setFont(font)
        painter.drawText(QRectF(centre.x() - radius * 2.2, centre.y() + radius * 1.42, radius * 4.4, unit * 0.07).toAlignedRect(),
                         Qt.AlignmentFlag.AlignCenter, knob.label)

    PLATE = 0.8              # the lever's plate: this many steps wide
    LEVER_NAMES = 0.55       # the positions' names: this many steps left of the plate

    def _lever_font(self, unit):
        return self._font(unit * 0.027, spacing=10, weight=QFont.Weight.DemiBold)

    def _draw_lever_plate(self, painter, unit):
        if self.meter_switch == 'lever':
            self._draw_slide_plate(painter, unit)
        else:
            self._draw_style_marks(painter, unit)

    def _draw_slide_plate(self, painter, unit):
        """The lever's escutcheon (a screwed plate with its slot), its positions' names left of it."""
        pivot, points, hit = self._lever
        span = points[1].y() - points[0].y()
        plate = QRectF(pivot.x() - span * self.PLATE / 2, points[0].y() - span * 0.6, span * self.PLATE, points[-1].y() - points[0].y() + span * 1.2)
        colors = self._colors()
        gradient = QLinearGradient(plate.topLeft(), plate.bottomRight())
        gradient.setColorAt(0.0, QColor(colors['escutcheon'][0]))
        gradient.setColorAt(1.0, QColor(colors['escutcheon'][1]))
        painter.setPen(QPen(QColor(colors['rim']), 0.8))
        painter.setBrush(gradient)
        painter.drawRoundedRect(plate, span * 0.18, span * 0.18)
        for y in (plate.top() + span * 0.22, plate.bottom() - span * 0.22):       # its two screws
            screw = QPointF(pivot.x(), y)
            painter.setBrush(QColor(colors['screw'][0]))
            painter.setPen(QPen(QColor(colors['screw'][1]), 0.7))
            painter.drawEllipse(screw, span * 0.08, span * 0.08)
            painter.drawLine(screw - QPointF(span * 0.06, 0), screw + QPointF(span * 0.06, 0))
        slot = QRectF(pivot.x() - span * 0.12, points[0].y() - span * 0.12, span * 0.24, points[-1].y() - points[0].y() + span * 0.24)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#1b1d21'))
        painter.drawRoundedRect(slot, span * 0.12, span * 0.12)
        painter.setPen(QColor(colors['ink']))
        painter.setFont(self._lever_font(unit))
        for point, (key, label) in zip(points, METERS):
            painter.drawText(QRectF(plate.left() - span * self.LEVER_NAMES - unit * 0.5, point.y() - unit * 0.03, unit * 0.5, unit * 0.06),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)

    def _draw_style_marks(self, painter, unit):
        """The style knob's marks: a tick a position (the one chosen longer), its name left of it."""
        knob = self.style_knob
        centre, radius = knob.centre, knob.radius
        color = QColor(self._colors()['ink'])
        painter.setFont(self._lever_font(unit))
        metrics = painter.fontMetrics()
        pivot, points, hit = self._lever
        # the names right aligned in the box that holds them: their right edge the one nearest the knob that is clear of
        # every tick (the middle one's, its tick the furthest left)
        right = min(point.x() for point in points)
        left = right - max(metrics.horizontalAdvance(label) + 4 for key, label in METERS)
        for index, (key, label) in enumerate(METERS):
            angle = math.radians(knob.position_angle(index) - 90)
            direction = QPointF(math.cos(angle), math.sin(angle))
            current = index == int(knob.value)
            painter.setPen(QPen(color, 1.6 if current else 1.2))
            painter.drawLine(centre + direction * radius * 1.16, centre + direction * radius * (1.38 if current else 1.28))
            point = points[index]
            painter.drawText(QRectF(left, point.y() - unit * 0.03, right - left, unit * 0.06),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)

    ICON_RING = 1.86        # the output icons' centres, in radii: clear of the ticks (to 1.38) as the style knob's names
    ICON_SIZE = 0.8         # the output icons (the played on's; the others 0.88 of it), in radii (was 0.5)

    def _draw_selector_scale(self, painter, unit):
        """The output selector's marks, a tick a position (the one played on longer), OUTPUT above it, the name of
        the output played on under it."""
        selector = self.selector
        centre, radius = selector.centre, selector.radius
        colors = self._colors()
        color = QColor(colors['ink'])
        for index in range(0 if self.web else len(selector.names)):
            angle = math.radians(selector.position_angle(index) - 90)
            direction = QPointF(math.cos(angle), math.sin(angle))
            current = index == int(selector.value)
            painter.save()      # (restored after its icon)
            if selector.names[index] in self.unavailable_outputs:       # not connected: its place kept, grey
                painter.setOpacity(painter.opacity() * 0.3)
            painter.setPen(QPen(color, 1.6 if current else 1.2))
            painter.drawLine(centre + direction * radius * 1.16, centre + direction * radius * (1.38 if current else 1.28))
            # what the output is (the Mac's speakers, headphones, Bluetooth...): a small line icon past its tick
            glyph = self.output_icons.get(selector.names[index])
            if glyph and glyph.startswith('@'):     # an output not told apart: a letter of its own in a ring
                size = radius * (self.ICON_SIZE if current else self.ICON_SIZE * 0.88)
                where = centre + direction * radius * self.ICON_RING
                painter.save()
                if not current:
                    painter.setOpacity(painter.opacity() * 0.6)
                painter.setPen(QPen(color, max(1.0, size * 0.07)))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(where, size / 2, size / 2)
                painter.setFont(self._font(size * 0.58, weight=QFont.Weight.DemiBold))
                painter.drawText(QRectF(where.x() - size / 2, where.y() - size / 2, size, size), Qt.AlignmentFlag.AlignCenter, glyph[1:])
                painter.restore()
            elif glyph:
                size = radius * (self.ICON_SIZE if current else self.ICON_SIZE * 0.88)
                where = centre + direction * radius * self.ICON_RING
                painter.save()
                if not current:
                    painter.setOpacity(painter.opacity() * 0.6)
                painter.translate(where.x() - size / 2, where.y() - size / 2)
                painter.scale(size / 24, size / 24)
                painter.setPen(QPen(color, 1.2 * 24 / max(size, 1), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                    Qt.PenJoinStyle.RoundJoin))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(line_path(glyph))
                painter.restore()
            painter.restore()
        width = self._selector_width
        painter.setPen(color)
        painter.setFont(self._lever_font(unit))
        # OUTPUT above it; with icons around it, above them
        icons = any(self.output_icons.get(name) for name in selector.names)
        lift = radius * (self.ICON_RING - 1.34 + (self.ICON_SIZE - 0.5) / 2) if icons else 0.0     # (past the icons' tops)
        painter.drawText(QRectF(centre.x() - width / 2, centre.y() - radius * 1.4 - lift - unit * 0.06, width, unit * 0.05),
                         Qt.AlignmentFlag.AlignCenter, 'OUTPUT')
        if self.web:
            return
        mute = self._mute
        painter.drawText(QRectF(mute.center().x() - width / 2, centre.y() - radius * 1.4 - unit * 0.06, width, unit * 0.05),
                         Qt.AlignmentFlag.AlignCenter, 'MUTE')
        bezel = mute.adjusted(-mute.width() * 0.08, -mute.width() * 0.08, mute.width() * 0.08, mute.width() * 0.08)
        painter.setPen(QPen(QColor(colors['rim']), 0.8))      # the button's recess in the plate
        painter.setBrush(QColor('#1b1d21'))
        painter.drawEllipse(bezel)
        font = self._font(unit * 0.026, weight=QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(colors['print']))
        # the output's name centred over the volume knob under it, as wide as fits in the plate
        inner = self._layout['inner']
        middle = self.volume.centre.x()
        wide = min(unit * 0.7, 2 * (inner.right() - unit * 0.03 - middle))
        name = QFontMetricsF(font).elidedText(_Selector.title(selector.current()), Qt.TextElideMode.ElideRight, wide)
        drop = radius * (self.ICON_RING - 1.5 + (self.ICON_SIZE - 0.5) / 2) if icons else 0.0     # (clear of the icons low at each side)
        painter.drawText(QRectF(middle - wide / 2, centre.y() + radius * 1.4 + drop + unit * 0.01, wide, unit * 0.05),
                         Qt.AlignmentFlag.AlignCenter, name)

    def _draw_lever(self, painter):
        """The slide lever's handle, at the position chosen (one, nowhere else): a thin metal rod across the slot,
        like a needle, round (lit from above), no cap; or the style knob itself, pointing at the style chosen."""
        if self.meter_switch != 'lever':
            self._draw_knob(painter, self.style_knob)
            return
        pivot, points, hit = self._lever
        span = points[1].y() - points[0].y()
        keys = [key for key, label in METERS]
        tip = points[keys.index(self.meter_style)]
        length, thickness = span * 0.7, max(4.0, span * 0.2)
        rod = QRectF(tip.x() - length / 2, tip.y() - thickness / 2, length, thickness)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 90))       # its shadow on the plate, down right
        painter.drawRect(rod.translated(span * 0.04, span * 0.08))
        metal = QLinearGradient(0, rod.top(), 0, rod.bottom())
        metal.setColorAt(0.0, QColor('#7d8188'))
        metal.setColorAt(0.3, QColor('#ffffff'))
        metal.setColorAt(0.65, QColor('#b4b8be'))
        metal.setColorAt(1.0, QColor('#3f4349'))
        painter.setBrush(metal)
        painter.drawRect(rod)
        painter.restore()

    def set_meter_switch(self, switch):
        """The lever or the style knob (amp/meter_switch)."""
        if switch in ('lever', 'knob') and switch != self.meter_switch:
            self.meter_switch = switch
            util.settings().setValue('amp/meter_switch', switch)
            self._panel = None
            self._compute_layout()
            self.update()

    def _draw_mute(self, painter):
        """The mute button: a round black push button, its red lamp lit and the button in while muted."""
        rect = self._mute
        size = rect.width()
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        button = rect.adjusted(size * 0.04, size * 0.04, -size * 0.04, -size * 0.04)
        if self.muted:      # pushed in: lower, lit from above less
            button.translate(0, size * 0.03)
        face = QRadialGradient(button.center() - QPointF(0, button.height() * 0.25), button.width() * 0.75)
        face.setColorAt(0.0, QColor('#3a3d43' if self.muted else '#555961'))
        face.setColorAt(1.0, QColor('#0f1012'))
        painter.setBrush(face)
        painter.drawEllipse(button)
        lamp = QPointF(button.center().x(), button.center().y())
        radius = size * 0.17
        glow = QRadialGradient(lamp, radius * (2.6 if self.muted else 1.0))
        if self.muted:
            glow.setColorAt(0.0, QColor('#ff6b5e'))
            glow.setColorAt(0.35, QColor('#e0281b'))
            glow.setColorAt(1.0, QColor(224, 40, 27, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(lamp, radius * 2.6, radius * 2.6)
        painter.setPen(QPen(QColor('#0a0a0a'), 0.8))
        painter.setBrush(QColor('#ff4a3d') if self.muted else QColor('#4a1714'))
        painter.drawEllipse(lamp, radius, radius)
        painter.restore()

    def _draw_knob(self, painter, knob, dark=None):
        """dark: a black anodised cap, else spun aluminium (None: as the amplifier's knobs are set; the mixer's,
        drawn through draw_knob without an amplifier: aluminium)."""
        if dark is None:
            dark = self.dark_knobs() if self is not None else False
        centre, radius = knob.centre, knob.radius
        painter.save()
        if not knob.enabled:
            painter.setOpacity(0.55)
        painter.setPen(Qt.PenStyle.NoPen)
        # its shadow, then the knurled black skirt
        shadow = QRadialGradient(centre + QPointF(0, radius * 0.18), radius * 1.25)
        shadow.setColorAt(0.75, QColor(0, 0, 0, 110))
        shadow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(shadow)
        painter.drawEllipse(centre + QPointF(0, radius * 0.18), radius * 1.25, radius * 1.25)
        skirt = QLinearGradient(0, centre.y() - radius, 0, centre.y() + radius)
        skirt.setColorAt(0.0, QColor('#3c3f45'))
        skirt.setColorAt(1.0, QColor('#0d0e10'))
        painter.setBrush(skirt)
        painter.drawEllipse(centre, radius, radius)
        rotation = knob.angle()
        painter.setPen(QPen(QColor(255, 255, 255, 38), 1))
        for index in range(36):
            angle = math.radians(rotation + index * 10)
            painter.drawLine(QPointF(centre.x() + radius * 0.82 * math.cos(angle), centre.y() + radius * 0.82 * math.sin(angle)),
                             QPointF(centre.x() + radius * 0.98 * math.cos(angle), centre.y() + radius * 0.98 * math.sin(angle)))
        # the spun aluminium cap: a conical gradient (light from above-left), fixed while the knob turns
        cap = radius * 0.72
        spun = QConicalGradient(centre, 135)
        stops = (((0.0, '#5a5e65'), (0.12, '#24262a'), (0.25, '#4a4d53'), (0.38, '#1a1b1e'), (0.5, '#5a5e65'),
                  (0.62, '#24262a'), (0.75, '#4a4d53'), (0.88, '#1a1b1e'), (1.0, '#5a5e65')) if dark else
                 ((0.0, '#f4f5f7'), (0.12, '#a9adb3'), (0.25, '#e9ebee'), (0.38, '#8f939a'), (0.5, '#f4f5f7'),
                  (0.62, '#a9adb3'), (0.75, '#e9ebee'), (0.88, '#8f939a'), (1.0, '#f4f5f7')))
        for stop, color in stops:
            spun.setColorAt(stop, QColor(color))
        painter.setPen(QPen(QColor('#0b0c0e' if dark else '#5e6268'), 0.8))
        painter.setBrush(spun)
        painter.drawEllipse(centre, cap, cap)
        rim = QRadialGradient(centre, cap)
        rim.setColorAt(0.85, QColor(0, 0, 0, 0))
        rim.setColorAt(1.0, QColor(0, 0, 0, 60))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(rim)
        painter.drawEllipse(centre, cap, cap)
        # the pointer: a line on the cap, a white mark on the skirt
        angle = math.radians(rotation - 90)
        direction = QPointF(math.cos(angle), math.sin(angle))
        painter.setPen(QPen(QColor('#e6e8eb' if dark else '#2b2e33'), max(1.5, radius * 0.07), Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        painter.drawLine(centre + direction * cap * 0.3, centre + direction * cap * 0.88)
        painter.setPen(QPen(QColor('#f2f2f2'), max(1.5, radius * 0.08), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(centre + direction * radius * 0.84, centre + direction * radius * 0.97)
        painter.restore()

    def paintEvent(self, event):
        key = (self.width(), self.height(), self.devicePixelRatioF())      # drawn again on a new size or screen
        if self._panel is None or self._panel_key != key:
            self._draw_panel()
            self._panel_key = key
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = event.rect()             # a meter frame repaints the window only: the rest is not drawn again
        ratio = self._panel.devicePixelRatioF()
        painter.drawPixmap(QRectF(area), self._panel, QRectF(area.x() * ratio, area.y() * ratio,
                                                              area.width() * ratio, area.height() * ratio))
        layout = self._layout
        now = time.monotonic()
        # the needles (the analog style; the others are the meter widget, over the faces)
        for channel, face in enumerate(layout['meters'] if self.meter_style == 'analog' else ()):
            self._draw_needle(painter, face, self.needles[channel], now - self.peak_lit[channel] < 0.35)
        if area.intersects(self._layout['inner'].toAlignedRect()) and not layout['window'].toAlignedRect().contains(area):
            self._draw_lever(painter)
            self._draw_mute(painter)
            for knob in self.knobs + [self.selector]:
                if area.intersects(QRectF(knob.centre.x() - knob.radius * 1.4, knob.centre.y() - knob.radius * 1.4,
                                          knob.radius * 2.8, knob.radius * 2.8).toAlignedRect()):
                    self._draw_knob(painter, knob)
