"""Docked at the right of the screen (as AG CuteCams' docked windows): a column the full height of the screen (less
its panels) and a fifth of its width, without title bar, on top, stacking the cover, the VU meter (a click: its next
style; after the spectrum, the lava lamp), the lyrics and what plays, with previous / play / next. The main window is hidden meanwhile and comes back as it was.

Esc, a double click, or the context menu's Undock, take it back to the main window; the mouse wheel over the cover
and the VU meter is the volume, the media keys work as ever."""

from PyQt6.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QMenu, QSlider, QToolButton, QVBoxLayout, QWidget

from cutemusic import APP_NAME, util
from cutemusic.icons import set_line_icon, transport_icon
from cutemusic.visuals import BACKGROUND, DIM, TEXT, VU_STYLES, CoverView, LyricsStrip, VUMeter


__all__ = ['DockWindow', 'DOCK_FRACTION']


DOCK_FRACTION = 5       # a fifth of the screen's width, as AG CuteCams


def _duration(seconds):
    seconds = int(seconds or 0)
    return f'{seconds // 60}:{seconds % 60:02d}'


class DockWindow(QWidget):
    """undock() asked for (Esc, double click, menu)."""

    undock = pyqtSignal()

    def __init__(self, main):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.main = main
        self.side = 'right'
        self.setWindowTitle(APP_NAME)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), BACKGROUND)
        self.setPalette(palette)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.cover = CoverView(captions=False)
        self.cover.setMinimumSize(80, 80)
        self.vu = VUMeter()
        self.vu.setMinimumSize(80, 80)
        style = util.settings().value('visuals/vu_style', 'bars', type=str)
        self.vu.set_style(style if style in dict(VU_STYLES) else 'bars')
        self.vu.style_changed.connect(lambda style: (util.settings().setValue('visuals/vu_style', style), self._arrange(),
                                                     self._check_pill()))
        self.vu.next_style = self._next_style       # its click and the menu: after its last style, the lava lamp
        # the lava lamp, the meter's style after its last: in its place (its own lyrics strip left hidden, the dock's
        # lyrics are under it); a click on its base's lights: its colours, elsewhere: the meter's first style again
        from cutemusic.lava import LavaLamp
        self.lava = LavaLamp()
        self.lava.setMinimumSize(80, 80)
        self.lava.FILL = 0.96           # the lamp filling its place: no room above and below it
        self.lava.mouseReleaseEvent = self._lava_clicked
        self.lyrics = LyricsStrip()     # four lines: the two before the one sung (what was just said), it, the next
        self.lyrics.LINES, self.lyrics.BEFORE = 4, 2
        layout.addWidget(self.cover)
        layout.addWidget(self._style_pills())       # over the meter: its height changes with its style, not theirs
        layout.addWidget(self.vu)
        layout.addWidget(self.lava)
        self._show_lava(util.settings().value('dock/lava', False, type=bool))
        layout.addStretch(1)                 # the lyrics centred in the room left between the meter and the play bar
        layout.addWidget(self.lyrics)
        layout.addStretch(1)
        self.now_box = self._now_playing()
        layout.addWidget(self.now_box)

        for key, slot in ((Qt.Key.Key_Escape, self.undock.emit), (Qt.Key.Key_Space, main.toggle),
                          (Qt.Key.Key_Right, main.next), (Qt.Key.Key_Left, main.previous)):
            action = QAction(self)
            action.setShortcut(QKeySequence(key.value))
            action.triggered.connect(slot)
            self.addAction(action)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)

    def _button(self, icon, tip, slot):
        button = QToolButton()
        button.setProperty('transport_icon', icon)
        button.setIcon(transport_icon(icon, TEXT))
        button.setIconSize(QSize(22, 22))
        button.setToolTip(tip)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setMinimumSize(40, 36)
        button.clicked.connect(slot)
        return button

    def _now_playing(self):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 8, 14, 12)
        layout.setSpacing(4)
        self.title = QLabel()
        font = self.title.font()
        font.setPointSizeF(font.pointSizeF() * 1.15)
        font.setBold(True)
        self.title.setFont(font)
        self.title.setStyleSheet(f'color: {TEXT.name()}')
        self.subtitle = QLabel()
        self.subtitle.setStyleSheet(f'color: {DIM.name()}')
        for label in (self.title, self.subtitle):
            label.setMinimumWidth(10)
        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)
        row = QHBoxLayout()
        self.elapsed, self.total = QLabel('0:00'), QLabel('0:00')
        for label in (self.elapsed, self.total):
            label.setStyleSheet(f'color: {DIM.name()}')
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.seek_slider.sliderReleased.connect(lambda: self.main._seek(self.seek_slider.value()))
        self.seek_slider.actionTriggered.connect(
            lambda action: action != QSlider.SliderAction.SliderMove and self.main._seek(self.seek_slider.sliderPosition()))
        row.addWidget(self.elapsed)
        row.addWidget(self.seek_slider, 1)
        row.addWidget(self.total)
        layout.addLayout(row)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.previous_button = self._button('previous', 'Previous', self.main.previous)
        self.play_button = self._button('play', 'Play', self.main.toggle)
        self.next_button = self._button('next', 'Next', self.main.next)
        for button in (self.previous_button, self.play_button, self.next_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return box

    # Fed by the main window

    def show_track(self, track):
        self.cover.show_track(track)
        self.lyrics.show_track(track)
        self._arrange()             # the lyrics shown or not: the meter's room
        if track is None:
            self.title.setText('Nothing playing')
            self.subtitle.setText('')
            self.seek_slider.setRange(0, 0)
            self.total.setText('0:00')
            return
        artist = track.get('artist') or track.album_artist
        album = track.get('album') or ''
        if track.get('year'):
            album += f' ({track["year"]})'
        self._texts = (track.display_title, ' · '.join(text for text in (artist, album) if text))
        self._elide()
        self.seek_slider.setRange(0, int(track.get('duration') or 0))
        self.total.setText(_duration(track.get('duration')))

    _texts = ('', '')

    def _elide(self):
        for label, text in zip((self.title, self.subtitle), self._texts):
            label.setText(label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(10, self.width() - 28)))
            label.setToolTip(text)

    def set_position(self, seconds, duration):
        self.lyrics.set_position(seconds, duration)
        if duration:
            self.seek_slider.setRange(0, int(duration))
            self.total.setText(_duration(duration))
        if not self.seek_slider.isSliderDown():
            self.seek_slider.setValue(int(seconds))
        self.elapsed.setText(_duration(seconds))

    def set_levels(self, rms, peak):
        self.vu.set_levels(rms, peak)
        self.lava.set_levels(rms, peak)

    def set_state(self, state):
        icon = 'pause' if state == 'playing' else 'play'
        self.play_button.setIcon(transport_icon(icon, TEXT))
        self.play_button.setToolTip('Pause' if state == 'playing' else 'Play')

    # Placing

    def place(self):
        # the main window's screen; when it was never shown (started docked) it has none of its own: the screen the
        # mouse is on (else the primary one), not one the dock could land on unseen (another monitor, one gone)
        if self.main is not None and self.main.isVisible():
            screen = self.main.screen()
        else:
            from PyQt6.QtGui import QCursor, QGuiApplication
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        screen = screen or self.screen()
        area = screen.availableGeometry()
        width = screen.geometry().width() // DOCK_FRACTION
        x = area.left() if self.side == 'left' else area.right() + 1 - width
        self.setGeometry(x, area.top(), width, area.height())
        self._arrange()

    def dock(self, side='right'):
        self.side = side
        self._follow_amp()
        self.place()
        self.show()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(0, self.place)        # again, once the window manager has it

    def _follow_amp(self):
        """The meter as the amplifier's: its lever's style (its own analog needles: the needles), not the lava lamp."""
        style = util.settings().value('amp/meter', '', type=str)
        style = 'needles' if style == 'analog' else style
        if style not in dict(VU_STYLES):
            return
        if not self.lava.isHidden():
            self._show_lava(False)
        self.vu.set_style(style)
        self._check_pill()

    def _show_lava(self, on):
        self.vu.setVisible(not on)
        self.lava.setVisible(on)
        util.settings().setValue('dock/lava', bool(on))
        self._arrange()
        self._check_pill()

    PILLS = [name for name, label in VU_STYLES] + ['lava']

    def _style_pills(self):
        """Small icons over the meter, a style each and the lava lamp: dim, lit when pointed at, the one shown
        checked."""
        box = self.pills_box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 2, 0, 4)
        row.setSpacing(2)
        row.addStretch(1)
        self.pills = QButtonGroup(self)
        labels = dict(VU_STYLES, lava='Lava lamp')
        for index, name in enumerate(self.PILLS):
            button = QToolButton()
            set_line_icon(button, name, DIM)
            button.setIconSize(QSize(13, 13))
            button.setFixedSize(22, 18)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setToolTip(labels[name])
            button.setStyleSheet('QToolButton { border: none; border-radius: 9px; background: transparent; }'
                                 'QToolButton:hover { background: rgba(255, 255, 255, 0.08); }'
                                 'QToolButton:checked { background: rgba(255, 255, 255, 0.16); }')
            self.pills.addButton(button, index)
            row.addWidget(button)
        row.addStretch(1)
        self.pills.idClicked.connect(self._pill_clicked)
        return box

    def _pill_clicked(self, index):
        name = self.PILLS[index]
        if name == 'lava':
            self._show_lava(True)
        else:
            if self.lava.isVisible():
                self._show_lava(False)
            self.vu.set_style(name)
        self._check_pill()

    def _check_pill(self):
        if not hasattr(self, 'pills'):
            return
        name = 'lava' if not self.lava.isHidden() else self.vu.style      # (hidden: not shown even when the dock is)
        button = self.pills.button(self.PILLS.index(name))
        if button is not None:
            button.setChecked(True)

    def _next_style(self):
        if self.lava.isVisible():
            self._show_lava(False)
            self.vu.set_style(VU_STYLES[0][0])
        elif self.vu.style == VU_STYLES[-1][0]:
            self._show_lava(True)
        else:
            VUMeter.next_style(self.vu)

    def _lava_clicked(self, event):
        from cutemusic.lava import LavaLamp
        glass, cap, base = self.lava._geometry()
        on_light = any(abs(event.position().x() - point.x()) <= radius * 2 and abs(event.position().y() - point.y()) <= radius * 2
                       for point, radius, width in self.lava._buttons(glass, base))
        if on_light:
            LavaLamp.mouseReleaseEvent(self.lava, event)
        elif event.button() == Qt.MouseButton.LeftButton:
            self._next_style()

    def _arrange(self):
        width = self.width()
        self.cover.setFixedHeight(width)                                    # square
        # the meter (or the lamp) at most the room left by the cover, the style pills and the play bar: never
        # pushing the play bar off the screen
        room = self.height() - width - (self.pills_box.sizeHint().height() if hasattr(self, 'pills_box') else 0) \
            - (self.now_box.sizeHint().height() if hasattr(self, 'now_box') else 0) - 8 \
            - (self.lyrics.sizeHint().height() if hasattr(self, 'lyrics') and not self.lyrics.isHidden() else 0)
        room = max(60, room) if self.height() > 0 else 10 ** 6
        self.lava.setFixedHeight(min(room, int(width * 0.72)))              # a small lamp: the lyrics keep their room
        self.vu.setFixedHeight(min(room, self.vu.content_height(width, int(width * (0.85 if self.vu.style == 'needles' else 0.75))) if self.vu.style != 'leds' else int(width * 0.6)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange()
        self._elide()

    def mouseDoubleClickEvent(self, event):
        self.undock.emit()

    def _context_menu(self, position):
        menu = QMenu(self)
        menu.addAction('Play / Pause', self.main.toggle)
        menu.addAction('Next', self.main.next)
        menu.addAction('Previous', self.main.previous)
        menu.addSeparator()
        menu.addAction('Next VU style', self.vu.next_style)
        menu.addSeparator()
        menu.addAction('Undock', self.undock.emit)
        menu.addAction('Quit', self.main.close)
        menu.exec(self.mapToGlobal(position))

    def closeEvent(self, event):
        # closed by the window manager (Alt+F4): back to the main window, not gone
        event.ignore()
        self.undock.emit()
