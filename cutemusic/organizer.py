"""Organize the music folder (File -> Organize library...): what it would change, previewed, done only for what is
ticked, then the library rescanned.

- Compilations: an album with tracks by several artists spread over a folder each (as iTunes filed a compilation
  without its compilation flag: <base>/<artist>/<album>/<file>), gathered in <base>/Compilations/<album>, as iTunes
  files the flagged ones. Optionally tagged as one (album artist "Various Artists", compilation).
- Years: an album's folder named "YYYY - Album" from its tracks' year.
- Unknown albums: the songs without an album, looked up online by their artist and title (MusicBrainz, else the
  iTunes Store; for Unknown Artist, "Artist - Title" in the name tried both ways): the album they first came out
  on and its year written in their tags, and they optionally moved into <artist>/<album>.
- Artists: the names that look like one artist written differently (case, accents, punctuation, "&"/"and", "The",
  words reversed: "De Phazz", "de-phazz", "Krall Diana"), and the ones all in lowercase: one picked (by default the
  most used, capitalized when all lowercase), written in the
  tags of the others (artist, album artist), and optionally their folders merged into its.
- Genres: the ones that look like one genre written differently (case, accents, punctuation and spaces: "Hip Hop",
  "hip-hop", "HipHop"; "&"/"and"/"n": "R&B", "RnB", "Rock 'n' Roll", "Rock & Roll"; a trailing "music"; the same
  genre in another language or spelling: "Alternatief", "Alternativo"; "Soundtracks", "OST"; and near-identical
  spellings, typos): one picked (by default the English one, else the most used), written in the genre tag of the
  others. A track's other genres stay.
- Duplicates: the same song (title, artist, length within 2 s) in more than one file: shown, nothing changed.

Files are moved (renamed on the same disk), never overwritten: a name already taken is skipped. A .lrc or .txt of
the same name goes with its file, a folder's images with its last tracks; folders left empty are removed. What was
done is added to ~/.ag-cute-music/organizer.log (a line a change, kept), and each run's moves written to
~/.ag-cute-music/organized-<time>.json (old and new paths), to undo by hand if need be.
"""

import collections
import json
import os
import re
import shutil
import time

import threading

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTabWidget,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from cutemusic import util
from cutemusic.library import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS
from cutemusic.util import log


__all__ = ['OrganizerDialog', 'find_compilations', 'find_year_folders', 'find_duplicates', 'find_genre_variants']


UNKNOWN_ALBUMS = {'', 'unknown album', 'unknown'}
COMPILATIONS = 'Compilations'
SIDECARS = ('.lrc', '.LRC', '.txt')
_YEAR_PREFIX = re.compile(r'^\s*(1[89]\d\d|20\d\d)\b')


def _normal(text):
    return re.sub(r'\W+', ' ', (text or '').lower()).strip()


def _safe(name):
    return re.sub(r'[/:\x00]', '_', name).strip().rstrip('.') or 'Unknown'


# What could change

def find_compilations(tracks):
    """[{album, target, moves: [(old path, new path)]}]: the albums by several artists in a folder each."""
    albums = collections.defaultdict(list)
    for track in tracks:
        albums[_normal(track.get('album'))].append(track)
    found = []
    for key, members in albums.items():
        if key in UNKNOWN_ALBUMS:
            continue
        folders = {os.path.dirname(track.path) for track in members}
        artists = {_normal(track.get('artist')) for track in members}
        album_artists = {_normal(track.get('albumartist')) for track in members} - {''}
        if len(folders) < 2 or len(artists) < 2 or len(album_artists) > 1:
            continue        # one folder already, one artist, or albums of different artists with the same name
        # <base>/<artist>/<album>: the base most of them share, the album folder's name most of them have
        base = collections.Counter(os.path.dirname(os.path.dirname(folder)) for folder in folders).most_common(1)[0][0]
        name = collections.Counter(os.path.basename(folder) for folder in folders).most_common(1)[0][0]
        target = os.path.join(base, COMPILATIONS, _safe(name))
        moves = [(track.path, os.path.join(target, os.path.basename(track.path)))
                 for track in sorted(members, key=lambda t: (t.get('disc') or 0, t.get('track') or 0, t.path))
                 if os.path.dirname(track.path) != target]
        if moves:
            found.append({'album': members[0].get('album'), 'target': target, 'moves': moves,
                          'artists': len(artists), 'folders': len(folders)})
    return sorted(found, key=lambda entry: entry['album'].lower())


def find_year_folders(tracks):
    """[{album, year, folder, target}]: an album's folder (its tracks all of one album) to be named "YYYY - name"."""
    folders = collections.defaultdict(list)
    for track in tracks:
        folders[os.path.dirname(track.path)].append(track)
    found = []
    for folder, members in folders.items():
        name = os.path.basename(folder)
        albums = {_normal(track.get('album')) for track in members}
        if len(albums) != 1 or albums & UNKNOWN_ALBUMS or _YEAR_PREFIX.match(name):
            continue
        years = collections.Counter(track.get('year') for track in members if track.get('year'))
        if not years:
            continue
        year = years.most_common(1)[0][0]
        target = os.path.join(os.path.dirname(folder), f'{year} - {name}')
        found.append({'album': members[0].get('album'), 'year': year, 'folder': folder, 'target': target,
                      'tracks': len(members)})
    return sorted(found, key=lambda entry: entry['folder'].lower())


_NOT_SONGS = re.compile(r'@|^\d[\d_\-]*$|answering|recording|_me_\d', re.IGNORECASE)
_TITLE_NOISE = re.compile(r'\s*\((album|single|radio|lp) version\)|^\s*\d{1,3}[\s.\-_]+', re.IGNORECASE)


def find_unknown_albums(tracks):
    """The songs without an album (Unknown Album), but recordings and the like (numbers, addresses, tones)."""
    return sorted((track for track in tracks if _normal(track.get('album')) in UNKNOWN_ALBUMS
                   and not _NOT_SONGS.search(track.display_title)),
                  key=lambda t: t.path.lower())


def song_guesses(track):
    """[(artist, title)] to look the track up by: its tags; for an unknown artist, "A - B" in its title as artist
    and title, and the other way round."""
    title = _TITLE_NOISE.sub('', track.display_title).strip()
    artist = track.get('artist') or track.get('albumartist') or ''
    if artist and _normal(artist) not in ('unknown artist', 'unknown', 'artist'):
        return [(artist, title)]
    parts = [part.strip(' `\'') for part in re.split(r'\s*-\s*', title, maxsplit=1)]
    if len(parts) == 2 and all(parts):
        return [(parts[0], parts[1]), (parts[1], parts[0])]
    return []


class AlbumLookup(QObject):
    """The albums of tracks looked up online, one after the other in a thread (MusicBrainz: a request a second):
    found(path, {'album', 'year', 'source', 'artist', 'title'} or None), finished()."""

    found = pyqtSignal(str, object)
    finished = pyqtSignal()

    def __init__(self, tracks, parent=None):
        super().__init__(parent)
        self.tracks = list(tracks)
        self.stopped = False

    def start(self):
        threading.Thread(target=self._run, name='album lookup', daemon=True).start()

    def stop(self):
        self.stopped = True

    def _run(self):
        from cutemusic.online import find_album
        for track in self.tracks:
            if self.stopped:
                break
            result = None
            for artist, title in song_guesses(track):
                try:
                    result = find_album(artist, title, track.get('duration') or 0)
                except (OSError, ValueError) as e:
                    log.info(f'[organizer] {track.path}: {e}')
                    result = None
                if result:
                    result.update(artist=artist, title=title)
                    break
            if not self.stopped:
                util.call_in_gui_thread(self.found.emit, track.path, result)
        if not self.stopped:
            util.call_in_gui_thread(self.finished.emit)


def _write_album_tags(path, found, errors, tagged):
    try:
        import mutagen
        audio = mutagen.File(path, easy=True)
        if audio is None:
            errors.append(f'{path}: not tagged: unknown format')
            return False
        if audio.tags is None:
            audio.add_tags()
        audio['album'] = found['album']
        if found.get('year'):
            audio['date'] = str(found['year'])
        if found.get('artist') and not (audio.get('artist') or [''])[0].strip() or \
                _normal((audio.get('artist') or [''])[0]) in ('unknown artist', 'unknown'):
            audio['artist'] = found['artist']
            audio['title'] = found['title']
        audio.save()
    except Exception as e:
        errors.append(f'{path}: not tagged: {e}')
        return False
    tagged.append((path, f'{found["album"]} ({found.get("year") or "?"}), {found["source"]}'))
    return True


def fill_albums(entries, root, move=False):
    """entries [(track, found)]: the album and year in the tags; moved into <artist folder>/<album> when move:
    (done [(old, new)], errors, tagged [(path, what)])."""
    done, errors, tagged = [], [], []
    sources = set()
    for track, found in entries:
        if not _write_album_tags(track.path, found, errors, tagged) or not move:
            continue
        folder = os.path.dirname(track.path)
        artist_folder = os.path.dirname(folder) if _normal(os.path.basename(folder)) in UNKNOWN_ALBUMS else folder
        if found.get('artist') and _normal(os.path.basename(artist_folder)) in ('unknown artist', 'unknown'):
            artist_folder = os.path.join(os.path.dirname(artist_folder), _safe(found['artist']))
        target = os.path.join(artist_folder, _safe(found['album']))
        if _move(track.path, os.path.join(target, os.path.basename(track.path)), done, errors):
            sources.add((folder, target))
    for folder, target in sources:
        _tidy(folder, target, done, errors, root)
    return done, errors, tagged


def _artist_key(name):
    """One artist however written: no case, accents, punctuation or "The", "&" as "and", its words in order."""
    import unicodedata
    text = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    text = re.sub(r'^the\s+|,\s*the$', '', text.strip()).replace('&', ' and ')
    words = re.findall(r'[a-z0-9]+', text)
    return ' '.join(sorted(words))


def _capitalized(name):
    """An all-lowercase name with each word capitalized ("de phazz" -> "De Phazz", "o'sullivan" -> "O'sullivan");
    a name with capitals as it is."""
    if name != name.lower():
        return name
    name = re.sub(r"(^|[\s\-(/&.])([a-z])", lambda m: m.group(1) + m.group(2).upper(), name)
    return re.sub(r'\b(Feat|Ft|Vs|Featuring)\b\.?', lambda m: m.group(0).lower(), name)     # "Feat." stays "feat."



def find_artist_variants(tracks):
    """[{key, variants: [(name, tracks)], choice}]: the names written differently for one artist (artist and
    album artist tags); choice the most used (then the one with capitals, accents)."""
    counts = collections.Counter()
    for track in tracks:
        for field in ('artist', 'albumartist'):
            if track.get(field):
                counts[track[field]] += 1
    groups = collections.defaultdict(list)
    for name, count in counts.items():
        key = _artist_key(name)
        if key and _normal(name) not in ('unknown artist', 'various artists', 'artist', 'unknown'):
            groups[key].append((name, count))
    found = []
    for key, variants in groups.items():
        variants.sort(key=lambda v: (-v[1], v[0] == v[0].lower(), not any(ord(c) > 127 for c in v[0]), v[0]))
        # the most used, capitalized when written all lowercase ("de phazz" alone: "De Phazz")
        choice = _capitalized(variants[0][0])
        if len(variants) < 2 and choice == variants[0][0]:
            continue
        found.append({'key': key, 'variants': variants, 'choice': choice})
    return sorted(found, key=lambda group: group['choice'].lower())


def unify_artists(groups, tracks, root, merge_folders=False):
    """groups [(name chosen, {names written otherwise})]: the tags of their tracks rewritten (artist, album
    artist), their folders merged into the chosen name's when merge_folders: (done, errors, tagged)."""
    done, errors, tagged = [], [], []
    import mutagen
    for name, others in groups:
        sources = set()
        for track in tracks:
            fields = [field for field in ('artist', 'albumartist') if track.get(field) in others]
            if fields:
                try:
                    audio = mutagen.File(track.path, easy=True)
                    if audio is None or audio.tags is None:
                        raise ValueError('no tags')
                    for field in fields:
                        audio[field] = name
                    audio.save()
                    tagged.append((track.path, f'{", ".join(track[field] for field in fields)} -> {name}'))
                except Exception as e:
                    errors.append(f'{track.path}: not tagged: {e}')
                    continue
            if not merge_folders:
                continue
            # <base>/<artist folder>/<album>/<file>: an artist folder named as one of the others
            album_folder = os.path.dirname(track.path)
            artist_folder = os.path.dirname(album_folder)
            if os.path.basename(artist_folder) in {_safe(other) for other in others} | others:
                target_folder = os.path.join(os.path.dirname(artist_folder), _safe(name))
                if target_folder != artist_folder:
                    new = os.path.join(target_folder, os.path.relpath(track.path, artist_folder))
                    if _move(track.path, new, done, errors):
                        sources.add((album_folder, os.path.dirname(new)))
        for folder, target in sources:
            _tidy(folder, target, done, errors, root)
    return done, errors, tagged


# Genres

# the same genre in another language or spelling, by key (_genre_key): the key it is merged under
GENRE_SYNONYMS = {
    'alternatief': 'alternative', 'alternativo': 'alternative', 'alternativ': 'alternative', 'alt': 'alternative',
    'alternativa': 'alternative',
    'elektronisch': 'electronic', 'elektronik': 'electronic', 'electronique': 'electronic', 'electronica': 'electronic',
    'electronico': 'electronic', 'elettronica': 'electronic',
    'klassiek': 'classical', 'klassik': 'classical', 'klassisch': 'classical', 'clasica': 'classical',
    'clasico': 'classical', 'classique': 'classical', 'classica': 'classical',
    'rnb': 'randb', 'rhythmandblues': 'randb',
    'worldmusic': 'world', 'wereld': 'world', 'wereldmuziek': 'world', 'weltmusik': 'world', 'musicadelmundo': 'world',
    'musiquedumonde': 'world',
    'soundtracks': 'soundtrack', 'originalsoundtrack': 'soundtrack', 'ost': 'soundtrack', 'bandeoriginale': 'soundtrack',
    'filmmuziek': 'soundtrack', 'bandasonora': 'soundtrack', 'filmmusik': 'soundtrack',
    'latijns': 'latin', 'latino': 'latin', 'latina': 'latin', 'latinamerican': 'latin',
    'popmuziek': 'pop', 'popmusik': 'pop',
    'kerst': 'christmas', 'kerstmuziek': 'christmas', 'navidad': 'christmas', 'weihnachten': 'christmas', 'xmas': 'christmas',
    'kinderen': 'childrens', 'kindermuziek': 'childrens', 'kids': 'childrens', 'children': 'childrens', 'infantil': 'childrens',
    'gesproken': 'spokenword', 'spoken': 'spokenword', 'audiobook': 'spokenword', 'luisterboek': 'spokenword',
}
# the name proposed for a key merged by a synonym, when none of the names in the library is already it
GENRE_NAMES = {
    'alternative': 'Alternative', 'electronic': 'Electronic', 'classical': 'Classical', 'randb': 'R&B',
    'world': 'World', 'soundtrack': 'Soundtrack', 'latin': 'Latin', 'pop': 'Pop', 'christmas': 'Christmas',
    'childrens': "Children's Music", 'spokenword': 'Spoken Word', 'hiphop': 'Hip-Hop', 'rockandroll': 'Rock & Roll',
}
_GENRE_TAIL = re.compile(r'\s+(music|muziek|musik|musica|musique|muzik)$')


def _genre_key(name):
    """One genre however written (_genre_spelling), as its English one (GENRE_SYNONYMS)."""
    key = _genre_spelling(name)
    return GENRE_SYNONYMS.get(key, key)


def _genre_spelling(name):
    """A genre without case, accents, punctuation or spaces, "&" and "'n'" as "and", no trailing "music"."""
    import unicodedata
    text = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower().strip()
    text = re.sub(r"\s*(&|\+|'n'|'n|n'|\bn\b)\s*", ' and ', text)
    text = _GENRE_TAIL.sub('', text) if ' ' in text else text
    return ''.join(re.findall(r'[a-z0-9]+', text))


def find_genre_variants(tracks):
    """[{key, variants: [(name, tracks)], choice}]: the names written differently for one genre; near-identical keys
    (a typo, a letter more) taken as one too. choice: the English name when the variants are translations, else the
    most used, capitalized when all lowercase."""
    import difflib
    counts = collections.Counter(genre for track in tracks for genre in (track.get('genres') or []))
    groups = collections.defaultdict(list)
    for name, count in counts.items():
        key = _genre_key(name)
        if key:
            groups[key].append((name, count))
    # near-identical keys: one group (long enough not to take "rock" for "pop"), under a known genre's key when one
    # of them is (the typo goes into the right spelling), else the most used
    keys = sorted(groups, key=lambda key: (key not in GENRE_NAMES, -sum(count for name, count in groups[key])))
    merged = {}
    for index, key in enumerate(keys):
        if key in merged or len(key) < 5:
            continue
        for other in keys[index + 1:]:
            if other not in merged and len(other) >= 5 and difflib.SequenceMatcher(None, key, other).ratio() >= 0.9:
                merged[other] = key
    for other, key in merged.items():
        groups[key] += groups.pop(other)
    found = []
    for key, variants in groups.items():
        variants.sort(key=lambda v: (-v[1], v[0] == v[0].lower(), v[0]))
        # the most used of the names spelled as the key (not a translation, not a typo), else the known name
        spelled = [name for name, count in variants if _genre_spelling(name) == key]
        if spelled:
            choice = _capitalized(spelled[0])
        else:
            choice = GENRE_NAMES.get(key, _capitalized(variants[0][0]))
        if len(variants) < 2 and choice == variants[0][0]:
            continue
        found.append({'key': key, 'variants': variants, 'choice': choice})
    return sorted(found, key=lambda group: group['choice'].lower())


def unify_genres(groups, tracks):
    """groups [(name chosen, {names written otherwise})]: those names replaced by the chosen one in the genre tag
    of their tracks (their other genres kept, each once): (done, errors, tagged)."""
    rename = {other: name for name, others in groups for other in others}
    errors, tagged = [], []
    import mutagen
    for track in tracks:
        if not any(genre in rename for genre in track.get('genres') or []):
            continue
        try:
            audio = mutagen.File(track.path, easy=True)
            if audio is None or audio.tags is None:
                raise ValueError('no tags')
            genres = []
            for value in audio.get('genre') or []:
                for genre in re.split(r'\s*[;/]\s*|\x00', str(value)):
                    genre = rename.get(genre.strip(), genre.strip())
                    if genre and genre not in genres:
                        genres.append(genre)
            audio['genre'] = genres
            audio.save()
            tagged.append((track.path, f'{", ".join(track["genres"])} -> {", ".join(genres)}'))
        except Exception as e:
            errors.append(f'{track.path}: not tagged: {e}')
    return [], errors, tagged


def find_duplicates(tracks):
    """[[track, ...]]: the same title and artist, lengths within 2 s, in more than one file."""
    groups = collections.defaultdict(list)
    for track in tracks:
        if track.get('title') and (track.get('artist') or track.get('albumartist')):
            groups[(_normal(track.get('title')), _normal(track.get('artist') or track.get('albumartist')))].append(track)
    found = []
    for members in groups.values():
        if len(members) < 2:
            continue
        lengths = [track.get('duration') or 0 for track in members]
        if max(lengths) - min(lengths) <= 2:
            found.append(sorted(members, key=lambda t: t.path))
    return sorted(found, key=lambda group: group[0].display_title.lower())


# Doing it

def _move(old, new, done, errors):
    if os.path.exists(new):
        errors.append(f'{new} exists: {os.path.basename(old)} not moved')
        return False
    try:
        os.makedirs(os.path.dirname(new), exist_ok=True)
        shutil.move(old, new)
    except OSError as e:
        errors.append(f'{old}: {e}')
        return False
    done.append((old, new))
    for extension in SIDECARS:
        sidecar = os.path.splitext(old)[0] + extension
        if os.path.exists(sidecar):
            target = os.path.splitext(new)[0] + extension
            if not os.path.exists(target):
                try:
                    shutil.move(sidecar, target)
                    done.append((sidecar, target))
                except OSError as e:
                    errors.append(f'{sidecar}: {e}')
    return True


def _tidy(folder, target, done, errors, stop):
    """folder after its tracks left: its images to target when no music is left in it (target lacks them), then it
    and its parents removed while empty (up to stop)."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    if any(os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS for name in names):
        return
    for name in names:
        if name.lower().endswith(IMAGE_EXTENSIONS) and not name.startswith('.'):
            new = os.path.join(target, name)
            if not os.path.exists(new):
                try:
                    shutil.move(os.path.join(folder, name), new)
                    done.append((os.path.join(folder, name), new))
                except OSError as e:
                    errors.append(f'{name}: {e}')
    while folder and folder != stop and folder.startswith(stop + os.sep):
        try:
            leftovers = [name for name in os.listdir(folder) if name != '.DS_Store']
            if leftovers:
                return
            if os.path.exists(os.path.join(folder, '.DS_Store')):
                os.remove(os.path.join(folder, '.DS_Store'))
            os.rmdir(folder)
        except OSError:
            return
        folder = os.path.dirname(folder)


def _tag_compilation(path, errors):
    try:
        import mutagen
        audio = mutagen.File(path, easy=True)
        if audio is None:
            return
        if audio.tags is None:
            audio.add_tags()
        audio['albumartist'] = 'Various Artists'
        try:
            audio['compilation'] = '1'
        except Exception:
            pass        # a format whose easy tags have no compilation key: the album artist is enough
        audio.save()
    except Exception as e:
        errors.append(f'{path}: not tagged: {e}')


def gather_compilations(entries, root, tag=False):
    """The tracks of entries (find_compilations) in their target folders: (done [(old, new)], errors)."""
    done, errors = [], []
    for entry in entries:
        sources = set()
        for old, new in entry['moves']:
            if _move(old, new, done, errors):
                sources.add(os.path.dirname(old))
                if tag:
                    _tag_compilation(new, errors)
        for folder in sources:
            _tidy(folder, entry['target'], done, errors, root)
    return done, errors


def name_with_years(entries):
    """The folders of entries (find_year_folders) renamed "YYYY - name": (done [(old file path, new)], errors)."""
    done, errors = [], []
    for entry in entries:
        old, new = entry['folder'], entry['target']
        if os.path.exists(new):
            errors.append(f'{new} exists: {os.path.basename(old)} not renamed')
            continue
        try:
            files = [os.path.join(old, name) for name in os.listdir(old)]
            os.rename(old, new)
        except OSError as e:
            errors.append(f'{old}: {e}')
            continue
        done.extend((path, os.path.join(new, os.path.basename(path))) for path in files)
    return done, errors


ORGANIZER_LOG = os.path.join(util.CONFIG_DIR, 'organizer.log')


def append_log(kind, done, errors, record=None, tagged=()):
    """Every change, one line each, added to ~/.ag-cute-music/organizer.log (kept: the history of the folder's
    organization): the time, the kind, MOVED old -> new or ERROR what."""
    stamp = time.strftime('%Y-%m-%d %H:%M:%S')
    lines = [f'{stamp} {kind}: {len(done)} moved, {len(errors)} errors' + (f' ({os.path.basename(record)})' if record else '')]
    lines += [f'{stamp} {kind} TAGGED {path}: {what}' for path, what in tagged]
    lines += [f'{stamp} {kind} MOVED {old} -> {new}' for old, new in done]
    lines += [f'{stamp} {kind} ERROR {error}' for error in errors]
    try:
        with open(ORGANIZER_LOG, 'a', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
    except OSError as e:
        log.warning(f'[organizer] cannot write {ORGANIZER_LOG}: {e}')


def write_log(kind, done, errors):
    path = os.path.join(util.CONFIG_DIR, time.strftime('organized-%Y%m%d-%H%M%S.json'))
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'kind': kind, 'time': time.time(), 'moved': done, 'errors': errors}, f, indent=1)
    except OSError as e:
        log.warning(f'[organizer] cannot write {path}: {e}')
        return None
    return path


# The window

class OrganizerDialog(QDialog):
    """organized({old path: new path}) after files were moved: the window follows them (queue, history) and
    rescans."""

    organized = pyqtSignal(dict)

    def __init__(self, tracks, root, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Organize library')
        self.resize(900, 600)
        self.root = root
        self.compilations = find_compilations(tracks)
        # the folders of compilations still to gather are left out: gathered first, their new folder then
        scattered = {os.path.dirname(old) for entry in self.compilations for old, new in entry['moves']}
        self.years = [entry for entry in find_year_folders(tracks) if entry['folder'] not in scattered]
        self.duplicates = find_duplicates(tracks)
        self.unknown = find_unknown_albums(tracks)
        self.tracks = tracks
        self.artists = find_artist_variants(tracks)
        self.genres = find_genre_variants(tracks)
        self.lookup = None
        self.suggestions = {}           # path: what was found for it
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._compilations_tab(), f'Compilations ({len(self.compilations)})')
        self.tabs.addTab(self._years_tab(), f'Year in folder names ({len(self.years)})')
        self.tabs.addTab(self._unknown_tab(), f'Unknown albums ({len(self.unknown)})')
        self.tabs.addTab(self._artists_tab(), f'Artists ({len(self.artists)})')
        self.tabs.addTab(self._genres_tab(), f'Genres ({len(self.genres)})')
        self.tabs.addTab(self._duplicates_tab(), f'Duplicates ({len(self.duplicates)})')
        layout.addWidget(self.tabs, 1)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close = QPushButton('Close')
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        layout.addLayout(bottom)

    def _base(self, paths):
        """The folder the paths shown have in common (Old iTunes Music, say), not repeated on every row."""
        folders = [os.path.dirname(path) for path in paths if path.startswith(self.root + os.sep)]
        if not folders:
            return self.root
        base = os.path.commonpath(folders)
        return base if base.startswith(self.root) else self.root

    def _relative(self, path, base=None):
        """path from base; from the music folder: without its top folder either (Old iTunes Music, Recordings)."""
        base = base or self.root
        if not path.startswith(base + os.sep):
            return path
        relative = os.path.relpath(path, base)
        if base == self.root and os.sep in relative:
            relative = relative.split(os.sep, 1)[1]
        return relative

    @staticmethod
    def _tree(headers):
        tree = QTreeWidget()
        tree.setHeaderLabels(headers)
        tree.setUniformRowHeights(True)
        tree.header().setStretchLastSection(True)
        return tree

    @staticmethod
    def _ticked(tree):
        return [index for index in range(tree.topLevelItemCount())
                if tree.topLevelItem(index).checkState(0) == Qt.CheckState.Checked]

    def _tick_all(self, tree, on):
        for index in range(tree.topLevelItemCount()):
            tree.topLevelItem(index).setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def _actions(self, tree, label, slot):
        row = QHBoxLayout()
        for text, on in (('Tick all', True), ('Tick none', False)):
            button = QPushButton(text)
            button.clicked.connect(lambda checked, on=on: self._tick_all(tree, on))
            row.addWidget(button)
        row.addStretch(1)
        apply = QPushButton(label)
        apply.setDefault(True)
        apply.clicked.connect(slot)
        row.addWidget(apply)
        return row

    # Compilations

    def _compilations_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel('Albums by several artists, filed in a folder per artist: gathered in a folder of '
                                f'their own under {COMPILATIONS}. Tick the ones to move.'))
        self.compilation_tree = tree = self._tree(['Album / file', 'Moves to'])
        base = self._base([path for entry in self.compilations for move in entry['moves'] for path in move])
        for entry in self.compilations:
            item = QTreeWidgetItem(tree, [f'{entry["album"]}  ({len(entry["moves"])} tracks, {entry["artists"]} artists, '
                                          f'{entry["folders"]} folders)', self._relative(entry['target'], base)])
            item.setCheckState(0, Qt.CheckState.Checked)
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            for old, new in entry['moves']:
                child = QTreeWidgetItem(item, [self._relative(old, base), self._relative(new, base)])
                child.setToolTip(0, old)
        tree.header().resizeSection(0, 520)
        layout.addWidget(tree, 1)
        self.tag_compilations = QCheckBox('Also tag them as a compilation (album artist "Various Artists"): the '
                                          'library then shows each such album once, under Various Artists')
        layout.addWidget(self.tag_compilations)
        layout.addLayout(self._actions(tree, 'Move ticked', self._apply_compilations))
        return page

    def _apply_compilations(self):
        entries = [self.compilations[index] for index in self._ticked(self.compilation_tree)]
        if not entries:
            return
        files = sum(len(entry['moves']) for entry in entries)
        tag = self.tag_compilations.isChecked()
        if QMessageBox.question(self, 'Organize library',
                                f'Move {files} files of {len(entries)} albums into {COMPILATIONS} folders'
                                + (' and tag them as compilations' if tag else '') + '?\n\n'
                                'Nothing is overwritten; a list of the moves is kept in ~/.ag-cute-music.',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        done, errors = gather_compilations(entries, self.root, tag)
        self._finish('compilations', done, errors)

    # Years

    def _years_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel('Album folders named with the year of their album first: "YYYY - name". Tick the ones '
                                'to rename. (Compilations still to gather are not listed: gathered first, their folder '
                                'is, the next time.)'))
        self.year_tree = tree = self._tree(['Folder', 'Renamed'])
        base = self._base([os.path.join(entry['folder'], '') for entry in self.years])
        for entry in self.years:
            item = QTreeWidgetItem(tree, [self._relative(entry['folder'], base), os.path.basename(entry['target'])])
            item.setCheckState(0, Qt.CheckState.Checked)
            item.setToolTip(0, f'{entry["album"]}, {entry["tracks"]} tracks, {entry["year"]}')
        tree.header().resizeSection(0, 520)
        layout.addWidget(tree, 1)
        layout.addLayout(self._actions(tree, 'Rename ticked', self._apply_years))
        return page

    def _apply_years(self):
        entries = [self.years[index] for index in self._ticked(self.year_tree)]
        if not entries:
            return
        if QMessageBox.question(self, 'Organize library', f'Rename {len(entries)} album folders with their year?',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        done, errors = name_with_years(entries)
        self._finish('years', done, errors)

    # Unknown albums

    def _unknown_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel('Songs without an album: Look up finds the album each first came out on, and its year '
                                '(MusicBrainz, else the iTunes Store), from its artist and title. Best effort: check '
                                'them, untick the wrong ones.'))
        self.unknown_tree = tree = self._tree(['File', 'Song', 'Album', 'Year', 'From'])
        base = self._base([track.path for track in self.unknown])
        self.unknown_items = {}
        for track in self.unknown:
            guesses = song_guesses(track)
            song = f'{guesses[0][0]} – {guesses[0][1]}' if guesses else '(no artist to look up by)'
            item = QTreeWidgetItem(tree, [self._relative(track.path, base), song, '', '', ''])
            item.setToolTip(0, track.path)
            item.setData(0, Qt.ItemDataRole.UserRole, track)
            self.unknown_items[track.path] = item
        tree.header().resizeSection(0, 300)
        tree.header().resizeSection(1, 260)
        tree.header().resizeSection(2, 200)
        layout.addWidget(tree, 1)
        self.move_found = QCheckBox('Also move each into its album\'s folder (<artist>/<album>)')
        layout.addWidget(self.move_found)
        row = QHBoxLayout()
        self.lookup_button = QPushButton('Look up online')
        self.lookup_button.clicked.connect(self._look_up)
        row.addWidget(self.lookup_button)
        self.lookup_status = QLabel()
        row.addWidget(self.lookup_status)
        for text, on in (('Tick all', True), ('Tick none', False)):
            button = QPushButton(text)
            button.clicked.connect(lambda checked, on=on: self._tick_found(on))
            row.addWidget(button)
        row.addStretch(1)
        apply = QPushButton('Write ticked')
        apply.clicked.connect(self._apply_unknown)
        row.addWidget(apply)
        layout.addLayout(row)
        return page

    def _look_up(self):
        if self.lookup is not None:
            self.lookup.stop()
            self.lookup = None
            self.lookup_button.setText('Look up online')
            return
        todo = [track for track in self.unknown if track.path not in self.suggestions and song_guesses(track)]
        if not todo:
            return
        self.lookup = AlbumLookup(todo, self)
        self.lookup.found.connect(self._found)
        self.lookup.finished.connect(self._looked_up)
        self.lookup_total, self.lookup_done = len(todo), 0
        self.lookup_button.setText('Stop')
        self.lookup_status.setText(f'0 of {len(todo)}...')
        self.lookup.start()

    def _found(self, path, found):
        self.lookup_done += 1
        self.lookup_status.setText(f'{self.lookup_done} of {self.lookup_total}, '
                                   f'{sum(1 for value in self.suggestions.values() if value) + bool(found)} found...')
        item = self.unknown_items.get(path)
        self.suggestions[path] = found
        if item is None:
            return
        if found:
            item.setText(1, f'{found["artist"]} – {found["title"]}')
            item.setText(2, found['album'])
            item.setText(3, str(found['year'] or ''))
            item.setText(4, found['source'])
            item.setCheckState(0, Qt.CheckState.Checked)
        else:
            item.setText(2, 'not found')
            item.setForeground(2, self.palette().color(self.palette().ColorRole.PlaceholderText))

    def _looked_up(self):
        self.lookup = None
        self.lookup_button.setText('Look up online')
        found = sum(1 for value in self.suggestions.values() if value)
        self.lookup_status.setText(f'{found} of {len(self.suggestions)} found')

    def _tick_found(self, on):
        for path, item in self.unknown_items.items():
            if self.suggestions.get(path):
                item.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def _apply_unknown(self):
        entries = [(item.data(0, Qt.ItemDataRole.UserRole), self.suggestions[path]) for path, item in self.unknown_items.items()
                   if self.suggestions.get(path) and item.checkState(0) == Qt.CheckState.Checked]
        if not entries:
            return
        move = self.move_found.isChecked()
        if QMessageBox.question(self, 'Organize library',
                                f'Write the album and year in the tags of {len(entries)} songs'
                                + (' and move them into their albums\' folders' if move else '') + '?',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        if self.lookup is not None:
            self.lookup.stop()
        done, errors, tagged = fill_albums(entries, self.root, move)
        self._finish('albums', done, errors, tagged)

    def reject(self):
        if self.lookup is not None:
            self.lookup.stop()
        super().reject()

    # Artists

    def _artists_tab(self):
        from PyQt6.QtWidgets import QComboBox
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel('Names that look like one artist written differently, and names all in lowercase '
                                '(capitalized). Pick (or type) the one to keep for each: the others are rewritten to '
                                'it in the tags. Untick the ones that are not the same artist.'))
        self.artist_tree = tree = self._tree(['Artist', 'Keep as'])
        self.artist_choices = []
        for group in self.artists:
            total = sum(count for name, count in group['variants'])
            spellings = f'{len(group["variants"])} spellings' if len(group['variants']) > 1 else 'all lowercase'
            item = QTreeWidgetItem(tree, [f'{group["choice"]}  ({spellings}, {total} tag' + ('s' if total != 1 else '') + ')', ''])
            item.setCheckState(0, Qt.CheckState.Checked)
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            for name, count in group['variants']:
                QTreeWidgetItem(item, [f'{name}', f'{count} track' + ('s' if count != 1 else '')])
            choice = QComboBox()
            choice.setEditable(True)
            names = [name for name, count in group['variants']]
            choice.addItems(([group['choice']] if group['choice'] not in names else []) + names)
            choice.setCurrentText(group['choice'])
            tree.setItemWidget(item, 1, choice)
            self.artist_choices.append(choice)
            item.setExpanded(True)
        tree.header().resizeSection(0, 420)
        layout.addWidget(tree, 1)
        self.merge_artist_folders = QCheckBox('Also merge their folders into the kept name\'s (<artist>/<album>)')
        layout.addWidget(self.merge_artist_folders)
        layout.addLayout(self._actions(tree, 'Unify ticked', self._apply_artists))
        return page

    def _apply_artists(self):
        groups = []
        for index in self._ticked(self.artist_tree):
            name = self.artist_choices[index].currentText().strip()
            if name:
                variants = {variant for variant, count in self.artists[index]['variants']}
                groups.append((name, variants - {name}))
        if not groups:
            return
        merge = self.merge_artist_folders.isChecked()
        if QMessageBox.question(self, 'Organize library',
                                f'Write the kept name of {len(groups)} artists in the tags of their tracks'
                                + (' and merge their folders' if merge else '') + '?',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        done, errors, tagged = unify_artists(groups, self.tracks, self.root, merge)
        self._finish('artists', done, errors, tagged)

    # Genres

    def _genres_tab(self):
        from PyQt6.QtWidgets import QComboBox
        page = QWidget()
        layout = QVBoxLayout(page)
        label = QLabel('Genres that look like one written differently: case, spacing and punctuation ("Hip Hop", '
                       '"Hip-Hop"), "&" and "and" ("R&B", "RnB"), another language ("Alternatief", "Alternative"), '
                       'near-identical spellings. Pick (or type) the one to keep for each: the others are rewritten to it '
                       'in the genre tags. Untick the ones that are not the same genre.')
        label.setWordWrap(True)
        layout.addWidget(label)
        self.genre_tree = tree = self._tree(['Genre', 'Keep as'])
        self.genre_choices = []
        for group in self.genres:
            total = sum(count for name, count in group['variants'])
            spellings = f'{len(group["variants"])} spellings' if len(group['variants']) > 1 else 'all lowercase'
            item = QTreeWidgetItem(tree, [f'{group["choice"]}  ({spellings}, {total} track' + ('s' if total != 1 else '') + ')', ''])
            item.setCheckState(0, Qt.CheckState.Checked)
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            for name, count in group['variants']:
                QTreeWidgetItem(item, [name, f'{count} track' + ('s' if count != 1 else '')])
            choice = QComboBox()
            choice.setEditable(True)
            names = [name for name, count in group['variants']]
            choice.addItems(([group['choice']] if group['choice'] not in names else []) + names)
            choice.setCurrentText(group['choice'])
            tree.setItemWidget(item, 1, choice)
            self.genre_choices.append(choice)
            item.setExpanded(True)
        tree.header().resizeSection(0, 420)
        layout.addWidget(tree, 1)
        layout.addLayout(self._actions(tree, 'Unify ticked', self._apply_genres))
        return page

    def _apply_genres(self):
        groups = []
        for index in self._ticked(self.genre_tree):
            name = self.genre_choices[index].currentText().strip()
            if name:
                variants = {variant for variant, count in self.genres[index]['variants']}
                groups.append((name, variants - {name}))
        if not groups:
            return
        if QMessageBox.question(self, 'Organize library',
                                f'Write the kept name of {len(groups)} genres in the tags of their tracks?',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        done, errors, tagged = unify_genres(groups, self.tracks)
        self._finish('genres', done, errors, tagged)

    # Duplicates

    def _duplicates_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel('The same song in more than one file (title, artist, length within 2 s). Nothing is '
                                'changed here: right-click a file to show it.'))
        self.duplicate_tree = tree = self._tree(['Song / file', 'Album', 'Length'])
        base = self._base([track.path for group in self.duplicates for track in group])
        for group in self.duplicates:
            first = group[0]
            item = QTreeWidgetItem(tree, [f'{first.display_title} – {first.get("artist") or first.album_artist}  ({len(group)} files)'])
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            for track in group:
                seconds = int(track.get('duration') or 0)
                child = QTreeWidgetItem(item, [self._relative(track.path, base), track.get('album') or '', f'{seconds // 60}:{seconds % 60:02d}'])
                child.setData(0, Qt.ItemDataRole.UserRole, track.path)
            item.setExpanded(True)
        tree.header().resizeSection(0, 520)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(self._duplicate_menu)
        layout.addWidget(tree, 1)
        return page

    def _duplicate_menu(self, position):
        from PyQt6.QtWidgets import QMenu
        item = self.duplicate_tree.itemAt(position)
        path = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if not path:
            return
        menu = QMenu(self)
        menu.addAction(util.REVEAL_LABEL, lambda: util.reveal([path]))
        menu.exec(self.duplicate_tree.viewport().mapToGlobal(position))

    # After

    def _finish(self, kind, done, errors, tagged=()):
        record = write_log(kind, done, errors)
        append_log(kind, done, errors, record, tagged)
        log.info(f'[organizer] {kind}: {len(done)} moved, {len(errors)} errors' + (f' ({record})' if record else ''))
        for error in errors:
            log.warning(f'[organizer] {error}')
        text = (f'{len(tagged)} songs tagged, ' if tagged else '') + f'{len(done)} files moved.'
        if errors:
            text += f'\n\n{len(errors)} not:\n' + '\n'.join(errors[:15]) + ('\n...' if len(errors) > 15 else '')
        text += f'\n\nEvery change is in {ORGANIZER_LOG}' + (f'\n(and this run in {record})' if record else '')
        QMessageBox.information(self, 'Organize library', text)
        self.organized.emit(dict(done))
        self.accept()
