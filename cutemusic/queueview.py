"""The Queue tab: the tracks in the order they play, the one playing highlighted, the next one marked. A double
click (or Enter) plays one; drag to reorder (and drop tracks from the library); Delete or the context menu removes
(the one playing too: on to the next); Clear upcoming leaves the one playing. When: the hour each one to come
starts, from the point the one playing is at (paused: as if played on now); In: how long until then (the one
playing: how much of it is left), counting down each second."""

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QFont, QKeySequence
from PyQt6.QtWidgets import QSizePolicy
from PyQt6.QtWidgets import QAbstractItemView, QLabel, QMenu, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

import json
import time

from cutemusic.browser import TRACKS_MIME, _duration


__all__ = ['QueueView']


def _until(seconds):
    """The In column: m:ss under an hour (counting the seconds), from an hour on h:mm (the minutes only)."""
    seconds = int(seconds)
    return f'{seconds // 60}:{seconds % 60:02d}' if seconds < 3600 else f'{seconds // 3600}:{seconds // 60 % 60:02d} h'


# the rows to come tinted by how soon they start: under 10 min reddish, then orange, then green, from 60 min none
# (translucent: over the dark theme and the light one alike)
_SOON = [(600, QColor(229, 72, 60, 110)), (1800, QColor(245, 150, 40, 110)), (3600, QColor(70, 200, 100, 100))]


def _soon(seconds):
    for limit, color in _SOON:
        if seconds < limit:
            return QBrush(color)
    return QBrush()


INDEX = Qt.ItemDataRole.UserRole         # the track's index in Queue.tracks


class _Tree(QTreeWidget):
    """Rows dragged to reorder (dropped); tracks dragged from the library put in where they are dropped
    (inserted(paths, position in the play order; -1: the end))."""

    dropped = pyqtSignal()
    inserted = pyqtSignal(list, int)

    def _tracks(self, event):
        return event.source() is not self and event.mimeData().hasFormat(TRACKS_MIME)

    def dragEnterEvent(self, event):
        if self._tracks(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._tracks(event):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if self._tracks(event):
            try:
                paths = json.loads(bytes(event.mimeData().data(TRACKS_MIME)).decode('utf-8'))
            except ValueError:
                return
            item = self.itemAt(event.position().toPoint())
            position = -1
            if item is not None:
                position = self.indexOfTopLevelItem(item)
                if event.position().toPoint().y() > self.visualItemRect(item).center().y():
                    position += 1
            event.acceptProposedAction()
            self.inserted.emit(paths, position)
            return
        super().dropEvent(event)
        self.dropped.emit()


class QueueView(QWidget):
    """jump(position in the play order), remove([track indexes]), reorder([track indexes in the new order]),
    insert([paths], position)."""

    jump = pyqtSignal(int)
    insert = pyqtSignal(list, int)
    remove = pyqtSignal(list)
    reorder = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.position = -1
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 0, 6)
        self.tree = _Tree()
        self.tree.setHeaderLabels(['#', 'Track', 'Time', 'When', ''])
        self._header_icon()
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tree.setAcceptDrops(True)
        self.tree.inserted.connect(self.insert.emit)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, header.ResizeMode.Stretch)
        header.setSectionResizeMode(2, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, header.ResizeMode.ResizeToContents)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.itemActivated.connect(lambda item: self.jump.emit(self.tree.indexOfTopLevelItem(item)))
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.dropped.connect(self._dropped)
        remove = QAction('Remove', self.tree)
        remove.setShortcuts([QKeySequence(Qt.Key.Key_Delete), QKeySequence(Qt.Key.Key_Backspace)])
        remove.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        remove.triggered.connect(self._remove_selected)
        self.tree.addAction(remove)
        layout.addWidget(self.tree, 1)
        self.status = QLabel()
        self.status.setStyleSheet('color: palette(mid)')
        # a long line is cut, not a reason for the pane to be wider (the splitter stays where it was put)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.status)
        self.durations = []         # the rows' tracks' durations (s), in the play order
        self.times = QTimer(self, interval=1000)
        self.times.timeout.connect(self._update_times)

    save_as_playlist = None
    elapsed = None          # the window's: how far into the one playing (s)

    def _header_icon(self):
        """In's header: a chronograph (in the text colour: again when the theme changes)."""
        from cutemusic.icons import line_icon
        header = self.tree.headerItem()
        header.setIcon(4, line_icon('stopwatch', self.palette().color(self.palette().ColorRole.WindowText)))
        header.setToolTip(4, 'How long until it plays (the one playing: how much of it is left)')

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == event.Type.PaletteChange:
            self._header_icon()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_times()
        self.times.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.times.stop()

    def _update_times(self):
        """When: when each track to come starts (HH:MM; the next day's with a +1); In: how long until then."""
        if self.position < 0 or self.position >= len(self.durations):
            return
        now = time.time()
        left = max(0.0, self.durations[self.position] - (self.elapsed() if self.elapsed else 0.0))
        start = now + left
        today = time.localtime(now).tm_yday
        if self.position < self.tree.topLevelItemCount():
            self.tree.topLevelItem(self.position).setText(4, '-' + _until(left))
        for row in range(self.position + 1, min(len(self.durations), self.tree.topLevelItemCount())):
            moment = time.localtime(start)
            days = (moment.tm_yday - today) % 366
            item = self.tree.topLevelItem(row)
            item.setText(3, time.strftime('%H:%M', moment) + (f' +{days}' if days else ''))
            item.setText(4, _until(start - now))
            brush = _soon(start - now)
            for column in range(5):
                item.setBackground(column, brush)
            start += self.durations[row]         # the window's: the queue saved as a new playlist

    def refresh(self, queue, marker='▶'):
        scrolled = self.tree.verticalScrollBar().value()
        self.tree.clear()
        self.position = queue.position
        self.durations = [queue.tracks[index].get('duration') or 0.0 for index in queue.order]
        for position, index in enumerate(queue.order):
            track = queue.tracks[index]
            artist = track.get('artist') or track.album_artist
            item = QTreeWidgetItem(self.tree, [str(position + 1), f'{track.display_title} – {artist}', _duration(track.get('duration'))])
            item.setData(0, INDEX, index)
            item.setToolTip(1, f'{track.get("album") or ""}\n{track.path}'.strip())
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
            if position == queue.position:
                font = QFont(self.tree.font())
                font.setBold(True)
                for column in range(5):
                    item.setFont(column, font)
                item.setText(0, marker)
            elif position < queue.position:
                for column in range(5):
                    item.setForeground(column, self.palette().color(self.palette().ColorRole.PlaceholderText))
            elif position == queue.position + 1:
                item.setText(0, 'Next')      # what plays after the one playing
                item.setToolTip(0, 'Plays next')
        self.tree.verticalScrollBar().setValue(scrolled)
        self._update_times()
        upcoming = queue.order[queue.position + 1:] if queue.position >= 0 else queue.order
        total = sum(queue.tracks[index].get('duration') or 0 for index in upcoming)
        self.status.setText(f'{len(queue.order)} tracks, {len(upcoming)} to come ({_duration(total)})' if queue.order else 'Empty')

    def show_current(self):
        item = self.tree.topLevelItem(self.position) if self.position >= 0 else None
        if item is not None:
            self.tree.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)

    def _selected(self):
        return [item.data(0, INDEX) for item in self.tree.selectedItems()]

    def _remove_selected(self):
        if self._selected():
            self.remove.emit(self._selected())

    def _dropped(self):
        self.reorder.emit([self.tree.topLevelItem(row).data(0, INDEX) for row in range(self.tree.topLevelItemCount())])

    def _context_menu(self, position):
        menu = QMenu(self)
        item = self.tree.itemAt(position)
        if item is not None:
            menu.addAction('Play', lambda: self.jump.emit(self.tree.indexOfTopLevelItem(item)))
            menu.addAction('Remove', self._remove_selected)
            menu.addSeparator()
        if self.save_as_playlist is not None:
            menu.addAction('Save as playlist...', self.save_as_playlist).setEnabled(self.tree.topLevelItemCount() > 0)
            menu.addSeparator()
        upcoming = list(range(self.position + 1, self.tree.topLevelItemCount()))
        action = menu.addAction('Clear upcoming', lambda: self.remove.emit([self.tree.topLevelItem(row).data(0, INDEX) for row in upcoming]))
        action.setEnabled(bool(upcoming))
        menu.exec(self.tree.viewport().mapToGlobal(position))
