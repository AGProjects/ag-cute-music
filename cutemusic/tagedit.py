"""Edit the tags of songs (the library's context menu, "Edit metadata..."): year, artist, album and genre, for one song or
all those of an album (or any selection). A field the songs disagree on shows "Multiple values" and is left as it
is unless typed in; a field emptied removes the tag. Several genres: separated by ";"."""

import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QCompleter, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QVBoxLayout

from cutemusic.util import log


__all__ = ['TagEditor', 'write_tags']


FIELDS = (('year', 'Year'), ('artist', 'Artist'), ('album', 'Album'), ('genre', 'Genre'))
MULTIPLE = 'Multiple values'


def _genres(text):
    """The genres of "Jazz; Vocal Jazz" (split as the scanner does), each once."""
    genres = []
    for genre in re.split(r'\s*[;/]\s*', text or ''):
        if genre.strip() and genre.strip() not in genres:
            genres.append(genre.strip())
    return genres


def _value(track, field):
    if field == 'genre':
        return '; '.join(track.get('genres') or [])
    value = track.get(field) or ''
    return str(value) if value else ''


def write_tags(path, changes):
    """changes {'year'|'artist'|'album'|'genre': text ('' removes it)} written in the file at path (mutagen, easy tags).
    The album artist follows the artist when it was the same. None, or the error."""
    try:
        import mutagen
    except ImportError:
        return 'mutagen is missing (python3-mutagen)'
    try:
        audio = mutagen.File(path, easy=True)
        if audio is None:
            return 'unknown format'
        if audio.tags is None:
            audio.add_tags()
        for field, text in changes.items():
            key = 'date' if field == 'year' else field
            if field == 'artist':
                old = (audio.get('artist') or [''])[0]
                if audio.get('albumartist') and audio['albumartist'][0] == old:
                    _set(audio, 'albumartist', text)
            _set(audio, key, _genres(text) if field == 'genre' else text)
            if field == 'year':
                for other in ('originaldate', 'year'):      # else read_track could still find the old year
                    try:
                        if other in audio:
                            del audio[other]
                    except (KeyError, ValueError):
                        pass
        audio.save()
    except Exception as e:
        return str(e) or e.__class__.__name__
    return None


def _set(audio, key, text):
    if text:
        audio[key] = text
    else:
        try:
            del audio[key]
        except KeyError:
            pass


class TagEditor(QDialog):
    """edited(tracks, changes): written (in the tracks' files; the tracks themselves updated too)."""

    edited = pyqtSignal(list, dict)

    def __init__(self, tracks, title, parent=None, genres=()):
        super().__init__(parent)
        self.tracks = tracks
        self.setWindowTitle('Edit metadata')
        layout = QVBoxLayout(self)
        count = f'{len(tracks)} song' + ('s' if len(tracks) != 1 else '')
        heading = QLabel(f'<b>{title}</b><br>{count}' if len(tracks) != 1 else f'<b>{title}</b>')
        heading.setWordWrap(True)
        layout.addWidget(heading)
        form = QFormLayout()
        self.edits, self.original = {}, {}
        for field, label in FIELDS:
            values = {_value(track, field) for track in tracks}
            edit = QLineEdit()
            if len(values) == 1:
                edit.setText(values.pop())
                self.original[field] = edit.text()
            else:
                edit.setPlaceholderText(MULTIPLE)
                self.original[field] = None         # untouched: kept as each file has it
            if field == 'year':
                edit.setMaxLength(4)
            if field == 'genre' and genres:       # the library's, offered as typed
                completer = QCompleter(sorted(genres, key=str.lower), edit)
                completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
                edit.setCompleter(completer)
                edit.setToolTip('Several: separated by ";"')
            self.edits[field] = edit
            form.addRow(label, edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setMinimumWidth(420)

    def _changes(self):
        changes = {}
        for field, edit in self.edits.items():
            text = edit.text().strip()
            original = self.original[field]
            if original is None and not text:
                continue
            if field == 'genre':
                text = '; '.join(_genres(text))
            if text != (original or ''):
                changes[field] = text
        return changes

    def _save(self):
        changes = self._changes()
        if not changes:
            self.reject()
            return
        year = changes.get('year')
        if year and not (year.isdigit() and len(year) == 4):
            QMessageBox.warning(self, 'Edit metadata', 'The year: four digits (1970, say).')
            return
        errors = []
        for track in self.tracks:
            error = write_tags(track.path, changes)
            if error:
                errors.append(f'{track.path}: {error}')
                continue
            for field, text in changes.items():
                if field == 'year':
                    track['year'] = int(text) if text else 0
                elif field == 'genre':
                    track['genres'] = _genres(text)
                else:
                    if field == 'artist' and track.get('albumartist') == track.get('artist'):
                        track['albumartist'] = text
                    track[field] = text
        for error in errors:
            log.warning(f'[tags] {error}')
        written = [track for track in self.tracks if not any(e.startswith(track.path + ':') for e in errors)]
        if written:
            self.edited.emit(written, changes)
        if errors:
            QMessageBox.warning(self, 'Edit metadata', f'{len(errors)} of {len(self.tracks)} files not written:\n\n' +
                                '\n'.join(errors[:10]) + ('\n...' if len(errors) > 10 else ''))
        self.accept()
