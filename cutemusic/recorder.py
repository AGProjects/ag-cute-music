"""The mixer's RECORDER: everything the strips play, mixed into one file, and (STREAMING On) into the driver's
"AG Podcast Streaming" input for a streaming app.

Each strip's pipeline has a branch to the Recorder from the start (Mic.set_recorder, Player.set_recorder): the
inputs (MIC/LINE ...), the Local Library, the app AUDIO APPS plays, Blink's call. While nothing records, what they push
is dropped (LinkSender keeps a few quanta per input, no more). Recording, they are mixed as the link mixes what goes to
Blink (link.py: summed, saturated, 5 ms quanta) and written into a file (FileWriter: FLAC, else MP3, else WAV);
silence is written while no strip plays, so the recording keeps the time it was made in.

Saved in the music folder, <artist>/<genre>/<title>, and tagged (mutagen) so that it shows in the Local Library:
during a Blink call, genre Blink Calls, artist the party's SIP URI; else genre My Memos, artist the Mac's user name;
the title its start, YYYY-MM-DD HH:MM:SS.
"""

import os
import threading
import time

from cutemusic.util import log
from cutemusic import player as _player
from cutemusic.link import CAPS, FRAME_BYTES, QUANTUM, QUANTUM_BYTES, RATE, LinkSender, _mix

Gst = _player.Gst

__all__ = ['Recorder', 'FileWriter']


# encoders tried in turn: (element, extension)
ENCODERS = (('flacenc', '.flac'), ('lamemp3enc', '.mp3'), ('wavenc', '.wav'))


def extension():
    """The file extension recordings get here (the first encoder there), None: none at all."""
    if Gst is None:
        return None
    return next((suffix for name, suffix in ENCODERS if Gst.ElementFactory.find(name) is not None), None)


class FileWriter:
    """appsrc ! audioconvert ! encoder ! filesink: LinkSender's destination (in place of the device), every quantum
    taken (block), timestamped by its frames."""

    def __init__(self, path):
        self.path = path
        encoder = next((Gst.ElementFactory.make(name, None) for name, suffix in ENCODERS
                        if path.endswith(suffix) and Gst.ElementFactory.find(name) is not None), None)
        self.pipeline = Gst.Pipeline.new('recorder')
        self._source = Gst.ElementFactory.make('appsrc', None)
        convert = Gst.ElementFactory.make('audioconvert', None)
        sink = Gst.ElementFactory.make('filesink', None)
        if None in (self._source, convert, encoder, sink):
            raise ValueError('appsrc, audioconvert, an encoder or filesink missing')
        self._source.set_property('caps', Gst.Caps.from_string(CAPS))
        self._source.set_property('format', Gst.Format.TIME)
        self._source.set_property('block', True)
        self._source.set_property('max-bytes', 400 * QUANTUM_BYTES)        # 2 s
        sink.set_property('location', path)
        for element in (self._source, convert, encoder, sink):
            self.pipeline.add(element)
        self._source.link(convert)
        convert.link(encoder)
        encoder.link(sink)
        self._frames = 0
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.pipeline.set_state(Gst.State.NULL)
            raise OSError(f'cannot write {path}')

    def writer_attach(self):
        pass

    def write(self, data):
        frames = len(data) // FRAME_BYTES
        buffer = Gst.Buffer.new_wrapped(data)
        buffer.pts = self._frames * Gst.SECOND // RATE
        buffer.duration = frames * Gst.SECOND // RATE
        self._frames += frames
        return self._source.emit('push-buffer', buffer) == Gst.FlowReturn.OK

    @property
    def seconds(self):
        return self._frames / RATE

    def close(self):
        """The end of the stream written (the encoder's last frames, the file's header), then down."""
        if self.pipeline is None:
            return
        self._source.emit('end-of-stream')
        message = self.pipeline.get_bus().timed_pop_filtered(5 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        if message is not None and message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            log.warning(f'[recorder] {self.path}: {error.message}')
        self.pipeline.set_state(Gst.State.NULL)
        self.pipeline = None


class Recorder(LinkSender):
    """The strips' branches mixed (LinkSender's mixing) while it records into a file (a FileWriter) or streams (a
    FeedOutput into the driver's "AG Podcast Streaming Feed": "AG Podcast Streaming" then carries it), or both."""

    INPUTS = ()
    BRANCH = 'recorder'     # its branches' names: recorder-<input>-branch, apart from the link's in the same pipeline
    HEADROOM = 1.0          # recorded as heard (the link's 3 dB down is for Opus)

    def __init__(self):
        super().__init__()
        self.path = None
        self.started = None         # time.monotonic() when it started recording, None: not recording
        self._file = None           # the FileWriter while recording
        self._stream = None         # the FeedOutput while streaming

    @property
    def recording(self):
        return self._file is not None

    @property
    def streaming(self):
        return self._stream is not None

    def _mixing(self, on):
        """The mixing thread: running while something takes the mix."""
        if on and not self._running:
            with self._lock:
                self._inputs = {name: type(entry)() for name, entry in self._inputs.items()}      # nothing from before
            self._running = True
            self._thread = threading.Thread(target=self._run, name='recorder', daemon=True)
            self._thread.start()
            self.pipeline = True
        elif not on and self._running:
            self._running = False
            self._thread.join(2.0)
            self._thread = None
            self.pipeline = None

    last_seconds = 0.0

    @property
    def position(self):
        """How far the recording is, in seconds (as written into the file); None when it is not recording."""
        with self._lock:
            writer = self._file
        return writer.seconds if writer is not None else None

    def start(self, path):
        """Recording into path (its folder made); True when it started."""
        if self._file is not None:
            return True
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            writer = FileWriter(path)
        except (OSError, ValueError) as e:
            log.warning(f'[recorder] cannot record into {path}: {e}')
            return False
        self.path = path
        self.started = time.monotonic()
        self._recorded = 0          # quanta written into it (silence padded to the time it records for)
        with self._lock:
            self._file = writer
        self._mixing(True)
        log.info(f'[recorder] recording into {path}')
        return True

    def stop(self):
        """Recording stopped, the file finished; its path (None: it was not recording)."""
        if self._file is None:
            return None
        with self._lock:
            writer, self._file = self._file, None
        if self._stream is None:
            self._mixing(False)
        seconds = writer.seconds
        self.last_seconds = seconds         # (the recording's length: the last marker's chapter ends there)
        writer.close()
        self.started = None
        log.info(f'[recorder] {self.path}: {seconds:.1f} s recorded')
        return self.path

    def start_streaming(self, device=None, node=None):
        """The mix played into the streaming Feed: the driver's device (macOS, its AudioDeviceID) or the PipeWire sink
        (Linux, node: ag_podcast_streaming_feed); True when it started."""
        if self._stream is not None:
            return True
        try:
            if node is not None:
                from cutemusic.link import NodeOutput
                output = NodeOutput(node)
            else:
                from cutemusic.hal import FeedOutput         # (hidden: by its id, through CoreAudio)
                output = FeedOutput(device)
        except (OSError, ValueError) as e:
            log.warning(f'[recorder] cannot stream: {e}')
            return False
        with self._lock:
            self._stream = output
        self._mixing(True)
        log.info(f'[recorder] streaming (AG Podcast Streaming, {"the PipeWire node " + node if node else f"Feed device {device}"})')
        return True

    def stop_streaming(self):
        if self._stream is None:
            return
        with self._lock:
            output, self._stream = self._stream, None
        if self._file is None:
            self._mixing(False)
        output.close()
        log.info('[recorder] no longer streaming')

    PREFILL = 4         # quanta (20 ms): the least an input is buffered before it is mixed (LinkSender's mixing)
    MAX = 60            # quanta kept per input (300 ms), the oldest dropped beyond (push)

    def _write(self, data):
        with self._lock:
            writer, stream = self._file, self._stream
        if writer is not None and writer.write(data):
            self._recorded += 1
        if stream is not None:
            stream.write(data)          # (dropped when the streaming app does not take it)

    def _run(self):
        period = QUANTUM / RATE
        silence = bytes(QUANTUM_BYTES)
        started, done = time.monotonic(), 0
        while self._running:
            due = int((time.monotonic() - started) / period) - done
            if due > 200:           # a second behind (the machine asleep): from now on, not that second's catch-up
                started, done, due = time.monotonic(), 0, 1
            for _ in range(due):
                self._write(_mix(self._take_clocked()) or silence)
                done += 1
            time.sleep(period / 2)


def tag(path, title, artist, genre):
    """The recording's tags, as the Local Library reads them (mutagen); False when they could not be written."""
    try:
        import mutagen
    except ImportError:
        return False
    try:
        audio = mutagen.File(path, easy=True)
        if audio is None:
            return False
        if audio.tags is None:
            audio.add_tags()
        audio['title'] = title
        audio['artist'] = artist
        audio['genre'] = genre
        audio['date'] = time.strftime('%Y')      # (the title may be any text: typed on the tape's label)
        audio.save()
        return True
    except Exception as e:
        log.warning(f'[recorder] cannot tag {path}: {e}')
        return False


def write_markers(path, markers, length=None):
    """The markers ([(seconds, name)]) as chapters in the recording, in place of any it had: Vorbis comments in FLAC
    (CHAPTER001=hh:mm:ss.mmm, CHAPTER001NAME), ID3 CHAP frames under a CTOC in MP3 (each to the next marker, the last
    to length: None, the file's); none in WAV (no such tags). False when they could not be written."""
    try:
        import mutagen
    except ImportError:
        return False

    def stamp(seconds):
        milliseconds = int(round(seconds * 1000))
        return f'{milliseconds // 3600000:02d}:{milliseconds // 60000 % 60:02d}:{milliseconds // 1000 % 60:02d}.{milliseconds % 1000:03d}'
    try:
        if path.endswith('.flac'):
            from mutagen.flac import FLAC
            audio = FLAC(path)
            if audio.tags is None:
                audio.add_tags()
            for key in [key for key in audio.tags.keys() if key.upper().startswith('CHAPTER')]:
                del audio.tags[key]
            for number, (seconds, name) in enumerate(markers, 1):
                audio[f'CHAPTER{number:03d}'] = stamp(seconds)
                audio[f'CHAPTER{number:03d}NAME'] = name
            audio.save()
        elif path.endswith('.mp3'):
            from mutagen.id3 import CHAP, CTOC, CTOCFlags, ID3, TIT2
            from mutagen.mp3 import MP3
            if length is None:
                length = MP3(path).info.length
            tags = ID3(path)
            tags.delall('CHAP')
            tags.delall('CTOC')
            if markers:
                ids = []
                ends = [seconds for seconds, name in markers[1:]] + [max(length, markers[-1][0])]
                for number, ((seconds, name), end) in enumerate(zip(markers, ends), 1):
                    element = f'chp{number}'
                    ids.append(element)
                    tags.add(CHAP(element_id=element, start_time=int(seconds * 1000), end_time=int(end * 1000),
                                  sub_frames=[TIT2(encoding=3, text=[name])]))
                tags.add(CTOC(element_id='toc', flags=CTOCFlags.TOP_LEVEL | CTOCFlags.ORDERED, child_element_ids=ids,
                              sub_frames=[TIT2(encoding=3, text=['Markers'])]))
            tags.save(path)
        else:
            if markers:
                log.info(f'[recorder] {path}: no chapters in this format, {len(markers)} markers not kept')
            return False
        log.info(f'[recorder] {path}: {len(markers)} markers as chapters ({", ".join(stamp(seconds) for seconds, name in markers)})')
        return True
    except Exception as e:
        log.warning(f'[recorder] cannot write the markers into {path}: {e}')
        return False


VORBIS_COMMENTS = ('.flac', '.ogg', '.oga', '.opus')      # chapters as CHAPTERnnn / CHAPTERnnnNAME comments
MP4 = ('.m4a', '.m4b', '.mp4', '.aac', '.m4p')
MATROSKA = ('.mka', '.mkv', '.webm')


def read_markers(path):
    """The chapters of a file as markers, [(seconds, name)] in order: CHAPTERnnn Vorbis comments (FLAC, Ogg Vorbis,
    Opus), ID3 CHAP frames (MP3), Nero chapters (M4A, M4B: audiobooks, podcasts); else what GStreamer reads as the
    file's table of contents (QuickTime chapter tracks, Matroska); [] for none, or a stream."""
    if not path or not os.path.isfile(path):      # (not a stream's)
        return []
    lowered = path.lower()
    markers = []
    try:
        import mutagen
    except ImportError:
        mutagen = None
    if mutagen is not None:
        try:
            if lowered.endswith(VORBIS_COMMENTS):
                tags = mutagen.File(path).tags or {}
                keys = {key.upper(): key for key in tags.keys()}
                numbers = sorted({key[7:10] for key in keys if key.startswith('CHAPTER') and key[7:10].isdigit() and len(key) == 10})
                for number in numbers:
                    value = (tags.get(keys[f'CHAPTER{number}']) or [''])[0]
                    name_key = keys.get(f'CHAPTER{number}NAME')
                    name = ((tags.get(name_key) if name_key else None) or [f'Chapter {int(number)}'])[0]
                    try:
                        hours, minutes, seconds = value.split(':')
                        markers.append((int(hours) * 3600 + int(minutes) * 60 + float(seconds), name))
                    except ValueError:
                        continue
            elif lowered.endswith('.mp3'):
                from mutagen.id3 import ID3
                for frame in ID3(path).getall('CHAP'):
                    titles = [sub.text[0] for sub in frame.sub_frames.values() if getattr(sub, 'FrameID', '') == 'TIT2' and sub.text]
                    markers.append((frame.start_time / 1000.0, titles[0] if titles else frame.element_id))
            elif lowered.endswith(MP4):
                from mutagen.mp4 import MP4 as _MP4
                for index, chapter in enumerate(getattr(_MP4(path), 'chapters', None) or []):       # (mutagen 1.45+)
                    markers.append((float(chapter.start), chapter.title or f'Chapter {index + 1}'))
        except Exception as e:
            log.warning(f'[recorder] cannot read the chapters of {path}: {e}')
            markers = []
    if not markers and lowered.endswith(MP4 + MATROSKA):      # (only where chapters may be that mutagen does not read:
        markers = _toc_markers(path)                         # the discoverer opens the file, not for every song)
    return sorted(markers)


def _toc_markers(path):
    """The chapters GStreamer finds (its discoverer's table of contents: qtdemux's chapter track, matroskademux's
    editions, ...), [(seconds, name)]; [] without them, or without GStreamer's pbutils."""
    if Gst is None:
        return []
    try:
        import gi
        gi.require_version('GstPbutils', '1.0')
        from gi.repository import GstPbutils
        from PyQt6.QtCore import QUrl
        info = GstPbutils.Discoverer.new(2 * Gst.SECOND).discover_uri(QUrl.fromLocalFile(path).toString())
        toc = info.get_toc() if info is not None else None
    except Exception:
        return []
    if toc is None:
        return []
    markers = []

    def walk(entries):
        for entry in entries:
            if entry.get_entry_type() == Gst.TocEntryType.CHAPTER:
                ok, start, stop = entry.get_start_stop_times()
                tags = entry.get_tags()
                found, title = tags.get_string(Gst.TAG_TITLE) if tags is not None else (False, None)
                if ok and start >= 0:
                    markers.append((start / Gst.SECOND, title if found and title else f'Chapter {len(markers) + 1}'))
            walk(entry.get_sub_entries() or [])

    walk(toc.get_entries() or [])
    return markers
