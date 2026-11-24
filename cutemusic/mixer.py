"""The Mixer view: a small mixing console for singing along with the music:

Inputs: as many MIC/LINE strips as wanted, up to one per input device, all alike (any device on any of them): + on
the last one adds one, - on any (while there is more than one) removes it. An INPUT lists only the devices no other
strip has chosen. The echo (KARAOKE) and TO AMPLIFIER, on the first strip, are for all of them.

MIC: the input selector (Off, then the input devices: a rotary switch, as the amplifier's output selector), the
echo, the mic's fader and its meter. The mic plays on the music's output from a pipeline of its own (mic.py).

BLINK, only while Blink runs, apart from the others: who is in the call, what Blink plays on the "AG
Podcast Mixer" device (the friends in the call) heard on the music's output with its fader and meter; never in what
Blink sends (they would hear themselves). Between it and APPS two arrows: orange to the call (what MIC/LINE and APPS
send), black from it. What Blink plays on the "AG
Podcast Mixer" device, heard on the music's output; never in what Blink sends (they would hear themselves).
Without a call neither is shown.

LIBRARY (Local Library): AG CuteMusic's player, always there: what it plays at its top; at its bottom, left of its
MUTE, VOCAL CUT (the singer taken out, audiokaraoke, player.py; KARAOKE), right of it TO CALLS (sent in Blink's call
too: Blink's device "AG Podcast Mixer", share.py, link.py; with a call); its fader the player's volume, its meter.

RECORDER, a bar across the console under the strips (it is no input: it takes everything they play): the DAT tape
(turning while it records, its label the recording's title), REC (everything the strips play into one file,
recorder.py), MARK (a marker at that moment: chapters in the file), TAPES (the recordings in the library), how long
and what it records (a Blink call, or a memo), and STREAMING on the right (On: "AG Podcast Streaming", an input for a
streaming app with what the recorder hears; the AG Podcast Mixer driver's). While someone hears the console (a Blink
call connected, on hold too; another app reading "AG Podcast Mixer"; STREAMING on with an app reading "AG Podcast
Streaming": recording alone is not on air), ON AIR is lit at its top left and a red line runs along
its top.

MUSIC (Audio Apps): other apps only, besides the Local Library (macOS 14.2+, taps.py): the SOURCE selector at its top
(Off, or an app that plays: Spotify, a browser), what it plays under it; at its bottom THRU (left as it plays, on its
own output, else only through the mixer) and TO CALLS, both with a call; its fader the app's level (a player app's
own volume), its meter. With THRU the app is heard as it plays, untouched: the fader is then SEND (how loud it goes
into a call), dimmed with the meter while nothing is sent.

A fader: dragged by its cap, a click on its slot puts it there, the mouse wheel over its strip moves it; a knob and
the selector as the amplifier's.
"""

import json
import sys
import math
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QColor, QConicalGradient, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen,
                         QRadialGradient)
from PyQt6.QtWidgets import QLineEdit, QMenu, QToolTip, QWidget

from cutemusic import util
from cutemusic.util import log
from cutemusic.amp import BACKGROUND, AmpView, _Knob, _Selector, draw_knob
from cutemusic.icons import line_path
from cutemusic.vufaces import AnalogFaces


__all__ = ['MixerView']


DB_LOW, DB_HIGH = -60.0, 0.0
REDIAL_DELAY = 5.0      # s: a call dropped by the other side (auto-redial), called again after
DIAL_WAIT = 8.0         # s: REDIAL turned, the call awaited from Blink before the switch goes back (it failed)
TITLE_HINT = 'Enter title...'      # the tape's label without a title
TITLE_SIZE = 0.026      # a strip's name under it (of the console's height)
REEL_SPEED = 72.0       # degrees a second: the tape's reels, both together, while it records
SEND_COLOR, RECEIVE_COLOR = '#ffb347', '#000000'      # what goes out to the call, what comes in from it
LOSS_SHOWN_FROM = 2.0       # packet loss (%) under this is not shown under a call
STRIP_TITLES = {'MIC': 'MIC/LINE', 'LIBRARY': 'Local Library', 'MUSIC': 'Audio Apps', 'BLINK': 'Blink client',
                'RECORDER': 'Audio Recorder'}       # a strip's name as written under it (its key stays: 'MIC', 'MUSIC')
SEGMENTS = 24


class _InputSelector(_Selector):
    """MICROPHONE's input: None Off, else a device's name (its kind in icons: a line icon's name, drawn around the
    knob at its position)."""

    REACH = 2.3         # a click this far (radii) from the centre still turns it: on the glyphs

    def __init__(self):
        self.icons = {}
        super().__init__()

    @staticmethod
    def title(name):
        return name or 'Off'


class _Slot:
    """One more input strip (MIC/LINE 2, 3, ...): its INPUT, fader, meter and MUTE; key: stable while it exists
    ('MIC#1', ...: its MUTE's name, the window's handle on its Mic), its number the strip's place."""

    def __init__(self, key, parent, chosen, input_name=None, level=0.8):
        self.key = key
        self.input = input_name or None     # the device chosen (None: Off), as the window plays it
        self.selector = _InputSelector()
        self.selector.set_names([None] + ([self.input] if self.input else []), self.input)
        self.fader = _Fader(level)
        self.meter = _Meter(2)        # left and right, as the first strip's (a mono device: the same on both)
        self.timer = QTimer(parent, singleShot=True, interval=400)
        self.timer.timeout.connect(lambda: chosen(self))





class _OnOffSelector(_Selector):
    """A two-way switch as a rotary selector: Off (None), On ('on'); on_title: what On is called under it."""

    REACH = 1.8
    STEP = 70.0         # its two positions well apart, OFF and ON beside their ticks

    def __init__(self, on=False, on_title='On'):
        super().__init__()
        self.on_title = on_title
        self.set_names([None, 'on'], 'on' if on else None)

    def title(self, name):
        return self.on_title if name else 'Off'


class _CallSelector(_Selector):
    """BLINK's call switch, a rotary switch as the others: left HANG UP (None), right REDIAL ('call'); it points
    right while a call is up."""

    STEP = 70.0         # its two positions well apart: 35 degrees each side of the top
    REACH = 2.0

    def __init__(self):
        super().__init__()
        self.set_names([None, 'call'], None)

    @staticmethod
    def title(name):
        return 'Call' if name else 'Off'


class _SourceSelector(_Selector):
    """MUSIC's source: None the player, else an app's bundle id (its name in titles; in icons, drawn around the knob
    at its position: the Local Library's a line icon, an app's an avatar of its first letter)."""

    REACH = 2.3         # a click this far (radii) from the centre still turns it: on the icons

    def __init__(self):
        self.titles = {}
        self.icons = {}
        super().__init__()

    def title(self, name):
        return self.titles.get(name, name) if name else 'Off'


class _Fader:
    """A slide pot: value 0..1 (its ratio of the travel), its slot set by the layout."""

    def __init__(self, value):
        self.value = value
        self.slot = QRectF()
        self.cap = QRectF()
        self.cap_height = 0.0       # set by the layout, with the slot

    def place(self, slot, cap_height):
        self.slot = slot
        self.cap_height = cap_height
        self._place_cap()

    def _place_cap(self):
        travel = self.slot.height() - self.cap_height
        y = self.slot.top() + (1 - self.value) * travel
        width = self.slot.width() * 4.2
        self.cap = QRectF(self.slot.center().x() - width / 2, y, width, self.cap_height)

    def value_at(self, y, offset=0.0):
        travel = self.slot.height() - self.cap_height
        return max(0.0, min(1.0, 1 - (y - offset - self.slot.top()) / travel)) if travel > 0 else self.value

    def set(self, value):
        value = max(0.0, min(1.0, value))
        if abs(value - self.value) < 1e-6:
            return False
        self.value = value
        self._place_cap()
        return True

    def hit(self, point):
        return self.cap.adjusted(-4, -4, 4, 4).contains(point) or \
            self.slot.adjusted(-self.cap.width() / 2, 0, self.cap.width() / 2, 0).contains(point)


class _Meter:
    """An LED column per channel: the level as it comes, falling slowly; the peak held a moment."""

    STALE = 0.3     # s without levels: silence

    def __init__(self, channels):
        self.levels = [DB_LOW] * channels
        self.peaks = [(DB_LOW, 0.0)] * channels
        self.target = [DB_LOW] * channels
        self.received = 0.0
        self.rect = QRectF()

    def feed(self, rms):
        self.received = time.monotonic()
        if not rms:
            self.target = [DB_LOW] * len(self.levels)
            return
        if len(rms) < len(self.levels):
            rms = list(rms) * len(self.levels)
        self.target = [DB_LOW if math.isnan(v) else max(DB_LOW, v) for v in rms[:len(self.levels)]]

    def tick(self, elapsed, now):
        if now - self.received > self.STALE:
            self.target = [DB_LOW] * len(self.levels)
        moved = False
        for index, (level, target) in enumerate(zip(self.levels, self.target)):
            new = target if target > level else max(target, level - 40 * elapsed)
            if abs(new - level) > 0.05:
                moved = True
            self.levels[index] = new
            peak, since = self.peaks[index]
            if new >= peak:
                self.peaks[index] = (new, now)
            elif now - since > 1.0:
                self.peaks[index] = (max(DB_LOW, peak - 20 * elapsed), since)
                moved = moved or peak > DB_LOW
        return moved


class _ParticipantMeter(_Meter):
    """A room participant's meter in the BLINK strip: levels every 0.3 s from Blink (its statistics), so held longer."""

    STALE = 1.0


class MixerView(AnalogFaces, QWidget):
    """input_chosen(name, '' off), mic_level_changed(0..1), echo_changed(0..1), music_changed(0..100),
    vocal_cut_changed(bool): turned by hand; inputs_wanted: shown, its selector to be brought up to date."""

    input_chosen = pyqtSignal(str)
    mic_level_changed = pyqtSignal(float)
    echo_changed = pyqtSignal(float)
    music_changed = pyqtSignal(int)             # LOCAL LIBRARY's fader: the player's volume (0..100)
    apps_level_changed = pyqtSignal(int)        # AUDIO APPS' fader (0..100): the app's level, a player app's own volume
    library_share_changed = pyqtSignal(bool)    # LOCAL LIBRARY's TO CALLS (share_changed: AUDIO APPS')
    record_toggled = pyqtSignal(bool)           # RECORDER's REC: start (True) or stop
    marker_requested = pyqtSignal()             # RECORDER's MARK: a marker in the recording, now
    tape_wanted = pyqtSignal()                  # RECORDER's TAPE: its recordings shown in the library
    strip_clicked = pyqtSignal(str)             # a strip clicked off its controls: its key ('LIBRARY', 'MUSIC', ...)
    record_renamed = pyqtSignal(str)            # the tape's label edited: the recording's new title (and file name)
    streaming_changed = pyqtSignal(bool)        # RECORDER's STREAMING turned (at rest)
    vocal_cut_changed = pyqtSignal(bool)
    share_changed = pyqtSignal(bool)
    mute_changed = pyqtSignal(str, bool)        # a strip's MUTE: 'MIC', 'BLINK' or 'MUSIC'
    channel_mutes_changed = pyqtSignal(bool, bool)  # LOCAL LIBRARY's L and R: left muted, right muted
    monitor_changed = pyqtSignal(bool)
    karaoke_changed = pyqtSignal(bool)          # KARAOKE: the echo and VOCAL CUT shown and heard
    blink_changed = pyqtSignal(bool)            # BLINK's strip shown or hidden: KARAOKE hidden and off while shown
    slot_added = pyqtSignal(str)                # + : one more input strip (its key)
    slot_removed = pyqtSignal(str)              # - : that strip gone (its key)
    slot_input_chosen = pyqtSignal(str, str)    # a strip's INPUT (its key, the device; '' Off)
    slot_level_changed = pyqtSignal(str, float) # a strip's fader (its key, 0..1)
    participant_mute_requested = pyqtSignal(object, bool)   # a room participant's MUTE: its target, muted or not
    hangup_requested = pyqtSignal(object)       # a call's HANG UP in the BLINK strip: its target ({"pid", "id", "channel"})
    auto_record_changed = pyqtSignal(bool)      # Auto-record when on air turned on or off (the menu button's menu)
    auto_redial_changed = pyqtSignal(bool)      # Auto-redial turned on or off (the menu button's menu)
    redial_requested = pyqtSignal(object)       # REDIAL under No call: the Blink's target ({"pid", "channel", "label"})
    power_changed = pyqtSignal(bool)            # POWER: off, the mixer stands down (no device for Blink, no mic)          # the MIC's TO AMPLIFIER: heard in the headset, or only sent to Blink
    call_level_changed = pyqtSignal(float)      # CALL: how loud the call is heard
    inputs_wanted = pyqtSignal()
    source_chosen = pyqtSignal(str)             # MUSIC's SOURCE: an app's bundle id, '' the player
    app_output_changed = pyqtSignal(bool)       # THRU: the app heard on its own output too

    ASPECT = 1.7            # the strips' part: width / unit
    BAR = 0.24              # RECORDER's bar under the strips, in units: the console is unit * (1 + BAR) tall

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)
        settings = util.settings()
        self._slot_muted = {}       # (the kept strips' MUTE, till self.muted is made)
        self.selector = _InputSelector()
        self.echo = _Knob('echo', 'ECHO', 0.0, 1.0, settings.value('mixer/echo', 0.0, type=float), 0.05, None, ('0', 'MAX'))
        self.mic_fader = _Fader(settings.value('mixer/mic', 0.8, type=float))
        self.call_fader = _Fader(settings.value('mixer/call', 0.8, type=float))
        self.music_fader = _Fader(1.0)          # AUDIO APPS'
        self.library_fader = _Fader(0.8)        # LOCAL LIBRARY's: the player's volume
        # every strip's meter a column per channel: an input's too (a two-channel interface: each input; a mono
        # microphone: the same level on both)
        self.mic_meter, self.call_meter, self.music_meter, self.library_meter = _Meter(2), _Meter(2), _Meter(2), _Meter(2)
        self.faders = [self.mic_fader, self.call_fader, self.music_fader, self.library_fader]
        self.meters = [self.mic_meter, self.call_meter, self.music_meter, self.library_meter]
        self.vocal_cut = False
        self.vocal_cut_available = True
        self._cut = QRectF()            # AUDIO APPS' THRU
        self._share = QRectF()          # AUDIO APPS' TO CALLS
        self._vocal = QRectF()          # LOCAL LIBRARY's VOCAL CUT
        self._library_share = QRectF()  # LOCAL LIBRARY's TO CALLS
        self.sharing = settings.value('mixer/to_calls', True, type=bool)      # AUDIO APPS' TO CALLS, kept (on the first time)
        self.library_sharing = settings.value('mixer/library_to_calls', True, type=bool)     # LOCAL LIBRARY's
        self.share_available = True
        self._strips = []
        self._drag = None
        self._wheel_rest = 0.0
        self._input_timer = QTimer(self, singleShot=True, interval=400)
        self._input_timer.timeout.connect(lambda: self.input_chosen.emit(self.selector.current() or ''))
        self._input_timer.timeout.connect(self._refresh_inputs)        # the others' lists, without it (or with it again)
        self._inputs = []           # the input devices, as the window lists them: each INPUT offers those no other has
        self.source_selector = _SourceSelector()
        self._source_timer = QTimer(self, singleShot=True, interval=400)
        self._source_timer.timeout.connect(lambda: self.source_chosen.emit(self.source_selector.current() or ''))
        # the other input strips, kept (mixer/inputs): [{"input", "level", "muted"}]
        self.slots = []
        self._slot_serial = 0
        self._add_rect = QRectF(-1e6, -1e6, 1, 1)       # + on the last MIC/LINE strip
        self._remove_rects = {}                         # - on each other one: its key: rect
        try:
            kept = json.loads(settings.value('mixer/inputs', '[]', type=str) or '[]')
        except ValueError:
            kept = []
        for entry in kept if isinstance(kept, list) else []:
            if isinstance(entry, dict):
                slot = self._new_slot(entry.get('input'), float(entry.get('level', 0.8)))
                self.slots.append(slot)
                if entry.get('muted'):
                    self._slot_muted[slot.key] = True
        self._selectors = []
        self._rebuild_selectors()
        self.sources_available = False          # other apps can be captured (taps.available())
        self.source_track = None                # (title, artist) the app plays, under SOURCE, as BLINK's party
        self.source_playing = False             # MUSIC's app plays now (a player that says so; another app: captured)
        self.library_track = None               # (title, artist) the Local Library plays (or has paused)
        self.library_playing = False            # playing it now (else paused: PAUSED, not NOW PLAYING)
        self.recording = False                  # RECORDER: recording now
        self.record_started = 0.0               # (monotonic) since when
        self.record_what = ''                   # what it records ("Blink Calls · sip:alice@example.com"), or last saved
        self.record_available = True            # an encoder there (recorder.extension())
        self._record_rect = QRectF(-1e6, -1e6, 1, 1)       # REC
        self._tape_button = QRectF(-1e6, -1e6, 1, 1)       # TAPES: right of MARK
        self._tape_rect = QRectF(-1e6, -1e6, 1, 1)
        self.record_title = ''                  # the recording's title, written on the tape's label (editable)
        self.markers = 0                        # MARK pressed this many times in this recording
        # RECORDER's bar shown or not (the menu button after PODCAST MIXER)
        self.recorder_shown = settings.value('mixer/recorder_shown', True, type=bool)
        self._view_button = QRectF(-1e6, -1e6, 1, 1)
        self._reel_angle = 0.0                  # the tape's reels' angle (both together) and speed (degrees a second)
        self._reel_speed = 0.0
        self._bar_second = -1                   # the recording's second the bar last showed
        self._bar = QRectF(-1e6, -1e6, 1, 1)    # RECORDER's bar
        self._mark_rect = QRectF(-1e6, -1e6, 1, 1)         # MARK: right of REC
        self._tape_view = QRectF(-1e6, -1e6, 1, 1)         # the part of the tape shown (cropped)
        self._on_air_rect = QRectF(-1e6, -1e6, 1, 1)       # ON AIR, top left
        # STREAMING: On called by the device it makes, under the knob (what to pick in the streaming app)
        self.streaming_selector = _OnOffSelector(settings.value('mixer/streaming', False, type=bool), 'AG Podcast Streaming')
        self._streaming_timer = QTimer(self, singleShot=True, interval=300)
        self._streaming_timer.timeout.connect(self._streaming_turned)
        self.streaming_available = False        # the driver there (with its streaming devices)
        self._rebuild_selectors()
        self._label_rect = QRectF(-1e6, -1e6, 1, 1)        # the label's writing, as last drawn: a click edits it
        self._title_editor = None               # the QLineEdit over it while it is edited
        self.app_output = settings.value('mixer/app_output', False, type=bool)
        self.app_volume_control = False         # the fader is the app's own volume (a player app: taps.set_volume)
        self.blink = False          # Blink runs
        self.bar_strip = 'LIBRARY'  # the strip whose songs the play bar under the view drives (set_bar_strip)
        self._connected_since = {}  # a call's id: when it was first seen connected (a Blink giving no start)
        self.muted = {'MIC': False, 'BLINK': False, 'MUSIC': False, 'LIBRARY': False}
        try:        # each MUTE as it was left (mixer/muted; the input strips' with the strips, _save_slots)
            kept = json.loads(settings.value('mixer/muted', '{}', type=str) or '{}')
            self.muted.update({name: bool(kept[name]) for name in self.muted if name in kept})
        except (ValueError, TypeError, AttributeError):
            pass
        self.muted.update(self._slot_muted)
        for slot in self.slots:
            self.muted.setdefault(slot.key, False)
        self._mutes = {}            # strip name: its MUTE button's rectangle
        self._channel_mute_rects = {}   # LOCAL LIBRARY's L and R: 'L', 'R': their rectangles
        try:        # as they were left (mixer/library_channel_mutes)
            kept = json.loads(settings.value('mixer/library_channel_mutes', '[false, false]', type=str) or '[false, false]')
            self.channel_mutes = [bool(kept[0]), bool(kept[1])]
        except (ValueError, TypeError, IndexError):
            self.channel_mutes = [False, False]
        self.karaoke = settings.value('mixer/karaoke', False, type=bool)
        self.powered = settings.value('mixer/power', True, type=bool)
        self._karaoke_rect = self._power_rect = QRectF()
        self.monitor = True         # TO AMPLIFIER (MIC): on, the mic heard here too (karaoke); off, only sent (a call)
        self._monitor = QRectF()
        self.blink_calls = []       # [(title, detail, held, quality, target)]: who is in Blink's calls, at the top of its strip
        self.listeners = {}         # {'mixer': [app], 'streaming': [app]}: the other apps reading our devices (control.listeners)
        self.send_cut = False       # HANG UP pressed: nothing sent to Blink till that call is gone (the orange arrow dim)
        self.blink_redial = None    # what REDIAL calls, under No call ({"pid", "channel", "label"}), None: no button
        self.blink_devices = None   # Blink's chosen sound devices ({"input", "output"}), None: not told (an older Blink)
        self.hide_blink_astray = settings.value('mixer/hide_blink_astray', False, type=bool)  # BLINK only with the mixer as its mic
        self.auto_record = settings.value('mixer/auto_record', False, type=bool)    # REC by itself while ON AIR
        self.auto_redial = settings.value('mixer/auto_redial', True, type=bool)   # a call dropped by the other side: called again
        self.needle_meters = settings.value('mixer/needle_meters', False, type=bool)  # the strips' meters as analog VU needles
        self.auto_redial_at = None  # when it is called again (monotonic), None: not waiting to
        self._hangup_buttons = []   # [(rect, target)]: each call's HANG UP, as last drawn
        self.blink_participants = []    # the rooms' participants (window._participants_of): a meter and a MUTE each
        self._participant_meters = {}   # participant key: its _ParticipantMeter (one or two columns)
        self._participant_mutes = []    # [(rect, participant)]: their MUTE buttons, as last drawn
        self._participant_columns = []  # [(rect, participant)]: their columns (meter, initial, MUTE), for the tooltips
        self._redial_rect = QRectF(-1e6, -1e6, 1, 1)
        self._redial_down = False   # (unused: the call switch turns)
        self.call_selector = _CallSelector()      # BLINK's call switch: left HANG UP, right REDIAL
        self._dialing_since = None  # turned to REDIAL (monotonic): kept there till the call shows (or does not)
        self._visible = []          # (strip, fader, meter, name) as laid out
        self.timer = QTimer(self, interval=16)      # 60 a second: the meters, and the reels turning fluidly
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)        # (even steps)
        self.timer.timeout.connect(self._tick)
        self.last = time.monotonic()

    # From the window

    def set_inputs(self, names, current):
        """The input devices, and MIC/LINE's (None: Off)."""
        relayout = self.can_add() != (1 + len(self.slots) < len(names))     # + shown or not
        self._inputs = list(names)
        if not self._input_timer.isActive():
            self._refresh_inputs(current)
        if relayout:
            self._layout()
            self.update()

    _KEEP = object()

    def _refresh_inputs(self, main=_KEEP):
        """Each INPUT: Off, then the devices no other strip has chosen; its own choice kept (Off when it is gone).
        One being turned is left as it is till it rests."""
        entries = [(self.selector, self._input_timer, self.selector.current() if main is self._KEEP else main)]
        entries += [(slot.selector, slot.timer, slot.selector.current() if slot.timer.isActive() else slot.input)
                    for slot in self.slots]
        currents = [current for selector, timer, current in entries]
        changed = False
        for index, (selector, timer, current) in enumerate(entries):
            if timer.isActive():
                continue
            taken = {other for position, other in enumerate(currents) if position != index and other}
            names = [None] + [name for name in self._inputs if name not in taken]
            current = current if current in names else None
            if names != selector.names or current != selector.current():
                selector.set_names(names, current)
                changed = True
        if changed:
            self.update()

    def set_slot_input(self, key, name):
        """A strip's device as the window plays it (None: Off)."""
        slot = self._slot(key)
        if slot is not None and slot.input != (name or None):
            slot.input = name or None
            self._save_slots()
            self._refresh_inputs()

    def set_slot_levels(self, key, rms):
        slot = self._slot(key)
        if slot is not None:
            slot.meter.feed(rms)

    # The other input strips

    def _slot(self, key):
        return next((slot for slot in self.slots if slot.key == key), None)

    def _new_slot(self, input_name=None, level=0.8):
        self._slot_serial += 1
        return _Slot(f'MIC#{self._slot_serial}', self, self._slot_chosen, input_name, level)

    def _slot_chosen(self, slot):
        """A strip's INPUT at rest: played (the window), kept, and the others' lists without it."""
        slot.input = slot.selector.current()
        self._save_slots()
        self.slot_input_chosen.emit(slot.key, slot.input or '')
        self._refresh_inputs()

    def _save_slots(self):
        util.settings().setValue('mixer/inputs', json.dumps(
            [{'input': slot.input or '', 'level': round(slot.fader.value, 3), 'muted': bool(self.muted.get(slot.key))}
             for slot in self.slots]))

    def _rebuild_selectors(self):
        """(selector, its timer, what a position is called in its tooltip) for each, the input strips' in order."""
        self._selectors = [(self.selector, self._input_timer, 'Input', '')]
        self._selectors += [(slot.selector, slot.timer, 'Input', '') for slot in self.slots]
        self._selectors.append((self.source_selector, self._source_timer, 'Music from', ''))
        if hasattr(self, 'streaming_selector'):       # (made after the strips kept are restored)
            self._selectors.append((self.streaming_selector, self._streaming_timer, 'Streaming', ''))

    def can_add(self):
        """+ shown: fewer strips than input devices."""
        return 1 + len(self.slots) < len(self._inputs)

    def add_slot(self):
        """+ : one more input strip, Off, at the level MIC/LINE starts at."""
        if not self.can_add():
            return None
        slot = self._new_slot()
        self.slots.append(slot)
        self.muted[slot.key] = False
        self._rebuild_selectors()
        self._save_slots()
        self._refresh_inputs()
        self._layout()
        self.update()
        self.slot_added.emit(slot.key)
        return slot

    def remove_slot(self, key):
        """- : the strip gone (its device back in the others' lists). The first ('MIC') is no different: the next
        one moves into its place (its device, fader and MUTE), and that one goes."""
        if key == 'MIC':
            if not self.slots:
                return
            moved = self.slots[0]
            self.remove_slot(moved.key)
            self.selector.set_names([None] + ([moved.input] if moved.input else []), moved.input)
            self.input_chosen.emit(moved.input or '')
            self._refresh_inputs(moved.input)
            if self.mic_fader.set(moved.fader.value):
                util.settings().setValue('mixer/mic', self.mic_fader.value)
                self.mic_level_changed.emit(self.mic_fader.value)
            muted = bool(self.muted.get(moved.key))
            if muted != self.muted.get('MIC'):
                self.muted['MIC'] = muted
                self.mute_changed.emit('MIC', muted)
            self.update()
            return
        slot = self._slot(key)
        if slot is None:
            return
        slot.timer.stop()
        self.slots.remove(slot)
        self.muted.pop(key, None)
        self._rebuild_selectors()
        self._save_slots()
        self._refresh_inputs()
        self._layout()
        self.update()
        self.slot_removed.emit(key)

    def strip_title(self, name):
        """A strip's name as written under it: MIC/LINE 2, 3, ... for the other input strips."""
        if name.startswith('MIC#'):
            index = next((position for position, slot in enumerate(self.slots) if slot.key == name), 0)
            return f'MIC/LINE {index + 2}'
        if name == 'MIC' and self.slots:
            return 'MIC/LINE 1'
        return STRIP_TITLES.get(name, name)

    def set_sources(self, apps, current):
        """MUSIC's SOURCE positions: the player, then [(bundle id, name)] (the app chosen kept, even gone)."""
        names = [None] + [bundle_id for bundle_id, name in apps]
        if current and current not in names:
            names.append(current)
        if self._source_timer.isActive() or (names == self.source_selector.names and current == self.source_selector.current()):
            return
        self.source_selector.titles.update(dict(apps))
        self.source_selector.set_names(names, current)
        self._layout()
        self.update()

    def set_sources_available(self, available):
        if available != self.sources_available:
            self.sources_available = available
            self._layout()
            self.update()

    def reset_source(self):
        """Back on the player (an app that could not be captured)."""
        if self.source_selector.current() is not None:
            self.source_selector.value = 0
            self._layout()
            self.update()

    def source(self):
        """The app MUSIC plays (its bundle id), None: the player."""
        return self.source_selector.current()

    def thru(self):
        """APPS plays an app left as it plays (THRU: chosen in a call; always without a call) whose volume the fader
        cannot drive (not a player app): its fader and meter are then only what is sent to a call."""
        return bool(self.source()) and (self.app_output or not self.blink_calls) and not self.app_volume_control

    def set_app_volume_control(self, on):
        """The fader drives the app's own volume (Spotify, Music): heard and sent alike, THRU or not."""
        if on != self.app_volume_control:
            self.app_volume_control = on
            self.update()

    def sending(self):
        """AUDIO APPS' app goes into a call: one chosen, TO CALLS on (and able), a call in Blink."""
        return bool(self.source()) and self.sharing and self.share_available and bool(self.blink_calls)

    def library_sending(self):
        """The Local Library goes into a call: its TO CALLS on (and able), a call in Blink."""
        return self.library_sharing and self.share_available and bool(self.blink_calls)

    def set_library_share(self, on):
        if on != self.library_sharing:
            self.library_sharing = on
            self.update()

    def set_recording(self, on, what='', title=None):
        """RECORDER: recording (what: what it records) or not (what: the last saved, shown till the next); title:
        the recording's, on the tape's label (None: kept)."""
        if on and not self.recording:
            self.record_started = time.monotonic()
            self.markers = 0
            self._bar_second = -1
        self.recording, self.record_what = on, what
        if title is not None:
            self.record_title = title
        self.update()

    def _record_time(self):
        """How long it has recorded, as the bar shows it (m:ss, h:mm:ss)."""
        seconds = int(time.monotonic() - self.record_started) if self.recording else 0
        return f'{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}' if seconds >= 3600 else f'{seconds // 60}:{seconds % 60:02d}'

    def set_markers(self, count):
        """MARK pressed count times in this recording (the bar says so)."""
        if count != self.markers:
            self.markers = count
            self.update(self._bar.adjusted(-2, -2, 2, 2).toAlignedRect())

    def edit_record_title(self):
        """The tape's label edited in place: Enter (or leaving it) renames, Escape leaves it as it was."""
        if self._label_rect.left() < -1e5:
            return
        if self._title_editor is None:
            editor = QLineEdit(self)
            editor.setFrame(False)
            editor.setStyleSheet('QLineEdit { background: #f4f1e8; color: #22252a; border: 1px solid #2f5ea8; '
                                 'border-radius: 2px; padding: 0 2px; selection-background-color: #2f5ea8; }')
            editor.editingFinished.connect(self._record_title_edited)
            editor.installEventFilter(self)
            self._title_editor = editor
        editor = self._title_editor
        rect = self._label_rect
        editor.setFont(self._font(max(10, rect.height() * 0.5), weight=QFont.Weight.DemiBold))
        editor.setGeometry(rect.adjusted(-2, -3, 2, 3).toAlignedRect())
        editor.setText(self.record_title)
        editor.setPlaceholderText(TITLE_HINT)
        editor.show()
        editor.setFocus()
        editor.selectAll()

    def _record_title_edited(self):
        editor = self._title_editor
        if editor is None or not editor.isVisible():
            return
        editor.hide()
        title = ' '.join(editor.text().split())
        if title and title != self.record_title:
            self.record_title = title
            self.record_renamed.emit(title)
        self.update()

    def eventFilter(self, watched, event):
        from PyQt6.QtCore import QEvent
        if watched is self._title_editor and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            watched.hide()          # (as it was: not renamed)
            self.update()
            return True
        return super().eventFilter(watched, event)

    def streaming(self):
        return bool(self.streaming_selector.current())

    def _streaming_turned(self):
        on = self.streaming()
        util.settings().setValue('mixer/streaming', on)
        self.streaming_changed.emit(on)

    def set_streaming(self, on):
        """STREAMING as it is (Off when it could not start)."""
        if on != self.streaming():
            self.streaming_selector.set_names([None, 'on'], 'on' if on else None)
            self.update()

    def set_streaming_available(self, available):
        if available != self.streaming_available:
            self.streaming_available = available
            self.update()

    def set_record_available(self, available):
        self.record_available = available
        self.update()

    def set_library_track(self, track, playing=False):
        """What the Local Library plays (title, artist) or has paused (playing False), None: nothing (stopped, the
        tune ended)."""
        if track != self.library_track or playing != self.library_playing:
            self.library_track, self.library_playing = track, playing
            self.update()

    def set_apps_level(self, value):
        """AUDIO APPS' fader (0..100): the app's level, or a player app's own volume as read."""
        if self.music_fader.set(value / 100):
            self.update()

    def set_app_output(self, on):
        """THRU as it is (the capture's): its lamp never other than what is heard."""
        if on != self.app_output:
            self.app_output = on
            util.settings().setValue('mixer/app_output', on)
            self.update()

    def set_source_icons(self, icons):
        """{bundle id (None: the Local Library): a line icon's name, or '@' and a letter (an avatar: an app without an
        icon)} for the positions around SOURCE."""
        self.source_selector.icons.update(icons)
        self.update()

    def set_input_icons(self, icons):
        """{input name (None: Off): a line icon's name, or '@' and a letter (an avatar; Off: '@Off')} for the
        positions around MICROPHONE's INPUT."""
        self.selector.icons.update(icons)
        self.update()

    def set_source_track(self, track, playing=False):
        """What MUSIC plays (title, artist), None: unknown (the source's name only); playing: the app plays now."""
        if track != self.source_track or playing != self.source_playing:
            self.source_track, self.source_playing = track, playing
            self.update()

    def set_source_levels(self, rms):
        """An app's levels (after its level), as sent."""
        if self.source():
            self.music_meter.feed([] if self.muted.get('MUSIC') else rms)

    def karaoke_on(self):
        """KARAOKE as it acts: chosen, and BLINK's strip not shown (while it is, KARAOKE is hidden, the echo and VOCAL
        CUT off; the choice is kept for when Blink quits, or is hidden: Hide Blink if not using AG Podcast Mixer,
        its mic elsewhere), and no Blink call."""
        return self.karaoke and not self.blink_shown() and not self.blink_calls

    def set_karaoke(self, on):
        if on != self.karaoke:
            self.karaoke = on
            util.settings().setValue('mixer/karaoke', on)
            self._layout()
            self.update()

    def set_power(self, on):
        if on != self.powered:
            self.powered = on
            util.settings().setValue('mixer/power', on)
            self.update()

    def set_monitor(self, on):
        if on != self.monitor:
            self.monitor = on
            self.update()

    def set_muted(self, name, muted):
        if self.muted.get(name) != muted:
            self.muted[name] = muted
            self._save_muted()
            self.update()

    def _save_muted(self):
        """MIC's, BLINK's, AUDIO APPS' and LOCAL LIBRARY's MUTE kept for the next start (the input strips' with them)."""
        util.settings().setValue('mixer/muted', json.dumps({name: bool(self.muted.get(name))
                                                            for name in ('MIC', 'BLINK', 'MUSIC', 'LIBRARY')}))

    def set_send_cut(self, cut):
        if cut != self.send_cut:
            self.send_cut = cut
            self.update()

    def set_blink_devices(self, devices):
        if devices != self.blink_devices:
            shown = self.blink_shown()
            self.blink_devices = devices
            if self.blink_shown() != shown:
                self._blink_strip_changed()
            self.update()

    def blink_shown(self):
        """BLINK's strip shown: Blink runs, and (Hide Blink if not using AG Podcast Mixer) has the mixer as its microphone."""
        return self.blink and not (self.hide_blink_astray and self.blink_mic_elsewhere() is not None)

    def set_hide_blink_astray(self, on):
        """Hide Blink if not using AG Podcast Mixer (kept: mixer/hide_blink_astray)."""
        if on != self.hide_blink_astray:
            shown = self.blink_shown()
            self.hide_blink_astray = on
            util.settings().setValue('mixer/hide_blink_astray', on)
            log.info(f'[mixer] hide Blink if not using the mixer: {"on" if on else "off"}')
            if self.blink_shown() != shown:
                self._blink_strip_changed()
            self.update()

    def _blink_strip_changed(self):
        if not self.blink_shown():     # its strip not drawn: no HANG UP or REDIAL to click
            self._hangup_buttons = []
            self._participant_mutes = self._participant_columns = []
            self._redial_rect = QRectF(-1e6, -1e6, 1, 1)
        self._layout()
        self.blink_changed.emit(self.blink_shown())     # KARAOKE back while it is hidden, off once it is shown

    def blink_mic_elsewhere(self):
        """Blink's microphone when it is not the mixer ('' for none), None when it is (or Blink does not tell)."""
        if not self.blink or not isinstance(self.blink_devices, dict):
            return None
        chosen = self.blink_devices.get('input')
        return None if chosen == 'AG Podcast Mixer' else (chosen or '')

    def set_auto_redial_at(self, when):
        if when != self.auto_redial_at:
            self.auto_redial_at = when
            self.update()

    def set_auto_record(self, on):
        """Auto-record when on air (kept: mixer/auto_record): REC started as ON AIR lights (a Blink call, an app
        listening, streaming), stopped a while after it goes out."""
        if on != self.auto_record:
            self.auto_record = on
            util.settings().setValue('mixer/auto_record', on)
            log.info(f'[mixer] auto-record when on air {"on" if on else "off"}')
            self.auto_record_changed.emit(on)

    def _silence_meter(self, meter, channel=None):
        """A meter (one channel of it) at rest at once: its LEDs and their held peak, its needle against its stop."""
        rest = self._needle_ratio(self.VU_MIN - 2)
        channels = range(len(meter.levels)) if channel is None else [channel]
        for index in channels:
            meter.levels[index] = meter.target[index] = DB_LOW
            meter.peaks[index] = (DB_LOW, 0.0)
            if getattr(meter, 'needles', None) is not None and index < len(meter.needles):
                meter.needles[index], meter.speeds[index] = rest, 0.0
                meter.peak_lit[index] = 0.0
                if getattr(meter, 'rms', None) is not None:
                    meter.rms[index] = DB_LOW
        meter.received = time.monotonic()       # (the empty target kept till the next levels)
        self.update()

    def set_channel_mutes(self, left, right):
        """LOCAL LIBRARY's L and R (kept: mixer/library_channel_mutes)."""
        self.channel_mutes = [bool(left), bool(right)]
        util.settings().setValue('mixer/library_channel_mutes', json.dumps(self.channel_mutes))
        self.update()

    def set_needle_meters(self, on):
        """The strips' meters as analog VU meters with a needle, one dial per channel, left above right (kept:
        mixer/needle_meters), instead of the LED columns. The room's people's meters stay LEDs (too narrow for dials)."""
        if on != self.needle_meters:
            self.needle_meters = on
            util.settings().setValue('mixer/needle_meters', on)
            log.info(f'[ui] Mixer meters: {"analog needles" if on else "LEDs"}')
            self._layout()      # the faders placed for them
            self.update()

    def set_auto_redial(self, on):
        """Auto-redial (kept: mixer/auto_redial): a call the other side hung up, or that did not go through while
        redialing, called again after REDIAL_DELAY, until it is hung up here."""
        if on != self.auto_redial:
            self.auto_redial = on
            util.settings().setValue('mixer/auto_redial', on)
            log.info(f'[mixer] auto-redial {"on" if on else "off"}')
            self.auto_redial_changed.emit(on)

    def _strip_at(self, point):
        return next((name for strip, fader, meter, name in self._visible if strip.contains(point)), None)

    def _blink_astray_tip(self):
        mic = self.blink_mic_elsewhere() or 'no microphone'
        output = (self.blink_devices or {}).get('output')
        text = (f'Blink\'s microphone is {mic}, not AG Podcast Mixer: callers hear that, not this console. '
                f'In Blink\'s audio settings, choose AG Podcast Mixer as the input')
        if output != 'AG Podcast Mixer':
            text += f' and the output (now {output or "none"}: the call is not on this strip)'
        return text + '.'

    def set_blink_redial(self, redial):
        if redial != self.blink_redial:
            self.blink_redial = redial
            self.update()

    def set_bar_strip(self, key):
        """The strip the play bar drives, highlighted: 'LIBRARY' or 'MUSIC'."""
        if key != self.bar_strip:
            self.bar_strip = key
            self.update()

    def set_blink_participants(self, participants):
        """The rooms' participants, their levels fed to their meters (one column mono, two stereo)."""
        meters = {}
        for participant in participants:
            channels = 2 if participant.get('channels') == 2 else 1
            meter = self._participant_meters.get(participant['key'])
            if meter is None or len(meter.levels) != channels:
                meter = _ParticipantMeter(channels)
            level, levels = participant.get('level'), participant.get('levels')
            if level is None or participant['muted']:
                meter.feed([])
            elif isinstance(levels, list) and len(levels) == channels:      # each channel its own (a stereo participant)
                meter.feed([float(value) if value is not None else -120.0 for value in levels])
            else:
                meter.feed([float(level)] * channels)
            meters[participant['key']] = meter
        self._participant_meters = meters
        if not participants:        # (not drawn again: none to click or hover)
            self._participant_mutes, self._participant_columns = [], []
        shown = [(p['key'], p['name'], p['muted'], p.get('channels')) for p in participants] != \
            [(p['key'], p['name'], p['muted'], p.get('channels')) for p in self.blink_participants]
        self.blink_participants = participants
        if shown:
            self.update()

    def set_blink_calls(self, calls):
        if calls != self.blink_calls:
            relayout = bool(calls) != bool(self.blink_calls)       # TO CALLS shown or hidden
            self.blink_calls = calls
            ids = {(call[4] or {}).get('id') or call[0] for call in calls}
            self._connected_since = {key: when for key, when in self._connected_since.items() if key in ids}
            if relayout:
                self._layout()
            self.update()

    def set_blink(self, running):
        """Blink started (its strip shown) or quit (hidden)."""
        if running != self.blink:
            self.blink = running
            if not running:     # its strip not drawn: no HANG UP or REDIAL to click
                self._hangup_buttons = []
                self._participant_mutes = self._participant_columns = []
                self._redial_rect = QRectF(-1e6, -1e6, 1, 1)
            self._layout()
            self.update()
            self.blink_changed.emit(self.blink_shown())

    MONO_AFTER, STEREO_AFTER = 40, 8        # level messages (25 ms) alike, or not, before the call is taken as such

    def set_call_levels(self, rms):
        """What Blink plays: its meter one column while the call is mono (the driver gives two alike: a mono call
        played on a stereo device), two while it is stereo."""
        if len(rms) >= 2 and not any(math.isnan(v) for v in rms[:2]) and max(rms[:2]) > DB_LOW:
            alike = abs(rms[0] - rms[1]) < 0.05
            count = getattr(self, '_call_alike', 0)
            count = max(1, count + 1) if alike else min(-1, count - 1)
            self._call_alike = count
            channels = len(self.call_meter.levels)
            if (channels == 2 and count >= self.MONO_AFTER) or (channels == 1 and count <= -self.STEREO_AFTER):
                self._set_call_channels(1 if channels == 2 else 2)
        if len(self.call_meter.levels) == 1 and rms:
            rms = [max(v for v in rms[:2] if not math.isnan(v))] if any(not math.isnan(v) for v in rms[:2]) else []
        self.call_meter.feed(rms)

    def _set_call_channels(self, channels):
        """BLINK's meter as many columns as the call has channels."""
        meter = _Meter(channels)
        self.meters[self.meters.index(self.call_meter)] = meter
        self.call_meter = meter
        log.info(f'[mixer] Blink\'s call: {"mono" if channels == 1 else "stereo"} (its meter {channels} column{"s" if channels > 1 else ""})')
        self._layout()
        self.update()

    def set_music(self, value):
        """The Local Library's volume (0..100)."""
        if self.library_fader.set(value / 100):
            self.update()

    def set_music_levels(self, rms, peak):
        """The Local Library's levels (the player's, before its volume): shown after the fader and the mute, as sent."""
        value = self.library_fader.value
        if self.muted.get('LIBRARY') or value <= 0:
            self.library_meter.feed([])
            return
        gain = 60 * math.log10(value)        # the volume is cubic (player.set_volume): 20 log10(value ** 3)
        levels = [level + gain for level in rms]
        self.library_meter.feed(levels)

    def set_mic_levels(self, rms):
        self.mic_meter.feed(rms)

    def set_vocal_cut(self, on):
        if on != self.vocal_cut:
            self.vocal_cut = on
            self.update()

    def set_share(self, on):
        if on != self.sharing:
            self.sharing = on
            self.update()

    def set_share_available(self, available):
        self.share_available = available
        self.update()

    def set_vocal_cut_available(self, available):
        self.vocal_cut_available = available
        self.update()

    # The layout

    def resizeEvent(self, event):
        if self._title_editor is not None and self._title_editor.isVisible():
            self._record_title_edited()         # (the label moves: what was typed kept)
        self._layout()

    def _layout(self):
        margin = 14.0
        tall = 1.0 + (self.BAR if self.recorder_shown else 0.0)       # the strips, RECORDER's bar under them (shown)
        width = min(self.width() - 2 * margin, (self.height() - 2 * margin) / tall * self.ASPECT)
        unit = width / self.ASPECT
        height = unit * tall
        self.panel = QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)
        self.unit = unit
        # the input strips (MIC/LINE, then the others), APPS, then Blink's call, while it runs
        names = ['MIC'] + [slot.key for slot in self.slots] + ['LIBRARY'] + (['MUSIC'] if self.sources_available else []) \
            + (['BLINK'] if self.blink_shown() else [])       # (RECORDER: the bar under them)
        self._strip_keys = names
        count = len(names)
        if count <= 3:          # as it always was: two strips, or three with BLINK
            strip_width = width * (0.28 if count == 3 else 0.36)
            gap = width * (0.025 if count == 3 else 0.06)
        else:                   # more inputs: narrower strips across the console
            gap = width * 0.018
            strip_width = (width * 0.96 - (count - 1) * gap - (width * 0.06 if self.blink_shown() else 0)) / count
        apart = gap + width * 0.075 if self.blink_shown() else gap        # BLINK apart from what is sent: the arrows between
        if count == 3 and self.blink_shown():
            apart = width * 0.1
        total = count * strip_width + (count - 2) * gap + apart if self.blink_shown() else count * strip_width + (count - 1) * gap
        lefts, x = [], self.panel.center().x() - total / 2
        for name in names:
            if name == 'BLINK':
                x += apart - gap
            lefts.append(x)
            x += strip_width + gap
        self._strips = [QRectF(x, self.panel.top() + unit * 0.12, strip_width, unit * 0.82) for x in lefts]
        mic, library = self._strips[0], self._strips[names.index('LIBRARY')]
        music = self._strips[names.index('MUSIC')] if 'MUSIC' in names else QRectF(-1e6, -1e6, 1, 1)
        parts = {'MIC': (self.mic_fader, self.mic_meter), 'BLINK': (self.call_fader, self.call_meter),
                 'MUSIC': (self.music_fader, self.music_meter), 'LIBRARY': (self.library_fader, self.library_meter)}
        parts.update({slot.key: (slot.fader, slot.meter) for slot in self.slots})
        self._visible = [(strip, *parts[name], name) for strip, name in zip(self._strips, names)]
        # RECORDER's bar, across the console under the strips: REC, MARK, TAPES, the tape (a DAT's proportions, 73 x 54 mm),
        # how long and what it records, STREAMING at the right
        bar = QRectF(self.panel.left() + unit * 0.03, self.panel.top() + unit * 0.97, self.panel.width() - unit * 0.06,
                     unit * (self.BAR - 0.04))
        self._bar = bar
        rec = unit * 0.05
        # REC, MARK, TAPES: lowered, apart from the title above them (their names under them, inside the bar)
        self._record_rect = QRectF(bar.left() + unit * 0.06, bar.center().y() + unit * 0.012 - rec / 2, rec, rec)
        self._mark_rect = self._record_rect.translated(unit * 0.1, 0)
        # TAPES after MARK (the recordings in the library), a little further apart
        self._tape_button = self._mark_rect.translated(unit * 0.14, 0)
        # ON AIR, a lamp at the console's top left, after its maker's name (while someone remote can hear: on_air)
        maker = QFontMetricsF(self._font(unit * 0.035, spacing=-2)).horizontalAdvance('AG Projects')
        self._on_air_rect = QRectF(self.panel.left() + unit * 0.05 + maker + unit * 0.04, self.panel.top() + unit * 0.033,
                                   unit * 0.15, unit * 0.045)
        # + on the last input strip (fewer strips than input devices), - on each while there is more than one
        small = unit * 0.03
        last = self._strips[len(self.slots)]
        self._add_rect = QRectF(last.right() - small - unit * 0.012, last.top() + unit * 0.012, small, small) \
            if self.can_add() else QRectF(-1e6, -1e6, 1, 1)
        keys = ['MIC'] + [slot.key for slot in self.slots] if self.slots else []
        self._remove_rects = {key: QRectF(strip.left() + unit * 0.012, strip.top() + unit * 0.012, small, small)
                              for key, strip in zip(keys, self._strips)}
        # the top row: the input selector and the echo (MIC), the VOCAL CUT switch (MUSIC)
        # (the faders shorter, the knobs bigger: room for the marks around them, the name and what plays under them)
        row = mic.top() + unit * 0.17
        knob = min(unit * 0.066, strip_width * 0.17)       # narrower strips: smaller knobs
        self._knob = knob           # (the strips' top rows are placed from it: the same on every strip)
        self.streaming_selector.radius = min(knob, unit * 0.032) * 1.15     # RECORDER's STREAMING, at the bar's right
        # (its centre level with REC's, MARK's and TAPES')
        self.streaming_selector.centre = QPointF(bar.right() - unit * 0.13, self._record_rect.center().y())
        # the tape right after TAPES, magnified and cropped as a video frame: only the band with its label and its window
        # (the reels) seen, 24 % to 86 % of its height (_draw_tape), under the bar's title down to near its bottom
        band_top = bar.top() + unit * 0.05
        band = bar.bottom() - unit * 0.015 - band_top
        tape_height = band / 0.62
        tape_width = tape_height * 1.35
        self._tape_rect = QRectF(self._tape_button.right() + unit * 0.06, band_top - tape_height * 0.24, tape_width, tape_height)
        self._tape_view = QRectF(self._tape_rect.left(), band_top, tape_width, band)
        if not self.recorder_shown:     # the bar hidden: none of it under the mouse
            away = QRectF(-1e6, -1e6, 1, 1)
            self._bar = self._record_rect = self._mark_rect = self._tape_button = self._tape_view = self._tape_rect = away
            self.streaming_selector.centre = QPointF(-1e6, -1e6)
        # the menu button after PODCAST MIXER (the recorder shown or not, auto-redial)
        side = unit * 0.018
        self._view_button = QRectF(self.panel.right() - unit * 0.045, self.panel.top() + unit * 0.055 - side, side * 2, side * 2)
        # MIC/LINE with KARAOKE: INPUT and ECHO side by side, each with its marks and glyphs inside the strip (the
        # selector's reach about 1.9 radii, the echo's scale 1.4): sized from the strip's width
        karaoke = self.karaoke_on()
        self.selector.radius = min(knob, strip_width * 0.125) if karaoke else knob
        self.selector.centre = QPointF(mic.left() + mic.width() * (0.29 if karaoke else 0.5), row)
        for slot, strip in zip(self.slots, self._strips[1:]):
            slot.selector.radius = knob
            slot.selector.centre = QPointF(strip.center().x(), row)
        self.echo.radius = min(unit * 0.058, strip_width * 0.12)
        self.echo.centre = QPointF(mic.left() + mic.width() * 0.76, row) if karaoke else QPointF(-1e6, -1e6)
        # MUSIC: the SOURCE selector, as MIC's INPUT
        self.source_selector.radius = knob
        self.source_selector.centre = QPointF(music.center().x(), row) if self.sources_available else QPointF(-1e6, -1e6)
        # the title bar: POWER and KARAOKE, between the maker's name and the model's
        small = unit * 0.032
        middle = self.panel.top() + unit * 0.055
        self._power_rect = QRectF(self.panel.center().x() - unit * 0.2 - small / 2, middle - small / 2, small, small)
        self._karaoke_rect = QRectF(self.panel.center().x() + unit * 0.06 - small / 2, middle - small / 2, small, small) \
            if not self.blink_shown() else QRectF(-1e6, -1e6, 1, 1)        # KARAOKE: hidden while BLINK is shown
        # the faders, the meters right of them
        top, bottom = mic.top() + unit * 0.37, mic.bottom() - unit * 0.185     # under it: MUTE, then the name
        slot_width = unit * 0.016
        size = unit * 0.045
        for strip, fader, meter, name in self._visible:
            # with the needle meters a bit to the left: the dials right of it get the room
            x = strip.center().x() - strip.width() * (0.2 if self.needle_meters else 0.12)
            fader.place(QRectF(x - slot_width / 2, top, slot_width, bottom - top), unit * 0.06)
            columns = len(meter.levels)
            meter_width = unit * 0.022 * columns + unit * 0.006 * (columns - 1)
            meter.rect = QRectF(strip.center().x() + strip.width() * 0.16, top, meter_width, bottom - top)
        # MUTE under each strip's fader; its other switches where MUTE was, under the meter, a second one left of the
        # fader (TO AMPLIFIER; LOCAL LIBRARY's VOCAL CUT (KARAOKE) and TO CALLS (a call); AUDIO APPS' THRU and TO CALLS)
        hidden = QRectF(-1e6, -1e6, 1, 1)
        y = mic.bottom() - unit * 0.14 - size / 2

        def button(centre_x):
            return QRectF(centre_x - size / 2, y, size, size)
        self._mutes = {name: button(fader.slot.center().x()) for strip, fader, meter, name in self._visible}
        faders = {name: fader for strip, fader, meter, name in self._visible}
        meters = {name: meter for strip, fader, meter, name in self._visible}
        strips = {name: strip for strip, fader, meter, name in self._visible}

        def switches(name, wanted):
            """The rects of a strip's switches shown (wanted: one per switch, True shown): under the meter when one,
            the first left of the fader when two."""
            shown = [index for index, on in enumerate(wanted) if on]
            rects = [hidden] * len(wanted)
            if name not in strips:
                return rects
            strip = strips[name]
            # under the meter: the dials' middle with the needle meters, else the LED meter's
            beside = (self._needle_area(meters[name], strip) if self.needle_meters else meters[name].rect).center().x()
            places = [beside] if len(shown) == 1 else [strip.left() + strip.width() * 0.13, beside]
            for index, place in zip(shown, places):
                rects[index] = button(place)
            return rects
        self._monitor, = switches('MIC', [True])
        self._vocal, self._library_share = switches('LIBRARY', [self.karaoke_on(), bool(self.blink_calls)])
        # LOCAL LIBRARY: L and R, a channel muted each, smaller, centred under its meter (the dials); with TO CALLS
        # there too (a call): TO CALLS, L and R centred there together
        self._channel_mute_rects = {}
        if 'LIBRARY' in strips:
            strip, small_size = strips['LIBRARY'], size * 0.72
            middle = (self._needle_area(meters['LIBRARY'], strip) if self.needle_meters else meters['LIBRARY'].rect).center().x()
            gap = size * 0.55
            share = self._library_share.left() > -1e5       # shown
            widths = ([size] if share else []) + [small_size, small_size]
            x = middle - (sum(widths) + gap * (len(widths) - 1)) / 2
            if share:
                self._library_share = button(x + size / 2)
                x += size + gap
            for key in ('L', 'R'):
                self._channel_mute_rects[key] = QRectF(x, y + (size - small_size) / 2, small_size, small_size)
                x += small_size + gap
        app_call = bool(self.source() and self.blink_calls and 'MUSIC' in names)
        self._cut, self._share = switches('MUSIC', [app_call, app_call])

    # Drawing

    def _font(self, pixels, bold=False, spacing=0.0, weight=None):
        font = QFont(self.font())
        font.setPixelSize(max(6, round(pixels)))
        font.setBold(bold)
        if weight is not None:
            font.setWeight(weight)
        if spacing:
            font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100 + spacing)
        return font

    def paintEvent(self, event):
        if not self._strips:
            self._layout()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), BACKGROUND)
        unit, panel = self.unit, self.panel
        # the console: dark anodized metal
        metal = QLinearGradient(0, panel.top(), 0, panel.bottom())
        metal.setColorAt(0.0, QColor('#3a3e45'))
        metal.setColorAt(1.0, QColor('#23262b'))
        painter.setPen(QPen(QColor('#0b0c0e'), 1))
        painter.setBrush(metal)
        painter.drawRoundedRect(panel, unit * 0.02, unit * 0.02)
        painter.setPen(QColor('#9aa0a8'))
        painter.setFont(self._font(unit * 0.035, spacing=-2))
        painter.drawText(QRectF(panel.left() + unit * 0.05, panel.top() + unit * 0.02, panel.width() - unit * 0.1, unit * 0.07),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, 'AG Projects')
        painter.setFont(self._font(unit * 0.026, spacing=14))
        painter.drawText(QRectF(panel.left() + unit * 0.05, panel.top() + unit * 0.02, panel.width() - unit * 0.1, unit * 0.07),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, 'PODCAST MIXER')
        # after it: the menu button, three lines as mobile apps draw it (the recorder shown, auto-redial)
        button, side = self._view_button, self._view_button.width() / 2
        middle = button.center()
        painter.save()
        painter.setPen(QPen(QColor('#9aa0a8'), max(1.5, side * 0.2), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for offset in (-side * 0.5, 0.0, side * 0.5):
            painter.drawLine(middle + QPointF(-side * 0.6, offset), middle + QPointF(side * 0.6, offset))
        painter.restore()
        label = self._font(unit * 0.026, spacing=10, weight=QFont.Weight.DemiBold)
        light = QColor('#d6d9de')
        buttons = [(self._power_rect, 'POWER', self.powered, '#3ddc84')]
        if not self.blink_shown():      # KARAOKE: hidden while BLINK is shown
            buttons.append((self._karaoke_rect, 'KARAOKE', self.karaoke_on(), '#ffb21f'))
        for rect, text, on, color in buttons:
            enabled = rect is not self._karaoke_rect or not self.blink_calls       # KARAOKE: not in a call
            self._draw_button(painter, rect, on, enabled, QColor(color))
            painter.setFont(self._font(unit * 0.022, spacing=8, weight=QFont.Weight.DemiBold))
            painter.setPen(light if enabled else QColor('#6a7078'))
            painter.drawText(QRectF(rect.right() + unit * 0.012, rect.top() - unit * 0.01, unit * 0.2, rect.height() + unit * 0.02),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        if not self.powered:
            painter.setOpacity(0.35)
        for strip, fader, meter, name in self._visible:
            painter.setPen(QPen(QColor(0, 0, 0, 90), 1))
            painter.setBrush(QColor(255, 255, 255, 10))
            painter.drawRoundedRect(strip, unit * 0.015, unit * 0.015)
            # the strip the play bar drives: lit, an orange edge; only while both music sources play (the Local
            # Library and Audio Apps), not the Local Library alone
            if name == self.bar_strip and 'MUSIC' in self._strip_keys and self.library_playing and self.source_playing:
                painter.setPen(QPen(QColor(255, 179, 71, 170), 1.6))
                painter.setBrush(QColor(255, 179, 71, 14))
                painter.drawRoundedRect(strip.adjusted(0.8, 0.8, -0.8, -0.8), unit * 0.015, unit * 0.015)
            # BLINK with another microphone than the mixer: its frame and name red (the console is not what the
            # call hears), the why on hovering it and under who the call is with
            astray = name == 'BLINK' and self.blink_mic_elsewhere() is not None
            if astray:
                painter.setPen(QPen(QColor(255, 59, 48, 220), 2.0))
                painter.setBrush(QColor(255, 59, 48, 22))
                painter.drawRoundedRect(strip.adjusted(1.0, 1.0, -1.0, -1.0), unit * 0.015, unit * 0.015)
            painter.setPen(QColor('#ff6b5b') if astray else light)
            title = ('\u26a0 ' if astray else '') + self.strip_title(name)
            font = self._font(unit * TITLE_SIZE, spacing=10, weight=QFont.Weight.Bold)
            if QFontMetricsF(font).horizontalAdvance(title) > strip.width() * 0.94:     # a longer name: tighter, smaller
                size = unit * TITLE_SIZE * 0.9
                font = self._font(size, spacing=4, weight=QFont.Weight.Bold)
                while QFontMetricsF(font).horizontalAdvance(title) > strip.width() * 0.94 and size > unit * 0.018:
                    size *= 0.92
                    font = self._font(size, spacing=4, weight=QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(QRectF(strip.left(), strip.bottom() - unit * 0.068, strip.width(), unit * 0.06),
                             Qt.AlignmentFlag.AlignCenter, title)
        if self.recorder_shown:
            self._draw_recorder(painter, self._bar, label, light)
        self._draw_on_air(painter)
        blink = next((strip for strip, fader, meter, name in self._visible if name == 'BLINK'), None)
        if blink is not None:
            self._draw_link(painter, blink)
            self._draw_calls(painter, blink, label, light)
        # MIC: the input selector
        mic = self._strips[0]
        centre, radius = self.selector.centre, self.selector.radius
        half = mic.width() * 0.56          # a column: its label and name centred on it
        self._draw_selector(painter, self.selector, 'INPUT', half, label, light,
                            name_centre=mic.center().x(), name_width=mic.width() * 0.92,      # the device: across the strip
                            icons=self.selector.icons)
        # the other input strips: their INPUT, and - ; + on the last one
        for slot, strip in zip(self.slots, self._strips[1:]):
            self._draw_selector(painter, slot.selector, 'INPUT', strip.width() * 0.9, label, light,
                                name_centre=strip.center().x(), name_width=strip.width() * 0.92, icons=self.selector.icons)
        for rect in self._remove_rects.values():
            self._draw_sign(painter, rect, False)
        self._draw_sign(painter, self._add_rect, True)
        # MIC: the echo, its scale and name (KARAOKE)
        knob = self.echo
        painter.setPen(QPen(light, 1.1))
        for index in range(11 if self.karaoke_on() else 0):
            angle = math.radians(knob.angle(index / 10) - 90)
            direction = QPointF(math.cos(angle), math.sin(angle))
            painter.drawLine(knob.centre + direction * knob.radius * 1.18, knob.centre + direction * knob.radius * (1.36 if index in (0, 10) else 1.27))
        if self.karaoke_on():
            painter.setFont(label)
            painter.drawText(QRectF(knob.centre.x() - half / 2, centre.y() - radius * 1.45 - unit * 0.05, half, unit * 0.045),
                             Qt.AlignmentFlag.AlignCenter, 'ECHO')
            draw_knob(painter, knob)
        # AUDIO APPS: the SOURCE selector (Off, or an app that plays), what it plays under it
        music = next((strip for strip, fader, meter, name in self._visible if name == 'MUSIC'), None)
        if music is not None:
            self._draw_selector(painter, self.source_selector, 'SOURCE', half, label, light,
                                name_centre=music.center().x(), name_width=music.width() * 0.92,
                                icons=self.source_selector.icons)
            self._draw_track(painter, music, self.source_track, self.source_selector.centre.y() + self.source_selector.radius * 1.4)
        # LOCAL LIBRARY: what it plays, where a selector's name would be
        library = next(strip for strip, fader, meter, name in self._visible if name == 'LIBRARY')
        self._draw_library(painter, library, label, light)
        # the music's switches at the bottom with their names under them, as TO AMPLIFIER: LOCAL LIBRARY's VOCAL CUT
        # (KARAOKE) and TO CALLS (a call), AUDIO APPS' THRU and TO CALLS (an app, a call)
        small = self._font(unit * 0.02, spacing=6, weight=QFont.Weight.DemiBold)
        switches = []
        if self.karaoke_on():
            switches.append((self._vocal, 'VOCAL CUT', self.vocal_cut, self.vocal_cut_available, '#ffb21f'))
        if self.blink_calls:
            switches.append((self._library_share, 'TO CALLS', self.library_sharing, self.share_available, '#3ddc84'))
            if self.source():       # THRU matters only with a call: without, the app plays as it is anyway
                switches.append((self._cut, 'THRU', self.app_output, True, '#4aa8ff'))
                switches.append((self._share, 'TO CALLS', self.sharing, self.share_available, '#3ddc84'))
        for rect, text, on, enabled, color in switches:
            self._draw_button(painter, rect, on, enabled, QColor(color))
            self._label_under(painter, rect, text, small, light if enabled else QColor('#6a7078'))
        # the faders and the meters
        for name, rect in self._mutes.items():
            self._draw_button(painter, rect, self.muted.get(name, False), True, QColor('#ff4a3d'))
            self._label_under(painter, rect, 'MUTE', small, light)
        for index, (key, rect) in enumerate(self._channel_mute_rects.items()):        # LOCAL LIBRARY's L and R, named under them
            self._draw_button(painter, rect, self.channel_mutes[index], True, QColor('#ff4a3d'))
            self._label_under(painter, rect, key, small, light)
        # MIC: TO AMPLIFIER, left of its MUTE, its name under it
        self._draw_button(painter, self._monitor, self.monitor, True, QColor('#3ddc84'))
        self._label_under(painter, self._monitor, 'TO AMPLIFIER', small, light)
        for strip, fader, meter, name in self._visible:
            send = name == 'MUSIC' and self.thru()
            painter.save()
            if send:        # THRU: APPS' fader is what goes into the call (SEND); dimmed while nothing is sent
                painter.setFont(small)
                painter.setPen(light)
                painter.drawText(QRectF(fader.slot.center().x() - unit * 0.1, fader.slot.top() - unit * 0.04, unit * 0.2, unit * 0.03),
                                 Qt.AlignmentFlag.AlignCenter, 'SEND')
                if not self.sending():
                    painter.setOpacity(painter.opacity() * 0.35)
            self._draw_scale(painter, fader)
            if name == 'BLINK' and self.blink_participants:     # a room: its people's meters, not the mix's
                self._draw_participant_meters(painter, strip, meter)
            else:
                self._draw_meter(painter, meter, strip)
            self._draw_fader(painter, fader)
            painter.restore()
        painter.setOpacity(1.0)

    def _label_under(self, painter, rect, text, font, color):
        """A button's name under it, as wide as the room to its nearest neighbour on that row allows (two lines
        when it has to: TO / CALLS)."""
        unit = self.unit
        others = list(self._mutes.values()) + [self._monitor, self._vocal, self._library_share, self._cut, self._share] + \
            list(self._channel_mute_rects.values())
        gaps = [abs(other.center().x() - rect.center().x()) for other in others
                if other is not rect and abs(other.center().y() - rect.center().y()) < 1 and other.left() > -1e5]
        width = min([unit * 0.2] + [gap - unit * 0.008 for gap in gaps])
        if text == 'TO AMPLIFIER' and QFontMetricsF(font).horizontalAdvance(text) > width:
            text = 'AMPLIFIER'
        size = font.pixelSize()
        while QFontMetricsF(font).horizontalAdvance(text) > width and size > unit * 0.012:      # one line: smaller
            size *= 0.92
            font = self._font(size, spacing=2, weight=QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(QRectF(rect.center().x() - width / 2, rect.bottom() + unit * 0.01, width, unit * 0.03),
                         Qt.AlignmentFlag.AlignCenter, text)

    def _draw_sign(self, painter, rect, plus):
        """+ (another input strip) or - (this one gone): a small round key."""
        if rect.left() < -1e5:
            return
        painter.save()
        painter.setPen(QPen(QColor('#0b0c0e'), 0.8))
        painter.setBrush(QColor('#4a4f57'))
        painter.drawEllipse(rect)
        painter.setPen(QPen(QColor('#e6e8eb'), max(1.2, rect.width() * 0.11), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        centre, arm = rect.center(), rect.width() * 0.26
        painter.drawLine(centre - QPointF(arm, 0), centre + QPointF(arm, 0))
        if plus:
            painter.drawLine(centre - QPointF(0, arm), centre + QPointF(0, arm))
        painter.restore()

    def _draw_recorder(self, painter, bar, label, light):
        """RECORDER's bar: its frame and name, the DAT tape (its reels turning while it records), REC, MARK, TAPES,
        REC and for how long (or READY), what it records (or saved last) and the markers, STREAMING at the right."""
        unit = self.unit
        painter.setPen(QPen(QColor(0, 0, 0, 90), 1))
        painter.setBrush(QColor(255, 255, 255, 10))
        painter.drawRoundedRect(bar, unit * 0.015, unit * 0.015)
        if self.recording:      # (as a selected strip: its frame lit, red)
            painter.setPen(QPen(QColor(255, 74, 61, 150), 1.6))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(bar.adjusted(0.8, 0.8, -0.8, -0.8), unit * 0.015, unit * 0.015)
        # its name in the top left corner, as the strips' under them (above REC, MARK and TAPES)
        painter.setPen(QColor('#9aa0a8'))      # (as the console's PODCAST MIXER: AUDIO RECORDER)
        title_font = self._font(unit * 0.026, spacing=14)
        title = self.strip_title('RECORDER').upper()
        painter.setFont(title_font)
        painter.drawText(QRectF(bar.left() + unit * 0.03, bar.top() + unit * 0.008, unit * 0.6, unit * 0.04),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        painter.save()          # the tape cropped to its band: the label and the reels
        view = QPainterPath()
        view.addRoundedRect(self._tape_view, unit * 0.012, unit * 0.012)
        painter.setClipPath(view)
        self._draw_tape(painter, self._tape_rect)
        painter.restore()
        painter.setPen(QPen(QColor(0, 0, 0, 140), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self._tape_view, unit * 0.012, unit * 0.012)
        # REC, MARK (while it records), TAPES, their names under them
        self._draw_button(painter, self._record_rect, self.recording, self.record_available, QColor('#ff2a1f'))
        self._draw_button(painter, self._mark_rect, False, self.recording, QColor('#ffb347'))
        self._draw_button(painter, self._tape_button, False, True, QColor('#d6d9de'))
        painter.setFont(self._font(unit * 0.02, spacing=6, weight=QFont.Weight.DemiBold))
        for rect, name, enabled in ((self._record_rect, 'REC', self.record_available), (self._mark_rect, 'MARK', self.recording),
                                    (self._tape_button, 'TAPES', True)):
            painter.setPen(light if enabled else QColor('#6a7078'))
            painter.drawText(QRectF(rect.center().x() - unit * 0.05, rect.bottom() + unit * 0.008, unit * 0.1, unit * 0.03),
                             Qt.AlignmentFlag.AlignCenter, name)
        # REC and for how long, what it records, the markers: after the tape, before STREAMING
        left = self._tape_view.right() + unit * 0.05
        right = self.streaming_selector.centre.x() - self.streaming_selector.radius - unit * 0.07
        width = max(unit * 0.1, right - left)
        if self.recording:
            seconds = int(time.monotonic() - self.record_started)
            text = f'REC {seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}' if seconds >= 3600 else \
                f'REC {seconds // 60:02d}:{seconds % 60:02d}'
        else:       # (ready to record: nothing said; only when it cannot)
            text = '' if self.record_available else 'NO ENCODER'
        painter.setFont(self._font(unit * 0.042, weight=QFont.Weight.DemiBold))
        painter.setPen(QColor('#ff4a3d') if self.recording else light)
        painter.drawText(QRectF(left, bar.top() + unit * 0.05, width, unit * 0.055), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        small = self._font(unit * 0.022, weight=QFont.Weight.Medium)
        painter.setFont(small)
        painter.setPen(QColor('#8a9098'))
        what = QFontMetricsF(small).elidedText(self.record_what or '', Qt.TextElideMode.ElideRight, width)
        painter.drawText(QRectF(left, bar.top() + unit * 0.105, width, unit * 0.035), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, what)
        if self.recording and self.markers:
            painter.setPen(QColor('#ffb347'))
            painter.drawText(QRectF(left, bar.top() + unit * 0.14, width, unit * 0.035), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             f'{self.markers} marker{"s" if self.markers != 1 else ""}')
        # STREAMING at the right: Off, On (dimmed without the driver)
        painter.save()
        if not self.streaming_available:
            painter.setOpacity(painter.opacity() * 0.4)
        knob_width = unit * 0.2
        small_label = self._font(unit * 0.026 * 0.75, spacing=10, weight=QFont.Weight.DemiBold)     # (the title's, smaller)
        self._draw_selector(painter, self.streaming_selector, '', knob_width, small_label, light, named=False)
        # its name under it, as REC's, MARK's and TAPES' under theirs; while it streams, what to pick in the streaming
        # app (OBS, a browser...) instead: centred under the knob as far as the bar's right edge lets it, else ending there
        knob = self.streaming_selector
        font = self._font(unit * 0.02, spacing=6, weight=QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(light)
        streaming = self.streaming() and self.streaming_available
        name = 'USE AG PODCAST STREAMING DEVICE' if streaming else 'STREAMING'
        text_width = QFontMetricsF(font).horizontalAdvance(name) + unit * 0.01
        name_left = min(knob.centre.x() - text_width / 2, bar.right() - unit * 0.015 - text_width)
        painter.drawText(QRectF(name_left, knob.centre.y() + knob.radius * 1.2, text_width, unit * 0.03),
                         Qt.AlignmentFlag.AlignCenter, name)
        # ON red while it streams (someone hears it: as ON AIR)
        self._tick_names(painter, self.streaming_selector, knob_width, 'OFF', 'ON', light,
                         QColor('#ff4a3d') if self.streaming() else light, reach=1.9)       # (outside its glow)
        painter.restore()
        if self.streaming() and self.streaming_available:       # streaming: a red ring glowing around the knob
            self._draw_glow(painter, self.streaming_selector)

    def set_listeners(self, listeners):
        if listeners != self.listeners:
            self.listeners = listeners
            self.update()

    def on_air(self):
        """Whether someone hears the console now: a Blink call connected (on hold too), another app reading "AG Podcast
        Mixer" (Zoom, a browser), or STREAMING on with an app reading "AG Podcast Streaming"."""
        return bool(self._on_air_with())

    def _on_air_with(self):
        """Who hears the console: a Blink call, the apps reading our devices."""
        found = ['a Blink call'] if any(not (target or {}).get('state')
                                         for title, detail, held, quality, target in self.blink_calls) else []
        found += [name or 'an app' for name in self.listeners.get('mixer') or []]
        if self.streaming():
            found += [f'{name or "an app"} (streaming)' for name in self.listeners.get('streaming') or []]
        return found

    def _draw_glow(self, painter, knob):
        """A red ring glowing around a knob: someone hears it through that (STREAMING on, a call connected)."""
        centre, radius = knob.centre, knob.radius
        painter.save()
        glow = QRadialGradient(centre, radius * 1.7)
        glow.setColorAt(1 / 1.7, QColor(255, 42, 31, 150))
        glow.setColorAt(0.75, QColor(255, 42, 31, 60))
        glow.setColorAt(1.0, QColor(255, 42, 31, 0))
        ring = QPainterPath()
        ring.addEllipse(centre, radius * 1.7, radius * 1.7)
        ring.addEllipse(centre, radius * 1.02, radius * 1.02)       # (the knob itself left as it is)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawPath(ring)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor('#ff2a1f'), max(1.5, self.unit * 0.003)))
        painter.drawEllipse(centre, radius * 1.06, radius * 1.06)
        painter.restore()

    def _draw_on_air(self, painter):
        """ON AIR at the console's top left, only while someone hears it (on_air: a call, an app, streaming): lit red,
        glowing, a red line along the console's top. Otherwise not there."""
        unit, rect = self.unit, self._on_air_rect
        if not self.on_air():
            return
        painter.save()
        painter.setPen(QPen(QColor(255, 42, 31, 200), max(2.0, unit * 0.004)))
        radius = unit * 0.02
        painter.drawLine(QPointF(self.panel.left() + radius, self.panel.top() + 1.5), QPointF(self.panel.right() - radius, self.panel.top() + 1.5))
        glow = QRadialGradient(rect.center(), rect.width() * 0.8)
        glow.setColorAt(0.0, QColor(255, 42, 31, 90))
        glow.setColorAt(1.0, QColor(255, 42, 31, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawRoundedRect(rect.adjusted(-unit * 0.03, -unit * 0.02, unit * 0.03, unit * 0.02), unit * 0.03, unit * 0.03)
        painter.setBrush(QColor('#ff2a1f'))
        painter.setPen(QPen(QColor('#7a120c'), 1))
        painter.drawRoundedRect(rect, rect.height() * 0.3, rect.height() * 0.3)
        painter.setFont(self._font(rect.height() * 0.55, spacing=8, weight=QFont.Weight.Bold))
        painter.setPen(QColor('#ffffff'))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, 'ON AIR')
        painter.restore()

    def _draw_tape(self, painter, rect):
        """A DAT cassette, front view (generic, no maker's name): the glossy shell, its lid over the tape with the grip
        ridges and the TAPE mark moulded in it, the paper label (one row: the title, or Enter title...), the large
        smoked window with half of each hub at its ends (turning while it records, the tape going from the left pack to
        the right), the write-protect tab. No REC on it: the bar's REC button says so. The bar shows the band from 24 %
        to 86 % of its height: the label and the window (_layout)."""
        p = painter
        w, h = rect.width(), rect.height()
        recording = self.recording
        wound = min(1.0, (time.monotonic() - self.record_started) / 3600) if recording else 0.0
        p.save()
        for k in range(6):      # its shadow
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 22))
            p.drawRoundedRect(rect.translated(0, 2 + k).adjusted(-k * 0.6, 0, k * 0.6, k * 0.4), h * 0.06, h * 0.06)
        # the shell: glossy black, lighter at the top
        g = QLinearGradient(0, rect.top(), 0, rect.bottom())
        g.setColorAt(0.0, QColor('#3b3e44'))
        g.setColorAt(0.08, QColor('#202226'))
        g.setColorAt(0.6, QColor('#141518'))
        g.setColorAt(1.0, QColor('#0b0c0e'))
        p.setPen(QPen(QColor('#050506'), 1.2))
        p.setBrush(g)
        p.drawRoundedRect(rect, h * 0.055, h * 0.055)
        # the lid over the tape along the top: a panel of its own, the gap under it, ridges at both ends
        lid = QRectF(rect.left() + w * 0.015, rect.top() + h * 0.015, w * 0.97, h * 0.2)
        lg = QLinearGradient(0, lid.top(), 0, lid.bottom())
        lg.setColorAt(0, QColor('#4a4e55'))
        lg.setColorAt(0.25, QColor('#2a2d32'))
        lg.setColorAt(1, QColor('#17191c'))
        p.setPen(QPen(QColor('#060607'), 1))
        p.setBrush(lg)
        p.drawRoundedRect(lid, h * 0.04, h * 0.04)
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.drawLine(QPointF(lid.left() + h * 0.04, lid.top() + 1.2), QPointF(lid.right() - h * 0.04, lid.top() + 1.2))
        p.setPen(QPen(QColor('#060607'), max(1.0, h * 0.012)))
        p.drawLine(QPointF(rect.left() + w * 0.02, lid.bottom() + h * 0.012), QPointF(rect.right() - w * 0.02, lid.bottom() + h * 0.012))
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        for k in range(9):
            for x0 in (lid.left() + w * 0.035, lid.right() - w * 0.035 - 8 * w * 0.009):
                x = x0 + k * w * 0.009
                p.drawLine(QPointF(x, lid.top() + lid.height() * 0.25), QPointF(x, lid.bottom() - lid.height() * 0.25))
        # the TAPE mark on the lid, moulded into it (light, a dark edge under it), between its ridges
        mark = self._font(lid.height() * 0.62, spacing=6, weight=QFont.Weight.Black)
        mark.setItalic(True)
        p.setFont(mark)
        p.setPen(QColor(0, 0, 0, 160))
        p.drawText(lid.translated(0, max(1.0, h * 0.008)), Qt.AlignmentFlag.AlignCenter, 'TAPE')
        p.setPen(QColor('#b9bdc3'))
        p.drawText(lid, Qt.AlignmentFlag.AlignCenter, 'TAPE')
        # the paper label: one row, the title written on it, as wide as the paper
        label = QRectF(rect.left() + w * 0.07, rect.top() + h * 0.27, w * 0.86, h * 0.14)
        paper = QLinearGradient(0, label.top(), 0, label.bottom())
        paper.setColorAt(0, QColor('#f4f1e8'))
        paper.setColorAt(1, QColor('#e2ddd0'))
        p.setPen(QPen(QColor('#9b968a'), 0.6))
        p.setBrush(paper)
        p.drawRoundedRect(label, h * 0.015, h * 0.015)
        text = label.adjusted(label.width() * 0.04, label.height() * 0.1, -label.width() * 0.04, -label.height() * 0.1)
        p.setPen(QColor('#22252a'))
        # the recording's title written on it, else "Enter title..." (a click edits it: the file renamed, or the next
        # recording's title)
        p.setFont(self._font(label.height() * 0.56, weight=QFont.Weight.DemiBold))
        if self.record_title:
            p.drawText(text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetricsF(p.font()).elidedText(self.record_title, Qt.TextElideMode.ElideRight, text.width()))
        else:
            hint = self._font(label.height() * 0.56, weight=QFont.Weight.Normal)
            hint.setItalic(True)
            p.setFont(hint)
            p.setPen(QColor('#9b968a'))
            p.drawText(text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetricsF(hint).elidedText(TITLE_HINT, Qt.TextElideMode.ElideRight, text.width()))
        self._label_rect = QRectF(text.left(), text.top(), text.width(), text.height())
        # the window, large: the hubs' centres at its two ends, half of each seen; the reels themselves inside the
        # shell, their packs' inner edges and the tape between them showing
        win = QRectF(rect.left() + w * 0.2, rect.top() + h * 0.45, w * 0.6, h * 0.38)
        p.setPen(QPen(QColor('#050506'), 1))
        p.setBrush(QColor('#07080a'))
        p.drawRoundedRect(win.adjusted(-2, -2, 2, 2), h * 0.02, h * 0.02)
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(win, h * 0.015, h * 0.015)
        p.setClipPath(clip)
        p.fillRect(win, QColor('#14100d'))
        r = win.height() * 0.62
        hubs = [QPointF(win.left() + r * 0.15, win.center().y()), QPointF(win.right() - r * 0.15, win.center().y())]
        for centre, amount in zip(hubs, (1.0 - wound * 0.6, 0.4 + wound * 0.6)):
            radius = r * (1.25 + 0.9 * amount)
            pack = QRadialGradient(centre, radius)
            pack.setColorAt(0, QColor('#5a4032'))
            pack.setColorAt(0.85, QColor('#3d2a20'))
            pack.setColorAt(1, QColor('#2a1c15'))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(pack)
            p.drawEllipse(centre, radius, radius)
        # both reels in step, counter-clockwise (the tape going from the left reel to the right one), eased (_tick)
        reels = self._reel_angle
        for index, centre in enumerate(hubs):
            radius = r * 0.8
            metal = QRadialGradient(centre - QPointF(radius * 0.3, radius * 0.3), radius * 1.4)
            metal.setColorAt(0, QColor('#f3f4f5'))
            metal.setColorAt(1, QColor('#9da2a8'))
            p.setPen(QPen(QColor('#3a3d42'), 0.8))
            p.setBrush(metal)
            p.drawEllipse(centre, radius, radius)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor('#15171a'))
            start = reels       # (both together, the same way)
            for k in range(6):      # the drive teeth around the centre hole
                angle = math.radians(start + k * 60)
                p.drawEllipse(centre + QPointF(math.cos(angle), math.sin(angle)) * radius * 0.55, radius * 0.12, radius * 0.12)
            p.drawEllipse(centre, radius * 0.28, radius * 0.28)
            p.setBrush(QColor('#c9ccd0'))
            p.drawEllipse(centre, radius * 0.1, radius * 0.1)
        p.fillRect(win, QColor(40, 50, 60, 70))      # the smoked glass, its highlight
        glare = QLinearGradient(win.topLeft(), win.bottomRight())
        glare.setColorAt(0.0, QColor(255, 255, 255, 50))
        glare.setColorAt(0.35, QColor(255, 255, 255, 0))
        p.fillRect(win, glare)
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(win.adjusted(-0.5, -0.5, 0.5, 0.5), h * 0.015, h * 0.015)
        # the write-protect tab (closed: dark, while it records), the slider's guide holes
        tab = QRectF(rect.left() + w * 0.07, rect.top() + h * 0.6, w * 0.07, h * 0.08)
        p.setPen(QPen(QColor('#050506'), 0.8))
        p.setBrush(QColor('#1b1d20') if recording else QColor('#d9dcdf'))
        p.drawRoundedRect(tab, 2, 2)
        p.setBrush(QColor('#050506'))
        for x in (0.14, 0.86):
            p.drawEllipse(QPointF(rect.left() + w * x, rect.bottom() - h * 0.08), h * 0.03, h * 0.03)
        # the gloss over its upper half
        sheen = QPainterPath()
        sheen.addRoundedRect(rect.adjusted(1, 1, -1, -h * 0.55), h * 0.05, h * 0.05)
        light = QLinearGradient(0, rect.top(), 0, rect.top() + h * 0.45)
        light.setColorAt(0, QColor(255, 255, 255, 30))
        light.setColorAt(1, QColor(255, 255, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(light)
        p.drawPath(sheen)
        p.restore()

    def _draw_library(self, painter, strip, label, light):
        """LOCAL LIBRARY's top: NOW PLAYING (or PAUSED), the song's title in orange (grey paused), the artist under it;
        NOTHING PLAYING when it is stopped (a tune that ended)."""
        unit = self.unit
        width = strip.width() * 0.92
        left = strip.center().x() - width / 2
        y = self.selector.centre.y() - self._knob * 1.45 - unit * 0.05
        header = ('NOW PLAYING' if self.library_playing else 'PAUSED') if self.library_track else 'NOTHING PLAYING'
        painter.setFont(self._fitted(label, header, width))
        painter.setPen(light)
        painter.drawText(QRectF(left, y, width, unit * 0.045), Qt.AlignmentFlag.AlignCenter, header)
        if not self.library_track:
            return
        title, artist = self.library_track
        # as the other strips: the title on the names row (a knob's name, Blink's party), the artist on the row under
        # it (Blink's RTT, the recorder's REC time), both in the names' font
        names_row = self.selector.centre.y() + self._knob * 1.4
        font = self._font(unit * 0.024, weight=QFont.Weight.Medium)
        metrics = QFontMetricsF(font)
        painter.setFont(font)
        painter.setPen(QColor('#ffb347') if self.library_playing else QColor('#8a9098'))
        painter.drawText(QRectF(left, names_row, width, unit * 0.04), Qt.AlignmentFlag.AlignCenter,
                         metrics.elidedText(title, Qt.TextElideMode.ElideRight, width))
        if artist:
            painter.setPen(QColor('#aab0b8'))
            painter.drawText(QRectF(left, names_row + unit * 0.04, width, unit * 0.04), Qt.AlignmentFlag.AlignCenter,
                             metrics.elidedText(artist, Qt.TextElideMode.ElideRight, width))

    def _fitted(self, font, text, width):
        """font, smaller if text would not fit width (a header on a narrow strip)."""
        fitted = QFont(font)
        while QFontMetricsF(fitted).horizontalAdvance(text) > width and fitted.pixelSize() > 8:
            fitted.setPixelSize(max(8, int(fitted.pixelSize() * 0.92)))
        return fitted

    def _draw_track(self, painter, strip, track, top):
        """What a source plays, a secondary line under its name: "title — artist"."""
        if not track:
            return
        unit = self.unit
        title, artist = track
        text = f'{title} — {artist}' if artist else title
        width = strip.width() * 0.92
        left = strip.center().x() - width / 2
        y = top + unit * 0.04          # the row under the names, in their font (as Blink's RTT, the REC time)
        detail_font = self._font(unit * 0.024, weight=QFont.Weight.Medium)
        painter.setFont(detail_font)
        painter.setPen(QColor('#aab0b8'))
        painter.drawText(QRectF(left, y, width, unit * 0.04), Qt.AlignmentFlag.AlignCenter,
                         QFontMetricsF(detail_font).elidedText(text, Qt.TextElideMode.ElideRight, width))

    def _arrow(self, painter, tip, angle, color):
        """A small filled arrowhead pointing at tip (angle: 0 right, -90 up)."""
        size = self.unit * 0.014
        direction = QPointF(math.cos(math.radians(angle)), math.sin(math.radians(angle)))
        normal = QPointF(-direction.y(), direction.x())
        base = tip - direction * size * 1.6
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        from PyQt6.QtGui import QPolygonF
        painter.drawPolygon(QPolygonF([tip, base + normal * size, base - normal * size]))
        painter.restore()

    def _sent(self):
        """Something goes out to the call now: the orange arrow lit."""
        live = any(not held for title, detail, held, quality, target in self.blink_calls)
        mics = (bool(self.selector.current()) and not self.muted.get('MIC')) or \
            any(slot.input and not self.muted.get(slot.key) for slot in self.slots)
        return live and not self.send_cut and (mics or (self.sending() and not self.muted.get('MUSIC')) or
                                               (self.library_sending() and not self.muted.get('LIBRARY')))

    def _draw_link(self, painter, blink):
        """Between the last strip sent (AUDIO APPS, else LOCAL LIBRARY) and BLINK: orange to the call (what is sent),
        black from it (what is heard); dim when nothing flows."""
        unit = self.unit
        music = self._visible[-2][0]
        live = any(not held for title, detail, held, quality, target in self.blink_calls)
        dim = QColor('#4a4f56')
        left, right = music.right() + unit * 0.025, blink.left() - unit * 0.025
        middle = blink.top() + blink.height() * 0.45
        painter.save()
        for y, color, out, lit in ((middle - unit * 0.04, QColor(SEND_COLOR), True, self._sent()),
                                   (middle + unit * 0.04, QColor(RECEIVE_COLOR), False, live and not self.muted.get('BLINK'))):
            pen = color if lit else dim
            head = unit * 0.022
            # the same width lit or dim, flat ends: both arrows span exactly left..right, one over the other
            painter.setPen(QPen(pen, unit * 0.008, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            painter.drawLine(QPointF(left, y), QPointF(right - head, y)) if out else \
                painter.drawLine(QPointF(left + head, y), QPointF(right, y))
            self._arrow(painter, QPointF(right, y) if out else QPointF(left, y), 0 if out else 180, pen)
        painter.restore()

    def _draw_calls(self, painter, strip, label, light):
        """Blink's strip, as the others: on the header row how the call goes (Calling..., Ringing..., On hold; once
        connected its link's quality: RTT, loss each way), the call button in the knob's place, under it on the names
        row who the call is with in orange (its display name, else its SIP URI; idle, who REDIAL calls); the other
        calls under it, each with its party, its address and a HANG UP of its own."""
        unit = self.unit
        width = strip.width() * 0.9
        left = strip.center().x() - width / 2
        y = self.selector.centre.y() - self._knob * 1.45 - unit * 0.05
        detail_font = self._font(unit * 0.022)
        title_font = self._font(unit * 0.03, weight=QFont.Weight.DemiBold)
        titles, details = QFontMetricsF(title_font), QFontMetricsF(detail_font)
        live = [call for call in self.blink_calls if not call[2]]
        first = (live or self.blink_calls or [None])[0]
        # the header row: how the call goes (Calling..., Ringing..., In call, On hold); IDLE without one
        state = (first[4] or {}).get('state') if first is not None else None
        if first is None and self.auto_redial_at is not None:       # dropped: called again in a moment
            header = f'Redial in {max(0, math.ceil(self.auto_redial_at - time.monotonic()))} s'
        elif first is None:
            header = 'IDLE'
        elif first[2]:
            header = 'On hold'
        elif state:
            header = f'{state[:1].upper()}{state[1:]}\u2026'
        else:       # connected: for how long (Blink's start; an older Blink gives none: since it was seen connected)
            started = (first[4] or {}).get('started')
            if started is None:
                key = (first[4] or {}).get('id') or first[0]
                started = self._connected_since.setdefault(key, time.time())
            seconds = max(0, int(time.time() - started))
            header = f'{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}' if seconds >= 3600 else \
                f'{seconds // 60:02d}:{seconds % 60:02d}'
        painter.setFont(self._fitted(label, header, width))
        painter.setPen(light)
        painter.drawText(QRectF(left, y, width, unit * 0.045), Qt.AlignmentFlag.AlignCenter, header)
        self._hangup_buttons = []
        self._redial_rect = QRectF(-1e6, -1e6, 1, 1)
        if self.blink_calls or self.blink_redial is not None:      # the call button: up REDIAL, down while a call is up
            self._draw_redial(painter, left, y, width, detail_font)
        # under the button, on the names row: who the call is with, in orange as the others name what they play
        # (the display name when there is one, else the SIP URI); idle, who REDIAL calls, in grey
        if first is not None:
            name = str(first[0] or (first[4] or {}).get('address') or '')
        elif self.blink_redial is not None:
            name = str(self.blink_redial.get('label') or '')
        else:
            name = ''
        if name.startswith('sip:'):
            name = name[4:]
        names_row = self.selector.centre.y() + self._knob * 1.4
        name_font = self._font(unit * 0.024, weight=QFont.Weight.Medium)
        painter.setFont(name_font)
        painter.setPen(QColor('#ffb347') if first is not None and not first[2] else QColor('#8a9098'))
        painter.drawText(QRectF(left, names_row, width, unit * 0.04), Qt.AlignmentFlag.AlignCenter,
                         QFontMetricsF(name_font).elidedText(name, Qt.TextElideMode.ElideRight, width))
        y = names_row + unit * 0.04
        # under it, once connected: its link's quality (RTT, loss each way)
        if first is not None and not first[2] and not state and self._quality_parts(first[3]):
            self._draw_quality(painter, QRectF(left, y, width, unit * 0.04), first[3])       # (as RECORDER's REC time)
            y += unit * 0.04
        y += unit * 0.012
        bottom = self.mic_fader.slot.top() - unit * 0.03       # the room above the fader and the meter
        # Blink's microphone not the mixer: what goes into its calls is not this console
        elsewhere = self.blink_mic_elsewhere()
        if elsewhere is not None and y + unit * 0.064 <= bottom:
            painter.setFont(detail_font)
            painter.setPen(QColor('#ff6b5b'))
            for text in ('Blink\'s mic is not the mixer:', elsewhere or 'none'):
                painter.drawText(QRectF(left, y, width, unit * 0.032), Qt.AlignmentFlag.AlignCenter,
                                 QFontMetricsF(detail_font).elidedText(text, Qt.TextElideMode.ElideRight, width))
                y += unit * 0.032
            y += unit * 0.008
        # the other calls (a second one, one on hold): party, address, a HANG UP each
        button = unit * 0.034
        others = [call for call in self.blink_calls if call is not first]
        for index, (title, detail, held, quality, target) in enumerate(others):
            if held:
                detail = f'on hold \u00b7 {detail}' if detail else 'on hold'
            needed = unit * 0.04 + (unit * 0.032 if detail else 0)
            if y + needed > bottom:     # no room for more: how many are left
                painter.setFont(detail_font)
                painter.setPen(QColor('#8a9098'))
                painter.drawText(QRectF(left, y, width, unit * 0.032), Qt.AlignmentFlag.AlignCenter,
                                 f'and {len(others) - index} more')
                break
            painter.setFont(title_font)
            painter.setPen(QColor('#7d746a') if held else QColor('#ffb347'))
            hangup = target is not None and target.get('id') is not None and target.get('pid') is not None
            room = width - 2 * (button + unit * 0.01) if hangup else width
            painter.drawText(QRectF(left + (width - room) / 2, y, room, unit * 0.04), Qt.AlignmentFlag.AlignCenter,
                             titles.elidedText(title, Qt.TextElideMode.ElideRight, room))
            if hangup:
                rect = QRectF(left + width - button, y + (unit * 0.04 - button) / 2, button, button)
                self._draw_hangup(painter, rect)
                self._hangup_buttons.append((rect, target))
            y += unit * 0.04
            if detail:
                painter.setFont(detail_font)
                painter.setPen(QColor('#6a7078') if held else QColor('#aab0b8'))
                painter.drawText(QRectF(left, y, width, unit * 0.032), Qt.AlignmentFlag.AlignCenter,
                                 details.elidedText(detail, Qt.TextElideMode.ElideRight, width))
                y += unit * 0.032
            y += unit * 0.008

    def _draw_hangup(self, painter, rect):
        """HANG UP: a red disc, a white handset across it (as phones draw it)."""
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#ff0000'))
        painter.drawEllipse(rect)
        size = rect.width()
        handset = QRectF(0, 0, size * 0.62, size * 0.2)
        handset.moveCenter(rect.center())
        painter.setBrush(QColor('#ffffff'))
        painter.drawRoundedRect(handset, handset.height() / 2, handset.height() / 2)
        painter.restore()

    def _tick_names(self, painter, knob, width, left_name, right_name, left_color, right_color, reach=1.38):
        """A two-way switch's positions named at its ticks' tips: left_name ending just left of the left tick,
        right_name starting just right of the right one (None: not); both the same size, as small as the narrower
        side needs (a small window). reach: how far from the centre, in radii (the ticks' tips: 1.38)."""
        unit = self.unit
        size = unit * 0.02
        gap = unit * 0.006
        strip_left = knob.centre.x() - width / 2
        rows = []
        for index, name, color in ((0, left_name, left_color), (1, right_name, right_color)):
            if not name:
                continue
            angle = math.radians(knob.position_angle(index) - 90)
            tip = knob.centre + QPointF(math.cos(angle), math.sin(angle)) * knob.radius * reach
            if index == 0:
                room = max(1.0, tip.x() - gap - strip_left)
                row = QRectF(tip.x() - gap - room, tip.y() - unit * 0.02, room, unit * 0.04)
                align = Qt.AlignmentFlag.AlignRight
            else:
                room = max(1.0, strip_left + width - tip.x() - gap)
                row = QRectF(tip.x() + gap, tip.y() - unit * 0.02, room, unit * 0.04)
                align = Qt.AlignmentFlag.AlignLeft
            advance = QFontMetricsF(self._font(size, spacing=6, weight=QFont.Weight.DemiBold)).horizontalAdvance(name)
            size = min(size, size * room / advance) if advance > room else size
            rows.append((name, color, row, align))
        font = self._font(max(5.0, size), spacing=6 if size >= unit * 0.016 else 0, weight=QFont.Weight.DemiBold)
        metrics = QFontMetricsF(font)
        painter.setFont(font)
        for name, color, row, align in rows:
            painter.setPen(color)
            painter.drawText(row, align | Qt.AlignmentFlag.AlignVCenter,
                             metrics.elidedText(name, Qt.TextElideMode.ElideNone, row.width()))

    def _draw_redial(self, painter, left, y, width, font):
        """BLINK's call switch where the other strips have their knob, the same knob: HANG UP (red) at its left
        position, REDIAL at its right; it points right while a call is up, left when there is none."""
        unit = self.unit
        knob = self.call_selector
        knob.radius = self._knob
        knob.centre = QPointF(left + width / 2, self.selector.centre.y())
        # right while a call is up, and while the one REDIAL asked for comes (DIAL_WAIT); back left when it ends or
        # never came (failed)
        if self.blink_calls:
            self._dialing_since = None
        elif self._dialing_since is not None and time.monotonic() - self._dialing_since > DIAL_WAIT:
            self._dialing_since = None
        knob.value = 1 if self.blink_calls or self._dialing_since is not None else 0
        light = QColor('#d6d9de')
        for index in range(2):      # its two ticks, the one it points at longer
            angle = math.radians(knob.position_angle(index) - 90)
            direction = QPointF(math.cos(angle), math.sin(angle))
            current = index == int(knob.value)
            painter.setPen(QPen(light, 1.6 if current else 1.1))
            painter.drawLine(knob.centre + direction * knob.radius * 1.16, knob.centre + direction * knob.radius * (1.38 if current else 1.28))
        # the positions' names beside their ticks: HANG UP left (red; none without a call to hang up), REDIAL right
        busy = bool(self.blink_calls) or self._dialing_since is not None
        self._tick_names(painter, knob, width, 'OFF' if busy else None, 'CALL', QColor('#ff3b30'),
                         light if self.blink_redial is not None and not busy else QColor('#8a9098'))
        # a call connected (not on hold, not still dialing or ringing): the knob glows, as STREAMING's
        if any(not held and not (target or {}).get('state') for title, detail, held, quality, target in self.blink_calls):
            self._draw_glow(painter, knob)
        draw_knob(painter, knob)
        self._redial_rect = QRectF(knob.centre.x() - knob.radius * 2, knob.centre.y() - knob.radius * 2,
                                   knob.radius * 4, knob.radius * 4)

    @staticmethod
    def _quality_parts(quality):
        """RTT and the packet loss each way (\u2193 what we receive, \u2191 what they receive) as shown: green, or pure
        red when high (RTT over 150 ms; any loss shown); left out, a 0 ms RTT (not measured yet) and a loss under
        LOSS_SHOWN_FROM, "No loss" then in its place (green). [] without quality (no line at all)."""
        if not quality:
            return []
        rtt, loss_rx, loss_tx = quality[:3]
        concealed = quality[3] if len(quality) > 3 else 0.0
        def color(value, warn):
            return QColor('#ff0000') if value > warn else QColor('#3ddc84')
        parts = [(f'{rtt:.0f} ms', color(rtt, 150))] if round(rtt) > 0 else []
        if loss_rx >= LOSS_SHOWN_FROM:
            parts.append((f'\u2193{loss_rx:.1f}%', color(loss_rx, 1)))
        if loss_tx >= LOSS_SHOWN_FROM:
            parts.append((f'\u2191{loss_tx:.1f}%', color(loss_tx, 1)))
        if concealed >= LOSS_SHOWN_FROM:        # audio filled in (late or lost packets): the gaps heard, amber
            parts.append((f'gaps {concealed:.1f}%', QColor('#ff0000') if concealed > 3 else QColor('#ffb347')))
        if loss_rx < LOSS_SHOWN_FROM and loss_tx < LOSS_SHOWN_FROM and concealed < LOSS_SHOWN_FROM:
            parts.append(('No loss', QColor('#3ddc84')))       # measured, and nothing lost: said so, next to the RTT
        return parts

    def _draw_quality(self, painter, rect, quality, size=None):
        """The quality line under a live call (_quality_parts)."""
        parts = self._quality_parts(quality)
        if not parts:
            return
        font = self._font(size or self.unit * 0.024, weight=QFont.Weight.Medium if size is None else QFont.Weight.DemiBold)
        metrics = QFontMetricsF(font)
        gap = metrics.horizontalAdvance('  ')
        total = sum(metrics.horizontalAdvance(text) for text, _ in parts) + gap * (len(parts) - 1)
        x = rect.center().x() - total / 2
        painter.setFont(font)
        for text, pen in parts:
            painter.setPen(pen)
            advance = metrics.horizontalAdvance(text)
            painter.drawText(QRectF(x, rect.top(), advance + 2, rect.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            x += advance + gap

    def _draw_selector(self, painter, selector, text, width, label, light, name_centre=None, name_width=None, lit=None,
                       icons=None, named=True, lift=0.0):
        """A rotary selector: its ticks, its label above, the name of its position under it (centred on name_centre,
        name_width wide: by default under the knob, width wide)."""
        unit, centre, radius = self.unit, selector.centre, selector.radius
        for index in range(len(selector.names)):
            angle = math.radians(selector.position_angle(index) - 90)
            direction = QPointF(math.cos(angle), math.sin(angle))
            current = index == int(selector.value)
            painter.setPen(QPen(light, 1.6 if current else 1.1))
            painter.drawLine(centre + direction * radius * 1.16, centre + direction * radius * (1.38 if current else 1.28))
            glyph = icons.get(selector.names[index]) if icons else None
            if glyph and glyph.startswith('@'):     # no icon for it: an avatar, as a contact's (a letter; Off)
                size = radius * (0.68 if current else 0.58)       # (avatars: small, about the ticks' length)
                where = centre + direction * radius * 1.7
                painter.save()
                if not current:
                    painter.setOpacity(painter.opacity() * 0.6)
                painter.setPen(Qt.PenStyle.NoPen)        # (no ring: as a contact's avatar)
                painter.setBrush(QColor('#9aa0a8') if current else QColor('#5a5f66'))      # grey, the console's
                painter.drawEllipse(where, size / 2, size / 2)
                painter.setFont(self._font(size * (0.52 if len(glyph) == 2 else 0.36), weight=QFont.Weight.DemiBold))
                painter.setPen(QColor('#16181b') if current else QColor('#d6d9de'))
                painter.drawText(QRectF(where.x() - size / 2, where.y() - size / 2, size, size), Qt.AlignmentFlag.AlignCenter, glyph[1:])
                painter.restore()
            elif glyph:     # who is there, without hovering: a small line icon past its tick, the chosen one lit
                size = radius * (0.58 if current else 0.5)
                where = centre + direction * radius * 1.68
                painter.save()
                painter.translate(where.x() - size / 2, where.y() - size / 2)
                painter.scale(size / 24, size / 24)
                painter.setPen(QPen(QColor('#ffffff') if current else QColor('#8a9098'), 2.0 * 24 / max(size, 1) * 0.6,
                                    Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(line_path(glyph))
                painter.restore()
        if not icons:       # (with icons around it, its label would sit on them: the name under it says enough)
            painter.setFont(label)
            painter.drawText(QRectF(centre.x() - width / 2, centre.y() - radius * 1.45 - unit * (0.05 + lift), width, unit * 0.045),
                             Qt.AlignmentFlag.AlignCenter, text)       # (lift: further up, in units)
        font = self._font(unit * 0.024, weight=QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor('#ffb347') if (selector.current() if lit is None else lit) else QColor('#8a9098'))
        middle = centre.x() if name_centre is None else name_centre
        wide = width if name_width is None else name_width
        name = QFontMetricsF(font).elidedText(selector.title(selector.current()), Qt.TextElideMode.ElideRight, wide)
        if named:       # (not: its ticks' names say it already, OFF / ON)
            painter.drawText(QRectF(middle - wide / 2, centre.y() + radius * 1.4, wide, unit * 0.04), Qt.AlignmentFlag.AlignCenter, name)
        draw_knob(painter, selector)

    def _draw_button(self, painter, rect, on, enabled, color):
        size = rect.width()
        painter.save()
        if not enabled:
            painter.setOpacity(0.45)
        painter.setPen(QPen(QColor('#0b0c0e'), 0.8))
        painter.setBrush(QColor('#121316'))
        painter.drawEllipse(rect.adjusted(-size * 0.1, -size * 0.1, size * 0.1, size * 0.1))
        button = rect.translated(0, size * 0.03) if on else rect
        face = QRadialGradient(button.center() - QPointF(0, size * 0.25), size * 0.75)
        face.setColorAt(0.0, QColor('#3a3d43' if on else '#555961'))
        face.setColorAt(1.0, QColor('#0f1012'))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(face)
        painter.drawEllipse(button)
        lamp, radius = button.center(), size * 0.17
        if on:
            glow = QRadialGradient(lamp, radius * 2.6)
            glow.setColorAt(0.0, color.lighter(140))
            glow.setColorAt(0.35, color)
            glow.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
            painter.setBrush(glow)
            painter.drawEllipse(lamp, radius * 2.6, radius * 2.6)
        painter.setPen(QPen(QColor('#0a0a0a'), 0.8))
        painter.setBrush(color if on else color.darker(380))
        painter.drawEllipse(lamp, radius, radius)
        painter.restore()

    def _draw_scale(self, painter, fader):
        """The fader's ticks left of its slot, 10 steps, the ends longer."""
        slot = fader.slot
        travel = slot.height() - fader.cap_height
        painter.setPen(QPen(QColor('#9aa0a8'), 1))
        for index in range(11):
            y = slot.top() + fader.cap_height / 2 + travel * index / 10
            long = index in (0, 5, 10)
            painter.drawLine(QPointF(slot.left() - fader.cap.width() * 0.62, y),
                             QPointF(slot.left() - fader.cap.width() * (0.95 if long else 0.8), y))

    def _draw_fader(self, painter, fader):
        slot = fader.slot
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#0d0e10'))
        painter.drawRoundedRect(slot, slot.width() / 2, slot.width() / 2)
        cap = fader.cap
        painter.setBrush(QColor(0, 0, 0, 100))
        painter.drawRoundedRect(cap.translated(0, cap.height() * 0.12), cap.height() * 0.15, cap.height() * 0.15)
        body = QLinearGradient(0, cap.top(), 0, cap.bottom())
        body.setColorAt(0.0, QColor('#5a5e66'))
        body.setColorAt(0.45, QColor('#2a2c31'))
        body.setColorAt(0.55, QColor('#1a1b1f'))
        body.setColorAt(1.0, QColor('#3a3d43'))
        painter.setBrush(body)
        painter.drawRoundedRect(cap, cap.height() * 0.15, cap.height() * 0.15)
        painter.setPen(QPen(QColor('#f2f2f2'), max(1.5, cap.height() * 0.08)))       # its line
        painter.drawLine(QPointF(cap.left() + cap.width() * 0.1, cap.center().y()), QPointF(cap.right() - cap.width() * 0.1, cap.center().y()))

    def _participant_area(self, strip, meter):
        """Where the room's people's meters go: from the BLINK strip's meter to the strip's right edge, down to the row of
        the MUTEs (BLINK's own is under its fader): their MUTEs on that row."""
        mute = self._mutes.get('BLINK')
        bottom = max(meter.rect.bottom(), mute.bottom()) if mute is not None else meter.rect.bottom()
        return QRectF(meter.rect.left() - self.unit * 0.01, meter.rect.top(),
                      strip.right() - self.unit * 0.015 - (meter.rect.left() - self.unit * 0.01), bottom - meter.rect.top())

    def _participant_layout(self, strip, meter):
        """[(participant, column rect)] that fit, and how many more do not."""
        area = self._participant_area(strip, meter)
        unit = self.unit
        widths = [unit * (0.014 if p.get('channels') != 2 else 0.026) for p in self.blink_participants]
        gap = unit * 0.012
        placed, x = [], area.left()
        for participant, width in zip(self.blink_participants, widths):
            column = max(width, unit * 0.03)        # room for the initial and MUTE under the meter
            if x + column > area.right() + 0.5:
                break
            placed.append((participant, QRectF(x, area.top(), column, area.height())))
            x += column + gap
        return placed, len(self.blink_participants) - len(placed)

    def _draw_participant_meters(self, painter, strip, meter):
        """Each participant of the room: an LED meter (a column mono, two stereo), the name's initial under it, a
        MUTE (red while muted) asking the room to mute them; the name and level on hovering. More than fit: +N."""
        unit = self.unit
        placed, more = self._participant_layout(strip, meter)
        self._participant_mutes, self._participant_columns = [], []
        button = unit * 0.026
        letter_height = unit * 0.026
        painter.save()
        for participant, column in placed:
            levels = self._participant_meters.get(participant['key']) or _ParticipantMeter(1)
            channels = len(levels.levels)
            bar_width = unit * (0.014 if channels == 1 else 0.026)
            bar = QRectF(column.center().x() - bar_width / 2, column.top(), bar_width,
                         column.height() - letter_height - button - unit * 0.012)
            saved, levels.rect = levels.rect, bar
            if participant['muted']:
                painter.setOpacity(0.45)
            self._draw_meter(painter, levels)
            painter.setOpacity(1.0)
            levels.rect = saved
            initial = next((char for char in participant['name'] if char.isalnum()), '?').upper()
            painter.setFont(self._font(unit * 0.02, weight=QFont.Weight.DemiBold))
            painter.setPen(QColor('#6a7078') if participant['muted'] else QColor('#d6d9de'))
            painter.drawText(QRectF(column.left() - unit * 0.01, bar.bottom() + unit * 0.004, column.width() + unit * 0.02, letter_height),
                             Qt.AlignmentFlag.AlignCenter, initial)
            mute = QRectF(column.center().x() - button / 2, column.bottom() - button, button, button)
            painter.setPen(QPen(QColor('#ff3b30') if participant['muted'] else QColor('#5a5f66'), 1))
            painter.setBrush(QColor(255, 59, 48, 70) if participant['muted'] else QColor('#16181b'))
            painter.drawEllipse(mute)
            from cutemusic.icons import line_path
            painter.save()
            painter.translate(mute.left() + button * 0.15, mute.top() + button * 0.15)
            painter.scale(button * 0.7 / 24, button * 0.7 / 24)
            painter.setPen(QPen(QColor('#ff3b30') if participant['muted'] else QColor('#aab0b8'), 2.0, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(line_path('muted' if participant['muted'] else 'speaker'))
            painter.restore()
            self._participant_mutes.append((mute, participant))
            self._participant_columns.append((column, participant))
        if more:
            area = self._participant_area(strip, meter)
            painter.setFont(self._font(unit * 0.02, weight=QFont.Weight.DemiBold))
            painter.setPen(QColor('#8a9098'))
            painter.drawText(QRectF(area.right() - unit * 0.05, area.top() - unit * 0.03, unit * 0.05, unit * 0.025),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f'+{more}')
        painter.restore()

    # Analog needle meters (the menu's Analog needle meters): the amplifier's VU meters (vufaces.AnalogFaces), its
    # face, scale (-20..+3 VU), needle, PEAK lamp and spring; one face per channel, left over right. Calibrated as the
    # amplifier's, by its own constants (amp.AmpView): 0 VU trimmed to each strip's loudness, HEADROOM above it, up in
    # TRIM_UP s, down in TRIM_DOWN s, within TRIM_RANGE dBFS, left alone below TRIM_FLOOR

    NEEDLE_REFERENCE = AmpView.REFERENCE
    NEEDLE_TRIM_RANGE = AmpView.TRIM_RANGE
    NEEDLE_TRIM_UP, NEEDLE_TRIM_DOWN, NEEDLE_HEADROOM = AmpView.TRIM_UP, AmpView.TRIM_DOWN, AmpView.HEADROOM
    NEEDLE_TRIM_FLOOR = AmpView.TRIM_FLOOR
    NEEDLE_SCALE = 0.8          # of the room right of the fader: the dials a size smaller than that
    NEEDLE_SPRING, NEEDLE_DAMPING = AmpView.SPRING, AmpView.DAMPING

    def _needle_area(self, meter, strip):
        """Where a strip's dials go: from right of where the fader's cap was with the LED meters to the strip's right edge, each channel's face as
        the amplifier's (FACE_ASPECT), stacked; no taller than the meter."""
        fader = next((fader for strip_, fader, meter_, name in self._visible if meter_ is meter), None)
        if fader is not None:     # where they were with the fader at its LED place: the fader moved left of them, the gap wider
            left = strip.center().x() - strip.width() * 0.12 + fader.cap.width() / 2 + self.unit * 0.012
        else:
            left = meter.rect.left() - self.unit * 0.006
        width = max(meter.rect.width(), strip.right() - self.unit * 0.012 - left) * self.NEEDLE_SCALE
        channels = len(meter.levels)
        height = min(meter.rect.height(), width / self.FACE_ASPECT * channels * 1.03 + (width / self.FACE_ASPECT * 0.1 if channels == 2 else 0))     # (the space between the two)
        middle = fader.slot.center().y() if fader is not None else meter.rect.center().y()     # centred on the fader's slot
        return QRectF(left, middle - height / 2, width, height)

    def _needle_windows(self, area, channels):
        """One meter window per channel, each its own bezel and glass, left above right with a space between."""
        if channels != 2:
            return [area]
        gap = area.height() * 0.08
        height = (area.height() - gap) / 2
        return [QRectF(area.left(), area.top() + index * (height + gap), area.width(), height) for index in range(2)]

    def _draw_needles(self, painter, meter, strip):
        """The amplifier's meter, one window per channel (LEFT above RIGHT, apart; one for a mono strip): the bezel,
        the face lit from behind, the scale, the needle, the PEAK lamp, the glass."""
        channels = len(meter.levels)
        needles = getattr(meter, 'needles', None) or [0.0] * channels
        lit = getattr(meter, 'peak_lit', None) or [0.0] * channels
        now = time.monotonic()
        for channel, window in enumerate(self._needle_windows(self._needle_area(meter, strip), channels)):
            gap = window.height() * 0.03
            face = window.adjusted(gap, gap, -gap, -gap)
            label = ('LEFT' if channel == 0 else 'RIGHT') if channels == 2 else ''
            dial, origin = self._dial_pixmap(window, face, label)
            painter.drawPixmap(origin, dial)        # what does not move: drawn once a size
            self._draw_needle(painter, face, needles[channel], now - lit[channel] < 0.35)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._glare(window))
            painter.drawRoundedRect(window, 4, 4)

    def _dial_pixmap(self, window, face, label):
        """A dial's bezel, lit face and scale, drawn once for its size and label (the needles over it each frame)."""
        from PyQt6.QtGui import QPixmap
        ratio = self.devicePixelRatioF()
        margin = 3
        key = (round(window.width(), 1), round(window.height(), 1), label, ratio)
        cache = self.__dict__.setdefault('_dial_cache', {})
        pixmap = cache.get(key)
        origin = QPointF(window.left() - margin, window.top() - margin)
        if pixmap is None:
            if len(cache) > 32:
                cache.clear()
            pixmap = QPixmap(round((window.width() + 2 * margin) * ratio), round((window.height() + 2 * margin) * ratio))
            pixmap.setDevicePixelRatio(ratio)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.translate(-origin)
            painter.setPen(QPen(QColor('#7d8188'), 1.2))
            painter.setBrush(QColor('#14161a'))
            painter.drawRoundedRect(window.adjusted(-2, -2, 2, 2), 5, 5)
            self._draw_backlight(painter, self._face_rects(face))       # (its two bulbs under the one face)
            self._draw_face(painter, face, label)
            painter.end()
            cache[key] = pixmap
        return pixmap, origin

    @staticmethod
    def _glare(window):
        glare = QLinearGradient(window.topLeft(), window.bottomLeft())
        glare.setColorAt(0.0, QColor(255, 255, 255, 46))
        glare.setColorAt(0.45, QColor(255, 255, 255, 8))
        glare.setColorAt(0.46, QColor(255, 255, 255, 0))
        return glare

    def _move_needles(self, meter, elapsed, now):
        """A meter's needles one step towards its levels, on the amplifier's spring; whether they moved."""
        channels = len(meter.levels)
        if getattr(meter, 'needles', None) is None or len(meter.needles) != channels:
            meter.needles, meter.speeds, meter.peak_lit = [0.0] * channels, [0.0] * channels, [0.0] * channels
            meter.rms = [DB_LOW] * channels
            meter.reference = getattr(meter, 'reference', self.NEEDLE_REFERENCE)
        # the trim (as the amplifier's): the loudness smoothed over 0.3 s, 0 VU following it
        follow = 1 - math.exp(-elapsed / 0.3)
        for channel in range(channels):
            meter.rms[channel] += (meter.target[channel] - meter.rms[channel]) * follow
        loudness = max(meter.rms)
        if loudness > self.NEEDLE_TRIM_FLOOR:
            low, high = self.NEEDLE_TRIM_RANGE
            target = min(high, max(low, loudness + self.NEEDLE_HEADROOM))
            speed = self.NEEDLE_TRIM_UP if target > meter.reference else self.NEEDLE_TRIM_DOWN
            meter.reference += (target - meter.reference) * min(1.0, elapsed / speed)
        steps = max(1, math.ceil(min(elapsed, 0.1) / 0.004))       # small steps: the spring stays stable
        dt = min(elapsed, 0.1) / steps
        moved = False
        rest = self._needle_ratio(self.VU_MIN - 2)
        for channel in range(channels):
            level = meter.target[channel]
            goal = self._needle_ratio(level - meter.reference) if level > DB_LOW else rest
            position, speed = meter.needles[channel], meter.speeds[channel]
            for _ in range(steps):
                speed += (self.NEEDLE_SPRING ** 2 * (goal - position) - 2 * self.NEEDLE_DAMPING * self.NEEDLE_SPRING * speed) * dt
                position += speed * dt
            drawn = getattr(meter, 'drawn', None)
            if drawn is None or len(drawn) != channels:
                drawn = meter.drawn = [position] * channels
            if abs(position - drawn[channel]) > 0.004:       # about a pixel at the needle's tip: worth drawing again
                moved = True
                drawn[channel] = position
            meter.needles[channel], meter.speeds[channel] = position, speed
            if position >= self._needle_ratio(self.PEAK_VU):
                meter.peak_lit[channel] = now
        lamps = [now - lit < 0.35 for lit in meter.peak_lit]      # the PEAK lamp lit: drawn when it changes
        if lamps != getattr(meter, 'lamps', None):
            meter.lamps = lamps
            moved = True
        return moved

    def _draw_meter(self, painter, meter, strip=None):
        if self.needle_meters and strip is not None:
            self._draw_needles(painter, meter, strip)
            return
        rect = meter.rect
        columns = len(meter.levels)
        width = (rect.width() - self.unit * 0.006 * (columns - 1)) / columns
        step = rect.height() / SEGMENTS
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#0d0e10'))
        painter.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), 3, 3)
        for column, (level, (peak, since)) in enumerate(zip(meter.levels, meter.peaks)):
            x = rect.left() + column * (width + self.unit * 0.006)
            lit = (level - DB_LOW) / (DB_HIGH - DB_LOW) * SEGMENTS
            held = int((peak - DB_LOW) / (DB_HIGH - DB_LOW) * SEGMENTS)
            for segment in range(SEGMENTS):
                db = DB_LOW + (segment + 1) / SEGMENTS * (DB_HIGH - DB_LOW)
                color = QColor('#ff3b30') if db > -3 else QColor('#ffcc00') if db > -12 else QColor('#34c759')
                on = segment < lit or (segment == held - 1 and peak > DB_LOW + 1)
                if not on:
                    color = color.darker(520)
                painter.setBrush(color)
                y = rect.bottom() - (segment + 1) * step
                painter.drawRect(QRectF(x, y + step * 0.15, width, step * 0.7))

    # The meters' falling

    def showEvent(self, event):
        self.last = time.monotonic()
        self.timer.start()
        self.inputs_wanted.emit()

    def hideEvent(self, event):
        self.timer.stop()

    def _tick(self):
        now = time.monotonic()
        elapsed, self.last = now - self.last, now
        moved = False
        for meter in self.meters + [slot.meter for slot in self.slots]:
            leds = meter.tick(elapsed, now)
            if self.needle_meters:      # the needles drawn: only their moving asks for a redraw, not the LEDs' levels
                moved = self._move_needles(meter, elapsed, now) or moved
            else:
                moved = leds or moved
        people = False
        for meter in self._participant_meters.values():
            people = meter.tick(elapsed, now) or people
        if people:      # their meters: the BLINK strip's
            blink = next((strip for strip, fader, meter, name in self._visible if name == 'BLINK'), None)
            if blink is not None:
                self.update(blink.toAlignedRect())
        # the reels, as the meters' needles: their speed eased towards where it goes (spinning up at REC, coasting down
        # after), the angle following it; only the tape's band drawn again each step (cheap: every frame in time)
        target = REEL_SPEED if self.recording else 0.0
        self._reel_speed += (target - self._reel_speed) * min(1.0, elapsed * 2.5)
        if self._reel_speed < 0.5 and not self.recording:
            self._reel_speed = 0.0
        if self._reel_speed:
            self._reel_angle = (self._reel_angle - self._reel_speed * elapsed) % 360.0     # counter-clockwise
            if self._strips:
                self.update(self._tape_view.adjusted(-2, -2, 2, 2).toAlignedRect())
        if self.recording and int(now - self.record_started) != self._bar_second:        # the time: once a second
            self._bar_second = int(now - self.record_started)
            self.update(self._bar.adjusted(-2, -2, 2, 2).toAlignedRect() if self._strips else self.rect())
        if moved:
            for strip, fader, meter, name in self._visible:
                area = self._needle_area(meter, strip) if self.needle_meters else meter.rect
                self.update(area.adjusted(-4, -4, 4, 4).toAlignedRect())

    # The mouse

    def _fader_at(self, point):
        return next((fader for strip, fader, meter, name in self._visible if fader.hit(point)), None)

    def _move_fader(self, fader, value):
        if not fader.set(value):
            return
        # logged once it rests (a drag: one line)
        name = self.strip_title(next((name for strip, f, meter, name in self._visible if f is fader), '?'))
        if not hasattr(self, '_fader_log'):
            self._fader_log = QTimer(self, singleShot=True, interval=400)
            self._fader_log.timeout.connect(lambda: log.info(f'[mixer] {self._fader_logged[0]} fader {self._fader_logged[1]:.0%}'))
        self._fader_logged = (name, fader.value)
        self._fader_log.start()
        slot = next((slot for slot in self.slots if slot.fader is fader), None)
        if slot is not None:
            self._save_slots()
            self.slot_level_changed.emit(slot.key, fader.value)
        elif fader is self.mic_fader:
            util.settings().setValue('mixer/mic', fader.value)
            self.mic_level_changed.emit(fader.value)
        elif fader is self.call_fader:
            util.settings().setValue('mixer/call', fader.value)
            self.call_level_changed.emit(fader.value)
        elif fader is self.music_fader:
            self.apps_level_changed.emit(round(fader.value * 100))
        else:
            self.music_changed.emit(round(fader.value * 100))
        self.update()

    def _turn_echo(self, value):
        if self.echo.set(value):
            util.settings().setValue('mixer/echo', self.echo.value)
            self.echo_changed.emit(self.echo.value)
            self.update()

    def _choose(self, selector, timer, index):
        index = max(0, min(len(selector.names) - 1, index))
        if index != int(selector.value):
            selector.value = index
            timer.start()       # chosen once it rests
            if selector is self.source_selector:
                self._layout()      # VOCAL CUT or THRU
            self.update()

    def _selector_at(self, point):
        return next((entry for entry in self._selectors
                     if entry[0].distance(point) <= entry[0].radius * getattr(entry[0], 'REACH', 1.5)), None)

    def _mute_at(self, point):
        return next((name for name, rect in self._mutes.items() if rect.adjusted(-5, -5, 5, 5).contains(point)), None)

    def _clicked(self, event, text):
        """A click's tip, also in the log ([ui]): what was pressed, to read next to what it did."""
        log.info(f'[ui] {text}')
        self._tip(event, text)

    def _view_menu(self, position):
        """The menu button's menu: the recorder shown, auto-redial."""
        menu = QMenu(self)
        action = menu.addAction('Audio recorder', lambda: self.set_recorder_shown(not self.recorder_shown))
        action.setCheckable(True)
        action.setChecked(self.recorder_shown)
        action = menu.addAction('Analog needle meters', lambda: self.set_needle_meters(not self.needle_meters))
        action.setCheckable(True)
        action.setChecked(self.needle_meters)
        menu.addSeparator()
        action = menu.addAction('Auto-record when on air', lambda: self.set_auto_record(not self.auto_record))
        action.setCheckable(True)
        action.setChecked(self.auto_record)
        action = menu.addAction('Hide Blink if not using AG Podcast Mixer', lambda: self.set_hide_blink_astray(not self.hide_blink_astray))
        action.setCheckable(True)
        action.setChecked(self.hide_blink_astray)
        action = menu.addAction('Auto-redial if call fails', lambda: self.set_auto_redial(not self.auto_redial))
        action.setCheckable(True)
        action.setChecked(self.auto_redial)
        menu.exec(position)

    def set_recorder_shown(self, shown):
        """RECORDER's bar shown or hidden (kept: mixer/recorder_shown); the console as tall as it then needs."""
        if shown == self.recorder_shown:
            return
        self.recorder_shown = shown
        util.settings().setValue('mixer/recorder_shown', shown)
        log.info(f'[ui] Audio Recorder {"shown" if shown else "hidden"}')
        self._layout()
        self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position()
        if self._view_button.adjusted(-6, -6, 6, 6).contains(point):
            self._view_menu(self.mapToGlobal(QPointF(self._view_button.right(), self._view_button.bottom()).toPoint()))
            return
        if self._power_rect.adjusted(-5, -5, self.unit * 0.13, 5).contains(point):
            self.set_power(not self.powered)
            self.power_changed.emit(self.powered)
            self._clicked(event, 'Mixer on' if self.powered else 'Mixer off: no AG Podcast Mixer device for Blink, no mic')
            return
        if not self.powered:
            return
        if self._label_rect.adjusted(-4, -6, 4, 6).contains(point):
            log.info('[ui] RECORDER title clicked')
            self.edit_record_title()
            return
        if self._mark_rect.adjusted(-6, -6, 6, 6).contains(point):
            if self.recording:
                self.marker_requested.emit()
                self._clicked(event, f'Marker {self.markers + 1} at {self._record_time()}')
            else:
                self._clicked(event, 'MARK: a marker in the recording, while it records')
            return
        if self._tape_button.adjusted(-6, -6, 6, 6).contains(point):
            log.info('[ui] RECORDER tape clicked')
            self.tape_wanted.emit()
            return
        if self._record_rect.adjusted(-6, -6, 6, 6).contains(point) or \
                (self.recording and self._tape_view.contains(point)):
            if self.record_available:
                self.record_toggled.emit(not self.recording)
                self._clicked(event, 'Recording everything the strips play' if not self.recording else 'Recording stopped')
            else:
                self._clicked(event, 'No audio encoder (flacenc, lamemp3enc, wavenc): cannot record')
            return
        if self._add_rect.adjusted(-4, -4, 4, 4).contains(point):
            slot = self.add_slot()
            if slot is not None:
                self._clicked(event, f'{self.strip_title(slot.key)}: choose its input')
            return
        key = next((key for key, rect in self._remove_rects.items() if rect.adjusted(-4, -4, 4, 4).contains(point)), None)
        if key is not None:
            title = self.strip_title(key)
            self.remove_slot(key)
            self._clicked(event, f'{title} removed')
            return
        for rect, target in self._hangup_buttons:
            if rect.adjusted(-4, -4, 4, 4).contains(point):
                self.hangup_requested.emit(target)
                self._clicked(event, 'Hanging up in Blink')
                return
        for rect, participant in self._participant_mutes:
            if rect.adjusted(-3, -3, 3, 3).contains(point):
                muted = not participant['muted']
                self.participant_mute_requested.emit(participant['target'], muted)
                self._clicked(event, f'{participant["name"] or "Them"}: {"muted" if muted else "unmuted"} in the room')
                return
        knob = self.call_selector
        if (self.blink_calls or self.blink_redial is not None) and self._redial_rect.contains(point) \
                and knob.distance(point) <= knob.radius * knob.REACH:
            # turned: to the position clicked around it, or (on its cap) to the other one
            if knob.distance(point) > knob.radius * 0.45:
                index = knob.position_at(point)
            else:
                index = 0 if self.blink_calls else 1
            if index == 0 and self.blink_calls:         # HANG UP: the call talked to (else the first)
                calls = [call for call in self.blink_calls if call[4] and call[4].get('id') is not None]
                live = [call for call in calls if not call[2]]
                target = (live or calls or [None])[0]
                if target is not None:
                    knob.value = 0
                    self.hangup_requested.emit(target[4])
                    self._clicked(event, 'Hanging up in Blink')
            elif index == 1 and not self.blink_calls and self.blink_redial is not None and self._dialing_since is None:
                self._dialing_since = time.monotonic()      # pointing at REDIAL till the call shows
                QTimer.singleShot(int(DIAL_WAIT * 1000) + 100, self.update)     # (back to HANG UP if it never does)
                self.redial_requested.emit(self.blink_redial)
                self._clicked(event, 'Calling in Blink')
            self.update()
            return
        if self._karaoke_rect.adjusted(-5, -5, self.unit * 0.15, 5).contains(point):
            if self.blink_calls:
                self._clicked(event, 'Karaoke is off during a Blink call')
                return
            self.set_karaoke(not self.karaoke)
            log.info(f'[ui] KARAOKE {"on" if self.karaoke else "off"}')
            self.karaoke_changed.emit(self.karaoke)
            return
        if self._monitor.adjusted(-5, -5, 5, 5).contains(point):
            self.monitor = not self.monitor
            self.monitor_changed.emit(self.monitor)
            self._clicked(event, 'Your mic on the amplifier too (the music\'s output)' if self.monitor else 'Your mic only to Blink (not on the amplifier)')
            self.update()
            return
        channel = next((index for index, rect in enumerate(self._channel_mute_rects.values())
                        if rect.adjusted(-4, -4, 4, 4).contains(point)), None)
        if channel is not None:
            self.set_channel_mutes(*[not on if index == channel else on for index, on in enumerate(self.channel_mutes)])
            self.channel_mutes_changed.emit(*self.channel_mutes)
            if self.channel_mutes[channel]:
                self._silence_meter(self.library_meter, channel)
            self._clicked(event, f'Local Library: {"left" if channel == 0 else "right"} channel '
                                 f'{"muted" if self.channel_mutes[channel] else "on"}')
            return
        name = self._mute_at(point)
        if name is not None:
            self.muted[name] = not self.muted[name]
            self.mute_changed.emit(name, self.muted[name])
            if self.muted[name]:        # its meter empty at once, not falling there: MUTE is seen to act
                meter = next((meter for strip, fader, meter, key in self._visible if key == name), None)
                if meter is not None:
                    self._silence_meter(meter)
            if name.startswith('MIC#'):
                self._save_slots()
            else:
                self._save_muted()
            title = self.strip_title(name)
            title = title if not title.isupper() else title.title()
            self._clicked(event, f'{title} muted' if self.muted[name] else f'{title} on')
            self.update()
            return
        if self._cut.adjusted(-4, -4, 4, 4).contains(point) and self.source():
            self.app_output = not self.app_output
            util.settings().setValue('mixer/app_output', self.app_output)
            self.app_output_changed.emit(self.app_output)
            self._clicked(event, self._app_output_tip())
            self.update()
            return
        if (self._library_share.adjusted(-4, -4, 4, 4).contains(point) or self._share.adjusted(-4, -4, 4, 4).contains(point)) \
                and not self.share_available:
            log.info('[ui] TO CALLS clicked, not available (no device for Blink)')
            return
        if self._library_share.adjusted(-4, -4, 4, 4).contains(point) and self.share_available:
            self.library_sharing = not self.library_sharing
            util.settings().setValue('mixer/library_to_calls', self.library_sharing)
            self.library_share_changed.emit(self.library_sharing)
            self._clicked(event, 'The Local Library in Blink\'s call too' if self.library_sharing else 'The Local Library heard here only')
            self.update()
            return
        if self._vocal.adjusted(-4, -4, 4, 4).contains(point) and self.vocal_cut_available:
            self.vocal_cut = not self.vocal_cut
            log.info(f'[ui] VOCAL CUT {"on" if self.vocal_cut else "off"}')
            self.vocal_cut_changed.emit(self.vocal_cut)
            self.update()
            return
        if self._share.adjusted(-4, -4, 4, 4).contains(point) and self.share_available:
            self.sharing = not self.sharing
            log.info(f'[ui] AUDIO APPS TO CALLS {"on" if self.sharing else "off"}')
            util.settings().setValue('mixer/to_calls', self.sharing)
            self.share_changed.emit(self.sharing)       # the window may turn it back off (set_share): it did not start
            self.update()
            return
        entry = self._selector_at(point)
        if entry is not None and entry[0] is self.streaming_selector and not self.streaming_available:
            self._clicked(event, ('Streaming needs the AG Podcast Mixer driver (Play menu: install it)' if sys.platform == 'darwin' else 'Streaming needs AG CuteMusic\'s PipeWire devices: log out and in again'))
            return
        if entry is not None:
            selector, timer, what, verb = entry
            if selector.distance(point) > selector.radius * 0.45:
                self._choose(selector, timer, selector.position_at(point))
            else:
                self._choose(selector, timer, (int(selector.value) + 1) % len(selector.names))
            self._clicked(event, f'{what}: {selector.title(selector.current())}')
            return
        if self.echo.distance(point) <= self.echo.radius * 1.6:
            log.info('[ui] ECHO knob clicked')
            self._drag = (self.echo, point, self.echo.value)
            if self.echo.distance(point) > self.echo.radius * 0.45:
                self._turn_echo(self.echo.ratio_at(point, self.echo.ratio()))
            self._clicked(event, f'Echo {self.echo.value * 100:.0f}%')
            return
        fader = self._fader_at(point)
        if fader is not None:
            # a click: to the tick nearest it (_draw_scale's, 10 steps): on the slot at once, on the cap when let go
            # without moving; dragged, it follows the mouse from where the cap is (a hand's tremor not undoing the jump)
            if not fader.cap.contains(point):
                self._move_fader(fader, round(fader.value_at(point.y() - fader.cap_height / 2) * 10) / 10)
            log.info(f'[ui] fader clicked at {fader.value:.0%}')
            offset = point.y() - fader.cap.center().y()
            self._drag = (fader, offset, point)
            return
        for strip, key in zip(self._strips, getattr(self, '_strip_keys', [])):     # the strip itself: its tile
            if strip.contains(point):
                log.info(f'[ui] {self.strip_title(key)} strip clicked')
                self.strip_clicked.emit(key)
                return

    def mouseMoveEvent(self, event):
        point = event.position()
        if self._drag is None and (not self.powered or self._power_rect.adjusted(-5, -5, self.unit * 0.13, 5).contains(point)
                                   or self._karaoke_rect.adjusted(-5, -5, self.unit * 0.15, 5).contains(point)):
            on_switch = self._power_rect.adjusted(-5, -5, self.unit * 0.13, 5).contains(point) or \
                (self.powered and self._karaoke_rect.adjusted(-5, -5, self.unit * 0.15, 5).contains(point))
            self.setCursor(Qt.CursorShape.PointingHandCursor if on_switch else Qt.CursorShape.ArrowCursor)
            return
        if self._drag is None:
            entry = self._selector_at(point)
            on_selector = entry is not None
            on_sign = self._add_rect.adjusted(-4, -4, 4, 4).contains(point) or \
                any(rect.adjusted(-4, -4, 4, 4).contains(point) for rect in self._remove_rects.values())
            over = on_sign or self._label_rect.adjusted(-4, -6, 4, 6).contains(point) or self._record_rect.adjusted(-6, -6, 6, 6).contains(point) or self._tape_button.adjusted(-6, -6, 6, 6).contains(point) or \
                (self.recording and self._mark_rect.adjusted(-6, -6, 6, 6).contains(point)) or \
                self._view_button.adjusted(-6, -6, 6, 6).contains(point) or self._cut.adjusted(-4, -4, 4, 4).contains(point) or self._share.adjusted(-4, -4, 4, 4).contains(point) or \
                self._vocal.adjusted(-4, -4, 4, 4).contains(point) or self._library_share.adjusted(-4, -4, 4, 4).contains(point) or \
                any(rect.adjusted(-4, -4, 4, 4).contains(point) for rect, target in self._hangup_buttons) or \
                any(rect.adjusted(-3, -3, 3, 3).contains(point) for rect, participant in self._participant_mutes) or \
                ((self.blink_calls or self.blink_redial is not None) and self._redial_rect.adjusted(-4, -4, 4, 4).contains(point)) or \
                self._mute_at(point) is not None or self._monitor.adjusted(-5, -5, 5, 5).contains(point) or \
                any(rect.adjusted(-4, -4, 4, 4).contains(point) for rect in self._channel_mute_rects.values()) or \
                self._fader_at(point) is not None or \
                on_selector or self.echo.distance(point) <= self.echo.radius * 1.6
            self.setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.ArrowCursor)
            if on_selector:     # around it: the position's name; on its cap: the one chosen
                selector, timer, what, verb = entry
                if selector.distance(point) > selector.radius * 0.45:
                    name = selector.names[selector.position_at(point)]
                    text = selector.title(name)
                else:
                    text = f'{what}: {selector.title(selector.current())} (click: the next one)'
                self._tip(event, text)
            elif any(column.contains(point) for column, participant in self._participant_columns):
                participant = next(participant for column, participant in self._participant_columns if column.contains(point))
                level = participant.get('level')
                self._tip(event, f'{participant["name"] or "?"}: ' + ('muted (click its MUTE: unmuted)' if participant['muted'] else
                          (f'{level:.0f} dBFS' if level is not None else 'silent') + ' (MUTE: muted in the room)'))
            elif self.blink_mic_elsewhere() is not None and self._strip_at(point) == 'BLINK':
                self._tip(event, self._blink_astray_tip())
            elif self.on_air() and self._on_air_rect.adjusted(-4, -4, 4, 4).contains(point):
                self._tip(event, 'On air: ' + ', '.join(self._on_air_with()))
            elif self._label_rect.adjusted(-4, -6, 4, 6).contains(point):
                self._tip(event, f'{self.record_title} (click: rename it)' if self.record_title else
                          'Click: the title of the next recording')
            elif self._add_rect.adjusted(-4, -4, 4, 4).contains(point):
                self._tip(event, 'Another input: a microphone, a line in (one strip per input device at most)')
            elif on_sign:
                self._tip(event, 'Remove this input')
            elif self.source() and self._cut.adjusted(-4, -4, 4, 4).contains(point):
                self._tip(event, self._app_output_tip())
            elif self.thru() and self._fader_at(point) is self.music_fader:
                app = self.source_selector.title(self.source())
                self._tip(event, f'SEND: {app} into the call' + ('' if self.sending() else ' (nothing sent now)')
                          + f'. You hear {app}\'s own volume.')
            elif self._share.adjusted(-4, -4, 4, 4).contains(point):
                self._tip(event, f'{self.source_selector.title(self.source())} in Blink\'s call too'
                          + (' (click: not sent)' if self.sharing else ''))
            elif self._library_share.adjusted(-4, -4, 4, 4).contains(point):
                self._tip(event, 'The Local Library in Blink\'s call too' + (' (click: not sent)' if self.library_sharing else ''))
            return
        item, start, value = self._drag
        if item is self.echo:
            moved = (start.y() - point.y()) + (point.x() - start.x())
            self._turn_echo(value + moved / 220.0)
            self._tip(event, f'Echo {self.echo.value * 100:.0f}%')
        else:
            if value is not None and abs(point.y() - value.y()) < 4:      # not dragged yet: still a click
                return
            self._drag = (item, start, None)
            self._move_fader(item, round(item.value_at(point.y() - item.cap_height / 2, start) * 10) / 10)   # tick to tick

    def mouseReleaseEvent(self, event):
        if self._redial_down:           # the call button let go over it: a call up, it hangs up; else it redials
            self._redial_down = False
            self.update(self._redial_rect.adjusted(-10, -10, 10, 10).toAlignedRect())
            if not self._redial_rect.adjusted(-4, -4, 4, 4).contains(event.position()):
                return
            if self.blink_calls:        # the call talked to (else the first): as its HANG UP
                calls = [call for call in self.blink_calls if call[4] and call[4].get('id') is not None]
                live = [call for call in calls if not call[2]]
                target = (live or calls or [None])[0]
                if target is not None:
                    self.hangup_requested.emit(target[4])
                    self._clicked(event, 'Hanging up in Blink')
            elif self.blink_redial is not None:
                self.redial_requested.emit(self.blink_redial)
                self._clicked(event, 'Calling in Blink')
            return
        drag, self._drag = self._drag, None
        if drag is not None and drag[0] is not self.echo and drag[2] is not None:     # a click, not a drag
            fader = drag[0]
            self._move_fader(fader, round(fader.value * 10) / 10)

    def wheelEvent(self, event):
        event.accept()
        if not self.powered:
            return
        point = event.position()
        delta = event.angleDelta().y() or event.angleDelta().x()
        if event.inverted():
            delta = -delta
        self._wheel_rest += delta / 120
        steps = int(self._wheel_rest)
        if not steps:
            return
        self._wheel_rest -= steps
        entry = self._selector_at(point)
        if entry is not None and entry[0] is self.streaming_selector and not self.streaming_available:
            return
        if entry is not None:
            self._choose(entry[0], entry[1], int(entry[0].value) + steps)
        elif self.echo.distance(point) <= self.echo.radius * 1.6:
            self._turn_echo(self.echo.value + steps * self.echo.step)
        else:       # the strip it is over: its fader
            fader = next((fader for strip, fader, meter, name in self._visible if strip.contains(point)), self.library_fader)
            self._move_fader(fader, round(fader.value * 10 + steps) / 10)        # a tick a step

    def _app_output_tip(self):
        app = self.source_selector.title(self.source())
        return (f'THRU: {app} plays on its own output, untouched; the mixer only sends it on' if self.app_output else
                f'{app} taken over: heard only through the mixer, with its fader (click: THRU, left as it plays)')

    def _tip(self, event, text):
        """A tooltip, wrapped (as rich text Qt wraps it, about 300 px wide) rather than one long line."""
        import html
        QToolTip.showText(event.globalPosition().toPoint(), f'<qt>{html.escape(text)}</qt>', self)
