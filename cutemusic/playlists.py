"""Playlists: named lists of songs, in the Playlists tab.

Kept as .m3u8 files (UTF-8 M3U: #EXTM3U, an #EXTINF line and the full path a song) in ~/.ag-cute-music/playlists,
one a playlist, so other players read them too. The .m3u, .m3u8 and .pls files found in the music folder are shown
as well (saved back where they are, as .m3u8 next to a .pls); an iTunes or Apple Music "Library.xml" (File ->
Library -> Export Library... in Music) is imported with File -> Import playlists..., its playlists copied in.

A song of a playlist is its path; the library's tags are used when it is in it, else the #EXTINF title, and a file
no longer there is shown dimmed (missing).
"""

import os
import plistlib
import re
import urllib.parse

from PyQt6.QtCore import QMimeData, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QFont, QKeySequence
from PyQt6.QtWidgets import QSizePolicy
from PyQt6.QtWidgets import (QAbstractItemView, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QPushButton,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from cutemusic import util
from cutemusic.browser import TRACKS_MIME, _duration
from cutemusic.library import AUDIO_EXTENSIONS, Track
from cutemusic.util import log


__all__ = ['Playlist', 'Playlists', 'PlaylistView', 'playlist_folder']


EXTENSIONS = ('.m3u', '.m3u8', '.pls')
PLAYLIST = Qt.ItemDataRole.UserRole          # a playlist item: its Playlist
ENTRY = Qt.ItemDataRole.UserRole + 1         # a song item: its index in the playlist


def playlist_folder():
    path = os.path.join(util.CONFIG_DIR, 'playlists')
    os.makedirs(path, exist_ok=True)
    return path


def _safe(name):
    return re.sub(r'[/:\x00\\]', '_', name).strip().rstrip('.') or 'Playlist'


# Reading and writing

def _local(reference, folder):
    """A playlist's line as a path: absolute, relative to the playlist's folder, or a file:// URL."""
    reference = reference.strip()
    if reference.startswith('file://'):
        reference = urllib.parse.unquote(urllib.parse.urlsplit(reference).path)
    elif '://' in reference:
        return None             # a stream: not for this player
    reference = reference.replace('\\', '/')
    return os.path.normpath(reference if os.path.isabs(reference) else os.path.join(folder, reference))


def read_m3u(path):
    """[(path, title or '')] of an .m3u or .m3u8 file."""
    entries, title = [], ''
    folder = os.path.dirname(path)
    with open(path, 'rb') as f:
        data = f.read()
    for encoding in ('utf-8-sig', 'latin-1'):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('#EXTINF:'):
            title = line.split(',', 1)[1].strip() if ',' in line else ''
        elif not line.startswith('#'):
            local = _local(line, folder)
            if local:
                entries.append((local, title))
            title = ''
    return entries


def read_pls(path):
    """[(path, title or '')] of a .pls file."""
    files, titles = {}, {}
    folder = os.path.dirname(path)
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            key, _, value = line.strip().partition('=')
            match = re.match(r'(File|Title)(\d+)$', key, re.IGNORECASE)
            if match:
                (files if match.group(1).lower() == 'file' else titles)[int(match.group(2))] = value.strip()
    entries = []
    for number in sorted(files):
        local = _local(files[number], folder)
        if local:
            entries.append((local, titles.get(number, '')))
    return entries


def write_m3u8(path, entries, known):
    """entries [(path, title)] to path (through a temporary file); known {path: Track} gives the #EXTINF lines."""
    lines = ['#EXTM3U']
    for entry, title in entries:
        track = known.get(entry)
        if track is not None:
            artist = track.get('artist') or track.album_artist
            lines.append(f'#EXTINF:{int(track.get("duration") or -1)},{artist} - {track.display_title}')
        elif title:
            lines.append(f'#EXTINF:-1,{title}')
        lines.append(entry)
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    os.replace(temporary, path)


def read_itunes_xml(path):
    """[(name, [paths])] of the playlists of an iTunes / Music "Library.xml" (not its own ones: Library, Music,
    Downloaded, Genius...)."""
    with open(path, 'rb') as f:
        library = plistlib.load(f)
    tracks = library.get('Tracks') or {}
    found = []
    for playlist in library.get('Playlists') or []:
        if playlist.get('Master') or playlist.get('Distinguished Kind') or playlist.get('Folder') \
                or playlist.get('Visible') is False:
            continue
        paths = []
        for item in playlist.get('Playlist Items') or []:
            track = tracks.get(str(item.get('Track ID'))) or {}
            location = track.get('Location') or ''
            if location.startswith('file://'):
                paths.append(urllib.parse.unquote(urllib.parse.urlsplit(location).path))
        if paths:
            found.append((playlist.get('Name') or 'Playlist', paths))
    return found


class Playlist(object):
    """name, path (its file), entries [(path, title)]; own: kept in ~/.ag-cute-music/playlists."""

    def __init__(self, path, entries=None):
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.entries = list(entries or [])
        self.own = os.path.dirname(path) == playlist_folder()

    @classmethod
    def load(cls, path):
        reader = read_pls if path.lower().endswith('.pls') else read_m3u
        return cls(path, reader(path))

    def save(self, known):
        if self.path.lower().endswith('.pls'):          # written as .m3u8 next to it, the .pls left as it was
            self.path = os.path.splitext(self.path)[0] + '.m3u8'
        write_m3u8(self.path, self.entries, known)


class Playlists(object):
    """All of them: the own ones and those in the music folder."""

    def __init__(self):
        self.items = []

    def load(self, root, found_in_library=()):
        """The own playlists, and found_in_library: the playlist files the library scan saw (or, when none given, a
        walk of root for them)."""
        paths = [os.path.join(playlist_folder(), name) for name in os.listdir(playlist_folder())
                 if name.lower().endswith(EXTENSIONS)]
        if root and os.path.isdir(root):
            for folder, folders, names in os.walk(root):
                folders[:] = [name for name in folders if not name.startswith('.') and not name.endswith('.musiclibrary')]
                paths += [os.path.join(folder, name) for name in names if name.lower().endswith(EXTENSIONS)
                          and not name.startswith('.')]
        self.items = []
        seen = set()
        for path in sorted(paths, key=lambda p: os.path.basename(p).lower()):
            stem = os.path.splitext(path)[0]
            if stem in seen:
                continue            # a .pls with its .m3u8 written next to it: the .m3u8
            try:
                self.items.append(Playlist.load(path))
                seen.add(stem)
            except OSError as e:
                log.warning(f'[playlists] {path}: {e}')
        log.info(f'[playlists] {len(self.items)} playlists')

    def create(self, name, entries, known):
        path = os.path.join(playlist_folder(), _safe(name) + '.m3u8')
        number = 2
        while os.path.exists(path):
            path = os.path.join(playlist_folder(), f'{_safe(name)} {number}.m3u8')
            number += 1
        playlist = Playlist(path, entries)
        playlist.save(known)
        self.items.append(playlist)
        self.items.sort(key=lambda p: p.name.lower())
        return playlist

    def rename(self, playlist, name, known):
        new = os.path.join(os.path.dirname(playlist.path), _safe(name) + '.m3u8')
        if os.path.exists(new):
            raise OSError(f'{os.path.basename(new)} exists')
        old = playlist.path
        playlist.path, playlist.name = new, os.path.splitext(os.path.basename(new))[0]
        playlist.save(known)
        if os.path.exists(old) and old != new and not old.lower().endswith('.pls'):
            os.remove(old)
        self.items.sort(key=lambda p: p.name.lower())

    def delete(self, playlist):
        os.remove(playlist.path)
        self.items.remove(playlist)


# The tab

class _Tree(QTreeWidget):
    """Songs dragged from the library dropped on a playlist (or a song of it): added there."""

    dropped_tracks = pyqtSignal(object, list, int)      # (playlist, paths, position: -1 the end)
    reordered = pyqtSignal(object, list)                # (playlist, its entries' indexes in the new order)

    def _tracks(self, event):
        return event.source() is not self and event.mimeData().hasFormat(TRACKS_MIME)

    def dragEnterEvent(self, event):
        if self._tracks(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._tracks(event):
            item = self.itemAt(event.position().toPoint())
            event.setAccepted(item is not None)
            if item is not None:
                event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        import json
        item = self.itemAt(event.position().toPoint())
        if self._tracks(event):
            if item is None:
                return
            playlist_item = item if item.parent() is None else item.parent()
            position = -1 if item.parent() is None else playlist_item.indexOfChild(item)
            paths = json.loads(bytes(event.mimeData().data(TRACKS_MIME)).decode('utf-8'))
            event.acceptProposedAction()
            self.dropped_tracks.emit(playlist_item.data(0, PLAYLIST), paths, position)
            return
        # a song moved within its playlist
        moving = self.currentItem()
        if moving is None or moving.parent() is None or item is None:
            return
        playlist_item = moving.parent()
        target = item if item.parent() is playlist_item else None
        if target is None and item is not playlist_item:
            return
        order = [playlist_item.child(index).data(0, ENTRY) for index in range(playlist_item.childCount())]
        moved = moving.data(0, ENTRY)
        order.remove(moved)
        position = order.index(target.data(0, ENTRY)) if target is not None and target is not moving else 0
        if target is not None and event.position().toPoint().y() > self.visualItemRect(target).center().y():
            position += 1
        order.insert(position, moved)
        event.accept()
        self.reordered.emit(playlist_item.data(0, PLAYLIST), order)


class PlaylistView(QWidget):
    """The Playlists tab. play(tracks, index) replaces the queue, enqueue(tracks, next up)."""

    play = pyqtSignal(list, int)
    enqueue = pyqtSignal(list, bool)
    make_negatives = None           # (tracks): the window's, their negatives made (negatives.py); None: not here
    fill_web_menu = None            # (menu, tracks): the window's, Copy to web library (web.py); None: not here

    def __init__(self, parent=None):
        super().__init__(parent)
        self.playlists = Playlists()
        self.known = {}             # path: Track (the library's)
        self.root = ''
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 0, 6)
        self.tree = _Tree()
        self.tree.setHeaderLabels(['Playlist / song', 'Time'])
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, header.ResizeMode.Stretch)
        header.setSectionResizeMode(1, header.ResizeMode.ResizeToContents)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemActivated.connect(self._activated)
        self.tree.dropped_tracks.connect(self._dropped)
        self.tree.reordered.connect(self._reordered)
        remove = QAction('Remove', self.tree)
        remove.setShortcuts([QKeySequence(Qt.Key.Key_Delete), QKeySequence(Qt.Key.Key_Backspace)])
        remove.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        remove.triggered.connect(self._remove_selected)
        self.tree.addAction(remove)
        layout.addWidget(self.tree, 1)
        row = QHBoxLayout()
        new = QPushButton('New playlist')
        new.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        new.clicked.connect(lambda: self.new_playlist([]))
        row.addWidget(new)
        row.addStretch(1)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setStyleSheet('color: palette(mid)')
        # a long line is cut, not a reason for the pane to be wider (the splitter stays where it was put)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.status)

    # What is shown

    def set_library(self, tracks, root):
        self.known = {track.path: track for track in tracks}
        if root != self.root:
            self.root = root
            self.playlists.load(root)
        self.refresh()

    def reload(self):
        self.playlists.load(self.root)
        self.refresh()

    def tracks_of(self, playlist):
        """Its songs as Tracks (the library's, else from the playlist: title, path), the missing ones left out."""
        tracks = []
        for path, title in playlist.entries:
            track = self.known.get(path)
            if track is None and os.path.exists(path):
                track = Track(path=path, title=title.split(' - ', 1)[-1] if title else '', artist=title.split(' - ', 1)[0] if ' - ' in title else '',
                              albumartist='', album='', genres=[], year=0, track=0, disc=0, duration=0.0)
            if track is not None:
                tracks.append(track)
        return tracks

    def refresh(self):
        expanded = {self.tree.topLevelItem(index).data(0, PLAYLIST).path for index in range(self.tree.topLevelItemCount())
                    if self.tree.topLevelItem(index).isExpanded()}
        scrolled = self.tree.verticalScrollBar().value()
        self.tree.clear()
        dim = self.palette().color(self.palette().ColorRole.PlaceholderText)
        for playlist in self.playlists.items:
            seconds = sum((self.known.get(path) or {}).get('duration') or 0 for path, title in playlist.entries)
            item = QTreeWidgetItem(self.tree, [f'{playlist.name}  ({len(playlist.entries)})', _duration(seconds)])
            item.setData(0, PLAYLIST, playlist)
            item.setToolTip(0, playlist.path)
            font = QFont(self.tree.font())
            font.setBold(True)
            item.setFont(0, font)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsDropEnabled)
            for index, (path, title) in enumerate(playlist.entries):
                track = self.known.get(path)
                if track is not None:
                    text = f'{track.display_title} – {track.get("artist") or track.album_artist}'
                else:
                    text = title or os.path.splitext(os.path.basename(path))[0]
                child = QTreeWidgetItem(item, [text, _duration((track or {}).get('duration'))])
                child.setData(0, ENTRY, index)
                child.setToolTip(0, path)
                if not os.path.exists(path):
                    child.setText(0, text + '  (missing)')
                    child.setForeground(0, dim)
            item.setExpanded(playlist.path in expanded)
        self.tree.verticalScrollBar().setValue(scrolled)
        self.status.setText(f'{len(self.playlists.items)} playlists' if self.playlists.items else
                            'No playlists: New playlist, or right-click in the library -> Add to playlist')

    # Changes

    def _save(self, playlist):
        try:
            playlist.save(self.known)
        except OSError as e:
            QMessageBox.warning(self, 'Playlists', f'Cannot save {playlist.path}: {e}')
        self.refresh()

    def new_playlist(self, tracks):
        name, ok = QInputDialog.getText(self, 'New playlist', 'Name:')
        if not ok or not name.strip():
            return None
        try:
            playlist = self.playlists.create(name.strip(), [(track.path, '') for track in tracks], self.known)
        except OSError as e:
            QMessageBox.warning(self, 'Playlists', f'Cannot create it: {e}')
            return None
        log.info(f'[playlists] {playlist.name}: created with {len(tracks)} songs')
        self.refresh()
        return playlist

    def add(self, playlist, tracks, position=-1):
        entries = [(track.path, '') for track in tracks]
        if 0 <= position <= len(playlist.entries):
            playlist.entries[position:position] = entries
        else:
            playlist.entries += entries
        self._save(playlist)

    def _dropped(self, playlist, paths, position):
        self.add(playlist, [self.known.get(path) or Track(path=path, title='', artist='', albumartist='', album='', genres=[],
                                                          year=0, track=0, disc=0, duration=0.0) for path in paths], position)

    def _reordered(self, playlist, order):
        playlist.entries = [playlist.entries[index] for index in order]
        self._save(playlist)

    def _remove_selected(self):
        songs = {}
        for item in self.tree.selectedItems():
            if item.parent() is not None:
                songs.setdefault(item.parent().data(0, PLAYLIST), set()).add(item.data(0, ENTRY))
        for playlist, indexes in songs.items():
            playlist.entries = [entry for index, entry in enumerate(playlist.entries) if index not in indexes]
            self._save(playlist)

    def _rename(self, playlist):
        name, ok = QInputDialog.getText(self, 'Rename playlist', 'Name:', text=playlist.name)
        if ok and name.strip() and name.strip() != playlist.name:
            try:
                self.playlists.rename(playlist, name.strip(), self.known)
            except OSError as e:
                QMessageBox.warning(self, 'Playlists', f'Cannot rename it: {e}')
            self.refresh()

    def _delete(self, playlist):
        if QMessageBox.question(self, 'Delete playlist', f'Delete the playlist "{playlist.name}"?\n\n'
                                f'Its file ({playlist.path}) is removed; the songs stay.',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.playlists.delete(playlist)
            log.info(f'[playlists] deleted {playlist.path}')
        except OSError as e:
            QMessageBox.warning(self, 'Playlists', f'Cannot delete it: {e}')
        self.refresh()

    def import_library_xml(self, path):
        """The playlists of an iTunes / Music Library.xml copied in (as own playlists); how many."""
        found = read_itunes_xml(path)
        for name, paths in found:
            self.playlists.create(name, [(p, '') for p in paths], self.known)
        log.info(f'[playlists] {len(found)} imported from {path}')
        self.refresh()
        return len(found)

    # Playing

    def _activated(self, item):
        playlist = (item.parent() or item).data(0, PLAYLIST)
        tracks = self.tracks_of(playlist)
        if item.parent() is None:
            return              # opened or closed (a double click on a playlist): Play is in its menu
        path = playlist.entries[item.data(0, ENTRY)][0]
        index = next((i for i, track in enumerate(tracks) if track.path == path), None)
        if index is not None:
            self.play.emit(tracks, index)

    def _context_menu(self, position):
        item = self.tree.itemAt(position)
        menu = QMenu(self)
        if item is not None:
            playlist = (item.parent() or item).data(0, PLAYLIST)
            tracks = self.tracks_of(playlist)
            menu.addAction(f'Play "{playlist.name}" (replaces the queue)', lambda: self.play.emit(tracks, 0)).setEnabled(bool(tracks))
            menu.addAction('Play next', lambda: self.enqueue.emit(tracks, True)).setEnabled(bool(tracks))
            menu.addAction('Add to queue', lambda: self.enqueue.emit(tracks, False)).setEnabled(bool(tracks))
            menu.addSeparator()
            if item.parent() is not None:
                menu.addAction('Remove from the playlist', self._remove_selected)
                menu.addAction(util.REVEAL_LABEL, lambda: util.reveal([playlist.entries[item.data(0, ENTRY)][0]]))
            elif self.make_negatives is not None:
                songs = [track for track in tracks if not track.get('remote')]
                menu.addAction(f'Make negatives ({len(songs)} songs, no singer, for karaoke)',
                               lambda: self.make_negatives(songs)).setEnabled(bool(songs))
                if self.fill_web_menu is not None and songs:
                    self.fill_web_menu(menu.addMenu('Copy to web library'), songs)
                menu.addSeparator()
            if item.parent() is None:
                menu.addAction('Rename...', lambda: self._rename(playlist))
                menu.addAction('Delete...', lambda: self._delete(playlist))
                menu.addAction(util.REVEAL_LABEL, lambda: util.reveal([playlist.path]))
            menu.addSeparator()
        menu.addAction('New playlist...', lambda: self.new_playlist([]))
        menu.exec(self.tree.viewport().mapToGlobal(position))
