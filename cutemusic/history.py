"""What was played: ~/.ag-cute-music/history.json, newest first, shown in the History tab as the last albums and
the last tracks, each a section of its own; and ~/.ag-cute-music/playback.log, every track as it starts, for good."""

import json
import os
import time

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QAbstractItemView, QMenu, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from cutemusic import util
from cutemusic.library import Track
from cutemusic.util import log


__all__ = ['History', 'HistoryView', 'log_play']


# every track as it starts playing, one line each, never trimmed: when (local time), artist, title, album, year,
# track number, duration and file, tab-separated (a tab or a line break in a tag: a space)
PLAYBACK_LOG = os.path.join(util.CONFIG_DIR, 'playback.log')


def log_play(track):
    fields = [time.strftime('%Y-%m-%d %H:%M:%S'), track.get('artist') or track.album_artist or '', track.display_title,
              track.get('album') or '', str(track.get('year') or ''), str(track.get('track') or ''),
              _length(track.get('duration'))]
    line = '\t'.join([' '.join(field.split()) for field in fields] + [track.path.replace('\t', ' ').replace('\n', ' ')])
    try:
        with open(PLAYBACK_LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except OSError as e:
        log.warning(f'[history] cannot write {PLAYBACK_LOG}: {e}')


def _length(seconds):
    seconds = int(seconds or 0)
    return f'{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}' if seconds >= 3600 else f'{seconds // 60}:{seconds % 60:02d}'


KEEP = 1000         # plays kept
ALBUMS = 50         # albums shown
TRACKS = 200        # tracks shown
TRACK = Qt.ItemDataRole.UserRole
TRACKS_ROLE = Qt.ItemDataRole.UserRole + 1


def _when(stamp):
    now = time.time()
    local, today = time.localtime(stamp), time.localtime(now)
    if local[:3] == today[:3]:
        return time.strftime('%H:%M', local)
    if now - stamp < 6 * 86400:
        return time.strftime('%a %H:%M', local)
    if local.tm_year == today.tm_year:
        return time.strftime('%d %b', local)
    return time.strftime('%d %b %Y', local)


class History(object):
    """The plays: [{'time': seconds, 'track': {...}}], newest first."""

    def __init__(self):
        self.path = os.path.join(util.CONFIG_DIR, 'history.json')
        self.plays = []
        try:
            with open(self.path, encoding='utf-8') as f:
                self.plays = [play for play in json.load(f) if isinstance(play, dict) and 'track' in play]
        except (OSError, ValueError):
            pass

    def add(self, track):
        if self.plays and self.plays[0]['track'].get('path') == track.path and time.time() - self.plays[0]['time'] < 60:
            return      # the same one again at once (Previous to its start)
        self.plays.insert(0, {'time': time.time(), 'track': dict(track)})
        del self.plays[KEEP:]
        self.save()

    def clear(self):
        self.plays = []
        self.save()

    def rename(self, moved):
        """Files moved ({old path: new}): the plays follow them."""
        changed = False
        for play in self.plays:
            new = moved.get(play['track'].get('path'))
            if new:
                play['track']['path'] = new
                changed = True
        if changed:
            self.save()

    def save(self):
        temporary = self.path + '.tmp'
        try:
            with open(temporary, 'w', encoding='utf-8') as f:
                json.dump(self.plays, f)
            os.replace(temporary, self.path)
        except OSError as e:
            log.warning(f'[history] cannot save {self.path}: {e}')


def _album_key(track):
    return track.album_artist.lower(), (track.get('album') or '').lower()


class HistoryView(QWidget):
    """Recent albums (each once, by its last play) and recent tracks. A double click plays: an album from its first
    track, a track and the rest of its album after it. play(tracks, index) as the browser's."""

    play = pyqtSignal(list, int)

    def __init__(self, history, parent=None):
        super().__init__(parent)
        self.history = history
        self.library = {}           # path: Track, for the albums' tracks as they are now
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 0, 6)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Name', 'Played'])
        self.tree.setUniformRowHeights(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, self.tree.header().ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, self.tree.header().ResizeMode.ResizeToContents)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.itemActivated.connect(self._activated)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.tree, 1)
        self.refresh()

    def set_library(self, tracks):
        self.library = {track.path: track for track in tracks}

    def _album_tracks(self, track):
        key = _album_key(track)
        members = [t for t in self.library.values() if _album_key(t) == key]
        if not members:
            members = [track]
        return sorted(members, key=lambda t: t.sort_key())

    def refresh(self):
        self.tree.clear()
        albums, seen = [], set()
        for play in self.history.plays:
            track = Track(play['track'])
            key = _album_key(track)
            if key not in seen and track.get('album'):
                seen.add(key)
                albums.append((play['time'], track))
            if len(albums) >= ALBUMS:
                break
        for title, rows, album in (('Albums', albums, True),
                                   ('Tracks', [(play['time'], Track(play['track'])) for play in self.history.plays[:TRACKS]], False)):
            section = QTreeWidgetItem(self.tree, [title, ''])
            font = QFont(self.tree.font())
            font.setBold(True)
            section.setFont(0, font)
            section.setFlags(Qt.ItemFlag.ItemIsEnabled)
            section.setFirstColumnSpanned(True)
            if not rows:
                empty = QTreeWidgetItem(section, ['Nothing played yet', ''])
                empty.setFlags(Qt.ItemFlag.NoItemFlags)
            for stamp, track in rows:
                if album:
                    text = f'{track["album"]} – {track.album_artist}'
                else:
                    text = f'{track.display_title} – {track.get("artist") or track.album_artist}'
                item = QTreeWidgetItem(section, ['    ' + text, _when(stamp)])
                item.setData(0, TRACK, track)
                item.setData(0, TRACKS_ROLE, album)
                item.setToolTip(0, track.path)
            section.setExpanded(True)

    def _activated(self, item):
        track = item.data(0, TRACK) if item is not None else None
        if track is None:
            return
        track = self.library.get(track.path, track)
        members = self._album_tracks(track)
        if item.data(0, TRACKS_ROLE):
            self.play.emit(members, 0)
        else:
            index = next((i for i, t in enumerate(members) if t.path == track.path), None)
            self.play.emit(members, index) if index is not None else self.play.emit([track], 0)

    def _context_menu(self, position):
        menu = QMenu(self)
        item = self.tree.itemAt(position)
        if item is not None and item.data(0, TRACK) is not None:
            menu.addAction('Play', lambda: self._activated(item))
            menu.addAction(util.REVEAL_LABEL, lambda: util.reveal([item.data(0, TRACK).path]))
            menu.addSeparator()
        menu.addAction('Clear history', self._clear)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _clear(self):
        self.history.clear()
        self.refresh()
