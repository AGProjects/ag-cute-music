"""The main window: the browser on the left, the visual pane on the right, the transport at the bottom, and the
play queue."""

import json
import os
import random
import re
import sys
import time

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence, QPalette
from PyQt6.QtWidgets import (QApplication, QFileDialog, QMessageBox, QHBoxLayout, QLabel, QMainWindow, QSlider, QTabWidget, QSplitter, QToolButton,
                             QVBoxLayout, QWidget)

from cutemusic import APP_NAME, util
from cutemusic.browser import MODES, Browser, _duration
from cutemusic import library
from cutemusic.history import History, HistoryView
from cutemusic.dock import DockWindow
from cutemusic.icons import refresh_line_icons, set_line_icon, transport_icon
from cutemusic.mpris import MPRIS
from cutemusic.online import Fetcher
from cutemusic.queueview import QueueView
from cutemusic.library import AUDIO_EXTENSIONS, Scanner, Track, music_folder, read_track, set_music_folder
from cutemusic.player import Player
from cutemusic.util import log
from cutemusic.visuals import PAGES, VisualPane


__all__ = ['MainWindow']


class Queue(object):
    """The tracks to play, in order, and the one playing; shuffle plays them in a random order (the one playing
    first), repeat starts again after the last."""

    def __init__(self):
        self.tracks = []
        self.order = []         # indexes into tracks
        self.position = -1      # into order
        self.shuffle = False
        self.repeat = False

    def set(self, tracks, index):
        self.tracks = list(tracks)
        self._reorder(index)

    def _reorder(self, current):
        self.order = list(range(len(self.tracks)))
        if self.shuffle and self.order:
            rest = [i for i in self.order if i != current]
            random.shuffle(rest)
            self.order = ([current] if current >= 0 else []) + rest
            self.position = 0 if current >= 0 else -1
        else:
            self.position = current

    def set_shuffle(self, on):
        self.shuffle = on
        self._reorder(self.order[self.position] if 0 <= self.position < len(self.order) else -1)

    def add(self, tracks, next_up):
        start = len(self.tracks)
        self.tracks += tracks
        new = list(range(start, start + len(tracks)))
        if next_up:
            self.order[self.position + 1:self.position + 1] = new
        else:
            self.order += new

    def insert(self, tracks, position):
        """Tracks put in at position in the play order (-1: the end); the one playing keeps playing."""
        start = len(self.tracks)
        self.tracks += tracks
        new = list(range(start, start + len(tracks)))
        if not 0 <= position <= len(self.order):
            position = len(self.order)
        self.order[position:position] = new
        if position <= self.position:
            self.position += len(new)

    @property
    def current(self):
        return self.tracks[self.order[self.position]] if 0 <= self.position < len(self.order) else None

    def peek(self, step=1):
        position = self.position + step
        if 0 <= position < len(self.order):
            return self.tracks[self.order[position]]
        if self.repeat and self.order:
            return self.tracks[self.order[position % len(self.order)]]
        return None

    def remove(self, indexes):
        """The tracks at indexes (into tracks) out of the queue. The one playing too: then the current one is the
        next one kept (else the one before it; -1 when none is left)."""
        current = self.order[self.position] if 0 <= self.position < len(self.order) else None
        gone = set(indexes)
        if current in gone:
            after = [index for index in self.order[self.position + 1:] if index not in gone]
            before = [index for index in self.order[:self.position] if index not in gone]
            current = after[0] if after else (before[-1] if before else None)
        kept = [index for index in range(len(self.tracks)) if index not in gone]
        renumber = {old: new for new, old in enumerate(kept)}
        self.tracks = [self.tracks[index] for index in kept]
        self.order = [renumber[index] for index in self.order if index in renumber]
        self.position = self.order.index(renumber[current]) if current is not None else -1

    def reorder(self, order):
        """A new play order (the same track indexes); the one playing keeps playing, where it now is."""
        if sorted(order) != sorted(self.order):
            return
        current = self.order[self.position] if 0 <= self.position < len(self.order) else None
        self.order = list(order)
        self.position = self.order.index(current) if current is not None else -1

    def state(self):
        return {'tracks': [dict(track) for track in self.tracks], 'order': self.order, 'position': self.position}

    def restore(self, state):
        tracks = [Track(track) for track in state.get('tracks') or []]
        order = [index for index in state.get('order') or [] if isinstance(index, int) and 0 <= index < len(tracks)]
        position = state.get('position', -1)
        if not tracks or len(order) != len(tracks) or not isinstance(position, int) or not 0 <= position < len(order):
            return False
        self.tracks, self.order, self.position = tracks, order, position
        return True

    def move(self, step=1):
        track = self.peek(step)
        if track is not None:
            self.position = (self.position + step) % len(self.order)
        return track


class MouseRest(QObject):
    """Full screen: the views' buttons and the play bar hidden after the mouse rests IDLE s (as a video call's
    controls), back as it moves, clicks or scrolls: the amplifier, the lyrics, whatever view, alone meanwhile."""

    IDLE = 5000     # ms

    def __init__(self, widgets, parent=None, keep=None, hide=None, plain=()):
        super().__init__(parent)
        self.widgets = widgets
        self.plain = list(plain)        # only hidden and shown: over a view that paints its own (the amplifier's - +)
        self.keep = keep or (lambda: ())        # the widgets kept shown now, whatever the mouse (the play bar on the VU meter)
        self.hide = hide or (lambda: ())        # the widgets kept hidden now, whatever the mouse (the play bar, see there)
        for widget in widgets:      # hidden, their room kept: the view does not move up and down
            policy = widget.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            widget.setSizePolicy(policy)
        self.timer = QTimer(self, singleShot=True, interval=self.IDLE)
        self.timer.timeout.connect(lambda: self._show(False))
        self.active = False

    def start(self):
        self.active = True
        QApplication.instance().installEventFilter(self)
        self.timer.start()

    def stop(self):
        self.active = False
        QApplication.instance().removeEventFilter(self)
        self.timer.stop()
        self._show(True)

    def _show(self, shown):
        kept, hidden = self.keep(), self.hide()
        for widget in self.widgets:
            if widget in hidden:
                continue            # (hidden by the window: _apply_transport)
            visible = shown or widget in kept
            widget.setVisible(visible)
            # where they were: the views' dark background, not the window's grey
            parent = widget.parentWidget()
            if not visible:
                from cutemusic.visuals import BACKGROUND
                palette = parent.palette()
                palette.setColor(palette.ColorRole.Window, BACKGROUND)
                parent.setPalette(palette)
                parent.setAutoFillBackground(True)
            else:
                parent.setPalette(QPalette())       # (empty: inherited again, following the theme)
                parent.setAutoFillBackground(False)
        for widget in self.plain:
            widget.setVisible(shown or widget in kept)

    def eventFilter(self, watched, event):
        if self.active and event.type() in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel):
            if not self.timer.isActive():       # (hidden, or about to be): back
                self._show(True)
            self.timer.start()
        return False

    def refresh(self):
        """What is kept shown changed (another view): hidden or shown again as the mouse says."""
        if self.active:
            self._show(self.timer.isActive())


class CornerButton(QObject):
    """A button kept over the bottom left of a view, as the view resizes or moves. The button is the view's
    parent's child, not the view's: a stacked view raises its current page over its own children."""

    MARGIN = 10

    def __init__(self, button, view):
        super().__init__(button)
        self.button, self.view = button, view
        view.installEventFilter(self)
        self.place()

    def place(self):
        self.button.adjustSize()
        view = self.view.geometry()         # in the parent both are in
        self.button.move(view.left() + self.MARGIN, view.bottom() - self.button.height() - self.MARGIN)
        self.button.raise_()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show):
            self.place()
        return False


class WheelVolume(QObject):
    """The mouse wheel over the visualizer (the cover and the VU meter) and the volume slider changes the volume: up
    louder, down softer (5 a notch; trackpads by their pixels); with the macOS natural scrolling, as the fingers move.
    Elsewhere (the library, the history, the lyrics) it scrolls as usual."""

    STEP = 5

    def __init__(self, slider, widgets, parent=None):
        super().__init__(parent)
        self.slider = slider
        self.widgets = widgets

    rest = 0

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel or not isinstance(watched, QWidget):
            return False
        if not any(widget is watched or widget.isAncestorOf(watched) for widget in self.widgets):
            return False
        delta = event.angleDelta().y() or event.angleDelta().x()
        if event.inverted():
            delta = -delta
        self.rest += delta * self.STEP
        steps = int(self.rest / 120)
        if steps:
            self.rest -= steps * 120
            self.slider.setValue(self.slider.value() + steps)
        return True


class SpaceToggles(QObject):
    """Space plays and pauses wherever the focus is (the library's tree, a button, a list), except where text is typed:
    the text fields keep it -- the search field only once something is typed in it (a search does not start with a
    space: in it empty, Space still plays and pauses). (Play -> Play / Pause has Space too: this is for the widgets
    that take Space before the shortcut does.)"""

    def __init__(self, toggle, parent=None):
        super().__init__(parent)
        self.toggle = toggle

    @staticmethod
    def typing(widget):
        from PyQt6.QtWidgets import QAbstractSpinBox, QComboBox, QLineEdit, QPlainTextEdit, QTextEdit
        if isinstance(widget, QLineEdit):
            return not (widget.property('space_plays_when_empty') and not widget.text().strip())
        if isinstance(widget, QAbstractSpinBox):
            return True
        if isinstance(widget, QComboBox):
            return widget.isEditable()
        if isinstance(widget, (QTextEdit, QPlainTextEdit)):
            return not widget.isReadOnly()
        return False

    def eventFilter(self, watched, event):
        if event.type() not in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride) or event.key() != Qt.Key.Key_Space:
            return False
        if event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:
            return False
        focus = QApplication.focusWidget()
        if watched is not focus or focus is None or self.typing(focus) or QApplication.activeModalWidget() is not None:
            return False
        if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()      # the KeyPress comes to us, not to the Play menu's shortcut (no double toggle)
            return True
        if not event.isAutoRepeat():
            self.toggle()
        return True


def karaoke_enabled():
    """The karaoke features (negatives, web libraries) shown: ui.conf general/karaoke, off at first."""
    return util.settings().value('general/karaoke', False, type=bool)

def _safe_name(text):
    """text as a file's or folder's name: no path separators, a colon (macOS) as a dot, not empty."""
    import re
    return re.sub(r'[/\\]+', '_', text.replace(':', '.')).strip(' .') or '_'


class MainWindow(QMainWindow):

    blink_control_changed = pyqtSignal()        # a Blink set 'agbc' (from a CoreAudio thread: queued to ours)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.queue = Queue()
        self.player = Player(self)
        self.scanner = Scanner(self)
        # libraries on the LAN (sharing.py): this one's shared when chosen, the others found, browsed, fetched from
        from cutemusic.sharing import Discovery, Downloader, LibraryServer
        self.local_tracks = []
        self.remote = None              # the library on the LAN shown in the tree, None: this computer's
        self.library_server = LibraryServer()
        self.discovery = Discovery(self)
        self.downloader = Downloader(self)
        self.downloader.done.connect(self._downloaded)
        self.downloader.idle.connect(self._downloads_idle)
        self._copied = 0
        self.online = Fetcher(self)     # lyrics and covers the tracks lack, looked up online
        self.online.found.connect(self._online_found)
        self.root = music_folder()
        self._seeking = False
        self._duration = 0.0

        self.history = History()
        self.browser = Browser()
        self.history_view = HistoryView(self.history)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        # a narrow pane: the tabs' names shortened (Libr…), never scroll arrows hiding some of them
        self.tabs.tabBar().setUsesScrollButtons(False)
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.addTab(self.browser, 'Library')
        # negatives (negatives.py): the songs without their singer, made in the background; the Negatives tab, after
        # the Library and as it is, shows the ones made (played with the lyrics shown) and the ones being made
        from cutemusic.negatives import Negatives
        self.negatives = Negatives(self)
        self.negative_tracks = []
        self.negative_scanner = Scanner(self)
        self.negatives_browser = Browser()
        self.negatives_browser.source.hide()        # (the libraries on the LAN: the Library's)
        self.negatives_browser.owners = True        # by whose they are: Local, then the web accounts and their tokens
        self.negatives_browser.show_made()          # when each was made
        self.tabs.addTab(self.negatives_browser, 'Negatives')
        # negatives for the LAN (web.py): songs sent from a web page with a token, waiting here for Start; the Web
        # tab, after the Negatives one, its name with how many wait ("Web (2)", in amber)
        from cutemusic.web import WebQueue, WebQueueView
        self.web = WebQueue(self.negatives, self)
        self.web_view = WebQueueView(self.web)
        self.web_view.play.connect(lambda tracks: self._play_now(tracks))     # right-click -> Play, Play negative
        self.tabs.addTab(self.web_view, 'Web')
        self.tabs.setTabVisible(self.tabs.indexOf(self.web_view), False)     # shown while serving (_serve_web)
        self._web_waiting = len(self.web.waiting())
        self._tracks_by_path = {}
        self.queue_view = QueueView()
        self.tabs.addTab(self.queue_view, 'Queue')
        self.tabs.addTab(self.history_view, 'History')
        from cutemusic.playlists import PlaylistView
        self.playlist_view = PlaylistView()
        self.tabs.addTab(self.playlist_view, 'Playlists')
        # × at the right end of the tabs: the browser closed (the Browser pill brings it back)
        close = QToolButton()
        close.setText('\u00d7')
        close.setAutoRaise(True)
        close.setToolTip('Hide the browser')
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.setStyleSheet('QToolButton { font-size: 16px; padding: 0 6px 4px 6px; border: none; }')     # (lifted: on the tabs' text line)
        close.clicked.connect(lambda: self.set_library_shown(False))
        self.tabs.setCornerWidget(close, Qt.Corner.TopRightCorner)
        self.visuals = VisualPane()
        # playing a song with a negative (or a negative): a button at the bottom left of the view, not on the
        # Mixer, to go on with the other one from the same point
        # Original | Negative, both shown: the one playing lit, the other a click away (not one button naming the
        # other: which one plays was not clear)
        from cutemusic.visuals import DIM, TEXT
        self.version_button = QWidget(self.visuals)
        row = QHBoxLayout(self.version_button)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self._version_buttons = {}
        for key, label, tip, side in (('original', 'Original', 'The song, with its singer', 'left'),
                                      ('negative', 'Negative', 'Its negative: no singer, for karaoke', 'right')):
            button = QToolButton(self.version_button)
            button.setText(label)
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            radius = 'border-top-left-radius: 12px; border-bottom-left-radius: 12px;' if side == 'left' else \
                'border-top-right-radius: 12px; border-bottom-right-radius: 12px; border-left: none;'
            button.setStyleSheet(
                f'QToolButton {{ color: {DIM.name()}; background: rgba(0, 0, 0, 140); border: 1px solid {DIM.name()};'
                f' border-radius: 0; {radius} padding: 4px 12px; }}'
                f'QToolButton:hover:!checked {{ color: {TEXT.name()}; }}'
                f'QToolButton:checked {{ color: #101318; background: #f0a030; font-weight: bold; }}')
            button.clicked.connect(lambda checked, key=key: self._choose_version(key))
            row.addWidget(button)
            self._version_buttons[key] = button
        self.version_button.hide()
        self._version_corner = CornerButton(self.version_button, self.visuals.stack)
        splitter = self.splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.visuals)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setChildrenCollapsible(False)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter, 1)
        self.transport = self._transport()
        # its slot: the play bar, or on the mixer the remote of the app MUSIC plays from (Spotify, Music: _remote_bar)
        from PyQt6.QtWidgets import QStackedWidget
        self.bar_slot = QStackedWidget()
        self.bar_slot.addWidget(self.transport)
        self.remote_bar = self._remote_bar()
        self.bar_slot.addWidget(self.remote_bar)
        self.bar_slot.setFixedHeight(self.transport.minimumHeight())     # (the play bar's fixed height)
        layout.addWidget(self.bar_slot)
        self.setCentralWidget(central)
        # full screen: the views' buttons, the VU meter's style buttons and the play bar hide while the mouse rests,
        # back as it moves; on the VU meter the play bar stays
        # on the amplifier its - + and the file info under it too (elsewhere the file info stays)
        self.mouse_rest = MouseRest([self.visuals.bar, self.visuals.vu_page.style_bar, self.bar_slot, self.visuals.song_info], self,
                                    keep=self._mouse_rest_kept,
                                    hide=lambda: [self.bar_slot] if self._bar_hidden() else [],
                                    plain=self.visuals.amp.size_buttons)
        self.visuals.page_changed.connect(lambda page: self.mouse_rest.refresh())
        self.visuals.page_changed.connect(lambda page: self._update_version_button())
        self.visuals.page_changed.connect(self._library_for_page)
        self.visuals.browser_wanted.connect(self._browser_pill_clicked)
        self.visuals.windowed_wanted.connect(lambda: self.full_screen and self.toggle_full_screen())
        self.visuals.full_screen_wanted.connect(lambda: self.full_screen or self.toggle_full_screen())
        self.visuals.page_changed.connect(lambda page: self._apply_transport())
        # macOS: the Dock icon a live VU meter (dockicon.py), View -> Live Dock icon
        from cutemusic import dockicon
        self.dock_icon = dockicon.DockIcon(self) if dockicon.available() else None
        self._menus()
        # the menus' shortcuts also with the menu bar hidden (full screen): the actions on the window too
        for menu_action in self.menuBar().actions():
            if menu_action.menu() is not None:
                self.addActions([action for action in menu_action.menu().actions() if not action.isSeparator()])
        self.leave_full_screen_action = QAction('Leave full screen', self)
        self.leave_full_screen_action.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        self.leave_full_screen_action.setEnabled(False)
        self.leave_full_screen_action.triggered.connect(self.toggle_full_screen)
        self.addAction(self.leave_full_screen_action)

        settings = util.settings()
        if not self.restoreGeometry(settings.value('window/geometry', b'')):
            self.resize(1200, 760)
        splitter.restoreState(settings.value('window/splitter', b''))
        self.volume.setValue(settings.value('player/volume', 80, type=int))
        output = settings.value('player/output', '', type=str)
        if output:                      # the output device chosen last time (Play -> Output device), when connected
            self.player.set_output(output)
            self._output_tooltip()
        self.shuffle_action.setChecked(settings.value('player/shuffle', False, type=bool))
        self.repeat_action.setChecked(settings.value('player/repeat', False, type=bool))
        self.set_library_shown(settings.value('window/library', True, type=bool))
        self._library_for_page(settings.value('visuals/page', '', type=str))       # started on the mixer
        self.tabs.setCurrentIndex(settings.value('window/tab', 0, type=int))
        self.tabs.currentChanged.connect(lambda index: util.settings().setValue('window/tab', index))

        self.browser.play.connect(self.play_tracks)
        self.browser.queue.connect(self._enqueue)
        self.browser.play_now.connect(self._play_now)
        self.browser.font_changed.connect(self._list_font_changed)
        self.browser.source.chosen.connect(self._show_library)
        self.browser.source.connect_to.connect(self._connect_to_library)
        self.browser.copy_local.connect(self._copy_local)
        self.browser.edit_tags.connect(self.edit_tags)
        self.browser.set_colors.connect(self.set_colors)
        self.browser.trash.connect(self.move_to_trash)
        self.discovery.changed.connect(self._libraries_found)
        self.discovery.start()
        # the cover view shows what is picked in the library, also while something else plays (until the next track)
        self.browser.selected.connect(lambda track, lines: self.visuals.cover.show_track(track, lines))
        self.browser.set_font_step(self.browser.font_step)      # A− A+ as left last time
        self.queue_view.insert.connect(self._insert_dropped)
        self.tabs.tabBar().setChangeCurrentOnDrag(True)     # dragging over the Queue tab shows it, to drop in it
        self.tabs.tabBar().setAcceptDrops(True)
        self.history_view.play.connect(self.play_tracks)
        self.playlist_view.play.connect(self.play_tracks)
        self.playlist_view.enqueue.connect(self._enqueue)
        self.browser.fill_playlist_menu = self._fill_playlist_menu
        self.browser.make_negatives = self.make_negatives
        self.browser.negatives_menu = self._negatives_menu
        self.playlist_view.make_negatives = self.make_negatives
        self.browser.fill_web_menu = self._fill_web_menu        # right-click -> Copy to web library (web.py)
        self.playlist_view.fill_web_menu = self._fill_web_menu
        # the Negatives tab: played, queued, edited as the Library; no Trash (not the music folder's) nor Make negative
        negatives_browser = self.negatives_browser
        negatives_browser.play.connect(self.play_tracks)
        negatives_browser.queue.connect(self._enqueue)
        negatives_browser.play_now.connect(self._play_now)
        negatives_browser.edit_tags.connect(self.edit_tags)
        negatives_browser.set_colors.connect(self.set_colors)
        negatives_browser.trash.connect(self.trash_negatives)
        negatives_browser.selected.connect(lambda track, lines: self.visuals.cover.show_track(track, lines))
        negatives_browser.fill_playlist_menu = self._fill_playlist_menu
        negatives_browser.pending_menu = self._pending_menu
        negatives_browser.delete = self._delete_negatives       # right-click -> Delete... on any item
        negatives_browser.fill_web_menu = self._fill_negatives_web_menu     # Copy to web library: song and negative
        negatives_browser.set_font_step(self.browser.font_step)
        self.negatives.changed.connect(self._negatives_changed)
        self.negatives.made.connect(lambda source, negative: (self._negatives_rescan.start(), self._update_version_button()))
        self._negatives_rescan = QTimer(self, singleShot=True, interval=1500)
        self._negatives_rescan.timeout.connect(lambda: self._scan_negatives(cached=False))
        self.negative_scanner.updated.connect(self._negatives_scanned)
        self.negative_scanner.finished.connect(self._negatives_scanned)
        QTimer.singleShot(5000, self.negatives.resume)      # what was left queued at the last quit goes on
        if sys.platform == 'darwin':
            QTimer.singleShot(3000, self._offer_driver)      # the AG Podcast Mixer driver, once per version we carry
        if sys.platform in ('darwin', 'win32'):
            from cutemusic.updater import Updater
            self.updater = Updater(self)        # newer versions of the app, checked at start and once a day
            self.updater.start()
        self.queue_view.save_as_playlist = self._save_queue_as_playlist
        self.queue_view.elapsed = lambda: self._position_seconds
        self.queue_view.jump.connect(self._jump)
        self.queue_view.remove.connect(self._remove_from_queue)
        self.queue_view.reorder.connect(self._reorder_queue)
        self.tabs.currentChanged.connect(lambda index: self.tabs.widget(index) is self.queue_view and self.queue_view.show_current())
        self.player.state_changed.connect(self._state_changed)
        self.player.position.connect(self._position)
        self.player.levels.connect(self.visuals.set_levels)
        if self.dock_icon is not None:
            self.player.levels.connect(self.dock_icon.set_levels)
            self.dock_icon.set_enabled(util.settings().value('view/live_dock_icon', True, type=bool))
        # the amplifier: its volume knob is the volume slider; bass, treble and balance go to the player
        amp = self.visuals.amp
        amp.set_volume(self.volume.value())
        self.volume.valueChanged.connect(amp.set_volume)
        amp.volume_changed.connect(self.volume.setValue)
        amp.extra_changed.connect(self.player.set_extra)
        amp.set_tone_available(self.player.available and self.player.has_tone)
        amp.tone_changed.connect(self.player.set_tone)
        amp.balance_changed.connect(self.player.set_balance)
        self.player.set_tone(amp.bass.value, amp.treble.value)
        self.player.set_balance(amp.balance.value)
        self.player.output_changed.connect(self._output_changed)
        # the amplifier's output selector: a position an output, as the Output device menu
        amp.output_chosen.connect(lambda name: self._set_output(name or None))
        amp.mute_toggled.connect(self._set_muted)
        # the mixer: the mic (its own pipeline, on the music's output), the music's fader the volume, VOCAL CUT
        from cutemusic.mic import Mic
        from cutemusic import recorder as _recorder
        mixer = self.visuals.mixer
        # RECORDER: every strip's pipeline has a branch to it from the start (recording starts without a gap)
        self.recorder = _recorder.Recorder()
        self.player.set_recorder(self.recorder)
        mixer.set_record_available(_recorder.extension() is not None)
        mixer.record_toggled.connect(self._record_by_hand)
        # Auto-record when on air: REC by itself as ON AIR lights, stopped OFF_AIR_GRACE after it goes out
        self._auto_recording = False        # the recording running was started by it
        self._auto_record_held = False      # REC stopped by hand while on air: not started again till off air
        self._off_air_since = None
        self._on_air_timer = QTimer(self, interval=1000)
        self._on_air_timer.timeout.connect(self._watch_on_air)
        self._on_air_timer.start()
        mixer.auto_record_changed.connect(lambda on: self._watch_on_air())
        mixer.marker_requested.connect(self._add_marker)
        self._markers = []          # [(seconds, name)]: MARK in the recording now, written as chapters when it stops
        from PyQt6.QtGui import QShortcut
        marker_key = QShortcut(QKeySequence('M'), self)     # M: MARK, while it records (not taken from a text field)
        marker_key.activated.connect(lambda: self._add_marker() if self.recorder.recording else None)
        # [ and ]: the previous and the next marker of what plays (its chapters, on the play bar)
        QShortcut(QKeySequence('['), self).activated.connect(lambda: self._jump_marker(-1))
        QShortcut(QKeySequence(']'), self).activated.connect(lambda: self._jump_marker(1))
        # - and + (= too, + unshifted on most layouts): in full screen, the amplifier smaller or bigger (as its - +)
        for key, step in (('-', -1), ('+', 1), ('=', 1)):
            QShortcut(QKeySequence(key), self).activated.connect(lambda step=step: self._zoom_amp(step))
        mixer.tape_wanted.connect(self._show_recordings)
        mixer.strip_clicked.connect(self._strip_clicked)
        mixer.record_renamed.connect(self._rename_recording)
        # STREAMING: the driver's "AG Podcast Streaming" input with the recorder's mix, for a streaming app
        from cutemusic import driver as _driver
        from cutemusic.share import streaming_nodes
        # macOS: the driver's streaming devices; Linux: the PipeWire streaming nodes (always there)
        mixer.set_streaming_available(_driver.feed_device() is not None or streaming_nodes())
        mixer.streaming_changed.connect(self._set_streaming)
        self._stream_tries = 0
        if mixer.streaming():
            QTimer.singleShot(0, lambda: self._set_streaming(True))         # as it was left
        self._recording = self._recorded = None     # (path, title, artist, genre): recording now, saved last
        self.mic = Mic(self)
        self.mic.set_recorder(self.recorder)
        # its ring 100 ms deep, its quantum (what is heard late) still 10 ms: on macOS's shared output the mixer holds it
        # up while it waits for an app's 40 ms quanta (with 20 ms it overran: "Can't record audio fast enough")
        self.mic.buffer = 100
        self.mic.set_output(self.player.output)
        self.mic.set_level(mixer.mic_fader.value)
        self.mic.set_echo(mixer.echo.value if mixer.karaoke_on() else 0.0)
        self.mic.levels.connect(mixer.set_mic_levels)
        self._feed_amp_from(self.mic, 'MIC')
        # chosen: kept (mixer/input, '' Off) and chosen again at the next start (_default_mic)
        mixer.input_chosen.connect(lambda name: (util.settings().setValue('mixer/input', name or ''), self.mic.set_input(name or None)))
        mixer.inputs_wanted.connect(self._mixer_inputs)
        mixer.mic_level_changed.connect(self.mic.set_level)
        mixer.echo_changed.connect(lambda value: [mic.set_echo(value) for mic in self._mics()])
        # the other input strips (mixer.py: + and -): a Mic each, as the first, by the strip's key
        self.slot_mics = {}
        mixer.slot_added.connect(self._slot_added)
        mixer.slot_removed.connect(self._slot_removed)
        mixer.slot_input_chosen.connect(lambda key, name: self.slot_mics[key].set_input(name or None) if key in self.slot_mics else None)
        mixer.slot_level_changed.connect(lambda key, value: self.slot_mics[key].set_level(value) if key in self.slot_mics else None)
        mixer.set_music(self.volume.value())
        # MUSIC's fader: the player's volume (the Local Library), or the app's level when MUSIC plays an app (_music_fader)
        # LOCAL LIBRARY's fader: the player's volume; AUDIO APPS': the app's level (_apps_fader)
        self.volume.valueChanged.connect(mixer.set_music)
        mixer.music_changed.connect(self.volume.setValue)
        mixer.apps_level_changed.connect(self._apps_fader)
        self.player.state_changed.connect(lambda *args: self._update_source_track())       # LOCAL LIBRARY's song
        mixer.set_vocal_cut_available(self.player.available and self.player.has_vocal_cut)
        mixer.vocal_cut_changed.connect(self.player.set_vocal_cut)
        # the mixer as a sound device for Blink: the mic (and the music while TO CALLS is on) is what Blink sends, what
        # Blink plays is the BLINK strip, on our output. The strip only while Blink runs
        from cutemusic.share import CALL_SINK, ensure_alsa_device
        ensure_alsa_device()        # (Linux, run from the source tree: the package's ALSA PCM for Blink, set up per user)
        for slot in mixer.slots:        # the strips kept from the last time (their devices once the permission is there)
            self._slot_added(slot.key, start=False)
        # the AG Podcast Mixer link (link.py): the mix to Blink through the driver (macOS), the PipeWire nodes (Linux),
        # nothing else (link.py)
        from cutemusic import link as _link
        self.link_devices = _link.LinkDevices()
        self.link_sender = _link.LinkSender()
        self._start_link()
        self._apply_share()         # TO CALLS as it was left
        mixer.share_changed.connect(lambda on: self._apply_share())
        mixer.library_share_changed.connect(lambda on: self._apply_share())
        self.call_audio = Mic(self)         # what Blink plays (CALL_SINK's monitor) on our output, never shared
        self.call_audio.link_name = 'blink'         # (its input in the recorder's mix)
        self.call_audio.boost = 1.0         # a call comes at full level already: as it comes at the top, never louder
                                            # (the mic's +6 dB clipped Blink's sound past 80 % on the fader)
        self.call_audio.set_recorder(self.recorder)
        self.call_audio.set_output(self.player.output)
        self.call_audio.set_level(mixer.call_fader.value)
        self.call_audio.levels.connect(mixer.set_call_levels)
        self._feed_amp_from(self.call_audio, 'BLINK')
        mixer.call_level_changed.connect(self.call_audio.set_level)
        mixer.mute_changed.connect(self._mixer_mute)
        mixer.channel_mutes_changed.connect(self.player.set_channel_mutes)      # LOCAL LIBRARY's L and R
        self.player.set_channel_mutes(*mixer.channel_mutes)
        # each MUTE as it was left (the mixer keeps them): MIC's and BLINK's here, AUDIO APPS' and LOCAL LIBRARY's below
        self.mic.set_mute(bool(mixer.muted.get('MIC')))
        self.call_audio.set_mute(bool(mixer.muted.get('BLINK')))
        mixer.hangup_requested.connect(self._hang_up)
        self._hung_up = set()       # (pid, id) of the calls HANG UP was pressed for: nothing sent to Blink till they are gone
        mixer.redial_requested.connect(lambda target: self._blink_request(target, 'redial'))
        mixer.participant_mute_requested.connect(lambda target, muted: self._blink_request(dict(target, muted=muted), 'mute'))
        self._auto_redial = None        # the call to make again ({"pid", "address", "at"}: auto-redial), None: none
        self._ended_seen = {}           # Blink's pid: the last 'ended' seq seen (each end acted on once)
        mixer.auto_redial_changed.connect(lambda on: None if on else self._cancel_auto_redial('auto-redial turned off'))
        mixer.monitor_changed.connect(self._monitor_clicked)
        # MUSIC's SOURCE: another app's sound (taps.py, macOS 14.2+) in place of the player, read as an input by a
        # pipeline of its own (as the mic), at the music's fader, sent to calls with TO CALLS as the player's music
        from cutemusic import taps
        self.app_audio = Mic(self)
        self.app_audio.set_recorder(self.recorder)
        self.app_audio.link_name = 'apps'           # its own input in the link's mix: the Local Library's is 'music'
        self.app_audio.latency = 40         # ms: an app's music through a tap, in 40 ms quanta (80 ms source buffer)
        self.app_audio.stereo = True        # (the tap is a stereo mixdown: kept stereo through the strip)
        self.app_audio.set_output(self.player.output)
        self.app_audio.boost = 1.0          # at the top, as the app plays it: its own volume is the app's
        self.app_audio.set_level(util.settings().value('mixer/app_level', 1.0, type=float))
        self.app_audio.set_mute(bool(mixer.muted.get('MUSIC')))
        self.app_audio.levels.connect(mixer.set_source_levels)
        self._feed_amp_from(self.app_audio, 'MUSIC')
        self._capture = None            # the taps.Capture MUSIC plays, None: the player
        self._player_volume = None      # the player app's volume the fader set (taps.set_volume), and when
        self._player_volume_at = 0.0
        self._player_volume_timer = QTimer(self, singleShot=True, interval=100)
        self._player_volume_timer.timeout.connect(self._send_player_volume)
        mixer.set_sources_available(taps.available())
        # chosen: kept (mixer/source, '' the player) and chosen again at the next start while that app is there
        mixer.source_chosen.connect(self._source_turned)
        mixer.app_output_changed.connect(lambda on: self._music_source(mixer.source() or ''))
        self._in_call = False           # a Blink call active (not on hold): TO AMPLIFIER off meanwhile
        self._listeners_busy = False    # (Linux) pw-dump being read for who reads our devices
        # TO AMPLIFIER: on in KARAOKE (singing, heard in the headset), off during a Blink call; otherwise as last
        # clicked (kept: mixer/to_amplifier), and back to that when karaoke or the call ends
        self._monitor_wanted = util.settings().value('mixer/to_amplifier', False, type=bool)
        self._set_monitor(self._monitor_resting())
        if mixer.powered:
            QTimer.singleShot(0, self._default_mic)         # MIC's input as last chosen, else the Mac's built-in mic
            QTimer.singleShot(0, self._default_source)      # MUSIC's source as last chosen
        self._set_muted(bool(mixer.muted.get('LIBRARY')))      # (the player muted as LOCAL LIBRARY was left)
        self._call_sink = CALL_SINK
        # the driver's control channel: a Blink's calls followed as it sets them (a call's progress at once), the 2 s
        # timer still there for the rest (a keep-alive: a Blink not heard from for 10 s is gone)
        self._blink_listener = None
        self._sylk_visible = False     # "AG Podcast Mixer" listed for Sylk Desktop (control.set_mixer_visible)
        self._blink_soon = QTimer(self, singleShot=True, interval=20)       # several sets in a row: one refresh
        self._blink_soon.timeout.connect(self._blink_control)
        self._blink_shape = None    # the Blinks' entries without the participants' levels: a change there, a full refresh
        self.blink_control_changed.connect(self._blink_soon.start)
        from cutemusic import control as _control      # Linux: the Blinks' control channel on D-Bus (bus.py)
        _control.start_bus(self.blink_control_changed.emit, show_mixer=self.show_mixer)      # (ShowMixer: Blink's notice)
        self._blink_timer = QTimer(self, interval=2000)
        self._blink_timer.timeout.connect(self._watch_blink)
        self._blink_timer.start()
        self._watch_blink()
        # KARAOKE: the echo and VOCAL CUT heard only then; POWER: off, the mixer stands down
        mixer.karaoke_changed.connect(self._mixer_karaoke)
        mixer.blink_changed.connect(lambda running: self._mixer_karaoke(mixer.karaoke, quiet=True))
        self._mixer_karaoke(mixer.karaoke, quiet=True)      # _watch_blink above may already have found Blink running
        mixer.power_changed.connect(self._mixer_power)
        if not mixer.powered:
            self._mixer_power(False)
        amp.set_muted(self.player.muted)
        amp.outputs_wanted.connect(self._amp_outputs)
        self.player.outputs_changed.connect(self._amp_outputs)
        self.player.auto_switch_outputs = util.settings().value('player/auto_switch_outputs', True, type=bool)
        self.player.started.connect(self._started)
        self.player.finished.connect(self._finished)
        self.player.error.connect(self._error)
        self.scanner.progress.connect(self.browser.status.setText)
        self.scanner.updated.connect(self._scanned)
        self.scanner.finished.connect(self._scanned)
        self.dock = DockWindow(self)
        self.dock.undock.connect(self.undock)
        self.player.levels.connect(self.dock.set_levels)
        self.player.spectrum.connect(self.visuals.vu.set_spectrum)
        self.player.spectrum.connect(self.visuals.amp.set_spectrum)
        self.player.spectrum.connect(self.visuals.lava.set_spectrum)
        self.player.spectrum.connect(self.dock.vu.set_spectrum)
        self.player.spectrum.connect(self.dock.lava.set_spectrum)
        self.wheel_volume = WheelVolume(self.volume, [self.visuals.cover, self.visuals.vu_page, self.volume,
                                                      self.dock.cover, self.dock.vu, self.dock.lava], self)
        QApplication.instance().installEventFilter(self.wheel_volume)
        self.space_toggles = SpaceToggles(self.toggle, self)
        QApplication.instance().installEventFilter(self.space_toggles)
        self._update_track(None)
        if not self.player.available:
            from cutemusic.player import gst_error
            self.now_playing.setText(f'GStreamer is not available: {gst_error}')
        self.load_library()
        self.share_action.setChecked(util.settings().value('sharing/on', False, type=bool))      # shared last time
        self.web.changed.connect(self._web_changed)
        self._web_changed()
        self.web_action.setChecked(util.settings().value('web/on', False, type=bool))         # served last time
        self._apply_karaoke_features()
        self._saved_at = 0.0
        self._media_keys()
        self._resume()
        if sys.platform == 'darwin':
            from cutemusic.nowplaying import NowPlaying
            self.mpris = NowPlaying(self)       # the media keys, also without the focus: macOS's Now Playing
        else:
            self.mpris = MPRIS(self)        # the media keys from the desktop, also without the focus (Linux)
        self._mpris_update()
        self._start_docked = util.settings().value('window/docked', False, type=bool)

    mpris = None

    # Docked at the right (dock.py): the main window hidden meanwhile, back as it was

    # Full screen (View -> Full screen, Ctrl+F): the visualizer and the play bar under it, the whole screen, no title
    # bar, no menu bar, no library (a kiosk). Ctrl+F or Esc back as it was.

    _full_screen_from = None        # (maximized, splitter state, geometry) before

    def _mouse_rest_kept(self):
        """Shown in full screen whatever the mouse: the play bar on the VU meter; the file info but on the amplifier."""
        page = self.visuals.stack.currentWidget()
        kept = [self.bar_slot] if page is self.visuals.vu_page else []
        if page is not self.visuals.amp:
            kept.append(self.visuals.song_info)
        return kept

    @property
    def full_screen(self):
        return self._full_screen_from is not None

    def _zoom_amp(self, step):
        if self.full_screen and self.visuals.stack.currentWidget() is self.visuals.amp:
            self.visuals.amp.resize_by(step)

    def toggle_full_screen(self):
        if not self.full_screen:
            self._full_screen_from = (self.isMaximized(), self.splitter.saveState(), self.saveGeometry())
            self.tabs.hide()
            self.menuBar().hide()
            self.showFullScreen()
            self.mouse_rest.start()
        else:
            maximized, state, geometry = self._full_screen_from
            self._full_screen_from = None
            self.mouse_rest.stop()
            self.tabs.setVisible(self._library_shown())
            self.menuBar().show()
            self.splitter.restoreState(state)
            self.showMaximized() if maximized else self.showNormal()
        self.full_screen_action.setChecked(self.full_screen)
        self.leave_full_screen_action.setEnabled(self.full_screen)
        self.visuals.set_full_screen(self.full_screen)
        self._sync_browser_pill()

    # The music browser (the tabs) shown or not: View -> Music browser (window/library), or hidden by itself while
    # on the mixer with nothing playing (_library_auto: hidden so, shown again when leaving the mixer)

    _library = True
    _library_auto = False

    def _library_shown(self):
        return self._library and not self._library_auto

    def set_library_shown(self, shown):
        self._library = bool(shown)
        self._library_auto = False
        util.settings().setValue('window/library', self._library)
        self._apply_library()

    def _apply_library(self):
        if hasattr(self, 'library_action'):
            self.library_action.setChecked(self._library_shown())
        if not self.full_screen:
            self.tabs.setVisible(self._library_shown())
        self._sync_browser_pill()

    def _sync_browser_pill(self):
        """The Browser pill left of Cover while the music browser is hidden (not in full screen: no browser there)."""
        if hasattr(self, 'visuals') and hasattr(self, 'tabs'):
            self.visuals.set_browser_shown(self.full_screen or not self.tabs.isHidden())

    def _browser_pill_clicked(self):
        """The Browser pill: the music browser shown again (as Ctrl+B), kept so."""
        self.set_library_shown(True)

    _bar_for = 'library'        # whose songs the bar drives: 'library' (the play bar) or 'apps' (AUDIO APPS' remote)

    def _strip_clicked(self, key):
        """A mixer strip's tile clicked: Audio Apps, the bar drives its app (when it has a remote: Spotify, Music);
        Local Library, the play bar again."""
        if key not in ('MUSIC', 'LIBRARY'):
            return
        self._bar_for = 'apps' if key == 'MUSIC' else 'library'
        self._apply_transport()

    def _transport_hidden(self):
        """The play bar hidden, an app's remote in its place: Audio Apps' tile clicked and its app has one."""
        return self._remote_app() is not None

    def _remote_app(self):
        """The app whose remote takes the play bar's place: AUDIO APPS' app, its tile clicked last (not the Local
        Library's), a player (taps.PLAYERS)."""
        from cutemusic import taps
        capture = getattr(self, '_capture', None)
        return capture.bundle_id if self._bar_for == 'apps' and capture is not None and capture.bundle_id in taps.PLAYERS \
            else None

    def _remote_bar(self):
        """The captured player's buttons and what it plays: prev, play/pause, next over AppleScript (taps.remote)."""
        from cutemusic import taps
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(6)

        def send(command):
            app = self._remote_app()
            if app and taps.remote(app, command):
                QTimer.singleShot(400, self._update_source_track)       # (its state and title, soon)
        self.remote_previous = self._button('previous', 'Previous', lambda: send('previous track'))
        self.remote_play = self._button('play', 'Play', lambda: send('playpause'))
        self.remote_next = self._button('next', 'Next', lambda: send('next track'))
        for button in (self.remote_previous, self.remote_play, self.remote_next):
            layout.addWidget(button)
        layout.addSpacing(12)
        self.remote_title = QLabel()
        font = self.remote_title.font()
        font.setPointSizeF(font.pointSizeF() * 1.1)
        self.remote_title.setFont(font)
        layout.addWidget(self.remote_title, 1)
        return bar

    def _update_remote(self, track):
        """The remote's title and play/pause (track: taps.now_playing's (title, artist, playing), None)."""
        app = self._remote_app()
        if app is None:
            return
        import html
        name = self._capture.name
        if track:
            title, artist, playing = track[:3]
            self.remote_title.setText(f'<b>{html.escape(title)}</b>' + (f' — {html.escape(artist)}' if artist else '')
                                      + f' <span style="color: gray">· {html.escape(name)}</span>')
        else:
            playing = False
            self.remote_title.setText(f'<span style="color: gray">{html.escape(name)}</span>')
        self.remote_play.setProperty('transport_icon', 'pause' if playing else 'play')
        self.remote_play.setIcon(transport_icon(self.remote_play.property('transport_icon')))
        self.remote_play.setToolTip('Pause' if playing else 'Play')

    def _bar_hidden(self):
        """The bar's slot empty: on the mixer, MUSIC from an app without a remote."""
        return self._transport_hidden() and self._remote_app() is None

    def _apply_transport(self):
        """The bar's slot: the play bar, the remote of MUSIC's player (on the mixer), or nothing (MUSIC from an app
        without a remote); its room kept when empty (retained by MouseRest), the views not resized, in their dark
        background."""
        remote = self._remote_app() is not None
        self.bar_slot.setCurrentWidget(self.remote_bar if remote else self.transport)
        if hasattr(self, 'visuals'):
            self.visuals.mixer.set_bar_strip('MUSIC' if remote else 'LIBRARY')
        if remote:
            self._update_source_track()
        hidden = self._bar_hidden()
        if hidden:
            self.bar_slot.hide()
        elif not self.mouse_rest.active or self.mouse_rest.timer.isActive():
            self.bar_slot.show()
        parent = self.bar_slot.parentWidget()
        if hidden:
            from cutemusic.visuals import BACKGROUND
            palette = parent.palette()
            palette.setColor(palette.ColorRole.Window, BACKGROUND)
            parent.setPalette(palette)
            parent.setAutoFillBackground(True)
        elif self.bar_slot.isVisible():
            parent.setPalette(QPalette())
            parent.setAutoFillBackground(False)

    def _library_for_page(self, page):
        if page == 'mixer':
            if self._library and self.player.state != 'playing':
                self._library_auto = True
                self._apply_library()
        elif self._library_auto:
            self._library_auto = False
            self._apply_library()

    def dock_right(self):
        if self.full_screen:
            self.toggle_full_screen()
        if self.dock.isVisible():
            return
        self.dock.show_track(self.queue.current)
        self.dock.set_state(self.player.state)
        self.dock.set_position(self._position_seconds, self._duration)
        self.hide()
        self.dock.dock('right')
        util.settings().setValue('window/docked', True)

    def undock(self):
        if not self.dock.isVisible():
            return
        self.dock.hide()
        self.show()
        self.raise_()
        self.activateWindow()
        util.settings().setValue('window/docked', False)

    def _mpris_update(self):
        if self.mpris is not None:
            self.mpris.update()

    def _media_keys(self):
        """The keyboard's media keys while the window has the focus (on desktops that do not send them over MPRIS)."""
        for key, slot in ((Qt.Key.Key_MediaTogglePlayPause, self.toggle), (Qt.Key.Key_MediaPlay, self.toggle),
                          (Qt.Key.Key_MediaPause, self.player.pause), (Qt.Key.Key_MediaStop, self.stop),
                          (Qt.Key.Key_MediaNext, self.next), (Qt.Key.Key_MediaPrevious, self.previous)):
            action = QAction(self)
            action.setShortcut(QKeySequence(key.value))
            action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            action.triggered.connect(slot)
            self.addAction(action)

    # Layout

    def _button(self, icon, tip, slot):
        button = QToolButton()
        button.setProperty('transport_icon', icon)
        button.setIcon(transport_icon(icon))
        button.setIconSize(QSize(26, 26))
        button.setToolTip(tip)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setMinimumSize(48, 44)
        button.clicked.connect(slot)
        return button

    def changeEvent(self, event):
        # the theme changed: the icons in its text colour
        if event.type() == QEvent.Type.PaletteChange:
            for button in (self.previous_button, self.play_button, self.next_button):
                button.setIcon(transport_icon(button.property('transport_icon')))
            refresh_line_icons(self)
            # the bar's texts carry their colours in their links (rich text): written again in the new ones
            QTimer.singleShot(0, self._recolor_bar)
        super().changeEvent(event)

    def _recolor_bar(self):
        shown = getattr(self, '_now_playing_shown', None)
        if shown is not None:
            self._show_now_playing(*shown)
        if getattr(self, 'queue', None) is not None and hasattr(self, 'next_up'):
            self._show_next_up(self.queue.peek(1))
        if hasattr(self, 'device_name'):
            self._output_tooltip()

    def _transport(self):
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(6)
        bar.setMinimumHeight(76)
        self.previous_button = self._button('previous', 'Previous', self.previous)
        self.play_button = self._button('play', 'Play', self.toggle)
        self.next_button = self._button('next', 'Next', self.next)
        for button in (self.previous_button, self.play_button, self.next_button):
            layout.addWidget(button)
        layout.addSpacing(12)
        middle = QVBoxLayout()
        middle.setSpacing(6)
        self.now_playing = QLabel()
        font = self.now_playing.font()
        font.setPointSizeF(font.pointSizeF() * 1.1)
        self.now_playing.setFont(font)
        # the title is a link: a click shows the track in the library
        self.now_playing.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                                 | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.now_playing.linkActivated.connect(self.show_playing_track)     # its link: track, album or artist
        self.now_playing.setMinimumHeight(self.now_playing.fontMetrics().height() + 2)     # empty: the bar keeps its height
        top = QHBoxLayout()
        top.addWidget(self.now_playing, 1)
        # what plays after it, dim at the right; a click shows the Queue tab
        self.next_up = QLabel()
        self.next_up.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.next_up.linkActivated.connect(lambda link: (self.tabs.setCurrentWidget(self.queue_view), self.queue_view.show_current()))
        self.next_up.setMaximumWidth(320)
        top.addWidget(self.next_up)
        middle.addLayout(top)
        row = QHBoxLayout()
        self.elapsed = QLabel('0:00')
        from cutemusic.markers import MarkerSlider
        self.seek_slider = MarkerSlider()           # (a recording's markers on it: ticks, a click jumps, a right-click renames)
        self.seek_slider.marker_clicked.connect(self._marker_clicked)
        self.seek_slider.marker_rename.connect(self._rename_marker)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.seek_slider.sliderPressed.connect(lambda: setattr(self, '_seeking', True))
        self.seek_slider.sliderReleased.connect(self._seek_released)
        self.seek_slider.actionTriggered.connect(self._seek_action)
        self.total = QLabel('0:00')
        row.addWidget(self.elapsed)
        row.addWidget(self.seek_slider, 1)
        row.addWidget(self.total)
        middle.addLayout(row)
        layout.addLayout(middle, 1)
        # the volume on the row of the track's position, level with it; the output device's name above it, on the
        # row of the title: [device name: a click, the devices] / [mute | volume]
        volume_width = 200
        top.addSpacing(18)
        self.device_name = QLabel()
        self.device_name.setFixedWidth(volume_width)
        self.device_name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        small = self.device_name.font()
        small.setPointSizeF(small.pointSizeF() * 0.85)
        self.device_name.setFont(small)
        self.device_name.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.device_name.linkActivated.connect(lambda link: self.output_menu.exec(
            self.device_name.mapToGlobal(self.device_name.rect().bottomLeft())))
        top.addWidget(self.device_name)
        row.addSpacing(18)
        volume_box = QWidget()
        volume_box.setFixedWidth(volume_width)
        layout = QHBoxLayout(volume_box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        row.addWidget(volume_box)
        self.mute_button = QToolButton()            # mute: a click; moving the volume ends it
        set_line_icon(self.mute_button, 'speaker')
        self.mute_button.setIconSize(QSize(20, 20))
        self.mute_button.setAutoRaise(True)
        self.mute_button.setCheckable(True)
        self.mute_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mute_button.setToolTip('Mute')
        self.mute_button.toggled.connect(self._set_muted)
        layout.addWidget(self.mute_button)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.volume.valueChanged.connect(self._volume_changed)
        layout.addWidget(self.volume, 1)
        # its height fixed, as tall as with a track's title, what is next and the device in it: not a few pixels
        # shorter between two tracks (the labels empty a moment), which moved the view above it down and up
        labels = {self.now_playing: '<a href="x"><b>Title</b></a> — <a href="x">Artist</a>',
                  self.next_up: '<a href="x">Next: Title</a>', self.device_name: '<a href="x">Output</a>'}
        texts = {label: label.text() for label in labels}
        for label, text in labels.items():
            label.setText(text)
        bar.setFixedHeight(max(76, bar.sizeHint().height()))
        for label, text in texts.items():
            label.setText(text)
        return bar

    def _menus(self):
        menu = self.menuBar().addMenu('&File')
        menu.addAction('Choose music folder...', self.choose_folder)
        self.recent_menu = menu.addMenu('Open previous library')
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        action = menu.addAction('Rescan music folder', self.load_library)
        action.setShortcut(QKeySequence('F5'))
        menu.addAction('Organize library...', self.organize_library)
        menu.addAction('Import playlists...', self._import_playlists)
        menu.addSeparator()
        self.empty_trash_action = menu.addAction('Empty Trash...', self.empty_trash)
        menu.aboutToShow.connect(self._update_trash_action)
        menu.addSeparator()
        self.share_action = menu.addAction('Share my library on the LAN')
        self.share_action.setCheckable(True)
        self.share_action.setToolTip('Other AG CuteMusic on the LAN can browse it, play from it and copy from it')
        self.share_action.toggled.connect(self._share)
        menu.addAction('Connect to a library...', self._connect_to_library)
        menu.addSeparator()
        # served or not: not in the menu, Web libraries...' Enable (web.WebInfo)
        self.web_action = QAction('Make negatives for the LAN (web page)', self)
        self.web_action.setCheckable(True)
        self.web_action.setToolTip('A web page on the LAN where songs are sent, with a token, to have their negatives made here')
        self.web_action.toggled.connect(self._serve_web)
        self.web_view.serve_action = self.web_action
        self.web_libraries_action = menu.addAction('Web libraries...', lambda: self.web_view.show_info())
        menu.addSeparator()
        if sys.platform in ('darwin', 'win32'):     # updates of the standalone app (updater.py): on macOS in the application menu
            action = menu.addAction('Check for Updates...', lambda: self.updater.check(manual=True))
            action.setMenuRole(QAction.MenuRole.ApplicationSpecificRole)
        action = menu.addAction('Quit', self.close)
        action.setShortcut(QKeySequence.StandardKey.Quit)

        menu = self.menuBar().addMenu('&Play')
        for text, shortcut, slot in (('Play / Pause', 'Space', self.toggle), ('Next', 'Right', self.next),
                                     ('Previous', 'Left', self.previous), ('Stop', 'Ctrl+.', self.stop),
                                     ('Forward 10 s', 'Shift+Right', lambda: self._skip(10)),
                                     ('Back 10 s', 'Shift+Left', lambda: self._skip(-10))):
            action = menu.addAction(text, slot)
            # Right and Left (also with Ctrl): in the tree too (expand and collapse with + and -, or a double click);
            # the filter field keeps them for its cursor
            action.setShortcuts([QKeySequence(shortcut)] + ([QKeySequence('Ctrl+' + shortcut)] if shortcut in ('Right', 'Left') else []))
        menu.addSeparator()
        self.shuffle_action = menu.addAction('Shuffle')
        self.shuffle_action.setCheckable(True)
        self.shuffle_action.setShortcut(QKeySequence('Ctrl+Shift+S'))     # (Ctrl+S, Cmd+S on macOS: Find)
        self.shuffle_action.toggled.connect(self._shuffle_toggled)
        self.repeat_action = menu.addAction('Repeat')
        self.repeat_action.setCheckable(True)
        self.repeat_action.setShortcut(QKeySequence('Ctrl+R'))
        self.repeat_action.toggled.connect(self._repeat_toggled)
        menu.addSeparator()
        self.output_menu = menu.addMenu('Output device')
        self.output_menu.aboutToShow.connect(self._fill_output_menu)
        action = menu.addAction('Play on a new output when plugged in')
        action.setCheckable(True)
        action.setChecked(util.settings().value('player/auto_switch_outputs', True, type=bool))
        action.toggled.connect(lambda on: (util.settings().setValue('player/auto_switch_outputs', on),
                                           setattr(self.player, 'auto_switch_outputs', on)))
        self._output_tooltip()          # the device's name above the volume
        from cutemusic import driver as _driver
        if _driver.supported():         # the AG Podcast Mixer driver (driver.py): installed, updated, removed
            self.driver_action = menu.addAction('Install AG Podcast Mixer driver...', self._driver_action)
            menu.aboutToShow.connect(self._update_driver_action)
        menu.addSeparator()
        menu.addAction('Save queue as playlist...', self._save_queue_as_playlist)
        action = menu.addAction('Show playing track', lambda: self.show_playing_track())
        action.setShortcut(QKeySequence('Ctrl+L'))

        menu = self.menuBar().addMenu('&View')
        for index, (key, label) in enumerate(PAGES):
            action = menu.addAction(label, lambda index=index: self.visuals.show_page(index))
            action.setShortcut(QKeySequence(f'Ctrl+{index + 1}'))
        action = menu.addAction('Next view', self.visuals.next_page)
        # (Qt's Ctrl is Cmd on macOS, its Meta the Control key: Cmd+Tab is the system's application switcher)
        action.setShortcut(QKeySequence('Meta+Tab' if sys.platform == 'darwin' else 'Ctrl+Tab'))
        menu.addAction('Next VU style', self.visuals.vu.next_style)
        menu.addSeparator()
        # the music browser (the tabs at the left): shown or hidden by hand, or hidden by itself on the mixer while
        # nothing plays (back when leaving the mixer)
        self.library_action = menu.addAction('Music browser', lambda: self.set_library_shown(self.library_action.isChecked()))
        self.library_action.setCheckable(True)
        self.library_action.setShortcut(QKeySequence('Ctrl+B'))
        self.negatives_action = menu.addAction('Negatives (no singer, for karaoke)',
                                               lambda: self.tabs.setCurrentWidget(self.negatives_browser))
        self.negatives_action.setShortcut(QKeySequence('Ctrl+K'))
        self.stop_negatives_action = menu.addAction('Stop making negatives (the rest kept queued)', self.negatives.stop)
        self.stop_negatives_action.setEnabled(False)
        # the web page's users: their tokens, names and email addresses, edited (web.Subscriptions)
        self.web_users_action = menu.addAction('Web users...', lambda: self.web_view.show_subscriptions())
        # how they are made: quality (overlapping windows), device, windows a pass; from the next song on
        from cutemusic import negatives as _negatives
        settings_menu = self.negatives_settings_menu = menu.addMenu('Negatives settings')

        def choices(title, items, current, choose):
            settings_menu.addSection(title)
            group = QActionGroup(settings_menu)
            for key, label in items:
                action = settings_menu.addAction(label, lambda key=key: choose(key))
                action.setCheckable(True)
                action.setChecked(key == current)
                group.addAction(action)
        choices('Quality', [(key, label) for key, label, overlap in _negatives.QUALITIES], _negatives.quality(), _negatives.set_quality)
        choices('Runs on', _negatives.DEVICES, _negatives.device(), _negatives.set_device)
        choices('Windows a pass', [(size, str(size)) for size in _negatives.BATCHES], _negatives.batch(), _negatives.set_batch)
        if self.dock_icon is not None:
            action = menu.addAction('Live Dock icon')
            action.setCheckable(True)
            action.setChecked(util.settings().value('view/live_dock_icon', True, type=bool))
            action.toggled.connect(lambda on: (self.dock_icon.set_enabled(on), util.settings().setValue('view/live_dock_icon', on)))
        # the amplifier's front plate (also its panel's context menu)
        from cutemusic.amp import PLATES
        amp = self.visuals.amp
        plates = menu.addMenu('Amplifier plate')
        group = QActionGroup(plates)
        for key, label in PLATES:
            action = plates.addAction(label.replace(' plate', ''), lambda key=key: amp.set_plate(key))
            action.setCheckable(True)
            action.setData(key)
            group.addAction(action)
        plates.aboutToShow.connect(lambda: [action.setChecked(action.data() == amp.plate) for action in plates.actions()])
        from cutemusic.amp import KNOBS
        knobs = menu.addMenu('Amplifier knobs')
        group = QActionGroup(knobs)
        for key, label in KNOBS:
            action = knobs.addAction(label.replace(' knobs', ''), lambda key=key: amp.set_knobs(key))
            action.setCheckable(True)
            action.setData(key)
            group.addAction(action)
        knobs.aboutToShow.connect(lambda: [action.setChecked(action.data() == amp.knob_style) for action in knobs.actions()])
        menu.addSeparator()
        action = menu.addAction('Dock at the right', lambda: self.dock_right())
        action.setShortcut(QKeySequence('Ctrl+D'))
        self.full_screen_action = menu.addAction('Full screen', self.toggle_full_screen)
        # Ctrl+F (Cmd+F on macOS; Find has no shortcut), on macOS its own apps' Control+Cmd+F too
        self.full_screen_action.setShortcuts([QKeySequence('Ctrl+F'), QKeySequence('Ctrl+Meta+F')]
                                             if sys.platform == 'darwin' else [QKeySequence('Ctrl+F')])
        self.full_screen_action.setCheckable(True)
        menu.addSeparator()
        from cutemusic.theme import THEMES, set_theme, theme
        themes = menu.addMenu('Theme')          # as the amplifier's plate: Follow system, Light, Dark
        group = QActionGroup(themes)
        for key, label in THEMES:
            action = themes.addAction(label, lambda key=key: set_theme(key))
            action.setCheckable(True)
            action.setData(key)
            group.addAction(action)
        themes.aboutToShow.connect(lambda: [action.setChecked(action.data() == theme()) for action in themes.actions()])
        # the karaoke features (general/karaoke, off at first): the negatives and the web libraries, their menus,
        # right-click items and tabs; off, only hidden (a web page being served goes on)
        self.karaoke_action = menu.addAction('Enable karaoke features')
        self.karaoke_action.setCheckable(True)
        self.karaoke_action.setChecked(karaoke_enabled())
        self.karaoke_action.toggled.connect(self.set_karaoke_features)
        from cutemusic import online
        action = menu.addAction('Look up lyrics and covers online')
        action.setCheckable(True)
        action.setChecked(online.enabled())
        action.toggled.connect(lambda on: (online.set_enabled(on), on and self.online.want(self.queue.current)))
        # the lyrics found online for the track playing are wrong (another song's, or it has none): forgotten
        self.forget_lyrics_action = menu.addAction('Wrong lyrics for this song: remove them', self._forget_lyrics)
        menu.aboutToShow.connect(lambda: self.forget_lyrics_action.setEnabled(
            self.queue.current is not None and online.cached_lyrics(self.queue.current) is not None))
        menu.addSeparator()
        action = menu.addAction('Find', lambda: (self.browser.filter.setFocus(), self.browser.filter.selectAll()))
        action.setShortcut(QKeySequence('Ctrl+S'))      # Cmd+S on macOS: the search field (Ctrl+F, Cmd+F: full screen)
        for index, (key, label) in enumerate(MODES):
            action = menu.addAction(f'By {label.lower()}', lambda index=index: self.browser.mode.setCurrentIndex(index))
            # macOS: Cmd+Option (Cmd+Shift+3, 4 and 5 are the system's screenshots)
            action.setShortcut(QKeySequence(f'Ctrl+Alt+{index + 1}' if sys.platform == 'darwin' else f'Ctrl+Shift+{index + 1}'))
        menu = self.menuBar().addMenu('&Help')
        action = menu.addAction(f'About {APP_NAME}', self.show_about)
        action.setMenuRole(QAction.MenuRole.AboutRole)      # macOS: in the application's menu, as its apps have it

    WEBSITE = 'https://ag-projects.com'

    def show_about(self):
        """About: the application's icon, name, version, what it is, its web site."""
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout
        from cutemusic import __doc__ as description, __version__
        dialog = QDialog(self)
        dialog.setWindowTitle(f'About {APP_NAME}')
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(28, 22, 28, 16)
        layout.setSpacing(8)
        from cutemusic import appicon       # the icon, its needles up: music playing
        image = appicon.pixmap(256, appicon.PLAYING)
        image.setDevicePixelRatio(2.0)      # 128 points, sharp on a Retina screen
        picture = QLabel()
        picture.setPixmap(image)
        picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(picture)
        name = QLabel(APP_NAME)
        font = name.font()
        font.setPointSizeF(font.pointSizeF() * 1.6)
        font.setBold(True)
        name.setFont(font)
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name)
        version = QLabel(f'Version {__version__}')
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(version)
        layout.addSpacing(6)
        about = QLabel((description or '').strip().rstrip('.') + '.')
        about.setWordWrap(True)
        about.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(about)
        site = QLabel(f'<a href="{self.WEBSITE}">{self.WEBSITE.split("//", 1)[1]}</a>')
        site.setOpenExternalLinks(True)
        site.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(site)
        rights = QLabel('Copyright \u00a9 2026 AG Projects')
        rights.setStyleSheet('color: gray')
        rights.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(rights)
        license = QLabel('License: <a href="https://www.gnu.org/licenses/gpl-3.0.html">GNU GPL version 3 or later</a>')
        license.setStyleSheet('color: gray')
        license.setOpenExternalLinks(True)
        license.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(license)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addSpacing(6)
        layout.addWidget(buttons)
        dialog.setFixedWidth(380)
        dialog.exec()

    # The library

    def load_library(self):
        self.root = music_folder()
        QTimer.singleShot(0, lambda: self._scan_negatives(cached=True))       # the music folder's negatives
        if not os.path.isdir(self.root):
            self.browser.set_tracks([], self.root)
            self.browser.status.setText(f'{self.root} does not exist: File -> Choose music folder...')
            return
        cached = self.scanner.cached(self.root)
        if cached:
            self._local_library(cached)
        self.scanner.scan(self.root)

    def _local_library(self, tracks):
        """This computer's tracks: in the tree unless a library on the LAN is shown, shared when sharing."""
        self.local_tracks = tracks
        self._tracks_by_path = {track.path: track for track in tracks}
        self.library_server.set_library(tracks, self.root)
        self.history_view.set_library(tracks)
        self.playlist_view.set_library(tracks, self.root)
        if self.remote is None:
            self.browser.set_tracks(tracks, self.root)

    def organize_library(self):
        """Compilations gathered, album folders named with their year, duplicates shown (organizer.py)."""
        from cutemusic.organizer import OrganizerDialog
        dialog = OrganizerDialog(self.local_tracks, self.root, self)
        dialog.organized.connect(self._organized)
        dialog.exec()

    def _organized(self, moved):
        """Files moved (old path: new): the queue and the history follow them, the library is read again."""
        for track in self.queue.tracks:
            if track.path in moved:
                track['path'] = moved[track.path]
        self.history.rename(moved)
        self._save_queue()
        self._prepare_next()
        self.history_view.refresh()
        self.load_library()

    def edit_tags(self, tracks, title):
        """Year, artist, album of tracks written in their files (tagedit.py); the queue follows, the library is
        read again (only the files changed) and the first of them selected in it."""
        from cutemusic.tagedit import TagEditor
        genres = {genre for track in self.local_tracks for genre in (track.get('genres') or [])}
        dialog = TagEditor(tracks, title, self, genres)
        dialog.edited.connect(self._tags_edited)
        dialog.exec()

    def set_colors(self, tracks, name, on):
        """A color marker on (or off) for tracks, written in their files (colors.py); the queue follows, the library
        is read again (only the files changed)."""
        from cutemusic.colors import NAMES, write_colors
        from PyQt6.QtWidgets import QApplication, QMessageBox
        tracks = [track for track in tracks if not track.get('remote') and not track.get('pending')]
        changed, errors = [], []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for track in tracks:
                colors = set(track.get('colors') or ())
                wanted = colors | {name} if on else colors - {name}
                if wanted == colors:
                    continue
                wanted = [color for color in NAMES if color in wanted]
                error = write_colors(track.path, wanted)
                if error:
                    errors.append(f'{track.path}: {error}')
                    continue
                track['colors'] = wanted
                changed.append(track)
        finally:
            QApplication.restoreOverrideCursor()
        for error in errors:
            log.warning(f'[colors] {error}')
        if changed:
            edited = {track.path: track for track in changed}
            for track in self.queue.tracks:
                if track.path in edited and track is not edited[track.path]:
                    track['colors'] = list(edited[track.path]['colors'])
            self._save_queue()
            self.browser.rebuild()
            self.negatives_browser.rebuild()
            shown = getattr(self, '_now_playing_shown', None)     # the bar's song among them: its dots
            if shown is not None and shown[0] is not None and shown[0].path in edited:
                shown[0]['colors'] = list(edited[shown[0].path]['colors'])
                self._show_now_playing(*shown)
            self.scanner.scan(self.root)
        if errors:
            QMessageBox.warning(self, 'Colors', f'{len(errors)} of {len(tracks)} files not written:\n\n' +
                                '\n'.join(errors[:10]) + ('\n...' if len(errors) > 10 else ''))

    def _tags_edited(self, tracks, changes):
        edited = {track.path: track for track in tracks}
        for track in self.queue.tracks:
            if track.path in edited and track is not edited[track.path]:
                for key in ('year', 'artist', 'albumartist', 'album', 'genres'):
                    track[key] = edited[track.path].get(key)
        self._save_queue()
        self._reveal = tracks[0]
        self.browser.rebuild()
        self._reveal_track()
        self.scanner.scan(self.root)

    def move_to_trash(self, tracks, title):
        """Songs moved to the Trash in the music folder (trash.py), once confirmed: out of the queue, the library read
        again."""
        from cutemusic.trash import move_to_trash
        if self.web_token is not None:      # a token's songs: removed (web.py), not the music folder's Trash
            return self._web_remove(tracks)
        tracks = [track for track in tracks if not track.get('remote')]
        if not tracks:
            return
        count = f'{len(tracks)} song' + ('s' if len(tracks) != 1 else '')
        if len(tracks) == 1:
            what = f'"{title}"'
        elif title.endswith(' selected'):
            what = count
        else:
            what = f'"{title}" ({count})'
        box = QMessageBox(QMessageBox.Icon.Question, 'Move to Trash', f'Move {what} to the Trash?', parent=self)
        box.setInformativeText('They are taken out of the library, into the Trash in the music folder. '
                               'File -> Empty Trash deletes them for good.')
        box.setDetailedText('\n'.join(track.path for track in tracks))
        move = box.addButton('Move to Trash', QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(move)
        box.exec()
        if box.clickedButton() is not move:
            return
        paths = {track.path for track in tracks}
        gone = [index for index, track in enumerate(self.queue.tracks) if track.path in paths]
        if gone:
            if self.queue.current is not None and self.queue.current.path in paths:
                self.player.stop()      # the file is not held open while it is moved
            self._remove_from_queue(gone)
        moved, errors = move_to_trash(sorted(paths), self.root)
        self.browser.status.setText(f'{moved} song' + ('s' if moved != 1 else '') + ' moved to the Trash')
        if errors:
            QMessageBox.warning(self, 'Move to Trash', f'{len(errors)} not moved:\n\n' + '\n'.join(errors[:10]) +
                                ('\n...' if len(errors) > 10 else ''))
        self.scanner.scan(self.root)

    def _update_trash_action(self):
        from cutemusic.trash import trash_contents
        files, size = trash_contents(self.root) if self.root else (0, 0)
        self.empty_trash_action.setEnabled(files > 0)
        self.empty_trash_action.setText(f'Empty Trash ({files} files, {size / 1e6:.0f} MB)...' if files else 'Empty Trash')

    def empty_trash(self):
        from cutemusic.trash import empty_trash, trash_contents, trash_folder
        files, size = trash_contents(self.root)
        if not files:
            return
        box = QMessageBox(QMessageBox.Icon.Warning, 'Empty Trash',
                          f'Delete the {files} files in the Trash ({size / 1e6:.0f} MB) for good?', parent=self)
        box.setInformativeText(f'{trash_folder(self.root)}\n\nThis cannot be undone.')
        delete = box.addButton('Empty Trash', QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not delete:
            return
        error = empty_trash(self.root)
        if error:
            QMessageBox.warning(self, 'Empty Trash', f'Not all deleted: {error}')
        else:
            self.browser.status.setText(f'Trash emptied: {files} files deleted')

    def _fill_recent_menu(self):
        menu = self.recent_menu
        menu.clear()
        current = os.path.normpath(self.root)
        folders = library.recent_folders()
        if current not in [os.path.normpath(folder) for folder in folders]:     # the one open: listed from the start
            folders.insert(0, self.root)
        for folder in folders:
            shown = folder.replace(os.path.expanduser('~'), '~', 1) if folder.startswith(os.path.expanduser('~')) else folder
            exists = os.path.isdir(folder)
            action = menu.addAction(shown if exists else f'{shown} (not found)')
            action.setCheckable(True)
            action.setChecked(os.path.normpath(folder) == current)
            action.setEnabled(exists)
            action.triggered.connect(lambda checked, folder=folder: self.open_folder(folder))
        menu.addSeparator()
        menu.addAction('Clear list', library.clear_recent_folders)

    def open_folder(self, folder):
        if os.path.normpath(folder) == os.path.normpath(self.root):
            return
        set_music_folder(folder)
        self.load_library()

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Music folder', self.root)
        if folder:
            set_music_folder(folder)
            self.load_library()

    _reveal = None

    def _reveal_track(self):
        if self._reveal is not None and self.browser.select_track(self._reveal):
            self._reveal = None

    def _scanned(self, tracks):
        self._local_library(tracks)
        self._reveal_track()
        if library.mutagen is None:
            self.browser.status.setText(self.browser.status.text() + ' — no tags: mutagen is missing (python3-mutagen)')

    # Negatives (negatives.py)

    def make_negatives(self, tracks):
        """Right-click -> Make negative: the songs' negatives queued, made in the background (not of negatives)."""
        if self.web_token is not None:      # a token's songs: Start (web.py)
            jobs = self.web.jobs_at([track.path for track in tracks])
            self.web.start_jobs(jobs)
            self.browser.status.setText(f'{self.web.title(self.web_token)}: {len(jobs)} song' + ('s' if len(jobs) != 1 else '') + ' started')
            return
        paths = [track.path for track in tracks if not track.get('remote') and not self._is_negative(track)]
        added = self.negatives.add(paths, self.root)
        from cutemusic.negatives import negatives_folder
        left = len(paths) - added
        self.browser.status.setText(f'Negatives: {added} song' + ('s' if added != 1 else '') + f' queued, made into {negatives_folder()}'
                                    + (f' ({left} made or queued already)' if left else ''))

    def _negatives_changed(self):
        installing = self.negatives.stage == 'install'
        self._separator_install_dialog(installing)
        self.stop_negatives_action.setEnabled(self.negatives.busy or bool(self.negatives.queue))
        self._show_negatives()      # the ones being made, queued, failed: in the Negatives tab with their state

    _install_dialog = None

    def _separator_install_dialog(self, shown):
        """The separator being installed (once, minutes: about 3 GB): a small window of its own with pip's latest
        line, closed when done; the queue then goes on as usual."""
        from PyQt6.QtWidgets import QProgressDialog
        if not shown:
            if self._install_dialog is not None:
                dialog, self._install_dialog = self._install_dialog, None
                dialog.canceled.disconnect()        # (closing it emits canceled: not a Stop)
                dialog.close()
                dialog.deleteLater()
            return
        if self._install_dialog is None:
            dialog = self._install_dialog = QProgressDialog(self)
            dialog.setWindowTitle('Negatives')
            dialog.setRange(0, 0)           # busy: pip's own progress is not counted
            dialog.setMinimumDuration(0)
            dialog.setMinimumWidth(520)
            dialog.setCancelButtonText('Stop')
            dialog.canceled.connect(self.negatives.stop)
            dialog.setWindowModality(Qt.WindowModality.NonModal)
            dialog.show()
        self._install_dialog.setLabelText('Installing the separator for making negatives (once, about 3 GB)...\n\n'
                                          + (self.negatives.message or ''))

    def _scan_negatives(self, cached):
        from cutemusic.negatives import negatives_folder
        folder = negatives_folder()
        if not os.path.isdir(folder):
            self._negatives_scanned([])
            return
        if cached:      # at once: what was found last time, else empty (never the songs while it is read)
            self._negatives_scanned(self.negative_scanner.cached(folder) or [])
        self.negative_scanner.scan(folder)

    def _negatives_scanned(self, tracks):
        self.negative_tracks = tracks
        self._show_negatives()

    def _show_negatives(self):
        """The Negatives tab: the negatives made, and the ones being made, queued or failed (this session) where
        they will be, dimmed with their state (not played: pending)."""
        from cutemusic.negatives import negative_path, negatives_folder
        if self.web_token is not None:      # a token's: its negatives, made or not
            songs, tracks, root = self.web.library(self.web_token)
            owner = self.web.title(self.web_token) if self.web.owner(self.web_token) else 'No account'
            tracks = [Track(dict(track, owner=owner, token=self.web_token, root=root)) for track in tracks]
            key = ['web'] + [(track.path, track.get('pending')) for track in tracks]
            if key != getattr(self, '_negatives_shown', None):
                self._negatives_shown = key
                self.negatives_browser.set_tracks(tracks, root)
            return
        negatives = self.negatives
        made = {track.path for track in self.negative_tracks}
        states = []         # (source, state)
        if negatives.current is not None:
            states.append((negatives.current[0], f'{negatives.percent}%'))
        states += [(source, 'queued') for source, negative in negatives.queue]
        states += [(source, f'failed: {error}') for source, negative, seconds, error in negatives.finished if error]
        pending, seen = [], set()
        for source, state in states:
            negative = negative_path(source, self.root)
            if negative is None or negative in made or negative in seen:
                continue
            seen.add(negative)
            original = self._tracks_by_path.get(source) or Track(
                path=source, title=os.path.splitext(os.path.basename(source))[0], artist='', albumartist='', album='',
                genres=[], year=0, track=0, disc=0, duration=0.0)
            pending.append(Track(dict(original, path=negative, pending=state)))
        # by whose they are: the ones made here (Local), then each web account's tokens' (a song copied into several
        # tokens: its negative under each)
        tracks = [Track(dict(track, owner='Local')) for track in self.negative_tracks + pending]
        for token in self.web.tokens():
            owner = self.web.title(token) if self.web.owner(token) else 'No account'
            songs, web_negatives, root = self.web.library(token)
            tracks += [Track(dict(track, owner=owner, token=token, root=root)) for track in web_negatives]
        key = [(track.path, track.get('pending'), track.get('owner')) for track in tracks]
        if key == getattr(self, '_negatives_shown', None):
            return          # (the separator's percent the same: nothing to draw again)
        self._negatives_shown = key
        self.negatives_browser.set_tracks(tracks, negatives_folder())
        if not tracks:
            self.negatives_browser.status.setText('No negatives yet: right-click songs, an album or a playlist -> Make negative')

    def trash_negatives(self, tracks, title):
        """Negatives moved to the system's Trash (not the music folder's), once confirmed, with their lyrics files;
        their folders gone when nothing else is in them but images. The songs stay; Make negative makes them again."""
        from PyQt6.QtCore import QFile
        from cutemusic.library import IMAGE_EXTENSIONS
        if self.web_token is not None:
            return self._web_remove(tracks)
        web = [track for track in tracks if track.get('token')]     # a web account's: its jobs removed (web.py)
        if web:
            self._web_remove(web)
        from cutemusic.negatives import negatives_folder
        tracks = [track for track in tracks if not track.get('token') and self._is_negative(track) and not track.get('pending')]
        if not tracks:
            return
        count = f'{len(tracks)} negative' + ('s' if len(tracks) != 1 else '')
        answer = QMessageBox.question(self, 'Move to Trash', f'Move {count} ({title}) to the Trash?\n\n'
                                      'The songs themselves stay; Make negative makes them again.')
        if answer != QMessageBox.StandardButton.Yes:
            return
        moved, errors, folders = 0, [], set()
        for track in tracks:
            base = os.path.splitext(track.path)[0]
            if not QFile.moveToTrash(track.path)[0]:       # (ok, where in the Trash): not there, left as it is
                errors.append(track.path)
                continue
            moved += 1
            for extension in ('.lrc', '.txt'):
                if os.path.exists(base + extension):
                    QFile.moveToTrash(base + extension)
            folders.add(os.path.dirname(track.path))
        top = os.path.abspath(negatives_folder())
        for folder in sorted(folders, key=len, reverse=True):       # emptied of songs: its images too, and up
            while os.path.abspath(folder).startswith(top + os.sep):
                try:
                    names = os.listdir(folder)
                except OSError:
                    break
                if any(os.path.isdir(os.path.join(folder, name)) or os.path.splitext(name)[1].lower() not in IMAGE_EXTENSIONS
                       for name in names if not name.startswith('.')):
                    break
                for name in names:
                    try:
                        os.remove(os.path.join(folder, name))
                    except OSError:
                        pass
                try:
                    os.rmdir(folder)
                except OSError:
                    break
                folder = os.path.dirname(folder)
        log.info(f'[negatives] {moved} moved to the Trash' + (f', {len(errors)} not: {errors}' if errors else ''))
        if errors:
            QMessageBox.warning(self, 'Move to Trash', f'{len(errors)} not moved:\n\n' + '\n'.join(errors[:10]))
        self._scan_negatives(cached=False)
        self._update_version_button()

    def _negatives_menu(self, menu, tracks):
        """Delete negative: the negatives made of the songs among tracks (the Library's right-click menu), to the
        Trash once confirmed (trash_negatives); none made: nothing in the menu."""
        negatives = []
        for track in tracks:
            if self._is_negative(track):
                continue
            other = self._counterpart(track)
            if other is not None and self._is_negative(other) and not other.get('pending') and os.path.exists(other.path):
                negatives.append(other)
        if not negatives:
            return
        if len(negatives) == 1:
            title = negatives[0].display_title
            label = 'Delete negative...'
        else:
            title = f'{len(negatives)} negatives'
            label = f'Delete negatives ({len(negatives)})...'
        menu.addAction(label, lambda: self.trash_negatives(negatives, title))

    def _delete_negatives(self, made, pending):
        """The Negatives tab's Delete...: the negatives made to the Trash (trash_negatives, confirmed), the ones not
        made yet out of the queue or forgotten (failed), a token's jobs removed."""
        from cutemusic.negatives import original_relative
        if pending:
            if self.web_token is not None or any(track.get('token') for track in pending):
                self._web_remove([track for track in pending if self.web_token is not None or track.get('token')])
            local = [track for track in pending if self.web_token is None and not track.get('token')]
            sources = []
            for track in local:
                stem = original_relative(track.path)
                source = next((path for path in self._tracks_by_path
                               if os.path.splitext(os.path.relpath(path, self.root))[0] == stem), None)
                if source:
                    sources.append(source)
            if sources:
                self.negatives.remove(sources)
                self.negatives.forget(sources)
        if made:
            title = made[0].display_title if len(made) == 1 else f'{len(made)} negatives'
            self.trash_negatives(made, title)
        else:
            self._show_negatives()

    def _pending_menu(self, menu, tracks):
        """Right-click on negatives not made yet: out of the queue; Stop; the songs in the file manager."""
        from cutemusic.negatives import original_relative
        if self.web_token is not None:      # a token's (web.py)
            jobs = self.web.jobs_at([track.path for track in tracks])
            menu.addAction('Start', lambda: self.web.start_jobs(jobs)).setEnabled(
                any(track.get('pending', '').startswith(('waiting', 'failed')) for track in tracks))
            menu.addAction('Remove', lambda: self._web_remove(tracks)).setEnabled(bool(jobs))
            menu.addAction('Stop making negatives (the rest kept queued)', self.negatives.stop).setEnabled(self.negatives.busy)
            return
        sources = []
        for track in tracks:
            stem = original_relative(track.path)
            source = next((path for path in self._tracks_by_path
                           if os.path.splitext(os.path.relpath(path, self.root))[0] == stem), None)
            if source:
                sources.append(source)
        queued = [source for source in sources if source in {entry[0] for entry in self.negatives.queue}]
        failed = [source for source in sources if any(entry[0] == source and entry[3] for entry in self.negatives.finished)]
        menu.addAction('Remove from the queue', lambda: self.negatives.remove(queued)).setEnabled(bool(queued))
        menu.addAction('Try again', lambda: (self.negatives.forget(failed), self.negatives.add(failed, self.root))).setEnabled(bool(failed))
        menu.addAction('Stop making negatives (the rest kept queued)', self.negatives.stop).setEnabled(self.negatives.busy)
        menu.addAction('Start making negatives', self.negatives.start).setEnabled(not self.negatives.busy and bool(self.negatives.queue))
        menu.addSeparator()
        menu.addAction(f'{util.REVEAL_LABEL}: the song' + ('s' if len(sources) > 1 else ''), lambda: util.reveal(sources)).setEnabled(bool(sources))

    def _is_negative(self, track):
        from cutemusic.negatives import negatives_folder
        if track is None or track.get('remote'):
            return False
        if self.web.is_negative(track.path):        # a token's (web.py)
            return True
        folder = os.path.abspath(negatives_folder())
        return os.path.commonpath([os.path.abspath(track.path), folder]) == folder

    def _counterpart(self, track):
        """The other version of track: its negative when there is one, or the song a negative is of (from this
        computer's library); None."""
        if track is None or track.get('remote'):
            return None
        other = self.web.counterpart(track.path)       # a song sent from the web page, or its negative
        if other is not None:
            return read_track(other, os.path.dirname(other), durations=False)
        from cutemusic.negatives import negative_path, original_relative
        if self._is_negative(track):
            stem = original_relative(track.path)
            for candidate in self.local_tracks:
                if os.path.splitext(os.path.relpath(candidate.path, self.root))[0] == stem:
                    return candidate
            return None
        negative = negative_path(track.path, self.root)
        if negative is None or not os.path.exists(negative):
            return None
        return next((candidate for candidate in self.negative_tracks if candidate.path == negative), None) or \
            Track(dict(track, path=negative))

    def _update_version_button(self, track=None):
        track = track if track is not None else self.queue.current
        other = self._counterpart(track)
        on_mixer = self.visuals.stack.currentWidget() is self.visuals.mixer
        if other is None or on_mixer:
            self.version_button.hide()
            return
        playing = 'negative' if self._is_negative(track) else 'original'
        for key, button in self._version_buttons.items():
            button.setChecked(key == playing)
            button.setCursor(Qt.CursorShape.ArrowCursor if key == playing else Qt.CursorShape.PointingHandCursor)
            button.setToolTip(('Playing: ' if key == playing else 'Click: go on with ') +
                              ('the song, with its singer' if key == 'original' else 'its negative (no singer, for karaoke)') +
                              ('' if key == playing else ', from here'))
        self._version_corner.place()
        self.version_button.show()

    def _choose_version(self, key):
        """Original | Negative clicked: the other one from here; the one playing stays as it is (lit)."""
        playing = 'negative' if self._is_negative(self.queue.current) else 'original'
        if key != playing:
            self.switch_version()
        else:
            self._version_buttons[key].setChecked(True)

    def switch_version(self):
        """The song playing swapped for its other version (negative / original), from the same point, playing or
        paused as it was; in the queue in its place."""
        track = self.queue.current
        other = self._counterpart(track)
        if other is None:
            return
        seconds = self._position_seconds
        paused = self.player.state == 'paused'
        self.queue.tracks[self.queue.order[self.queue.position]] = other
        log.info(f'[player] {other.path} from {seconds:.0f} s (the other version)')
        self.player.play_file(other.path, seconds, paused=paused)
        self._update_track(other, lyrics=False)     # the view stays as it is (the amplifier, the VU meter...)
        self._position_seconds = seconds
        self._position(seconds, other.get('duration') or track.get('duration') or 0)
        self._prepare_next()
        self._save_queue()

    # Playing

    def play_tracks(self, tracks, index=0):
        if not tracks:
            return
        self.queue.set(tracks, index)
        self._play_current()

    def _enqueue(self, tracks, next_up):
        if self.queue.current is None:
            self.play_tracks(tracks, 0)
            return
        self.queue.add(tracks, next_up)
        self._prepare_next()

    def _play_now(self, tracks):
        """Tracks played now, put after the one playing; the rest of the queue kept."""
        if self.queue.current is None:
            self.play_tracks(tracks, 0)
            return
        self.queue.add(tracks, True)
        self.queue.move(1)
        self._play_current()

    def _insert_dropped(self, paths, position):
        """Tracks dragged from the library to the Queue: put in at position in the play order (-1: the end)."""
        known = {track.path: track for track in self.browser.tracks}       # (the library shown: a remote one too)
        tracks = [known[path] for path in paths if path in known]
        if not tracks:
            return
        if self.queue.current is None and not self.queue.order:
            self.play_tracks(tracks, 0)
            return
        self.queue.insert(tracks, position)
        self._prepare_next()

    def _play_current(self):
        track = self.queue.current
        if track is None:
            self.stop()
            return
        if track.get('remote') and not os.path.exists(track.path):
            # from a library on the LAN: fetched into the cache first, played when there (_downloaded)
            self.player.stop()
            self._update_track(track)
            self.now_playing.setText(f'Fetching {_html(track.display_title)} from the LAN...')
            self.downloader.fetch(track, track.path, ('play', track.path))
            return
        log.info(f'[player] {track.path}')
        self.player.play_file(track.path)
        self._update_track(track)
        self._prepare_next()
        self._played(track)

    def _played(self, track):
        from cutemusic.history import log_play
        log_play(track)
        self.history.add(track)
        self.history_view.refresh()
        self._save_queue()

    def _prepare_next(self):
        # called after every change of the queue: the next track for the gapless start, and the Queue tab
        following = self.queue.peek(1)
        if following is not None and following.get('remote') and not os.path.exists(following.path):
            self.player.set_next_file(None)         # fetched now; the next one when it is there (_downloaded)
            if following.path not in self._fetching:
                self._fetching.add(following.path)
                self.downloader.fetch(following, following.path, ('next', following.path))
        else:
            self.player.set_next_file(following.path if following is not None else None)
        self.queue_view.refresh(self.queue)
        self._show_next_up(following)
        self._mpris_update()

    # Negatives for the LAN (web.py)

    @property
    def web_token(self):
        """The token whose songs are shown (the source button's "Web 12345"), else None."""
        return self.remote.get('web') if self.remote is not None else None

    def _show_web(self):
        """The Library tab on the shown token's songs sent, the Negatives tab on their negatives."""
        if self.web_token not in self.web.tokens():         # all its songs removed
            self._show_library(None)
            return
        songs, negatives, root = self.web.library(self.web_token)
        key = [(track.path, track.get('title')) for track in songs]
        if key != getattr(self, '_web_shown', None):
            self._web_shown = key
            self.browser.set_tracks(songs, root)
        self._show_negatives()

    def _web_confirm(self, title, what, detail):
        box = QMessageBox(QMessageBox.Icon.Question, title, what, parent=self)
        box.setInformativeText(detail)
        remove = box.addButton('Remove', QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is remove

    def _web_remove(self, tracks):
        """Songs of a token (sent, or their negatives) removed: the song sent and its negative deleted, once confirmed."""
        jobs = self.web.jobs_at([track.path for track in tracks])
        if jobs and self._web_confirm('Remove', f'Remove {len(jobs)} song' + ('s' if len(jobs) != 1 else '') + (f' of {self.web.title(self.web_token)}?' if self.web_token else ' from the web accounts?'),
                                      'The songs sent and their negatives are deleted, on the web page too (not the one being made).'):
            self.web.remove(jobs)

    def set_karaoke_features(self, on):
        util.settings().setValue('general/karaoke', bool(on))
        self._apply_karaoke_features()

    def _apply_karaoke_features(self):
        """The negatives' and the web libraries' menu items, right-click items and tabs shown or not (general/karaoke)."""
        on = karaoke_enabled()
        for action in (self.negatives_action, self.stop_negatives_action, self.web_users_action,
                       self.negatives_settings_menu.menuAction(), self.web_libraries_action):
            action.setVisible(on)
        self.negatives_action.setEnabled(on)          # (its shortcut too)
        self.browser.make_negatives = self.make_negatives if on else None
        self.browser.negatives_menu = self._negatives_menu if on else None
        self.browser.fill_web_menu = self._fill_web_menu if on else None
        self.playlist_view.make_negatives = self.make_negatives if on else None
        self.playlist_view.fill_web_menu = self._fill_web_menu if on else None
        negatives = self.tabs.indexOf(self.negatives_browser)
        if not on and self.tabs.currentIndex() in (negatives, self.tabs.indexOf(self.web_view)):
            self.tabs.setCurrentWidget(self.browser)
        self.tabs.setTabVisible(negatives, on)
        self.tabs.setTabVisible(self.tabs.indexOf(self.web_view), on and self.web_action.isChecked())

    def _serve_web(self, on):
        util.settings().setValue('web/on', on)
        self.tabs.setTabVisible(self.tabs.indexOf(self.web_view), on and karaoke_enabled())     # the Web tab: only while serving
        if not on:
            self.web.stop()
        elif not self.web.start():
            from cutemusic.web import port
            self.web_action.setChecked(False)
            QMessageBox.warning(self, 'Negatives for the LAN', f'Cannot listen on port {port()} (in use?): File -> Web libraries... (port)')

    def _fill_web_menu(self, menu, tracks, browser=None):
        """Right-click -> Copy to web library: the songs copied into a token's library (played on its web page;
        their negatives made with Start), the tokens there are, or a new one."""
        paths = [track.path for track in tracks if not track.get('remote') and not track.get('pending')]
        count = f' ({len(paths)} songs)' if len(paths) > 1 else ''
        for token in self.web.tokens():
            if token != self.web_token:         # (not the token shown: its own songs)
                menu.addAction(f'{self.web.title(token)}{count}', lambda token=token: self._copy_to_web(paths, token, browser))
        if menu.actions():
            menu.addSeparator()
        menu.addAction('New token...', lambda: self._copy_to_web(paths, None, browser))
        menu.setEnabled(bool(paths))

    def _fill_negatives_web_menu(self, menu, tracks):
        """The Negatives tab's right-click -> Copy to web library: the songs the negatives are of, copied into a
        token's library with their negatives (web.add_local finds them in the Negatives folder: made at once).
        Not on a token's negatives (those are web ones already)."""
        if self.web_token is not None:
            menu.setEnabled(False)
            return
        songs = [song for song in (self._counterpart(track) for track in tracks) if song is not None]
        self._fill_web_menu(menu, songs, self.negatives_browser)

    def _copy_to_web(self, paths, token, browser=None):
        self.web_view.put(paths, token, ask=token is None)
        (browser or self.browser).status.setText(self.web_view.status.text())

    def _web_changed(self):
        """The Web tab's name: how many songs sent wait for Start, in amber; a new one: the window asks for attention.
        The tokens in the source button; the token shown, if one is, shown again."""
        self._libraries_found(list(self.discovery.libraries.values()))
        if self.web_token is not None:
            self._show_web()
        else:
            self._show_negatives()      # (the web accounts' negatives in the Negatives tab)
        from PyQt6.QtGui import QColor
        from cutemusic.web import WAITING_COLOR
        waiting = len(self.web.waiting())
        index = self.tabs.indexOf(self.web_view)
        self.tabs.setTabText(index, f'Web ({waiting})' if waiting else 'Web')
        self.tabs.tabBar().setTabTextColor(index, QColor(WAITING_COLOR) if waiting else QColor())
        self.tabs.setTabToolTip(index, f'{waiting} song' + ('s' if waiting != 1 else '') + ' sent from the LAN wait for Start'
                                if waiting else 'Songs sent from the LAN for their negatives')
        if waiting > self._web_waiting:
            self.browser.status.setText(f'Web: {waiting} song' + ('s' if waiting != 1 else '') + ' sent from the LAN wait for Start (the Web tab)')
            QApplication.alert(self)
        self._web_waiting = waiting
        asked = len(self.web.download_requests())      # Request download on the page: the window alerted too
        if asked > getattr(self, '_web_asked', asked):
            self.browser.status.setText(f'Web: {asked} download' + ('s' if asked != 1 else '') + ' requested (the Web tab: Allow download)')
            QApplication.alert(self)
        self._web_asked = asked

    # Libraries on the LAN

    _fetching = set()

    def _share(self, on):
        util.settings().setValue('sharing/on', on)
        if on:
            self.library_server.set_library(self.local_tracks, self.root)
            if not self.library_server.start():
                self.share_action.setChecked(False)
        else:
            self.library_server.stop()

    def _libraries_found(self, libraries):
        self.browser.source.set_libraries(libraries + self._connected + self.web.sources())

    _connected = []

    def _connect_to_library(self):
        from PyQt6.QtWidgets import QInputDialog
        from cutemusic.sharing import PORT
        text, ok = QInputDialog.getText(self, 'Connect to a library', 'Address of a computer sharing its library '
                                        f'(host or IP, :port when not {PORT}):')
        text = text.strip()
        if not ok or not text:
            return
        address = text if text.startswith('http') else f'http://{text}' + ('' if ':' in text else f':{PORT}')
        self._show_library({'name': text, 'id': '', 'address': address})

    def _show_library(self, library):
        """The tree on a library on the LAN (read in a thread), or on this computer's (None)."""
        self._web_shown = None
        if library is None:
            self.remote = None
            self.browser.source.set_current(None)
            self.browser.set_tracks(self.local_tracks, self.root)
            self._show_negatives()
            return
        if library.get('web'):          # a token's songs sent from the web page (web.py)
            self.remote = library
            self.browser.source.set_current(library)
            self._show_web()
            return
        self.browser.status.setText(f'Reading the library of {library["name"]}...')
        import threading
        from cutemusic.sharing import fetch_library

        def read():
            try:
                name, identifier, tracks = fetch_library(library['address'])
                util.call_in_gui_thread(self._remote_library, dict(library, name=name, id=identifier), tracks, None)
            except (OSError, ValueError) as e:
                util.call_in_gui_thread(self._remote_library, library, [], str(e))
        threading.Thread(target=read, name='remote library', daemon=True).start()

    def _remote_library(self, library, tracks, error):
        if error is not None:
            log.warning(f'[sharing] {library["address"]}: {error}')
            self.browser.status.setText(f'Cannot read the library of {library["name"]}: {error}')
            return
        from cutemusic.sharing import cache_root
        if library['address'] not in [entry['address'] for entry in self.discovery.libraries.values()] and \
                library['address'] not in [entry['address'] for entry in self._connected]:
            self._connected = self._connected + [library]       # connected by address: kept in the list
            self._libraries_found(list(self.discovery.libraries.values()))
        self.remote = library
        self.browser.source.set_current(library)
        self.browser.set_tracks(tracks, cache_root(library['id']))
        self._show_negatives()
        log.info(f'[sharing] showing {library["name"]} ({len(tracks)} tracks)')

    def _copy_local(self, tracks):
        """Tracks of a library on the LAN copied into this computer's music folder as <artist>/<album>/<file>: their
        path there without its top folder ("Old iTunes Music/...", "Music/..."); the ones already here left; the
        library read again after."""
        count = 0
        for track in tracks:
            if not track.get('remote'):
                continue
            parts = track['rel'].split('/')
            if len(parts) >= 4:         # <top folder>/<artist>/<album>/<file>: the top folder left out
                parts = parts[1:]
            destination = os.path.join(self.root, *parts)
            if os.path.exists(destination):
                continue
            self.downloader.fetch(track, destination, ('copy', destination))
            count += 1
        self._copied += count
        self.browser.status.setText(f'Copying {count} tracks to this computer...' if count else
                                    'Already on this computer')
        log.info(f'[sharing] copying {count} tracks into {self.root}')

    def _downloaded(self, tag, track, ok):
        kind, path = tag
        if kind == 'play':
            current = self.queue.current
            if current is not None and current.path == path:
                if ok:
                    self._play_current()
                else:
                    self.now_playing.setText(f'Cannot fetch {_html(track.display_title)} from the LAN')
        elif kind == 'next':
            self._fetching.discard(path)
            if ok:
                self._prepare_next()
        elif kind == 'copy':
            self.browser.status.setText(f'Copying to this computer: {self.downloader.pending} to go...'
                                        if self.downloader.pending else self.browser.status.text())
            if ok:
                log.info(f'[sharing] copied {path}')

    def _downloads_idle(self):
        if self._copied:
            self.browser.status.setText(f'{self._copied} tracks copied to this computer')
            self._copied = 0
            self.scanner.scan(self.root)        # they appear in this computer's library

    # Playlists (playlists.py)

    def _fill_playlist_menu(self, menu, tracks):
        for playlist in self.playlist_view.playlists.items:
            menu.addAction(playlist.name, lambda playlist=playlist: self.playlist_view.add(playlist, tracks))
        if self.playlist_view.playlists.items:
            menu.addSeparator()
        menu.addAction('New playlist...', lambda: self.playlist_view.new_playlist(tracks))

    def _save_queue_as_playlist(self):
        tracks = [self.queue.tracks[index] for index in self.queue.order]
        if tracks and self.playlist_view.new_playlist(tracks) is not None:
            self.tabs.setCurrentWidget(self.playlist_view)

    def _import_playlists(self):
        paths, chosen = QFileDialog.getOpenFileNames(self, 'Import playlists', os.path.expanduser('~/Music'),
                                                     'Playlists (Library.xml *.xml *.m3u *.m3u8 *.pls)')
        if not paths:
            return
        from cutemusic.playlists import Playlist
        count, errors = 0, []
        for path in paths:
            try:
                if path.lower().endswith('.xml'):
                    count += self.playlist_view.import_library_xml(path)
                else:
                    loaded = Playlist.load(path)
                    self.playlist_view.playlists.create(loaded.name, loaded.entries, self.playlist_view.known)
                    count += 1
            except Exception as e:
                errors.append(f'{os.path.basename(path)}: {e}')
                log.warning(f'[playlists] cannot import {path}: {e}')
        self.playlist_view.refresh()
        self.tabs.setCurrentWidget(self.playlist_view)
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.information(self, 'Import playlists', f'{count} playlists imported.'
                                + ('\n\n' + '\n'.join(errors) if errors else ''))

    def _show_next_up(self, following):
        if self.queue.current is None:
            self.next_up.setText('')
            return
        dim = self.palette().color(self.palette().ColorRole.PlaceholderText).name()
        if following is None:
            text, tip = 'Last in the queue', 'Nothing after it: the queue ends here'
        else:
            wraps = self.queue.position + 1 >= len(self.queue.order)
            name = f'{following.display_title} – {following.get("artist") or following.album_artist}'
            text = 'Next: ' + self.next_up.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 260) + (' (again from the top)' if wraps else '')
            tip = f'Next: {name}' + (f'\n{following["album"]}' if following.get('album') else '') + '\nClick: the Queue'
        self.next_up.setText(f'<a href="queue" style="color: {dim}; text-decoration: none">{_html(text)}</a>')
        self.next_up.setToolTip(tip)

    def _jump(self, position):
        if 0 <= position < len(self.queue.order):
            self.queue.position = position
            self._play_current()

    def _remove_from_queue(self, indexes):
        playing = self.queue.current
        self.queue.remove(indexes)
        if self.queue.current is not playing:       # the one playing was removed: on to the next one, or none
            state = self.player.state
            if self.queue.current is None:
                self.stop()
            elif state == 'playing':
                self._play_current()
            else:
                self.player.play_file(self.queue.current.path, paused=True)
                self._update_track(self.queue.current)
                self._position_seconds = 0.0
        self._prepare_next()
        self._save_queue()

    def _reorder_queue(self, order):
        self.queue.reorder(order)
        self._prepare_next()
        self._save_queue()

    def _fill_output_menu(self):
        menu = self.output_menu
        menu.clear()
        group = QActionGroup(menu)
        names = sorted(self.player.output_devices(), key=str.lower)
        for name in [None] + names:
            action = menu.addAction(name or 'System default')
            action.setCheckable(True)
            action.setChecked(name == self.player.output)
            action.triggered.connect(lambda checked, name=name: self._set_output(name))
            group.addAction(action)
            if name is None and names:
                menu.addSeparator()
        if self.player.output and self.player.output not in names:      # chosen, not connected now
            action = menu.addAction(f'{self.player.output} (not connected)')
            action.setEnabled(False)

    def _set_output(self, name):
        value = self._output_volumes().get(name or '')
        if isinstance(value, int) and not self.player.muted:     # its volume before it plays: no burst at the old one
            self.player.set_volume(value / 100)
        self.player.set_output(name)
        util.settings().setValue('player/output', self.player.output or '')
        self._output_tooltip()
        self.visuals.amp.set_output(self.player.output)      # no device probe right after a switch
        for mic in self._mics():
            mic.set_output(self.player.output)
        self.call_audio.set_output(self.player.output)
        self.app_audio.set_output(self.player.output)
        self._restore_output_volume()

    def _output_changed(self, name, why):
        """The player moved to another output by itself (plugged in, or gone): shown, and kept for the next start."""
        util.settings().setValue('player/output', name)
        self._output_tooltip()
        self.visuals.amp.set_output(self.player.output)
        for mic in self._mics():
            mic.set_output(self.player.output)
        self.call_audio.set_output(self.player.output)
        self.app_audio.set_output(self.player.output)
        self._restore_output_volume()
        self.next_up.setText(_html(why))          # where "Next: ..." is, for a few seconds
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(8000, lambda: self._show_next_up(self.queue.peek(1)))
        log.info(f'[player] {why}')

    def _amp_outputs(self):
        """The amplifier's output selector: the outputs the Output device menu lists, the one played on. Asked for
        later, from the event loop (not while the output is being switched, nor while starting or showing), once
        for a burst of asks, and only while the amplifier is shown (it asks again when shown)."""
        if getattr(self, '_amp_outputs_pending', False):
            return
        self._amp_outputs_pending = True
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(200, self._fill_amp_outputs)

    def _fill_amp_outputs(self):
        self._amp_outputs_pending = False
        amp = self.visuals.amp
        if amp.isVisible():
            devices = self.player.output_devices()
            names, unavailable, kinds, others = self._amp_positions(devices)
            amp.set_outputs(names, self.player.output, unavailable, others)
            icons = self._output_icons(devices)
            for name in unavailable:        # (a place kept: its icon as when it was there)
                icons[name] = kinds[name]
            icons[None] = 'default'         # the system's default: an icon of its own (which device: its tooltip)
            from cutemusic.amp import OTHER_OUTPUTS
            icons[OTHER_OUTPUTS] = 'more'
            amp.set_output_icons(icons)
            if sys.platform == 'darwin':
                from cutemusic import taps
                amp.default_output_name = taps.output_kinds().get(None)

    # the amplifier's OUTPUT, five positions: the system default (the selector adds it, its icon the circling arrow),
    # the computer's speakers, the headphones, a Bluetooth device -- their places kept when they are not there (grey),
    # so they never move -- and the other outputs: a menu of them (there may be many), the position the one of them
    # played on (else "Other outputs", three dots)
    _AMP_FIXED = (('laptop', 'speakers', 'Speakers'), ('headset', 'headphones', 'Headphones'),
                  ('bluetooth', 'bluetooth', 'Bluetooth'))

    def _amp_positions(self, devices):
        """([the four positions after the default], {the names not there now: their places kept}, {name: its kind},
        [the other outputs, by name: the last position's menu])."""
        if sys.platform == 'darwin':
            from cutemusic import taps
            kinds = taps.output_kinds()
            kinds.pop(None, None)
        else:
            kinds = self._output_kinds(devices)
        kinds = {name: kind for name, kind in kinds.items() if name in devices}
        settings = util.settings()
        fixed, unavailable = [], set()
        for kind, key, label in self._AMP_FIXED:
            there = sorted((name for name in devices if kinds.get(name) == kind), key=str.lower)
            if kind == 'headset':       # the computer's own (the jack) before a USB or wired headset
                there.sort(key=lambda name: 0 if re.search(r'external headphones|headphones', name, re.I) else 1)
            if there:
                name = there[0]
                settings.setValue(f'amp/{key}', name)       # its name for when it is not there
            else:
                name = settings.value(f'amp/{key}', label, type=str) or label
                if name in devices:         # (the remembered name taken by another kind of device)
                    name = label
                unavailable.add(name)
            kinds.setdefault(name, kind)
            fixed.append(name)
        rest = sorted((name for name in devices if name not in fixed), key=str.lower)
        from cutemusic.amp import OTHER_OUTPUTS
        last = self.player.output if self.player.output in rest else OTHER_OUTPUTS
        return fixed + [last], unavailable, kinds, rest

    @staticmethod
    def _output_icons(devices):
        """{output name (None the default): a line icon ('laptop', 'headset', 'bluetooth', 'wave'), or '@' and a
        letter for any other output: its first one, else the next of its name no other has (one letter each)}."""
        default = None
        if sys.platform == 'darwin':
            from cutemusic import taps
            kinds = taps.output_kinds()
            default = kinds.pop(None, None)
        else:
            kinds = MainWindow._output_kinds(devices)
        icons, taken = {}, set()
        for name in sorted(kinds, key=str.lower):
            if kinds[name] != 'speaker':
                icons[name] = kinds[name]
                continue
            letters = [char.upper() for char in name if char.isalnum()]
            letter = next((char for char in letters if char not in taken), None) or \
                next((char for char in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789' if char not in taken), '?')
            taken.add(letter)
            icons[name] = '@' + letter
        if default in icons:
            icons[None] = icons[default]
        return icons

    @staticmethod
    def _output_kinds(devices):
        """{output name: 'laptop', 'headset', 'bluetooth', 'wave' or 'speaker'} from the sound server's device
        properties (Linux; macOS: taps.output_kinds)."""
        kinds = {}
        for name, device in devices.items():
            properties = device.get_properties()
            get = (lambda key: properties.get_string(key) or '') if properties is not None else (lambda key: '')
            form, bus = get('device.form_factor'), get('device.bus')
            kinds[name] = ('bluetooth' if bus == 'bluetooth' else
                           'headset' if form in ('headset', 'headphone', 'hands-free') else
                           'laptop' if form == 'internal' else
                           'wave' if get('device.class') == 'filter' or get('factory.name') == 'support.null-audio-sink' else
                           'speaker')
        return kinds

    def _output_tooltip(self):
        name = self.player.output or 'System default'
        dim = self.palette().color(self.palette().ColorRole.PlaceholderText).name()
        shown = self.device_name.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, self.device_name.width() - 4)
        self.device_name.setText(f'<a href="devices" style="color: {dim}; text-decoration: none">{_html(shown)}</a>')
        self.device_name.setToolTip(f'Output device: {name} (click: another one)')

    def _started(self, uri):
        # a track began: the gapless next one moves the queue on
        path = QUrl(uri).toLocalFile()
        current = self.queue.current
        if current is not None and current.path == path:
            return
        following = self.queue.peek(1)
        if following is not None and following.path == path:
            self.queue.move(1)
            self._update_track(self.queue.current)
            self._prepare_next()
            self._played(self.queue.current)

    def _finished(self):
        # the end of the last track queued (the next ones go gapless): repeat or nothing more
        track = self.queue.move(1)
        if track is not None:
            self._play_current()
        else:       # the queue ended: the last track stays, greyed (Space plays it again), the bar as tall as ever
            self._update_track(self.queue.current, ended=True)

    def _error(self, text):
        track = self.queue.current
        log.warning(f'[player] {track.path if track else ""}: {text}')
        self.now_playing.setText(f'Cannot play {track.display_title if track else ""}: {text}')
        if self.queue.peek(1) is not None and self.queue.peek(1) is not track:
            self.queue.move(1)
            self._play_current()

    def toggle(self):
        log.info(f'[ui] play/pause ({self.player.state}, at {self._position_seconds:.1f} s)')
        if self.player.state == 'stopped':
            if self.queue.current is not None:
                self._play_current()
            else:
                self.browser._activated(self.browser.tree.currentItem() or self.browser.tree.topLevelItem(0))
        else:
            self.player.toggle()

    def stop(self):
        log.info('[ui] stop')
        self.player.stop()
        self._update_track(None)

    def next(self):
        log.info('[ui] next')
        if self.queue.move(1) is not None:
            self._play_current()
            self._follow_in_library()

    def previous(self):
        log.info(f'[ui] previous (at {self._position_seconds:.1f} s)')
        if self._position_seconds > 3 or self.queue.peek(-1) is None:
            self._seek(0)
        elif self.queue.move(-1) is not None:
            self._play_current()
            self._follow_in_library()

    def _follow_in_library(self):
        """Next or Previous by hand: the selection in the library moves to the track now playing (its album opened,
        it in view), the tab, the focus and the filters left as they are; not there (filtered out), nothing moves."""
        track = self.queue.current
        if track is not None:
            (self.negatives_browser if self._is_negative(track) else self.browser).select_track(track)

    def _skip(self, seconds):
        self._seek(self._position_seconds + seconds)

    def _seek(self, seconds):
        self.player.seek(seconds)
        self._position_seconds = max(0.0, seconds)
        if self.mpris is not None:
            self.mpris.seeked()

    _position_seconds = 0.0

    def _position(self, seconds, duration):
        self._position_seconds = seconds
        if duration:
            self._duration = duration
            current = self.queue.current
            if current is not None and not current.get('duration'):
                current['duration'] = duration      # the tags had none
        if time.monotonic() - self._saved_at > 10:
            self._save_queue()
        self.visuals.set_position(seconds, self._duration)
        if self.dock.isVisible():
            self.dock.set_position(seconds, self._duration)
        if not self._seeking:
            self.seek_slider.setRange(0, int(self._duration))
            self.seek_slider.setValue(int(seconds))
        self.elapsed.setText(_duration(seconds))
        self.total.setText(_duration(self._duration))

    def _seek_released(self):
        self._seeking = False
        self._seek(self.seek_slider.value())

    def _seek_action(self, action):
        if action != QSlider.SliderAction.SliderMove:        # a click on the groove, the keys
            self._seek(self.seek_slider.sliderPosition())

    def _list_font_changed(self, font):
        """A− A+ in the library: the queue and the history in the same size."""
        self.queue_view.tree.setFont(font)
        self.queue_view.refresh(self.queue)
        self.history_view.tree.setFont(font)
        self.history_view.refresh()
        if hasattr(self, 'playlist_view'):
            self.playlist_view.tree.setFont(font)
            self.playlist_view.refresh()

    def _hang_up(self, target):
        """HANG UP: silence to Blink at once, whatever Blink does (a call it does not end, a request it does not get),
        till that call is gone from its list (_watch_blink); then asked of Blink."""
        self._hung_up.add((target.get('pid'), target.get('id')))
        self._set_send_cut(True)
        self._blink_request(target, 'hangup')

    def _set_send_cut(self, cut):
        if cut == self.link_sender.cut:
            return
        self.link_sender.cut = cut
        self.visuals.mixer.set_send_cut(cut)
        log.info(f'[mixer] {"HANG UP: nothing sent to Blink" if cut else "the call hung up is gone: sending to Blink again"}')

    # Auto-redial: a call the other side hung up (or that did not go through while redialing) called again after
    # REDIAL_DELAY, again and again, until a call is hung up here (in Blink, or with the call knob's OFF)

    def _watch_call_ends(self, states):
        """How the Blinks' last calls ended ('ended': {"seq", "at", "by", "address"}; an older Blink tells none)."""
        from cutemusic.mixer import REDIAL_DELAY
        mixer = self.visuals.mixer
        for state, channel in states:
            ended, pid = state.get('ended'), state.get('pid')
            if not isinstance(ended, dict) or pid is None:
                continue
            seen = self._ended_seen.get(pid)
            self._ended_seen[pid] = ended.get('seq')
            if seen == ended.get('seq'):
                continue
            if seen is None and time.time() - float(ended.get('at') or 0) > 15:      # (ended before we looked)
                continue
            by, address = ended.get('by'), str(ended.get('address') or '')
            if by == 'local':
                self._cancel_auto_redial(f'the call with {address or "?"} was hung up here')
            elif by in ('remote', 'failed') and mixer.auto_redial and mixer.powered and address:
                self._auto_redial = {'pid': pid, 'channel': channel, 'address': address,
                                     'at': time.monotonic() + REDIAL_DELAY}
                log.info(f'[mixer] the call with {address} {"was hung up there" if by == "remote" else "did not go through"}: '
                         f'auto-redial in {REDIAL_DELAY:.0f} s')
                QTimer.singleShot(int(REDIAL_DELAY * 1000) + 50, self._auto_redial_now)
        mixer.set_auto_redial_at(self._auto_redial['at'] if self._auto_redial else None)

    def _auto_redial_now(self):
        pending, mixer = self._auto_redial, self.visuals.mixer
        if pending is None or time.monotonic() < pending['at'] - 0.2:      # (cancelled, or made again since)
            return
        self._auto_redial = None
        mixer.set_auto_redial_at(None)
        if not mixer.auto_redial or not mixer.powered or not mixer.blink:
            return
        if mixer.blink_calls:       # a call is there again (answered, or called by hand): nothing to do
            log.info('[mixer] auto-redial: a call is there already')
            return
        log.info(f'[mixer] auto-redial: calling {pending["address"]} again')
        self._blink_request(pending, 'call')

    def _cancel_auto_redial(self, why):
        if self._auto_redial is not None:
            log.info(f'[mixer] auto-redial stopped: {why}')
        self._auto_redial = None
        self.visuals.mixer.set_auto_redial_at(None)

    def _blink_request(self, target, op):
        """HANG UP or REDIAL in the BLINK strip: asked of the Blink that has the call (target: its pid and the call's
        id) on the control channel (the driver's 'agrq' on macOS, D-Bus on Linux)."""
        from cutemusic import control
        request = {'seq': int(time.time() * 1000), 'pid': target.get('pid'), 'op': op}
        if op == 'hangup':
            request['id'] = target.get('id')
        elif op == 'call':
            request['address'] = target.get('address')
        elif op == 'mute':      # a room's participant: its MUTE in the BLINK strip
            request.update(id=target.get('id'), participant=target.get('participant'), muted=bool(target.get('muted')))
        done = control.request(json.dumps(request))
        log.info(f'[mixer] Blink asked to {op}' + ('' if done else ': failed'))
        QTimer.singleShot(300, self._watch_blink)       # the strip follows soon, not at the next 2 s

    def show_mixer(self):
        """The main window in front on its Mixer view (Blink's notice, --mixer): undocked, unminimized."""
        if self.full_screen:
            self.toggle_full_screen()
        if self.dock.isVisible():
            self.undock()
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        self.visuals.show_page(next(i for i, (key, label) in enumerate(PAGES) if key == 'mixer'))

    # The AG Podcast Mixer driver (macOS, driver.py)

    def _update_driver_action(self):
        from cutemusic import driver
        state = driver.state()
        text = {'missing': 'Install AG Podcast Mixer driver...',
                'older': f'Update AG Podcast Mixer driver ({driver.installed_version()} to {driver.bundled_version()})...',
                'installed': f'Remove AG Podcast Mixer driver ({driver.installed_version()})...',
                'unavailable': 'Install AG Podcast Mixer driver (not in this build)'}.get(state)
        self.driver_action.setText(text or 'AG Podcast Mixer driver')
        self.driver_action.setEnabled(state in ('missing', 'older', 'installed'))

    def _driver_action(self):
        from cutemusic import driver
        state = driver.state()
        if state in ('missing', 'older'):
            self._install_driver()
        elif state == 'installed':
            answer = QMessageBox.question(self, 'AG Podcast Mixer driver',
                                          'Remove the AG Podcast Mixer driver?\n\nBlink then no longer has the mixer as '
                                          'its microphone (without the driver, only through the older link, not '
                                          'in Blink Pro). The sound stops for a moment on all devices while macOS '
                                          'restarts its audio.')
            if answer == QMessageBox.StandardButton.Yes:
                driver.uninstall(lambda error: self._driver_done('removed', error))

    def _install_driver(self):
        from cutemusic import driver
        driver.install(lambda error: self._driver_done('installed', error))

    def _offer_driver(self):
        """At start, once per driver version we carry: the install (or update) offered."""
        from cutemusic import driver
        if not driver.offer_at_start():
            return
        update = driver.state() == 'older'
        box = QMessageBox(QMessageBox.Icon.Question, 'AG Podcast Mixer driver',
                          (f'A newer AG Podcast Mixer driver comes with this AG CuteMusic ({driver.bundled_version()}; '
                           f'installed: {driver.installed_version()}).' if update else
                           'The AG Podcast Mixer driver makes the mixer a microphone for Blink, Blink Pro included.') +
                          '\n\nmacOS asks for the administrator\'s password, and the sound stops for a moment on '
                          'all devices while it restarts its audio. Later: Play -> Install AG Podcast Mixer driver.',
                          parent=self)
        install = box.addButton('Update' if update else 'Install', QMessageBox.ButtonRole.AcceptRole)
        box.addButton('Not now', QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is install:
            self._install_driver()

    def _driver_done(self, what, error):
        if error == '':
            return          # cancelled at the password
        if error:
            QMessageBox.warning(self, 'AG Podcast Mixer driver', f'Not {what}: {error}')
            return
        # coreaudiod restarted: the link again (on the Feed when the driver is there) once it is up,
        # then what happened said
        self._relink_tries = 10
        self._driver_report = what
        QTimer.singleShot(1000, self._relink)

    def _report_driver(self):
        """After an install or a removal: what it did, and what to do next."""
        from cutemusic import driver
        what, self._driver_report = getattr(self, '_driver_report', None), None
        if what is None:
            return
        if what == 'removed':
            text = 'The AG Podcast Mixer driver is removed.'
        elif driver.feed_device() is None:
            text = (f'The AG Podcast Mixer driver {driver.installed_version()} is installed, but macOS has not loaded it '
                    f'yet: restart the Mac if Blink does not list "AG Podcast Mixer".')
        else:       # the device names, nothing more
            text = (f'AG Podcast Mixer driver {driver.installed_version()} installed.\n\n'
                    f'Blink: AG Podcast Mixer\nPodcasting and streaming apps: AG Podcast Streaming')
        QMessageBox.information(self, 'AG Podcast Mixer driver', text)

    def _relink(self):
        """After the driver was installed, updated or removed: the link built again, on the Feed when the driver is
        there (waiting up to 10 s for coreaudiod to load it)."""
        from cutemusic import driver
        if not self.visuals.mixer.powered:
            self._report_driver()
            return
        if driver.feed_device() is None and driver.state() in ('installed', 'older') and self._relink_tries > 0:
            self._relink_tries -= 1
            QTimer.singleShot(1000, self._relink)
            return
        self._stop_link()
        self._start_link()
        self._apply_share()
        log.info(f'[link] {self._link_kind()} from now on')
        self._report_driver()

    def _link_kind(self):
        """Where the mix goes to Blink, for the log."""
        return 'on the AG Podcast Mixer driver' if self.link_devices.feed is not None else \
            f'on the PipeWire node {self.link_devices.node}' if self.link_devices.node is not None else \
            'nowhere (POWER off, or no device: see [link])'

    def _start_link(self):
        """The link (the driver's Feed, the PipeWire node), and the mix sent through it
        (the mic always, the music with TO CALLS)."""
        from cutemusic.share import pipewire_nodes
        error = self.link_devices.start(pipewire=pipewire_nodes())
        self.visuals.mixer.set_share_available(self.player.available and self.link_devices.active)
        if error:
            log.warning(f'[link] the mixer is not Blink\'s device through the link: {error}')
            return
        if self.link_sender.start(self.link_devices.feed, self.link_devices.node):
            for mic in self._mics():
                mic.set_link(self.link_sender)

    def _stop_link(self):
        for mic in self._mics():
            mic.set_link(None)
        self.player.set_link(None)
        if self.call_audio.link_source is not None:
            self.call_audio.set_link_source(None)
        self.link_sender.stop()
        self.link_devices.stop()

    def _apply_share(self):
        """Each TO CALLS: the Local Library through the link (its own TO CALLS); the app AUDIO APPS through the link
        (its TO CALLS); off: not."""
        mixer = self.visuals.mixer
        linked = self.link_sender.pipeline is not None
        library = mixer.library_sharing
        self.player.set_link(self.link_sender if library and linked else None)
        if hasattr(self, 'app_audio'):
            app = getattr(self, '_capture', None) is not None
            self.app_audio.set_link(self.link_sender if mixer.sharing and app and linked else None)

    def _apps_fader(self, value):
        """AUDIO APPS' fader moved (0..100): a player app's own volume (Spotify, Music: what is heard of it, through
        the mixer or THRU, and so what goes into a call); another app's level in the mixer (kept: mixer/app_level)."""
        from cutemusic import taps
        capture = getattr(self, '_capture', None)
        if capture is not None and capture.bundle_id in taps.PLAYERS:
            self._player_volume = value
            self._player_volume_at = time.monotonic()
            if not self._player_volume_timer.isActive():         # (a drag: sent at most every 100 ms)
                self._player_volume_timer.start()
        else:
            self.app_audio.set_level(value / 100)
            util.settings().setValue('mixer/app_level', value / 100)

    def _send_player_volume(self):
        from cutemusic import taps
        capture = getattr(self, '_capture', None)
        if capture is not None and capture.bundle_id in taps.PLAYERS and self._player_volume is not None:
            taps.set_volume(capture.bundle_id, self._player_volume)

    def _unstick_music(self):
        """MUSIC muted 1 s and back, as by hand (a workaround: see _music_source).

        Off now (the cause found: two outputs on one device; mic.py's _Output plays them as one). Kept a while to
        compare: AG_CUTEMUSIC_UNSTICK picks what is muted: 'both' (as by hand), 'player' (only the paused player's
        playbin), 'app' (only the capture's volume); unset or 'off': nothing."""
        if self._capture is None or self.player.muted:
            return
        which = os.environ.get('AG_CUTEMUSIC_UNSTICK', 'off')
        if which == 'off':
            return
        log.info(f'[mixer] {"MUSIC" if which == "both" else "the " + which} muted 1 s and back (the distortion workaround, {which})')
        if which == 'player':
            self.player.set_muted(True)
            QTimer.singleShot(1000, lambda: self.player.set_muted(False))
        elif which == 'app':
            self.app_audio.set_mute(True)
            QTimer.singleShot(1000, lambda: self.app_audio.set_mute(False))
        else:
            self._set_muted(True)
            QTimer.singleShot(1000, lambda: self._set_muted(False))

    def _thru_active(self):
        """The app left as it plays, on its own output (THRU): always without a Blink call (THRU hidden: the fader
        drives a player's own volume, nothing goes anywhere else); in a call, as THRU is chosen."""
        return self.visuals.mixer.app_output or not getattr(self, '_in_call', False)

    def _source_turned(self, bundle_id):
        """SOURCE turned by hand: kept for the next start, the new source captured and heard here as usual, but not
        sent into a call (TO CALLS off) until the user turns TO CALLS on."""
        util.settings().setValue('mixer/source', bundle_id)
        mixer = self.visuals.mixer
        if mixer.sharing:
            mixer.set_share(False)
            util.settings().setValue('mixer/to_calls', False)
        self._music_source(bundle_id)       # (_apply_share: the music no longer sent)
        log.info('[mixer] new MUSIC source: heard here, not sent to calls (TO CALLS to send it)')

    def _music_source(self, bundle_id):
        """MUSIC's SOURCE turned: an app captured (taps.py; the player paused), or the player again ('').
        THRU: the app on its own output too (not then also on ours: heard once), else only through the mixer."""
        from cutemusic import taps
        mixer = self.visuals.mixer
        if self._capture is not None:
            self.app_audio.set_device(None)
            self._capture.close()
            self._capture = None
        if bundle_id and mixer.powered:
            try:
                self._capture = taps.Capture(bundle_id, mute=not self._thru_active())
            except Exception as e:
                log.warning(f'[taps] {bundle_id}: {e}')
                mixer.reset_source()
                QMessageBox.warning(self, 'Music source', f'Cannot capture {mixer.source_selector.title(bundle_id)}: {e}')
            else:
                if self._in_call:
                    mixer.set_app_output(not self._capture.mute)    # (the lamp, shown in a call, as it is)
                self.app_audio.set_monitor(self._capture.mute)          # the mixer's copy heard: the app silent there
                self.app_audio.set_device(self._capture.device_id, self._capture.name)
                log.info(f'[mixer] THRU {"on: " + self._capture.name + " plays on its own output, the mixer only sends it" if not self._capture.mute else "off: " + self._capture.name + " heard only through the mixer"}')
                # an app's capture started while the mic runs sounded distorted until MUTE was pressed off and on:
                # the same, by itself, once it runs (the cause not found yet; Mic.kick, the capture's volume alone,
                # was not enough: the whole MUSIC mute, the paused player's too, as the hand does it)
                QTimer.singleShot(2500, self._unstick_music)
        self._apply_share()
        self._update_source_track()
        self._apply_transport()
        # MUSIC's fader: a player app's own volume (set when it is read: _update_source_track), another app's level,
        # or the Local Library's volume again
        from cutemusic import taps
        player = self._capture is not None and self._capture.bundle_id in taps.PLAYERS
        mixer.set_app_volume_control(player)
        if player:
            self.app_audio.set_level(1.0)           # (its own volume does it, heard and sent alike)
        else:
            self.app_audio.set_level(util.settings().value('mixer/app_level', 1.0, type=float))
            mixer.set_apps_level(round(self.app_audio.level * 100))
        log.info(f'[mixer] AUDIO APPS: {self._capture.name if self._capture else "Off"}')

    def _update_source_track(self):
        """What MUSIC plays, shown under SOURCE: an app's tune when it tells it (taps.now_playing: Spotify, Music),
        else the Local Library's song (playing or paused)."""
        from cutemusic import taps
        mixer = self.visuals.mixer
        track, playing = None, False
        if self._capture is not None:
            track = taps.now_playing(self._capture.bundle_id)
            self._update_remote(track)
            # the player's own volume on the fader (changed in the app too), unless the fader just moved it
            if track and track[3] is not None and time.monotonic() - self._player_volume_at > 1.5:
                mixer.set_apps_level(track[3])
            # playing: as the player says; an app that does not tell (a browser): captured, so taken as playing
            playing = bool(track[2]) if track else self._capture.bundle_id not in taps.PLAYERS
            track = track[:2] if track else None
        mixer.set_source_track(track, playing)
        # NOW PLAYING only while it plays; PAUSED paused; nothing once stopped (a tune that ended, the queue done)
        song = self.queue.current if self.player.state in ('playing', 'paused') else None
        mixer.set_library_track((song.display_title, song.get('artist') or '') if song is not None else None,
                                self.player.state == 'playing')

    def _refresh_sources(self):
        """MUSIC's SOURCE positions: the apps that play sound now (taps.py), while the mixer is shown."""
        from cutemusic import taps
        mixer = self.visuals.mixer
        if not mixer.sources_available:
            return
        try:
            found = taps.apps()
        except Exception as e:
            log.warning(f'[taps] cannot list the apps: {e}')
            return
        # offered: the apps playing now (not every app that once made a sound: Terminal's bell, notifications), and
        # the one MUSIC plays from, playing or not
        offered = [app for app in found if app.playing or app.bundle_id == mixer.source()]
        # logged when they change: each app, its bundle id, its processes (a browser's helpers)
        summary = '; '.join(f'{app.name} ({app.bundle_id}, {len(app.objects)} process{"es" if len(app.objects) > 1 else ""}'
                            f'{"" if app.playing else ", not playing"})' for app in offered) or 'no app playing'
        summary += f' ({len(found) - len(offered)} other apps silent)' if len(found) > len(offered) else ''
        if summary != getattr(self, '_sources_logged', None):
            self._sources_logged = summary
            log.info(f'[taps] MUSIC sources: {summary}')
        mixer.set_sources([(app.bundle_id, app.name) for app in offered], mixer.source())
        self._source_icons([(app.bundle_id, app.name) for app in offered])

    # avatars that say better who it is than the first letter (a name's start, any case: its letter): none for now
    # (Microsoft Teams Audio is M, the new Teams' "Teams audio" T: their first letters)
    AVATARS = ()

    @classmethod
    def _avatar(cls, name):
        """'@' and the first letter of name (an avatar, as a contact's without a picture: S Spotify, B BlackHole;
        AVATARS)."""
        lowered = name.strip().lstrip('\u200e\u200f').lower()
        for start, letters in cls.AVATARS:
            if lowered.startswith(start):
                return '@' + letters
        letter = next((char for char in name if char.isalnum()), '?')      # (WhatsApp's name starts with a mark)
        return '@' + letter.upper()

    def _source_icons(self, apps):
        """Around SOURCE: the Local Library a music note, each app an avatar of its first letter, as a contact
        without a picture ([(bundle id, name)])."""
        icons = {None: 'genre'}
        for bundle_id, name in apps:
            icons[bundle_id] = self._avatar(name)
        self.visuals.mixer.set_source_icons(icons)

    def _input_icons(self):
        """Around MICROPHONE's INPUT: Off written; a microphone its kind (taps.input_kinds: built in, Bluetooth,
        plugged in); a virtual input (BlackHole, Teams, an app's) an avatar of its first letter."""
        from cutemusic import taps
        icons = {None: '@Off'}         # (an avatar as the others)
        kinds = taps.input_kinds() if sys.platform == 'darwin' else self.mic.input_kinds()     # (Linux: GStreamer's)
        for name, kind in kinds.items():
            icons[name] = self._avatar(name) if kind == 'wave' else kind
        self.visuals.mixer.set_input_icons(icons)

    def _watch_listeners(self, blink_pids):
        """The other apps reading "AG Podcast Mixer" and "AG Podcast Streaming" (control.listeners): ON AIR with them.
        CoreAudio answers at once; pw-dump (Linux) in a thread."""
        from cutemusic import control
        mixer = self.visuals.mixer
        if sys.platform == 'darwin':
            mixer.set_listeners(control.listeners(blink_pids))
            return
        if self._listeners_busy:
            return
        self._listeners_busy = True

        def done(found):
            self._listeners_busy = False
            if self._blink_timer.isActive():        # (POWER off meanwhile: none)
                mixer.set_listeners(found)

        def read():
            util.call_in_gui_thread(done, control.listeners(blink_pids))
        import threading
        threading.Thread(target=read, name='listeners', daemon=True).start()

    @staticmethod
    def _shape(blinks):
        """The Blinks' entries as they are but for the participants' levels (those move every 0.3 s in a room)."""
        def plain(value):
            if isinstance(value, dict):
                return {key: plain(item) for key, item in value.items() if key != 'level'}
            if isinstance(value, list):
                return [plain(item) for item in value]
            return value
        return json.dumps(plain(blinks), sort_keys=True)

    def _blink_control(self):
        """A Blink set its entry: the room participants' meters at once; the rest (calls, redial, devices) only
        when more than the levels changed (_watch_blink: also every 2 s)."""
        from cutemusic import control
        from cutemusic.share import calls_of
        blinks = control.blinks() if self.link_devices.active else {}
        if self._shape(blinks) != self._blink_shape:
            self._watch_blink()
            return
        calls = [call for state in blinks.values() for call in calls_of(state, 'control')]
        self.visuals.mixer.set_blink_participants(self._participants_of(calls))

    @staticmethod
    def _participants_of(calls):
        """The rooms' participants, for their meters and MUTE in the BLINK strip: [{"key", "name", "level",
        "channels", "muted", "target"}], target what MUTE asks ({"pid", "id", "channel", "participant"})."""
        found = []
        for title, detail, held, quality, target in calls:
            for participant in (target or {}).get('participants') or []:
                found.append({'key': (target.get('pid'), target.get('id'), str(participant['id'])),
                              'name': str(participant.get('name') or ''), 'level': participant.get('level'), 'levels': participant.get('levels'),
                              'channels': participant.get('channels'), 'muted': bool(participant.get('muted')),
                              'target': {'pid': target.get('pid'), 'id': target.get('id'), 'channel': target.get('channel'),
                                         'participant': participant['id']}})
        return found

    def _watch_blink(self):
        """Blink started or quit: the mixer's BLINK strip shown and heard, or not; who is in its call, on the strip.
        (Also MUSIC's SOURCE positions, while the mixer is shown.)"""
        if self.visuals.mixer.isVisible():
            self._refresh_sources()
            self._update_source_track()
        from cutemusic import control
        from cutemusic.share import calls_of
        # the Blinks on the control channel: the driver's properties (macOS), D-Bus (Linux)
        device = control.control_device()
        if device != (self._blink_listener.device if self._blink_listener is not None else None):
            if self._blink_listener is not None:
                self._blink_listener.remove()
            self._blink_listener = control.PropertyListener(device, control.BLINK_CALLS, self.blink_control_changed.emit) \
                if device is not None else None
        blinks = control.blinks() if self.link_devices.active else {}
        self._blink_shape = self._shape(blinks)
        states = [(state, 'control') for state in blinks.values()]
        # Sylk Desktop (Electron: only listed devices): "AG Podcast Mixer" listed while it runs, its sound on the strip
        sylk = control.sylk_running() if self.link_devices.feed is not None else False
        if sylk != self._sylk_visible:
            if control.set_mixer_visible(sylk):
                log.info(f'[devices] Sylk Desktop {"runs: AG Podcast Mixer listed for it" if sylk else "is gone: AG Podcast Mixer hidden again"}')
            elif sylk:
                log.warning('[devices] Sylk Desktop runs, but the driver cannot list AG Podcast Mixer (older than 1.3.0?)')
            self._sylk_visible = sylk
        running = self.link_devices.active and (bool(blinks) or sylk)
        calls = [call for state, channel in states for call in calls_of(state, channel)] if running else []
        calls.sort(key=lambda call: call[2])        # the ones on hold last
        redial = next(({'pid': state.get('pid'), 'channel': channel,       # (as a call: its name if any, else its address)
                        'label': str(state['redial'].get('name') or state['redial'].get('address') or '')}
                       for state, channel in states if isinstance(state.get('redial'), dict) and state.get('pid') is not None),
                      None) if running else None
        mixer = self.visuals.mixer
        mixer.set_blink_redial(redial)
        self._watch_call_ends(states if running else [])
        # the sound devices chosen in Blink (a Blink that tells them: 'devices'): its mic the mixer, or not
        devices = next((state['devices'] for state, channel in states if isinstance(state.get('devices'), dict)), None) \
            if running else None
        if devices != mixer.blink_devices:
            log.info(f'[mixer] Blink\'s sound devices: {devices}' if devices is not None else '[mixer] Blink\'s sound devices: not told')
        mixer.set_blink_devices(devices)
        if self._hung_up:       # sending again once every call HANG UP was pressed for is gone (or Blink with it)
            listed = {(call[4].get('pid'), call[4].get('id')) for call in calls if call[4]}
            self._hung_up &= listed
            if not self._hung_up:
                self._set_send_cut(False)
        if bool(calls) != bool(mixer.blink_calls):       # KARAOKE off during a call (the echo, VOCAL CUT), as it was after
            mixer.set_blink_calls(calls)
            self._apply_karaoke_audio()
        mixer.set_blink_calls(calls)
        mixer.set_blink_participants(self._participants_of(calls))
        self._watch_listeners(set(blinks))
        in_call = any(not held for title, detail, held, quality, target in calls)
        if in_call != self._in_call:        # a call: not hearing oneself (TO AMPLIFIER off); after it, as it was
            self._in_call = in_call
            monitor = False if in_call else self._monitor_resting()     # after it: as before it
            self._set_monitor(monitor)
            log.info(f'[mixer] Blink {"in a call: the mic only to Blink" if in_call else "call over: the mic monitored" if monitor else "call over"}')
            if self._capture is not None and not mixer.app_output:      # taken over in a call, as it is without one
                self._music_source(self._capture.bundle_id)
        # what Blink plays: the Feed's input (macOS, the driver), the call sink's monitor (Linux, PipeWire)
        want = None
        if running:
            want = 'driver' if self.link_devices.feed is not None else 'pulse' if self.link_devices.node is not None else None
        feed_receiver = getattr(self, '_feed_receiver', None)
        have = 'pulse' if self.call_audio.pulse_source == f'{self._call_sink}.monitor' else \
            'other' if self.call_audio.pulse_source else \
            'driver' if feed_receiver is not None and self.call_audio.link_source is feed_receiver else None
        if want == have:
            return
        log.info(f'[devices] Blink {"runs: its strip in the mixer (" + want + ")" if want else "is gone: its strip hidden"}')
        if want == 'driver':        # the Feed's input, hidden: read through CoreAudio by its id (hal.py)
            from cutemusic.hal import FeedReceiver
            self.call_audio.stereo = True
            self._feed_receiver = FeedReceiver(self.link_devices.feed)
            self.call_audio.set_link_source(self._feed_receiver, 'Blink')
        elif want == 'pulse':
            self.call_audio.set_pulse_source(f'{self._call_sink}.monitor', 'Blink')
        elif have == 'driver':
            self.call_audio.set_link_source(None)
            self._feed_receiver = None
        else:
            self.call_audio.set_pulse_source(None)
        self.visuals.mixer.set_blink(want is not None)

    OFF_AIR_GRACE = 15.0        # s off air before an auto-recording stops (a call dropped and redialed: one file)

    def _record_by_hand(self, on):
        """REC pressed: as asked; stopped while on air, auto-record waits for the next time on air."""
        if not on and self.visuals.mixer.on_air():
            self._auto_record_held = True
        self._auto_recording = False
        self._record(on)

    def _watch_on_air(self):
        mixer = self.visuals.mixer
        on_air = mixer.powered and mixer.on_air()
        now = time.monotonic()
        if on_air:
            self._off_air_since = None
            if mixer.auto_record and not self._auto_record_held and not self.recorder.recording:
                log.info(f'[recorder] on air ({", ".join(mixer._on_air_with()) or "?"}): recording by itself (auto-record)')
                self._record(True)
                self._auto_recording = self.recorder.recording
            return
        self._auto_record_held = False
        if not (self._auto_recording and self.recorder.recording):
            self._auto_recording = False
            return
        if self._off_air_since is None:
            self._off_air_since = now
        elif now - self._off_air_since >= self.OFF_AIR_GRACE:
            log.info(f'[recorder] off air for {self.OFF_AIR_GRACE:.0f} s: the auto-recording stops')
            self._auto_recording = False
            self._off_air_since = None
            self._record(False)

    def _record(self, on):
        """RECORDER's REC: everything the strips play into one file in the music folder, then tagged and in the Local
        Library. In a Blink call: Blink Calls, by the party's SIP URI; else My Memos, by the Mac's user name; titled
        with when it started."""
        from cutemusic import recorder as _recorder
        mixer = self.visuals.mixer
        if not on:
            self._stop_recording()
            return
        if self.recorder.recording:
            return
        import datetime
        import getpass
        call = next((call for call in mixer.blink_calls if not call[2] and call[4] and not call[4].get('state')), None) \
            or next((call for call in mixer.blink_calls if not call[2]), None)
        if call is not None:
            address = (call[4] or {}).get('address') or call[1] or call[0]
            genre, artist = 'Blink Calls', address.split(':', 1)[1] if address.startswith(('sip:', 'sips:')) else address
        else:
            try:
                user = getpass.getuser()
            except Exception:
                user = 'me'
            genre, artist = 'My Memos', user
        # the title typed on the tape's label before it recorded, else when it started
        title = getattr(self, '_next_title', None) or datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self._next_title = None
        suffix = _recorder.extension()
        folder = os.path.join(self.root or music_folder(), genre, _safe_name(artist))     # Blink Calls/alice@example.com/
        path = os.path.join(folder, _safe_name(title) + suffix)
        if not self.recorder.start(path):
            mixer.set_recording(False, f'Cannot record into {folder}')
            return
        self._recording = (path, title, artist, genre)
        self._markers = []
        mixer.set_recording(True, f'{genre} \u00b7 {artist}', title)

    def _add_marker(self):
        """MARK (or M): a marker at this moment of the recording, named by its number (a chapter in the file)."""
        seconds = self.recorder.position
        if seconds is None:
            return
        name = f'Marker {len(self._markers) + 1}'
        self._markers.append((seconds, name))
        self.visuals.mixer.set_markers(len(self._markers))
        log.info(f'[recorder] {name} at {seconds:.1f} s')

    def _marker_clicked(self, index):
        """A marker's tick clicked on the play bar: playing from there."""
        seconds, name = self.seek_slider.markers[index]
        log.info(f'[player] to {name} ({seconds:.1f} s)')
        self._seek(seconds)

    def _jump_marker(self, step):
        """[ or ]: to the previous marker (the one before where it plays: a second's grace, as Previous's 3) or the
        next one."""
        markers = self.seek_slider.markers
        if not markers or self.queue.current is None:
            return
        now = self._position_seconds
        if step > 0:
            target = next((seconds for seconds, name in markers if seconds > now + 0.5), None)
        else:
            target = next((seconds for seconds, name in reversed(markers) if seconds < now - 1.0), 0.0)
        if target is not None:
            self._seek(target)

    def _rename_marker(self, index):
        """Rename... on a marker's tick: its new name, written into the file's chapters."""
        from PyQt6.QtWidgets import QInputDialog
        from cutemusic import recorder as _recorder
        track = self.queue.current
        markers = list(self.seek_slider.markers)
        if track is None or not 0 <= index < len(markers):
            return
        seconds, name = markers[index]
        new, ok = QInputDialog.getText(self, 'Rename marker', f'Marker at {int(seconds) // 60}:{int(seconds) % 60:02d}:', text=name)
        new = new.strip()
        if not ok or not new or new == name:
            return
        markers[index] = (seconds, new)
        if _recorder.write_markers(track.path, markers):
            self.seek_slider.set_markers(markers)
            log.info(f'[player] {track.path}: marker {index + 1} renamed {new!r}')
        else:
            QMessageBox.warning(self, 'Rename marker', f'Cannot write the markers into {track.path}')

    def _show_recordings(self):
        """RECORDER's TAPE: the library shown with the recordings' genre ticked and the recording selected: the one
        recording now (once it is saved and scanned; till then its genre), else the last saved; neither, the genre
        the next one goes to (Blink Calls during a call, else My Memos)."""
        recording = self._recording or self._recorded
        if recording is not None:
            path, genre = recording[0], recording[3]
        else:
            calls = self.visuals.mixer.blink_calls
            path, genre = None, 'Blink Calls' if any(not call[2] for call in calls) else 'My Memos'
        self.set_library_shown(True)
        self.tabs.setCurrentWidget(self.browser)
        browser = self.browser
        if browser.filter.text():
            browser.filter.clear()
        if not browser.genres.select_only(genre):
            browser.status.setText(f'No {genre} recordings yet')
        track = next((track for track in self.local_tracks if track.path == path), None) if path else None
        if track is not None and not browser.select_track(track):
            browser.clear_filters()
            browser.select_track(track)
        browser.tree.setFocus()

    def _stop_recording(self):
        """The recording finished, tagged, scanned into the Local Library."""
        from cutemusic import recorder as _recorder
        recording = getattr(self, '_recording', None)
        path = self.recorder.stop()
        self._recording = None
        mixer = self.visuals.mixer
        if path is None or recording is None:
            mixer.set_recording(False)
            return
        path, title, artist, genre = recording
        path = self._renamed_path(path, title)         # its label edited while it recorded: that name
        _recorder.tag(path, title, artist, genre)
        markers, self._markers = self._markers, []
        if markers:
            _recorder.write_markers(path, markers, self.recorder.last_seconds)
        self._recorded = (path, title, artist, genre)
        log.info(f'[recorder] saved {path} ({genre}, {artist})')
        mixer.set_recording(False, f'Saved in {genre}: {title}', title)
        self.scanner.scan(self.root)        # in the Local Library

    def _set_streaming(self, on):
        """STREAMING turned: macOS, the driver adds "AG Podcast Streaming" (and the output the mix is played into: its
        Feed), the recorder's mix played there, off: both gone; Linux, the recorder's mix played into the PipeWire
        Streaming Feed (always there: AG Podcast Streaming silent while off)."""
        from cutemusic import control, driver
        mixer = self.visuals.mixer
        if not on:
            self.recorder.stop_streaming()
            control.set_streaming(False)
            log.info('[mixer] streaming off' + (': AG Podcast Streaming silent' if sys.platform.startswith('linux') else ': AG Podcast Streaming gone'))
            return
        if sys.platform.startswith('linux'):
            from cutemusic.share import STREAM_FEED, streaming_nodes
            if not mixer.powered or not streaming_nodes():
                if mixer.powered:
                    log.warning('[mixer] cannot stream: the PipeWire streaming nodes are not there (log in again)')
                mixer.set_streaming(False)
                return
            if self.recorder.start_streaming(node=STREAM_FEED):
                log.info('[mixer] streaming on: AG Podcast Streaming carries what the recorder hears')
            else:
                mixer.set_streaming(False)
            return
        if not mixer.powered or driver.feed_device() is None:
            mixer.set_streaming(False)
            return
        if not control.set_streaming(True):
            log.warning('[mixer] cannot turn streaming on (an older driver?)')
            mixer.set_streaming(False)
            return
        self._stream_tries = 0
        self._start_streaming()

    def _start_streaming(self):
        """The mix into the streaming Feed once macOS lists it (the driver adds it: a moment)."""
        from cutemusic import driver
        mixer = self.visuals.mixer
        if not mixer.streaming() or self.recorder.streaming:
            return
        device = driver.device_id(driver.STREAM_FEED_UID)
        if device is None:
            self._stream_tries += 1
            if self._stream_tries <= 20:
                QTimer.singleShot(150, self._start_streaming)
            else:
                log.warning('[mixer] AG Podcast Streaming did not appear')
                mixer.set_streaming(False)
            return
        if self.recorder.start_streaming(device):
            log.info('[mixer] streaming on: AG Podcast Streaming carries what the recorder hears')
        else:
            mixer.set_streaming(False)

    def _rename_recording(self, title):
        """The tape's label edited: the recording's title; recording, its file named so when it stops; saved, its
        file renamed and its title tag set now (the Local Library scanned again)."""
        from cutemusic import recorder as _recorder
        if self._recording is not None and self.recorder.recording:
            path, old, artist, genre = self._recording
            self._recording = (path, title, artist, genre)
            log.info(f'[recorder] the recording to be saved as {title!r}')
            return
        if self._recorded is None:          # nothing recorded yet: the next recording's title
            self._next_title = title
            log.info(f'[recorder] the next recording to be titled {title!r}')
            return
        path, old, artist, genre = self._recorded
        if not os.path.exists(path):
            log.warning(f'[recorder] cannot rename {path}: it is gone')
            return
        new = self._renamed_path(path, title)
        _recorder.tag(new, title, artist, genre)
        self._recorded = (new, title, artist, genre)
        self.visuals.mixer.set_recording(False, f'Saved in {genre}: {title}', title)
        self.scanner.scan(self.root)

    def _renamed_path(self, path, title):
        """path renamed after title (in its folder, its extension kept, another's name not taken); path itself
        when it is already so named or cannot be renamed."""
        folder, name = os.path.split(path)
        suffix = os.path.splitext(name)[1]
        stem = _safe_name(title)
        new, count = os.path.join(folder, stem + suffix), 2
        if new == path:
            return path
        while os.path.exists(new):
            new, count = os.path.join(folder, f'{stem} ({count}){suffix}'), count + 1
        try:
            os.replace(path, new)
        except OSError as e:
            log.warning(f'[recorder] cannot rename {path} to {new}: {e}')
            return path
        log.info(f'[recorder] renamed {path} to {new}')
        return new

    def _mics(self):
        """The input strips' Mics: the first's, then the others' (mixer.py: + and -)."""
        return [self.mic] + list(self.slot_mics.values())

    def _slot_added(self, key, start=True):
        """+ (or a strip kept from the last time): its Mic, made as the first's (output, link, monitor, echo),
        its device played once chosen (start: now; else at _start_slots, once the microphone permission is there)."""
        from cutemusic.mic import Mic
        mixer = self.visuals.mixer
        slot = mixer._slot(key)
        if slot is None or key in self.slot_mics:
            return
        mic = Mic(self)
        mic.buffer = self.mic.buffer
        mic.link_name = f'mic-{key}'        # its own input in the LinkSender's mix (and the recorder's)
        mic.set_recorder(self.recorder)
        mic.set_output(self.player.output)
        mic.set_level(slot.fader.value)
        mic.set_echo(mixer.echo.value if mixer.karaoke_on() else 0.0)
        mic.set_monitor(self.mic.monitor)
        mic.set_mute(bool(mixer.muted.get(key)))
        sender = getattr(self, 'link_sender', None)     # (at start, not made yet: _start_link links them all)
        if sender is not None and sender.pipeline is not None:
            mic.set_link(sender)
        mic.levels.connect(lambda rms, key=key: mixer.set_slot_levels(key, rms))
        self._feed_amp_from(mic, key)
        self.slot_mics[key] = mic
        log.info(f'[mixer] {mixer.strip_title(key)} added')
        if start and slot.input:
            mic.set_input(slot.input)

    def _slot_removed(self, key):
        mic = self.slot_mics.pop(key, None)
        if mic is not None:
            mic.stop()
            mic.set_link(None)
            log.info(f'[mixer] an input strip removed ({mic.input or "Off"})')

    def _start_slots(self):
        """The other strips' devices played (at start, POWER on): those there now; one gone stays Off."""
        mixer = self.visuals.mixer
        if not mixer.powered:
            return
        devices = self.mic.input_devices()
        for slot in mixer.slots:
            mic = self.slot_mics.get(slot.key)
            if mic is None or not slot.input:
                continue
            if slot.input in devices:
                mic.set_input(slot.input)
            else:
                log.info(f'[mic] {mixer.strip_title(slot.key)}: {slot.input} is not there')

    def _default_mic(self):
        """INPUTS' device at start (and POWER on): the one last chosen (mixer/input) when it is there; Off when Off was
        chosen last, or that device is gone; the first time (nothing chosen yet), on macOS, the built-in microphone
        (taps.builtin_microphone). Once the microphone permission is given."""
        from cutemusic import taps
        from cutemusic.mic import microphone_access
        if self.mic.input or not self.visuals.mixer.powered:
            return
        saved = util.settings().value('mixer/input', None, type=str)       # None: never chosen; '': Off
        if saved == '':
            log.info('[mic] INPUTS off, as last chosen')
            if microphone_access(self._start_slots):
                self._start_slots()
            return

        def choose():
            if self.mic.input:
                return
            self.mic.forget_devices()
            devices = self.mic.input_devices()
            name = saved if saved is not None else taps.builtin_microphone()
            self._start_slots()         # the other strips' devices, whatever the first's
            if not name or name not in devices:
                log.info(f'[mic] INPUTS off: {saved + " is not there" if saved else "no built-in microphone"} '
                         f'({", ".join(devices) or "no inputs"})')
                return
            log.info(f'[mic] {name}: {"as last chosen" if saved is not None else "the built-in microphone, the first time"}')
            self.mic.set_input(name)
            self.visuals.mixer.set_inputs(sorted(devices, key=str.lower), name)
        if microphone_access(choose):
            choose()

    def _default_source(self):
        """MUSIC's source as last chosen (mixer/source), when that app is running and has used sound; else the
        player (the choice kept for the next start)."""
        from cutemusic import taps
        mixer = self.visuals.mixer
        saved = util.settings().value('mixer/source', '', type=str)
        if not saved or mixer.source() or not mixer.powered or not mixer.sources_available:
            return
        try:
            apps = [(app.bundle_id, app.name) for app in taps.apps() if app.playing]
        except Exception as e:
            log.warning(f'[taps] cannot list the apps: {e}')
            return
        if saved not in dict(apps):
            log.info(f'[mixer] MUSIC from the Local Library: {saved}, last chosen, is not playing now')
            return
        mixer.set_sources(apps, saved)
        self._music_source(saved)

    def _mixer_inputs(self):
        """The MIC's list of inputs (the mixer shown): on macOS, the microphone permission asked for first (no inputs
        without it), the list made again once given."""
        from cutemusic.mic import microphone_access
        mixer = self.visuals.mixer

        def fill():
            mixer.set_inputs(sorted(self.mic.input_devices(), key=str.lower), self.mic.input)

        def given():
            self.mic.forget_devices()
            fill()
        microphone_access(given)
        fill()
        self._input_icons()
        # all of what the strips can play, logged each time the mixer is shown
        inputs = sorted(self.mic.input_devices(), key=str.lower)
        log.info(f'[mixer] shown. MIC inputs: {", ".join(inputs) or "none"} (on: {self.mic.input or "Off"}); '
                 f'AUDIO APPS: {self._capture.name if self._capture else "Off"}'
                 f'{"" if mixer.sources_available else " (no other apps: macOS 14.2+ only)"}; '
                 f'BLINK: {"Blink runs" if mixer.blink else "Blink not running"}; '
                 f'to Blink: {self._link_kind()}')
        self._sources_logged = None         # MUSIC's sources logged now too, not only when they change
        self._refresh_sources()

    def _apply_karaoke_audio(self):
        """The echo and VOCAL CUT heard as KARAOKE acts (mixer.karaoke_on: not while Blink runs)."""
        mixer = self.visuals.mixer
        on = mixer.karaoke_on()
        for mic in self._mics():
            mic.set_echo(mixer.echo.value if on else 0.0)
        self.player.set_vocal_cut(mixer.vocal_cut and on)

    def _feed_amp_from(self, mic, key):
        """The amplifier's meters to show a mixer input too, while it is heard on the amplifier's output (its
        monitor on: TO AMPLIFIER for a mic, Blink's call, an app's sound heard through the mixer)."""
        mic.levels.connect(lambda rms, mic=mic, key=key:
                           self.visuals.set_heard_levels(key, rms if mic.monitor and self.visuals.mixer.powered else []))

    def _monitor_clicked(self, on):
        """TO AMPLIFIER clicked: as it is now, and what it comes back to after karaoke or a call."""
        self._monitor_wanted = on
        util.settings().setValue('mixer/to_amplifier', on)
        for mic in self._mics():
            mic.set_monitor(on)

    def _monitor_resting(self):
        """TO AMPLIFIER outside a call: on in KARAOKE, else as last clicked."""
        return self.visuals.mixer.karaoke_on() or self._monitor_wanted

    def _set_monitor(self, on):
        self.visuals.mixer.set_monitor(on)
        for mic in self._mics():
            mic.set_monitor(on)

    def _mixer_karaoke(self, on, quiet=False):
        """KARAOKE turned, or Blink started or quit (quiet): the echo, VOCAL CUT and TO AMPLIFIER as KARAOKE acts
        (mixer.karaoke_on: off while Blink runs)."""
        mixer = self.visuals.mixer
        self._apply_karaoke_audio()
        acting = mixer.karaoke_on()
        if not self._in_call:           # TO AMPLIFIER: on to sing (in the headset), else as clicked; a call keeps it off
            self._set_monitor(self._monitor_resting())
        if not quiet:
            log.info(f'[mixer] karaoke {"on: echo and VOCAL CUT" if acting else "off"}')
        elif on:
            log.info(f'[mixer] karaoke {"on again: Blink quit" if acting else "off while Blink runs"}')

    def _mixer_power(self, on):
        """The mixer on (its devices for Blink, the mic, what Blink plays, the music to calls) or off (all of it
        gone: Blink falls back to the system default)."""
        mixer = self.visuals.mixer
        if on:
            self._start_link()
            self._apply_share()
            if self.mic.input:
                self.mic.set_input(self.mic.input)      # (built again)
                self._start_slots()
            else:
                self._default_mic()
            self._music_source(mixer.source() or '')        # an app as MUSIC's source captured again
            if mixer.streaming():
                self._set_streaming(True)
            self._blink_timer.start()
            self._watch_blink()
        else:
            if self.recorder.recording:         # POWER off: the recording ends there
                self._stop_recording()
            if self.recorder.streaming:         # and the streaming, its device gone (back with POWER)
                from cutemusic import control
                self.recorder.stop_streaming()
                control.set_streaming(False)
            self._blink_timer.stop()
            for mic in self._mics():
                mic.stop()
            self._music_source('')          # an app no longer captured (its sound back on its own output)
            if self.call_audio.link_source is not None:     # (the driver's Feed: what Blink plays)
                self.call_audio.set_link_source(None)
            self._feed_receiver = None
            self.call_audio.set_pulse_source(None)
            self._stop_link()
            mixer.set_blink(False)
            mixer.set_blink_calls([])
            mixer.set_listeners({})
            mixer.set_blink_redial(None)
            self._hung_up.clear()           # POWER on again: sending as usual
            self._set_send_cut(False)
            self._in_call = False
        log.info(f'[mixer] {"on" if on else "off: no device for Blink, no mic"}')

    def _mixer_mute(self, strip, muted):
        """A mixer strip's MUTE: the mic (headset and call), what Blink plays (the call goes on), the music (the
        play bar's mute)."""
        log.info(f'[mixer] {strip} {"muted" if muted else "unmuted"}')
        if strip == 'MIC':
            self.mic.set_mute(muted)
        elif strip in self.slot_mics:
            self.slot_mics[strip].set_mute(muted)
        elif strip == 'BLINK':
            self.call_audio.set_mute(muted)
        elif strip == 'MUSIC':          # AUDIO APPS: the app's sound through the mixer
            self.app_audio.set_mute(muted)
        else:                           # LOCAL LIBRARY: the player (the play bar's mute)
            self._set_muted(muted)

    def _set_muted(self, muted):
        self.player.set_muted(muted)
        self.visuals.amp.set_muted(muted)
        self.visuals.mixer.set_muted('LIBRARY', muted)
        set_line_icon(self.mute_button, 'muted' if muted else 'speaker')
        self.mute_button.setToolTip('Unmute' if muted else 'Mute')
        if self.mute_button.isChecked() != muted:
            self.mute_button.setChecked(muted)

    def _volume_changed(self, value):
        if self.player.muted:
            self._set_muted(False)
        self.player.set_volume(value / 100)
        # logged once it rests (a fader dragged: one line)
        if not hasattr(self, '_volume_log'):
            from PyQt6.QtCore import QTimer
            self._volume_log = QTimer(self, singleShot=True, interval=400)
            self._volume_log.timeout.connect(lambda: log.info(f'[player] volume {self.volume.value()} ({self.player.applied()})'))
        self._volume_log.start()
        util.settings().setValue('player/volume', value)
        volumes = self._output_volumes()        # and the output's own: back to it when it is played on again
        volumes[self.player.output or ''] = value
        util.settings().setValue('player/output_volumes', json.dumps(volumes))
        self._mpris_update()

    def _output_volumes(self):
        """{output's name ('' the system's default): its volume (0..100)}, the last set on each."""
        try:
            volumes = json.loads(util.settings().value('player/output_volumes', '{}', type=str))
        except ValueError:
            return {}
        return volumes if isinstance(volumes, dict) else {}

    def _restore_output_volume(self):
        """Another output played on: its volume, the last set on it (unchanged if never played on)."""
        value = self._output_volumes().get(self.player.output or '')
        if isinstance(value, int) and value != self.volume.value():
            self.volume.setValue(value)

    def _shuffle_toggled(self, on):
        self.queue.set_shuffle(on)
        util.settings().setValue('player/shuffle', on)
        self._prepare_next()
        self._mpris_update()

    def _repeat_toggled(self, on):
        self.queue.repeat = on
        util.settings().setValue('player/repeat', on)
        self._prepare_next()
        self._mpris_update()

    def _state_changed(self, state):
        if state == 'paused':
            self._save_queue()
        self.play_button.setProperty('transport_icon', 'pause' if state == 'playing' else 'play')
        self.play_button.setIcon(transport_icon(self.play_button.property('transport_icon')))
        self.play_button.setToolTip('Pause' if state == 'playing' else 'Play')
        self.dock.set_state(state)
        self._mpris_update()

    def _forget_lyrics(self):
        from cutemusic import online
        track = self.queue.current
        if track is None:
            return
        online.forget_lyrics(track)
        self.visuals.show_track(track)
        self.dock.show_track(track)

    def _online_found(self, path):
        track = self.queue.current
        if track is None or track.path != path:
            return
        self.visuals.show_track(track)
        self.dock.show_track(track)
        if self.mpris is not None:
            self.mpris._cover_for = None    # the cover again
        self._mpris_update()

    def show_playing_track(self, level='track'):
        """The track playing ('track'), its album or its artist selected in the library, its tab shown; the filter
        cleared when it hides it."""
        track = self.queue.current
        if track is None:
            return
        self.tabs.setCurrentWidget(self.browser)
        if level not in ('track', 'album', 'artist'):
            level = 'track'
        if not self.browser.select_track(track, level) and self.browser.has_filters():
            self.browser.clear_filters()
            self.browser.select_track(track, level)
        self.browser.tree.setFocus()

    def _update_track(self, track, ended=False, lyrics=True):
        self.visuals.show_track(track)
        if lyrics and self._is_negative(track):        # a negative: the lyrics to sing along (not on a switch)
            self.visuals.show_page(next(i for i, (key, label) in enumerate(PAGES) if key == 'lyrics'))
        self._update_version_button(track)
        self.dock.show_track(track)
        self.online.want(track)
        self._show_next_up(self.queue.peek(1))
        self._duration = (track.get('duration') or 0.0) if track else 0.0
        self._position_seconds = 0.0
        from cutemusic import recorder as _recorder
        self.seek_slider.set_markers(_recorder.read_markers(track.path) if track is not None else [])     # its chapters
        self.seek_slider.setRange(0, int(self._duration))
        self.seek_slider.setValue(0)
        self.elapsed.setText('0:00')
        self.total.setText(_duration(self._duration))
        self._mpris_update()
        self._show_now_playing(track, ended)

    def _show_now_playing(self, track, ended=False):
        """The bar's title, artist and album (links), in the theme's text colour (written into the links: shown
        again when the theme changes, changeEvent)."""
        self._now_playing_shown = (track, ended)
        if track is None:
            self.now_playing.setText('')
            self.setWindowTitle(APP_NAME)
            return
        artist = track.get('artist') or track.album_artist
        palette = self.now_playing.palette()
        color = (palette.color(palette.ColorGroup.Disabled, palette.ColorRole.WindowText) if ended
                 else palette.color(self.now_playing.foregroundRole())).name()
        link = f'style="color: {color}; text-decoration: none"'
        # its color markers (colors.py) before the title
        from cutemusic.colors import QCOLORS
        dots = ''.join(f'<span style="color: {QCOLORS[name].name()}">\u25cf</span>'
                       for name in (track.get('colors') or ()) if name in QCOLORS)
        self.now_playing.setText((dots + '&nbsp;' if dots else '') +
                                 f'<a href="track" {link}><b>{_html(track.display_title)}</b></a>'
                                 f' — <a href="artist" {link}>{_html(artist)}</a>'
                                 + (f' · <a href="album" {link}>{_html(track["album"])}</a>' if track.get('album') else ''))
        self.setWindowTitle(APP_NAME if ended else f'{track.display_title} — {artist} — {APP_NAME}')

    # Where it was left: the queue, the track playing and the point in it (~/.ag-cute-music/queue.json)

    @property
    def _queue_path(self):
        return os.path.join(util.CONFIG_DIR, 'queue.json')

    def _save_queue(self):
        self._saved_at = time.monotonic()
        state = self.queue.state() if self.queue.current is not None else {}
        if state:
            state['seconds'] = self._position_seconds
        temporary = self._queue_path + '.tmp'
        try:
            with open(temporary, 'w', encoding='utf-8') as f:
                json.dump(state, f)
            os.replace(temporary, self._queue_path)
        except OSError as e:
            log.warning(f'[queue] cannot save {self._queue_path}: {e}')

    def _resume(self, play=False):
        """At start (and switching queues): the queue as it was, at the track and the point it was left, paused
        (Space plays on) unless play. False when there is none."""
        try:
            with open(self._queue_path, encoding='utf-8') as f:
                state = json.load(f)
        except (OSError, ValueError):
            return False
        if not isinstance(state, dict) or not self.queue.restore(state):
            return False
        track = self.queue.current
        if not os.path.exists(track.path):
            self.queue.set([], -1)
            return False
        seconds = float(state.get('seconds') or 0)
        self.queue.shuffle = self.shuffle_action.isChecked()
        self.queue.repeat = self.repeat_action.isChecked()
        self._update_track(track)
        self._position_seconds = seconds
        self._reveal = track        # shown in the library once it is there (the scan may still be going)
        self._reveal_track()
        self.player.play_file(track.path, seconds, paused=not play)
        self._prepare_next()
        self._position(seconds, track.get('duration') or 0)
        log.info(f'[queue] resumed at {track.path}, {seconds:.0f} s')
        return True

    # Files from the command line (and the desktop: Exec=ag-cute-music %F)

    def open_paths(self, paths):
        tracks = []
        for path in paths:
            path = os.path.abspath(os.path.expanduser(path))
            if os.path.isdir(path):
                for directory, folders, names in os.walk(path):
                    folders.sort()
                    tracks += [read_track(os.path.join(directory, name), path) for name in sorted(names)
                               if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS]
            elif os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS:
                tracks.append(read_track(path, os.path.dirname(path)))
        if tracks:
            self.play_tracks(tracks, 0)

    def closeEvent(self, event):
        if self.recorder.recording:
            self._stop_recording()
        if self.recorder.streaming:         # AG Podcast Streaming gone with us (its choice kept for the next start)
            from cutemusic import control
            self.recorder.stop_streaming()
            control.set_streaming(False)
        for mic in self._mics():
            mic.stop()
        self.call_audio.stop()
        if getattr(self, '_feed_receiver', None) is not None:       # (the Feed's input closed)
            self._feed_receiver.stop()
        self._music_source('')          # an app's tap removed: its sound back on its own output
        self._stop_link()           # Blink no longer lists the mixer
        if self._sylk_visible:      # hidden again: listed only while we run and Sylk Desktop does
            from cutemusic import control
            control.set_mixer_visible(False)
            self._sylk_visible = False
        from cutemusic import control as _control      # (Linux: off the session bus)
        _control.stop_bus()
        if self._blink_listener is not None:        # CoreAudio calls no one gone
            self._blink_listener.remove()
            self._blink_listener = None
        settings = util.settings()
        if self.full_screen:        # kept as it was before full screen
            maximized, state, geometry = self._full_screen_from
            settings.setValue('window/geometry', geometry)
            settings.setValue('window/splitter', state)
        else:
            settings.setValue('window/geometry', self.saveGeometry())
            settings.setValue('window/splitter', self.splitter.saveState())
        self._save_queue()
        self.player.stop()
        self.scanner.stop()
        self.negative_scanner.stop()
        self.negatives.detach()     # the worker goes on making them (negatives.py)
        self.library_server.stop()
        self.web.stop()
        self.discovery.stop()
        if self.mpris is not None:
            self.mpris.close()
        docked = self.dock.isVisible()
        self.dock.hide()
        settings.setValue('window/docked', docked)
        super().closeEvent(event)
        # quit-on-last-window-closed only counts a visible window being closed: docked, the main window was
        # hidden and the dock just hidden above, so the event loop would go on with nothing on the screen
        QApplication.quit()


def _html(text):
    import html
    return html.escape(text or '')
