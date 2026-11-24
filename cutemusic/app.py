"""Starting AG CuteMusic."""

import os
import re
import shutil
import signal
import subprocess
import sys

from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import QApplication

from cutemusic import APP_ID, APP_NAME, util


def _gsetting(key):
    try:
        return subprocess.run(['gsettings', 'get', 'org.gnome.desktop.interface', key], capture_output=True, text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def _apply_desktop_font(application):
    # Qt on GNOME ignores the desktop's text scaling (and without a GTK platform theme falls back to its own 9pt default),
    # so text comes out smaller than in every GTK app; take the desktop font with its scaling instead
    if not sys.platform.startswith('linux') or not shutil.which('gsettings'):
        return
    match = re.fullmatch(r"'(.+?)\s+(\d+(?:\.\d+)?)'", _gsetting('font-name'))
    if match is None:
        return
    try:
        scale = float(_gsetting('text-scaling-factor') or 1)
    except ValueError:
        scale = 1.0
    screen = application.primaryScreen()
    if screen is not None and screen.logicalDotsPerInch() >= 96 * scale - 1:
        scale = 1.0     # already applied through the X11 DPI (Xft.dpi)
    family, size = match.group(1), float(match.group(2)) * scale
    font = QFont(application.font())
    font.setFamily(re.sub(r'\s+(Regular|Medium)$', '', family))
    font.setPointSizeF(size)
    application.setFont(font)


def _register_desktop_file(icon):
    """Linux: the dock shows the icon of the .desktop file matching the window (its app id on Wayland, its
    WM_CLASS on X11), not the window's own; ~/.local/share/applications/ag-cute-music.desktop, written when missing
    or pointing elsewhere."""
    if not sys.platform.startswith('linux'):
        return
    root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    if root.startswith('/usr/'):
        return      # installed (the Debian package): its own /usr/share/applications/ag-cute-music.desktop
    entry = '\n'.join(['[Desktop Entry]', 'Type=Application', f'Name={APP_NAME}', 'Comment=Music player',
                        f'Exec={os.path.join(root, "bin", APP_ID)} %F', f'Icon={icon}', f'StartupWMClass={APP_ID}',
                        'Categories=AudioVideo;Audio;Player;', 'Terminal=false', ''])
    path = os.path.join(os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'), 'applications', f'{APP_ID}.desktop')
    try:
        with open(path) as f:
            if f.read() == entry:
                return
    except OSError:
        pass
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(entry)
        util.log.info(f'Desktop entry: {path}')
    except OSError as e:
        util.log.warning(f'Cannot write the desktop entry {path}: {e}')


def main():
    if sys.argv[1:2] == ['--negatives']:     # the negatives made in a process of their own (negatives.py)
        from cutemusic.negatives import worker_main
        return worker_main()
    if sys.argv[1:2] == ['--tunnel']:        # the web page's public port: the shootback slaver (tunnel.py)
        from cutemusic.tunnel import main as tunnel
        return tunnel(sys.argv[2:])
    if '--headless' in sys.argv[1:]:        # the music folder shared on the LAN, no windows (a NAS): headless.py
        from cutemusic.headless import main as headless
        return headless()
    # GNOME on Wayland draws no title bar for Qt (Qt draws its own, light, before Qt 6.8): through XWayland, where
    # GNOME's window manager draws it dark or light as the window asks (theme.apply_title_bar); QT_QPA_PLATFORM set,
    # or CUTEMUSIC_WAYLAND=1: as is
    if sys.platform.startswith('linux') and os.environ.get('WAYLAND_DISPLAY') and not os.environ.get('QT_QPA_PLATFORM') \
            and os.environ.get('CUTEMUSIC_WAYLAND') != '1':
        os.environ['QT_QPA_PLATFORM'] = 'xcb;wayland'
    if sys.platform == 'darwin':
        QApplication.setStyle('Fusion')     # as Blink Qt: the macOS style keeps form fields at their size hint, labels right-aligned
    application = QApplication(sys.argv)
    from cutemusic.theme import apply_theme
    apply_theme(application)        # as Blink Qt: rounded buttons over the platform's style, or Fusion with a dark palette
    application.setApplicationName(APP_NAME)
    application.setOrganizationName('AG Projects')
    application.setDesktopFileName(APP_ID)      # the Wayland app id, matched to ag-cute-music.desktop
    _apply_desktop_font(application)
    icon = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), 'icons', f'{APP_ID}.svg')
    if sys.platform.startswith('linux') and os.path.exists(icon[:-4] + '.png'):
        # the PNG made from it (rsvg-convert -w 512): the SVG's wood came out black on some desktops (their SVG
        # renderer or Qt's without its SVG icon engine), a picture looks the same everywhere
        icon = icon[:-4] + '.png'
    if os.path.exists(icon):
        application.setWindowIcon(QIcon(icon))     # the Dock's too, when started from bin/
    if sys.platform == 'darwin':        # the amplifier's VU meters (appicon.py), as the .icns and the live Dock icon
        from cutemusic import appicon
        application.setWindowIcon(appicon.icon())
    util.init()
    if os.path.exists(icon):
        _register_desktop_file(icon)
    from cutemusic.player import gst_error
    if gst_error:
        util.log.warning(f'GStreamer is not available: {gst_error}')
    from cutemusic.window import MainWindow
    window = MainWindow()
    from cutemusic.theme import apply_title_bar
    apply_title_bar(window)
    if window._start_docked:        # docked when it was quit: only the dock, the main window never mapped (shown and
        window._start_docked = False    # hidden at once, the compositor kept a black window for it)
        window.dock_right()
    else:
        window.show()
    if '--mixer' in sys.argv[1:]:       # opened on the Mixer view (Blink's notice, CuteMusic.py there)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, window.show_mixer)
    paths = [argument for argument in sys.argv[1:] if not argument.startswith('-')]
    if paths:
        window.open_paths(paths)
    interrupts = []

    def interrupted(*args):
        interrupts.append(1)
        if len(interrupts) > 1:         # the second ^C: closing did not end it, so gone at once
            os._exit(130)
        window.close()
    signal.signal(signal.SIGINT, interrupted)
    from PyQt6.QtCore import QTimer
    signals = QTimer(application)       # Python runs its signal handlers only between Qt's calls into it
    signals.timeout.connect(lambda: None)
    signals.start(500)
    if hasattr(signal, 'SIGUSR2'):      # kill -USR2 <pid>: where every thread is, on stderr (console.log)
        import faulthandler
        try:
            faulthandler.register(signal.SIGUSR2, all_threads=True)
        except (OSError, RuntimeError, ValueError):
            pass
    code = application.exec()
    # torn down in order: the main window (its dock, views and timers with it) before the application. Left to the
    # end of this frame, Python (3.14) may free the application first, and the window's wrapper then reaches into
    # Qt objects already gone: a segmentation fault at quit
    signal.signal(signal.SIGINT, signal.SIG_DFL)        # the handler held the window
    signals.stop()
    from PyQt6 import sip
    for widget in (window.dock, window):     # the dock is a window of its own (no parent): not deleted with it
        if not sip.isdeleted(widget):
            sip.delete(widget)
    del window, interrupted
    return code


if __name__ == '__main__':
    sys.exit(main())
