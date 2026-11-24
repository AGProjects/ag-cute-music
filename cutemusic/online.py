"""Lyrics and covers looked up online for the tracks that have none: lyrics from LRCLIB (synced when it has them),
covers from MusicBrainz's Cover Art Archive, else the iTunes Store.

What is found is kept in ~/.ag-cute-music/online (lyrics/<key>.lrc or .txt, covers/<key>.jpg), never written next
to the music; library.read_lyrics() and read_cover() look there after the files' own. A lookup that finds nothing
leaves <key>.none, and is not tried again for NOT_FOUND_DAYS. One worker thread does them in turn (MusicBrainz
asks for at most one request a second).
"""

import hashlib
import json
import os
import queue
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from PyQt6.QtCore import QObject, pyqtSignal

from cutemusic import APP_NAME, __version__, util
from cutemusic.util import log


__all__ = ['Fetcher', 'cached_lyrics', 'cached_cover', 'enabled', 'set_enabled']


CACHE_DIR = os.path.join(util.CONFIG_DIR, 'online')
NOT_FOUND_DAYS = 30
TIMEOUT = 15
USER_AGENT = f'{APP_NAME.replace(" ", "-")}/{__version__} ( https://ag-projects.com )'
MUSICBRAINZ_INTERVAL = 1.1


def enabled():
    return util.settings().value('online/fetch', True, type=bool)


def set_enabled(on):
    util.settings().setValue('online/fetch', bool(on))


# The cache

def _normal(text):
    text = (text or '').lower()
    text = re.sub(r'\s*[(\[].*?[)\]]', '', text)            # (Remastered 2011), [Live]
    return re.sub(r'\W+', ' ', text).strip()


def _artist(track):
    artist = track.get('artist') or track.get('albumartist') or ''
    return '' if artist == 'Unknown artist' else artist


def _key(*parts):
    return hashlib.sha1('\x00'.join(_normal(part) for part in parts).encode('utf-8')).hexdigest()


# an instrumental (karaoke, backing track) has no lyrics: not looked up, nor the cached ones of the song it is a
# version of (its title without the "(Instrumental)" is the song's: the same key, the singer's lyrics)
_INSTRUMENTAL = re.compile(r'\b(instrumental|karaoke|backing track|off vocal|no vocals?|without vocals?)\b|\binstr\.',
                           re.IGNORECASE)


def instrumental(track):
    return any(_INSTRUMENTAL.search(track.get(tag) or '') for tag in ('title', 'album', 'version')) or \
        bool(_INSTRUMENTAL.search(track.display_title or ''))


def _lyrics_key(track):
    return _key(_artist(track), track.get('title') or track.display_title)


def _cover_key(track):
    if track.get('album'):
        return _key(track.album_artist, track['album'])
    return _key(_artist(track), track.get('title') or track.display_title)


def _folder(kind):
    path = os.path.join(CACHE_DIR, kind)
    os.makedirs(path, exist_ok=True)
    return path


def cached_lyrics(track):
    if instrumental(track):
        return None
    base = os.path.join(CACHE_DIR, 'lyrics', _lyrics_key(track))
    for extension in ('.lrc', '.txt'):
        try:
            with open(base + extension, encoding='utf-8') as f:
                return f.read()
        except OSError:
            pass
    return None


def cached_cover(track):
    path = os.path.join(CACHE_DIR, 'covers', _cover_key(track) + '.jpg')
    return path if os.path.isfile(path) else None


def forget_lyrics(track):
    """The lyrics found online for track are wrong (another song's, or it has none): gone, and not looked up
    again (a .never in the cache: removed by hand, it is looked up again)."""
    key = _lyrics_key(track)
    for extension in ('.lrc', '.txt', '.none'):
        try:
            os.remove(os.path.join(_folder('lyrics'), key + extension))
        except OSError:
            pass
    _save('lyrics', key + '.never', b'')
    log.info(f'[online] lyrics forgotten for {track.display_title}')


def _never(kind, key):
    return os.path.exists(os.path.join(CACHE_DIR, kind, key + '.never'))


def _not_found_lately(kind, key):
    try:
        return time.time() - os.path.getmtime(os.path.join(CACHE_DIR, kind, key + '.none')) < NOT_FOUND_DAYS * 86400
    except OSError:
        return False


def _save(kind, name, data):
    folder = _folder(kind)
    path = os.path.join(folder, name)
    temporary = f'{path}.{os.getpid()}.tmp'
    with open(temporary, 'wb') as f:
        f.write(data)
    os.replace(temporary, path)
    return path


# HTTP

_ssl_context = None


def _context():
    """certifi's certificates when there (python.org's Python, in the standalone Mac app, has none of its own)."""
    global _ssl_context
    if _ssl_context is None:
        try:
            import certifi
            _ssl_context = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            _ssl_context = ssl.create_default_context()
    return _ssl_context


def _get(url, params=None, accept='application/json'):
    """The body of url, None on 404."""
    if params:
        url += '?' + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': accept})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=_context()) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _get_json(url, params=None):
    body = _get(url, params)
    return json.loads(body) if body is not None else None


# Lyrics: LRCLIB

def _fetch_lyrics(track):
    """(text, synced) or None."""
    artist, title = _artist(track), track.get('title') or track.display_title
    if not artist or not title:
        return None
    duration = round(track.get('duration') or 0)
    found = None
    if duration and track.get('album'):
        found = _get_json('https://lrclib.net/api/get', {'artist_name': artist, 'track_name': title,
                                                          'album_name': track['album'], 'duration': duration})
    if not found:
        results = _get_json('https://lrclib.net/api/search', {'artist_name': artist, 'track_name': title}) or []
        if duration:          # the same recording: within a few seconds of it
            results = [r for r in results if not r.get('duration') or abs(r['duration'] - duration) <= 4]
        results.sort(key=lambda r: (not r.get('syncedLyrics'), abs((r.get('duration') or duration) - duration)))
        found = results[0] if results else None
    if not found:
        return None
    if found.get('syncedLyrics'):
        return found['syncedLyrics'], True
    if found.get('plainLyrics'):
        return found['plainLyrics'], False
    if found.get('instrumental'):
        return '(instrumental)', False
    return None


# Covers: the Cover Art Archive (by MusicBrainz release group), else the iTunes Store

_last_musicbrainz = 0.0


def _musicbrainz(path, query):
    global _last_musicbrainz
    wait = _last_musicbrainz + MUSICBRAINZ_INTERVAL - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    try:
        return _get_json(f'https://musicbrainz.org/ws/2/{path}', {'query': query, 'fmt': 'json', 'limit': 5})
    finally:
        _last_musicbrainz = time.monotonic()


def _lucene(text):
    return '"' + re.sub(r'([\\"])', r'\\\1', text) + '"'


def _cover_art_archive(track):
    artist = track.album_artist if track.get('albumartist') else _artist(track)
    if not artist or artist == 'Unknown artist':
        return None
    if track.get('album'):
        result = _musicbrainz('release-group/', f'releasegroup:{_lucene(track["album"])} AND artist:{_lucene(artist)}') or {}
        ids = [group['id'] for group in result.get('release-groups', []) if group.get('score', 0) >= 90]
    else:
        title = track.get('title') or track.display_title
        result = _musicbrainz('recording/', f'recording:{_lucene(title)} AND artist:{_lucene(artist)}') or {}
        ids = []
        for recording in result.get('recordings', []):
            if recording.get('score', 0) < 90:
                continue
            for release in recording.get('releases', []):
                group = (release.get('release-group') or {}).get('id')
                if group and group not in ids:
                    ids.append(group)
    for group in ids[:3]:
        data = _get(f'https://coverartarchive.org/release-group/{group}/front-500', accept='image/*')
        if data:
            return data
    return None


def _itunes(track):
    artist = _artist(track) if not track.get('albumartist') else track.album_artist
    if not artist or artist == 'Unknown artist':
        return None
    album = track.get('album')
    if album:
        params = {'term': f'{artist} {album}', 'entity': 'album', 'limit': 10}
        wanted, field = _normal(album), 'collectionName'
    else:
        title = track.get('title') or track.display_title
        params = {'term': f'{artist} {title}', 'entity': 'song', 'limit': 10}
        wanted, field = _normal(title), 'trackName'
    results = (_get_json('https://itunes.apple.com/search', params) or {}).get('results', [])
    for result in results:
        if _normal(result.get(field)) == wanted and _normal(artist) in _normal(result.get('artistName')) \
                and result.get('artworkUrl100'):
            return _get(result['artworkUrl100'].replace('100x100bb', '600x600bb'), accept='image/*')
    return None


def _fetch_cover(track):
    return _cover_art_archive(track) or _itunes(track)


# The album of a song (the organizer's Unknown albums): MusicBrainz, else the iTunes Store

_ALBUM_KIND = {'Album': 0, 'EP': 1, 'Single': 2}


def find_album(artist, title, duration=0.0):
    """{'album', 'year', 'source'} the song (artist, title, its length in s) first came out on, best effort: the
    earliest studio album with it (not a compilation or live), else an EP or single; None when not found."""
    if not artist or not title:
        return None
    result = _musicbrainz('recording/', f'recording:{_lucene(title)} AND artist:{_lucene(artist)}') or {}
    best = None
    for recording in result.get('recordings', []):
        if recording.get('score', 0) < 90:
            continue
        length = (recording.get('length') or 0) / 1000
        if duration and length and abs(length - duration) > 10:
            continue        # another recording (a remix, a live one)
        for release in recording.get('releases', []):
            group = release.get('release-group') or {}
            kind = _ALBUM_KIND.get(group.get('primary-type'))
            if kind is None or group.get('secondary-types') or not release.get('date'):
                continue        # a compilation, a live album, a soundtrack...; or no date
            key = (kind, release['date'])
            if best is None or key < best[0]:
                best = (key, release.get('title') or group.get('title'), release['date'][:4])
    if best is not None:
        return {'album': best[1], 'year': int(best[2]) if best[2].isdigit() else 0, 'source': 'MusicBrainz'}
    results = (_get_json('https://itunes.apple.com/search', {'term': f'{artist} {title}', 'entity': 'song', 'limit': 10}) or {}).get('results', [])
    for found in results:
        if _normal(found.get('trackName')) == _normal(title) and _normal(artist) in _normal(found.get('artistName')) \
                and found.get('collectionName') and 'compilation' not in (found.get('collectionName') or '').lower():
            year = (found.get('releaseDate') or '')[:4]
            album = re.sub(r'\s*-\s*(Single|EP)$', '', found['collectionName'])
            return {'album': album, 'year': int(year) if year.isdigit() else 0, 'source': 'iTunes'}
    return None


# The worker

class Fetcher(QObject):
    """Looks up what a track lacks, in a thread; found(path) when something was found for the track at path."""

    found = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = queue.Queue()
        self._pending = set()
        self._lock = threading.Lock()
        self._thread = None

    def want(self, track):
        """Look up the lyrics and cover of track, when it has none (in its files or the cache) and online lookups
        are on."""
        if track is None or not enabled():
            return
        with self._lock:
            if track.path in self._pending:
                return
            self._pending.add(track.path)
        self._queue.put(track)
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='online', daemon=True)
            self._thread.start()

    def _run(self):
        while True:
            track = self._queue.get()
            try:
                if self._lookup(track):
                    util.call_in_gui_thread(self.found.emit, track.path)
            except Exception as e:
                log.warning(f'[online] {track.path}: {e}')
            finally:
                with self._lock:
                    self._pending.discard(track.path)

    def _lookup(self, track):
        from cutemusic.library import read_cover, read_lyrics      # the files' own, then the cache
        found = False
        key = _lyrics_key(track)
        if read_lyrics(track) is None and not instrumental(track) and not _never('lyrics', key) and \
                not _not_found_lately('lyrics', key):
            result = _try('lyrics', track, _fetch_lyrics)
            if result:
                text, synced = result
                _save('lyrics', key + ('.lrc' if synced else '.txt'), text.encode('utf-8'))
                log.info(f'[online] lyrics ({"synced" if synced else "plain"}) for {track.display_title}')
                found = True
            elif result is None:
                _save('lyrics', key + '.none', b'')
        key = _cover_key(track)
        if read_cover(track) == (None, None) and not _not_found_lately('covers', key):
            data = _try('cover', track, _fetch_cover)
            if data:
                _save('covers', key + '.jpg', data)
                log.info(f'[online] cover for {track.get("album") or track.display_title}')
                found = True
            elif data is None:
                _save('covers', key + '.none', b'')
        return found


def _try(what, track, function):
    """function(track); False when the lookup itself failed (no network...): tried again next time."""
    try:
        return function(track)
    except (OSError, ValueError) as e:
        log.info(f'[online] no {what} for {track.display_title}: {e}')
        return False
