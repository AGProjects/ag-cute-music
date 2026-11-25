"""Where things are kept (~/.ag-cute-music), the logs, and calling into the GUI thread.

  cutemusic.log   the application's log (also on the console)
  console.log    everything written to the console: the log, and what Python (tracebacks), Qt and
                 GStreamer print themselves; also when started from the Finder, with no console
Each is moved to <name>.1 at start once larger than LOG_LIMIT. An exception not caught is logged
(written at once to both files) and the application goes on; a crash (segmentation fault) writes
the Python stacks to console.log directly.
"""

import logging
import os
import re
import sys
import threading
import time
import traceback

from PyQt6.QtCore import QObject, QSettings, Qt, pyqtSignal


__all__ = ['CONFIG_DIR', 'log', 'settings', 'portable_path', 'local_path', 'init', 'call_in_gui_thread', 'bring_to_front',
           'self_executable']


CONFIG_DIR = os.path.expanduser('~/.ag-cute-music')
LOG_LIMIT = 5 * 1024 * 1024

log = logging.getLogger('cutemusic')

_HOME_PREFIX = re.compile(r'^(/Users|/home)/[^/]+(?=/|$)')


def portable_path(path):
    """A folder as kept in ui.conf: ~/... under the home folder, so the configuration copied between macOS
    (/Users/<name>) and Linux (/home/<name>) still points to the same place; absolute otherwise."""
    if not path:
        return path
    path = os.path.abspath(os.path.expanduser(path))
    home = os.path.expanduser('~')
    if path == home or path.startswith(home + os.sep):
        return '~' + path[len(home):]
    return path


def local_path(path):
    """A folder from ui.conf for this computer: ~ expanded; an absolute path in another computer's home (the
    configuration of a Mac on Linux, or the other way, saved before portable_path) taken under this home when it
    does not exist here."""
    if not path:
        return path
    path = os.path.expanduser(path)
    if not os.path.exists(path) and _HOME_PREFIX.match(path):
        moved = _HOME_PREFIX.sub(os.path.expanduser('~').replace('\\', '\\\\'), path, count=1)
        if os.path.exists(moved) or not os.path.exists(os.path.dirname(path)):
            return moved
    return path


class _Invoker(QObject):
    invoke = pyqtSignal(object)

    def __init__(self):
        super(_Invoker, self).__init__()
        self.invoke.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def _run(self, call):
        call()


_invoker = None
_settings = None
_console_file = None    # console.log, written to directly by what must not wait for the copying thread
_copiers = []           # (fd, the console it was, the thread copying it)


def _rotate(path):
    try:
        if os.path.getsize(path) > LOG_LIMIT:
            os.replace(path, path + '.1')
    except OSError:
        pass


def _tee_console(path):
    """File descriptors 1 and 2 (Python, Qt and GStreamer write there) go through a pipe whose reader copies
    what comes to the console as it was and to path."""
    global _console_file
    _rotate(path)
    try:
        output = _console_file = open(path, 'ab', buffering=0)
    except OSError:
        return
    import faulthandler
    try:
        faulthandler.enable(output)     # a crash: the Python stacks, straight to the file
    except (OSError, RuntimeError, ValueError) as e:
        output.write(f'faulthandler not enabled: {e}\n'.encode())
    output.write(f'\n==== {time.strftime("%Y-%m-%d %H:%M:%S")} started (pid {os.getpid()})\n'.encode())
    for fd in (1, 2):
        try:
            console = os.dup(fd)
            read_end, write_end = os.pipe()
            os.dup2(write_end, fd)
            os.close(write_end)
        except OSError:
            continue

        def copy(read_end=read_end, console=console):
            while True:
                try:
                    data = os.read(read_end, 65536)
                except OSError:
                    return
                if not data:
                    return
                for target in (console, output.fileno()):
                    try:
                        os.write(target, data)
                    except OSError:
                        pass        # no console (the Finder): the file still gets it
        thread = threading.Thread(target=copy, name=f'console {fd}', daemon=True)
        thread.start()
        _copiers.append((fd, console, thread))
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)     # a pipe now: written line by line, not at exit
        except (AttributeError, ValueError):
            pass
    import atexit
    atexit.register(_drain_console)


def _drain_console():
    """At exit: what is still in the pipes is copied before the process ends (the console back on 1 and 2
    closes their write ends, the copying threads then end at the end of what was written)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    try:
        import ctypes
        ctypes.CDLL(None).fflush(None)      # C stdio buffers (stdout to a pipe is fully buffered)
    except Exception:
        pass
    for fd, console, thread in _copiers:
        try:
            os.dup2(console, fd)
        except OSError:
            pass
    for fd, console, thread in _copiers:
        thread.join(1.0)


def _unhandled(kind, value, trace):
    """An exception not caught (PyQt6 would abort the application): written at once to console.log (the
    copying thread could be too late) and to cutemusic.log, and the application goes on."""
    text = ''.join(traceback.format_exception(kind, value, trace))
    console = next((console for fd, console, thread in _copiers if fd == 2), 2)     # the terminal, not through the pipe
    try:
        os.write(console, text.encode())
    except OSError:
        pass
    if _console_file is not None:
        try:
            _console_file.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} unhandled exception\n{text}'.encode())
        except OSError:
            pass
    for handler in log.handlers:
        if isinstance(handler, logging.FileHandler):
            handler.emit(logging.LogRecord(log.name, logging.ERROR, __file__, 0, 'Unhandled exception\n%s', (text,), None))


def init():
    """In the GUI thread, before anything else: the config folder, the logs and the invoker."""
    global _invoker
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    _tee_console(os.path.join(CONFIG_DIR, 'console.log'))
    log.setLevel(logging.INFO)
    if not log.handlers:
        _rotate(os.path.join(CONFIG_DIR, 'cutemusic.log'))
        formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
        for handler in (logging.StreamHandler(), logging.FileHandler(os.path.join(CONFIG_DIR, 'cutemusic.log'))):
            handler.setFormatter(formatter)
            log.addHandler(handler)
    sys.excepthook = _unhandled
    threading.excepthook = lambda arguments: _unhandled(arguments.exc_type, arguments.exc_value, arguments.exc_traceback)
    _invoker = _Invoker()


def settings():
    """The window and view settings (the library caches are in libraries/, one a music folder)."""
    global _settings
    if _settings is None:
        _settings = QSettings(os.path.join(CONFIG_DIR, 'ui.conf'), QSettings.Format.IniFormat)
    return _settings


def call_in_gui_thread(function, *args, **kw):
    _invoker.invoke.emit(lambda: function(*args, **kw))


def bring_to_front(dialog, opener=None):
    """Over the window it was opened from (opener), and active. The dialog is the opener's child (a window without a
    parent, or made transient once shown, is left behind it on Wayland: GNOME ignores raising it); opened again from
    another window, it moves to that one (shown again: a window changing parent is hidden). Raised again once the
    menu that opened it is gone."""
    from PyQt6.QtCore import QTimer
    if opener is not None and dialog.parentWidget() is not opener:
        visible = dialog.isVisible()
        dialog.setParent(opener, dialog.windowFlags())
        if visible:
            dialog.show()
    if not dialog.isVisible():
        dialog.show()
    def front():
        if dialog.isMinimized():
            dialog.showNormal()
        dialog.raise_()
        dialog.activateWindow()
    front()
    QTimer.singleShot(0, front)


def adopt_dialogs(window, heir):
    """window is closing: the dialogs it is the parent of go to heir (another window), else close."""
    from PyQt6.QtWidgets import QDialog
    for dialog in window.findChildren(QDialog, options=Qt.FindChildOption.FindDirectChildrenOnly):
        if not dialog.isVisible():
            continue
        if heir is not None:
            dialog.setParent(heir, dialog.windowFlags())
            dialog.show()
        else:
            dialog.close()


# Showing files

REVEAL_LABEL = 'Open in Finder' if sys.platform == 'darwin' else 'Show in file manager'


def reveal(paths):
    """The file manager on paths: one file is shown selected in its folder; files of one folder (an album), that
    folder opened; of several (an artist, a genre), the folder they have in common."""
    import subprocess
    paths = [path for path in paths if path]
    if not paths:
        return
    folders = {os.path.dirname(path) for path in paths}
    if len(paths) == 1:
        target, select = paths[0], True
    else:
        target, select = (folders.pop() if len(folders) == 1 else os.path.commonpath(sorted(folders))), False
    try:
        if sys.platform == 'darwin':
            subprocess.Popen(['open', '-R', target] if select else ['open', target])
            return
        if sys.platform == 'win32':     # Explorer: the file selected in its folder, or the folder
            subprocess.Popen(['explorer', f'/select,{os.path.normpath(target)}'] if select else ['explorer', os.path.normpath(target)])
            return
        if select:      # the freedesktop file manager interface (Nautilus, Dolphin, Nemo...) selects the file
            from PyQt6.QtCore import QUrl
            uri = QUrl.fromLocalFile(target).toString(QUrl.ComponentFormattingOption.FullyEncoded)
            result = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.FileManager1',
                                     '--object-path', '/org/freedesktop/FileManager1',
                                     '--method', 'org.freedesktop.FileManager1.ShowItems', f"['{uri}']", ''],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            if result.returncode == 0:
                return
            target = os.path.dirname(target)
        subprocess.Popen(['xdg-open', target])
    except (OSError, subprocess.SubprocessError) as e:
        log.warning(f'Cannot show {target}: {e}')


def self_executable():
    """The program that runs this application again with arguments of its own (--negatives, --tunnel): a standalone
    build's own executable (the Windows .exe; on macOS the app's, Contents/MacOS/<CFBundleExecutable>, not py2app's
    Contents/MacOS/python next to it, which is only the interpreter), else None (run from a checkout)."""
    import sys
    if not getattr(sys, 'frozen', False):
        return None
    if sys.platform == 'darwin':
        import plistlib
        contents = os.path.dirname(os.path.dirname(sys.executable))
        try:
            with open(os.path.join(contents, 'Info.plist'), 'rb') as file:
                return os.path.join(contents, 'MacOS', plistlib.load(file)['CFBundleExecutable'])
        except (OSError, KeyError, ValueError):
            return None
    return sys.executable


def on_screen(widget):
    """Whether widget can be seen now: shown, not covered entirely, its window neither minimized nor unmapped. The
    animated views draw and compute only then (a hidden or minimized view uses no CPU)."""
    if not widget.isVisible() or widget.visibleRegion().isEmpty():
        return False
    window = widget.window()
    handle = window.windowHandle()
    return not window.isMinimized() and (handle is None or handle.isExposed())
