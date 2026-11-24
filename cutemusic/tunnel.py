"""The web page (web.py) on a public port: a shootback reverse TCP tunnel (../shootback, its slaver) to a master on
the Internet, as run by hand:

    python3 shootback/slaver.py -m rtunnels.dns-hosting.info:20049 -t 127.0.0.1:47101 -k <password>

File -> Web libraries..., "Public address (shootback tunnel)" (settings web/tunnel/...): on, the master
(host:port), its password, and the page's public address there (shown, and in the emails). The slaver runs in a
process of its own while the page is served (shootback has no way to stop it but that): this python running
../shootback/slaver.py with those arguments, or, in a standalone build, the application itself with --tunnel
(main() here, run by app.main). Started again when it ends on its own (RETRY s later); its output in the log.
"""

import os
import runpy
import sys

import subprocess
import threading

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from cutemusic import util
from cutemusic.util import log


__all__ = ['Tunnel', 'tunnel_settings', 'set_tunnel_settings', 'SLAVER', 'main']


SLAVER = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), 'shootback', 'slaver.py')
RETRY = 5000        # ms
# the slaver keeps 5 spare connections to the master, each used by one visitor: connected again and again with no
# visitors (CHURN of them in CHURN_WINDOW s) is the master closing them at the handshake, the password not its own
CHURN, CHURN_WINDOW = 20, 10.0


def tunnel_settings():
    settings = util.settings()
    return {'on': settings.value('web/tunnel/on', False, type=bool), 'master': settings.value('web/tunnel/master', '', type=str),
            'password': settings.value('web/tunnel/password', '', type=str), 'public': settings.value('web/tunnel/public', '', type=str)}


def set_tunnel_settings(values):
    settings = util.settings()
    for key, value in values.items():
        settings.setValue(f'web/tunnel/{key}', value)


def _master_ok(master):
    host, _, port = master.rpartition(':')
    return bool(host) and port.isdigit() and 0 < int(port) < 65536


class Tunnel(QObject):
    """The slaver's process. changed(): its state (state: 'off', 'starting', 'connected', 'failed'; message)."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.target = None          # the local port tunnelled to
        self.state, self.message = 'off', ''
        self._retry = QTimer(self, singleShot=True, interval=RETRY)
        self._retry.timeout.connect(self._launch)
        self._connects = []         # when the slaver connected to the master lately (time.monotonic())
        self._used = 0.0            # when a visitor was last tunnelled (a handshake)
        self._refused = False
        from PyQt6.QtWidgets import QApplication
        if QApplication.instance() is not None:
            QApplication.instance().aboutToQuit.connect(self.stop)

    def _set(self, state, message=''):
        if (state, message) != (self.state, self.message):
            self.state, self.message = state, message
            self.changed.emit()

    def start(self, port):
        """Tunnelled to this computer's port (the page's), when on in the settings; again with them as they are now."""
        self.stop()
        settings = tunnel_settings()
        if not settings['on']:
            return
        if not _master_ok(settings['master']):
            self._set('failed', 'set the server as host:port')
            return
        if not getattr(sys, 'frozen', False) and not os.path.isfile(SLAVER):
            self._set('failed', f'shootback is not there ({SLAVER})')
            return
        self.target = port
        self._launch()

    def _launch(self):
        settings = tunnel_settings()
        if self.target is None or not settings['on']:
            return
        arguments = ['-m', settings['master'], '-t', f'127.0.0.1:{self.target}'] + (['-k', settings['password']] if settings['password'] else [])
        if util.self_executable():       # a standalone build: itself, as the slaver
            program, arguments = util.self_executable(), ['--tunnel'] + arguments
        else:
            program, arguments = sys.executable, ['-u', SLAVER] + arguments
        # a plain subprocess read by a thread of its own, not a QProcess: stopped while its output was coming, a
        # QProcess's socket notifiers could be called once deleted (macOS: a crash on quitting)
        self._set('starting', f'connecting to {settings["master"]}')
        log.info(f'[tunnel] to {settings["master"]}, for 127.0.0.1:{self.target}')
        try:
            process = self.process = subprocess.Popen([program] + arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                                      stderr=subprocess.STDOUT, bufsize=1, text=True, errors='replace')
        except OSError as e:
            self.process = None
            self._finished(None, f'not started: {e}')
            return

        def read():
            for line in process.stdout:
                util.call_in_gui_thread(self._output, process, line)
            code = process.wait()
            util.call_in_gui_thread(self._finished, process, code)
        threading.Thread(target=read, name='tunnel', daemon=True).start()

    def _output(self, process, line):
        """(The GUI thread) a line of the slaver's output: its state from it; not every line in the log (a line a
        connection to the master, several a second when refused): the others, and the changes."""
        import time
        if process is not self.process:
            return
        for line in [line]:
            line = line.strip()
            if not line:
                continue
            now = time.monotonic()
            if 'connected to master' in line:
                self._connects = [when for when in self._connects if now - when < CHURN_WINDOW] + [now]
                refused = len(self._connects) >= CHURN and now - self._used > CHURN_WINDOW
                if refused and not self._refused:
                    log.warning(f'[tunnel] the master closes every connection at the handshake: its -k is not this '
                                f'password ({len(self._connects)} connections in {CHURN_WINDOW:.0f} s)')
                elif not refused and self._refused:
                    log.info('[tunnel] the master takes the connections again')
                self._refused = refused
                if refused:
                    self._set('failed', 'the server refuses this password (its -k)')
                else:
                    if self.state != 'connected':
                        log.info(f'[tunnel] {line}')
                    self._set('connected', '')
            elif 'Success master handshake' in line:
                self._used = now                # a visitor: the connections used, not refused
            elif 'unable to connect master' in line:
                if self.state != 'starting':
                    log.info(f'[tunnel] {line}')
                self._set('starting', line.split('unable to connect master', 1)[1].strip() or 'the server is not answering')
            else:
                log.info(f'[tunnel] {line}')

    def _finished(self, process, code):
        if process is not self.process and process is not None:
            return
        self.process = None
        log.warning(f'[tunnel] ended ({code}): started again in {RETRY // 1000} s')
        self._set('failed', f'the tunnel ended ({code}): trying again')
        self._retry.start()

    def stop(self):
        self._retry.stop()
        self.target = None
        process, self.process = self.process, None
        if process is not None:         # (its reader then ends: what it hands on is for a process no longer ours)
            process.terminate()
            try:
                process.wait(2)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(1)
                except subprocess.TimeoutExpired:
                    pass
            log.info('[tunnel] stopped')
        self._set('off')


def main(arguments):
    """ag-cute-music --tunnel -m host:port -t host:port [-k password] (a standalone build): the slaver, as
    ../shootback/slaver.py run by hand."""
    folder = os.path.dirname(SLAVER)
    sys.path.insert(0, folder)
    sys.argv = [SLAVER] + arguments
    runpy.run_path(SLAVER, run_name='__main__')
