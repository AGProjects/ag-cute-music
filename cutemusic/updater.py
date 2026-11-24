"""Updates of the standalone Mac app, as Sparkle does for Blink: an appcast next to the disk images
(https://download.ag-projects.com/Cute/AGCuteMusicAppcast.xml, made by macos/make-appcast.py, uploaded by
macos/upload.sh), checked at start and once a day (or File -> Check for Updates...).

- The appcast is Sparkle's format: one <item> for the latest version (sparkle:shortVersionString, release notes in
  <description>, sparkle:minimumSystemVersion), an <enclosure> per build: ag:arch universal2 or x86_64 (the app's
  AGCuteMusicBuild in its Info.plist: the Intel app stays on the Intel image), with ag:sha256.
- A newer version: a dialog with its notes, Install Update / Skip This Version / Remind Me Later, and "check
  automatically".
- Installed: the image downloaded and checked against ag:sha256, mounted (not shown in Finder), its app checked
  (same bundle id, the version announced, codesign --verify --deep --strict, the same Developer ID team as this app,
  notarized: spctl) and copied beside this app; then we quit, and once we are gone a detached shell swaps it in (as
  root, after the administrator's password, only when the app's folder is not writable) and starts it. Nothing of
  the running app moves while it runs.
- Only the standalone app, Developer ID signed (macos/standalone.py sets AG_CUTEMUSIC_BUNDLE): not from a checkout,
  not an alias or ad hoc build.
"""

import hashlib
import os
import platform
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import xml.etree.ElementTree as ElementTree

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
                             QPushButton, QTextBrowser, QVBoxLayout)

from cutemusic import APP_NAME, __version__, util
from cutemusic.util import log


__all__ = ['Updater', 'supported', 'FEED_URL']


FEED_URL = 'https://download.ag-projects.com/Cute/AGCuteMusicAppcast.xml'
CHECK_EVERY = 24 * 3600         # s
FIRST_CHECK = 15                # s after start
SPARKLE = '{http://www.andymatuschak.org/xml-namespaces/sparkle}'
AG = '{https://ag-projects.com/xml-namespaces/cute}'


def bundle():
    """This app's bundle (the standalone app), else None."""
    path = os.environ.get('AG_CUTEMUSIC_BUNDLE')
    return path if sys.platform == 'darwin' and path and os.path.isdir(path) else None


def _info(app):
    with open(os.path.join(app, 'Contents', 'Info.plist'), 'rb') as file:
        return plistlib.load(file)


def _team(app):
    """The Developer ID team that signed app, None when ad hoc or unsigned."""
    try:
        output = subprocess.run(['codesign', '-dv', '--verbose=2', app], capture_output=True, text=True, timeout=60).stderr
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r'^TeamIdentifier=(\S+)', output, re.M)
    return match.group(1) if match and match.group(1) != 'not' else None


_own_team = Ellipsis


def supported():
    """Updates for this app: the standalone app, signed with a Developer ID."""
    global _own_team
    if bundle() is None:
        return False
    if _own_team is Ellipsis:
        _own_team = _team(bundle())
    return _own_team is not None


def _key(version):
    return tuple(int(part) if part.isdigit() else 0 for part in re.split(r'[.\-]', version or '0'))


def _ssl_context():
    from cutemusic.online import _context
    return _context()


class Release:
    def __init__(self, version, notes, url, sha256, length, minimum):
        self.version, self.notes, self.url, self.sha256, self.length, self.minimum = version, notes, url, sha256, length, minimum


def _parse(data, arch):
    """The appcast's latest release with an image for our build (arch), else None."""
    root = ElementTree.fromstring(data)
    best = None
    for item in root.iter('item'):
        version = (item.findtext(f'{SPARKLE}shortVersionString') or item.findtext(f'{SPARKLE}version') or '').strip()
        if not version or (best is not None and _key(version) <= _key(best.version)):
            continue
        for enclosure in item.findall('enclosure'):
            if enclosure.get(f'{SPARKLE}os', 'macos') != 'macos' or enclosure.get(f'{AG}arch', 'universal2') != arch:
                continue
            if not enclosure.get('url', '').startswith('https://') or not enclosure.get(f'{AG}sha256'):
                continue
            best = Release(version, item.findtext('description') or '', enclosure.get('url'),
                           enclosure.get(f'{AG}sha256').lower(), int(enclosure.get('length') or 0),
                           (item.findtext(f'{SPARKLE}minimumSystemVersion') or '').strip())
            break
    return best


def _fetch(url, timeout=30):
    request = urllib.request.Request(url, headers={'User-Agent': f'{APP_NAME}/{__version__}'})
    return urllib.request.urlopen(request, timeout=timeout, context=_ssl_context())


class Updater(QObject):
    """The checks, the dialog and the install. window: the main window (closed to restart)."""

    _checked = pyqtSignal(object, str, bool)        # (Release or None, error, manual): from the check's thread
    _progress = pyqtSignal(int, int)                # (done, total) bytes
    _installed = pyqtSignal(object, object, str)    # (staged app or None, its temporary folder, error)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.dialog = None
        self._busy = False
        self._checked.connect(self._on_checked)
        self._progress.connect(self._on_progress)
        self._installed.connect(self._on_installed)
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: self.check(manual=False))

    # Checking

    @staticmethod
    def automatic():
        return util.settings().value('updates/automatic', True, type=bool)

    @staticmethod
    def set_automatic(on):
        util.settings().setValue('updates/automatic', on)

    def start(self):
        """Checks at start (soon after) and every CHECK_EVERY, while automatic."""
        if not supported():
            return
        self._timer.start(3600 * 1000)          # hourly: checks when the last one is CHECK_EVERY old
        QTimer.singleShot(FIRST_CHECK * 1000, lambda: self.check(manual=False))

    def check(self, manual=True):
        if not supported():
            if manual:
                QMessageBox.information(self.window, 'Check for Updates',
                                        'Updates come with the AG CuteMusic app from download.ag-projects.com '
                                        '(signed): not for this one (from a checkout, or built here).')
            return
        if self._busy:
            if manual and self.dialog is not None:
                util.bring_to_front(self.dialog, self.window)
            return
        settings = util.settings()
        if not manual:
            if not self.automatic():
                return
            if time.time() - settings.value('updates/last_check', 0, type=float) < CHECK_EVERY:
                return
        settings.setValue('updates/last_check', time.time())
        self._busy = True
        arch = str(_info(bundle()).get('AGCuteMusicBuild') or 'universal2')

        def run():
            try:
                with _fetch(FEED_URL) as response:
                    release = _parse(response.read(), arch)
                self._checked.emit(release, '', manual)
            except Exception as e:
                self._checked.emit(None, str(e), manual)
        threading.Thread(target=run, name='update-check', daemon=True).start()

    def _on_checked(self, release, error, manual):
        self._busy = False
        if error:
            log.warning(f'[updates] cannot read {FEED_URL}: {error}')
            if manual:
                QMessageBox.warning(self.window, 'Check for Updates', f'Cannot check for updates: {error}')
            return
        if release is None or _key(release.version) <= _key(__version__):
            log.info(f'[updates] {APP_NAME} {__version__} is the latest')
            if manual:
                QMessageBox.information(self.window, 'Check for Updates', f'{APP_NAME} {__version__} is the latest version.')
            return
        if release.minimum and _key(platform.mac_ver()[0]) < _key(release.minimum):
            log.info(f'[updates] {release.version} needs macOS {release.minimum}')
            if manual:
                QMessageBox.information(self.window, 'Check for Updates',
                                        f'{APP_NAME} {release.version} is out, for macOS {release.minimum} and later.')
            return
        if not manual and util.settings().value('updates/skipped') == release.version:
            return
        log.info(f'[updates] {release.version} is available ({release.url})')
        self._show(release)

    # The dialog

    def _show(self, release):
        if self.dialog is not None:
            self.dialog.close()
        dialog = self.dialog = QDialog(self.window)
        dialog.setWindowTitle('Software Update')
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.destroyed.connect(lambda: setattr(self, 'dialog', None))
        layout = QVBoxLayout(dialog)
        title = QLabel(f'<b>A new version of {APP_NAME} is available</b>')
        layout.addWidget(title)
        layout.addWidget(QLabel(f'{APP_NAME} {release.version} is available: you have {__version__}. Install it now?'))
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setHtml(release.notes or '<i>No release notes.</i>')
        notes.setMinimumSize(460, 200)
        layout.addWidget(notes)
        self._bar = QProgressBar()
        self._bar.hide()
        layout.addWidget(self._bar)
        self._status = QLabel()
        self._status.hide()
        layout.addWidget(self._status)
        automatic = QCheckBox('Check for updates automatically')
        automatic.setChecked(self.automatic())
        automatic.toggled.connect(self.set_automatic)
        layout.addWidget(automatic)
        buttons = QHBoxLayout()
        skip = QPushButton('Skip This Version')
        later = QPushButton('Remind Me Later')
        install = QPushButton('Install Update')
        install.setDefault(True)
        buttons.addWidget(skip)
        buttons.addStretch(1)
        buttons.addWidget(later)
        buttons.addWidget(install)
        layout.addLayout(buttons)
        self._buttons = (skip, later, install)

        def skip_it():
            util.settings().setValue('updates/skipped', release.version)
            dialog.close()
        skip.clicked.connect(skip_it)
        later.clicked.connect(dialog.close)
        install.clicked.connect(lambda: self._install(release))
        util.bring_to_front(dialog, self.window)

    def _on_progress(self, done, total):
        if self.dialog is None:
            return
        self._bar.setRange(0, total or 0)
        self._bar.setValue(done)
        self._status.setText(f'Downloading: {done / 1e6:.1f} of {total / 1e6:.1f} MB' if total else
                             f'Downloading: {done / 1e6:.1f} MB')

    def _set_status(self, text):
        if self.dialog is not None:
            self._status.setText(text)

    # Installing

    def _install(self, release):
        if self._busy:
            return
        self._busy = True
        self._release = release
        for button in self._buttons:
            button.setEnabled(False)
        self._bar.show()
        self._status.show()
        self._set_status('Downloading...')
        threading.Thread(target=self._download_and_check, args=(release,), name='update-install', daemon=True).start()

    def _download_and_check(self, release):
        """In a thread: the image downloaded and checked, its app checked and staged beside ours."""
        work = tempfile.mkdtemp(prefix='ag-cute-music-update-')
        mount = os.path.join(work, 'mount')
        image = os.path.join(work, 'update.dmg')
        attached = False
        try:
            digest = hashlib.sha256()
            with _fetch(release.url, timeout=60) as response, open(image, 'wb') as file:
                total = int(response.headers.get('Content-Length') or release.length or 0)
                done = last = 0
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    file.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if time.monotonic() - last > 0.1:
                        last = time.monotonic()
                        self._progress.emit(done, total)
                self._progress.emit(done, total)
            if digest.hexdigest() != release.sha256:
                raise RuntimeError('the download does not match the appcast (sha256): try again later')
            util.call_in_gui_thread(self._set_status, 'Checking the update...')
            os.mkdir(mount)
            subprocess.run(['hdiutil', 'attach', '-nobrowse', '-readonly', '-noautoopen', '-mountpoint', mount, image],
                           check=True, capture_output=True, timeout=300)
            attached = True
            apps = [name for name in os.listdir(mount) if name.endswith('.app')]
            if len(apps) != 1:
                raise RuntimeError(f'no single app in the image ({", ".join(apps) or "none"})')
            new = os.path.join(mount, apps[0])
            ours, theirs = _info(bundle()), _info(new)
            if theirs.get('CFBundleIdentifier') != ours.get('CFBundleIdentifier'):
                raise RuntimeError(f'the image holds {theirs.get("CFBundleIdentifier")}, not {APP_NAME}')
            if theirs.get('CFBundleShortVersionString') != release.version:
                raise RuntimeError(f'the image holds {theirs.get("CFBundleShortVersionString")}, not {release.version}')
            check = subprocess.run(['codesign', '--verify', '--deep', '--strict', new], capture_output=True, text=True, timeout=600)
            if check.returncode != 0:
                raise RuntimeError(f'its signature does not verify: {check.stderr.strip()}')
            if _team(new) != _own_team:
                raise RuntimeError(f'it is signed by another team ({_team(new)}, not {_own_team})')
            check = subprocess.run(['spctl', '--assess', '--type', 'execute', new], capture_output=True, text=True, timeout=300)
            if check.returncode != 0:
                raise RuntimeError(f'it is not notarized: {check.stderr.strip()}')
            util.call_in_gui_thread(self._set_status, 'Copying...')
            staged = self._staged_path()
            shutil.rmtree(staged, ignore_errors=True)
            if os.access(os.path.dirname(bundle()), os.W_OK):
                subprocess.run(['ditto', new, staged], check=True, capture_output=True, timeout=600)
            else:       # copied into a folder of our own first: the swap (as root) only moves
                staged = os.path.join(work, apps[0])
                subprocess.run(['ditto', new, staged], check=True, capture_output=True, timeout=600)
            self._installed.emit(staged, work, '')
        except subprocess.CalledProcessError as e:
            self._installed.emit(None, work, f'{" ".join(e.cmd[:2])}: {(e.stderr or b"").decode("utf-8", "replace").strip() or e}')
        except Exception as e:
            self._installed.emit(None, work, str(e))
        finally:
            if attached:
                subprocess.run(['hdiutil', 'detach', mount, '-force', '-quiet'], capture_output=True, timeout=120)
            try:
                os.unlink(image)
            except OSError:
                pass

    @staticmethod
    def _staged_path():
        app = bundle()
        return os.path.join(os.path.dirname(app), f'.{os.path.basename(app)}.update-{os.getpid()}')

    def _on_installed(self, staged, work, error):
        if error:
            self._busy = False
            shutil.rmtree(work, ignore_errors=True)
            log.warning(f'[updates] not installed: {error}')
            QMessageBox.warning(self.dialog or self.window, 'Software Update', f'The update was not installed: {error}')
            if self.dialog is not None:
                for button in self._buttons:
                    button.setEnabled(True)
                self._bar.hide()
                self._status.hide()
            return
        self._set_status('Installing: AG CuteMusic restarts...')
        self._swap(staged, work)

    def _swap(self, staged, work):
        """Once we have quit, a detached shell sets our app aside, moves the new one in, removes the old one and
        starts the new one; as root (one password, asked now) when the app's folder is not ours to write."""
        app = bundle()
        aside = os.path.join(os.path.dirname(app), f'.{os.path.basename(app)}.old-{os.getpid()}')
        pid = os.getpid()
        q = shlex.quote
        wait = f'while kill -0 {pid} 2>/dev/null; do sleep 0.3; done'
        swap = (f'{wait}; rm -rf {q(aside)} && mv {q(app)} {q(aside)} && mv {q(staged)} {q(app)} && rm -rf {q(aside)}; '
                f'rm -rf {q(work)}')
        # started again once swapped (the staged copy gone), or after 2 minutes whatever happened: the old one back
        start = f'{wait}; i=0; while [ -e {q(staged)} ] && [ $i -lt 400 ]; do sleep 0.3; i=$((i+1)); done; open {q(app)}'

        # (without our GStreamer and Python variables: the new app, opened by this shell, sets its own)
        clean = {name: value for name, value in os.environ.items()
                 if not name.startswith(('GST_', 'GI_', 'GIO_', 'PYTHON', 'RESOURCEPATH', 'ARGVZERO', 'EXECUTABLEPATH',
                                         'AG_CUTEMUSIC_BUNDLE')) and name != 'LD_LIBRARY_PATH'}

        def detached(script):
            subprocess.Popen(['/bin/sh', '-c', script], start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=clean)

        def quit_now():
            log.info(f'[updates] {self._release.version} ready in {staged}: quitting for the swap into {app}')
            detached(start)
            if self.dialog is not None:
                self.dialog.close()
            self.window.close()
            QTimer.singleShot(0, QApplication.instance().quit)

        if os.access(os.path.dirname(app), os.W_OK):
            detached(swap)
            quit_now()
            return

        def authorized(error):
            if error is None:
                quit_now()
                return
            self._busy = False
            shutil.rmtree(work, ignore_errors=True)
            if error:
                QMessageBox.warning(self.dialog or self.window, 'Software Update', f'The update was not installed: {error}')
            if self.dialog is not None:
                self.dialog.close()
        from cutemusic.driver import run_as_admin
        run_as_admin(f'( {swap} ) > /dev/null 2>&1 &', f'Install {APP_NAME} {self._release.version}.', authorized)
