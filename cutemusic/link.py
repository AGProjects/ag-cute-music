"""The AG Podcast Mixer link (python3-sipsimple docs/PodcastMixerLink.md): the mixer's mix to Blink, through a sound
device Blink takes as its microphone, "AG Podcast Mixer".

- LinkDevices: where the mix goes. macOS: the AG Podcast Mixer driver's Feed device (driver.py), whose "AG Podcast
  Mixer" is Blink's input; what Blink plays on it comes back from the Feed's input (hal.py FeedReceiver), the BLINK
  strip. Linux: the PipeWire sink ag_podcast_mixer_feed (the package's pipewire/ag-podcast-mixer.conf), looped back
  as the source ag_podcast_mixer, Blink's microphone (through its ALSA PCM); what Blink plays goes into
  ag_podcast_mixer_call, whose monitor is the BLINK strip (window.py _watch_blink).
- LinkSender: what Blink gets. The mic and, while TO CALLS is on, the music each feed it from a branch of their own
  pipeline (branch(): audioconvert ! audioresample ! S16LE, 48 kHz, 2 ! appsink); a thread of its own mixes them
  (summed, saturated) and writes the mix to the device in 5 ms quanta (FeedOutput, NodeOutput).

  The music's branch is clocked (appsink sync): it reaches the call as it is heard here, in time with the singer;
  the mic's is not (as it comes).
"""

import array
import ctypes
import threading
import time
import warnings

from cutemusic.util import log
from cutemusic import player as _player

Gst = _player.Gst


__all__ = ['LinkDevices', 'LinkSender', 'FeedOutput', 'NodeOutput', 'available', 'DEVICE', 'RATE', 'CHANNELS', 'QUANTUM',
           'FRAME_BYTES']


DEVICE = 'AG Podcast Mixer'         # Blink's name for it
RATE = 48000
CHANNELS = 2
QUANTUM = 240                       # frames: 5 ms
FRAME_BYTES = CHANNELS * 2          # S16LE
CAPS = 'audio/x-raw,format=S16LE,rate=%d,channels=%d,layout=interleaved' % (RATE, CHANNELS)
QUANTUM_BYTES = QUANTUM * FRAME_BYTES


def available():
    """GStreamer with appsrc / appsink (gst-plugins-base's app plugin): all the link needs."""
    return Gst is not None and all(Gst.ElementFactory.find(name) is not None for name in ('appsrc', 'appsink'))


class LinkDevices:
    """Where the mix goes: the driver's Feed (macOS) or the PipeWire Feed node (Linux)."""

    def __init__(self):
        self.active = False
        self.feed = None            # the driver's Feed (an AudioDeviceID)
        self.node = None            # the PipeWire node

    def start(self, pipewire=False):
        """None, else why not. pipewire: the PipeWire fragment's nodes are there (share.pipewire_nodes())."""
        if self.active:
            return None
        if not available():
            return 'GStreamer appsrc / appsink missing (gst-plugins-base)'
        if pipewire:
            if Gst.ElementFactory.find('pipewiresink') is None:
                return 'GStreamer\'s pipewiresink is missing (gstreamer1.0-pipewire)'
            from cutemusic.share import FEED, SOURCE
            self.node = FEED
            self.active = True
            log.info(f'[link] the mixer plays into the PipeWire node {FEED}: Blink\'s "{DEVICE}" input ({SOURCE})')
            return None
        feed = self._feed()
        if feed is not None:
            self.feed = feed
            self.active = True
            log.info(f'[link] the mixer plays into the AG Podcast Mixer driver (Feed device {feed}): Blink\'s "{DEVICE}" input')
            return None
        import sys
        if sys.platform == 'darwin':
            return 'the AG Podcast Mixer driver is not installed (Play menu: install it)'
        if sys.platform.startswith('linux'):
            return ('the PipeWire nodes are not there (pipewire/ag-podcast-mixer.conf): log out and in again, or '
                    'systemctl --user restart pipewire')
        return 'not on this system'

    def stop(self):
        if self.active:
            log.info('[link] the mixer is no longer Blink\'s device')
        self.active = False
        self.feed = None
        self.node = None

    @staticmethod
    def _feed():
        """The driver's Feed, when it is installed and loaded (macOS) and GStreamer can play into it; else None."""
        from cutemusic import driver
        if not driver.supported() or Gst.ElementFactory.find('osxaudiosink') is None:
            return None
        return driver.feed_device()


class FeedOutput:
    """The driver's Feed as LinkSender's destination: appsrc ! audioconvert !
    osxaudiosink, not synchronised (the quanta come in real time from the mic and the music already); write() drops
    a quantum the sink does not take."""

    MAX = 8         # quanta queued in the appsrc (40 ms)

    def __init__(self, device):
        self.pipeline = Gst.Pipeline.new('link-feed')
        self._source = Gst.ElementFactory.make('appsrc', None)
        convert = Gst.ElementFactory.make('audioconvert', None)
        sink = Gst.ElementFactory.make('osxaudiosink', None)
        if None in (self._source, convert, sink):
            raise ValueError('appsrc, audioconvert or osxaudiosink missing')
        self._source.set_property('caps', Gst.Caps.from_string(CAPS))
        self._source.set_property('is-live', True)
        self._source.set_property('format', Gst.Format.TIME)
        self._source.set_property('do-timestamp', True)
        self._source.set_property('block', False)
        self._source.set_property('max-bytes', self.MAX * QUANTUM_BYTES)
        sink.set_property('device', device)
        sink.set_property('sync', False)
        sink.set_property('buffer-time', 40000)       # us
        sink.set_property('latency-time', 5000)       # one quantum
        for element in (self._source, convert, sink):
            self.pipeline.add(element)
        self._source.link(convert)
        convert.link(sink)
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            why = ''
            message = self.pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
            if message is not None:
                error, debug = message.parse_error()
                why = f': {error.message} ({debug})'
            self.pipeline.set_state(Gst.State.NULL)
            raise OSError(f'cannot play into the Feed device {device}{why}')

    def writer_attach(self):
        pass

    def write(self, data):
        if self._source.get_property('current-level-bytes') + len(data) > self.MAX * QUANTUM_BYTES:
            return False
        return self._source.emit('push-buffer', Gst.Buffer.new_wrapped(data)) == Gst.FlowReturn.OK

    def close(self):
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None


class NodeOutput:
    """A PipeWire sink as LinkSender's destination (Linux, v2: ag_podcast_mixer_feed, whose loopback is
    ag_podcast_mixer, Blink's microphone): appsrc ! audioconvert ! pipewiresink, by node name. Paced by the clock
    (no queued()): pipewiresink takes buffers in bursts of the graph's cycle (often 1024 frames), so
    what waits in the appsrc cannot pace the sender (paced so, it took several quanta at once from inputs that push
    5-10 ms at a time, which ran dry: choppy). The appsrc absorbs the bursts; write() drops a quantum the sink does
    not take (clock drift, now and then)."""

    MAX = 16        # quanta queued in the appsrc (80 ms: more than a graph cycle)

    def __init__(self, node):
        self.pipeline = Gst.Pipeline.new('link-node')
        self._source = Gst.ElementFactory.make('appsrc', None)
        convert = Gst.ElementFactory.make('audioconvert', None)
        sink = Gst.ElementFactory.make('pipewiresink', None)
        if None in (self._source, convert, sink):
            raise ValueError('appsrc, audioconvert or pipewiresink missing')
        self._source.set_property('caps', Gst.Caps.from_string(CAPS))
        self._source.set_property('is-live', True)
        self._source.set_property('format', Gst.Format.TIME)
        self._source.set_property('do-timestamp', True)
        self._source.set_property('block', False)
        self._source.set_property('max-bytes', self.MAX * QUANTUM_BYTES)
        if sink.find_property('target-object') is not None:     # PipeWire 0.3.64+
            sink.set_property('target-object', node)
        else:
            sink.set_property('path', node)
        sink.set_property('client-name', 'AG CuteMusic')
        sink.set_property('stream-properties', Gst.Structure.new_from_string(
            'props,media.role=Production,node.dont-reconnect=true,node.dont-fallback=true,node.latency=240/48000'))
        sink.set_property('sync', False)
        for element in (self._source, convert, sink):
            self.pipeline.add(element)
        self._source.link(convert)
        convert.link(sink)
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            why = ''
            message = self.pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
            if message is not None:
                error, debug = message.parse_error()
                why = f': {error.message} ({debug})'
            self.pipeline.set_state(Gst.State.NULL)
            raise OSError(f'cannot play into the PipeWire node {node}{why}')

    def writer_attach(self):
        pass

    def write(self, data):
        if self._source.get_property('current-level-bytes') + len(data) > self.MAX * QUANTUM_BYTES:
            return False
        return self._source.emit('push-buffer', Gst.Buffer.new_wrapped(data)) == Gst.FlowReturn.OK

    def close(self):
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None


class _Input:
    """One input's PCM, as pushed, waiting to be mixed."""

    def __init__(self):
        self.data = bytearray()
        self.last = 0.0         # when it last pushed (monotonic)
        self.chunk = 0          # quanta: the most it pushed at once (its buffering: _take_clocked)
        self.primed = False     # buffered enough to be mixed


class LinkSender:
    """The mix sent to Blink: inputs pushed by branch()es, mixed here (no GStreamer mixer: the app plugin is all the
    link needs), written to the device in 5 ms quanta by a thread of its own."""

    INPUTS = ('mic', 'music')
    BRANCH = 'link'         # its branches' names: link-<input>-branch
    ACTIVE = 0.25           # s: an input that pushed in this time is mixed; one that stopped, forgotten
    # each input 3 dB down before the sum: headroom for Opus, whose decoding overshoots full-scale music (mastered at
    # 0 dBFS, sent at the top of its fader: about 20 % over at the other end, clipped in its 16-bit decoding)
    HEADROOM = 0.7
    MAX = 64                # quanta kept per input (320 ms: a decoder's 85 ms pushes, buffered twice), the oldest
                            # dropped beyond

    def __init__(self):
        self.pipeline = None        # (kept as the "started" flag: set while sending)
        self._inputs = {}
        self._output = None
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        self.written = self.dropped = 0         # quanta
        self.cut = False        # HANG UP pressed: silence sent, whatever Blink does, till that call is gone (window.py)

    def start(self, feed=None, node=None):
        """feed: the driver's Feed device (LinkDevices.feed) to play the mix into; node: the PipeWire node
        (LinkDevices.node)."""
        if self._running:
            return True
        if feed is None and node is None:
            return False
        destination = 'the Feed device' if feed is not None else f'the PipeWire node {node}'
        try:
            if feed is not None:        # the driver's Feed, hidden: through CoreAudio by its id (hal.py)
                from cutemusic.hal import FeedOutput as _HalFeedOutput
                self._output = _HalFeedOutput(feed)
            else:
                self._output = NodeOutput(node)
        except (OSError, ValueError) as e:
            log.warning(f'[link] cannot open {destination}: {e}')
            return False
        self._output.writer_attach()
        self._inputs = {name: _Input() for name in set(self.INPUTS) | set(self._inputs)}     # (the input strips' too)
        self._running = True
        self._thread = threading.Thread(target=self._run, name='link-send', daemon=True)
        self._thread.start()
        self.pipeline = True
        log.info(f'[link] sending the mix to Blink ({destination})')
        return True

    def stop(self):
        if self._running:
            self._running = False
            self._thread.join(1.0)
            log.info(f'[link] no longer sending ({self.written} quanta written, {self.dropped} not taken by Blink)')
        self._thread = None
        self.pipeline = None
        if self._output is not None:
            self._output.close()
            self._output = None

    def push(self, name, data):
        """An input's PCM (S16LE, 48 kHz, stereo), from its branch's streaming thread."""
        grew = None
        with self._lock:
            entry = self._inputs.get(name)
            if entry is None:
                return
            entry.data += data
            size = -(-len(data) // QUANTUM_BYTES)
            if size > entry.chunk:
                entry.chunk = min(size, self.MAX // 2)
                grew = entry.chunk
            excess = len(entry.data) - self.MAX * QUANTUM_BYTES
            if excess > 0:      # the oldest dropped, on a frame's boundary
                del entry.data[:excess + (-excess % FRAME_BYTES)]
            entry.last = time.monotonic()
        if grew is not None:
            log.info(f'[link] {name}: pushes up to {grew * QUANTUM * 1000 // RATE} ms at a time')

    # The mix kept in time by its destination, not by its inputs: a quantum each time the Feed (Blink's input) has
    # taken one, each input's next quantum in it when it has one. An input is buffered a push deep and a quantum more
    # before it is heard (the mic's 10 ms, an app's tap 40 ms: in bursts, each on its own clock); one running ahead
    # (its clock faster) loses a frame a quantum till it is back under its high mark; one that ran dry is buffered
    # again: no input waits for another, none piles up and is cut, the Feed never refuses a write.
    PREFILL = 1         # quanta: the least an input is buffered before it is mixed (its pushes' size + 1 above it)
    SLACK = 4           # quanta above that before a frame is dropped per quantum (clock drift)
    TARGET = 2 * QUANTUM       # frames queued in the Feed: kept at 10 ms (its I/O takes 5 ms a cycle)

    def _take_clocked(self):
        """The next quantum's parts: each active input's next quantum (none from one still buffering)."""
        now = time.monotonic()
        parts = []
        with self._lock:
            for entry in self._inputs.values():
                if now - entry.last >= self.ACTIVE:     # it stopped: what it left dropped, buffered again next time
                    if entry.data:
                        del entry.data[:]
                    entry.primed = False
                    continue
                prefill = max(self.PREFILL, entry.chunk + 1)
                if not entry.primed:
                    if len(entry.data) < prefill * QUANTUM_BYTES:
                        continue
                    entry.primed = True
                # ahead: one frame less. Above what one push adds (a decoder's 85 ms at once lifts it that much
                # every time: dropping there was a click every quantum, a buzz)
                if len(entry.data) > (prefill + max(self.SLACK, entry.chunk)) * QUANTUM_BYTES:
                    del entry.data[:FRAME_BYTES]
                if len(entry.data) >= QUANTUM_BYTES:
                    parts.append(bytes(entry.data[:QUANTUM_BYTES]))
                    del entry.data[:QUANTUM_BYTES]
                else:       # it ran dry: what is there, silence after it, buffered again
                    parts.append(bytes(entry.data) + bytes(QUANTUM_BYTES - len(entry.data)))
                    del entry.data[:]
                    entry.primed = False
        return parts

    def _run(self):
        period = QUANTUM / RATE
        silence = bytes(QUANTUM_BYTES)
        queued = getattr(self._output, 'queued', None)       # the Feed: paced by what it took; else by the clock
        started, done = time.monotonic(), 0
        while self._running:
            if queued is not None:
                due = max(0, (self.TARGET - queued()) // QUANTUM)
            else:
                due = int((time.monotonic() - started) / period) - done
                if due > 200:
                    started, done, due = time.monotonic(), 0, 1
            for _ in range(due):
                data = _mix(self._take_clocked()) or silence
                if self.cut:
                    data = silence
                if self._output.write(data):
                    self.written += 1
                else:
                    self.dropped += 1       # the device did not take it
                done += 1
            time.sleep(0.001 if queued is not None else period / 2)

    def branch(self, name, clocked):
        """A Bin (a sink pad) for a tee in the input's own pipeline: converted to the link's format and pushed here.
        clocked: in time with that pipeline's clock (the music, as heard), else as it comes (the mic)."""
        with self._lock:        # an input strip's mic ('mic-MIC#2'...): an input of the mix from now on
            self._inputs.setdefault(name, _Input())
        convert = Gst.ElementFactory.make('audioconvert', None)
        resample = Gst.ElementFactory.make('audioresample', None)
        caps = Gst.ElementFactory.make('capsfilter', None)
        sink = Gst.ElementFactory.make('appsink', None)
        if None in (convert, resample, caps, sink):
            log.warning(f'[link] cannot build the {name} branch')
            return None
        caps.set_property('caps', Gst.Caps.from_string(CAPS))
        headroom = None
        if self.HEADROOM != 1.0:
            headroom = Gst.ElementFactory.make('volume', None)
            if headroom is not None:
                headroom.set_property('volume', self.HEADROOM)
        sink.set_property('sync', clocked)
        sink.set_property('emit-signals', True)
        sink.set_property('max-buffers', 20)
        sink.set_property('drop', True)
        sink.connect('new-sample', lambda appsink: self._branch_sample(name, appsink))
        bin_ = Gst.Bin.new(f'{self.BRANCH}-{name}-branch')       # (the link's and the recorder's apart: one name each)
        elements = [convert] + ([headroom] if headroom is not None else []) + [resample, caps, sink]
        for element in elements:
            bin_.add(element)
        for left, right in zip(elements, elements[1:]):
            left.link(right)
        bin_.add_pad(Gst.GhostPad.new('sink', convert.get_static_pad('sink')))
        return bin_

    def _branch_sample(self, name, appsink):
        sample = appsink.emit('pull-sample')
        if sample is not None:
            buffer = sample.get_buffer()
            self.push(name, buffer.extract_dup(0, buffer.get_size()))
        return Gst.FlowReturn.OK


with warnings.catch_warnings():
    warnings.simplefilter('ignore', DeprecationWarning)
    try:
        import audioop      # Python <= 3.12: a C sum of two s16 buffers, saturated
    except ImportError:
        audioop = None

try:
    import numpy        # (where there is no _feedio and no audioop: Linux and Windows on Python 3.13+)
except ImportError:
    numpy = None


_c_lib = None


def _c_mix():
    """_feedio.dylib when it has feedio_mix, else None."""
    global _c_lib
    if _c_lib is None:
        try:
            from cutemusic import hal
            lib = hal._c()
            _c_lib = lib if lib is not None and hasattr(lib, 'feedio_mix') else False
        except Exception:
            _c_lib = False
    return _c_lib or None


def _mix(parts):
    """The quanta summed (s16le, saturated); None when there are none."""
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    lib = _c_mix()
    if lib is not None:         # in C (feedio.c): audioop is gone from Python 3.13
        mixed = ctypes.create_string_buffer(parts[0], len(parts[0]))
        for part in parts[1:]:
            lib.feedio_mix(mixed, part, len(part) // 2)
        return mixed.raw
    if audioop is not None:
        mixed = parts[0]
        for part in parts[1:]:
            mixed = audioop.add(mixed, part, 2)
        return mixed
    if numpy is not None:
        total = numpy.frombuffer(parts[0], dtype=numpy.int16).astype(numpy.int32)
        for part in parts[1:]:
            total += numpy.frombuffer(part, dtype=numpy.int16)
        return numpy.clip(total, -32768, 32767).astype(numpy.int16).tobytes()
    total = array.array('h', parts[0])
    for part in parts[1:]:
        other = array.array('h', part)
        for i, sample in enumerate(other):
            value = total[i] + sample
            total[i] = 32767 if value > 32767 else -32768 if value < -32768 else value
    return total.tobytes()
