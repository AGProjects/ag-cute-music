"""Playing: a GStreamer playbin with its audio filter: the amplifier's tone (equalizer-3bands: bass, treble) and
balance (audiopanorama), then level (the VU meter) and spectrum, so the meters show what the knobs do.

The bus is polled from a Qt timer (no GLib main loop needed: the same on macOS). The levels come ahead of
what is heard (posted as the buffers pass the filter, before the sink's queue): they are held until the
pipeline's running time reaches theirs. The next track is set at about-to-finish (gapless); its stream-start
on the bus says it began.
"""

import os
import re
import sys
import threading
from collections import deque

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal

from cutemusic.util import log

try:
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init([])
    gst_error = None
except (ImportError, ValueError) as e:
    Gst = None
    gst_error = str(e)

if Gst is not None:
    # gst-python's override of Gst.ElementFactory.make (in GStreamer's own wheels, the Windows app's) raises
    # Gst.MissingPluginError for an element not there, where PyGObject's returns None: None everywhere, as this code
    # (and link, mic, recorder, hal through this Gst) tests for
    _missing = getattr(Gst, 'MissingPluginError', None) or getattr(sys.modules.get('gi.overrides.Gst'), 'MissingPluginError', None)
    if _missing is not None:
        _make = Gst.ElementFactory.make

        def _make_or_none(factory_name, name=None, _make=_make, _missing=_missing):
            try:
                return _make(factory_name, name)
            except _missing:
                return None
        Gst.ElementFactory.make = staticmethod(_make_or_none)

    # Device lists (Gst.DeviceMonitor, one per strip) from PulseAudio's provider (pipewire-pulse) only: PipeWire's
    # watches the PipeWire metadata from libpipewire's own thread and crashed AG CuteMusic there (a metadata event's
    # callback through a stale pointer, in libpipewire-module-metadata). PulseAudio's entries were the ones taken
    # anyway (_is_pipewire); pipewiresrc / pipewiresink (link.py) are not affected.
    _provider = Gst.DeviceProviderFactory.find('pipewiredeviceprovider')
    if _provider is not None:
        _provider.set_rank(Gst.Rank.NONE)


__all__ = ['Player', 'gst_error', 'audio_devices']


_monitors = {}      # Linux: device class ('Audio/Sink', 'Audio/Source'): its Gst.DeviceMonitor, started once


def audio_devices(kind):
    """The Gst.Devices of kind ('Audio/Sink', 'Audio/Source') now. Linux: from one DeviceMonitor per kind, started
    once and kept: PulseAudio's provider probed afresh leaked its context's memfd pool each time (one
    memfd:pulseaudio for every DeviceMonitor.new() + get_devices(), never closed): probed every 2 s for the outputs
    and the inputs, AG CuteMusic ran out of file descriptors in about 18 minutes and GLib aborted it (cannot create
    wakeup pipe). Started, the provider keeps its one context and follows the devices plugged in and gone itself.
    Elsewhere a fresh probe each time: a started monitor kept only what was there when it started on macOS (Core
    Audio's provider watches no hot plugging)."""
    if not sys.platform.startswith('linux'):
        monitor = Gst.DeviceMonitor.new()
        monitor.add_filter(kind, None)
        return monitor.get_devices() or []
    monitor = _monitors.get(kind)
    if monitor is None:
        monitor = Gst.DeviceMonitor.new()
        monitor.add_filter(kind, None)
        monitor.get_bus().set_flushing(True)        # its device-added / -removed messages: not read, not kept
        if not monitor.start():
            log.warning(f'[player] cannot watch the {kind} devices: probed each time')
            return monitor.get_devices() or []
        _monitors[kind] = monitor
    return monitor.get_devices() or []


AUDIO_SINKS = ('osxaudiosink', 'pulsesink', 'pipewiresink', 'alsasink', 'wasapi2sink', 'autoaudiosink')     # (wasapi2sink: Windows)
OWN_DEVICES = re.compile(r'\bAG Podcast (Mixer|Streaming)\b|\bAG CuteMusic Tap\b')   # the driver's and PipeWire's (link.py), the taps' (taps.py)
LEVEL_INTERVAL = 25         # ms between two levels (and two spectra)
SPECTRUM_BANDS = 512        # FFT bands from 0 to half the sample rate (~43 Hz each at 44.1 kHz): grouped for drawing
SPECTRUM_THRESHOLD = -90    # dB: the floor of the magnitudes
SILENCE = -90.0             # dB


def _audio_sink():
    forced = os.environ.get('CUTEMUSIC_AUDIO_SINK')
    if forced and ' ' in forced.strip():        # with properties: "fakesink sync=true", "alsasink device=hw:1"
        try:
            return Gst.parse_bin_from_description(forced, True)
        except Exception as e:
            log.warning(f'[player] CUTEMUSIC_AUDIO_SINK={forced}: {e}')
    for name in ((forced,) if forced else AUDIO_SINKS):
        sink = Gst.ElementFactory.make(name, 'audiosink')
        if sink is None:
            continue
        if name == 'autoaudiosink' or forced:
            return sink
        if sink.set_state(Gst.State.READY) != Gst.StateChangeReturn.FAILURE:
            sink.set_state(Gst.State.NULL)
            return sink
    return None


def _is_pipewire(device):
    """Whether device plays through pipewiresink. The same output is often listed twice (PipeWire, and PulseAudio
    through pipewire-pulse): the PulseAudio one is taken, as pipewiresink deadlocks on a flushing seek while paused
    (its main loop holds its lock waiting for the stream, the seek waits for the lock: a track resumed where it was
    left froze the player, and the window with it)."""
    try:
        element = device.create_element(None)
    except Exception:
        return False
    factory = element.get_factory() if element is not None else None
    return factory is not None and factory.get_name() == 'pipewiresink'


def _pulse_sink(device):
    """A pulsesink on a PipeWire device (through pipewire-pulse: its node's name is the PulseAudio sink's), None when
    there is no pulsesink or no node name."""
    properties = device.get_properties()
    node = properties.get_string('node.name') if properties is not None else None
    sink = Gst.ElementFactory.make('pulsesink', None) if node else None
    if sink is None:
        return None
    sink.set_property('device', node)
    if sink.set_state(Gst.State.READY) == Gst.StateChangeReturn.FAILURE:       # no pipewire-pulse
        sink.set_state(Gst.State.NULL)
        return None
    sink.set_state(Gst.State.NULL)
    return sink


_NUMBER_LIST = re.compile(r'\{\s*([^}]*)\}')


_players = []       # the Players made (the profiler's output diagnosis: output_diagnosis())


def output_diagnosis():
    """For the log ([profile]): the output sink's state, the clocks, what its last buffer held and when it was due.
    A sink fed loud buffers yet silent: its clock not the pipeline's, or buffers out of time."""
    import math
    lines = []
    for player in _players:
        pipeline = player.pipeline
        if pipeline is None:
            continue
        sink = None
        top = pipeline.get_property('audio-sink')
        candidates = [top]
        if isinstance(top, Gst.Bin):
            iterator = top.iterate_recurse()
            while True:
                result, element = iterator.next()
                if result != Gst.IteratorResult.OK:
                    break
                candidates.append(element)
        for element in candidates:
            factory = element.get_factory() if element is not None else None
            if factory is not None and factory.get_name() in ('pulsesink', 'pipewiresink', 'alsasink', 'osxaudiosink', 'wasapi2sink'):
                sink = element
                break
        clock = pipeline.get_clock()
        running = clock.get_time() - pipeline.get_base_time() if clock is not None else None
        text = f'pipeline {pipeline.get_state(0)[1].value_nick}, clock {clock.get_name() if clock else None}'
        if sink is None:
            lines.append(text + ', no output sink found')
            continue
        own = sink.provide_clock() if hasattr(sink, 'provide_clock') else None
        state = sink.get_state(0)
        pending = f' (pending {state[2].value_nick})' if state[2] != Gst.State.VOID_PENDING else ''
        same = " (the pipeline's)" if own is not None and own is clock else ''
        used = sink.get_clock()
        text += (f'; {sink.get_factory().get_name()} {sink.get_name()} {state[1].value_nick}{pending}, '
                 f'its clock {own.get_name() if own else None}{same}, its element clock {used.get_name() if used else None}')
        try:
            sink.set_property('enable-last-sample', True)
            sample = sink.get_property('last-sample')
        except Exception:
            sample = None
        if sample is not None:
            buffer = sample.get_buffer()
            caps = sample.get_caps()
            data = buffer.extract_dup(0, buffer.get_size())
            fmt = caps.get_structure(0).get_string('format') if caps is not None and caps.get_size() else None
            rms = None
            try:
                import numpy
                dtype = {'S16LE': numpy.int16, 'F32LE': numpy.float32, 'S32LE': numpy.int32}.get(fmt)
                if dtype is not None:
                    values = numpy.frombuffer(data, dtype=dtype).astype(numpy.float64)
                    if dtype is numpy.int16:
                        values /= 32768.0
                    elif dtype is numpy.int32:
                        values /= 2147483648.0
                    rms = float(numpy.sqrt(numpy.mean(values ** 2))) if values.size else 0.0
            except Exception:
                pass
            level = f'{20 * math.log10(rms):.1f} dBFS' if rms else ('silent' if rms == 0.0 else f'? ({fmt})')
            segment = sample.get_segment()
            stamp = segment.to_running_time(Gst.Format.TIME, buffer.pts) if segment is not None and buffer.pts != Gst.CLOCK_TIME_NONE else None
            late = f', due {(stamp - running) / Gst.SECOND:+.2f} s from now' if stamp is not None and running is not None else ''
            text += f'; its last buffer {level}{late}'
        else:
            text += '; no last buffer'
        lines.append(text)
    return lines


_FROM_TEXT = set()      # the fields PyGObject could not convert: read from the structure's text at once


def _doubles(structure, name):
    if name not in _FROM_TEXT:
        try:
            value = structure.get_value(name)
            return [float(v) for v in value]
        except Exception:
            _FROM_TEXT.add(name)        # (the exception each time costs as much as the reading)
    # a GValueArray or GstValueList PyGObject cannot convert ("unknown type"); from the structure's text instead
    # (level's rms and peak: GValueArray <...> or (double){...}; spectrum's magnitude: a list, (float){...})
    text = structure.to_string()
    at = text.find(f' {name}=(')
    if at < 0:
        return []
    at = text.find(')', at) + 1
    while at < len(text) and text[at] == ' ':
        at += 1
    if at >= len(text) or text[at] not in '{<':
        return []
    end = text.find('}' if text[at] == '{' else '>', at)
    if end < 0:
        return []
    values = text[at + 1:end].split(',')
    try:
        return [float(v) for v in values if v.strip()]
    except ValueError:          # typed values: (double)-20.5
        try:
            return [float(re.sub(r'\(\w+\)', '', v)) for v in values if v.strip()]
        except ValueError:
            return []


class Player(QObject):
    """state_changed('playing' | 'paused' | 'stopped'), position(seconds, duration), levels(rms dB list, peak dB
    list), spectrum(magnitude dB list, sample rate; empty: none), started(uri) when a track begins to be heard (also the gapless next one), finished() at the end of the
    last, error(text)."""

    state_changed = pyqtSignal(str)
    position = pyqtSignal(float, float)
    levels = pyqtSignal(object, object)     # (rms, peak dB lists) as objects: a list type is converted to a QVariantList and back for each slot
    spectrum = pyqtSignal(object, int)      # (the 512 magnitudes: passed as they are, not converted for each slot)
    output_changed = pyqtSignal(str, str)       # (the output now, '' the default; why): plugged in, or gone
    outputs_changed = pyqtSignal()              # an output plugged in, or gone
    started = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = 'stopped'
        self.uri = None
        self._next_uri = None           # set by the GUI thread, taken at about-to-finish
        self._gapless_uri = None        # the one taken, until its stream-start
        self._lock = threading.Lock()
        self._levels = deque()
        self._spectra = deque()
        self._spectrum = None
        self._tone = None               # equalizer-3bands: bass and treble
        self._panorama = None           # audiopanorama: the balance
        self._volume = 1.0
        self._extra = 0.0               # dB over the top (the amplifier's volume past 10)
        self._start = None
        self._start_paused = False
        self.pipeline = None
        self.output = None              # the output device's name; None: the system's default
        if Gst is None:
            return
        self.pipeline = Gst.ElementFactory.make('playbin', 'player')
        _players.append(self)       # (the profiler's output diagnosis)
        if self.pipeline is None:
            self.pipeline = None
            return
        self.pipeline.set_property('flags', 0x02 | 0x10)       # audio, soft volume: no video (a cover is not a video)
        sink = _audio_sink()
        if sink is not None:
            self.pipeline.set_property('audio-sink', sink)
        audio_filter = self._audio_filter()
        if audio_filter is not None:
            self.pipeline.set_property('audio-filter', audio_filter)
        self.pipeline.connect('about-to-finish', self._about_to_finish)
        self.bus = self.pipeline.get_bus()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(20)
        self._ticks = 0

    def _audio_filter(self):
        """The tone (equalizer-3bands) and the balance (audiopanorama, simple: the other side turned down) when there
        (gst-plugins-good), then level (the VU meter) and spectrum (the spectrum analyser): one bin."""
        level = Gst.ElementFactory.make('level', 'level')
        if level is None:
            return None
        level.set_property('interval', LEVEL_INTERVAL * Gst.MSECOND)
        level.set_property('post-messages', True)
        level.set_property('peak-ttl', 0)
        elements = []
        # the singer taken out (the mixer's VOCAL CUT): audiokaraoke, its level 0 until switched on
        karaoke = Gst.ElementFactory.make('audiokaraoke', 'karaoke')
        karaoke_convert = Gst.ElementFactory.make('audioconvert', 'karaoke-convert')
        if karaoke is not None and karaoke_convert is not None:
            karaoke.set_property('level', 0.0)
            karaoke.set_property('mono-level', 0.0)
            self._karaoke = karaoke
            elements += [karaoke_convert, karaoke]
        convert, tone, panorama = (Gst.ElementFactory.make(name, name) for name in ('audioconvert', 'equalizer-3bands', 'audiopanorama'))
        if None in (convert, tone, panorama):
            log.info('[player] no equalizer-3bands or audiopanorama (gst-plugins-good): no tone and balance')
        else:
            Gst.util_set_object_arg(panorama, 'method', 'simple')
            self._tone, self._panorama = tone, panorama
            elements += [convert, tone, panorama]
        elements.append(level)
        spectrum = Gst.ElementFactory.make('spectrum', 'spectrum')
        if spectrum is None:
            log.info('[player] no spectrum element (gst-plugins-good): no spectrum analyser')
        else:
            spectrum.set_property('bands', SPECTRUM_BANDS)
            spectrum.set_property('threshold', SPECTRUM_THRESHOLD)
            spectrum.set_property('interval', LEVEL_INTERVAL * Gst.MSECOND)
            spectrum.set_property('post-messages', True)
            spectrum.set_property('message-magnitude', True)
            spectrum.set_property('message-phase', False)
            spectrum.set_property('multi-channel', False)
            self._spectrum = spectrum
            elements.append(spectrum)
        if len(elements) == 1:
            return level
        audio_filter = Gst.Bin.new('audio-filter')
        for element in elements:
            audio_filter.add(element)
        for first, second in zip(elements, elements[1:]):
            first.link(second)
        audio_filter.add_pad(Gst.GhostPad.new('sink', elements[0].get_static_pad('sink')))
        audio_filter.add_pad(Gst.GhostPad.new('src', elements[-1].get_static_pad('src')))
        return audio_filter

    _karaoke = None

    @property
    def has_vocal_cut(self):
        return self._karaoke is not None

    def set_vocal_cut(self, on):
        """The singer (what is in the middle of the stereo) taken out, or not."""
        if self._karaoke is not None:
            self._karaoke.set_property('level', 1.0 if on else 0.0)

    @property
    def has_tone(self):
        """Whether bass, treble and balance can be set (the elements are there)."""
        return self._tone is not None

    def set_tone(self, bass, treble):
        """bass and treble in dB, -12..12 (equalizer-3bands: its 100 Hz and 11 kHz bands)."""
        if self._tone is not None:
            self._tone.set_property('band0', max(-24.0, min(12.0, float(bass))))
            self._tone.set_property('band2', max(-24.0, min(12.0, float(treble))))

    _balance = 0.0
    channel_mutes = (False, False)      # the mixer's L and R on LOCAL LIBRARY: a channel silenced

    def set_balance(self, value):
        """-1 (left only) .. 0 .. 1 (right only)."""
        self._balance = max(-1.0, min(1.0, float(value)))
        self._apply_panorama()

    def set_channel_mutes(self, left, right):
        """One channel silenced (the mixer's L / R on LOCAL LIBRARY): the balance all the way to the other side
        (audiopanorama simple: the other side turned down), before the meters; both: muted as the mute does."""
        self.channel_mutes = (bool(left), bool(right))
        self._apply_panorama()
        self._apply_volume()
        log.info(f'[player] channels: {"left muted" if left else "left on"}, {"right muted" if right else "right on"}')

    def _apply_panorama(self):
        if self._panorama is None:
            return
        left, right = self.channel_mutes
        value = 1.0 if left and not right else -1.0 if right and not left else self._balance
        self._panorama.set_property('panorama', value)

    def _spectrum_rate(self):
        caps = self._spectrum.get_static_pad('sink').get_current_caps() if self._spectrum is not None else None
        if caps is None or caps.get_size() == 0:
            return 0
        ok, rate = caps.get_structure(0).get_int('rate')
        return rate if ok else 0

    spectrum_wanted = True

    def set_spectrum_wanted(self, on):
        """The spectrum analysed and posted only while a spectrum is on screen (the window's watch): its 512 bands
        read 40 times a second for nothing otherwise."""
        if on == self.spectrum_wanted or self._spectrum is None:
            return
        self.spectrum_wanted = on
        self._spectrum.set_property('post-messages', on)
        if not on:
            self._spectra.clear()
        log.info(f'[player] spectrum {"on: shown" if on else "off: none shown"}')

    def _clear_meters(self):
        self._levels.clear()
        self._spectra.clear()

    # The output device

    def output_devices(self):
        """{name: Gst.Device} of the audio outputs (GStreamer's device providers: Core Audio, PulseAudio, PipeWire)."""
        if Gst is None:
            return {}
        devices = {}
        for device in audio_devices('Audio/Sink'):
            name = device.get_display_name()
            if OWN_DEVICES.search(name):      # the mixer's own (link.py): not an output to play on
                continue
            if name not in devices or (_is_pipewire(devices[name]) and not _is_pipewire(device)):
                devices[name] = device
        return devices

    # Outputs plugged in and taken out, while playing: the music paused when the one it plays on goes (or the
    # system's default one: what it was on), moved to a new one when plugged in (auto_switch_outputs)

    auto_switch_outputs = True
    _outputs = None
    VIRTUAL = re.compile(r'airplay|teams|zoom|virtual|blackhole|aggregate|multi-output|soundflower|loopback|'
                         r'background music|webex|discord|ag cutemusic', re.IGNORECASE)

    def _probe_outputs(self):
        """The output devices' names now (audio_devices)."""
        names = {device.get_display_name() for device in audio_devices('Audio/Sink')}
        return {name for name in names if not OWN_DEVICES.search(name)}     # the mixer's own: not unplugged

    def _watch_outputs(self):
        if not self.available or os.environ.get('CUTEMUSIC_AUDIO_SINK'):
            return
        try:
            names = self._probe_outputs()
        except Exception as e:
            log.warning(f'[player] cannot list the outputs: {e}')
            return
        before, self._outputs = self._outputs, names
        if before is None or names == before:
            return
        gone, added = before - names, names - before
        self.outputs_changed.emit()
        for name in sorted(gone):
            log.info(f'[player] output gone: {name}')
        for name in sorted(added):
            log.info(f'[player] output plugged in: {name}')
        if gone and (self.output in gone or (self.output is None and self.state == 'playing')):
            lost = self.output
            if self.state == 'playing':
                self.pause()
            if lost is not None:
                self.set_output(None)       # on the default one, paused where it was
            self.output_changed.emit('', f'{lost or "The output"} is gone: paused')
            return
        added = sorted(name for name in added if not self.VIRTUAL.search(name))
        if added and self.state == 'playing' and self.auto_switch_outputs:
            self.set_output(added[0])
            if self.output == added[0]:
                self.output_changed.emit(added[0], f'Playing on {added[0]}, plugged in')

    link = None         # a LinkSender the music is sent to too (link.py: Blink's "AG Podcast Mixer"), None: not
    recorder = None     # the mixer's Recorder (recorder.py): a branch to it always, None: not
    _shared_volume = None   # the audioamplify at the head of the shared sink (before its tee): the fader and mute there

    def set_recorder(self, recorder):
        """A branch to the mixer's Recorder (the Local Library in what it records), None: not."""
        if recorder is not self.recorder:
            self.recorder = recorder
            self.set_output(self.output)

    def set_link(self, sender):
        """The music sent through the link too (a LinkSender: TO CALLS), None: not; what plays goes on from where it
        was."""
        if sender is not self.link:
            self.link = sender
            self.set_output(self.output)

    QUEUE_TIME = 150        # ms: the shared sink's queues at most (MUTE and the fader heard after about that)

    @classmethod
    def _short_queue(cls, queue):
        queue.set_property('max-size-time', cls.QUEUE_TIME * Gst.MSECOND)
        queue.set_property('max-size-buffers', 0)
        queue.set_property('max-size-bytes', 0)

    def _shared(self, sink):
        """sink, and the link's and the recorder's branches: a tee to all, buffered alike (the link's branch is
        clocked: the music reaches the call as it is heard here)."""
        extras = []
        if self.link is not None:
            branch = self.link.branch('music', clocked=True)
            if branch is not None:
                extras.append((branch, 'through the link'))
        if self.recorder is not None:
            branch = self.recorder.branch('music', clocked=True)
            if branch is not None:
                extras.append((branch, 'to the recorder'))
        self._shared_volume = None
        if not extras:
            return sink
        tee, first = Gst.ElementFactory.make('tee', None), Gst.ElementFactory.make('queue', None)
        volume = Gst.ElementFactory.make('audioamplify', 'shared-volume')
        if None in (tee, first, volume):
            return sink
        # the fader and mute here, before the tee, for every branch: playsink otherwise uses a sink inside the bin
        # that has a volume of its own (pulsesink's stream volume, also with the soft-volume flag), which only the
        # output follows; the link and the recorder got the music at full level, never muted. audioamplify, not
        # volume: playsink takes any element with a stream volume inside the bin as the playbin's own (a volume
        # element there, on macOS where osxaudiosink has none, was set back to the playbin's 1.0, unmuted)
        # each branch's queue short: the fader and mute act before them, so what a queue holds is heard (sent,
        # recorded) after a mute as it was. A queue's default (1 s, filled ahead by the clocked sinks) made MUTE
        # take a second or two; the branches are all clocked, the decoder ahead of them, so a short one suffices
        self._short_queue(first)
        both = Gst.Bin.new('shared-sink')
        for element in (volume, tee, first, sink):
            both.add(element)
        volume.link(tee)
        tee.link(first)
        first.link(sink)
        for extra, where in extras:
            queue = Gst.ElementFactory.make('queue', None)
            self._short_queue(queue)
            both.add(queue)
            both.add(extra)
            tee.link(queue)
            queue.link(extra)
            log.info(f'[player] the music shared {where}')
        both.add_pad(Gst.GhostPad.new('sink', volume.get_static_pad('sink')))
        self._shared_volume = volume
        return both

    def set_output(self, name):
        """Play on the output named name (output_devices), None the system's default; what plays goes on there from
        where it was."""
        if not self.available or os.environ.get('CUTEMUSIC_AUDIO_SINK'):
            return
        sink = None
        if name:
            device = self.output_devices().get(name)
            if device is None:
                log.warning(f'[player] no output {name!r}: the default one')
                name = None
            else:
                sink = device.create_element(None)
                if sink is not None and sink.get_factory().get_name() == 'pipewiresink':
                    sink = _pulse_sink(device) or sink       # pipewiresink hangs on the resume's seek (_is_pipewire)
                log.info(f'[player] {name}: {sink.get_factory().get_name() if sink is not None else "no sink"}')
        if sink is None:
            name = None
            sink = _audio_sink()
        if sink is None:
            return
        sink = self._shared(sink)
        self._apply_volume()        # (on the shared sink's volume when there is one, else the playbin's)
        self.output = name
        state, uri, position = self.state, self.uri, 0.0
        if state != 'stopped':
            ok, at = self.pipeline.query_position(Gst.Format.TIME)
            position = at / Gst.SECOND if ok else 0.0
        # a start's seek still running (another output chosen right after one): let it end first, the pipeline
        # taken to NULL under a flushing seek hangs both
        seek = self._seek_thread
        if seek is not None and seek.is_alive():
            seek.join(3.0)
            if seek.is_alive():
                log.warning('[player] the seek is still running: switching the output anyway')
        self.pipeline.set_state(Gst.State.NULL)         # the sink is changed only out of PAUSED / PLAYING
        self.pipeline.set_property('audio-sink', sink)
        log.info(f'[player] output: {name or "the system default"}')
        if uri and state != 'stopped':
            self.play_uri(uri, start=position, paused=state == 'paused')

    @property
    def available(self):
        return self.pipeline is not None

    # Control

    def play_uri(self, uri, start=0.0, paused=False):
        """From start (s): a seek once the pipeline is prerolled; paused, ready for resume() there."""
        if not self.available:
            self.error.emit(f'GStreamer is not available: {gst_error}')
            return
        self.pipeline.set_state(Gst.State.READY)
        self._clear_meters()
        with self._lock:
            self._gapless_uri = None
        self.uri = uri
        self._start = start if start > 0 else None
        self.pipeline.set_property('uri', uri)
        if self._start is not None:
            self.pipeline.set_state(Gst.State.PAUSED)       # the seek at ASYNC_DONE, then PLAYING unless paused
            self._start_paused = paused
        else:
            self.pipeline.set_state(Gst.State.PAUSED if paused else Gst.State.PLAYING)
        self._set_state('paused' if paused else 'playing')

    def play_file(self, path, start=0.0, paused=False):
        self.play_uri(QUrl.fromLocalFile(path).toString(QUrl.ComponentFormattingOption.FullyEncoded), start, paused)

    def set_next_file(self, path):
        with self._lock:
            self._next_uri = QUrl.fromLocalFile(path).toString(QUrl.ComponentFormattingOption.FullyEncoded) if path else None

    def pause(self):
        if self.available and self.state == 'playing':
            result = self.pipeline.set_state(Gst.State.PAUSED)
            log.info(f'[player] paused ({result.value_nick})')
            self._set_state('paused')
            self._clear_meters()
            self.levels.emit([], [])        # the meters back to silence, not frozen at the last level
            self.spectrum.emit([], 0)

    def resume(self):
        if self.available and self.state == 'paused':
            self._start_paused = False
            # not prerolled (an output changed while paused whose sink never got ready: its start's seek still
            # waiting, or the state change failed): PLAYING on it would not play -- the track started again there
            result, current, pending = self.pipeline.get_state(0)
            if self.uri and (self._start is not None or result in (Gst.StateChangeReturn.FAILURE, Gst.StateChangeReturn.ASYNC)
                             or current != Gst.State.PAUSED):
                start = self._start
                if start is None:
                    ok, at = self.pipeline.query_position(Gst.Format.TIME)
                    start = at / Gst.SECOND if ok and at > 0 else 0.0
                log.info(f'[player] resume: the pipeline not ready ({result.value_nick}, {current.value_nick}): '
                         f'started again at {start:.1f} s')
                self.play_uri(self.uri, start=start)
                return
            result = self.pipeline.set_state(Gst.State.PLAYING)
            log.info(f'[player] resumed ({result.value_nick})')
            self._set_state('playing')

    def toggle(self):
        if self.state == 'playing':
            self.pause()
        elif self.state == 'paused':
            self.resume()

    def stop(self):
        self._start = None
        if self.available:
            self.pipeline.set_state(Gst.State.NULL)
        self._clear_meters()
        self.uri = None
        self._set_state('stopped')
        self.levels.emit([], [])
        self.spectrum.emit([], 0)

    def seek(self, seconds):
        if self._start is not None:         # not prerolled yet: there instead
            self._start = max(0.0, seconds)
            return
        if self.available and self.state != 'stopped':
            self._clear_meters()
            self.pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, int(max(0, seconds) * Gst.SECOND))

    muted = False

    def set_muted(self, muted):
        self.muted = bool(muted)
        self._apply_volume()
        log.info(f'[player] {"muted" if self.muted else "unmuted"} ({self.applied()})')

    def applied(self):
        """What the playbin has now, for the log: its volume and mute as read back, and its sink."""
        if not self.available:
            return 'no player'
        sink = self.pipeline.get_property('audio-sink')
        shared = self._shared_volume
        where = (f'shared-sink amplification {shared.get_property("amplification"):.3f}' if shared is not None else
                 f'playbin volume {self.pipeline.get_property("volume"):.3f}, mute {self.pipeline.get_property("mute")}')
        return f'{where}, sink {sink.get_name() if sink is not None else None}, {self.state}{self._stream_state(sink)}'

    @staticmethod
    def _stream_state(sink):
        """The output's own stream volume and mute (pulsesink's: as the sound server holds them, restored per app),
        for the log: a stream left muted or at 0 there is silent whatever the amplification."""
        found = None
        if sink is not None:
            if isinstance(sink, Gst.Bin):
                iterator = sink.iterate_recurse()
                while True:
                    result, element = iterator.next()
                    if result != Gst.IteratorResult.OK:
                        break
                    if element.get_factory() is not None and element.get_factory().get_name() == 'pulsesink':
                        found = element
                        break
            elif sink.get_factory() is not None and sink.get_factory().get_name() == 'pulsesink':
                found = sink
        if found is None:
            return ''
        try:
            return f'; stream volume {found.get_property("volume"):.3f}, mute {found.get_property("mute")}'
        except Exception:
            return ''

    def set_volume(self, value):
        """value 0..1, as heard (cubic, as the desktop's volume sliders)."""
        self._volume = max(0.0, min(1.0, value))
        self._apply_volume()

    def set_extra(self, db):
        """db (0..6) more than the volume's top: the amplifier's 11 (it clips, as it should)."""
        self._extra = max(0.0, min(12.0, float(db)))
        self._apply_volume()

    def _apply_volume(self):
        """The volume and mute on the shared sink's volume (every branch: the output, the link, the recorder) when
        there is one, the playbin then left at 1, unmuted; else on the playbin."""
        if not self.available:
            return
        value = self._volume ** 3 * 10 ** (self._extra / 20)
        muted = self.muted or all(self.channel_mutes)      # both channels silenced: as muted
        if self._shared_volume is not None:
            self._shared_volume.set_property('amplification', 0.0 if muted else min(value, 10.0))
            self.pipeline.set_property('volume', 1.0)
            self.pipeline.set_property('mute', False)
        else:
            self.pipeline.set_property('volume', value)
            self.pipeline.set_property('mute', muted)

    # GStreamer

    def _set_state(self, state):
        if state != self.state:
            self.state = state
            self.state_changed.emit(state)

    def _about_to_finish(self, playbin):
        # a streaming thread: the next file now, so it follows without a gap. Not while the start's seek runs (a
        # track resumed near its end): the playbin is not touched then (it hung there); the next one plays at the EOS
        with self._lock:
            if self._seeking:
                return
            uri, self._next_uri = self._next_uri, None
            self._gapless_uri = uri
        if uri:
            playbin.set_property('uri', uri)

    _seeking = False
    _seek_thread = None

    def _seek_start(self, start, paused):
        try:
            self.pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE, int(start * Gst.SECOND))
            ok, duration = self.pipeline.query_duration(Gst.Format.TIME)
            self.position.emit(start, duration / Gst.SECOND if ok and duration > 0 else 0.0)     # queued to the window
            if not paused:
                self.pipeline.set_state(Gst.State.PLAYING)
        finally:
            with self._lock:
                self._seeking = False

    def _running_time(self):
        clock = self.pipeline.get_clock()
        if clock is None:
            return None
        return clock.get_time() - self.pipeline.get_base_time()

    def _poll(self):
        while True:
            message = self.bus.pop()
            if message is None:
                break
            self._message(message)
        if self.state == 'playing':
            now = self._running_time()
            # held until heard: the pipeline's running time reaches theirs. When that clock and the meters' stamps
            # do not agree (some output devices' clocks: the stamps seconds ahead, or behind, for good), as they come
            latest = self._due(self._levels, now)
            if latest is not None:
                self.levels.emit(latest[1], latest[2])
            latest = self._due(self._spectra, now)
            if latest is not None:
                self.spectrum.emit(latest[1], latest[2])
        self._ticks += 1
        if self._ticks % 100 == 0:          # every 2 s: outputs plugged in or gone
            self._watch_outputs()
        if self._ticks % 10 == 0 and self.state != 'stopped':
            ok, position = self.pipeline.query_position(Gst.Format.TIME)
            ok2, duration = self.pipeline.query_duration(Gst.Format.TIME)
            if ok:
                self.position.emit(position / Gst.SECOND, duration / Gst.SECOND if ok2 and duration > 0 else 0.0)

    _unsynced_logged = False

    def _due(self, items, now):
        """The last of items (running time, ...) due at now, the earlier ones dropped; all of them when the clock
        does not match their stamps."""
        if not items:
            return None
        if now is not None and abs(items[0][0] - now) > 2 * Gst.SECOND:
            if not self._unsynced_logged:
                self._unsynced_logged = True
                log.info(f'[player] the meters\' time stamps are {(items[0][0] - now) / Gst.SECOND:+.1f} s from the clock: '
                         'shown as they come')
            now = None
        latest = None
        while items and (now is None or items[0][0] <= now):
            latest = items.popleft()
        return latest

    def _message(self, message):
        kind = message.type
        if kind == Gst.MessageType.ELEMENT:
            structure = message.get_structure()
            if structure is not None and structure.get_name() == 'level':
                ok, running = structure.get_clock_time('running-time')
                if not ok:
                    ok, running = structure.get_clock_time('endtime')
                self._levels.append((running if ok else 0, _doubles(structure, 'rms'), _doubles(structure, 'peak')))
                while len(self._levels) > 400:
                    self._levels.popleft()
            elif structure is not None and structure.get_name() == 'spectrum':
                ok, running = structure.get_clock_time('running-time')
                if not ok:
                    ok, running = structure.get_clock_time('endtime')
                self._spectra.append((running if ok else 0, _doubles(structure, 'magnitude'), self._spectrum_rate()))
                while len(self._spectra) > 400:
                    self._spectra.popleft()
        elif kind == Gst.MessageType.ASYNC_DONE and self._start is not None:
            # the start's seek, in a thread of its own: a flushing seek waits for the streaming threads, and the
            # window must not wait with it (it froze, black, resuming a track 24 s before its end)
            start, self._start = self._start, None
            with self._lock:
                self._seeking = True
            self._seek_thread = threading.Thread(target=self._seek_start, args=(start, self._start_paused), name='seek', daemon=True)
            self._seek_thread.start()
        elif kind == Gst.MessageType.STREAM_START:
            with self._lock:
                uri, self._gapless_uri = self._gapless_uri, None
            if uri:
                self.uri = uri
            if self.uri:
                self.started.emit(self.uri)
        elif kind == Gst.MessageType.EOS:
            self.pipeline.set_state(Gst.State.NULL)
            self.uri = None
            self._set_state('stopped')
            self.levels.emit([], [])
            self.spectrum.emit([], 0)
            self.finished.emit()
        elif kind == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            log.warning(f'[player] {error.message} ({debug})')
            self.pipeline.set_state(Gst.State.NULL)
            self._set_state('stopped')
            self.error.emit(error.message)
