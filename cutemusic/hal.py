"""The AG Podcast Mixer driver's hidden devices (the Feeds), opened by their AudioDeviceID through CoreAudio's own I/O:
in C (_feedio.dylib, macos/feedio/feedio.c: the real-time IOProc and lock-free rings, no Python on CoreAudio's
thread, no added latency), else (not built) a Python IOProc through ctypes, longer cycles: GStreamer's osxaudiosink and osxaudiosrc open only the devices the system lists, and the driver hides the
Feeds from the list so that other apps never offer them (macos/driver/AGPodcastMixer.cpp).

- DeviceIO: an IOProc on a device: its output fed from a queue (output=True), or its input handed to a callback.
- FeedOutput: LinkSender's and the Recorder's destination (the mix to Blink, the mix to "AG Podcast Streaming"):
  S16LE quanta in, float32 to the device (the driver's format: 48 kHz, stereo, float32).
- FeedReceiver: what Blink plays, read from the Feed's input, as an appsrc for the BLINK strip (as LinkReceiver).

The driver's streams are 48 kHz stereo float32 interleaved, one buffer: what the IOProc gets and gives, as is.
"""

import array
import collections
import ctypes
import struct
import sys
import threading
import time

from cutemusic.util import log

__all__ = ['DeviceIO', 'FeedOutput', 'FeedReceiver', 'available']

RATE = 48000
CHANNELS = 2
FRAME_BYTES = CHANNELS * 4        # float32
# The IOProc is Python, on CoreAudio's real-time thread: it needs the GIL, which another thread may hold a few ms. A
# cycle this long leaves it time (5 ms ones came late: the call scratchy); it only copies, a thread of ours does the rest
IO_FRAMES = 1024                  # 21 ms


def available():
    return sys.platform == 'darwin' and _lib() is not None


# The C I/O (macos/feedio/build.sh builds it next to this file): 5 ms cycles, at most 20 ms queued to the device
C_IO_FRAMES = 240
C_MAX_FRAMES = 960
C_RING_FRAMES = 4096
_feedio = None


def _c():
    """_feedio.dylib (ctypes), None when it is not there (not built: the Python IOProc then)."""
    global _feedio
    if _feedio is None:
        import os
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_feedio.dylib')
        try:
            lib = ctypes.CDLL(path)
        except OSError:
            log.warning(f'[hal] {path} missing (macos/feedio/build.sh builds it): the Feeds through Python, longer cycles')
            _feedio = False
            return None
        lib.feedio_open.restype = ctypes.c_void_p
        lib.feedio_open.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                                    ctypes.POINTER(ctypes.c_int32)]
        lib.feedio_close.restype = None
        lib.feedio_close.argtypes = [ctypes.c_void_p]
        lib.feedio_write.restype = ctypes.c_uint32
        lib.feedio_write.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        lib.feedio_read.restype = ctypes.c_uint32
        lib.feedio_read.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
        lib.feedio_queued.restype = ctypes.c_uint32
        lib.feedio_queued.argtypes = [ctypes.c_void_p]
        try:        # (a dylib built before it: no feedio_mix)
            lib.feedio_mix.restype = None
            lib.feedio_mix.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        except AttributeError:
            pass
        _feedio = lib
    return _feedio or None


class _CFeed:
    """A Feed device opened by the C I/O: output (write S16LE frames) or input (read S16LE frames)."""

    def __init__(self, device, output):
        lib = _c()
        status = ctypes.c_int32(0)
        self._io = lib.feedio_open(device, 1 if output else 0, C_IO_FRAMES, C_RING_FRAMES, C_MAX_FRAMES if output else 0,
                                   ctypes.byref(status))
        if not self._io:
            raise OSError(f'cannot open device {device} (OSStatus {status.value})')
        self._lib = lib
        self._buffer = ctypes.create_string_buffer(C_RING_FRAMES * 4)

    def write(self, data):
        return self._lib.feedio_write(self._io, data, len(data) // 4) > 0

    def queued(self):
        return self._lib.feedio_queued(self._io) if self._io else 0

    def read(self):
        frames = self._lib.feedio_read(self._io, self._buffer, C_RING_FRAMES)
        return self._buffer.raw[:frames * 4] if frames else b''

    def close(self):
        if self._io:
            self._lib.feedio_close(self._io)
            self._io = None


class _AudioBuffer(ctypes.Structure):
    _fields_ = [('mNumberChannels', ctypes.c_uint32), ('mDataByteSize', ctypes.c_uint32), ('mData', ctypes.c_void_p)]


class _AudioBufferList(ctypes.Structure):
    _fields_ = [('mNumberBuffers', ctypes.c_uint32), ('mBuffers', _AudioBuffer * 8)]


class _Address(ctypes.Structure):
    _fields_ = [('mSelector', ctypes.c_uint32), ('mScope', ctypes.c_uint32), ('mElement', ctypes.c_uint32)]


_IOProc = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(_AudioBufferList),
                           ctypes.c_void_p, ctypes.POINTER(_AudioBufferList), ctypes.c_void_p, ctypes.c_void_p)

_library = None


def _lib():
    global _library
    if _library is None:
        try:
            ca = ctypes.CDLL('/System/Library/Frameworks/CoreAudio.framework/CoreAudio')
        except OSError:
            _library = False
            return None
        ca.AudioDeviceCreateIOProcID.restype = ctypes.c_int32
        ca.AudioDeviceCreateIOProcID.argtypes = [ctypes.c_uint32, _IOProc, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        ca.AudioDeviceDestroyIOProcID.restype = ctypes.c_int32
        ca.AudioDeviceDestroyIOProcID.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        ca.AudioDeviceStart.restype = ctypes.c_int32
        ca.AudioDeviceStart.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        ca.AudioDeviceStop.restype = ctypes.c_int32
        ca.AudioDeviceStop.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        ca.AudioObjectSetPropertyData.restype = ctypes.c_int32
        ca.AudioObjectSetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32, ctypes.c_void_p,
                                                  ctypes.c_uint32, ctypes.c_void_p]
        _library = ca
    return _library or None


def _fourcc(code):
    return struct.unpack('>I', code.encode('ascii'))[0]


class DeviceIO:
    """An IOProc on device: output (True) plays what write() queued (silence when there is none); input hands each
    buffer (float32 interleaved bytes) to on_input."""

    def __init__(self, device, output, on_input=None, frames=IO_FRAMES):
        ca = _lib()
        if ca is None:
            raise OSError('CoreAudio not there')
        self.device, self.output, self._on_input = device, output, on_input
        self._queue = collections.deque()       # output: bytes chunks, oldest first
        self._queued = 0
        self._lock = threading.Lock()
        self._proc = _IOProc(self._io)          # kept: ctypes must not free it while CoreAudio calls it
        self._id = ctypes.c_void_p()
        # its I/O cycle: 5 ms (frames), the link's quantum
        size = ctypes.c_uint32(frames)
        address = _Address(_fourcc('fsiz'), _fourcc('outp' if output else 'inpt'), 0)
        ca.AudioObjectSetPropertyData(device, ctypes.byref(address), 0, None, ctypes.sizeof(size), ctypes.byref(size))
        status = ca.AudioDeviceCreateIOProcID(device, self._proc, None, ctypes.byref(self._id))
        if status != 0:
            raise OSError(f'cannot open device {device} (OSStatus {status})')
        status = ca.AudioDeviceStart(device, self._id)
        if status != 0:
            ca.AudioDeviceDestroyIOProcID(device, self._id)
            raise OSError(f'cannot start device {device} (OSStatus {status})')
        self._open = True

    @property
    def queued_bytes(self):
        return self._queued

    def write(self, data):
        """float32 interleaved bytes, played next."""
        with self._lock:
            self._queue.append(bytes(data))
            self._queued += len(data)

    def _io(self, device, now, input_data, input_time, output_data, output_time, client):
        try:
            if self.output and output_data:
                buffers = output_data.contents
                for index in range(min(buffers.mNumberBuffers, 8)):
                    buffer = buffers.mBuffers[index]
                    self._fill(buffer.mData, buffer.mDataByteSize)
            elif not self.output and input_data and self._on_input is not None:
                buffers = input_data.contents
                if buffers.mNumberBuffers:
                    buffer = buffers.mBuffers[0]
                    self._on_input(ctypes.string_at(buffer.mData, buffer.mDataByteSize))
        except Exception:
            pass
        return 0

    def _fill(self, address, size):
        """size bytes at address from the queue, silence for what it does not have."""
        offset = 0
        with self._lock:
            while offset < size and self._queue:
                chunk = self._queue[0]
                take = min(len(chunk), size - offset)
                ctypes.memmove(address + offset, chunk, take)
                offset += take
                self._queued -= take
                if take == len(chunk):
                    self._queue.popleft()
                else:
                    self._queue[0] = chunk[take:]
        if offset < size:
            ctypes.memset(address + offset, 0, size - offset)

    def close(self):
        if not getattr(self, '_open', False):
            return
        self._open = False
        ca = _lib()
        ca.AudioDeviceStop(self.device, self._id)
        ca.AudioDeviceDestroyIOProcID(self.device, self._id)


def _s16_to_f32(data):
    samples = array.array('h')
    samples.frombytes(data)
    if sys.byteorder != 'little':
        samples.byteswap()
    return array.array('f', [sample * (1.0 / 32768.0) for sample in samples]).tobytes()


class FeedOutput:
    """A Feed device (its AudioDeviceID, hidden: by UID) as LinkSender's destination, in place of the to-call ring:
    write() takes an S16LE quantum, False (dropped) when the device has not taken the last 40 ms yet."""

    MAX = 3 * IO_FRAMES * FRAME_BYTES       # 64 ms of float32 queued (the device takes 21 ms a cycle)

    def __init__(self, device):
        self._c = _CFeed(device, output=True) if _c() is not None else None
        self._io = None if self._c is not None else DeviceIO(device, output=True)

    def writer_attach(self):
        pass

    def queued(self):
        """Frames written, not played yet: LinkSender writes as the device takes them."""
        if self._c is not None:
            return self._c.queued()
        return self._io.queued_bytes // 8 if self._io is not None else 0       # (float32 stereo)

    def write(self, data):
        if self._c is not None:         # (the C ring: refused when 20 ms are queued, the latency kept)
            return self._c.write(data)
        if self._io.queued_bytes > self.MAX:
            return False
        self._io.write(_s16_to_f32(data))
        return True

    def close(self):
        if self._c is not None:
            self._c.close()
            self._c = None
        if self._io is not None:
            self._io.close()
            self._io = None


class FeedReceiver:
    """What Blink plays, read from the Feed's input (float32 stereo): an appsrc for the BLINK strip (a Mic's
    link_source, as LinkReceiver's). The IOProc only queues what it gets; a thread of ours pushes it into the appsrc
    (GStreamer is not called from CoreAudio's real-time thread)."""

    def __init__(self, device):
        self.device = device
        self._io = None
        self._source = None
        self._queue = collections.deque(maxlen=64)      # the IOProc's buffers (21 ms each: 1.3 s at most)
        self._thread = None
        self._running = False

    def source(self):
        """A new appsrc fed from the Feed's input (the previous one no longer fed); None when it cannot open."""
        from cutemusic import player as _player
        Gst = _player.Gst
        self.stop()
        source = Gst.ElementFactory.make('appsrc', 'feed-from-blink')
        if source is None:
            return None
        c = _c() is not None        # the C I/O gives S16LE, the Python one float32
        source.set_property('caps', Gst.Caps.from_string(
            f'audio/x-raw,format={"S16LE" if c else "F32LE"},rate={RATE},channels={CHANNELS},layout=interleaved'))
        source.set_property('is-live', True)
        source.set_property('format', Gst.Format.TIME)
        source.set_property('do-timestamp', True)
        source.set_property('block', False)
        source.set_property('max-bytes', 8 * IO_FRAMES * FRAME_BYTES)
        self._source = source
        self._queue.clear()
        try:
            if c:
                self._io = _CFeed(self.device, output=False)
            else:
                self._io = DeviceIO(self.device, output=False, on_input=self._queue.append)     # (append: atomic, quick)
        except OSError as e:
            log.warning(f'[hal] cannot read the Feed device {self.device}: {e}')
            self._source = None
            return None
        self._running = True
        self._thread = threading.Thread(target=self._run, args=(source, Gst), name='feed-receive', daemon=True)
        self._thread.start()
        return source

    def _run(self, source, Gst):
        c = isinstance(self._io, _CFeed)
        while self._running and self._source is source:
            if c:       # what the C ring has: every 2 ms (the device's cycles are 5 ms)
                data = self._io.read()
                if data:
                    source.emit('push-buffer', Gst.Buffer.new_wrapped(data))
                time.sleep(0.002)
                continue
            while self._queue:
                data = self._queue.popleft()
                source.emit('push-buffer', Gst.Buffer.new_wrapped(data))
            time.sleep(0.005)

    def stop(self):
        self._running = False
        self._source = None
        if self._thread is not None:        # (first: it reads from the device till then)
            self._thread.join(0.5)
            self._thread = None
        if self._io is not None:
            self._io.close()
            self._io = None
