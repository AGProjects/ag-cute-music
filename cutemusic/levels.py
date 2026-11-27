"""A song's levels worked out ahead, for the mobile app's VU meter (its player gives no levels): the file decoded
as fast as it goes through GStreamer's level and spectrum (as the player's, player.py), the result kept in
~/.ag-cute-music/levels/ by the file's path, size and time. Served by the library on the LAN (sharing.py,
GET /levels?path=).

The JSON: {interval: ms a frame, frames, rms, peak: base64 of a byte a channel a frame (L R, -dB x 2: 0 is
0 dBFS, 120 is -60 and less), bands, low, high (Hz), spectrum: base64 of a byte a band a frame (the strongest
magnitude in each of the bands, equal on a log scale from low to high, -dB x 2: 0 is 0 dB, 180 is -90)}.
"""

import base64
import hashlib
import json
import math
import os
import threading

from cutemusic import util
from cutemusic.util import log

try:
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init([])
except (ImportError, ValueError):
    Gst = None


__all__ = ['levels', 'available']


INTERVAL = 50           # ms a frame: 20 a second (the app's meters ease between them)
FFT_BANDS = 512
BANDS = 32
LOW, HIGH = 30.0, 16000.0
FLOOR, SPECTRUM_FLOOR = -60.0, -90.0
VERSION = 1

_lock = threading.Lock()        # one song at a time (a NAS: not every core for the phones)


def available():
    return Gst is not None and Gst.ElementFactory.find('level') is not None


def _cache_path(path):
    info = os.stat(path)
    key = hashlib.sha1(f'{VERSION}\0{path}\0{info.st_size}\0{info.st_mtime_ns}'.encode()).hexdigest()
    return os.path.join(util.CONFIG_DIR, 'levels', key[:2], key + '.json')


def levels(path):
    """The levels of path as JSON bytes (worked out once, then from the cache); None when it cannot be done."""
    if not available():
        return None
    cache = _cache_path(path)
    try:
        with open(cache, 'rb') as f:
            return f.read()
    except OSError:
        pass
    with _lock:
        try:
            with open(cache, 'rb') as f:        # (worked out meanwhile, for another request)
                return f.read()
        except OSError:
            pass
        try:
            data = json.dumps(_analyze(path), separators=(',', ':')).encode()
        except Exception as e:
            log.warning(f'[levels] {path}: {e}')
            return None
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache + '.part', 'wb') as f:
            f.write(data)
        os.replace(cache + '.part', cache)
        return data


def _byte(db, floor):
    if db is None or math.isnan(db):
        return int(-floor * 2)
    return int(round(min(0.0, max(floor, db)) * -2))


def _analyze(path):
    from cutemusic.player import _doubles
    pipeline = Gst.parse_launch(
        f'filesrc name=src ! decodebin ! audioconvert ! audioresample ! audio/x-raw,channels=2 ! '
        f'level name=level interval={INTERVAL * Gst.MSECOND} post-messages=true peak-ttl=0 ! '
        f'spectrum name=spectrum bands={FFT_BANDS} threshold={int(SPECTRUM_FLOOR)} interval={INTERVAL * Gst.MSECOND} '
        f'post-messages=true message-magnitude=true message-phase=false multi-channel=false ! fakesink sync=false')
    pipeline.get_by_name('src').set_property('location', path)
    rms, peak, spectrum = bytearray(), bytearray(), bytearray()
    rate = 44100
    low, high = math.log2(LOW), math.log2(HIGH)
    edges = [2 ** (low + (high - low) * index / BANDS) for index in range(BANDS + 1)]
    bus = pipeline.get_bus()
    pipeline.set_state(Gst.State.PLAYING)
    try:
        while True:
            message = bus.timed_pop_filtered(60 * Gst.SECOND, Gst.MessageType.ELEMENT | Gst.MessageType.EOS | Gst.MessageType.ERROR)
            if message is None:
                raise TimeoutError('no progress')
            if message.type == Gst.MessageType.EOS:
                break
            if message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                raise RuntimeError(error.message)
            structure = message.get_structure()
            name = structure.get_name() if structure is not None else ''
            if name == 'level':
                values, peaks = _doubles(structure, 'rms'), _doubles(structure, 'peak')
                values, peaks = (values * 2)[:2] if values else [None, None], (peaks * 2)[:2] if peaks else [None, None]
                rms += bytes(_byte(v, FLOOR) for v in values)
                peak += bytes(_byte(v, FLOOR) for v in peaks)
            elif name == 'spectrum':
                if len(spectrum) == 0:
                    pad = pipeline.get_by_name('spectrum').get_static_pad('sink')
                    caps = pad.get_current_caps() if pad is not None else None
                    if caps is not None:
                        ok, value = caps.get_structure(0).get_int('rate')
                        rate = value if ok else rate
                magnitudes = _doubles(structure, 'magnitude') or [SPECTRUM_FLOOR] * FFT_BANDS
                width = rate / 2 / len(magnitudes)
                row = []
                for index in range(BANDS):
                    i0, i1 = edges[index] / width, edges[index + 1] / width
                    if i1 - i0 < 1:         # narrower than an FFT band (the low ones): the one at its centre
                        centre = min(len(magnitudes) - 1.001, max(0.0, (i0 + i1) / 2 - 0.5))
                        below = int(centre)
                        db = magnitudes[below] + (magnitudes[below + 1] - magnitudes[below]) * (centre - below)
                    else:
                        db = max(magnitudes[int(i0):min(len(magnitudes), int(math.ceil(i1)))])
                    row.append(_byte(db, SPECTRUM_FLOOR))
                spectrum += bytes(row)
    finally:
        pipeline.set_state(Gst.State.NULL)
    frames = len(rms) // 2
    return {'version': VERSION, 'interval': INTERVAL, 'frames': frames,
            'rms': base64.b64encode(bytes(rms)).decode(), 'peak': base64.b64encode(bytes(peak)).decode(),
            'bands': BANDS, 'low': LOW, 'high': HIGH, 'spectrum': base64.b64encode(bytes(spectrum)).decode()}
