"""The theme: View -> Theme, as the amplifier's plate: Follow system (dark or light as the desktop is, changing with
it), Light or Dark (ui.conf: view/theme; before it view/dark, dark the default).

Dark is Fusion with a dark palette, the same on every platform and desktop; the desktop's is the platform's style
with its own palette. The title bar follows: the application's color scheme (Qt 6.8+: macOS, Windows, Wayland
decorations), and on X11 the window's _GTK_THEME_VARIANT, which GNOME's window manager (also through XWayland)
draws the title bar by."""

from PyQt6.QtGui import QColor, QPalette
import ctypes
import ctypes.util

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication

from cutemusic import util


__all__ = ['THEMES', 'theme', 'set_theme', 'dark_theme', 'set_dark_theme', 'apply_theme', 'apply_title_bar']

THEMES = [('system', 'Follow system'), ('light', 'Light'), ('dark', 'Dark')]


_platform = {}      # the style and palette the application started with


def theme():
    """'system', 'light' or 'dark' (view/theme; an older ui.conf's view/dark: dark or light)."""
    settings = util.settings()
    chosen = settings.value('view/theme', '', type=str)
    if chosen in dict(THEMES):
        return chosen
    return 'dark' if settings.value('view/dark', True, type=bool) else 'light'


def _system_dark():
    """Whether the desktop is dark now: Qt's colour scheme (6.5+; ours left to the system's in Follow system), else
    the palette the application started with."""
    hints = QGuiApplication.styleHints()
    if hasattr(hints, 'colorScheme'):
        scheme = hints.colorScheme()
        if scheme != Qt.ColorScheme.Unknown:
            return scheme == Qt.ColorScheme.Dark
    palette = _platform.get('palette') or QGuiApplication.palette()
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def dark_theme():
    chosen = theme()
    return _system_dark() if chosen == 'system' else chosen == 'dark'


def set_theme(chosen):
    util.settings().setValue('view/theme', chosen)
    _reapply()


def set_dark_theme(on):
    set_theme('dark' if on else 'light')


_following = []     # the system's colour scheme watched (once)


def _reapply():
    application = QApplication.instance()
    apply_theme(application)
    for window in application.topLevelWidgets():
        if window.isWindow() and window.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
            apply_title_bar(window)


def _dark_palette():
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: '#1b1e25',
        QPalette.ColorRole.WindowText: '#e8eaf0',
        QPalette.ColorRole.Base: '#14171c',
        QPalette.ColorRole.AlternateBase: '#1f232b',
        QPalette.ColorRole.ToolTipBase: '#262b36',
        QPalette.ColorRole.ToolTipText: '#e8eaf0',
        QPalette.ColorRole.PlaceholderText: '#6c7282',
        QPalette.ColorRole.Text: '#e8eaf0',
        QPalette.ColorRole.Button: '#2a2f3a',
        QPalette.ColorRole.ButtonText: '#e8eaf0',
        QPalette.ColorRole.BrightText: '#ff6b5a',
        QPalette.ColorRole.Light: '#3a404d',
        QPalette.ColorRole.Midlight: '#30353f',
        QPalette.ColorRole.Mid: '#5c6270',
        QPalette.ColorRole.Dark: '#101318',
        QPalette.ColorRole.Shadow: '#000000',
        QPalette.ColorRole.Highlight: '#3d6fb8',
        QPalette.ColorRole.HighlightedText: '#ffffff',
        QPalette.ColorRole.Link: '#7cc4ff',
        QPalette.ColorRole.LinkVisited: '#b39ddb',
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    disabled = QPalette.ColorGroup.Disabled
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(disabled, role, QColor('#6c7282'))
    palette.setColor(disabled, QPalette.ColorRole.Highlight, QColor('#3a404d'))
    return palette


def apply_theme(application):
    from cutemusic.style import CuteStyle
    if not _platform:
        _platform['style'] = application.style().name()
        _platform['palette'] = QPalette(application.palette())
    chosen = theme()
    hints = application.styleHints()
    if hasattr(hints, 'setColorScheme'):        # Qt 6.8+: the title bars of macOS, Windows and Qt's own decorations;
        # Follow system: left to the system (colorScheme() then tells it, and changes with it)
        hints.setColorScheme({'dark': Qt.ColorScheme.Dark, 'light': Qt.ColorScheme.Light}.get(chosen, Qt.ColorScheme.Unknown))
    if not _following and hasattr(hints, 'colorSchemeChanged'):
        _following.append(True)
        hints.colorSchemeChanged.connect(lambda scheme: theme() == 'system' and _reapply())
    if dark_theme():
        application.setStyle(CuteStyle('Fusion'))       # the platform's style (gtk, macOS) may ignore the palette
        application.setPalette(_dark_palette())
    else:
        application.setStyle(CuteStyle(_platform['style']))
        palette = _platform['palette']
        if palette.color(QPalette.ColorRole.Window).lightness() < 128:      # started with a dark desktop: a light one
            from PyQt6.QtWidgets import QStyleFactory
            fusion = QStyleFactory.create('Fusion')
            palette = fusion.standardPalette() if fusion is not None else palette
        application.setPalette(palette)


class _X11(object):
    """Xlib through ctypes (no python3-xlib): a window's _GTK_THEME_VARIANT."""

    def __init__(self):
        self.lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library('X11') or 'libX11.so.6')
        self.lib.XOpenDisplay.restype = ctypes.c_void_p
        self.lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.lib.XInternAtom.restype = ctypes.c_ulong
        self.lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        self.lib.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int,
                                             ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        self.lib.XFlush.argtypes = [ctypes.c_void_p]
        self.display = self.lib.XOpenDisplay(None)
        if not self.display:
            raise OSError('cannot open the X display')
        self.variant = self.lib.XInternAtom(self.display, b'_GTK_THEME_VARIANT', False)
        self.utf8 = self.lib.XInternAtom(self.display, b'UTF8_STRING', False)

    def set_variant(self, window_id, value):
        self.lib.XChangeProperty(self.display, window_id, self.variant, self.utf8, 8, 0, value, len(value))      # 0: PropModeReplace
        self.lib.XFlush(self.display)


_x11 = []      # the _X11, or None when it failed (not tried again)


def apply_title_bar(window):
    """The title bar of window dark or light, with the theme (X11: before the window is shown, or at once after)."""
    if QGuiApplication.platformName() != 'xcb':
        return
    if not _x11:
        try:
            _x11.append(_X11())
        except Exception as e:
            _x11.append(None)
            util.log.warning(f'[theme] cannot set the title bar: {e}')
    if _x11[0] is not None:
        _x11[0].set_variant(int(window.winId()), b'dark' if dark_theme() else b'light')
