"""The left side: the tracks in a tree, by genre, year or artist, with a filter."""

import json
import os
import re

from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QKeySequence, QPalette
from PyQt6.QtWidgets import QWidgetAction
from PyQt6.QtWidgets import QSizePolicy, QStyle, QStyleOptionButton, QStyleOptionToolButton
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLayout,
                             QLineEdit, QMenu, QPushButton, QScrollArea, QStyledItemDelegate, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from cutemusic import colors as markers, util
from cutemusic.icons import set_line_icon
from cutemusic.style import make_segmented


__all__ = ['Browser', 'MODES']


# Measured without touching the buttons: changing a button's style or text to measure it, then changing it back, asks
# for a new layout each time, whose sizing measures it again (a loop, at 100% of a CPU while the pane is shown).

def _tool_button_width(button, with_text):
    option = QStyleOptionToolButton()
    button.initStyleOption(option)
    width = option.iconSize.width()
    height = option.iconSize.height()
    if with_text and option.text:
        metrics = button.fontMetrics()
        text = metrics.size(Qt.TextFlag.TextShowMnemonic, option.text)
        width += 4 + text.width() + 2 * metrics.horizontalAdvance(' ')
        height = max(height, text.height())
        option.toolButtonStyle = Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    else:
        option.toolButtonStyle = Qt.ToolButtonStyle.ToolButtonIconOnly
    if button.menu() is not None and button.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup:
        width += button.style().pixelMetric(QStyle.PixelMetric.PM_MenuButtonIndicator, option, button)
    return button.style().sizeFromContents(QStyle.ContentsType.CT_ToolButton, option, QSize(width, height), button)


def _push_button_width(button, label):
    option = QStyleOptionButton()
    button.initStyleOption(option)
    option.text = label
    width = height = 0
    if not option.icon.isNull():
        width, height = option.iconSize.width() + 4, option.iconSize.height()
    if label:
        text = button.fontMetrics().size(Qt.TextFlag.TextShowMnemonic, label)
        width += text.width()
        height = max(height, text.height())
    else:
        width -= 4 if width else 0
    height = max(height, button.fontMetrics().height())
    return button.style().sizeFromContents(QStyle.ContentsType.CT_PushButton, option, QSize(width, height), button).width()


MODES = [('year', 'Year'), ('artist', 'Artist'), ('album', 'Album'), ('folder', 'Folder')]
UNKNOWN_GENRE = 'Unknown genre'
TRACK = Qt.ItemDataRole.UserRole          # a track item: its Track
TRACKS = Qt.ItemDataRole.UserRole + 1     # a group item: its tracks, in play order
COLORS = Qt.ItemDataRole.UserRole + 2     # the color markers drawn after the name: a song's, those all of a group's have
TRACKS_MIME = 'application/x-ag-cute-music-tracks'      # dragged to the queue: the tracks' paths, JSON


class _LibraryTree(QTreeWidget):
    """Items dragged out (to the Queue): their tracks, in play order; file URLs too, for other applications."""

    def mimeTypes(self):
        return [TRACKS_MIME, 'text/uri-list']

    def mimeData(self, items):
        from PyQt6.QtCore import QMimeData, QUrl
        tracks, seen = [], set()
        for item in items:
            for track in ([item.data(0, TRACK)] if item.data(0, TRACK) else item.data(0, TRACKS) or []):
                if track.path not in seen and not track.get('pending'):
                    seen.add(track.path)
                    tracks.append(track)
        data = QMimeData()
        data.setData(TRACKS_MIME, json.dumps([track.path for track in tracks]).encode('utf-8'))
        data.setUrls([QUrl.fromLocalFile(track.path) for track in tracks])
        return data


class _NativeRows(QStyledItemDelegate):
    """The rows as macOS lists draw them: taller (room around the text), the time a secondary grey, right-aligned,
    in figures of one width (the times line up)."""

    PADDING = 8         # px added to the text's height

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width(), max(size.height(), option.fontMetrics.height() + self.PADDING))

    def paint(self, painter, option, index):
        colors = index.data(COLORS) if index.column() == 0 else None
        if not colors:
            return super().paint(painter, option, index)
        # the row as always (its selection the full width), its name cut shorter: the dots in the room left at its right
        from PyQt6.QtGui import QFontMetrics
        from PyQt6.QtWidgets import QStyleOptionViewItem
        item = QStyleOptionViewItem(option)
        self.initStyleOption(item, index)
        widget = item.widget
        style = widget.style() if widget is not None else QApplication.style()
        text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, item, widget)
        room = markers.dots_width(len(colors)) + 10
        item.text = QFontMetrics(item.font).elidedText(item.text, Qt.TextElideMode.ElideRight, max(0, text_rect.width() - room))
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, item, painter, widget)
        markers.draw_dots(painter, QRectF(item.rect).adjusted(0, 0, -6, 0), colors)

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index.column() == 1:
            option.displayAlignment = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            color = QColor(option.palette.color(QPalette.ColorRole.Text))
            color.setAlphaF(0.55)
            option.palette.setColor(QPalette.ColorRole.Text, color)
            if hasattr(QFont, 'Tag'):       # Qt 6.7+: tabular figures
                font = QFont(option.font)
                font.setFeature(QFont.Tag('tnum'), 1)
                option.font = font


def _common_colors(tracks):
    """The colors all of tracks have (an album, an artist: its songs), in the six's order."""
    if not tracks:
        return []
    common = set(tracks[0].get('colors') or [])
    for track in tracks[1:]:
        common &= set(track.get('colors') or [])
        if not common:
            return []
    return [name for name in markers.NAMES if name in common]


def _duration(seconds):
    seconds = int(seconds or 0)
    return f'{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}' if seconds >= 3600 else f'{seconds // 60}:{seconds % 60:02d}'


# what joins artists: Diana Krall & Tony Bennett, Diana Krall feat. X, Diana Krall, X, Diana Krall - X; the first one
# leads (a / or a dash only with spaces around it: AC/DC and Jay-Z are one)
_JOINS = re.compile(r'\s*(?:&|\+|,|;|\s/\s|\s[-–—]\s|\s(?:and|with|feat\.?|ft\.?|featuring|vs\.?|y|e|et|und)\s)\s*', re.IGNORECASE)


def _size(size):
    """A file's size, as people read it: 34.2 MB, 812 kB."""
    return f'{size / 1048576:.1f} MB' if size >= 1048576 else f'{max(1, round(size / 1024))} kB'


def _lead_artist(name):
    lead = _JOINS.split(' ' + name + ' ', maxsplit=1)[0].strip()
    return lead or name


def _album_key(track):
    return track.album_artist.lower(), (track.get('album') or '').lower()


_DISC_FOLDER = re.compile(r'^(cd|dis[ck])\s*\d+$', re.IGNORECASE)


def _album_folder(track):
    """The folder an album is in: a disc folder (CD1, Disc 2) is part of the album above it."""
    folder = os.path.dirname(track.path)
    return os.path.dirname(folder) if _DISC_FOLDER.match(os.path.basename(folder)) else folder


def _album_group_key(track):
    """An album as a whole: its title in its folder, whatever its tracks' artists (a compilation without an album
    artist tag is one album, not one per artist), and two albums of the same title (Greatest Hits) kept apart."""
    return (track.get('album') or '').lower(), _album_folder(track)


class _FlowLayout(QLayout):
    """Widgets left to right, wrapping to the next line when the width is used up (Qt's flow layout example)."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._place(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._place(rect, move=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _place(self, rect, move):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line = area.x(), area.y(), 0
        for item in self._items:
            if item.widget() is not None and item.widget().isHidden():
                continue
            hint = item.sizeHint()
            if x + hint.width() > area.right() + 1 and line > 0:
                x, y, line = area.x(), y + line + self._spacing, 0
            if move:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line = max(line, hint.height())
        return y + line - rect.y() + margins.bottom()


class _GenrePanel(QFrame):
    """The genres to pick from: a panel of large toggles, scrolled when there are many, a field to find one, All
    genres (none ticked) and a close button (or Esc, or a click outside it)."""

    def __init__(self, button):
        super().__init__(button.window(), Qt.WindowType.Popup)
        self.button = button
        self.setObjectName('genrePanel')
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel('Genres')
        font = title.font()
        font.setPointSizeF(font.pointSizeF() * 1.3)
        font.setBold(True)
        title.setFont(font)
        head.addWidget(title)
        self.count = QLabel()
        self.count.setStyleSheet('color: gray')
        head.addWidget(self.count)
        head.addStretch(1)
        self.all = QPushButton('All genres')
        self.all.setToolTip('No genre filter: every genre shown')
        self.all.clicked.connect(lambda: (button.clear(), self._sync()))
        head.addWidget(self.all)
        close = QToolButton()
        close.setText('\u2715')
        close.setToolTip('Close (Esc)')
        close.setAutoRaise(True)
        font = close.font()
        font.setPointSizeF(font.pointSizeF() * 1.4)
        close.setFont(font)
        close.clicked.connect(self.close)
        head.addWidget(close)
        layout.addLayout(head)
        self.find = QLineEdit()
        self.find.setPlaceholderText('Find a genre')
        self.find.setClearButtonEnabled(True)
        self.find.textChanged.connect(self._find)
        layout.addWidget(self.find)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.scroll, 1)
        self.toggles = {}

    def fill(self):
        inside = QWidget()
        flow = _FlowLayout(inside, spacing=8)
        flow.setContentsMargins(0, 0, 0, 0)
        self.toggles = {}
        chosen = {g.lower() for g in self.button.selected}
        for genre in self.button.genres:
            toggle = QPushButton(genre)
            toggle.setCheckable(True)
            toggle.setChecked(genre.lower() in chosen)
            toggle.setObjectName('genreToggle')
            toggle.setCursor(Qt.CursorShape.PointingHandCursor)
            toggle.toggled.connect(lambda on, genre=genre: (self.button._toggled(genre, on), self._sync()))
            flow.addWidget(toggle)
            self.toggles[genre] = toggle
        self.scroll.setWidget(inside)
        self._find(self.find.text())
        self._sync()

    def _sync(self):
        chosen = {g.lower() for g in self.button.selected}
        for genre, toggle in self.toggles.items():
            if toggle.isChecked() != (genre.lower() in chosen):
                toggle.blockSignals(True)
                toggle.setChecked(genre.lower() in chosen)
                toggle.blockSignals(False)
        self.all.setEnabled(bool(chosen))
        self.count.setText(f'{len(chosen)} of {len(self.toggles)} picked' if chosen else f'{len(self.toggles)}, all shown')

    def _find(self, text):
        words = text.lower().split()
        for genre, toggle in self.toggles.items():
            toggle.setVisible(all(word in genre.lower() for word in words))
        self.scroll.widget().layout().invalidate()

    def open(self):
        window = self.button.window()
        size = QSize(max(420, int(window.width() * 0.55)), max(320, int(window.height() * 0.7)))
        self.resize(size)
        below = self.button.mapToGlobal(QPoint(0, self.button.height() + 2))
        screen = (self.button.screen() or QApplication.primaryScreen()).availableGeometry()
        x = min(max(screen.left(), below.x()), screen.right() - size.width())
        y = min(below.y(), screen.bottom() - size.height())
        self.move(x, y)
        self.fill()
        self.find.clear()
        self.show()
        self.find.setFocus()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)


GENRE_STYLE = """
QFrame#genrePanel { border: 1px solid palette(mid); border-radius: 8px; }
QPushButton#genreToggle { font-size: 15px; padding: 7px 14px; border: 1px solid palette(mid); border-radius: 14px; }
QPushButton#genreToggle:checked { background: #ff3b30; color: white; border-color: #ff3b30; }
"""


class _GenreButton(QToolButton):
    """Genres: a panel of them to tick, several at once (_GenrePanel); none ticked: all. changed() after a change;
    selected: the ticked ones (kept in the settings). Red while a genre filter is on."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        set_line_icon(self, 'genre')
        self.setIconSize(QSize(16, 16))
        self.panel = None
        self.clicked.connect(self._open)
        self.selected = set(util.settings().value('browser/genres', [], type=list) or [])
        self.genres = []
        self._label()

    def _open(self):
        if self.panel is None:
            self.panel = _GenrePanel(self)
            self.panel.setStyleSheet(GENRE_STYLE)
        self.panel.open()

    def set_genres(self, genres):
        self.genres = sorted(genres, key=lambda g: (g == UNKNOWN_GENRE, g.lower()))
        known = {g.lower() for g in self.genres}
        self.selected = {g for g in self.selected if g.lower() in known}
        self._fill()
        self._label()

    def clear(self, notify=True):
        if not self.selected:
            return
        self.selected = set()
        self._save()
        self._fill()
        self._label()
        if notify:
            self.changed.emit()

    def _fill(self):
        if self.panel is not None and self.panel.isVisible():
            self.panel.fill()

    def select_only(self, genre):
        """Only genre ticked (as the library spells it); False when the library has none of it."""
        known = next((g for g in self.genres if g.lower() == genre.lower()), None)
        if known is None:
            return False
        if self.selected != {known}:
            self.selected = {known}
            self._save()
            self._fill()
            self._label()
            self.changed.emit()
        return True

    def _toggled(self, genre, on):
        self.selected = {g for g in self.selected if g.lower() != genre.lower()} | ({genre} if on else set())
        self._save()
        self._label()
        self.changed.emit()

    def _save(self):
        util.settings().setValue('browser/genres', sorted(self.selected))

    def _label(self):
        names = sorted(self.selected, key=str.lower)
        if not names:
            self.setText('All genres')
        elif len(names) <= 2:
            self.setText(', '.join(names))
        else:
            self.setText(f'{names[0]} +{len(names) - 1}')
        # a genre filter on: red, so a shorter list is not taken for the whole library
        self.setStyleSheet('QToolButton { color: #ff3b30; border: 1.5px solid #ff3b30; border-radius: 5px; padding: 1px 4px; }'
                           if names else '')
        self.setToolTip(('Genre filter on: ' + ', '.join(names) + ' (click to change)') if names else 'Filter by genre')


class _SourceButton(QToolButton):
    """Whose library is shown: this computer's, or one shared on the LAN (sharing.py). chosen(library dict, or None
    for this computer's); connect_to() for Connect to a library... (by its address)."""

    chosen = pyqtSignal(object)
    connect_to = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        set_line_icon(self, 'computer')
        self.setIconSize(QSize(16, 16))
        self.menu_ = QMenu(self)
        self.setMenu(self.menu_)
        self.libraries = []
        self.current = None
        self.compact = False
        self.set_current(None)

    def full_width(self):
        """Its width with its name shown (as it is when not compact)."""
        return _tool_button_width(self, True).width()

    def set_compact(self, compact):
        """The icon alone (a narrow pane: its name in the tooltip), or with its name."""
        if compact != self.compact:
            self.compact = compact
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly if compact else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

    def minimumSizeHint(self):
        return _tool_button_width(self, False)

    def set_libraries(self, libraries):
        self.libraries = sorted(libraries, key=lambda library: (bool(library.get('web')), library['name'].lower()))
        self._fill()

    def set_current(self, library):
        self.current = library
        self.setText(library['name'] if library else 'This computer')
        if library and library.get('web'):
            self.setToolTip(f'The songs sent from the web page with the token {library["web"]}, and their negatives')
        else:
            self.setToolTip(f'The library of {library["name"]} ({library["address"]})' if library
                            else 'This computer\'s library (others on the LAN that share theirs are listed here)')
        self._fill()

    def _fill(self):
        self.menu_.clear()
        action = self.menu_.addAction('This computer', lambda: self.chosen.emit(None))
        action.setCheckable(True)
        action.setChecked(self.current is None)
        previous = None
        for library in self.libraries:      # (the libraries on the LAN, then the web page's tokens: web.py)
            kind = bool(library.get('web'))
            if kind != previous:
                if kind:
                    self.menu_.addSection('Sent from the web page')
                else:
                    self.menu_.addSeparator()
                previous = kind
            action = self.menu_.addAction(library['name'], lambda library=library: self.chosen.emit(library))
            action.setCheckable(True)
            action.setChecked(self.current is not None and self.current.get('address') == library['address'])
            action.setToolTip(library['address'])
        self.menu_.addSeparator()
        self.menu_.addAction('Connect to a library...', self.connect_to.emit)


class _ModeButtons(QWidget):
    """Year | Artist | Album | Folder as one segmented control; the part of a QComboBox's interface Browser and the
    window use (currentData, currentIndex, setCurrentIndex, findData, currentIndexChanged)."""

    currentIndexChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.group = QButtonGroup(self)
        buttons = []
        for index, (key, label) in enumerate(MODES):
            button = QPushButton(label)
            set_line_icon(button, key)
            button.setIconSize(QSize(16, 16))
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setToolTip(f'By {label.lower()} (Ctrl+Shift+{index + 1})')
            self.group.addButton(button, index)
            layout.addWidget(button)
            buttons.append(button)
        make_segmented('library-modes', *buttons)
        self.buttons = buttons
        self.compact = False
        self.group.button(0).setChecked(True)
        self.group.idClicked.connect(self._clicked)
        self._index = 0

    def _clicked(self, index):
        self.setCurrentIndex(index)

    def _width(self, labels):
        return sum(_push_button_width(button, label if labels else '') for button, (key, label) in zip(self.buttons, MODES))

    def full_width(self):
        return self._width(True)

    def set_compact(self, compact):
        """Icons alone (a narrow pane: the names in the tooltips), or icons and names."""
        if compact != self.compact:
            self.compact = compact
            for button, (key, label) in zip(self.buttons, MODES):
                button.setText('' if compact else label)

    def minimumSizeHint(self):
        return QSize(self._width(False), super().minimumSizeHint().height())

    def currentIndex(self):
        return self._index

    def currentData(self):
        return MODES[self._index][0]

    def findData(self, key):
        return next((index for index, (name, label) in enumerate(MODES) if name == key), -1)

    def setCurrentIndex(self, index):
        if not 0 <= index < len(MODES):
            return
        self.group.button(index).setChecked(True)
        if index != self._index:
            self._index = index
            self.currentIndexChanged.emit(index)


class Browser(QWidget):
    """play(tracks, index): a double click or Enter; queue(tracks, next): the context menu."""

    fill_playlist_menu = None       # (menu, tracks): the window's, the playlists to add them to
    make_negatives = None           # (tracks): the window's, their negatives made (negatives.py); None: not here
    pending_menu = None             # (menu, tracks): the window's, for negatives not made yet (Remove, Stop...)
    negatives_menu = None           # (menu, tracks): the window's, Delete negative for the songs that have one
    delete = None                   # (made, pending): the window's, Delete... on any item (the Negatives tab)
    fill_web_menu = None            # (menu, tracks): the window's, Copy to web library (web.py); None: not here

    FONT_STEPS = (-3, 8)            # points from the application's size, at most
    font_step = 0
    font_changed = pyqtSignal(object)       # the QFont of the lists

    play = pyqtSignal(list, int)
    play_now = pyqtSignal(list)
    copy_local = pyqtSignal(list)               # tracks of a library on the LAN: copied into this computer's
    selected = pyqtSignal(object, list)         # (a track for the cover, its captions; [] a track's own): the item picked
    queue = pyqtSignal(list, bool)              # (tracks, next up)
    set_colors = pyqtSignal(list, str, bool)    # (tracks, a color, on): right-click -> a dot (written in their files)
    edit_tags = pyqtSignal(list, str)           # (tracks, what they are: the item's name): Edit metadata...
    trash = pyqtSignal(list, str)               # (tracks, what they are): Move to Trash (Delete), confirmed by the window

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tracks = []
        self.root = ''
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 0, 6)
        top = QHBoxLayout()         # whose library, then the views; the filter under them, the full width
        self.source = _SourceButton()
        top.addWidget(self.source)
        top.addStretch(1)
        self.mode = _ModeButtons()
        top.addWidget(self.mode)
        top.addStretch(1)
        layout.addLayout(top)
        search = QHBoxLayout()      # the genres (several at a time) and the words searched for: both narrow the tree
        self.genres = _GenreButton()
        search.addWidget(self.genres)
        # the color markers: the songs with any of those ticked (none ticked: all), with the genres and the words
        self.colors = markers.ColorRow(spacing=0)
        self.colors.setToolTip('Only the songs with these colors (any of them)')
        self.colors.set_checked(set(util.settings().value('browser/colors', [], type=list) or []) & set(markers.NAMES))
        search.addWidget(self.colors)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText('Search')
        self.filter.setClearButtonEnabled(True)
        # focused only when clicked (or Find, Cmd+F): not by itself when the window opens, nor by Tab
        self.filter.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.filter.setProperty('space_plays_when_empty', True)     # Space: play / pause until something is typed
        search.addWidget(self.filter, 1)
        # A- | A+: the text size of the lists (the library, the queue, the history), kept in the settings
        search.addSpacing(6)
        box = QWidget()                 # glued together: one segmented control, narrow (as the views' buttons)
        sizes = QHBoxLayout(box)
        sizes.setContentsMargins(0, 0, 0, 0)
        sizes.setSpacing(0)
        self.smaller, self.larger = QPushButton('A−'), QPushButton('A+')
        for button, tip, step in ((self.smaller, 'Smaller text', -1), (self.larger, 'Larger text', 1)):
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedWidth(button.fontMetrics().horizontalAdvance('A+') + 16)
            button.clicked.connect(lambda checked, step=step: self.set_font_step(self.font_step + step))
            sizes.addWidget(button)
        make_segmented('font-size', self.smaller, self.larger)
        search.addWidget(box)
        layout.addLayout(search)
        self.tree = _LibraryTree()
        self.tree.setHeaderLabels(['Name', 'Time'])
        self.tree.setDragEnabled(True)          # to the Queue tab
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.tree.setUniformRowHeights(True)
        self.tree.setItemDelegate(_NativeRows(self.tree))
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)      # a name too long for the pane: cut with an ...
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, self.tree.header().ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, self.tree.header().ResizeMode.ResizeToContents)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        layout.addWidget(self.tree, 1)
        # what is selected, explicitly: a double click no longer replaces the queue
        actions = QHBoxLayout()
        actions.setSpacing(0)
        self.action_buttons = []
        for label, tip, slot in (('Play', 'Play the selection now: it replaces the queue', lambda tracks: self.play.emit(tracks, 0)),
                                 ('Play next', 'After the track playing', lambda tracks: self.queue.emit(tracks, True)),
                                 ('Add to queue', 'At the end of the queue (or drag it to the Queue tab)', lambda tracks: self.queue.emit(tracks, False))):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda checked, slot=slot: self._selected_tracks() and slot(self._selected_tracks()))
            actions.addWidget(button)
            self.action_buttons.append(button)
        make_segmented('library-actions', *self.action_buttons)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.status = QLabel()
        self.status.setStyleSheet('color: palette(mid)')
        # a long line is cut, not a reason for the pane to be wider (the splitter stays where it was put)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.status)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self._selection_changed()
        self.tree.currentItemChanged.connect(self._current_changed)
        self.font_step = util.settings().value('browser/font_step', 0, type=int)

        mode = util.settings().value('browser/mode', 'artist', type=str)
        self.mode.setCurrentIndex(max(0, self.mode.findData(mode if mode in dict(MODES) else 'artist')))
        self.genres.changed.connect(self.rebuild)
        self.colors.toggled.connect(self._colors_changed)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self._filter_timer = QTimer(self, singleShot=True, interval=200)
        self._filter_timer.timeout.connect(self.rebuild)
        self.filter.textChanged.connect(self._filter_timer.start)
        self.tree.itemActivated.connect(self._activated)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        play = QAction('Play', self.tree)
        play.setShortcuts([QKeySequence(Qt.Key.Key_Return), QKeySequence(Qt.Key.Key_Enter)])
        play.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        play.triggered.connect(lambda: self._activated(self.tree.currentItem(), keyboard=True))
        self.tree.addAction(play)
        trash = QAction('Move to Trash', self.tree)
        trash.setShortcuts([QKeySequence(Qt.Key.Key_Delete), QKeySequence(Qt.Key.Key_Backspace),
                            QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_Backspace)])     # (Cmd+Backspace on macOS)
        trash.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        trash.triggered.connect(self._trash_selected)
        self.tree.addAction(trash)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_top()

    def _fit_top(self):
        """The library's and the views' buttons with their names while there is room, their icons alone when the
        pane is made narrower (it can be: its width is not held by them, nor by the songs' names, cut with an ...)."""
        margins = self.layout().contentsMargins()
        need = self.source.full_width() + self.mode.full_width() + 24 + margins.left() + margins.right()
        # a few pixels either way of the line, so a width at it does not flip them at every layout
        compact = self.width() < need + (8 if self.source.compact else 0)
        self.source.set_compact(compact)
        self.mode.set_compact(compact)

    def list_font(self):
        from PyQt6.QtWidgets import QApplication
        font = QFont(QApplication.font())
        font.setPointSizeF(max(6.0, font.pointSizeF() + self.font_step))
        return font

    def set_font_step(self, step):
        """The lists' text size step points from the application's (A− A+); the window gives it to the queue and the
        history too (font_changed)."""
        self.font_step = max(self.FONT_STEPS[0], min(self.FONT_STEPS[1], step))
        util.settings().setValue('browser/font_step', self.font_step)
        self.smaller.setEnabled(self.font_step > self.FONT_STEPS[0])
        self.larger.setEnabled(self.font_step < self.FONT_STEPS[1])
        font = self.list_font()
        self.tree.setFont(font)
        self.rebuild()          # the group rows from the tree's font
        self.font_changed.emit(font)

    def set_tracks(self, tracks, root):
        self.tracks = tracks
        self.root = root
        self.genres.set_genres({genre for track in tracks for genre in (track.get('genres') or [UNKNOWN_GENRE])})
        self.rebuild()

    def has_filters(self):
        return bool(self.filter.text() or self.genres.selected or self.colors.checked())

    def _colors_changed(self, name=None, on=None):
        util.settings().setValue('browser/colors', [name for name in markers.NAMES if name in self.colors.checked()])
        self.rebuild()

    def clear_filters(self):
        """No words, all genres; the tree rebuilt now."""
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.blockSignals(False)
        self._filter_timer.stop()
        self.genres.clear(notify=False)
        if self.colors.checked():
            self.colors.set_checked(set())
            util.settings().setValue('browser/colors', [])
        self.rebuild()

    def view_state(self):
        """What the browser shows now (the words, genres and colours, the groups open, the selection, where it is
        scrolled), for restore_view_state(): the TAPES button's way back."""
        item = self.tree.currentItem()
        return {'filter': self.filter.text(), 'genres': set(self.genres.selected), 'colors': set(self.colors.checked()),
                'expanded': self._expanded_paths(), 'current': self._item_path(item) if item is not None else None,
                'selected': [self._item_path(item) for item in self.tree.selectedItems()],
                'scroll': self.tree.verticalScrollBar().value()}

    def restore_view_state(self, state):
        self.filter.blockSignals(True)
        self.filter.setText(state['filter'])
        self.filter.blockSignals(False)
        self._filter_timer.stop()
        if self.genres.selected != state['genres']:
            self.genres.selected = set(state['genres'])
            self.genres._save()
            self.genres._fill()
            self.genres._label()
        if set(self.colors.checked()) != state['colors']:
            self.colors.set_checked(state['colors'])
            util.settings().setValue('browser/colors', [name for name in markers.NAMES if name in state['colors']])
        self.rebuild()
        self.tree.collapseAll()
        self._restore_expanded(state['expanded'])
        current = self._find_path(state['current']) if state['current'] else None
        if current is not None:
            self.tree.setCurrentItem(current)
        self.tree.clearSelection()
        for path in state['selected']:
            item = self._find_path(path)
            if item is not None:
                item.setSelected(True)
        scroll = state['scroll']
        self.tree.verticalScrollBar().setValue(scroll)
        QTimer.singleShot(0, lambda: self.tree.verticalScrollBar().setValue(scroll))     # (after the layout)

    def _mode_changed(self):
        util.settings().setValue('browser/mode', self.mode.currentData())
        # what is selected, found again in the new view (its tree has other parents: rebuild's own restoring by the
        # item's path cannot)
        item = self.tree.currentItem()
        track = item.data(0, TRACK) if item is not None else None
        paths = {t.path for t in (item.data(0, TRACKS) or [])} if item is not None and track is None else None
        self.rebuild()
        if track is not None:
            self.select_track(track)
        elif paths:
            self._select_group(paths)

    def go_to(self, track, view):
        """The track's album in the Album view, or its artist in the Artist view: selected, expanded, in view."""
        self.mode.setCurrentIndex(self.mode.findData(view))
        self.select_track(track, view)
        self.tree.setFocus()

    def _select_group(self, paths):
        """The group in the tree whose tracks are most like paths (the same album, artist, year or folder when the
        view has it; else the closest: an artist under a genre, say), selected, expanded and in view."""
        best, best_score, best_depth = None, 0.0, 0
        items = [(self.tree.invisibleRootItem(), 0)]
        while items:
            parent, depth = items.pop()
            for index in range(parent.childCount()):
                child = parent.child(index)
                if child.data(0, TRACK) is not None:
                    continue
                members = {t.path for t in (child.data(0, TRACKS) or [])}
                common = len(members & paths)
                if not common:
                    continue
                score = common / len(members | paths)       # 1: the same tracks
                if score > best_score or (score == best_score and depth < best_depth):
                    best, best_score, best_depth = child, score, depth
                if score < 1.0:
                    items.append((child, depth + 1))
        if best is None:
            return
        parent = best.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        self.tree.setCurrentItem(best)
        self.tree.scrollToItem(best, QAbstractItemView.ScrollHint.PositionAtTop)

    # The tree

    def _matching(self):
        words = self.filter.text().lower().split()
        genres = {genre.lower() for genre in self.genres.selected}
        tracks = self.tracks
        if genres:
            tracks = [track for track in tracks if genres & {g.lower() for g in (track.get('genres') or [UNKNOWN_GENRE])}]
        colors = self.colors.checked()
        if colors:
            tracks = [track for track in tracks if colors & set(track.get('colors') or ())]
        if not words:
            return tracks
        found = []
        for track in tracks:
            text = ' '.join([track.display_title, track.get('artist', ''), track.get('albumartist', ''), track.get('album', ''),
                             ' '.join(track.get('genres', [])), str(track.get('year') or ''),
                             ' '.join(track.get('colors') or [])]).lower()
            if all(word in text for word in words):
                found.append(track)
        return found

    def rebuild(self):
        expanded = self._expanded_paths()
        scrolled = self.tree.verticalScrollBar().value()
        current = self.tree.currentItem()
        current = self._item_path(current) if current is not None else None
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        tracks = self._matching()
        mode = self.mode.currentData()
        if self.owners:         # the Negatives tab: by whose they are first (Local; a web account, then its tokens)
            owners = {}
            for track in tracks:
                owners.setdefault(track.get('owner') or 'Local', {}).setdefault(track.get('token'), []).append(track)
            for owner in sorted(owners, key=lambda name: (name != 'Local', name == 'No account', name.lower())):
                tokens = owners[owner]
                members = [track for group in tokens.values() for track in group]
                item = self._group(self.tree, owner, members)
                for token in sorted(tokens, key=lambda token: token or ''):
                    parent = item if token is None else self._group(item, f'Token {token}', tokens[token])
                    self._build(parent, tokens[token], mode)
        else:
            self._build(self.tree, tracks, mode)
        self._restore_expanded(expanded)
        if current is not None:
            item = self._find_path(current)
            if item is not None:
                self.tree.setCurrentItem(item, 0, self.tree.selectionModel().SelectionFlag.NoUpdate)
                item.setSelected(True)
        self.tree.verticalScrollBar().setValue(scrolled)
        self.tree.setUpdatesEnabled(True)
        albums = len({_album_group_key(track) for track in tracks})
        total = sum(track.get('duration') or 0 for track in tracks)
        self.status.setText(f'{len(tracks)} tracks, {albums} albums, {_duration(total)}' if tracks else
                            ('Nothing matches' if self.tracks else f'No music in {self.root}'))

    owners = False      # the Negatives tab: its tracks grouped by their 'owner' and 'token' first (window.py)
    made = False        # the Negatives tab: a Made column, when each was made (its file's time)

    def show_made(self):
        """A Made column after Time: when each negative was made (its file written)."""
        self.made = True
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(['Name', 'Time', 'Size', 'Made'])
        for column in (2, 3):
            self.tree.header().setSectionResizeMode(column, self.tree.header().ResizeMode.ResizeToContents)
        self.rebuild()

    def _build(self, parent, tracks, mode):
        """The tree of tracks under parent, as the view (mode) wants it."""
        if mode == 'year':
            groups = {}
            for track in tracks:
                groups.setdefault(track.get('year') or 0, []).append(track)
            for year in sorted(groups, reverse=True):
                members = groups[year]
                self._add_albums(self._group(parent, str(year) if year else 'Unknown year', members), members, with_artist=True)
        elif mode == 'artist':
            self._add_artists(parent, tracks)
        elif mode == 'album':
            self._add_albums(parent, tracks, by_title=True)
        else:
            self._add_folders(tracks, parent, tracks[0].get('root') if tracks and tracks[0].get('root') else None)

    def _group(self, parent, name, members, ordered=None):
        # (a negative being made, queued or failed is shown, not played: not in its group's tracks)
        members = [track for track in members if not track.get('pending')]
        ordered = [track for track in ordered if not track.get('pending')] if ordered is not None else None
        item = QTreeWidgetItem(parent, [name, ''])
        item.setData(0, TRACKS, ordered if ordered is not None else self._play_order(members))
        item.setData(0, COLORS, _common_colors(members))
        item.setToolTip(0, f'{name}: {len(members)} tracks')
        item.setFont(0, QFont(self.tree.font()))       # regular weight at every level, as macOS lists (no bold)
        return item

    @staticmethod
    def _play_order(tracks):
        """By album (artist, year, album), then disc and track number."""
        return sorted(tracks, key=lambda t: (t.album_artist.lower(), t.get('year') or 0, (t.get('album') or '').lower()) + t.sort_key())

    def _add_artists(self, parent, tracks):
        """An artist, its albums, their tracks. Artists starting with the same one (Diana Krall, Diana Krall & Tony
        Bennett, Diana Krall feat. X) go under it, one level down; alone (Simon & Garfunkel) an artist stays as is."""
        artists = {}
        for track in tracks:
            artists.setdefault(track.album_artist.lower(), (track.album_artist, []))[1].append(track)
        leads = {}
        for key, (name, members) in artists.items():
            lead = _lead_artist(name)
            leads.setdefault(lead.lower(), (lead, []))[1].append(key)
        entries = []        # (sort key, name, the artist keys under it, grouped)
        for lead_key, (lead, keys) in leads.items():
            if len(keys) > 1:
                exact = next((artists[key][0] for key in keys if key == lead_key), lead)
                entries.append((lead_key, exact, keys, True))
            else:
                entries.append((keys[0], artists[keys[0]][0], keys, False))
        for sort_key, name, keys, grouped in sorted(entries):
            if not grouped:
                members = artists[keys[0]][1]
                self._add_albums(self._group(parent, name, members), members)
                continue
            members = [track for key in keys for track in artists[key][1]]
            group = self._group(parent, name, members)
            group.setToolTip(0, f'{name}: {len(keys)} artists, {len(members)} tracks')
            # the lead artist alone first, then the others by name
            for key in sorted(keys, key=lambda key: (key != sort_key, key)):
                sub_name, sub_members = artists[key]
                self._add_albums(self._group(group, sub_name, sub_members), sub_members)

    def _add_albums(self, parent, tracks, with_artist=False, by_title=False):
        """The albums of tracks under parent: by year (under an artist), by artist (with_artist: "Artist – Album") or
        by title (by_title, the Album view: "Album – Artist")."""
        albums = {}
        for track in tracks:
            albums.setdefault(_album_group_key(track) if by_title else _album_key(track), []).append(track)
        if by_title:
            order = lambda k: (k[0] or '\uffff', albums[k][0].album_artist.lower(), k[1])      # the albums without a name last
        elif with_artist:
            order = lambda k: (k[0], k[1])
        else:
            order = lambda k: (albums[k][0].get('year') or 0, k[1])
        for key in sorted(albums, key=order):
            members = sorted(albums[key], key=lambda t: t.sort_key())
            first = members[0]
            name = first.get('album') or 'Unknown album'
            if by_title:
                artists = {t.album_artist.lower() for t in members}
                name = f'{name} – {first.album_artist if len(artists) == 1 else "Various artists"}'
            elif with_artist:
                name = f'{first.album_artist} – {name}'
            elif first.get('year'):
                name = f'{name} ({first["year"]})'
            album = self._group(parent, name, members, members)
            album.setText(1, _duration(sum(t.get('duration') or 0 for t in members)))
            for track in members:
                self._add_track(album, track, members)

    def _add_track(self, parent, track, siblings):
        number = f'{track["track"]:02d}  ' if track.get('track') else ''
        title = track.display_title
        if track.get('artist') and track.get('artist') != track.album_artist:
            title += f' – {track["artist"]}'
        pending = track.get('pending')         # a negative not made yet: dimmed, its state in the Time column
        state = pending.split(':')[0] if pending else None      # (30%, queued, failed: the reason in the tooltip)
        item = QTreeWidgetItem(parent, [number + title, state if pending else _duration(track.get('duration'))])
        if pending:
            item.setToolTip(1, pending)
        elif self.made:         # its file's size and time (when it was made)
            try:
                info = os.stat(track.path)
            except OSError:
                info = None
            if info is not None:
                import time
                item.setText(2, _size(info.st_size))
                item.setText(3, time.strftime('%d %b %Y %H:%M', time.localtime(info.st_mtime)))
                for column in (2, 3):
                    item.setTextAlignment(column, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        item.setData(0, TRACK, track)
        item.setData(0, COLORS, list(track.get('colors') or []))
        item.setData(0, TRACKS, [sibling for sibling in siblings if not sibling.get('pending')])
        item.setToolTip(0, track.path)
        if pending:
            dim = self.palette().color(self.palette().ColorRole.PlaceholderText)
            item.setForeground(0, dim)
            item.setForeground(1, dim)

    def _add_folders(self, tracks, parent=None, root=None):
        top = parent if parent is not None else self.tree
        root = root or self.root
        folders = {}
        for track in tracks:
            folders.setdefault(os.path.dirname(track.path), []).append(track)
        items = {}

        def folder_item(path):
            if path in items:
                return items[path]
            if path == root or not path.startswith(root + os.sep):
                return top
            parent = folder_item(os.path.dirname(path))
            item = items[path] = QTreeWidgetItem(parent, [os.path.basename(path), ''])
            return item
        for path in sorted(folders, key=str.lower):
            members = sorted(folders[path], key=lambda t: t.sort_key())
            parent = folder_item(path)
            for track in members:
                self._add_track(parent, track, members)
        for path, item in items.items():
            under = [t for folder, members in folders.items() if folder == path or folder.startswith(path + os.sep) for t in members]
            item.setData(0, TRACKS, sorted(under, key=lambda t: (os.path.dirname(t.path).lower(),) + t.sort_key()))

    def _expanded_paths(self):
        paths = set()

        def walk(item, prefix):
            for index in range(item.childCount()):
                child = item.child(index)
                if child.isExpanded():
                    path = prefix + (child.text(0),)
                    paths.add(path)
                    walk(child, path)
        walk(self.tree.invisibleRootItem(), ())
        return paths

    @staticmethod
    def _item_path(item):
        path = []
        while item is not None:
            path.insert(0, item.text(0))
            item = item.parent()
        return tuple(path)

    def _find_path(self, path):
        item = self.tree.invisibleRootItem()
        for text in path:
            item = next((item.child(i) for i in range(item.childCount()) if item.child(i).text(0) == text), None)
            if item is None:
                return None
        return item

    def _restore_expanded(self, paths):
        def walk(item, prefix):
            for index in range(item.childCount()):
                child = item.child(index)
                path = prefix + (child.text(0),)
                if path in paths:
                    child.setExpanded(True)
                    walk(child, path)
        walk(self.tree.invisibleRootItem(), ())

    # Playing

    def _selected_tracks(self, pending=False):
        """The selected tracks (pending: the negatives not made yet among them, instead of the playable ones)."""
        tracks, seen = [], set()
        for item in self.tree.selectedItems():
            for track in ([item.data(0, TRACK)] if item.data(0, TRACK) else item.data(0, TRACKS) or []):
                if bool(track.get('pending')) != pending:
                    continue
                if track.path not in seen:
                    seen.add(track.path)
                    tracks.append(track)
        return tracks

    def _current_changed(self, item, previous):
        """The item picked, for the cover view: a track itself; a group (album, artist, year, folder) by its first
        track's cover, with its own captions."""
        if item is None:
            return
        track = item.data(0, TRACK)
        if track is not None:
            self.selected.emit(track, [])
            return
        tracks = item.data(0, TRACKS) or []
        if not tracks:
            return
        parent = item.parent()
        count = f'{len(tracks)} track' + ('s' if len(tracks) != 1 else '')
        self.selected.emit(tracks[0], [item.text(0), parent.text(0) if parent is not None else '', count])

    def _selection_changed(self):
        selected = bool(self.tree.selectedItems())
        for button in self.action_buttons:
            button.setEnabled(selected)

    def _activated(self, item, keyboard=False):
        """A double click (or Enter) on a track: it plays now, put after the one playing, the rest of the queue kept;
        on a group: open or closed (Enter; a double click does that itself)."""
        if item is None:
            return
        track = item.data(0, TRACK)
        if track is not None:
            if not track.get('pending'):        # (a negative not made yet: nothing to play)
                self.play_now.emit([track])
        elif keyboard:
            item.setExpanded(not item.isExpanded())

    def _context_menu(self, position):
        tracks = self._selected_tracks()
        pending = self._selected_tracks(pending=True)
        if not tracks and pending and self.pending_menu is not None:      # only negatives not made yet
            menu = QMenu(self)
            self.pending_menu(menu, pending)
            if self.delete is not None:
                menu.addSeparator()
                menu.addAction(self._delete_label(tracks, pending), lambda: self.delete(tracks, pending))
            menu.exec(self.tree.viewport().mapToGlobal(position))
            return
        if not tracks:
            return
        menu = QMenu(self)
        if not any(track.get('remote') for track in tracks):
            self._colors_menu(menu, tracks)
        if self.delete is not None:     # first, on whatever is clicked: the songs, an album, an artist, being made or not
            menu.addAction(self._delete_label(tracks, pending), lambda: self.delete(tracks, pending))
            menu.addSeparator()
        menu.addAction('Play (replaces the queue)', lambda: self.play.emit(tracks, 0))
        menu.addAction('Play next', lambda: self.queue.emit(tracks, True))
        menu.addAction('Add to queue', lambda: self.queue.emit(tracks, False))
        if self.fill_playlist_menu is not None:
            self.fill_playlist_menu(menu.addMenu('Add to playlist'), tracks)
        if self.make_negatives is not None and not any(track.get('remote') for track in tracks):
            menu.addAction('Make negative' + (f' ({len(tracks)} songs)' if len(tracks) > 1 else '') + ' (no singer, for karaoke)',
                           lambda: self.make_negatives(tracks))
        if self.negatives_menu is not None and not any(track.get('remote') for track in tracks):
            self.negatives_menu(menu, tracks)
        if self.fill_web_menu is not None and not any(track.get('remote') for track in tracks):
            self.fill_web_menu(menu.addMenu('Copy to web library'), tracks)
        item = self.tree.itemAt(position)
        track = item.data(0, TRACK) if item is not None else None
        clicked = track
        if track is None and item is not None:      # an album's node (in the Artist view: its songs by that artist)
            members = item.data(0, TRACKS) or []
            if members and len({_album_group_key(t) for t in members}) == 1 and members[0].get('album') and \
                    members[0].get('album').lower() in item.text(0).lower():
                track = members[0]
        if track is not None and track.get('album'):
            # the whole album of the song, in its order (from the library, whatever view or search shows of it)
            key = _album_group_key(track)
            album = sorted((t for t in self.tracks if _album_group_key(t) == key), key=lambda t: t.sort_key())
            menu.addSeparator()
            menu.addAction(f'Add album to queue ({len(album)} tracks)', lambda: self.queue.emit(album, False))
            if self.fill_playlist_menu is not None:
                self.fill_playlist_menu(menu.addMenu(f'Add album to playlist ({len(album)} tracks)'), album)
            menu.addAction('Play album next', lambda: self.queue.emit(album, True))
            menu.addSeparator()
            if self.mode.currentData() != 'album':
                menu.addAction('Go to album', lambda checked=False, track=track: self.go_to(track, 'album'))
            if self.mode.currentData() != 'artist':
                menu.addAction('Go to artist', lambda checked=False, track=track: self.go_to(track, 'artist'))
        track = clicked
        menu.addSeparator()
        if not any(track.get('remote') for track in tracks):
            # the song, or the album (artist, year, folder) clicked on: its songs; else the selection
            if item is not None and item.isSelected() and len(self.tree.selectedItems()) == 1:
                title = item.text(0) if track is None else track.display_title
            else:
                title = f'{len(tracks)} songs selected'
            menu.addAction('Edit metadata...', lambda: self.edit_tags.emit(tracks, title))
            if self.delete is None:
                menu.addAction('Move to Trash...', lambda: self.trash.emit(tracks, title))
        if any(track.get('remote') for track in tracks):
            menu.addAction(f'Copy to this computer ({len(tracks)} track' + ('s' if len(tracks) != 1 else '') + ')',
                           lambda: self.copy_local.emit(tracks))
        else:
            menu.addAction(util.REVEAL_LABEL, lambda: util.reveal([track.path for track in tracks]))
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _colors_menu(self, menu, tracks):
        """The six dots at the top of the menu, as Finder's: a color all of tracks have ticked (a click: off for all),
        some of them a dash; a click on one: on for all."""
        counts = {name: sum(1 for track in tracks if name in (track.get('colors') or ())) for name in markers.NAMES}
        row = markers.ColorRow(spacing=2)
        row.set_checked({name for name, count in counts.items() if count == len(tracks)},
                        {name for name, count in counts.items() if 0 < count < len(tracks)})
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.addWidget(row)
        layout.addStretch(1)
        action = QWidgetAction(menu)
        action.setDefaultWidget(holder)
        menu.addAction(action)
        row.toggled.connect(lambda name, on: (menu.close(), self.set_colors.emit(tracks, name, on)))
        menu.addSeparator()

    @staticmethod
    def _delete_label(tracks, pending):
        count = len(tracks) + len(pending)
        return 'Delete...' if count == 1 else f'Delete ({count})...'

    def _trash_selected(self):
        tracks = self._selected_tracks()
        if not tracks or any(track.get('remote') for track in tracks):
            return
        items = self.tree.selectedItems()
        if len(items) == 1:
            track = items[0].data(0, TRACK)
            title = track.display_title if track is not None else items[0].text(0)
        else:
            title = f'{len(tracks)} songs selected'
        self.trash.emit(tracks, title)

    def _track_item(self, track):
        items = [self.tree.invisibleRootItem()]
        while items:
            item = items.pop()
            for index in range(item.childCount()):
                child = item.child(index)
                found = child.data(0, TRACK)
                if found is not None and found.path == track.path:
                    return child
                items.append(child)
        return None

    def track_after(self, track):
        """The track after this one in the tree as shown (its album, then the next ones; the filters applied), else
        None: Next by hand when the queue has nothing after it."""
        found, items = False, [self.tree.invisibleRootItem()]
        while items:
            item = items.pop()
            for index in reversed(range(item.childCount())):
                items.append(item.child(index))
            other = item.data(0, TRACK) if item is not self.tree.invisibleRootItem() else None
            if other is None:
                continue
            if found and not other.get('pending'):
                return other
            if other.path == track.path:
                found = True
        return None

    def _artist_item(self, item, track):
        """The artist above a track item: its album artist, else the lead artist it is grouped under."""
        names = {track.album_artist.lower(), _lead_artist(track.album_artist).lower()}
        parent = item.parent()
        while parent is not None:
            if parent.text(0).lower() in names:
                return parent
            parent = parent.parent()
        return None

    def select_track(self, track, level='track'):
        """The track (level 'track'), its album ('album') or its artist ('artist') shown in the tree: the parents
        expanded, it selected and in view. An artist the view has no level for (by folder) is shown by artist. False
        when it is not there (filtered out, or the tree not filled yet)."""
        item = self._track_item(track)
        if item is None:
            return False
        if level == 'album':        # by folder: its folder
            item = item.parent() or item
        elif level == 'artist':
            artist = self._artist_item(item, track)
            if artist is None and self.mode.currentData() != 'artist':
                self.mode.setCurrentIndex(self.mode.findData('artist'))
                self.rebuild()
                item = self._track_item(track)
                artist = item and self._artist_item(item, track)
            item = artist or item
            if item is None:
                return False
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        if level != 'track':
            item.setExpanded(True)
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtTop if level != 'track'
                               else QAbstractItemView.ScrollHint.PositionAtCenter)
        return True
