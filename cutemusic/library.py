"""The music folder: its files, their tags (mutagen), covers and lyrics.

The tags are cached by path, with each file's mtime and size, in a file per music folder
(~/.ag-cute-music/libraries/<hash of the folder's path>.json): a rescan reads only the files new or changed since,
switching back to a folder used before too.
"""

import base64
import hashlib
import json
import os
import re
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

from cutemusic import util
from cutemusic.util import log

try:
    import mutagen
except ImportError:         # no tags: titles from the file names, artists and albums from the folders
    mutagen = None


__all__ = ['AUDIO_EXTENSIONS', 'Track', 'Scanner', 'music_folder', 'set_music_folder', 'read_cover', 'read_lyrics']


AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.oga', '.opus', '.m4a', '.mp4', '.aac', '.alac', '.wav', '.aif', '.aiff',
                    '.wv', '.ape', '.wma', '.mpc', '.dsf'}
COVER_NAMES = ('cover', 'folder', 'front', 'album', 'albumart', 'albumartsmall')
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif')
CACHE_VERSION = 2         # 2: the color markers (colors.py) read too


def music_folder():
    return util.local_path(util.settings().value('library/folder', '~/Music', type=str)) or os.path.expanduser('~/Music')


RECENT_FOLDERS = 10


def set_music_folder(path):
    """The library's folder, the latest of the recent ones (the previous one kept among them)."""
    settings = util.settings()
    previous = settings.value('library/folder', '~/Music', type=str)
    path = util.portable_path(path)
    settings.setValue('library/folder', path)
    folders = [path] + [folder for folder in [previous] + recent_folders(portable=True) if folder != path]
    settings.setValue('library/recent', json.dumps(_unique(folders)[:RECENT_FOLDERS]))


def recent_folders(portable=False):
    """The libraries opened before, the latest first (the current one too), as kept (portable) or for here."""
    try:
        folders = json.loads(util.settings().value('library/recent', '[]', type=str))
    except ValueError:
        folders = []
    folders = [folder for folder in folders if isinstance(folder, str) and folder] if isinstance(folders, list) else []
    return folders if portable else _unique([util.local_path(folder) for folder in folders])


def clear_recent_folders():
    util.settings().setValue('library/recent', json.dumps([util.portable_path(music_folder())]))


def _unique(items):
    seen = set()
    return [item for item in items if not (item in seen or seen.add(item))]


def _first(tags, key):
    value = tags.get(key) if tags else None
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value).strip() if value is not None else ''


def _number(text):
    match = re.match(r'\s*(\d+)', text or '')
    return int(match.group(1)) if match else 0


def _year(text):
    match = re.search(r'(1[89]\d\d|20\d\d)', text or '')
    return int(match.group(1)) if match else 0


class Track(dict):
    """A music file: path, title, artist, albumartist, album, genres (list), year, track, disc, duration (s), colors
    (list: colors.py)."""

    @property
    def path(self):
        return self['path']

    @property
    def album_artist(self):
        return self.get('albumartist') or self.get('artist') or 'Unknown artist'

    @property
    def display_title(self):
        return self.get('title') or os.path.splitext(os.path.basename(self['path']))[0]

    def sort_key(self):
        return self.get('disc') or 0, self.get('track') or 0, self.display_title.lower()


def read_track(path, root, durations=True):
    """The tags of path; what has none is taken from the folders: <root>/<artist>/<album>/<file>. Without a length
    in them, from GStreamer when durations (slow: the scanner does it in a pass of its own)."""
    track = Track(path=path, title='', artist='', albumartist='', album='', genres=[], year=0, track=0, disc=0, duration=0.0, colors=[])
    if mutagen is not None:
        try:
            audio = mutagen.File(path, easy=True)
        except Exception as e:
            log.warning(f'[library] {path}: {e}')
            audio = None
        if audio is not None:
            tags = audio.tags or {}
            track['title'] = _first(tags, 'title')
            track['artist'] = _first(tags, 'artist')
            track['albumartist'] = _first(tags, 'albumartist')
            track['album'] = _first(tags, 'album')
            genres = []
            for value in (tags.get('genre') or []):
                for genre in re.split(r'\s*[;/]\s*|\x00', str(value)):
                    if genre.strip() and genre.strip() not in genres:
                        genres.append(genre.strip())
            track['genres'] = genres
            track['year'] = _year(_first(tags, 'date') or _first(tags, 'originaldate') or _first(tags, 'year'))
            track['track'] = _number(_first(tags, 'tracknumber'))
            track['disc'] = _number(_first(tags, 'discnumber'))
            from cutemusic.colors import read_from
            track['colors'] = read_from(audio)
            try:
                track['duration'] = float(audio.info.length or 0)
            except AttributeError:
                pass
    if not track['duration'] and durations:
        track['duration'] = _gst_duration(path)
    relative = os.path.relpath(path, root).split(os.sep)
    folders = relative[:-1]
    if not track['album'] and folders:
        track['album'] = folders[-1]
    if not track['artist'] and not track['albumartist'] and len(folders) >= 2:
        track['artist'] = folders[-2]
    if not track['title']:
        name = os.path.splitext(relative[-1])[0]
        track['title'] = re.sub(r'^\d+[\s.\-_]+', '', name) or name
        if not track['track']:
            track['track'] = _number(name)
    return track


_discoverer = threading.local()


def _gst_duration(path):
    """The length of path from GStreamer (its discoverer, in this thread): when mutagen is missing or gives none."""
    try:
        from cutemusic.player import Gst
        if Gst is None:
            return 0.0
        if not hasattr(_discoverer, 'instance'):
            import gi
            gi.require_version('GstPbutils', '1.0')
            from gi.repository import GstPbutils
            _discoverer.instance = GstPbutils.Discoverer.new(5 * Gst.SECOND)
        from PyQt6.QtCore import QUrl
        info = _discoverer.instance.discover_uri(QUrl.fromLocalFile(path).toString(QUrl.ComponentFormattingOption.FullyEncoded))
        return max(0.0, info.get_duration() / Gst.SECOND)
    except Exception as e:
        log.warning(f'[library] {path}: no duration: {e}')
        return 0.0


class Scanner(QObject):
    """The music folder read in a thread. progress(text) for the status line; updated(tracks) as it goes (every
    UPDATE seconds, the files not read yet as they were in the cache); finished(tracks) at the end.

    First the tags of the files new or changed (or all, once, when the cache was made without mutagen and it is there
    now), then, for those still without a length (no mutagen), their length from GStreamer: slower, a pass of its
    own, so the tree is full before it."""

    progress = pyqtSignal(str)
    updated = pyqtSignal(list)
    finished = pyqtSignal(list)
    UPDATE = 2.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self._generation = 0        # a scan started anew: the one before stops at its next file

    @staticmethod
    def cache_path(root):
        folder = os.path.join(util.CONFIG_DIR, 'libraries')
        os.makedirs(folder, mode=0o700, exist_ok=True)
        return os.path.join(folder, hashlib.sha1(root.encode('utf-8')).hexdigest()[:16] + '.json')

    @classmethod
    def _migrate(cls, root):
        """The one library.json of before, moved to its folder's file (once: then it is gone)."""
        old = os.path.join(util.CONFIG_DIR, 'library.json')
        if not os.path.exists(old):
            return
        try:
            with open(old, encoding='utf-8') as f:
                data = json.load(f)
            target = cls.cache_path(data.get('root') or '') if data.get('root') else None
            if target and not os.path.exists(target):
                os.replace(old, target)
                log.info(f'[library] {old} moved to {target}')
            else:
                os.replace(old, old + '.old')
        except (OSError, ValueError) as e:
            log.warning(f'[library] cannot move {old}: {e}')

    def cached(self, root):
        """The tracks of the last scan of root, at once (before the rescan)."""
        cache, tags = self._load_cache(root)
        return [Track(entry[2]) for entry in cache.values()]

    def _load_cache(self, root):
        """(files, whether they were read with mutagen)."""
        try:
            self._migrate(root)
            with open(self.cache_path(root), encoding='utf-8') as f:
                data = json.load(f)
            if data.get('version') == CACHE_VERSION and data.get('root') == root:
                return data.get('files', {}), data.get('tags', True)
        except (OSError, ValueError):
            pass
        return {}, True

    def _save_cache(self, root, files):
        path = self.cache_path(root)
        temporary = path + '.tmp'
        try:
            with open(temporary, 'w', encoding='utf-8') as f:
                json.dump({'version': CACHE_VERSION, 'root': root, 'tags': mutagen is not None, 'files': files}, f)
            os.replace(temporary, path)
        except OSError as e:
            log.warning(f'[library] cannot save {path}: {e}')

    def scan(self, root):
        self._generation += 1
        self._thread = threading.Thread(target=self._scan, args=(root, self._generation), name='library scan', daemon=True)
        self._thread.start()

    def stop(self):
        self._generation += 1

    def _scan(self, root, generation):
        stopped = lambda: generation != self._generation
        cache, cached_tags = self._load_cache(root)
        retag = mutagen is not None and not cached_tags         # mutagen installed since: the tags at last
        paths = []
        for directory, folders, names in os.walk(root, followlinks=True):
            if stopped():
                return
            folders[:] = sorted(folder for folder in folders if not folder.startswith('.'))
            paths += [os.path.join(directory, name) for name in sorted(names)
                      if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS and not name.startswith('.')]
        files, read, shown = {}, 0, time.monotonic()

        def current():
            # what is known now: read, else as cached
            return [Track(files[path][2] if path in files else cache[path][2]) for path in paths if path in files or path in cache]
        for index, path in enumerate(paths):
            if stopped():
                return
            try:
                stat = os.stat(path)
            except OSError:
                continue
            entry = cache.get(path)
            if entry is None or entry[0] != stat.st_mtime or entry[1] != stat.st_size or retag:
                track = read_track(path, root, durations=False)
                if entry is not None and not track.get('duration'):
                    track['duration'] = entry[2].get('duration') or 0.0
                entry = [stat.st_mtime, stat.st_size, track]
                read += 1
            files[path] = entry
            if read and time.monotonic() - shown > self.UPDATE:
                shown = time.monotonic()
                self.updated.emit(current())
                self.progress.emit(f'Reading {root}: {index + 1} of {len(paths)} files')
        if read or len(files) != len(cache) or retag:
            self._save_cache(root, files)
        tracks = [Track(entry[2]) for entry in files.values()]
        log.info(f'[library] {root}: {len(tracks)} files, {read} read')
        self.finished.emit(tracks)

        # the lengths mutagen did not give
        missing = [path for path, entry in files.items() if not entry[2].get('duration')]
        if not missing:
            return
        log.info(f'[library] {len(missing)} files without a length: from GStreamer')
        found, saved = 0, time.monotonic()
        for index, path in enumerate(missing):
            if stopped():
                break
            duration = _gst_duration(path)
            if duration:
                files[path][2]['duration'] = duration
                found += 1
            if time.monotonic() - shown > self.UPDATE:
                shown = time.monotonic()
                self.updated.emit([Track(entry[2]) for entry in files.values()])
                self.progress.emit(f'Reading the lengths: {index + 1} of {len(missing)} files')
            if time.monotonic() - saved > 30:       # kept as it goes: a quit does not lose them
                saved = time.monotonic()
                self._save_cache(root, files)
        if found:
            self._save_cache(root, files)
        if not stopped():
            self.finished.emit([Track(entry[2]) for entry in files.values()])
        log.info(f'[library] {found} lengths from GStreamer')


# Covers

def _embedded_cover(path):
    if mutagen is None:
        return None
    try:
        audio = mutagen.File(path)
    except Exception:
        return None
    if audio is None:
        return None
    pictures = getattr(audio, 'pictures', None)         # FLAC
    if pictures:
        front = [picture for picture in pictures if picture.type == 3]
        return (front or pictures)[0].data
    tags = audio.tags
    if tags is None:
        return None
    if hasattr(tags, 'getall'):                         # ID3
        frames = tags.getall('APIC')
        if frames:
            front = [frame for frame in frames if frame.type == 3]
            return (front or frames)[0].data
        return None
    if 'covr' in tags and tags['covr']:                 # MP4
        return bytes(tags['covr'][0])
    blocks = tags.get('metadata_block_picture') if hasattr(tags, 'get') else None
    if blocks:                                          # Ogg Vorbis / Opus
        from mutagen.flac import Picture
        try:
            return Picture(base64.b64decode(blocks[0])).data
        except Exception:
            return None
    return None


def _folder_image(directory, names):
    try:
        files = os.listdir(directory)
    except OSError:
        return None
    images = [name for name in files if name.lower().endswith(IMAGE_EXTENSIONS) and not name.startswith('.')]
    for wanted in names:
        for name in images:
            if os.path.splitext(name)[0].lower() == wanted:
                return os.path.join(directory, name)
    return None


def read_cover(track):
    """(image data or None, image path or None): the cover in the file, else cover.jpg and the like next to it,
    else the only image in its folder, else one looked up online."""
    data = _embedded_cover(track.path)
    if data:
        return data, None
    directory = os.path.dirname(track.path)
    path = _folder_image(directory, COVER_NAMES)
    if path is None:
        try:
            images = [name for name in os.listdir(directory) if name.lower().endswith(IMAGE_EXTENSIONS)]
        except OSError:
            images = []
        if len(images) == 1:
            path = os.path.join(directory, images[0])
    if path is None:
        from cutemusic.online import cached_cover          # looked up online (cutemusic/online.py)
        path = cached_cover(track)
    return None, path


def read_artist_image(track):
    """artist.jpg (or the like) in the album's folder or the one above it: the artist's picture."""
    directory = os.path.dirname(track.path)
    return _folder_image(directory, ('artist',)) or _folder_image(os.path.dirname(directory), ('artist', 'folder'))


# Lyrics

_LRC_TIME = re.compile(r'\[(\d+):(\d+(?:[.:]\d+)?)\]')


def parse_lrc(text):
    """[(seconds, line)] of a synced .lrc text, or None when it has no time stamps."""
    lines = []
    for raw in text.splitlines():
        stamps = _LRC_TIME.findall(raw)
        if not stamps:
            continue
        words = _LRC_TIME.sub('', raw).strip()
        for minutes, seconds in stamps:
            lines.append((int(minutes) * 60 + float(seconds.replace(':', '.')), words))
    return sorted(lines) or None


def _embedded_lyrics(path):
    if mutagen is None:
        return None
    try:
        audio = mutagen.File(path)
    except Exception:
        return None
    tags = getattr(audio, 'tags', None)
    if tags is None:
        return None
    if hasattr(tags, 'getall'):                         # ID3
        for frame in tags.getall('USLT'):
            if str(frame.text).strip():
                return str(frame.text)
        for frame in tags.getall('SYLT'):
            return '\n'.join(f'[{int(t // 60000):02d}:{t % 60000 / 1000:05.2f}]{words}' for words, t in frame.text)
        return None
    for key in ('\xa9lyr', 'lyrics', 'LYRICS', 'unsyncedlyrics', 'UNSYNCEDLYRICS'):
        try:
            value = tags.get(key) if hasattr(tags, 'get') else None
        except Exception:
            value = None
        if value:
            return str(value[0] if isinstance(value, list) else value)
    return None


_NOT_LYRICS = re.compile(r'created (with|by)|mp3tag|https?://|www\.|\.(com|net|org)\b', re.IGNORECASE)


def _real_lyrics(text):
    """Not what a tagger left in the lyrics tag ("Created with mp3Tag", a web address): one short line is not
    lyrics, synced ones always are."""
    if _LRC_TIME.search(text):
        return True
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    if len(lines) <= 2 and (_NOT_LYRICS.search(text) or len(text.strip()) < 80):
        return False
    return True


def read_lyrics(track):
    """The lyrics text (synced ones with their [mm:ss.xx] stamps): <file>.lrc or <file>.txt next to it, else in
    the file's tags, else looked up online; None when there are none."""
    base = os.path.splitext(track.path)[0]
    for extension in ('.lrc', '.LRC', '.txt'):
        try:
            with open(base + extension, encoding='utf-8', errors='replace') as f:
                return f.read()
        except OSError:
            pass
    text = _embedded_lyrics(track.path)
    if text is not None and not _real_lyrics(text):
        text = None
    if text is None:
        from cutemusic.online import cached_lyrics         # looked up online (cutemusic/online.py)
        text = cached_lyrics(track)
    return text
