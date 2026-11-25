"""The mixer's microphone: an input device played straight to the output the music plays on, for singing along (the
Mixer view, mixer.py). A pipeline of its own, live and low latency, the sound server mixing it with the music: the
music's pause, seek and output switch do not touch it (it follows the output switch).

    src ! audioconvert ! audioresample ! audioecho ! volume ! level ! sink

The source and the sink are PulseAudio's where they can be (pipewire-pulse too): PipeWire's own elements hang on
state changes while live (as pipewiresink on a seek, player.py).
"""

import json
import os
import sys
import time

from PyQt6.QtCore import QCoreApplication, QObject, QTimer, pyqtSignal

from cutemusic import util
from cutemusic.util import log
from cutemusic import player as _player

Gst = _player.Gst


__all__ = ['Mic']


LATENCY = 10        # ms: the source's and the sink's buffers (a few times this is heard: under 50 ms, singable)
# (AG_CUTEMUSIC_MIC_LATENCY=<ms>: another, to test whether the mic's small buffers, which set the output device's I/O
# cycle for the whole process, make an app's capture played on the same device crackle)
LATENCY = int(os.environ.get('AG_CUTEMUSIC_MIC_LATENCY') or LATENCY)
# An input whose right channel stays silent while the left carries sound: a mono microphone opened as two channels
# (osxaudiosrc does so: the mic on the left, nothing on the right), or a mic on a two-channel interface's first input.
# Taken as mono once seen (opened with one channel, so it is on both sides from there on), and remembered per device.
MONO_SIGNAL = -50.0         # dB: the left carries sound
MONO_SILENCE = -80.0        # dB: the right has none
# An input that stalls: a USB mic now and then opened silent (no buffers at all, or digital zeros), until chosen again
# (another input and back). Made anew by the watchdog instead, a few times at most, until sound comes again.
STALL_AFTER = 2.0           # s without a buffer (no level message): stalled
DEAD_AFTER = 4.0            # s of digital zero (no hardware's noise floor): opened dead
DEAD_SILENCE = -200.0       # dB: digital zero (level's -inf, or NaN)
STALL_RETRIES = 3           # made anew this many times, then left as it is (a muted mic, a switched-off one)
MONO_AFTER = 40             # level messages in a row (25 ms: 1 s) that say so


def _mono_inputs():
    try:
        return set(json.loads(util.settings().value('mixer/mono_inputs', '[]', type=str) or '[]'))
    except (ValueError, TypeError):
        return set()


def _remember_mono(name):
    names = _mono_inputs() | {name}
    util.settings().setValue('mixer/mono_inputs', json.dumps(sorted(names)))
ECHO_DELAY = 0.18   # s: the echo's repeat, a small hall


def _monitor_name(name):
    return name.lower().startswith('monitor of')


def microphone_access(then):
    """macOS: the microphone permission, asked for when not decided yet (macOS shows its prompt once): then() on the
    main thread once given. Until it is, Core Audio gives the app no inputs (GStreamer lists none) or silence.
    True: allowed (or not macOS), then() not called; False: refused or restricted (System Settings -> Privacy &
    Security -> Microphone); None: being asked."""
    if sys.platform != 'darwin':
        return True
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
    except ImportError:
        return True     # (the app from a checkout without PyObjC's AVFoundation: Terminal's permission)
    status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
    if status == 3:         # authorized
        return True
    if status != 0:         # restricted (1), denied (2)
        log.warning('[mic] microphone access refused: System Settings -> Privacy & Security -> Microphone -> AG CuteMusic')
        return False

    def answered(granted):
        log.info(f'[mic] microphone access {"given" if granted else "refused"}')
        if granted:
            util.call_in_gui_thread(then)
    log.info('[mic] asking for microphone access')
    AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVMediaTypeAudio, answered)
    return None


class Mic(QObject):
    """levels(rms dB per channel): the mic's, after its level."""

    levels = pyqtSignal(object)     # (as an object: not converted to a QVariantList and back)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.input = None           # the input device's name; None: off
        self.output = None          # the output's (the player's): None the system's default
        self.pulse_source = None    # a PulseAudio source played instead of an input device (Linux: what Blink plays,
                                    # ag_podcast_mixer_call's monitor)
        self.link = None            # a LinkSender the mic is sent to too (link.py: Blink's "AG Podcast Mixer"), None: not
        self.link_source = None     # a receiver played instead of an input device (macOS: hal.py FeedReceiver, what
                                    # Blink plays)
        self.device_id = None       # a Core Audio device read instead (taps.py: another app's sound), by its id
        self.link_name = 'mic'      # what it is to the LinkSender ('music': an app as MUSIC's source)
        self.recorder = None        # the mixer's Recorder (recorder.py), its branch always there; None: not
        self.latency = LATENCY      # ms: its buffers' quantum, what it hands on at a time
        self.buffer = None          # ms: its source's buffer (None: twice the quantum); an app's music: more
        self.stereo = False         # the source taken in stereo (an app's music), not as it offers itself (a mic: mono)
        self.boost = 2.0            # the level's top: +6 dB (a quiet mic); 1.0: as it comes, never louder (an app's music)
        self.level = 0.8            # 0..1 (cubic, as the volume)
        self.echo = 0.0             # 0..1
        self.pipeline = None
        self._output = None         # macOS: the _Output its chain (a bin, self.pipeline) plays in
        self._volume = self._echo = None
        self._timer = QTimer(self, interval=25)
        self._timer.timeout.connect(self._poll)
        self._watchdog = QTimer(self, interval=500)
        self._watchdog.timeout.connect(self._watch)
        self._stalls = 0            # made anew by the watchdog since sound last came (or the input was chosen)
        self._last_level = self._dead_since = None

    @property
    def available(self):
        return Gst is not None and not os.environ.get('CUTEMUSIC_AUDIO_SINK')

    def forget_devices(self):
        """(Nothing kept now: input_devices() probes afresh each time.)"""

    def input_kinds(self):
        """{input device name: 'laptop' (built in), 'headset' (Bluetooth, a headset), 'wave' (virtual: an avatar of its
        first letter) or 'mic'}, from the sound server's device properties (Linux: taps.input_kinds() is macOS's)."""
        kinds = {}
        for name, device in self.input_devices().items():
            properties = device.get_properties()
            text = lambda key: (properties.get_string(key) or '').lower() if properties is not None else ''
            bus, form, icon = text('device.bus'), text('device.form_factor'), text('device.icon_name')
            if bus == 'bluetooth' or 'bluetooth' in icon:
                kinds[name] = 'headset'
            elif form == 'internal' or bus == 'pci' or icon.startswith('audio-card-pci'):
                kinds[name] = 'laptop'
            elif form in ('headset', 'headphone', 'hands-free') or 'headset' in icon:
                kinds[name] = 'headset'
            elif bus in ('usb', 'firewire', 'isa') or form in ('webcam', 'microphone', 'speaker'):
                kinds[name] = 'mic'
            else:       # no hardware behind it: a loopback, a null sink's monitor taken as a source, an app's
                kinds[name] = 'wave'
        return kinds

    def input_devices(self):
        """{name: Gst.Device} of the audio inputs (not the outputs' monitors), PulseAudio's taken over PipeWire's."""
        if Gst is None:
            return {}
        # (player.audio_devices: probed afresh on macOS, a mic plugged in since or the microphone permission given)
        devices = {}
        found = _player.audio_devices('Audio/Source')
        for device in found:
            name = device.get_display_name()
            properties = device.get_properties()
            if _player.OWN_DEVICES.search(name) or _monitor_name(name) or (properties is not None and properties.get_string('device.class') == 'monitor'):
                continue
            if name not in devices or (_is_pipewire_source(devices[name]) and not _is_pipewire_source(device)):
                devices[name] = device
        if not devices:
            log.warning(f'[mic] no audio input listed ({len(found)} found: '
                        f'{", ".join(device.get_display_name() for device in found) or "none"})')
        return devices

    # From the mixer

    def set_input(self, name):
        """Sing into the input named name; None: off."""
        self.input = name or None
        self.device_id = None
        self._stalls = 0
        self._rebuild()

    def set_device(self, device_id, title=None):
        """Play the Core Audio device device_id (taps.py: an app's sound, title its name); None: off."""
        self.device_id = device_id
        self.pulse_source = None
        self.link_source = None
        self.input = (title or str(device_id)) if device_id is not None else None
        self._rebuild()

    def set_output(self, name):
        """The music's output: the mic goes there too."""
        if name != self.output:
            self.output = name
            if self.pipeline is not None:
                self._rebuild()

    def set_pulse_source(self, source, title=None):
        """Play the PulseAudio source named source (a monitor) rather than an input device; None: off."""
        self.pulse_source = source
        self.link_source = None
        self.input = (title or source) if source else None
        self._rebuild()

    def set_link_source(self, receiver, title=None):
        """Play what Blink plays through the link (a LinkReceiver) rather than an input device; None: off."""
        if self.link_source is not None and receiver is not self.link_source:
            self.link_source.stop()
        self.link_source = receiver
        self.pulse_source = None
        self.input = (title or 'Blink') if receiver is not None else None
        self._rebuild()

    def set_link(self, sender):
        """The mic sent through the link too (a LinkSender), None: not."""
        if sender is not self.link:
            self.link = sender
            if self.pipeline is not None:
                self._rebuild()

    def set_recorder(self, recorder):
        """A branch to the mixer's Recorder (as link_name), None: not."""
        if recorder is not self.recorder:
            self.recorder = recorder
            if self.pipeline is not None:
                self._rebuild()

    muted = False
    monitor = True          # heard on the output too (the mixer's TO AMPLIFIER); off: only in what is shared
    _monitor_volume = None

    def set_monitor(self, on):
        """The mic heard on our output too, or only sent (a call: not hearing oneself)."""
        self.monitor = bool(on)
        if self._monitor_volume is not None:
            self._monitor_volume.set_property('mute', not self.monitor)

    def set_mute(self, muted):
        """Silent, its level kept (the mixer's MUTE)."""
        self.muted = bool(muted)
        if self._volume is not None:
            self._volume.set_property('mute', self.muted)

    def kick(self, gap=150):
        """Muted for gap ms, then as it was: what a hand's MUTE and unmute did to an app's capture that started
        distorted while the mic ran (its volume's passthrough switched off and on again: the stream reconfigured).
        A workaround until the cause is known."""
        if self._volume is None or self.muted:
            return
        self._volume.set_property('mute', True)
        QTimer.singleShot(gap, lambda volume=self._volume: volume.set_property('mute', self.muted) if volume is self._volume else None)
        log.info(f'[mic] {self.input}: muted {gap} ms and back (the distortion workaround)')

    def set_level(self, value):
        self.level = max(0.0, min(1.0, value))
        if self._volume is not None:
            self._volume.set_property('volume', self.level ** 3 * self.boost)       # cubic, as the volume; up to boost

    def set_echo(self, value):
        self.echo = max(0.0, min(1.0, value))
        if self._echo is not None:
            self._echo.set_property('intensity', self.echo * 0.6)
            self._echo.set_property('feedback', self.echo * 0.45)

    # The pipeline

    def stop(self):
        self._timer.stop()
        self._watchdog.stop()
        if self.pipeline is not None:
            if self._output is not None:        # (macOS: a bin in the output's pipeline)
                self._output.remove(self.pipeline)
            else:
                self.pipeline.set_state(Gst.State.NULL)
        self.pipeline = self._volume = self._echo = self._monitor_volume = None
        self._output = None
        self.levels.emit([])

    def _rebuild(self):
        self.stop()
        if self.input is None or not self.available:
            return
        if self.link_source is not None:
            source = self.link_source.source()
        elif self.device_id is not None:
            source = Gst.ElementFactory.make('osxaudiosrc', None)
            if source is not None:
                source.set_property('device', self.device_id)
        elif self.pulse_source:
            source = Gst.ElementFactory.make('pulsesrc', None)
            if source is not None:
                source.set_property('device', self.pulse_source)
        else:
            device = self.input_devices().get(self.input)
            if device is None:
                log.warning(f'[mic] no input {self.input!r}')
                return
            source = _pulse_element(device, 'pulsesrc') or device.create_element(None)
            # (diagnosis of an app's capture crackling while the mic runs: AG_CUTEMUSIC_MIC_TEST=source plays a test
            # tone in place of the mic, =sink plays the mic nowhere: which of the mic's two ends is the culprit)
            if os.environ.get('AG_CUTEMUSIC_MIC_TEST') == 'source':
                source = Gst.ElementFactory.make('audiotestsrc', None)
                source.set_property('is-live', True)
                source.set_property('volume', 0.0)
                log.info('[mic] AG_CUTEMUSIC_MIC_TEST=source: a silent test source in place of the mic')
        shared = SHARED_OUTPUT
        sink = None if shared else self._sink()
        if not shared and os.environ.get('AG_CUTEMUSIC_MIC_TEST') == 'sink' and self.device_id is None and self.link_source is None \
                and not self.pulse_source:
            sink = Gst.ElementFactory.make('fakesink', None)
            sink.set_property('sync', False)
            log.info('[mic] AG_CUTEMUSIC_MIC_TEST=sink: the mic played nowhere')
        names = ('audioconvert', 'audioresample', 'audioecho', 'volume', 'level')
        elements = [Gst.ElementFactory.make(name, None) for name in names]
        if source is None or (sink is None and not shared) or None in elements:
            log.warning('[mic] cannot build the mic pipeline (gst-plugins-good, -base?)')
            return
        convert, resample, echo, volume, level = elements
        for element, buffer in ((source, self.buffer or self.latency * 2), (sink, self.latency * 3)):
            if element is not None and element.find_property('buffer-time') is not None:
                element.set_property('buffer-time', buffer * 1000)
                element.set_property('latency-time', self.latency * 1000)
        if sink is not None and sink.find_property('sync') is not None:
            sink.set_property('sync', False)        # as it comes: no clock to wait on, no latency to add up
        if shared:      # the chain's end: the output's format, into its mixer (_Output)
            sink = Gst.ElementFactory.make('audioconvert', None)
            tail = [Gst.ElementFactory.make('audioresample', None), Gst.ElementFactory.make('capsfilter', None)]
            tail[1].set_property('caps', Gst.Caps.from_string(_Output.CAPS))
        echo.set_property('max-delay', int(ECHO_DELAY * 2 * Gst.SECOND))
        echo.set_property('delay', int(ECHO_DELAY * Gst.SECOND))
        level.set_property('interval', 25 * Gst.MSECOND)
        level.set_property('post-messages', True)
        pipeline = Gst.Bin.new(None) if shared else Gst.Pipeline.new('mic')
        monitor = Gst.ElementFactory.make('volume', None)       # TO AMPLIFIER: heard on the output, or only shared
        if monitor is None:
            return
        chain = [source, convert, resample, echo, volume, level, monitor, sink]
        # sent on as well, from a tee after the level: the link's branch (link.py), the recorder's
        extras = []
        if self.link is not None and self.link_source is None:
            branch = self.link.branch(self.link_name, clocked=False)
            if branch is not None:
                extras.append(branch)
        if self.recorder is not None:       # (Blink's call too: what is recorded is everything heard)
            branch = self.recorder.branch(self.link_name, clocked=False)
            if branch is not None:
                extras.append(branch)
        branches = []
        if extras:
            tee, queue = Gst.ElementFactory.make('tee', None), Gst.ElementFactory.make('queue', None)
            chain = [source, convert, resample, echo, volume, level, tee, queue, monitor, sink]
            for extra in extras:
                queue = Gst.ElementFactory.make('queue', None)
                queue.set_property('max-size-time', 100 * Gst.MSECOND)
                queue.set_property('leaky', 2)      # never holds the mic up
                branches.append((queue, extra))
        self._left_only = 0
        self._mono = self._is_input() and self.input in _mono_inputs()
        if self.stereo or self._mono:   # two channels from the source itself (an app's music: not a mono downmix
            caps = Gst.ElementFactory.make('capsfilter', None)      # upmixed again); one from a mono mic (both sides)
            caps.set_property('caps', Gst.Caps.from_string('audio/x-raw,channels=%d' % (1 if self._mono else 2)))
            chain.insert(1, caps)
        if shared:
            chain += tail
        for element in chain + [element for pair in branches for element in pair]:
            pipeline.add(element)
        links = list(zip(chain, chain[1:]))
        for queue, extra in branches:
            links += [(tee, queue), (queue, extra)]
        for first, second in links:
            if not first.link(second):
                log.warning(f'[mic] cannot link {first.get_name()} to {second.get_name()}')
                return
        self.pipeline, self._volume, self._echo, self._monitor_volume = pipeline, volume, echo, monitor
        self.set_monitor(self.monitor)
        self.set_level(self.level)
        self.set_mute(self.muted)
        self.set_echo(self.echo)
        if shared:
            pipeline.add_pad(Gst.GhostPad.new('src', chain[-1].get_static_pad('src')))
            if source.get_factory().get_name() == 'osxaudiosrc':
                self._stamp_source(source)
            self._output = _Output.get(self)
            if not self._output.add(self, pipeline):
                log.warning(f'[mic] {self.input} does not start')
                self.stop()
                return
            sink = self._output.sink
        elif pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            log.warning(f'[mic] {self.input} does not start')
            self.stop()
            return
        log.info(f'[mic] {self.input} ({source.get_factory().get_name()}) to {self.output or "the system default"} '
                 f'({sink.get_factory().get_name()}{", mixed with the others there" if shared else ""})')
        if not shared:
            self._timer.start()
        if self._is_input():
            self._last_level, self._dead_since = time.monotonic(), None
            self._watchdog.start()
        # what was negotiated, once running: the source's format and the sink's (a quality problem's first clue)
        def negotiated(pipeline=pipeline):
            if pipeline is not self.pipeline:
                return
            pads = [('from', source.get_static_pad('src')), ('to', sink.get_static_pad('sink'))]
            formats = [f'{what} {pad.get_current_caps().to_string() if pad and pad.get_current_caps() else "?"}' for what, pad in pads]
            log.info(f'[mic] {self.input}: {"; ".join(formats)}; source buffer {self.buffer or self.latency * 2} ms '
                     f'in {self.latency} ms quanta')
        QTimer.singleShot(2000, negotiated)

    def _is_input(self):
        """An input device (a mic, a line in), not what Blink plays, nor an app's sound."""
        return bool(self.input) and self.link_source is None and self.device_id is None and not self.pulse_source \
            and not self.stereo

    def _heard(self, rms):
        """A level message: buffers flow; digital zero on every channel noted (the watchdog's DEAD_AFTER)."""
        now = time.monotonic()
        self._last_level = now
        if rms and all(value != value or value <= DEAD_SILENCE for value in rms):
            if self._dead_since is None:
                self._dead_since = now
        else:
            self._dead_since = None
            if self._stalls and any(value == value and value > MONO_SILENCE for value in rms):
                log.info(f'[mic] {self.input}: sound again, after being made anew {self._stalls} time(s)')
                self._stalls = 0

    def _watch(self):
        """An input opened, or gone, silent (no buffers, or digital zeros): made anew, as choosing it again did."""
        if self.pipeline is None or self._last_level is None:
            return
        now = time.monotonic()
        if now - self._last_level >= STALL_AFTER:
            why = f'no sound from it for {now - self._last_level:.1f} s (stalled)'
        elif self._dead_since is not None and now - self._dead_since >= DEAD_AFTER:
            why = f'digital silence for {now - self._dead_since:.1f} s (opened dead)'
        else:
            return
        if self._stalls >= STALL_RETRIES:
            self._watchdog.stop()
            log.warning(f'[mic] {self.input}: {why}, still after {self._stalls} times made anew: left as it is')
            return
        self._stalls += 1
        log.warning(f'[mic] {self.input}: {why}: made anew ({self._stalls}/{STALL_RETRIES})')
        QTimer.singleShot(0, self._rebuild)

    def _check_mono(self, rms):
        """Sound on the left, silence on the right for a second: a mono input opened as two channels; opened again
        with one (remembered for that device)."""
        if getattr(self, '_mono', True) or len(rms) < 2 or not self._is_input():
            return
        left, right = rms[0], rms[1]
        if left > MONO_SIGNAL and (right != right or right < MONO_SILENCE):     # (NaN: silence too)
            self._left_only += 1
        elif right == right and right > MONO_SILENCE:
            self._left_only = 0
        if self._left_only >= MONO_AFTER:
            self._mono = True
            _remember_mono(self.input)
            log.info(f'[mic] {self.input}: sound on the left only: a mono input, opened again with one channel (on both sides)')
            QTimer.singleShot(0, self._rebuild)

    def _sink(self):
        if self.output:
            device = None
            try:
                device = next((d for d in _player.audio_devices('Audio/Sink') if d.get_display_name() == self.output), None)
            except Exception as e:
                log.warning(f'[mic] cannot list the outputs: {e}')
            if device is not None:
                return _pulse_element(device, 'pulsesink') or device.create_element(None)
        return _player._audio_sink()

    RESTAMP_DRIFT = 50      # ms: a mic's own stamps this far from when its buffers come: stamped anew from there

    def _stamp_source(self, source):
        """A device's buffers stamped by the output's clock (macOS, a shared output): the first as it comes, the
        next ones following on by their durations (no jitter), stamped anew past RESTAMP_DRIFT. osxaudiosrc slaved
        to an output's clock it did not provide (a mic joining an output Blink's call started: the sink's clock)
        stamped every buffer 0: the output's mixer dropped them all as late, the mic counted on the meters but never
        heard (GST_DEBUG=aggregator:5, its pad's buffers all 'pts 0:00:00')."""
        state = {'next': None, 'resyncs': 0}

        def probe(pad, info):
            buffer = info.get_buffer()
            output = self._output
            clock = output.pipeline.get_clock() if output is not None else None
            if buffer is None or clock is None:
                return Gst.PadProbeReturn.OK
            duration = buffer.duration if buffer.duration != Gst.CLOCK_TIME_NONE else 0
            arrived = clock.get_time() - output.pipeline.get_base_time() - duration     # (captured just before)
            stamp = state['next']
            if stamp is None or abs(stamp - arrived) > self.RESTAMP_DRIFT * Gst.MSECOND:
                if stamp is not None:
                    state['resyncs'] += 1
                    if state['resyncs'] <= 3 or state['resyncs'] % 100 == 0:
                        log.info(f'[mic] {self.input}: stamped anew, {(stamp - arrived) / Gst.MSECOND:+.0f} ms off ({state["resyncs"]})')
                stamp = max(0, arrived)
            try:
                buffer.pts = stamp
                buffer.dts = Gst.CLOCK_TIME_NONE
            except Exception as e:      # (not writable here: said once, left as it is)
                if not state.get('failed'):
                    state['failed'] = True
                    log.warning(f'[mic] {self.input}: cannot stamp its buffers: {e}')
                return Gst.PadProbeReturn.OK
            state['next'] = stamp + duration
            return Gst.PadProbeReturn.OK

        source.get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER, probe)

    def _poll(self):
        if self.pipeline is None:
            return
        bus = self.pipeline.get_bus()
        while self.pipeline is not None:
            message = bus.pop()
            if message is None:
                return
            self._message(message)

    def _message(self, message):
        """A message of its pipeline (or, macOS, of its bin in the output's: _Output._poll)."""
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            log.warning(f'[mic] {error.message}')
            self.stop()
            return
        if message.type == Gst.MessageType.WARNING:     # (a source not read fast enough, a sink starved: crackles)
            warning, debug = message.parse_warning()
            self._warnings = getattr(self, '_warnings', 0) + 1
            if self._warnings <= 5 or self._warnings % 50 == 0:
                log.warning(f'[mic] {self.input}: {warning.message} ({self._warnings})')
        structure = message.get_structure()
        if message.type == Gst.MessageType.ELEMENT and structure is not None and structure.get_name() == 'level':
            rms = _player._doubles(structure, 'rms')
            self._heard(rms)
            self._check_mono(rms)
            self.levels.emit(rms)


# macOS: the Mics of one output (the mic, an app's capture, Blink's call) played by one pipeline, their chains bins
# mixed by an audiomixer into one osxaudiosink. Each with an osxaudiosink of its own, an app's capture (taps.py)
# crackled while the mic played on the same output (python3-sipsimple docs/PodcastMixerLink.md).
SHARED_OUTPUT = sys.platform == 'darwin' and os.environ.get('AG_CUTEMUSIC_SHARED_OUTPUT') != 'off'


class _Output:
    CAPS = 'audio/x-raw,format=F32LE,rate=48000,channels=2,layout=interleaved'
    MIXER_LATENCY = 20          # ms: how long the mixer waits for a late input (the mic sings within it)
    _outputs = {}               # output name ('': the system default): _Output

    @classmethod
    def get(cls, mic):
        key = mic.output or ''
        output = cls._outputs.get(key)
        if output is None:
            output = cls._outputs[key] = cls(key, mic._sink())
        return output

    def __init__(self, key, sink):
        self.key, self.sink = key, sink
        self.pipeline = Gst.Pipeline.new(f'output-{key or "default"}')
        # the system clock, never an input's: an osxaudiosrc chosen as the clock and then removed (remove: its bin to
        # NULL, its ring buffer freed) left the mixer's streaming thread waiting on that source's GstAudioClock, which
        # read the freed ring buffer (gst_audio_base_src_get_time): a segmentation fault in audiomixer:src, the
        # CLOCK_LOST restart in _poll coming too late. The sink does not sync and _stamp_source stamps the mics by
        # this clock, so nothing needs the device's own.
        self.pipeline.use_clock(Gst.SystemClock.obtain())
        self.mixer = Gst.ElementFactory.make('audiomixer', None)
        convert = Gst.ElementFactory.make('audioconvert', None)
        self.mixer.set_property('latency', self.MIXER_LATENCY * Gst.MSECOND)
        if sink.find_property('buffer-time') is not None:
            sink.set_property('buffer-time', LATENCY * 3 * 1000)
            sink.set_property('latency-time', LATENCY * 1000)
        if sink.find_property('sync') is not None:
            sink.set_property('sync', False)
        for element in (self.mixer, convert, sink):
            self.pipeline.add(element)
        self.mixer.link(convert)
        convert.link(sink)
        self.mics = {}              # bin: (Mic, the mixer's pad)
        # the application's (not this object's: Python would free it with us, maybe inside its own timeout, a Mic
        # stopping in _poll taking the output's last chain: a segmentation fault), let go with deleteLater
        self._timer = QTimer(QCoreApplication.instance(), interval=25)
        self._timer.timeout.connect(self._poll)

    def add(self, mic, bin_):
        self.pipeline.add(bin_)
        pad = self.mixer.request_pad_simple('sink_%u')
        if bin_.get_static_pad('src').link(pad) != Gst.PadLinkReturn.OK:
            self.mixer.release_request_pad(pad)
            self.pipeline.remove(bin_)
            return False
        self.mics[bin_] = (mic, pad)
        if len(self.mics) == 1:
            if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                self.remove(bin_)
                return False
            self._timer.start()
        elif not bin_.sync_state_with_parent():
            self.remove(bin_)
            return False
        else:
            self._restart(f'{mic.input or "an input"} joined')
        return True

    def _restart(self, why):
        """PLAYING -> PAUSED -> PLAYING: the clock chosen again and the latency worked out again for the inputs there
        now. An input that joined a running output kept the clock and latency chosen for the first one (Blink's
        call's: the sink's clock, its short latency): its mic counted on the meters but was never heard, the mixer
        dropping it as late, until the output was made anew. A clock provider gone (CLOCK_LOST) needs the same."""
        if self.pipeline.set_state(Gst.State.PAUSED) == Gst.StateChangeReturn.FAILURE or \
                self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            log.warning(f'[mic] output {self.key or "the system default"}: cannot restart it ({why})')
            return
        log.info(f'[mic] output {self.key or "the system default"} restarted: {why} (its clock and latency again)')

    def remove(self, bin_):
        """A chain out of the mix: first cut from the mixer (its pad released, which flushes it: a streaming thread
        blocked in the mixer's wait returns), only then stopped. Stopped first, its state change waited on that thread
        for ever: the whole app hung (a capture made again while Blink's call started)."""
        entry = self.mics.pop(bin_, None)
        if entry is not None:
            src = bin_.get_static_pad('src')
            if src is not None:
                src.unlink(entry[1])
            self.mixer.release_request_pad(entry[1])
        bin_.set_state(Gst.State.NULL)
        if bin_.get_parent() is self.pipeline:
            self.pipeline.remove(bin_)
        if not self.mics:       # the last one: the device let go
            self._timer.stop()
            self._timer.timeout.disconnect()
            self._timer.deleteLater()       # (once back in the event loop: maybe in its own timeout now)
            self.pipeline.set_state(Gst.State.NULL)
            self._outputs.pop(self.key, None)

    def _owner(self, element):
        while element is not None:
            if element in self.mics:
                return self.mics[element][0]
            element = element.get_parent()
        return None

    def _poll(self):
        bus = self.pipeline.get_bus()
        while True:
            message = bus.pop()
            if message is None:
                return
            if message.type == Gst.MessageType.LATENCY:         # (an input came or went)
                self.pipeline.recalculate_latency()
                continue
            if message.type == Gst.MessageType.CLOCK_LOST:      # (not with the system clock forced: kept in case)
                self._restart('its clock went with an input')
                continue
            mic = self._owner(message.src)
            if mic is not None:
                mic._message(message)
            elif message.type == Gst.MessageType.ERROR:         # the output itself: every Mic on it stops
                error, debug = message.parse_error()
                log.warning(f'[mic] output {self.key or "the system default"}: {error.message}')
                for mic, pad in list(self.mics.values()):
                    mic.stop()
                return


def _is_pipewire_source(device):
    try:
        element = device.create_element(None)
    except Exception:
        return False
    factory = element.get_factory() if element is not None else None
    return factory is not None and factory.get_name() == 'pipewiresrc'


def _pulse_element(device, factory):
    """pulsesrc / pulsesink on device: itself when it is PulseAudio's, through pipewire-pulse (its node's name) when
    PipeWire's; None when neither."""
    element = device.create_element(None)
    if element is not None and element.get_factory().get_name() == factory:
        return element
    properties = device.get_properties()
    node = properties.get_string('node.name') if properties is not None else None
    pulse = Gst.ElementFactory.make(factory, None) if node else None
    if pulse is None:
        return None
    pulse.set_property('device', node)
    return pulse
