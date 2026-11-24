"""Negatives for the LAN: a web page (File -> Web libraries...: Enable; port 47101, settings web/port) where anyone
on the LAN, with a token of 5 digits of their choosing, sends songs to have their negatives made here, and gets them
back: http://<this computer>:47101/

- The token is a namespace, not a password: a token's page shows its own songs only (sent, waiting, being made,
  made: its negatives library), and plays and removes only those. For a trusted LAN.
- The page's Library tab: the token's songs, played. Its Negatives tab: the negatives made only (the others' state
  in the Library's Negative column), played. Downloaded, the songs and their negatives alike, only once allowed
  here (the Web tab: Allow download), song by song or a whole token. (Streamed, it can still be recorded: a courtesy, not a lock.)
- A song sent waits: the one in front of AG CuteMusic starts it (the Web tab, its count of songs waiting in its
  name); then it is made as the Negatives tab's (negatives.py: same queue, one at a time), its progress on the page.
- Kept in ~/.ag-cute-music/web/<token>/<job>/: the song sent, and its negative "<name> (negative).flac" when made;
  the jobs in ~/.ag-cute-music/web/jobs.json.
- The page's top: the Amplifier view's front panel, dark plate, drawn once when the server starts (amp.py).
- In the window, each token is also a library of its own in the Library tab's source button ("Web 12345", beside
  the libraries on the LAN): the Library tab then shows the token's songs sent, the Negatives tab its negatives
  (made, or dimmed with their state); Make negative there is Start, Move to Trash removes the jobs.
- Songs are also put in a token's library here: right-click -> Copy to web library (the Library, a playlist;
  the Negatives tab: the songs with their negatives, made at once),
  dropped on the Web tab (from the Library, or a file manager; on a token's row: that token), pasted there (Ctrl+V,
  files copied in a file manager), or Add songs...: copied in, played on the page (its Library tab), their negatives
  not asked for: Start makes them. The songs sent from the page wait for Start.
- A song whose negative is there already is made at once, not made again: the Negatives folder's (a song put
  here from the music folder), or another job's of the same file (its SHA-1), copied as its own; or Set negative...
  in the Web tab, a FLAC chosen.
- The Web tab's right-click: Play (here), Play negative, Start / Stop making negative (the rest of the queue goes
  on), Allow / Forbid download, Set negative..., Remove.

    GET  /                          the page
    GET  /amp.png                   the amplifier's picture (without the page's script)
    GET  /amp-panel.png             the same without the knobs, the needles, the PEAK lamps and the mute button
    GET  /amp.json                  where those are (the page draws and turns them: its own amplifier)
    GET  /api/jobs?token=           {jobs: [{id, name, state, percent, error, added, size, download}]}
    POST /api/upload?token=&name=   the song as the body: a job waiting
    GET  /api/lyrics?token=&id=     {timed: [[seconds, line]] or null, plain: [line] or null}: shown over the player
                                    (not allowed to be downloaded: /api/stream and /api/song serve the middle half
                                    only, PREVIEW, a file of its own)
    GET  /api/stream?token=&id=     the negative, played in the page (ranges served)
    GET  /api/download?token=&id=   the negative as a file: only once allowed here
                                    (&bass=&treble=&balance=: made with the page's amplifier, its selector on Download)
    GET  /api/song?token=&id=       the song itself, played in the page; /api/song-download: as a file, once allowed
    GET  /api/token?token=&email=   {taken: the digits not free, verified: the address}
    GET  /api/email?email=          {verified}: the address verified already (the enrollment skips the code)
    POST /api/code?email=           a code to verify an email address, sent to it
    POST /api/verify?email=&code=   the address verified with it
    POST /api/token?token=&name=&email=&code=   a new token made with its owner's name and email address, verified
                                    (the code, once): {name, key}; 428: a code needed
    POST /api/login?token=&email=   a token opened with the email address it was made with: {name, key}
    POST /api/logout?token=&key=    that key forgotten
                                    (every other request: &key= of a login; else 401)
    POST /api/recover?email=        a forgotten token: the tokens of that address emailed to it (the same answer either way)
    POST /api/request?token=&id=    Request download: a song not downloadable asked to be (Allow download here)
    POST /api/remove?token=&id=     a job not being made, its files gone
"""

import hashlib
import http.server
import json
import math
import os
import re
import shutil
import socketserver
import threading
import time
import urllib.parse
import uuid

from PyQt6.QtCore import QBuffer, QIODevice, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QGridLayout,
                             QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMenu, QMessageBox, QPushButton, QSizePolicy, QSpinBox, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from cutemusic import APP_NAME, __version__, util
from cutemusic.library import AUDIO_EXTENSIONS
from cutemusic.util import log


__all__ = ['WebQueue', 'WebQueueView', 'WebInfo', 'PORT', 'port', 'set_port', 'amp_picture']


PORT = 47101
FOLDER = os.path.join(util.CONFIG_DIR, 'web')
JOBS_FILE = os.path.join(FOLDER, 'jobs.json')
VERIFIED_FILE = os.path.join(FOLDER, 'verified.json')    # the email addresses verified (a code sent, typed back)
OWNERS_FILE = os.path.join(FOLDER, 'tokens.json')     # {token: {name, email}: whose it is, where to notify them}
EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
MAX_SIZE = 1024 * 1024 * 1024       # a song sent: at most 1 GB
PREVIEW = (0.25, 0.75)              # not allowed to be downloaded: only this part of a song (of its length) played
RECOVER_EVERY = 600                 # s: a forgotten token asked for once in this, an address and a client
NOTIFY_DELAY = 60                   # s: a token's news gathered this long (the queue going on) before one email
KEYS = 20                           # a token's logins kept (browsers logged in at once)
LOGIN_TRIES, LOGIN_WINDOW = 10, 600  # a client's wrong email addresses before it waits
CODE_VALID, CODE_TRIES, CODE_EVERY = 900, 5, 60     # an email address's code: good this long, this many tries; resent
TOKEN_FILES = 50                    # a token's library: at most this many songs
TOKEN_BYTES = 200 * 1024 * 1024     # and this much, in all (the songs, not their negatives)
CHUNK = 256 * 1024
TOKEN = re.compile(r'^\d{5}$')
# a job's state; 'song': a song put here, played on the page, its negative not asked for (Start makes it)
STATES = {'song': 'song', 'waiting': 'waiting for Start', 'queued': 'queued', 'making': 'making', 'done': 'made',
          'failed': 'failed'}
STARTABLE = ('song', 'waiting', 'failed')
WAITING_COLOR = '#f0a030'
FAILED_COLOR = '#e05a4f'


def port():
    return util.settings().value('web/port', PORT, type=int)


def set_port(value):
    util.settings().setValue('web/port', int(value))


def _safe_name(name):
    """A file name sent, as a name only (no folders, no control characters), its extension kept."""
    name = os.path.basename((name or '').replace('\\', '/')).strip()
    name = re.sub(r'[\x00-\x1f/:*?"<>|]', '_', name).lstrip('.')
    return name[:180] or 'song'


def _sha1(path):
    try:
        digest = hashlib.sha1()
        with open(path, 'rb') as file:
            for block in iter(lambda: file.read(1 << 20), b''):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return ''


# the page's amplifier's output selector: playing here (the knobs on what plays), or downloading too (the downloads
# made with the bass, treble and balance: ffmpeg)
OUTPUTS = ['streaming', 'download']
OUTPUT_TITLES = {'streaming': 'Streaming', 'download': 'Download'}


def amp_picture(width=1500, height=560):
    """The Amplifier view's front panel, dark plate, drawn off screen, once, in the GUI thread: (the whole picture,
    the panel without what moves, its layout); the page draws the knobs, the needles, the PEAK lamps and the mute
    button over the panel and turns them (the page's own amplifier, in the browser: Web Audio). (b'', b'', None)
    when it cannot be drawn."""
    try:
        from PyQt6.QtCore import QRect
        from PyQt6.QtGui import QPainter
        from PyQt6.QtGui import QFontInfo
        from cutemusic.amp import METERS, WHY_11, AmpView
        view = AmpView()
        view.plate = 'dark'         # (not set_plate: the window's own plate setting left as it is)
        view.amp_size = 1.0
        view.meter_style = 'analog'     # (not set_meter_style: the window's own left as it is)
        view.meter.hide()
        view.web = True                 # no mute; the selector's marks and name: the page's
        view.selector.set_names(OUTPUTS, OUTPUTS[0])    # the page's outputs: playing here, or downloading
        for button in view.size_buttons:
            button.hide()
        view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        view.resize(width, height)
        view.show()
        view.lyrics.hide()
        view._compute_layout()
        crop = view._layout['plate'].adjusted(-12, -8, 12, 16).toAlignedRect().intersected(view.rect())
        whole = view.grab(crop)
        # the panel: the plate, the meter window and faces, the lettering and scales: what does not move on the page
        view._draw_panel()
        panel = view._panel
        ratio = panel.devicePixelRatioF()
        panel = panel.copy(QRect(round(crop.x() * ratio), round(crop.y() * ratio), round(crop.width() * ratio), round(crop.height() * ratio)))
        layout = view._layout
        x0, y0 = crop.x(), crop.y()

        def box(rect):
            return [rect.x() - x0, rect.y() - y0, rect.width(), rect.height()]
        lamp = QColor('#ff3b30')
        dark = lamp.darker(320)
        unit = layout['inner'].height()
        middle = view.volume.centre.x()
        wide = min(unit * 0.7, 2 * (layout['inner'].right() - unit * 0.03 - middle))
        geometry = {
            'width': crop.width(), 'height': crop.height(),
            'inner': box(layout['inner']), 'window': box(layout['window']), 'faces': [box(face) for face in layout['meters']],
            'why': box(view._why) if view._why is not None else None, 'whyUrl': WHY_11,
            'knobs': [{'key': knob.key, 'label': knob.label, 'x': knob.centre.x() - x0, 'y': knob.centre.y() - y0,
                       'r': knob.radius, 'low': knob.low, 'high': knob.high, 'limit': knob.limit, 'step': knob.step,
                       'default': knob.default, 'value': 80 if knob.key == 'volume' else 0.0, 'sweep': knob.SWEEP}
                      for knob in view.knobs],
            'selector': {'x': view.selector.centre.x() - x0, 'y': view.selector.centre.y() - y0, 'r': view.selector.radius,
                         'sweep': view.selector.SWEEP, 'names': OUTPUTS, 'titles': [OUTPUT_TITLES[name] for name in OUTPUTS],
                         'nameBox': [middle - wide / 2 - x0, view.selector.centre.y() + view.selector.radius * 1.4 + unit * 0.01 - y0,
                                     wide, unit * 0.05], 'nameFont': unit * 0.026},
            'lever': {'x': view._lever[0].x() - x0, 'points': [point.y() - y0 for point in view._lever[1]],
                      'hit': box(view._lever[2]), 'styles': [key for key, label in METERS]},
            'meter': box(layout['window'].adjusted(4, 4, -4, -4)), 'meterFont': QFontInfo(view.meter.font()).pixelSize() * 0.85,
            'ink': '#d8dbdf', 'print': '#a4a8af',
            'lamp': {'lit': [lamp.lighter(170).name(), lamp.name()], 'off': [dark.lighter(130).name(), dark.name()],
                     'glow': [lamp.red(), lamp.green(), lamp.blue()]},
            'vu': {key: getattr(AmpView, key) for key in ('VU_MIN', 'VU_MAX', 'SWEEP', 'PEAK_VU', 'HEADROOM', 'TRIM_UP',
                                                           'TRIM_DOWN', 'TRIM_RANGE', 'FLOOR', 'SPRING', 'DAMPING', 'EXTRA',
                                                           'CLICK_RING')},
        }
        view.hide()
        view.deleteLater()
        pictures = []
        for pixmap in (whole, panel):
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            pixmap.save(buffer, 'PNG')
            pictures.append(bytes(buffer.data()))
        return pictures[0], pictures[1], geometry
    except Exception as e:
        log.warning(f'[web] the amplifier not drawn: {e}')
        return b'', b'', None


# The server

class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = f'AGCuteMusic/{__version__}'
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        pass

    def _send(self, code, body, kind):
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code=200):
        self._send(code, json.dumps(data).encode('utf-8'), 'application/json')

    def _error(self, code, message):
        self._json({'error': message}, code)

    def _query(self):
        url = urllib.parse.urlsplit(self.path)
        return url.path, {key: values[0] for key, values in urllib.parse.parse_qs(url.query).items()}

    def _site(self):
        """The page's address as the browser asked for it (its Host: the LAN's, or the tunnel's public one)."""
        host = (self.headers.get('Host') or '').strip()
        return f'http://{host}/' if host and re.match(r'^[\w.\-\[\]:]+$', host) else ''

    def _token(self, query, key=True):
        """The request's token, logged in (its key; key: not asked for): else None, the error sent."""
        token = query.get('token', '')
        if not TOKEN.match(token):
            self._error(400, 'the token is 5 digits')
            return None
        if key and not self.server.web.check(token, query.get('key', '')):
            self._error(401, 'log in again: the token and its email address')
            return None
        return token

    def do_GET(self):
        path, query = self._query()
        web = self.server.web
        try:
            if path in ('/', '/index.html'):
                self._send(200, PAGE.encode('utf-8'), 'text/html; charset=utf-8')
            elif path in ('/amp.png', '/amp-panel.png'):
                picture = web.picture if path == '/amp.png' else web.panel_picture
                if picture:
                    self._send(200, picture, 'image/png')
                else:
                    self.send_error(404)
            elif path == '/amp.json':
                if web.amp_layout:
                    self._json(web.amp_layout)
                else:
                    self.send_error(404)
            elif path == '/api/jobs':
                token = self._token(query)
                if token is not None:
                    self._json({'jobs': web.jobs_of(token), 'app': APP_NAME, 'version': __version__,
                                'limits': {'files': TOKEN_FILES, 'bytes': TOKEN_BYTES,
                                           'extensions': sorted(AUDIO_EXTENSIONS)}})
            elif path == '/api/email':
                self._json({'verified': web.verified_email(query.get('email', ''))})
            elif path == '/api/token':
                token = self._token(query, key=False)
                if token is not None:
                    self._json({'taken': web.taken(token), 'verified': web.verified_email(query.get('email', ''))})
            elif path == '/api/lyrics':
                token = self._token(query)
                if token is not None:
                    self._json(web.lyrics(token, query.get('id', '')))
            elif path in ('/api/stream', '/api/download', '/api/song', '/api/song-download'):
                token = self._token(query)
                if token is None:
                    return
                job = web.job(token, query.get('id', ''))
                song = path.startswith('/api/song')
                file = job and (job['upload'] if song else job['result'])
                if job is None or (not song and job['state'] != 'done') or not os.path.isfile(file):
                    self._error(404, 'not there' if song else 'not made')
                elif path.endswith('download') and not job.get('download'):     # (the song and its negative alike)
                    self._error(403, 'download not allowed: ask the one running AG CuteMusic')
                elif path.endswith('download') and _tone(query):
                    self._amp_download(file, _tone(query))
                elif not path.endswith('download') and not job.get('download'):
                    # played only: its middle half (PREVIEW), made apart: the rest of the file never served
                    preview = web.preview(job, song)
                    if preview is None:
                        self._error(503, 'cannot be played: ffmpeg is needed on the computer running AG CuteMusic')
                    else:
                        self._file(preview, attachment=False)
                else:
                    self._file(file, attachment=path.endswith('download'))
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        path, query = self._query()
        web = self.server.web
        try:
            if path == '/api/upload':
                self._upload(query)
            elif path == '/api/recover':
                self._drain()
                error = web.recover(query.get('email', ''), self.client_address[0])
                if error:
                    self._error(429 if 'wait' in error else 503 if 'not set up' in error else 400, error)
                else:       # (the same answer whether the address has tokens or not)
                    self._json({'ok': True})
            elif path == '/api/token':
                self._drain()
                token = self._token(query, key=False)
                if token is None:
                    return
                error, key = web.register(token, query.get('name', ''), query.get('email', ''), query.get('code', ''), self._site())
                if error == 'verify':
                    self._error(428, 'verify your email address: the code sent to it')
                elif error:
                    self._error(409, error)
                else:
                    self._json({'name': web.owner(token), 'key': key})
            elif path == '/api/verify':
                self._drain()
                error = web.verify(query.get('email', ''), query.get('code', ''))
                if error:
                    self._error(400, error)
                else:
                    self._json({'ok': True})
            elif path == '/api/code':
                self._drain()
                error = web.send_code(query.get('email', ''), self.client_address[0], self._site())
                if error:
                    self._error(429 if 'moment' in error else 503 if 'not set up' in error else 400, error)
                else:
                    self._json({'ok': True})
            elif path == '/api/login':
                self._drain()
                token = self._token(query, key=False)
                if token is None:
                    return
                status, key, name = web.login(token, query.get('email', ''), self.client_address[0])
                if status == 200:
                    self._json({'name': name, 'key': key})
                else:
                    self._error(status, {404: 'no such token: make it (New token)',
                                         403: 'this email address is not the one the token was made with',
                                         429: 'too many tries: wait a few minutes'}[status])
            elif path == '/api/logout':
                self._drain()
                web.logout(query.get('token', ''), query.get('key', ''))
                self._json({'ok': True})
            elif path == '/api/request':
                self._drain()
                token = self._token(query)
                if token is None:
                    return
                if web.request(token, query.get('id', '')):
                    self._json({'ok': True})
                else:
                    self._error(409, 'not asked for: allowed already, or asked for already')
            elif path == '/api/remove':
                self._drain()
                token = self._token(query)
                if token is None:
                    return
                if web.remove_remote(token, query.get('id', '')):
                    self._json({'ok': True})
                else:
                    self._error(409, 'not removed: being made, or not there')
            else:
                self._drain()
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _drain(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        if 0 < length <= 65536:
            self.rfile.read(length)
        elif length:
            self.close_connection = True

    def _refuse(self, code, message):
        self.close_connection = True        # (the body not read)
        self._error(code, message)

    def _upload(self, query):
        token = query.get('token', '')
        if not TOKEN.match(token):
            return self._refuse(400, 'the token is 5 digits')
        if not self.server.web.check(token, query.get('key', '')):
            return self._refuse(401, 'log in again: the token and its email address')
        name = _safe_name(query.get('name', ''))
        if os.path.splitext(name)[1].lower() not in AUDIO_EXTENSIONS:
            return self._refuse(415, 'not a music file (' + ' '.join(sorted(AUDIO_EXTENSIONS)) + ')')
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_SIZE:
            return self._refuse(413, f'empty, or more than {MAX_SIZE // 2**20} MB')
        refused = self.server.web.reserve(token, length)
        if refused:
            return self._refuse(413, refused)
        try:
            self._receive(token, name, length)
        finally:
            self.server.web.release(token, length)

    def _receive(self, token, name, length):
        identifier = uuid.uuid4().hex[:12]
        folder = os.path.join(FOLDER, token, identifier)
        upload = os.path.join(folder, name)
        partial = upload + '.part'
        left = length
        try:
            os.makedirs(folder, mode=0o700, exist_ok=True)
            with open(partial, 'wb') as file:
                while left > 0:
                    data = self.rfile.read(min(CHUNK, left))
                    if not data:
                        break
                    if left == length and not _sniff(data):       # its first bytes: not a music file's
                        file.close()
                        shutil.rmtree(folder, ignore_errors=True)
                        log.info(f'[web] {name} for {token} refused: not a music file')
                        return self._refuse(415, f'{name} is not a music file')
                    file.write(data)
                    left -= len(data)
            if left:            # the sender gone
                shutil.rmtree(folder, ignore_errors=True)
                self.close_connection = True
                return
            os.replace(partial, upload)
        except OSError as e:
            shutil.rmtree(folder, ignore_errors=True)
            return self._refuse(500, str(e))
        if not _playable(upload):         # read as music (its format, a length): else not kept
            shutil.rmtree(folder, ignore_errors=True)
            log.info(f'[web] {name} for {token} refused: not readable as music')
            return self._error(415, f'{name} is not a music file (or it is damaged)')
        stem = os.path.splitext(name)[0]
        job = self.server.web.add_remote(token, identifier, name, upload, os.path.join(folder, f'{stem} (negative).flac'), length)
        self._json({'job': job})

    def _amp_download(self, source, tone):
        """A download made with the page's amplifier (its selector on Download): bass, treble (shelves at 100 Hz and
        11 kHz, as the page's) and balance (the other side turned down) by ffmpeg, as a FLAC with the tags and the
        cover; named "<name> (amp).flac"."""
        if shutil.which('ffmpeg') is None:
            self._error(503, 'downloads with the amplifier need ffmpeg on the computer running AG CuteMusic')
            return
        import subprocess
        import tempfile
        balance = tone['balance']
        left, right = (1 - balance if balance > 0 else 1.0), (1 + balance if balance < 0 else 1.0)
        chain = (f'aformat=channel_layouts=stereo,bass=g={tone["bass"]}:f=100:t=s:w=1,'
                 f'treble=g={tone["treble"]}:f=11000:t=s:w=1,pan=stereo|c0={left:.4f}*c0|c1={right:.4f}*c1')
        os.makedirs(FOLDER, mode=0o700, exist_ok=True)
        handle, temporary = tempfile.mkstemp(suffix='.flac', dir=FOLDER)
        os.close(handle)
        try:
            for cover in (['-map', '0:v?', '-c:v', 'copy'], []):        # with its cover, else without
                command = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', source, '-map', '0:a:0'] + cover + \
                          ['-map_metadata', '0', '-af', chain, '-c:a', 'flac', temporary]
                result = subprocess.run(command, capture_output=True, text=True, timeout=900)
                if result.returncode == 0:
                    break
            else:
                log.warning(f'[web] {source}: not made with the amplifier: {result.stderr.strip()[-300:]}')
                self._error(500, 'not made with the amplifier (ffmpeg failed)')
                return
            log.info(f'[web] {os.path.basename(source)} downloaded with the amplifier: {tone}')
            self._file(temporary, attachment=True, name=os.path.splitext(os.path.basename(source))[0] + ' (amp).flac')
        except subprocess.TimeoutExpired:
            self._error(504, 'not made with the amplifier in time')
        finally:
            try:
                os.remove(temporary)
            except OSError:
                pass

    def _file(self, path, attachment, name=None):
        """The negative: as a file (attachment), or played (inline, ranges served: the player seeks); named name, else
        as it is."""
        size = os.path.getsize(path)
        start, end = 0, size - 1
        ranged = self.headers.get('Range', '') if not attachment else ''
        if ranged.startswith('bytes='):
            first, _, last = ranged[6:].split(',')[0].partition('-')
            try:
                if first:
                    start, end = int(first), int(last) if last else size - 1
                elif last:
                    start = max(0, size - int(last))
            except ValueError:
                pass
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.send_response(206)
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        else:
            self.send_response(200)
        import mimetypes
        kind = 'audio/flac' if path.lower().endswith('.flac') else mimetypes.guess_type(path)[0] or 'application/octet-stream'
        self.send_header('Content-Type', kind)
        self.send_header('Accept-Ranges', 'none' if attachment else 'bytes')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Cache-Control', 'no-store')
        quoted = urllib.parse.quote(name or os.path.basename(path))
        self.send_header('Content-Disposition', ('attachment' if attachment else 'inline') + f"; filename*=UTF-8''{quoted}")
        self.end_headers()
        with open(path, 'rb') as file:
            file.seek(start)
            left = end - start + 1
            while left > 0:
                data = file.read(min(CHUNK, left))
                if not data:
                    break
                self.wfile.write(data)
                left -= len(data)


def _album_key(job):
    """How a job's song is grouped (the page's albumKey): its album and album artist; none: last."""
    tags = job.get('tags') or {}
    return (((tags.get('albumartist') or tags.get('artist') or '') + '\x01' + tags['album']).lower() if tags.get('album')
            else '\uffff')


def _song_title(job):
    return (job.get('tags') or {}).get('title') or os.path.splitext(job['name'])[0]


def mail_settings():
    """The SMTP server the owners' notifications go through (settings web/mail/...): on, host, port, security
    ('starttls', 'ssl' or 'none'), user, password, sender."""
    settings = util.settings()
    return {'on': settings.value('web/mail/on', False, type=bool), 'host': settings.value('web/mail/host', '', type=str),
            'port': settings.value('web/mail/port', 587, type=int), 'security': settings.value('web/mail/security', 'starttls', type=str),
            'user': settings.value('web/mail/user', '', type=str), 'password': settings.value('web/mail/password', '', type=str),
            'sender': settings.value('web/mail/sender', '', type=str), 'name': settings.value('web/mail/name', '', type=str)}


def set_mail_settings(values):
    settings = util.settings()
    for key, value in values.items():
        settings.setValue(f'web/mail/{key}', value)


def _signed(lines, settings):
    """An email's lines, signed with the sender's name when one is set (else not signed)."""
    return lines + (['', f'— {settings["name"]}'] if settings.get('name') else [])


def send_mail(settings, to, subject, body):
    """(Any thread) a plain text email sent; an error, or None."""
    import smtplib
    import ssl
    from email.message import EmailMessage
    from email.utils import formataddr, make_msgid
    if not settings['host'] or not settings['sender']:
        return 'no SMTP server or sender set (File -> Web libraries...)'
    message = EmailMessage()
    # From: the address set, with a name only when one is set (no application's name in the emails)
    message['From'] = formataddr((settings['name'], settings['sender'])) if settings.get('name') else settings['sender']
    message['To'] = to
    message['Subject'] = subject
    message['Message-ID'] = make_msgid()
    message.set_content(body)
    try:
        context = ssl.create_default_context()
        if settings['security'] == 'ssl':
            server = smtplib.SMTP_SSL(settings['host'], settings['port'], timeout=30, context=context)
        else:
            server = smtplib.SMTP(settings['host'], settings['port'], timeout=30)
        with server:
            if settings['security'] == 'starttls':
                server.starttls(context=context)
            if settings['user']:
                server.login(settings['user'], settings['password'])
            server.send_message(message)
    except (OSError, smtplib.SMTPException, ssl.SSLError) as e:
        return str(e) or e.__class__.__name__
    return None


def _private(url):
    """An address not reachable from the Internet: a private, loopback or link-local IP, localhost, a .local name."""
    import ipaddress
    host = urllib.parse.urlsplit(url).hostname or ''
    if host == 'localhost' or host.endswith('.local') or host.endswith('.localdomain') or '.' not in host and ':' not in host:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False            # a name: taken as public
    return address.is_private or address.is_loopback or address.is_link_local or address.is_reserved


def _duration(path):
    """A music file's length in seconds (mutagen), 0 when unknown."""
    try:
        import mutagen
        audio = mutagen.File(path)
        return float(audio.info.length) if audio is not None else 0.0
    except Exception:
        return 0.0


def _sniff(head):
    """The first bytes of a music file of one of the formats taken (AUDIO_EXTENSIONS): its signature."""
    if len(head) < 12:
        return False
    return (head[:4] in (b'fLaC', b'OggS', b'wvpk', b'MAC ', b'MPCK', b'DSD ') or head[:3] in (b'ID3', b'MP+')
            or (head[0] == 0xFF and head[1] & 0xE0 == 0xE0)                  # an MPEG audio or ADTS frame
            or (head[:4] == b'RIFF' and head[8:12] == b'WAVE')
            or (head[:4] == b'FORM' and head[8:12] in (b'AIFF', b'AIFC'))
            or head[4:8] == b'ftyp'                                          # MP4: m4a, alac, aac
            or head[:16] == bytes.fromhex('3026b2758e66cf11a6d900aa0062ce6c'))     # ASF: wma


def _playable(path):
    """A file mutagen reads as music, with a length (without mutagen: taken)."""
    try:
        import mutagen
    except ImportError:
        return True
    try:
        audio = mutagen.File(path)
    except Exception:
        return False
    return audio is not None and getattr(audio.info, 'length', 0) > 0


def _tone(query):
    """The page's amplifier asked for on a download: {bass, treble (dB, -12..12), balance (-1..1)}; None: as it is."""
    def number(key, low, high):
        try:
            value = float(query.get(key, 0))
        except ValueError:
            return 0.0
        return max(low, min(high, value)) if math.isfinite(value) else 0.0
    tone = {'bass': number('bass', -12, 12), 'treble': number('treble', -12, 12), 'balance': number('balance', -1, 1)}
    return tone if any(abs(value) > 1e-6 for value in tone.values()) else None


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        """A browser that went away (a seek, a closed tab: the connection reset or broken): not an error worth a
        traceback on the console; anything else logged."""
        import sys
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionError, TimeoutError)):
            return
        log.warning(f'[web] request from {client_address[0]}: {error!r}')


class WebQueue(QObject):
    """The songs sent from the LAN (jobs), the server, and the jobs made by negatives (a negatives.Negatives) once
    started here. changed(): a job came, went or changed state (in the GUI thread)."""

    changed = pyqtSignal()

    def __init__(self, negatives, parent=None):
        super().__init__(parent)
        self.negatives = negatives
        self.jobs = {}              # id: {id, token, name, upload, result, state, percent, error, added, size}
        self.picture = b''              # the amplifier, whole (amp_picture): the page without its script
        self.panel_picture = b''        # without the knobs, the needles and the mute: the page draws them
        self.amp_layout = None          # where they are
        self._incoming = {}             # token: (songs, bytes) being sent now (reserve, release)
        from cutemusic.tunnel import Tunnel
        self.tunnel = Tunnel(self)      # the page on a public port, while served (tunnel.py)
        try:
            with open(OWNERS_FILE, encoding='utf-8') as file:
                self.owners = {str(token): ({'name': str(value.get('name', '')), 'email': str(value.get('email', '')),
                                             'keys': [str(key) for key in value.get('keys', [])], 'site': str(value.get('site', ''))}
                                            if isinstance(value, dict) else {'name': str(value), 'email': '', 'keys': []})
                               for token, value in json.load(file).items()}
        except (OSError, ValueError, AttributeError):
            self.owners = {}
        try:
            with open(VERIFIED_FILE, encoding='utf-8') as file:
                self.verified = {str(email).lower() for email in json.load(file)}
        except (OSError, ValueError, TypeError):
            self.verified = set()
        self._codes = {}            # an email address: (its code, until when, tries left)
        self.port = None
        self._server = None
        self._lock = threading.Lock()
        self._load()
        self._clean_up()
        self._tag_old_jobs()
        negatives.changed.connect(self._sync)
        self._sync()

    # Jobs

    def _load(self):
        try:
            with open(JOBS_FILE, encoding='utf-8') as file:
                jobs = json.load(file)
        except (OSError, ValueError):
            jobs = []
        for job in jobs:
            if os.path.isfile(job.get('upload', '')) or os.path.isfile(job.get('result', '')):
                job.setdefault('percent', 0)
                job.setdefault('error', '')
                if job['state'] == 'making':
                    job['state'] = 'queued'         # (the negatives' queue has it first again: _sync)
                self.jobs[job['id']] = job

    def _clean_up(self):
        """At start: the same song put more than once in a token (the same file: its SHA-1) kept once: the one made
        (else the oldest), with what the others had (allowed to be downloaded, a download asked for); the others'
        files removed, and out of the negatives' queue."""
        order = {'done': 0, 'making': 1, 'queued': 2, 'waiting': 3, 'failed': 4, 'song': 5}
        groups = {}
        for job in self.jobs.values():
            if job.get('sha1'):
                groups.setdefault((job['token'], job['sha1']), []).append(job)
        removed = []
        for (token, digest), group in groups.items():
            if len(group) < 2:
                continue
            group.sort(key=lambda job: (order.get(job['state'], 9), job['added']))
            kept, others = group[0], group[1:]
            kept['download'] = any(job.get('download') for job in group)
            asked = [job['requested'] for job in group if job.get('requested')]
            if asked and not kept['download']:
                kept['requested'] = min(asked)
            kept['added'] = min(job['added'] for job in group)
            for job in others:
                del self.jobs[job['id']]
                shutil.rmtree(os.path.dirname(job['upload']), ignore_errors=True)
                removed.append(job)
            log.info(f'[web] {kept["name"]} ({token}): {len(others)} copies of it removed (put more than once)')
        if removed:
            self.negatives.remove([job['upload'] for job in removed])
            self._save()

    def _tag_old_jobs(self):
        """The jobs from before their tags were kept: their songs' tags read once, in a thread (the album trees)."""
        untagged = [(job['id'], job['upload']) for job in self.jobs.values() if 'duration' not in job.get('tags', {})]
        if not untagged:
            return

        def read():
            tags = {identifier: self._read_tags(path) for identifier, path in untagged if os.path.isfile(path)}
            with self._lock:
                for identifier, value in tags.items():
                    if identifier in self.jobs:
                        self.jobs[identifier]['tags'] = value
            util.call_in_gui_thread(self._changed)
        threading.Thread(target=read, name='web tags', daemon=True).start()

    def _save(self):
        with self._lock:
            jobs = sorted(self.jobs.values(), key=lambda job: job['added'])
        try:
            os.makedirs(FOLDER, mode=0o700, exist_ok=True)
            with open(JOBS_FILE + '.tmp', 'w', encoding='utf-8') as file:
                json.dump(jobs, file, indent=1)
            os.replace(JOBS_FILE + '.tmp', JOBS_FILE)
        except OSError as e:
            log.warning(f'[web] {JOBS_FILE}: {e}')

    @staticmethod
    def _public(job):
        duration = (job.get('tags') or {}).get('duration') or 0
        return dict({key: job[key] for key in ('id', 'name', 'state', 'percent', 'error', 'added', 'size')},
                    requested=bool(job.get('requested')) and not job.get('download'),
                    download=bool(job.get('download')), tags=job.get('tags') or {},
                    preview=0 if job.get('download') else round(duration * PREVIEW[0], 3))

    @staticmethod
    def _read_tags(path):
        """A song's tags for the page's album tree (library.read_track's; its folder, a job's, not taken as the album)."""
        from cutemusic.library import read_track
        try:
            track = read_track(path, os.path.dirname(path), durations=False)
        except Exception as e:
            log.warning(f'[web] tags of {path}: {e}')
            return {}
        if not track.get('duration'):
            track['duration'] = _duration(path)
        return {key: track.get(key) or ('' if key in ('title', 'artist', 'albumartist', 'album') else 0)
                for key in ('title', 'artist', 'albumartist', 'album', 'year', 'track', 'disc', 'duration')}

    def reserve(self, token, size):
        """(The server's threads) room for a song of size sent to token's library, kept while it comes: its songs
        and the ones coming at most TOKEN_FILES, TOKEN_BYTES in all; why not (None: room taken, release() after)."""
        with self._lock:
            files, used = self._incoming.get(token, (0, 0))
            if token not in self.owners:
                return 'log in first'
            for job in self.jobs.values():
                if job['token'] == token:
                    files, used = files + 1, used + job.get('size', 0)
            if files >= TOKEN_FILES:
                return f'a token holds at most {TOKEN_FILES} songs: remove some first'
            if used + size > TOKEN_BYTES:
                return f'a token holds at most {TOKEN_BYTES // 2**20} MB of songs ({used / 2**20:.0f} MB used): remove some first'
            coming = self._incoming.get(token, (0, 0))
            self._incoming[token] = (coming[0] + 1, coming[1] + size)
            return None

    def release(self, token, size):
        with self._lock:
            files, used = self._incoming.get(token, (1, size))
            if files <= 1:
                self._incoming.pop(token, None)
            else:
                self._incoming[token] = (files - 1, used - size)

    def lyrics(self, token, identifier):
        """(The server's threads) a job's lyrics, as the app finds them (library.read_lyrics: a .lrc or .txt beside the
        song, its tags, the ones looked up online before), else its negative's (made with the song's lyrics in its
        tags): {timed: [[seconds, line]] (synced) or None, plain: [line] or None}."""
        from cutemusic.library import parse_lrc, read_lyrics, read_track
        job = self.job(token, identifier)
        text = None
        for path in (job['upload'], job['result']) if job else ():
            if os.path.isfile(path):
                try:
                    text = read_lyrics(read_track(path, os.path.dirname(path), durations=False))
                except Exception as e:
                    log.warning(f'[web] lyrics of {path}: {e}')
                if text:
                    break
        timed = parse_lrc(text) if text else None
        plain = [line.strip() for line in text.splitlines() if line.strip()] if text and not timed else None
        return {'timed': timed, 'plain': plain}

    _previewing = {}            # a preview's path: its lock (made once, the others waiting for it)
    _previewing_lock = threading.Lock()

    def preview(self, job, song):
        """(The server's threads) what is played of a job's song (song) or negative while not allowed to be downloaded:
        its middle half (PREVIEW of its length), made by ffmpeg into the job's folder ('.preview-song.flac',
        '.preview-negative.flac'), again when the file is newer; None when it cannot be made."""
        source = job['upload'] if song else job['result']
        target = os.path.join(os.path.dirname(source), '.preview-song.flac' if song else '.preview-negative.flac')
        with self._previewing_lock:
            lock = self._previewing.setdefault(target, threading.Lock())
        with lock:
            try:
                if os.path.getmtime(target) >= os.path.getmtime(source):
                    return target
            except OSError:
                pass
            if shutil.which('ffmpeg') is None:
                return None
            duration = _duration(source)
            if not duration:
                return None
            import subprocess
            start, length = duration * PREVIEW[0], duration * (PREVIEW[1] - PREVIEW[0])
            partial = target + '.part.flac'
            command = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', f'{start:.3f}', '-t', f'{length:.3f}', '-i', source,
                       '-map', '0:a:0', '-map_metadata', '0', '-c:a', 'flac', partial]
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=600)
            except subprocess.TimeoutExpired:
                return None
            if result.returncode != 0:
                log.warning(f'[web] preview of {source}: {result.stderr.strip()[-300:]}')
                return None
            os.replace(partial, target)
            log.info(f'[web] {job["name"]} ({job["token"]}): its {"song" if song else "negative"} played from '
                     f'{start:.0f} s to {start + length:.0f} s only (download not allowed)')
            return target

    def jobs_of(self, token):
        """(The server's threads) a token's jobs as the page shows them, with their songs' tags (read once, kept)."""
        with self._lock:
            jobs = [job for job in sorted(self.jobs.values(), key=lambda job: job['added']) if job['token'] == token]
            untagged = [(job['id'], job['upload']) for job in jobs if 'duration' not in job.get('tags', {})]
        if untagged:
            read = {identifier: self._read_tags(path) for identifier, path in untagged if os.path.isfile(path)}
            with self._lock:
                for identifier, tags in read.items():
                    if identifier in self.jobs:
                        self.jobs[identifier]['tags'] = tags
            util.call_in_gui_thread(self._save)
        with self._lock:
            return [self._public(job) for job in jobs]

    def job(self, token, identifier):
        with self._lock:
            job = self.jobs.get(identifier)
            return dict(job) if job is not None and job['token'] == token else None

    def add_remote(self, token, identifier, name, upload, result, size, negative=None, state='waiting'):
        """(Not the GUI thread) a song sent, or put here: waiting for Start, or made already when its negative is
        there: negative (the Negatives folder's, for a song put here), else a job's of the same song (the same
        SHA-1), copied as its own."""
        digest = _sha1(upload)
        job = {'id': identifier, 'token': token, 'name': name, 'upload': upload, 'result': result, 'state': state,
               'percent': 0, 'error': '', 'added': time.time(), 'size': size, 'download': False, 'sha1': digest,
               'tags': self._read_tags(upload)}
        with self._lock:
            if not (negative and os.path.isfile(negative)):
                negative = next((other['result'] for other in self.jobs.values() if digest and other.get('sha1') == digest
                                 and other['state'] == 'done' and os.path.isfile(other['result'])), None)
        if negative:
            try:
                shutil.copyfile(negative, result + '.part')
                os.replace(result + '.part', result)
                job['state'], job['percent'] = 'done', 100
            except OSError as e:
                log.warning(f'[web] {negative} not reused for {name}: {e}')
        with self._lock:
            self.jobs[identifier] = job
        log.info(f'[web] {name} into {token}: ' + (f'its negative made already ({negative})' if job['state'] == 'done' else job['state']))
        util.call_in_gui_thread(self._changed)
        return self._public(job)

    def request(self, token, identifier):
        """(The server's thread) Request download on the page: a song (and its negative) not downloadable asked to
        be: marked requested (when) in the Web tab, the window alerted, until Allow download (the owner then
        emailed)."""
        with self._lock:
            job = self.jobs.get(identifier)
            if job is None or job['token'] != token or job.get('download') or job.get('requested'):
                return False
            job['requested'] = time.time()
            name = job['name']
        log.info(f'[web] {name} ({token}): its download requested from the page')
        util.call_in_gui_thread(self._changed)
        return True

    def download_requests(self):
        """The jobs whose download is asked for and not allowed yet."""
        with self._lock:
            return [job['id'] for job in self.jobs.values() if job.get('requested') and not job.get('download')]

    def remove_remote(self, token, identifier):
        """(The server's thread) a job of token's removed, unless being made."""
        with self._lock:
            job = self.jobs.get(identifier)
            if job is None or job['token'] != token or job['state'] == 'making':
                return False
        util.call_in_gui_thread(self.remove, [identifier])
        return True

    def waiting(self):
        """The jobs to be started: sent and waiting (not the failed ones: started again one by one)."""
        with self._lock:
            return [job['id'] for job in self.jobs.values() if job['state'] == 'waiting']

    def all(self):
        with self._lock:
            return [dict(job) for job in sorted(self.jobs.values(), key=lambda job: (job['token'], job['added']))]

    def start_jobs(self, identifiers):
        """Here: the jobs (waiting, or failed: again) given to the negatives' queue, made one at a time."""
        entries = []
        with self._lock:
            for identifier in identifiers:
                job = self.jobs.get(identifier)
                if job is not None and job['state'] in STARTABLE and os.path.isfile(job['upload']):
                    job['was'] = job['state'] if job['state'] != 'failed' else job.get('was', 'waiting')
                    job['state'], job['error'], job['percent'] = 'queued', '', 0
                    entries.append((job['upload'], job['result']))
        if entries:
            self.negatives.forget([source for source, result in entries])
            self.negatives.add_jobs(entries)
            log.info(f'[web] {len(entries)} started')
        self._changed()

    def stop_jobs(self, identifiers):
        """Here: jobs queued or being made taken out of the negatives' queue, back to what they were (a song, or
        waiting); the rest of the queue goes on."""
        with self._lock:
            jobs = [self.jobs[identifier] for identifier in identifiers
                    if identifier in self.jobs and self.jobs[identifier]['state'] in ('queued', 'making')]
        if not jobs:
            return
        sources = {job['upload'] for job in jobs}
        current = self.negatives.current
        stopped = current is not None and current[0] in sources
        if stopped:
            self.negatives.stop()           # (it goes back first in the queue: removed below)
        self.negatives.remove(sources)
        if stopped:
            self.negatives.start()          # the others go on (a queue stopped here before stays stopped)
        log.info(f'[web] {len(jobs)} stopped')

    def tracks(self, identifiers, negatives=False):
        """The jobs' songs (negatives: their negatives made) as Tracks, to play here."""
        from cutemusic.library import read_track
        with self._lock:
            jobs = [dict(self.jobs[identifier]) for identifier in identifiers if identifier in self.jobs]
        paths = [job['result'] if negatives else job['upload'] for job in jobs if not negatives or job['state'] == 'done']
        return [read_track(path, os.path.dirname(path), durations=False) for path in paths if os.path.isfile(path)]

    def set_negative(self, identifier, path):
        """Here: a job made with a negative there is (a FLAC): copied as its own, the job made. An error, or None."""
        with self._lock:
            job = self.jobs.get(identifier)
            if job is None or job['state'] == 'making':
                return 'not there, or being made'
            result = job['result']
        try:
            shutil.copyfile(path, result + '.part')
            os.replace(result + '.part', result)
        except OSError as e:
            return str(e)
        self.negatives.remove([job['upload']])
        with self._lock:
            job['state'], job['percent'], job['error'] = 'done', 100, ''
        log.info(f'[web] {job["name"]} ({job["token"]}): negative set from {path}')
        self._notify(job['token'], job['name'], 'done', '')
        self._changed()
        return None

    def set_download(self, identifiers, allowed):
        """Here: the jobs' songs and negatives downloadable from the page (allowed), or played there only; the ones
        asked for (Request download) and now allowed: their owners told by email."""
        granted = []
        with self._lock:
            for identifier in identifiers:
                if identifier in self.jobs:
                    job = self.jobs[identifier]
                    if allowed and job.get('requested') and not job.get('download'):
                        granted.append((job['token'], job['name']))
                    job['download'] = bool(allowed)
                    job.pop('requested', None)
        for token, name in granted:
            self._notify(token, name, 'download', '')
        log.info(f'[web] download {"allowed" if allowed else "not allowed"} for {len(identifiers)}')
        self._changed()

    def remove(self, identifiers):
        """Jobs out of the queue, their files gone (not the one being made)."""
        removed = []
        with self._lock:
            for identifier in identifiers:
                job = self.jobs.get(identifier)
                if job is not None and job['state'] != 'making':
                    removed.append(self.jobs.pop(identifier))
        if not removed:
            return
        self.negatives.remove([job['upload'] for job in removed])
        for job in removed:
            shutil.rmtree(os.path.dirname(job['upload']), ignore_errors=True)
            log.info(f'[web] {job["name"]} ({job["token"]}) removed')
        self._changed()

    def _sync(self):
        """The started jobs' states from the negatives' queue: queued, making (its percent), made or failed."""
        negatives = self.negatives
        current = negatives.current[0] if negatives.current is not None else None
        queued = {source for source, negative in negatives.queue}
        finished = {source: error for source, negative, seconds, error in negatives.finished}
        changed = False
        news = []           # (token, song, made or failed, error): their owners told (_notify)
        with self._lock:
            for job in self.jobs.values():
                source = job['upload']
                if job['state'] in STARTABLE and source not in queued and source != current:
                    continue
                if source == current:
                    state, percent, error = 'making', negatives.percent, ''
                elif source in queued:
                    state, percent, error = 'queued', 0, ''
                elif os.path.isfile(job['result']):
                    state, percent, error = 'done', 100, ''
                elif finished.get(source):
                    state, percent, error = 'failed', 0, finished[source]
                elif job['state'] == 'done':
                    state, percent, error = 'failed', 0, 'the negative is gone'
                else:
                    state, percent, error = job.get('was', 'waiting'), 0, ''      # the negatives' queue cleared
                if (state, percent, error) != (job['state'], job['percent'], job['error']):
                    if state in ('done', 'failed') and job['state'] in ('queued', 'making'):
                        news.append((job['token'], job['name'], state, error))
                    job['state'], job['percent'], job['error'] = state, percent, error
                    changed = True
        for token, name, state, error in news:
            self._notify(token, name, state, error)
        if changed:
            self._changed()

    def _changed(self):
        self._save()
        self.changed.emit()

    # Email: a token's owner told when their negatives are made (or failed), by the address they gave (tokens.json),
    # through the SMTP server set in Web libraries... (settings web/mail/...). The news of a token gathered for
    # NOTIFY_DELAY (a whole album made one after the other: one email), then sent in a thread.

    _news = {}                  # token: [(song, state, error)]
    _notify_timer = None

    def _notify(self, token, name, state, error):
        settings = mail_settings()
        if not settings['on'] or not self.email(token):
            return
        self._news.setdefault(token, []).append((os.path.splitext(name)[0], state, error))
        if self._notify_timer is None:
            from PyQt6.QtCore import QTimer
            self._notify_timer = QTimer(self, singleShot=True, interval=NOTIFY_DELAY * 1000)
            self._notify_timer.timeout.connect(self._send_news)
        self._notify_timer.start()        # (again: the news gathered until the queue rests)

    _recovered = {}             # an address, a client: when last asked (RECOVER_EVERY)

    def recover(self, email, client):
        """(The server's threads) a forgotten token: the tokens registered with email sent to it (not shown on the
        page: whoever asks learns nothing); an error, or None. Once per RECOVER_EVERY an address, and a client."""
        email = email.strip()
        if not EMAIL.match(email):
            return 'an email address is needed'
        settings = mail_settings()
        if not settings['on'] or not settings['host'] or not settings['sender']:
            return 'email is not set up here: ask the one running AG CuteMusic for your token'
        now = time.time()
        with self._lock:
            for key in (email.lower(), client):
                if now - self._recovered.get(key, 0) < RECOVER_EVERY:
                    return f'asked a moment ago: wait {RECOVER_EVERY // 60} minutes and look in your mailbox'
            self._recovered[email.lower()] = self._recovered[client] = now
            tokens = sorted(token for token, owner in self.owners.items() if owner.get('email', '').lower() == email.lower())
            names = {token: self.owners[token].get('name', '') for token in tokens}
        if not tokens:
            log.info(f'[web] token recovery asked for {email} from {client}: none')
            return None
        lines = ['Hello,', '',
                 'Your token' + ('s' if len(tokens) > 1 else '') + ' for the karaoke negatives page:', '']
        for token in tokens:
            lines += [f'  {token}', ''] + ([f'    {url}#{token}' for url in self._links(token)] + [''] if self._links(token) else [])
        lines = _signed(lines + ['Someone asked for them with your address; if it was not you, nothing else happens.'], settings)
        threading.Thread(target=self._send_mail, args=(settings, email, 'Your token' + ('s' if len(tokens) > 1 else ''),
                                                        '\n'.join(lines), ', '.join(tokens)),
                         name='web mail', daemon=True).start()
        log.info(f'[web] token recovery asked for {email} from {client}: {", ".join(tokens)} sent')
        return None

    def _send_news(self):
        news, self._news = self._news, {}
        settings = mail_settings()
        for token, songs in news.items():
            to, owner = self.email(token), self.owner(token)
            if not to:
                continue
            made = [song for song, state, error in songs if state == 'done']
            failed = [(song, error) for song, state, error in songs if state == 'failed']
            allowed = [song for song, state, error in songs if state == 'download']
            subject = (f'Your karaoke negatives are made ({len(made)})' if made else
                       f'Downloads allowed ({len(allowed)})' if allowed else 'Your karaoke negatives could not be made') + \
                f' — {token}'
            lines = ['Hello,', '']
            if allowed:
                lines += ['You can now download these (the songs and their negatives):', ''] + [f'  • {song}' for song in allowed] + ['']
            if made:
                lines += ['These negatives (the songs without their singer) are made:', ''] + [f'  • {song}' for song in made] + ['']
            if failed:
                lines += ['These could not be made:', ''] + [f'  • {song}: {error}' for song, error in failed] + ['']
            links = self._links(token)
            if links:
                lines += ['Play them on the page, with your token:', ''] + [f'  {url}#{token}' for url in links]
            else:
                lines += [f'Play them on the page, with your token {token}.']
            lines = _signed(lines, settings)
            threading.Thread(target=self._send_mail, args=(settings, to, subject, '\n'.join(lines), token),
                             name='web mail', daemon=True).start()

    @staticmethod
    def _send_mail(settings, to, subject, body, token):
        error = send_mail(settings, to, subject, body)
        if error:
            log.warning(f'[web] {token}: not emailed to {to}: {error}')
        else:
            log.info(f'[web] {token}: emailed {to}: {subject}')

    def add_local(self, token, paths):
        """Here: songs (files, or folders: their songs) put in token's library, copied in a thread: played on its
        page, their negatives not asked for (made: Start), unless made already; how many."""
        from cutemusic.library import music_folder
        from cutemusic.negatives import negative_path
        songs = []
        for path in paths:
            if os.path.isdir(path):
                for folder, names, files in os.walk(path):
                    names.sort()
                    songs += [os.path.join(folder, name) for name in sorted(files)
                              if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS and not name.startswith('.')]
            elif os.path.isfile(path) and os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS:
                songs.append(path)
        if not TOKEN.match(token or '') or not songs:
            return 0
        root = music_folder()
        # a negative given (the Negatives folder's, or a job's): its song instead, which brings it along; a song and
        # its negative given together: once
        from cutemusic.negatives import negatives_folder, original_relative
        folder_of_negatives = os.path.abspath(negatives_folder())
        with self._lock:
            results = {os.path.abspath(job['result']): job['upload'] for job in self.jobs.values()}
        originals = []
        for path in songs:
            absolute = os.path.abspath(path)
            if absolute in results:
                original = results[absolute]
            elif os.path.commonpath([absolute, folder_of_negatives]) == folder_of_negatives:
                stem = original_relative(absolute, folder_of_negatives)
                original = next((os.path.join(root, stem + extension) for extension in sorted(AUDIO_EXTENSIONS)
                                 if os.path.isfile(os.path.join(root, stem + extension))), None)
                if original is None:
                    log.info(f'[web] {path}: a negative whose song is not in the music folder: not put in {token}')
                    continue
            else:
                original = path
            if original not in originals:
                originals.append(original)
        songs = originals
        if not songs:
            return 0
        made = {path: negative_path(path, root) for path in songs}      # (the settings read here: the GUI thread)

        def copy():
            for path in songs:
                digest = _sha1(path)            # in this token already (the same file): not again
                with self._lock:
                    there = digest and any(job['token'] == token and job.get('sha1') == digest for job in self.jobs.values())
                if there:
                    log.info(f'[web] {path}: in {token} already')
                    continue
                name = _safe_name(os.path.basename(path))
                identifier = uuid.uuid4().hex[:12]
                folder = os.path.join(FOLDER, token, identifier)
                upload = os.path.join(folder, name)
                try:
                    os.makedirs(folder, mode=0o700, exist_ok=True)
                    shutil.copyfile(path, upload + '.part')
                    os.replace(upload + '.part', upload)
                    for extension in ('.lrc', '.LRC', '.txt'):      # its lyrics beside it (library.read_lyrics)
                        lyrics = os.path.splitext(path)[0] + extension
                        if os.path.isfile(lyrics):
                            shutil.copyfile(lyrics, os.path.splitext(upload)[0] + extension.lower())
                            break
                except OSError as e:
                    shutil.rmtree(folder, ignore_errors=True)
                    log.warning(f'[web] {path} not put in {token}: {e}')
                    continue
                stem = os.path.splitext(name)[0]
                self.add_remote(token, identifier, name, upload, os.path.join(folder, f'{stem} (negative).flac'),
                                os.path.getsize(upload), made.get(path), state='song')
        threading.Thread(target=copy, name='web copy', daemon=True).start()
        return len(songs)

    # A token's songs as a library (the Library tab's source button: "Web 12345", as a library on the LAN)

    def tokens(self):
        with self._lock:
            return sorted({job['token'] for job in self.jobs.values()})

    # whose a token is: the name and email address given when it was first opened on the page (tokens.json); the
    # address: where to notify them (not shown on the page)

    def site(self, token):
        """The page's address a token was made at ('': made here, or before it was kept): its emails' link."""
        with self._lock:
            return self.owners.get(token, {}).get('site', '')

    def _links(self, token=None):
        """The page's address in the emails: only the public one configured (Web libraries..., the tunnel's Public
        address); none when there is none."""
        public = self.public_url()
        return [public] if public else []

    def owner(self, token):
        with self._lock:
            return self.owners.get(token, {}).get('name', '')

    def email(self, token):
        with self._lock:
            return self.owners.get(token, {}).get('email', '')

    def title(self, token):
        """A token as the window shows it: its owner, 'Adi <adi@example.com>'; 'Web 12345' when it has none."""
        owner, email = self.owner(token), self.email(token)
        if not owner:
            return f'Web {token}'
        return owner + (f' <{email}>' if email else '')

    # Logging in on the page: a token and the email address it was made with; a key then (random, kept in the
    # browser and here, KEYS a token), given with every request (Handler._token). A token is made only with an email
    # address verified (a code sent to it, typed on the page: verified.json). A token from before, with songs and no
    # owner, is taken by whoever registers it first.

    def taken(self, token):
        with self._lock:
            return token in self.owners or any(job['token'] == token for job in self.jobs.values())

    def send_code(self, email, client, site=''):
        """(The server's threads) a code to verify an email address, emailed to it (CODE_VALID s); an error, or None.
        Once a CODE_EVERY s an address, and a client."""
        import secrets
        email = email.strip()
        if not EMAIL.match(email):
            return 'an email address is needed'
        settings = mail_settings()
        if not settings['on'] or not settings['host'] or not settings['sender']:
            return 'email is not set up here: no token can be made'
        now = time.time()
        with self._lock:
            for key in (email.lower(), client):
                if now - self._recovered.get(('code', key), 0) < CODE_EVERY:
                    return f'a code was sent a moment ago: look in your mailbox, or ask again in {CODE_EVERY} s'
            self._recovered[('code', email.lower())] = self._recovered[('code', client)] = now
            code = f'{secrets.randbelow(10 ** 6):06d}'
            self._codes[email.lower()] = (code, now + CODE_VALID, CODE_TRIES)
        body = '\n'.join(_signed([f'Your code to verify this email address: {code}', '',
                                  f'Type it on the page (it is good for {CODE_VALID // 60} minutes)'
                                  + (':' if self._links() else '.'), ''] + [f'  {url}' for url in self._links()] + ([''] if self._links() else []) + [
                                  'If you did not ask for it, ignore this email.'], settings))
        threading.Thread(target=self._send_mail, args=(settings, email, f'Your verification code: {code}', body, 'verify'),
                         name='web mail', daemon=True).start()
        log.info(f'[web] a code sent to {email} (asked from {client})')
        return None

    def register(self, token, name, email, code='', site=''):
        """(The server's threads) a new token's owner (or one from before with no owner): name and email address,
        verified (code: the one sent to it, when not yet); (error, key): a key logged in with, or an error
        ('verify': a code is needed)."""
        name, email = ' '.join(name.split())[:40], email.strip()[:120]
        if not name:
            return 'a name is needed', None
        if not EMAIL.match(email):
            return 'an email address is needed', None
        key = uuid.uuid4().hex
        with self._lock:
            if token in self.owners:
                return f'the token {token} is already registered', None
            address = email.lower()
            if address not in self.verified:
                if not code:
                    return 'verify', None
                sent, until, tries = self._codes.get(address, ('', 0, 0))
                if not sent or time.time() > until or tries <= 0:
                    self._codes.pop(address, None)
                    return 'the code is no longer good: send a new one', None
                if code.strip() != sent:
                    self._codes[address] = (sent, until, tries - 1)
                    return 'not the code sent' + ('' if tries > 1 else ': send a new one'), None
                del self._codes[address]
                self.verified.add(address)
                verified = sorted(self.verified)
            else:
                verified = None
            self.owners[token] = {'name': name, 'email': email, 'keys': [key], 'site': site}
        if verified is not None:
            self._save_json(VERIFIED_FILE, verified)
            log.info(f'[web] {email} verified')
        self._save_owners()
        log.info(f'[web] token {token} registered by {name} <{email}>')
        util.call_in_gui_thread(self._changed)
        return None, key

    def verify(self, email, code):
        """(The server's threads) an email address verified with the code sent to it; an error, or None."""
        address = email.strip().lower()
        if not EMAIL.match(address):
            return 'an email address is needed'
        with self._lock:
            if address in self.verified:
                return None
            sent, until, tries = self._codes.get(address, ('', 0, 0))
            if not sent or time.time() > until or tries <= 0:
                self._codes.pop(address, None)
                return 'the code is no longer good: send a new one'
            if code.strip() != sent:
                self._codes[address] = (sent, until, tries - 1)
                return 'not the code sent' + ('' if tries > 1 else ': send a new one')
            del self._codes[address]
            self.verified.add(address)
            verified = sorted(self.verified)
        self._save_json(VERIFIED_FILE, verified)
        log.info(f'[web] {email} verified')
        return None

    def verified_email(self, email):
        with self._lock:
            return email.strip().lower() in self.verified

    _failed = {}                # a client: the times its logins failed lately (LOGIN_TRIES in LOGIN_WINDOW s)

    def login(self, token, email, client):
        """(The server's threads) a token opened with the email address it was made with: (status, key, name):
        200 and a key; 404: no such token (register it); 403: not its address; 429: too many tries."""
        now = time.time()
        with self._lock:
            tries = [when for when in self._failed.get(client, []) if now - when < LOGIN_WINDOW]
            if len(tries) >= LOGIN_TRIES:
                return 429, None, ''
            owner = self.owners.get(token)
            if owner is None:
                return 404, None, ''
            if owner.get('email', '').lower() != email.strip().lower():
                self._failed[client] = tries + [now]
                return 403, None, ''
            key = uuid.uuid4().hex
            owner['keys'] = (owner.get('keys', []) + [key])[-KEYS:]
            name = owner.get('name', '')
        self._save_owners()
        log.info(f'[web] token {token} opened from {client}')
        return 200, key, name

    def check(self, token, key):
        """A request's token and key: logged in."""
        with self._lock:
            return bool(key) and key in self.owners.get(token, {}).get('keys', [])

    def logout(self, token, key):
        with self._lock:
            owner = self.owners.get(token)
            if owner is None or key not in owner.get('keys', []):
                return
            owner['keys'].remove(key)
        self._save_owners()

    # The subscriptions, edited here (Subscriptions...): every token, with its owner or not

    def subscriptions(self):
        """[{token, name, email, verified, songs, logins}]: the tokens with an owner, and the ones with songs only."""
        with self._lock:
            songs = {}
            for job in self.jobs.values():
                songs[job['token']] = songs.get(job['token'], 0) + 1
            tokens = sorted(set(self.owners) | set(songs))
            return [{'token': token, 'name': self.owners.get(token, {}).get('name', ''),
                     'email': self.owners.get(token, {}).get('email', ''),
                     'verified': self.owners.get(token, {}).get('email', '').lower() in self.verified,
                     'songs': songs.get(token, 0), 'logins': len(self.owners.get(token, {}).get('keys', []))}
                    for token in tokens]

    def set_owner(self, token, name, email):
        """Here: a token's owner set or changed (an address set here taken as verified); an error, or None. Its
        logins kept when only the name changes, else dropped (the address is what logs in)."""
        name, email = ' '.join(name.split())[:40], email.strip()[:120]
        if not TOKEN.match(token):
            return 'a token is 5 digits'
        if not name:
            return 'a name is needed'
        if not EMAIL.match(email):
            return 'an email address is needed'
        with self._lock:
            owner = self.owners.get(token)
            keys = owner.get('keys', []) if owner and owner.get('email', '').lower() == email.lower() else []
            self.owners[token] = {'name': name, 'email': email, 'keys': keys, 'site': (owner or {}).get('site', '')}
            self.verified.add(email.lower())
            verified = sorted(self.verified)
        self._save_json(VERIFIED_FILE, verified)
        self._save_owners()
        log.info(f'[web] token {token}: {name} <{email}> (set here)')
        self._changed()
        return None

    def remove_owner(self, token, songs=False):
        """Here: a token's subscription removed (its owner and logins: its digits free again); songs: its songs and
        their negatives too."""
        with self._lock:
            self.owners.pop(token, None)
            jobs = [job['id'] for job in self.jobs.values() if job['token'] == token]
        self._save_owners()
        log.info(f'[web] token {token}: its subscription removed' + (f', with its {len(jobs)} songs' if songs else ''))
        if songs and jobs:
            self.remove(jobs)
        else:
            self._changed()

    def log_out_everywhere(self, token):
        """Here: every browser logged in to a token logged out (its keys forgotten)."""
        with self._lock:
            if token in self.owners:
                self.owners[token]['keys'] = []
        self._save_owners()
        self._changed()

    def _save_owners(self):
        with self._lock:
            owners = json.loads(json.dumps(self.owners))
        self._save_json(OWNERS_FILE, owners)

    @staticmethod
    def _save_json(path, data):
        try:
            os.makedirs(FOLDER, mode=0o700, exist_ok=True)
            with open(path + '.tmp', 'w', encoding='utf-8') as file:
                json.dump(data, file, indent=1)
            os.chmod(path + '.tmp', 0o600)
            os.replace(path + '.tmp', path)
        except OSError as e:
            log.warning(f'[web] {path}: {e}')

    def sources(self):
        """The tokens as the source button's libraries: {name, address, web: token}."""
        return [{'name': self.title(token), 'address': f'web:{token}', 'id': '', 'web': token} for token in self.tokens()]

    def library(self, token):
        """A token's songs sent (its Library) and their negatives, made or not (its Negatives, the ones not made
        with 'pending': their state), as Tracks; and the folder they are in."""
        from cutemusic.library import Track
        read_track = self._read_track
        songs, negatives = [], []
        with self._lock:
            jobs = [dict(job) for job in sorted(self.jobs.values(), key=lambda job: job['added']) if job['token'] == token]
        for job in jobs:
            stem = os.path.splitext(job['name'])[0]
            if os.path.isfile(job['upload']):
                # (its own folder as the root: no artist nor album taken from <token>/<job>/)
                track = read_track(job['upload'], os.path.dirname(job['upload']), durations=False)
                track['title'] = track.get('title') or stem
                track['web'] = job['id']
                songs.append(track)
            else:
                track = None
            if job['state'] == 'song':      # (no negative asked for)
                continue
            if job['state'] == 'done' and os.path.isfile(job['result']):
                negative = read_track(job['result'], os.path.dirname(job['result']), durations=False)
                if negative.get('title') in ('', os.path.splitext(os.path.basename(job['result']))[0]):     # no tags
                    negative['title'] = (track or {}).get('title') or stem
            else:
                state = STATES[job['state']] + (f' {job["percent"]}%' if job['state'] == 'making' else '') + \
                    (f': {job["error"]}' if job['error'] else '')
                base = track or Track(path=job['upload'], title=stem, artist='', albumartist='', album='', genres=[],
                                      year=0, track=0, disc=0, duration=0.0)
                negative = Track(dict(base, path=job['result'], pending=state))
            negative['web'] = job['id']
            negatives.append(negative)
        return songs, negatives, os.path.join(FOLDER, token)

    _tracks = {}        # (path, mtime): its Track (library() is asked for often: the Negatives tab, every change)

    def _read_track(self, path, root, durations=False):
        from cutemusic.library import Track, read_track
        try:
            key = (path, os.path.getmtime(path))
        except OSError:
            key = None
        if key is not None and key in self._tracks:
            return Track(dict(self._tracks[key]))
        track = read_track(path, root, durations=durations)
        if key is not None:
            self._tracks[key] = Track(dict(track))
        return track

    def jobs_at(self, paths):
        """The jobs whose song sent or negative is at one of paths."""
        paths = set(paths)
        with self._lock:
            return [job['id'] for job in self.jobs.values() if job['upload'] in paths or job['result'] in paths]

    def is_negative(self, path):
        """Whether path is a job's negative (the Original / Negative button: window.py)."""
        with self._lock:
            return any(job['result'] == path for job in self.jobs.values())

    def counterpart(self, path):
        """The other version of a song sent, or of its negative: its path when it is there, else None."""
        with self._lock:
            for job in self.jobs.values():
                other = job['result'] if path == job['upload'] else job['upload'] if path == job['result'] else None
                if other is not None:
                    return other if os.path.isfile(other) else None
        return None

    # The server

    @property
    def running(self):
        return self._server is not None

    def start(self, port_number=None):
        if self._server is not None:
            return True
        port_number = port_number or port()
        try:
            server = _Server(('', port_number), _Handler)
        except OSError as e:
            log.warning(f'[web] port {port_number}: {e}')
            return False
        if not self.picture:
            self.picture, self.panel_picture, self.amp_layout = amp_picture()
        server.web = self
        self._server, self.port = server, port_number
        threading.Thread(target=server.serve_forever, name='web', daemon=True).start()
        self.tunnel.start(port_number)
        log.info(f'[web] serving on {", ".join(self.urls())}')
        self.changed.emit()
        return True

    def stop(self):
        self.tunnel.stop()
        if self._server is not None:
            server, self._server = self._server, None
            server.shutdown()
            server.server_close()
            log.info('[web] stopped')
            self.changed.emit()

    def urls(self):
        """The page's addresses: its public one first (the tunnel's, when on), then this computer's on the LAN."""
        from cutemusic.sharing import _addresses
        local = [f'http://{address}:{self.port}/' for address in _addresses()] or [f'http://localhost:{self.port}/']
        return ([public] if (public := self.public_url()) else []) + local

    def public_url(self):
        from cutemusic.tunnel import tunnel_settings
        settings = tunnel_settings()
        public = settings['public'].strip()
        if not settings['on'] or not public:
            return ''
        if '://' not in public:
            public = 'http://' + public
        return public if public.endswith('/') else public + '/'


# The Web tab

class WebInfo(QDialog):
    """The web page: served or not (Enable: the window's serve action, settings web/on), its port (changed: served
    again on it), its addresses on the LAN (each with Copy) and how it works: File -> Web libraries... and the Web
    tab's Web page... button."""

    def __init__(self, web, parent=None, action=None):
        super().__init__(parent)
        self.web = web
        self.action = action
        self.setWindowTitle('Web libraries')
        self.setModal(True)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        if action is not None:
            self.enable = QCheckBox('Enable: make negatives for the LAN (web page)')
            self.enable.setToolTip(action.toolTip())
            self.enable.setChecked(action.isChecked())
            self.enable.toggled.connect(action.setChecked)
            action.toggled.connect(lambda on: (self.enable.blockSignals(True), self.enable.setChecked(on),
                                               self.enable.blockSignals(False), self.refresh()))
            layout.addWidget(self.enable)
        row = QHBoxLayout()
        row.addWidget(QLabel('Port:'))
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(port())
        row.addWidget(self.port)
        self.apply = QPushButton('Apply')
        self.apply.setToolTip('Kept; served on it now if serving')
        self.apply.clicked.connect(self._apply)
        self.port.valueChanged.connect(lambda value: (self.apply.setEnabled(value != port()), self.refresh()))
        self.apply.setEnabled(False)
        row.addWidget(self.apply)
        row.addStretch(1)
        layout.addLayout(row)
        self.state = QLabel()
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        self.grid = QGridLayout()
        self.grid.setColumnStretch(0, 1)
        layout.addLayout(self.grid)
        layout.addWidget(self._tunnel_box())
        layout.addWidget(self._mail_box())
        notes = QLabel('Open the address in a web browser on the LAN, choose a token of 5 digits, send songs.\n'
                       'Songs sent wait in the Web tab for Start; their negatives are played on the page, '
                       'downloaded only once allowed (Allow download).\n'
                       'The token is not a password: anyone on the LAN can try tokens.')
        notes.setWordWrap(True)
        notes.setStyleSheet('color: palette(placeholder-text);')
        layout.addWidget(notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        web.changed.connect(self.refresh)
        self.refresh()

    def _tunnel_box(self):
        """The page on a public port: a shootback tunnel to a master on the Internet (tunnel.py)."""
        from PyQt6.QtWidgets import QFormLayout, QGroupBox, QLineEdit
        from cutemusic.tunnel import tunnel_settings, set_tunnel_settings
        box = QGroupBox('Public address (shootback tunnel)')
        box.setCheckable(True)
        settings = tunnel_settings()
        box.setChecked(settings['on'])
        form = QFormLayout(box)
        self.tunnel_fields = {}
        for key, label, tip in (('master', 'Server:', 'host:port of the shootback master, e.g. rtunnels.example.com:20049'),
                                ('password', 'Password:', 'its secret key (-k); kept in the settings, as it is'),
                                ('public', 'Public address:', 'where the page is then, e.g. rtunnels.example.com:20080')):
            field = QLineEdit(settings[key])
            field.setPlaceholderText(tip)
            field.setToolTip(tip)
            if key == 'password':
                field.setEchoMode(QLineEdit.EchoMode.Password)
            field.textChanged.connect(lambda text, key=key: (set_tunnel_settings({key: text.strip() if key != 'password' else text}),
                                                             self.tunnel_apply.setEnabled(True)))
            self.tunnel_fields[key] = field
            form.addRow(label, field)
        row = QHBoxLayout()
        self.tunnel_apply = QPushButton('Connect again')
        self.tunnel_apply.setToolTip('The tunnel started again with these (while the page is served)')
        self.tunnel_apply.clicked.connect(self._tunnel_restart)
        row.addWidget(self.tunnel_apply)
        self.tunnel_state = QLabel()
        self.tunnel_state.setWordWrap(True)
        row.addWidget(self.tunnel_state, 1)
        form.addRow(row)
        box.toggled.connect(lambda on: (set_tunnel_settings({'on': on}), self._tunnel_restart()))
        self.web.tunnel.changed.connect(self._tunnel_shown)
        self._tunnel_shown()
        return box

    def _tunnel_restart(self):
        if self.web.running:
            self.web.tunnel.start(self.web.port)
        self.tunnel_apply.setEnabled(False)
        self._tunnel_shown()

    def _tunnel_shown(self):
        tunnel = self.web.tunnel
        if not self.web.running:
            text = 'Started with the page (Enable)'
        else:
            text = {'off': 'Off', 'starting': 'Connecting…', 'connected': 'Connected', 'failed': 'Not connected'}[tunnel.state]
            if tunnel.message:
                text += f': {tunnel.message}'
        self.tunnel_state.setText(text)
        self.refresh()

    def _mail_box(self):
        """Email notifications: the owners told when their negatives are made, through this SMTP server."""
        from PyQt6.QtWidgets import QComboBox, QFormLayout, QGroupBox, QLineEdit
        box = QGroupBox('Email the token\'s owner when their negatives are made')
        box.setCheckable(True)
        settings = mail_settings()
        box.setChecked(settings['on'])
        form = QFormLayout(box)
        self.mail = {}
        for key, label, tip in (('host', 'SMTP server:', 'e.g. smtp.example.com'), ('port', 'Port:', ''),
                                ('security', 'Security:', ''), ('user', 'User:', 'empty: no login'),
                                ('password', 'Password:', 'kept in the settings, as it is'),
                                ('sender', 'From:', 'the address the emails come from'),
                                ('name', 'From name:', 'optional: the name they come from, and are signed with')):
            if key == 'port':
                field = QSpinBox()
                field.setRange(1, 65535)
                field.setValue(settings['port'])
                field.valueChanged.connect(self._mail_changed)
            elif key == 'security':
                field = QComboBox()
                for value, text in (('starttls', 'STARTTLS (587)'), ('ssl', 'SSL/TLS (465)'), ('none', 'None (25)')):
                    field.addItem(text, value)
                field.setCurrentIndex(max(0, field.findData(settings['security'])))
                field.currentIndexChanged.connect(self._mail_changed)
            else:
                field = QLineEdit(settings[key])
                field.setPlaceholderText(tip)
                if key == 'password':
                    field.setEchoMode(QLineEdit.EchoMode.Password)
                field.textChanged.connect(self._mail_changed)
            self.mail[key] = field
            form.addRow(label, field)
        row = QHBoxLayout()
        self.mail_test = QPushButton('Send a test')
        self.mail_test.setToolTip('An email to the From address, through this server')
        self.mail_test.clicked.connect(self._mail_test)
        row.addWidget(self.mail_test)
        self.mail_state = QLabel()
        self.mail_state.setWordWrap(True)
        row.addWidget(self.mail_state, 1)
        form.addRow(row)
        box.toggled.connect(lambda on: set_mail_settings({'on': on}))
        return box

    def _mail_values(self):
        return {'host': self.mail['host'].text().strip(), 'port': self.mail['port'].value(),
                'security': self.mail['security'].currentData(), 'user': self.mail['user'].text().strip(),
                'password': self.mail['password'].text(), 'sender': self.mail['sender'].text().strip(),
                'name': self.mail['name'].text().strip()}

    def _mail_changed(self):
        set_mail_settings(self._mail_values())
        self.mail_state.setText('')

    def _mail_test(self):
        settings = dict(mail_settings(), **self._mail_values())
        if not EMAIL.match(settings['sender']):
            self.mail_state.setText('Set the From address first')
            return
        self.mail_test.setEnabled(False)
        self.mail_state.setText('Sending…')
        def send():
            error = send_mail(settings, settings['sender'], 'A test',
                              'The negatives\' owners will be emailed through this server.')
            util.call_in_gui_thread(lambda: (self.mail_test.setEnabled(True),
                                             self.mail_state.setText(f'Not sent: {error}' if error else f'Sent to {settings["sender"]}')))
        threading.Thread(target=send, name='web mail test', daemon=True).start()

    def _apply(self):
        old = port()
        set_port(self.port.value())
        if self.web.running:
            self.web.stop()
            if not self.web.start():
                set_port(old)
                self.web.start()
                self.port.setValue(old)
                self.state.setText(f'Port {self.port.value()} cannot be used (in use?): still on {old}')
                return
        self.apply.setEnabled(False)
        self.refresh()

    def refresh(self):
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        # this computer's addresses on the LAN with the port chosen: links once served on it
        chosen = self.port.value()
        live = self.web.running and self.web.port == chosen
        if live:
            self.state.setText(f'Serving on port {chosen}, at:')
        elif self.web.running:
            self.state.setText(f'Serving on port {self.web.port}; with port {chosen} (Apply) it would be at:')
        else:
            self.state.setText(f'Not serving (Enable); with port {chosen} it would be at:')
        from cutemusic.sharing import _addresses
        # the public one first (the tunnel's), a link once connected
        public = self.web.public_url()
        rows = ([(public, live and self.web.tunnel.state == 'connected')] if public else []) + \
            [(f'http://{address}:{chosen}/', live) for address in _addresses() or ['localhost']]
        for row, (url, linked) in enumerate(rows):
            link = QLabel((f'<a href="{url}">{url}</a>' if linked else url) + ('  (public)' if url == public else ''))
            link.setOpenExternalLinks(linked)
            link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction if linked
                                         else Qt.TextInteractionFlag.TextSelectableByMouse)
            copy = QPushButton('Copy')
            copy.clicked.connect(lambda checked=False, url=url: QApplication.clipboard().setText(url))
            self.grid.addWidget(link, row, 0)
            self.grid.addWidget(copy, row, 1)


class Subscriptions(QDialog):
    """View -> Web users: the web page's subscriptions, every token with its owner's name and email address (edited in place: the
    address set here taken as verified), whether verified, its songs, the browsers logged in to it; Add..., Remove...,
    Log out everywhere."""

    COLUMNS = ['Token', 'Name', 'Email', 'Verified', 'Songs', 'Logged in']

    def __init__(self, web, parent=None):
        from PyQt6.QtWidgets import QTableWidget
        super().__init__(parent)
        self.web = web
        self.setWindowTitle('Web users')
        self.resize(720, 420)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._edited)
        self.table.itemSelectionChanged.connect(self._buttons)
        layout.addWidget(self.table)
        hint = QLabel('Double click a name or an email address to change it. An address set here is taken as verified; '
                      'changed, the browsers logged in with the old one are logged out.')
        hint.setWordWrap(True)
        hint.setStyleSheet('color: palette(placeholder-text);')
        layout.addWidget(hint)
        row = QHBoxLayout()
        for text, slot, tip in (('Add...', self._add, 'A token made here, with its owner'),
                                ('Remove...', self._remove, 'Its subscription removed (its digits free), its songs too if asked'),
                                ('Log out everywhere', self._log_out, 'Every browser logged in to it asked to log in again')):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
            setattr(self, '_' + text.split()[0].strip('.').lower() + '_button', button)
        row.addStretch(1)
        close = QPushButton('Close')
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)
        web.changed.connect(self.refresh)
        self.refresh()

    def _selected(self):
        rows = {index.row() for index in self.table.selectedIndexes()}
        return [self.table.item(row, 0).text() for row in sorted(rows)]

    def _buttons(self):
        selected = self._selected()
        self._remove_button.setEnabled(bool(selected))
        self._log_button.setEnabled(any(self.table.item(row, 5).text() not in ('', '0') for row in
                                        {index.row() for index in self.table.selectedIndexes()}))

    def refresh(self):
        if self.table.state() == QAbstractItemView.State.EditingState:
            return
        from PyQt6.QtWidgets import QTableWidgetItem
        selected = set(self._selected())
        self.table.blockSignals(True)
        rows = self.web.subscriptions()
        self.table.setRowCount(len(rows))
        for row, subscription in enumerate(rows):
            values = [subscription['token'], subscription['name'], subscription['email'],
                      'yes' if subscription['verified'] else ('' if not subscription['email'] else 'no'),
                      str(subscription['songs']), str(subscription['logins'])]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column not in (1, 2):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column in (4, 5):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if not subscription['email'] and column in (1, 2):
                    item.setToolTip('No owner yet: set a name and an email address')
                self.table.setItem(row, column, item)
            if subscription['token'] in selected:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        for column in (0, 1, 3, 4, 5):
            self.table.resizeColumnToContents(column)
        self._buttons()

    def _edited(self, item):
        row = item.row()
        token, name, email = (self.table.item(row, column).text() for column in (0, 1, 2))
        if not name.strip() and not email.strip():
            return
        if not name.strip() or not EMAIL.match(email.strip()):     # both needed: the other one asked for next
            self.table.editItem(self.table.item(row, 1 if not name.strip() else 2))
            return
        error = self.web.set_owner(token, name, email)
        if error:
            QMessageBox.warning(self, 'Web users', error)
        self.refresh()

    def _add(self):
        token, ok = QInputDialog.getText(self, 'Add a subscription', 'Token (5 digits):')
        token = token.strip()
        if not ok or not token:
            return
        if not TOKEN.match(token):
            QMessageBox.warning(self, 'Add a subscription', 'A token is 5 digits')
            return
        if self.web.owner(token):
            QMessageBox.warning(self, 'Add a subscription', f'{token} has an owner already: change it in the list')
            return
        name, ok = QInputDialog.getText(self, 'Add a subscription', f'{token}: the owner\'s name:')
        if not ok:
            return
        email, ok = QInputDialog.getText(self, 'Add a subscription', f'{token}: the owner\'s email address:')
        if not ok:
            return
        error = self.web.set_owner(token, name, email)
        if error:
            QMessageBox.warning(self, 'Add a subscription', error)

    def _remove(self):
        tokens = self._selected()
        if not tokens:
            return
        box = QMessageBox(QMessageBox.Icon.Question, 'Remove', f'Remove the subscription{"s" if len(tokens) > 1 else ""} of '
                          + ', '.join(tokens) + '?', parent=self)
        box.setInformativeText('Its owner and logins are forgotten: the digits are free again. Its songs and their '
                               'negatives can stay (a token with no owner), or go too.')
        keep = box.addButton('Remove, keep the songs', QMessageBox.ButtonRole.AcceptRole)
        everything = box.addButton('Remove with the songs', QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() in (keep, everything):
            for token in tokens:
                self.web.remove_owner(token, songs=box.clickedButton() is everything)

    def _log_out(self):
        for token in self._selected():
            self.web.log_out_everywhere(token)


class _DropTree(QTreeWidget):
    """The jobs' tree, taking files dropped (dropped(paths, the token of the row under the drop or None))."""

    dropped = pyqtSignal(list, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(False)

    @staticmethod
    def top(item):
        while item is not None and item.parent() is not None:
            item = item.parent()
        return item

    def token_at(self, item):
        item = self.top(item)
        return item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            item = self.itemAt(event.position().toPoint())
            self.setCurrentItem(self.top(item))
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if not paths:
            return super().dropEvent(event)
        event.acceptProposedAction()
        self.dropped.emit(paths, self.token_at(self.itemAt(event.position().toPoint())))


class WebQueueView(QWidget):
    """The songs sent from the LAN, by token: started here (Start, Start all waiting), removed. play(tracks): played
    here (the window's), from the right-click menu."""

    play = pyqtSignal(list)

    def __init__(self, web, parent=None):
        super().__init__(parent)
        self.web = web
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        top = QHBoxLayout()
        self.address = QLabel()
        self.address.setStyleSheet('color: palette(placeholder-text);')
        # (a long address, a long status, the buttons: none of them a reason for the browser's pane to be wider;
        # the pane takes the widest of its tabs, this one too while hidden)
        self.address.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(self.address, 1)
        self.subscriptions_button = QPushButton('Web users...')
        self.subscriptions_button.setToolTip('The tokens, their owners\' names and email addresses: edited')
        self.subscriptions_button.clicked.connect(self.show_subscriptions)
        top.addWidget(self.subscriptions_button)
        self.info_button = QPushButton('Web page...')
        self.info_button.setToolTip("The web page's addresses on the LAN, and its port")
        self.info_button.clicked.connect(self.show_info)
        top.addWidget(self.info_button)
        layout.addLayout(top)
        self._info = None
        self.tree = _DropTree()
        self.tree.dropped.connect(self.put)
        self.tree.setToolTip("Drop songs here (on a token: into its library), or paste them (Ctrl+V)")
        self.tree.setHeaderLabels(['Owner / album / song', 'State', 'Download', 'Sent', 'Size'])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.itemSelectionChanged.connect(self._buttons)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemDoubleClicked.connect(lambda item, column: self.web.start_jobs(self._selected(STARTABLE)))
        layout.addWidget(self.tree, 1)
        from cutemusic.browser import _FlowLayout
        buttons = QWidget()
        row = _FlowLayout(buttons, spacing=6)      # wrapped onto more lines when the pane is narrow
        row.setContentsMargins(0, 0, 0, 0)
        self.start_button = QPushButton('Start')
        self.start_button.setToolTip("Make the selected songs' negatives (a token: all its songs not made)")
        self.start_button.clicked.connect(lambda: self.web.start_jobs(self._selected(STARTABLE)))
        self.start_all_button = QPushButton('Start all waiting')
        self.start_all_button.clicked.connect(lambda: self.web.start_jobs(self.web.waiting()))
        self.download_button = QPushButton('Allow download')
        self.download_button.setToolTip('The selected negatives (a token: all its songs) downloadable from the web '
                                        'page; without it they are only played there')
        self.download_button.clicked.connect(self._toggle_download)
        self.set_button = QPushButton('Set negative...')
        self.set_button.setToolTip('The selected song made already: its negative chosen (a FLAC), not made again')
        self.set_button.clicked.connect(self._set_negative)
        self.remove_button = QPushButton('Remove')
        self.remove_button.setToolTip('Out of the queue, the song sent and its negative deleted (not the one being made)')
        self.remove_button.clicked.connect(lambda: self.web.remove(self._selected(('song', 'waiting', 'queued', 'done', 'failed'))))
        self.add_button = QPushButton('Add songs...')
        self.add_button.setToolTip("Songs of this computer into a token's library: played on its web page, their "
                                   'negatives made only with Start (also: drop them here, or paste them with Ctrl+V)')
        self.add_button.clicked.connect(self._add)
        for button in (self.start_button, self.start_all_button, self.set_button, self.download_button, self.remove_button,
                       self.add_button):
            row.addWidget(button)
        layout.addWidget(buttons)
        self.status = QLabel()
        self.status.setStyleSheet('color: palette(placeholder-text);')
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.status)
        paste = QShortcut(QKeySequence.StandardKey.Paste, self, context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        paste.activated.connect(self._paste)
        web.changed.connect(self.refresh)
        self._shown = None
        self.refresh()

    def _selected(self, states):
        identifiers = []
        def songs(item):        # a song, or the songs of a token or an album
            if not item.childCount():
                return [item]
            return [song for index in range(item.childCount()) for song in songs(item.child(index))]
        for item in self.tree.selectedItems():
            identifiers += [job.data(0, Qt.ItemDataRole.UserRole) for job in songs(item)
                            if job.data(1, Qt.ItemDataRole.UserRole) in states]
        return list(dict.fromkeys(identifiers))

    def _context_menu(self, position):
        """Right-click on songs (a token: all its songs): play them here, their negatives; start, stop making them;
        download; set a negative; remove."""
        item = self.tree.itemAt(position)
        if item is None:
            return
        if not item.isSelected():
            self.tree.clearSelection()
            item.setSelected(True)
        every = self._selected(tuple(STATES))
        startable, running = self._selected(STARTABLE), self._selected(('queued', 'making'))
        made = self._selected(('done',))
        count = lambda ids: f' ({len(ids)} songs)' if len(ids) > 1 else ''
        menu = QMenu(self)
        menu.addAction('Play' + count(every), lambda: self.play.emit(self.web.tracks(every))).setEnabled(bool(every))
        menu.addAction('Play negative' + count(made), lambda: self.play.emit(self.web.tracks(made, negatives=True))).setEnabled(bool(made))
        menu.addSeparator()
        menu.addAction('Start making negative' + count(startable), lambda: self.web.start_jobs(startable)).setEnabled(bool(startable))
        menu.addAction('Stop making negative' + count(running), lambda: self.web.stop_jobs(running)).setEnabled(bool(running))
        menu.addSeparator()
        allowed = {job['id'] for job in self.web.all() if job.get('download')}
        forbid = bool(every) and all(identifier in allowed for identifier in every)
        menu.addAction('Forbid download' if forbid else 'Allow download', self._toggle_download).setEnabled(bool(every))
        menu.addAction('Set negative...', self._set_negative).setEnabled(len(every) == 1 and not running)
        menu.addSeparator()
        removable = [identifier for identifier in every if identifier not in self._selected(('making',))]
        menu.addAction('Remove' + count(removable), lambda: self.web.remove(removable)).setEnabled(bool(removable))
        menu.exec(self.tree.viewport().mapToGlobal(position))

    serve_action = None             # the window's (checkable): served or not; WebInfo's Enable

    _subscriptions = None

    def show_subscriptions(self):
        if self._subscriptions is None:
            self._subscriptions = Subscriptions(self.web, self.window())
        self._subscriptions.refresh()
        util.bring_to_front(self._subscriptions, self.window())
        self._subscriptions.show()

    def show_info(self):
        if self._info is None:
            self._info = WebInfo(self.web, self.window(), self.serve_action)
        self._info.refresh()
        util.bring_to_front(self._info, self.window())
        self._info.show()

    def _token(self, token=None, ask=False):
        """The token songs go into: given (dropped on its row), else the selected one's, else asked (the only
        one there is proposed; ask: asked, none proposed: a new one); None when not given."""
        if token is None and not ask:
            selected = self.tree.selectedItems()
            tokens = {self.tree.token_at(item) for item in selected}
            token = tokens.pop() if len(tokens) == 1 else None
        if token is None:
            tokens = self.web.tokens()
            token, ok = QInputDialog.getText(self.window(), 'Into a token\'s library', 'Token (5 digits): the web page of '
                                             'that token shows the songs', text=tokens[0] if len(tokens) == 1 and not ask else '')
            token = token.strip()
            if not ok:
                return None
        if not TOKEN.match(token):
            self.status.setText(f'"{token}": a token is 5 digits')
            return None
        return token

    def put(self, paths, token=None, ask=False):
        """Songs (or folders of them) into a token's queue: copied in, waiting for Start."""
        token = self._token(token, ask)
        if token is None:
            return
        count = self.web.add_local(token, paths)
        self.status.setText(f'{count} song' + ('s' if count != 1 else '') + f' put in {token}\'s library (Start: their negatives)'
                            if count else 'No songs there (' + ' '.join(sorted(AUDIO_EXTENSIONS)) + ')')

    def _add(self):
        from cutemusic.library import music_folder
        paths, kind = QFileDialog.getOpenFileNames(self, "Songs into a token's library", music_folder(),
                                                   'Music (' + ' '.join('*' + extension for extension in sorted(AUDIO_EXTENSIONS)) + ')')
        if paths:
            self.put(paths)

    def _paste(self):
        """Files copied in a file manager (their URLs), or paths as text, one a line."""
        data = QApplication.clipboard().mimeData()
        if data is None:
            return
        paths = [url.toLocalFile() for url in data.urls() if url.isLocalFile()] if data.hasUrls() else \
            [line.strip() for line in data.text().splitlines() if line.strip() and os.path.exists(line.strip())]
        if paths:
            self.put(paths)
        else:
            self.status.setText('Nothing to paste: copy songs in a file manager first')

    def _set_negative(self):
        jobs = self._selected(('song', 'waiting', 'queued', 'failed', 'done'))
        if len(jobs) != 1:
            return
        from cutemusic.negatives import negatives_folder
        path, kind = QFileDialog.getOpenFileName(self, 'The negative of this song', negatives_folder(), 'FLAC (*.flac)')
        if path:
            error = self.web.set_negative(jobs[0], path)
            self.status.setText(f'Not set: {error}' if error else 'Negative set: made')

    def _toggle_download(self):
        selected = self._selected(tuple(STATES))
        allowed = {job['id'] for job in self.web.all() if job.get('download')}
        self.web.set_download(selected, not all(identifier in allowed for identifier in selected))

    def _buttons(self):
        selected = self._selected(tuple(STATES))
        allowed = {job['id'] for job in self.web.all() if job.get('download')}
        forbid = bool(selected) and all(identifier in allowed for identifier in selected)
        self.download_button.setText('Forbid download' if forbid else 'Allow download')
        self.download_button.setEnabled(bool(selected))
        self.start_button.setEnabled(bool(self._selected(STARTABLE)))
        self.set_button.setEnabled(len(self._selected(('song', 'waiting', 'queued', 'failed', 'done'))) == 1)
        self.remove_button.setEnabled(bool(self._selected(('song', 'waiting', 'queued', 'done', 'failed'))))
        self.start_all_button.setEnabled(bool(self.web.waiting()))

    def refresh(self):
        self.address.setText(f'Serving on port {self.web.port}' if self.web.running else 'Not serving')
        jobs = self.web.all()
        key = [(job['id'], job['state'], job['percent'], job['error'], job.get('download'), job.get('requested'),
                self.web.owner(job['token']),
                self.web.email(job['token']),
                bool(job.get('tags'))) for job in jobs]
        if key == self._shown:
            return
        self._shown = key
        selected = set(self._selected(tuple(STATES)))
        # what was folded: tokens and albums ((token, album key))
        collapsed = set()
        for index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(index)
            if not top.isExpanded():
                collapsed.add(top.data(0, Qt.ItemDataRole.UserRole))
            for row in range(top.childCount()):
                album = top.child(row)
                if album.childCount() and not album.isExpanded():
                    collapsed.add((top.data(0, Qt.ItemDataRole.UserRole), album.data(2, Qt.ItemDataRole.UserRole)))
        self.tree.clear()
        groups, albums = {}, {}
        # the most recent first: the tokens by their latest song, in a token its albums by their latest song, in an
        # album its songs, the latest on top
        latest_token, latest_album = {}, {}
        for job in jobs:
            latest_token[job['token']] = max(latest_token.get(job['token'], 0), job['added'])
            album = (job['token'], _album_key(job))
            latest_album[album] = max(latest_album.get(album, 0), job['added'])
        jobs = sorted(jobs, key=lambda job: (-latest_token[job['token']], job['token'],
                                             -latest_album[(job['token'], _album_key(job))], _album_key(job), -job['added']))
        for job in jobs:
            group = groups.get(job['token'])
            if group is None:
                group = groups[job['token']] = QTreeWidgetItem(self.tree, [self.web.title(job['token']), '', '', ''])
                group.setToolTip(0, f'Token {job["token"]}')
                group.setData(0, Qt.ItemDataRole.UserRole, job['token'])
                font = group.font(0)
                font.setBold(True)
                group.setFont(0, font)
            tags, album_key = job.get('tags') or {}, _album_key(job)
            album = albums.get((job['token'], album_key))
            if album is None:
                artist = tags.get('albumartist') or tags.get('artist') or ''
                title = (tags.get('album') or 'Unknown album') + (f'  —  {artist}' if artist and tags.get('album') else '') + \
                    (f' ({tags["year"]})' if tags.get('year') and tags.get('album') else '')
                album = albums[(job['token'], album_key)] = QTreeWidgetItem(group, [title, '', '', ''])
                album.setData(0, Qt.ItemDataRole.UserRole, job['token'])
                album.setData(2, Qt.ItemDataRole.UserRole, album_key)
                font = album.font(0)
                font.setBold(True)
                album.setFont(0, font)
            state = STATES[job['state']] + (f' {job["percent"]}%' if job['state'] == 'making' else '')
            if job['error']:
                state += f': {job["error"]}'
            requested = job.get('requested') and not job.get('download')
            artist = tags.get('artist') if tags.get('artist') and tags.get('artist') != (tags.get('albumartist') or tags.get('artist')) else ''
            text = (f'{tags["track"]}. ' if tags.get('track') else '') + _song_title(job) + (f'  —  {artist}' if artist else '')
            size = job.get('size') or 0
            item = QTreeWidgetItem(album, [text, state, 'allowed' if job.get('download') else 'requested' if requested else 'play only',
                                           time.strftime('%d %b %H:%M', time.localtime(job['added'])),
                                           f'{size / 1048576:.1f} MB' if size >= 1048576 else f'{max(1, round(size / 1024))} kB' if size else ''])
            item.setTextAlignment(4, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setToolTip(0, job['name'])
            if requested:       # its download asked for on the page (Request download): a bell, the time asked
                item.setText(0, '🔔 ' + text)
                asked = 'Its download requested from the page, ' + time.strftime('%d %b %H:%M', time.localtime(job['requested'])) + \
                    ' (Allow download)'
                item.setToolTip(0, f'{job["name"]}\n{asked}')
                item.setToolTip(2, asked)
                item.setForeground(2, QColor(WAITING_COLOR))
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
            item.setData(0, Qt.ItemDataRole.UserRole, job['id'])
            item.setData(1, Qt.ItemDataRole.UserRole, job['state'])
            item.setToolTip(1, state)
            if job['state'] in ('waiting', 'failed'):
                item.setForeground(1, QColor(WAITING_COLOR if job['state'] == 'waiting' else FAILED_COLOR))
            if job['id'] in selected:
                item.setSelected(True)
        for (token, album_key), album in albums.items():
            count = album.childCount()
            waiting = sum(1 for index in range(count) if album.child(index).data(1, Qt.ItemDataRole.UserRole) == 'waiting')
            album.setText(1, f'{waiting} waiting' if waiting else f'{count} song' + ('s' if count != 1 else ''))
            album.setForeground(1, QColor(WAITING_COLOR) if waiting else QColor())
            album.setExpanded(bool(waiting) or (token, album_key) not in collapsed)
        for token, group in groups.items():
            waiting = sum(1 for (other, key), album in albums.items() if other == token
                          for index in range(album.childCount()) if album.child(index).data(1, Qt.ItemDataRole.UserRole) == 'waiting')
            group.setText(1, f'{waiting} waiting' if waiting else '')
            group.setForeground(1, QColor(WAITING_COLOR))
            group.setExpanded(bool(waiting) or token not in collapsed)
        for column in (1, 2, 3, 4):
            self.tree.resizeColumnToContents(column)
        self._buttons()


# The page

PAGE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AG CuteMusic — Negatives</title>
<style>
  /* the app's dark theme (theme.py): window, base, alternate rows, highlight, the Web tab's amber */
  :root { --window:#1b1e25; --base:#14171c; --alt:#1f232b; --button:#2a2f3a; --light:#3a404d; --mid:#5c6270;
          --dark:#101318; --text:#e8eaf0; --dim:#8a90a0; --placeholder:#6c7282; --highlight:#3d6fb8;
          --amber:#f0a030; --red:#e05a4f; --green:#6fbf73; }
  * { box-sizing:border-box; }
  html, body { height:100%; }
  body { margin:0; background:var(--window); color:var(--text); font:14px/1.4 system-ui, -apple-system, "Segoe UI", Ubuntu, sans-serif;
         display:flex; flex-direction:column; }
  /* the visual pane: the Amplifier view, dark plate */
  #visual { background:var(--dark); display:flex; justify-content:center; align-items:center; padding:10px; min-height:120px; }
  #visual { position:relative; }
  #amp { position:relative; width:calc(min(1000px, 100%) * var(--amp-size, 0.9)); }
  /* the amplifier's size, as the app's − + at the top right */
  #ampSize { position:absolute; top:4px; right:6px; display:flex; }
  #ampSize button { background:none; border:none; color:var(--dim); padding:1px 6px; font-size:19px; font-weight:700; line-height:1; }
  #ampSize button:hover { color:var(--text); } #ampSize button:disabled { color:#3a3f48; cursor:default; }
  #amp img { width:100%; height:auto; display:block; }
  #amp canvas { position:absolute; inset:0; width:100%; height:100%; touch-action:none; }
  #ampTip { position:fixed; z-index:8; background:var(--window); color:var(--text); border:1px solid var(--light);
            border-radius:4px; padding:2px 8px; font-size:12px; pointer-events:none; white-space:nowrap; }
  #visual .fallback { font:600 28px/1 system-ui; letter-spacing:-1px; color:var(--dim); padding:30px; }
  /* the library pane */
  #pane { flex:1; display:flex; flex-direction:column; min-height:0; max-width:1100px; width:100%; margin:0 auto; }
  .top { display:flex; align-items:center; gap:8px; padding:8px 8px 0; }
  .source { position:relative; }
  .source > button { display:flex; align-items:center; gap:6px; }
  .source .menu { position:absolute; top:100%; left:0; z-index:5; background:var(--window); border:1px solid var(--light);
                  border-radius:6px; padding:4px 0; min-width:180px; box-shadow:0 6px 20px #0008; }
  .source .menu div { padding:6px 14px; cursor:pointer; } .source .menu div:hover { background:var(--highlight); }
  .source .menu .section { color:var(--dim); font-size:12px; cursor:default; } .source .menu .section:hover { background:none; }
  button { background:var(--button); color:var(--text); border:1px solid var(--light); border-radius:5px; padding:4px 12px;
           font:inherit; cursor:pointer; }
  button:hover { border-color:var(--mid); } button:disabled { color:var(--placeholder); cursor:default; }
  input[type=search] { flex:1; background:var(--base); color:var(--text); border:1px solid var(--light); border-radius:5px;
                       padding:4px 8px; font:inherit; min-width:0; }
  input[type=search]::placeholder { color:var(--placeholder); }
  .tabs { display:flex; gap:2px; padding:8px 8px 0; border-bottom:1px solid var(--light); }
  .tabs div { padding:6px 16px; color:var(--dim); cursor:pointer; border-bottom:2px solid transparent; margin-bottom:-1px; }
  .tabs div.on { color:var(--text); border-bottom-color:var(--highlight); }
  .tabs div.waiting { color:var(--amber); }
  .view { flex:1; overflow:auto; background:var(--base); min-height:160px; position:relative; }
  .view.over { outline:2px dashed var(--amber); outline-offset:-6px; }
  table { width:100%; border-collapse:collapse; }
  th { position:sticky; top:0; background:var(--window); color:var(--dim); font-weight:normal; text-align:left;
       padding:4px 8px; border-bottom:1px solid var(--light); white-space:nowrap; }
  td { padding:4px 8px; white-space:nowrap; }
  td.title { white-space:normal; word-break:break-word; width:100%; }
  tr:nth-child(even) td { background:var(--alt); }
  tr.pending td { color:var(--placeholder); }
  tr.album td { background:var(--window) !important; cursor:pointer; padding-top:8px; border-top:1px solid var(--light); white-space:normal; }
  tr.album .fold { color:var(--dim); display:inline-block; width:14px; }
  tr.song td:first-child { padding-left:30px; }     /* one level in from its album */
  tr[data-play] { cursor:pointer; } tr[data-play]:hover td { color:var(--amber); }
  tr.album:hover td { color:var(--amber); }
  .by { color:var(--dim); font-weight:normal; }
  td.state.waiting { color:var(--amber); } td.state.failed { color:var(--red); } td.state.done { color:var(--green); }
  td.num { text-align:right; color:var(--dim); font-variant-numeric:tabular-nums; }
  .bar { display:inline-block; width:90px; height:6px; background:var(--dark); border-radius:3px; overflow:hidden; vertical-align:middle; margin-left:6px; }
  .bar i { display:block; height:100%; background:var(--amber); transition:width .4s; }
  a.get { color:#7cc4ff; text-decoration:none; } a.get:hover { text-decoration:underline; }
  .x { background:none; border:none; color:var(--placeholder); padding:0 4px; font-size:16px; line-height:1; }
  .x:hover { color:var(--red); }
  .empty { color:var(--placeholder); text-align:center; padding:40px 16px; }
  .play { background:none; border:none; color:var(--text); padding:0 6px; font-size:13px; }
  .play:hover { color:var(--amber); } tr.playing td { color:var(--amber); }
  .only { color:var(--placeholder); font-size:12px; }
  /* the lyrics being sung, over the player (visuals.LyricsStrip's): three lines, the middle one sung */
  #lyrics { position:relative; background:var(--dark); border-top:1px solid var(--light); padding:8px 56px; text-align:center;
            font-size:calc(var(--lyrics, 1) * 17px); line-height:1.6; }
  #lyrics div { color:var(--dim); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-height:1.6em; }
  #lyrics div.sung { color:var(--text); font-weight:700; font-size:1.3em; line-height:1.5; }
  #lyrics .size { position:absolute; right:6px; bottom:4px; display:flex; }
  #lyrics .size button { background:none; border:none; color:var(--dim); padding:1px 4px; font-size:13px; font-weight:600; }
  #lyrics .size button:hover { color:var(--text); }
  #player { display:flex; align-items:center; gap:10px; padding:6px 8px; background:var(--window); border-top:1px solid var(--light); }
  #player .now { flex:0 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--amber); }
  /* Original | Negative: both shown, the one playing lit (amber), the other a click away */
  #swap { flex:none; display:inline-flex; }
  #swap button { border-radius:0; padding:4px 12px; color:var(--dim); }
  #swap button:first-child { border-radius:12px 0 0 12px; } #swap button:last-child { border-radius:0 12px 12px 0; border-left:none; }
  #swap button.on { background:var(--amber); border-color:var(--amber); color:var(--dark); font-weight:600; cursor:default; }
  #swap button:not(.on):hover { color:var(--text); }
  #ask { flex:none; } #ask.asked { color:var(--amber); border-color:var(--amber); } #ask:disabled { opacity:0.85; }
  /* the player's own controls: the whole song's length shown; while not allowed to be downloaded only its middle
     half (PREVIEW) is played and sought in (lit), the rest shaded */
  #toggle { flex:none; width:34px; padding:4px 0; }
  #time { flex:none; color:var(--dim); font-variant-numeric:tabular-nums; font-size:13px; }
  #seek { flex:1; min-width:160px; height:22px; position:relative; cursor:pointer; touch-action:none; }
  #seek::before { content:''; position:absolute; left:0; right:0; top:9px; height:4px; border-radius:2px; background:var(--dark); }
  #seek .allowed { position:absolute; top:9px; height:4px; background:var(--light); border-radius:2px; }
  #seek .done { position:absolute; top:9px; height:4px; background:var(--amber); border-radius:2px; }
  #seek .knob { position:absolute; top:5px; width:12px; height:12px; margin-left:-6px; border-radius:6px; background:var(--text); }
  #seek.preview::after { content:'preview'; position:absolute; right:0; top:-6px; font-size:10px; color:var(--placeholder); }
  .status { color:var(--dim); font-size:13px; padding:6px 8px 10px; min-height:1.4em; }
  .status.error { color:var(--red); }
  /* the token */
  #login { position:fixed; inset:0; background:#0009; display:flex; align-items:center; justify-content:center; z-index:9; }
  #login .card { background:var(--window); border:1px solid var(--light); border-radius:10px; padding:24px; width:min(380px, 92vw); text-align:center; }
  #login h2 { margin:0 0 6px; font-size:19px; font-weight:600; }
  #login p { color:var(--dim); margin:0 0 14px; font-size:13px; }
  #login input { display:block; width:100%; margin:0 0 10px; }
  #login .big { display:block; width:100%; padding:12px; margin:0 0 10px; font-size:16px; font-weight:600; text-align:left; }
  #login .big small { display:block; font-size:12px; font-weight:normal; color:var(--dim); margin-top:2px; }
  #login .big:hover { border-color:var(--amber); }
  #login .note { color:var(--amber) !important; }
  #login #token, #login #newToken { width:9ch; margin:0 auto 10px; }
  /* the step's action: the button, full width, lit; Back under it: a quiet link */
  #login .primary { display:block; width:100%; padding:9px; margin-top:4px; background:var(--amber); border-color:var(--amber);
                    color:var(--dark); font-weight:600; }
  #login .primary:hover { filter:brightness(1.08); }
  #login .back { background:none; border:none; color:var(--dim); font-size:12px; padding:6px; margin-top:8px; }
  #login .back:hover { color:var(--text); text-decoration:underline; }
  #login .links { margin:14px 0 0; } #login .links a { color:var(--dim); font-size:12px; } #login .links a:hover { color:var(--text); }
  #token, #newToken { width:8ch; margin-left:auto !important; margin-right:auto !important; font:600 28px/1 ui-monospace, Menlo, monospace; letter-spacing:6px; text-align:center; background:var(--base);
           color:var(--amber); border:1px solid var(--light); border-radius:6px; padding:8px; margin-bottom:12px; }
  #token:focus { outline:none; border-color:var(--amber); }
  #owner, #email, #code, #loginEmail { width:100%; background:var(--base); color:var(--text); border:1px solid var(--light); border-radius:6px; padding:6px 8px;
           font:inherit; margin-bottom:12px; text-align:center; }
  #owner:focus, #email:focus, #code:focus, #loginEmail:focus { outline:none; border-color:var(--amber); }
  #loginNote { color:var(--amber) !important; }

  .hidden { display:none !important; }
</style>
</head>
<body>
<div id="visual"><div id="amp"><img id="ampImage" src="/amp.png" alt="AG CuteMusic" onerror="this.parentNode.outerHTML='&lt;div class=fallback&gt;AG CuteMusic&lt;/div&gt;'"><canvas id="ampCanvas" class="hidden"></canvas></div>
  <div id="ampSize"><button id="ampSmaller" title="Smaller amplifier">−</button><button id="ampBigger" title="Bigger amplifier">+</button></div></div>
<div id="ampTip" class="hidden"></div>
<div id="pane">
  <div class="tabs"><div data-tab="library" class="on">Library</div><div data-tab="negatives">Negatives</div></div>
  <div class="top">
    <div class="source"><button id="sourceButton">&#128187; <span id="sourceName">Web</span> &#9662;</button>
      <div class="menu hidden" id="sourceMenu"><div class="section">Sent from the web page</div><div id="current"></div><div id="change">New token…</div><div id="logout">Log out</div></div></div>
    <input type="search" id="filter" placeholder="Filter">
    <button id="add">Add songs…</button>
    <button id="logoutButton" title="This browser forgets the token: back to the first screen">Log out</button>
    <input type="file" id="files" multiple accept="audio/*,.flac,.mp3,.ogg,.opus,.m4a,.wav,.aiff" class="hidden">
  </div>
  <div class="view" id="view"></div>
  <div id="lyrics" class="hidden"><div></div><div class="sung"></div><div></div>
    <span class="size"><button id="smaller" title="Smaller lyrics">A−</button><button id="bigger" title="Bigger lyrics">A+</button></span></div>
  <div id="player" class="hidden"><span class="now" id="now"></span><span id="swap" class="hidden"><button data-version="song" title="The song, with its singer">Original</button><button data-version="neg" title="Its negative: no singer, for karaoke">Negative</button></span><audio id="audio" preload="none"></audio>
    <button id="toggle" title="Play">▶</button><span id="time">0:00 / 0:00</span>
    <div id="seek"><div class="allowed"></div><div class="done"></div><div class="knob"></div></div>
    <button id="ask" class="hidden">Request download</button></div>
  <div class="status" id="status"></div>
</div>
<div id="login"><div class="card">
  <!-- landing: the two ways in -->
  <div id="landing">
    <h2>Karaoke negatives</h2>
    <p>Songs without their singer, to sing them yourself.</p>
    <button type="button" class="big" id="toRequest">Request negatives<small>New here: enroll with your email address</small></button>
    <button type="button" class="big" id="toRetrieve">Retrieve negatives<small>Enrolled: your email address and token</small></button>
  </div>
  <!-- Request negatives: email and name, the code emailed (when not verified yet), then a token of one's own -->
  <form id="enrol" class="hidden" autocomplete="on">
    <h2>Request negatives</h2>
    <p id="enrolText"></p>
    <div id="stepWho">
      <input type="text" id="owner" maxlength="40" autocomplete="name" placeholder="Display name">
      <input type="email" id="email" maxlength="120" autocomplete="email" placeholder="Email address">
    </div>
    <div id="stepCode" class="hidden">
      <input type="text" id="code" inputmode="numeric" maxlength="6" autocomplete="one-time-code" placeholder="The code emailed to you">
    </div>
    <div id="stepToken" class="hidden">
      <input type="text" id="newToken" inputmode="numeric" maxlength="5" autocomplete="off" placeholder="•••••">
    </div>
    <p class="note hidden" id="enrolNote"></p>
    <button type="submit" id="enrolNext" class="primary">Continue</button>
    <button type="button" class="back">← Back</button>
    <p class="links"><a href="#" id="resend" class="hidden">Send the code again</a></p>
  </form>
  <!-- Retrieve negatives: the email address and the token -->
  <form id="retrieve" class="hidden" autocomplete="on">
    <h2>Retrieve negatives</h2>
    <p>Your email address and your token.</p>
    <input type="email" id="loginEmail" maxlength="120" autocomplete="email" placeholder="Email address">
    <input type="text" id="token" inputmode="numeric" maxlength="5" autocomplete="off" placeholder="Token •••••">
    <p class="note hidden" id="loginNote"></p>
    <button type="submit" class="primary">Log in</button>
    <button type="button" class="back">← Back</button>
    <p class="links"><a href="#" id="forgot">Forgot your token?</a></p>
  </form>
  <p class="links hidden" id="cancelRow"><a href="#" id="cancel">Cancel: stay with the token in use</a></p>
</div></div>
<script>
const $ = id => document.getElementById(id);
let playing = '', token = '', timer = null, uploads = {}, jobs = [], tab = 'library', offline = false, note = '', noteError = false;
const STATE = {song:'', waiting:'requested', queued:'queued', making:'making', done:'made', failed:'failed', uploading:'sending'};

function store(value, secret) {
  try {
    value ? localStorage.setItem('token', value) : localStorage.removeItem('token');
    secret ? localStorage.setItem('key', secret) : localStorage.removeItem('key');
  } catch (e) {}
}
function storedKey() { try { return localStorage.getItem('key') || ''; } catch (e) { return ''; } }
// a login's key (the token and its email address), given with every request: &token=…&key=…
let authKey = '';
function auth() { return `token=${token}&key=${authKey}`; }
function stored() { try { return localStorage.getItem('token') || ''; } catch (e) { return ''; } }
function esc(text) { const d = document.createElement('div'); d.textContent = text == null ? '' : text; return d.innerHTML; }
function size(bytes) { return bytes > 1048576 ? (bytes / 1048576).toFixed(1) + ' MB' : Math.ceil(bytes / 1024) + ' kB'; }
function stem(name) { return name.replace(/\.[^.]+$/, ''); }
function when(seconds) { return new Date(seconds * 1000).toLocaleString([], {day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit'}); }
function say(text, error) { note = text; noteError = !!error; draw(); }

// Coming in: Request negatives (enrolling: a display name and an email address, the address verified with a code
// emailed to it when it is not yet, then a token of one's own: 5 digits not taken) or Retrieve negatives (an email
// address and a token made with it). The server gives a key, kept in this browser, sent with every request.
const VALID = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
let step = 'who';
function shown(id) { ['landing', 'enrol', 'retrieve'].forEach(other => $(other).classList.toggle('hidden', other !== id)); }
function landing() {
  shown('landing'); $('login').classList.remove('hidden');
  $('cancelRow').classList.toggle('hidden', !(token && authKey));
}
function say_(id, text) { $(id).textContent = text; $(id).classList.toggle('hidden', !text); }
function enrolStep(name) {
  step = name;
  $('stepWho').classList.toggle('hidden', name !== 'who');
  $('stepCode').classList.toggle('hidden', name !== 'code');
  $('stepToken').classList.toggle('hidden', name !== 'token');
  $('resend').classList.toggle('hidden', name !== 'code');
  const email = $('email').value.trim();
  $('enrolText').textContent = name === 'who' ? 'Your display name and your email address (for notifications).'
                             : name === 'code' ? `A code was sent to ${email}: type it here.`
                             : `${email} is verified. Now choose your token: 5 digits of your choosing.`;
  $('enrolNext').textContent = name === 'token' ? 'Make my token' : 'Continue';
  say_('enrolNote', '');
  ({who: $('owner'), code: $('code'), token: $('newToken')})[name].focus();
}
function toRequest() { shown('enrol'); $('owner').value = storedName(); $('email').value = storedEmail(); enrolStep('who'); }
function toRetrieve() {
  shown('retrieve'); say_('loginNote', '');
  $('loginEmail').value = $('loginEmail').value || storedEmail();
  ($('loginEmail').value ? $('token') : $('loginEmail')).focus();
}
async function post(path) {
  try {
    const reply = await fetch(path, {method: 'POST'});
    return [reply.status, await reply.json().catch(() => ({}))];
  } catch (e) { return [0, {error: 'AG CuteMusic is not answering: try again'}]; }
}
async function sendCode(email) {
  const [status, data] = await post('/api/code?email=' + encodeURIComponent(email));
  if (status !== 200) { say_('enrolNote', data.error || 'not sent'); return false; }
  enrolStep('code');
  return true;
}
async function enrol() {
  const name = $('owner').value.trim(), email = $('email').value.trim();
  if (step === 'who') {
    if (!name) { say_('enrolNote', 'Your display name, please'); $('owner').focus(); return; }
    if (!VALID.test(email)) { say_('enrolNote', 'Your email address, please'); $('email').focus(); return; }
    try { localStorage.setItem('name', name); localStorage.setItem('email', email); } catch (e) {}
    let known = {};
    try { known = await (await fetch('/api/email?email=' + encodeURIComponent(email), {cache: 'no-store'})).json(); } catch (e) {}
    if (known.verified) enrolStep('token'); else await sendCode(email);
  } else if (step === 'code') {
    const code = $('code').value.trim();
    if (!/^\d{6}$/.test(code)) { say_('enrolNote', 'The 6 digits emailed to you'); return; }
    const [status, data] = await post(`/api/verify?email=${encodeURIComponent(email)}&code=${code}`);
    if (status === 200) enrolStep('token'); else say_('enrolNote', data.error || 'not verified');
  } else {
    const value = $('newToken').value.trim();
    if (!/^\d{5}$/.test(value)) { say_('enrolNote', 'Your token: 5 digits'); return; }
    const [status, data] = await post(`/api/token?token=${value}&name=${encodeURIComponent(name)}&email=${encodeURIComponent(email)}`);
    if (status === 200) return done(value, data.name, data.key, email);
    if (status === 428) { await sendCode(email); return; }        // (no longer verified: a code again)
    say_('enrolNote', status === 409 && /registered/.test(data.error || '') ? `${value} is taken: choose other digits` : data.error || 'not made');
  }
}
async function retrieve() {
  const email = $('loginEmail').value.trim(), value = $('token').value.trim();
  if (!VALID.test(email)) { say_('loginNote', 'Your email address, please'); $('loginEmail').focus(); return; }
  if (!/^\d{5}$/.test(value)) { say_('loginNote', 'Your token: 5 digits'); $('token').focus(); return; }
  const [status, data] = await post(`/api/login?token=${value}&email=${encodeURIComponent(email)}`);
  if (status === 200) return done(value, data.name, data.key, email);
  say_('loginNote', status === 404 ? `There is no token ${value}: Request negatives to make one` : data.error || 'not logged in');
}
function done(value, name, secret, email) {
  try { localStorage.setItem('email', email); } catch (e) {}
  enter(value, name, secret);
}
// the key no longer good (logged out elsewhere, or forgotten here): Retrieve asked again, the token and address kept
function loginAgain() {
  const value = token;
  clearInterval(timer); authKey = ''; store('', '');
  $('token').value = value;           // (ready in Retrieve, once chosen)
  landing();
}
function storedName() { try { return localStorage.getItem('name') || ''; } catch (e) { return ''; } }
function storedEmail() { try { return localStorage.getItem('email') || ''; } catch (e) { return ''; } }

function enter(value, name, secret) {
  if (token && token !== value) { playing = ''; $('audio').pause(); $('audio').removeAttribute('src'); $('player').classList.add('hidden'); lyrics.id = ''; sing(); }
  token = value; authKey = secret; store(value, secret); history.replaceState(null, '', '#' + value);
  try { localStorage.setItem('ownerName', name || ''); } catch (e) {}
  const title = 'Web ' + value + (name ? ' · ' + name : '');
  $('sourceName').textContent = title; $('current').textContent = '✓ ' + title;
  $('login').classList.add('hidden');
  jobs = []; note = ''; draw(); refresh(); clearInterval(timer); timer = setInterval(refresh, 2000);
}
// New token…: a new one asked for (digits not taken, a name, an email address), the token in use kept until it is
// made (Cancel, Escape or a click outside: kept)
function leave() {
  $('login').classList.remove('hidden'); toRequest();
  $('cancelRow').classList.toggle('hidden', !(token && authKey));
}
function stay() {
  if (!token || !authKey) return;
  $('login').classList.add('hidden');
}

async function refresh() {
  if (!token) return;
  try {
    const reply = await fetch('/api/jobs?' + auth(), {cache: 'no-store'});
    if (reply.status === 401) { loginAgain(); return; }
    const reply_ = await reply.json();
    jobs = reply_.jobs || []; limits = reply_.limits || limits; offline = false;
  } catch (e) { offline = true; }
  draw();
}

function matches(job) {
  const f = $('filter').value.trim().toLowerCase(), t = job.tags || {};
  return !f || [job.name, t.title, t.artist, t.albumartist, t.album].some(text => text && String(text).toLowerCase().includes(f));
}

// The songs by album, as the app's library tree: an album's row (its name, artist, year, how many; a click folds
// it), its songs in their order (disc, track); the albums by artist and name, the songs with no album last, the
// ones being sent first. Folded albums kept in this browser
const UNKNOWN = '\uffff', SENDING = '';
let folded = new Set();
try { folded = new Set(JSON.parse(localStorage.getItem('folded') || '[]')); } catch (e) {}
function albumKey(job) {
  if (!job.id) return SENDING;
  const t = job.tags || {};
  return t.album ? ((t.albumartist || t.artist || '') + '\u0001' + t.album).toLowerCase() : UNKNOWN;
}
function songTitle(job) { return (job.tags && job.tags.title) || stem(job.name); }
function ordered(list) {
  return list.slice().sort((a, b) => {
    const ka = albumKey(a), kb = albumKey(b), x = a.tags || {}, y = b.tags || {};
    if (ka !== kb) return ka < kb ? -1 : 1;
    return (x.disc || 0) - (y.disc || 0) || (x.track || 0) - (y.track || 0) || songTitle(a).localeCompare(songTitle(b));
  });
}
function tree(rows, columns, row) {
  rows = ordered(rows);
  const counts = {};
  rows.forEach(job => counts[albumKey(job)] = (counts[albumKey(job)] || 0) + 1);
  let html = '', last = null;
  for (const job of rows) {
    const key = albumKey(job);
    if (key !== last) {
      last = key;
      const t = job.tags || {}, open = !folded.has(key), count = counts[key];
      const name = key === SENDING ? 'Sending' : key === UNKNOWN ? 'Unknown album' : esc(t.album);
      const who = key === SENDING || key === UNKNOWN ? '' : esc(t.albumartist || t.artist || '');
      html += `<tr class="album" data-album="${esc(key)}"><td colspan="${columns}"><span class="fold">${open ? '▾' : '▸'}</span> ` +
              `<b>${name}</b>${who ? ` <span class="by">${who}</span>` : ''}${t.year && key !== UNKNOWN ? ` <span class="by">(${t.year})</span>` : ''}` +
              ` <span class="by">· ${count} song${count === 1 ? '' : 's'}</span></td></tr>`;
    }
    if (!folded.has(key)) html += row(job);
  }
  return html;
}
function fold(key) {
  folded.has(key) ? folded.delete(key) : folded.add(key);
  try { localStorage.setItem('folded', JSON.stringify([...folded])); } catch (e) {}
  draw();
}
function titleCell(job) {
  const t = job.tags || {}, artist = t.artist && t.artist !== (t.albumartist || t.artist) ? ` <span class="by">${esc(t.artist)}</span>` : '';
  return `<td class="title">${t.track ? `<span class="by">${t.track}.</span> ` : ''}${esc(songTitle(job))}${artist}</td>`;
}
function remove(job) { return job.state === 'making' ? '' : `<button class="x" title="Remove the song and its negative" data-remove="${job.id}">×</button>`; }

function drawLibrary() {
  const rows = Object.entries(uploads).map(([key, u]) => ({upload: key, name: u.name, size: u.size, state: 'uploading', percent: u.percent, error: u.error}))
                     .concat(jobs).filter(matches);
  if (!rows.length) return '<div class="empty">No songs yet: drop songs here, or Add songs…</div>';
  return '<table><tr><th>Title</th><th>Size</th><th>Added</th><th>Negative</th><th></th><th></th></tr>' + tree(rows, 6, job => {
    let state = STATE[job.state] == null ? job.state : STATE[job.state];
    if (job.state === 'uploading' || job.state === 'making') state += ' ' + job.percent + '%';
    if (job.error) state += ': ' + job.error;
    const key = 'song:' + job.id, on = playing === key;
    const get = !job.id ? '' : job.download ? `<a class="get" href="/api/song-download?${auth()}&id=${job.id}">Download</a>`
                                         : '<span class="only" title="Not downloadable: Request download">play only</span>';
    return `<tr class="song ${on ? 'playing' : ''}"${job.id ? ` data-play="${key}" title="Into the player"` : ''}>${titleCell(job)}<td class="num">${size(job.size)}</td>
            <td class="num">${job.added ? when(job.added) : ''}</td><td class="state ${job.state}">${esc(state)}</td>
            <td>${get}</td><td>${job.id ? remove(job) : job.upload && !job.error ? `<button class="x" title="Cancel sending it" data-cancel="${job.upload}">×</button>` : ''}</td></tr>`;
  }) + '</table>';
}

function drawNegatives() {
  const rows = jobs.filter(job => job.state === 'done').filter(matches);
  if (!rows.length) return '<div class="empty">No negatives made yet: their state is in the Library tab</div>';
  return '<table><tr><th>Title</th><th>State</th><th></th><th></th></tr>' + tree(rows, 4, job => {
    let state = STATE[job.state] + (job.error ? ': ' + job.error : '');
    if (job.state === 'making') state += ` ${job.percent}%<span class="bar"><i style="width:${job.percent}%"></i></span>`;
    else state = esc(state);
    const made = job.state === 'done';
    const key = 'neg:' + job.id;
    const get = !made ? '' : job.download ? `<a class="get" href="/api/download?${auth()}&id=${job.id}">Download</a>`
                                          : '<span class="only" title="Not downloadable: Request download">play only</span>';
    return `<tr class="song ${made ? (playing === key ? 'playing' : '') : 'pending'}"${made ? ` data-play="${key}" title="Into the player"` : ''}>${titleCell(job)}
            <td class="state ${job.state}">${state}</td><td>${get}</td><td>${remove(job)}</td></tr>`;
  }) + '</table>';
}

function draw() {
  swapButton();
  $('view').innerHTML = tab === 'library' ? drawLibrary() : drawNegatives();
  const waiting = jobs.filter(job => job.state === 'waiting').length, made = jobs.filter(job => job.state === 'done').length;
  const negatives = document.querySelector('[data-tab=negatives]');
  negatives.textContent = made ? `Negatives (${made})` : 'Negatives';
  let text = note;
  if (offline) text = 'AG CuteMusic is not answering — trying again…';
  else if (!text) text = `${jobs.length} song${jobs.length === 1 ? '' : 's'}` + (waiting ? `, ${waiting} requested` : '') +
                         (made ? `, ${made} negative${made === 1 ? '' : 's'} made` : '');
  $('status').textContent = text; $('status').classList.toggle('error', offline || noteError);
}

// a token's library: at most limits.files songs and limits.bytes (the server says so too); the ones sending counted
let limits = null;
function room(file) {
  if (!limits) return '';
  const all = jobs.map(job => job.size).concat(Object.values(uploads).filter(u => !u.error).map(u => u.size));
  if (all.length >= limits.files) return `at most ${limits.files} songs a token: remove some first`;
  const used = all.reduce((sum, size) => sum + size, 0);
  if (used + file.size > limits.bytes) return `at most ${Math.round(limits.bytes / 1048576)} MB a token (${Math.round(used / 1048576)} MB used): remove some first`;
  return '';
}

function send(file) {
  const extension = (file.name.match(/\.[^.]+$/) || [''])[0].toLowerCase();
  if (limits && limits.extensions && !limits.extensions.includes(extension)) { say(file.name + ': not a music file, not sent', true); return; }
  const full = room(file);
  if (full) { say(file.name + ': not sent, ' + full, true); return; }
  const key = Math.random().toString(36).slice(2);
  const entry = uploads[key] = {name: file.name, size: file.size, percent: 0, error: ''};
  const done = error => {
    if (!error) { delete uploads[key]; say(''); refresh(); return; }
    entry.error = error; say(file.name + ': ' + error, true); setTimeout(() => { delete uploads[key]; draw(); }, 8000);
  };
  const request = new XMLHttpRequest();
  request.open('POST', '/api/upload?' + auth() + '&name=' + encodeURIComponent(file.name));
  request.upload.onprogress = e => { if (e.lengthComputable) { entry.percent = Math.round(100 * e.loaded / e.total); draw(); } };
  request.onload = () => {
    if (request.status === 200) return done('');
    try { done(JSON.parse(request.responseText).error); } catch (e) { done('refused (' + request.status + ')'); }
  };
  request.onerror = () => done('not sent');
  request.onabort = () => { delete uploads[key]; say(file.name + ': not sent (cancelled)'); };
  entry.request = request;
  request.send(file);
  if (tab !== 'library') select('library'); else draw();
}

function select(name) {
  tab = name;
  document.querySelectorAll('.tabs div').forEach(div => div.classList.toggle('on', div.dataset.tab === name));
  draw();
}

document.querySelectorAll('.tabs div').forEach(div => div.onclick = () => select(div.dataset.tab));
$('enrol').onsubmit = e => { e.preventDefault(); enrol(); };
$('retrieve').onsubmit = e => { e.preventDefault(); retrieve(); };
$('toRequest').onclick = toRequest;
$('toRetrieve').onclick = toRetrieve;
document.querySelectorAll('#login .back').forEach(button => button.onclick = () => {
  if ($('enrol').classList.contains('hidden') || step === 'who') landing(); else enrolStep(step === 'token' ? 'who' : 'who');
});
['token', 'newToken', 'code'].forEach(id => $(id).oninput = () => { $(id).value = $(id).value.replace(/\D/g, '').slice(0, id === 'code' ? 6 : 5); });
$('sourceButton').onclick = e => { e.stopPropagation(); $('sourceMenu').classList.toggle('hidden'); };
document.addEventListener('click', () => $('sourceMenu').classList.add('hidden'));
$('change').onclick = leave;
$('cancel').onclick = e => { e.preventDefault(); stay(); };
$('resend').onclick = e => { e.preventDefault(); const email = $('email').value.trim(); if (VALID.test(email)) sendCode(email); };
// Log out: this browser forgets the token (its songs stay on the computer), back to the first screen
function logout() {
  playing = ''; $('audio').pause(); $('audio').removeAttribute('src'); $('audio').load(); $('player').classList.add('hidden');
  lyrics.id = ''; $('lyrics').classList.add('hidden');
  Object.values(uploads).forEach(upload => upload.request && upload.request.abort());      // (the ones being sent: stopped)
  if (token && authKey) post('/api/logout?' + auth());
  token = ''; authKey = ''; store('', ''); clearInterval(timer); jobs = []; uploads = {}; note = '';
  history.replaceState(null, '', location.pathname);
  $('token').value = ''; landing();
  draw();
}
$('logout').onclick = logout;
$('logoutButton').onclick = logout;
// a forgotten token: emailed to the address it was made with (the page learns nothing of it)
$('forgot').onclick = async e => {
  e.preventDefault();
  const email = $('loginEmail').value.trim();
  const note = text => say_('loginNote', text);
  if (!VALID.test(email)) { note('Your email address first, then Forgot your token?'); $('loginEmail').focus(); return; }
  try {
    const reply = await fetch('/api/recover?email=' + encodeURIComponent(email), {method: 'POST'});
    const data = await reply.json().catch(() => ({}));
    note(reply.ok ? `If ${email} made tokens here, they are on their way there: look in your mailbox.` : data.error || 'not sent');
  } catch (err) { note('AG CuteMusic is not answering: try again'); }
};
$('login').onclick = e => { if (e.target === $('login')) stay(); };
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('login').classList.contains('hidden')) stay(); });
$('filter').oninput = draw;
$('add').onclick = () => $('files').click();
$('files').onchange = () => { [...$('files').files].forEach(send); $('files').value = ''; };
// the drop taken here, never the browser's own (opening the file in a tab): every dragenter and dragover refused to
// the browser, as a copy (a file manager offers copy: a 'move' asked for would not match, and the browser opens it)
['dragenter', 'dragover'].forEach(kind => document.addEventListener(kind, e => {
  e.preventDefault();
  if (e.dataTransfer) e.dataTransfer.dropEffect = token ? 'copy' : 'none';
  if (token) $('view').classList.add('over');
}));
document.addEventListener('dragleave', e => { if (!e.relatedTarget) $('view').classList.remove('over'); });
document.addEventListener('drop', e => { e.preventDefault(); $('view').classList.remove('over'); if (token) [...e.dataTransfer.files].forEach(send); });
// a song clicked: put in the player, as it is (played and paused there); start: played at once (the next one when
// one ends)
// what is in the player: where its file starts in the song (offset: the preview's start, else 0) and the song's
// whole length (total), the bar showing the whole of it
let offset = 0, total = 0;
function placed(job) {
  offset = job.preview || 0;
  total = offset ? (job.tags && job.tags.duration) || 0 : 0;      // (else the file's own, once known)
}
function play(key, start) {    // 'song:<id>': the song itself; 'neg:<id>': its negative
  const audio = $('audio'), [kind, id] = key.split(':'), job = jobs.find(job => job.id === id);
  if (!job || (playing === key && !start)) return;
  ampConnect();
  placed(job);
  playing = key; audio.src = `/api/${kind === 'song' ? 'song' : 'stream'}?${auth()}&id=${id}`;
  audio.preload = 'metadata';
  if (start) audio.play(); else audio.load();
  showLyrics(id);
  $('now').textContent = songTitle(job); $('player').classList.remove('hidden'); draw();
}

// the song with its negative made: Original / Negative goes on with the other one from the same point (as the app)
// Request download, at the right of the player: the song in the player (and its negative) asked to be allowed to
// be downloaded, while it is not (only its middle half played); asked: its state; allowed: not there
function askButton() {
  const [kind, id] = playing.split(':'), job = jobs.find(job => job.id === id), button = $('ask');
  const shown = !!job && !job.download;
  button.classList.toggle('hidden', !shown);
  if (!shown) return;
  button.disabled = !!job.requested;
  button.classList.toggle('asked', !!job.requested);
  button.textContent = job.requested ? 'Download requested' : 'Request download';
  button.title = job.requested ? 'Download requested' : 'Request downloading it (the song and its negative)';
}
$('ask').onclick = async () => {
  const [kind, id] = playing.split(':');
  const reply = await fetch(`/api/request?${auth()}&id=${id}`, {method: 'POST'}).catch(() => null);
  if (!reply || !reply.ok) say('Not requested: ' + ((reply && (await reply.json().catch(() => ({}))).error) || 'AG CuteMusic is not answering'), true);
  else say('Download requested');
  refresh();
};
function swapButton() {
  askButton();
  const [kind, id] = playing.split(':'), job = jobs.find(job => job.id === id), button = $('swap');
  const both = !!job && job.state === 'done';
  button.classList.toggle('hidden', !both);
  if (!both) return;
  button.querySelectorAll('button').forEach(part => {
    const on = part.dataset.version === kind;
    part.classList.toggle('on', on);
    part.title = on ? 'Playing: ' + (kind === 'neg' ? 'its negative (no singer, for karaoke)' : 'the song, with its singer')
                    : 'Click: go on with ' + (part.dataset.version === 'neg' ? 'its negative (no singer, for karaoke)' : 'the song, with its singer') + ', from here';
  });
}
$('swap').onclick = e => {
  const part = e.target.closest('button');
  if (!part || part.dataset.version === playing.split(':')[0]) return;      // (the one playing: as it is)
  const audio = $('audio'), [kind, id] = playing.split(':'), at = audio.currentTime + offset, paused = audio.paused;
  const job = jobs.find(job => job.id === id), other = kind === 'neg' ? 'song' : 'neg';
  if (!job) return;
  playing = other + ':' + id;
  placed(job);
  audio.src = `/api/${other === 'song' ? 'song' : 'stream'}?${auth()}&id=${id}`;
  audio.addEventListener('loadedmetadata', () => { seekTo(at); if (!paused) audio.play(); }, {once: true});
  audio.load();
  $('now').textContent = songTitle(job);
  draw();
};
['play', 'pause', 'ended'].forEach(kind => $('audio').addEventListener(kind, draw));
// the controls
function clock(seconds) { seconds = Math.max(0, Math.floor(seconds || 0)); return Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0'); }
function length() { return total || (isFinite($('audio').duration) ? $('audio').duration + offset : 0); }
function seekTo(at) {         // a point of the whole song: within what the file has (the preview's half)
  const audio = $('audio');
  if (!isFinite(audio.duration)) return;
  audio.currentTime = Math.max(0, Math.min(audio.duration - 0.05, at - offset));
}
function showTime() {
  const audio = $('audio'), whole = length(), at = audio.currentTime + offset;
  $('toggle').textContent = audio.paused ? '▶' : '❚❚'; $('toggle').title = audio.paused ? 'Play' : 'Pause';
  $('time').textContent = clock(at) + ' / ' + clock(whole);
  const part = (seconds => whole ? Math.max(0, Math.min(100, seconds / whole * 100)) : 0);
  const from = part(offset), to = isFinite(audio.duration) ? part(offset + audio.duration) : 100;
  const allowed = $('seek').querySelector('.allowed'), done = $('seek').querySelector('.done');
  allowed.style.left = from + '%'; allowed.style.width = (to - from) + '%';
  done.style.left = from + '%'; done.style.width = Math.max(0, part(at) - from) + '%';
  $('seek').querySelector('.knob').style.left = part(at) + '%';
  $('seek').classList.toggle('preview', offset > 0);
}
$('toggle').onclick = () => { const audio = $('audio'); ampConnect(); audio.paused ? audio.play() : audio.pause(); };
['timeupdate', 'loadedmetadata', 'play', 'pause', 'seeked', 'emptied'].forEach(kind => $('audio').addEventListener(kind, showTime));
$('seek').addEventListener('pointerdown', e => {
  const seek = $('seek'), to = event => {
    const box = seek.getBoundingClientRect();
    seekTo(Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)) * length()); showTime();
  };
  seek.setPointerCapture(e.pointerId); to(e);
  seek.onpointermove = to; seek.onpointerup = seek.onpointercancel = () => seek.onpointermove = null;
});
$('audio').addEventListener('play', ampConnect);     // (its own controls' play)
$('audio').addEventListener('ended', () => {        // the next one of the same list
  const [kind, id] = playing.split(':');
  const list = ordered(kind === 'song' ? jobs : jobs.filter(job => job.state === 'done')), index = list.findIndex(job => job.id === id);
  if (index >= 0 && index + 1 < list.length) play(kind + ':' + list[index + 1].id, true);
});
document.addEventListener('contextmenu', e => { if (e.target.tagName === 'AUDIO') e.preventDefault(); });
$('view').onclick = async e => {
  if (e.target.dataset.cancel) {      // a song being sent: stopped (the computer drops what came of it)
    const upload = uploads[e.target.dataset.cancel];
    if (upload && upload.request) upload.request.abort();
    return;
  }
  const album = e.target.closest('tr.album');
  if (album) return fold(album.dataset.album);
  if (e.target.classList.contains('get') && amp.geometry) { e.target.href = ampDownload(e.target.href); return; }
  const song = e.target.closest('tr[data-play]');
  if (song && !e.target.closest('a, button')) return play(song.dataset.play);
  const id = e.target.dataset.remove;
  if (!id || !confirm('Remove this song and its negative?')) return;
  await fetch('/api/remove?' + auth() + '&id=' + id, {method: 'POST'});
  refresh();
};

// The lyrics being sung, over the player (the app's LyricsStrip): three lines, the middle one sung; synced ones by
// their time stamps, plain ones by where the song is (approximate). Hidden when there are none. A− A+: their size,
// kept in this browser
let lyrics = {id: '', timed: null, plain: null, current: -2};
async function showLyrics(id) {
  if (id === lyrics.id) { lyrics.current = -2; sing(); return; }
  lyrics = {id, timed: null, plain: null, current: -2};
  $('lyrics').classList.add('hidden');
  try {
    const data = await (await fetch(`/api/lyrics?${auth()}&id=${id}`, {cache: 'no-store'})).json();
    if (lyrics.id !== id) return;
    lyrics.timed = data.timed; lyrics.plain = data.plain;
  } catch (e) {}
  sing();
}
function sing() {
  const lines = lyrics.timed ? lyrics.timed.map(([start, words]) => words) : lyrics.plain || [];
  $('lyrics').classList.toggle('hidden', !lines.length || !playing);
  if (!lines.length || !playing) return;
  const audio = $('audio'), at = audio.currentTime + offset;
  let current = -1;
  if (lyrics.timed) {
    for (let index = 0; index < lyrics.timed.length && lyrics.timed[index][0] <= at + 0.15; index++) current = index;
  } else if (audio.duration) current = Math.min(lines.length - 1, Math.floor(at / audio.duration * lines.length));
  if (current === lyrics.current) return;
  lyrics.current = current;
  [...$('lyrics').children].slice(0, 3).forEach((row, offset) => {
    const index = current + offset - 1;
    row.textContent = index >= 0 && index < lines.length ? lines[index] || '♪' : '';
  });
}
function lyricsSize(factor) {
  let size = 1;
  try { size = +localStorage.getItem('lyrics/size') || 1; } catch (e) {}
  size = Math.max(0.6, Math.min(4, size * factor));
  try { localStorage.setItem('lyrics/size', String(size)); } catch (e) {}
  document.documentElement.style.setProperty('--lyrics', size);
}
lyricsSize(1);
$('smaller').onclick = () => lyricsSize(1 / 1.12);
$('bigger').onclick = () => lyricsSize(1.12);
setInterval(() => { if (playing && !$('audio').paused) sing(); }, 100);
['seeked', 'loadedmetadata'].forEach(kind => $('audio').addEventListener(kind, sing));

// The amplifier (amp.py's, the dark plate): its knobs, meter lever, output selector, needles and PEAK lamps drawn
// here over its panel (/amp-panel.png, /amp.json), turned as in the app (a click on a knob's edge or scale turns it
// there and it follows the mouse, dragged up or right from its middle, Shift: fine; the wheel in its column,
// elsewhere the volume; a double click back to the middle), acting on what plays here (Web Audio): bass and treble
// as equalizer-3bands' shelves (100 Hz, 11 kHz), balance as audiopanorama's simple (the other side turned down),
// the volume cubic and past 10 to 11 (+6 dB, clipping).
// The meter window as the app's lever sets it: ANALOG (the VU needles: the level after the tone and balance, moved
// by the volume, a mass on a spring, 0 VU trimmed to the music), LED BAR (two rows of LEDs, -60..0 dBFS, the peak
// held) or SPECTRUM (bars on a log frequency scale) (visuals.VUMeter's).
// The output selector: Streaming (the knobs on what plays here), or Download (also on the downloads: made with the
// bass, treble and balance on the computer running AG CuteMusic, ffmpeg). Kept in this browser (amp/...).
// Without /amp.json or Web Audio: the whole picture, as it was.
const amp = {geometry: null, knobs: [], selector: null, meter: 'analog', needles: [0, 0], speeds: [0, 0],
             rms: [-70, -70], target: [-70, -70], reference: -18, peakLit: [-9, -9], last: 0, drag: null, dirty: true,
             wheelRest: 0, wheelKnob: null, context: null, tipTimer: null,
             // visuals.VUMeter's: the LEDs' level (fast up, RELEASE down) and peaks held; the spectrum's bars
             leds: [-60, -60], peaks: [[-60, 0], [-60, 0]], levels: [-60, -60], top: [-60, -60], bars: [], barPeaks: [],
             spectrum: null};
const VU = {FLOOR: -60, HOLD: 1.2, RELEASE: 0.3, FREQUENCIES: [30, 16000], TILT: 3, DB_LOW: -72, DB_HIGH: -12,
            LABELS: [[50, '50'], [100, '100'], [200, '200'], [500, '500'], [1000, '1k'], [2000, '2k'], [5000, '5k'], [10000, '10k']]};

function ampLoad(key, value) { try { const v = localStorage.getItem('amp/' + key); return v == null ? value : typeof value === 'number' ? (isNaN(+v) ? value : +v) : v; } catch (e) { return value; } }
function ampKeep(key, value) { try { localStorage.setItem('amp/' + key, String(value)); } catch (e) {} }
function ampKnob(key) { return amp.knobs.find(knob => knob.key === key); }
function ampRatio(knob) { return (knob.value - knob.low) / (knob.high - knob.low); }
function ampAngle(knob, ratio) { return -knob.sweep + 2 * knob.sweep * (ratio == null ? ampRatio(knob) : ratio); }
function ampExtra(value) { return Math.max(0, value - 100) / 10 * amp.geometry.vu.EXTRA; }
function ampOutput() { return amp.selector ? amp.geometry.selector.names[amp.selector.value] : 'streaming'; }

async function ampStart() {
  if (!(window.AudioContext || window.webkitAudioContext) || !window.ResizeObserver) return;
  let geometry;
  try { const reply = await fetch('/amp.json', {cache: 'no-store'}); if (!reply.ok) return; geometry = await reply.json(); }
  catch (e) { return; }
  const image = $('ampImage');
  const loaded = await new Promise(done => { image.onload = () => done(true); image.onerror = () => done(false); image.src = '/amp-panel.png'; });
  if (!loaded) { image.onerror = null; image.src = '/amp.png'; return; }
  amp.geometry = geometry;
  amp.knobs = geometry.knobs.map(knob => Object.assign({}, knob, {value: Math.max(knob.low, Math.min(knob.high, ampLoad(knob.key, knob.value)))}));
  const selector = geometry.selector;
  amp.selector = {key: 'output', x: selector.x, y: selector.y, r: selector.r, low: 0, high: 1, limit: 1, step: 1, sweep: selector.sweep,
                  value: Math.max(0, selector.names.indexOf(ampLoad('output', 'streaming')))};
  amp.meter = geometry.lever.styles.includes(ampLoad('meter', 'analog')) ? ampLoad('meter', 'analog') : 'analog';
  const canvas = $('ampCanvas');
  canvas.classList.remove('hidden');
  new ResizeObserver(() => { const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(canvas.clientWidth * ratio); canvas.height = Math.round(canvas.clientHeight * ratio); amp.dirty = true; }).observe(canvas);
  canvas.addEventListener('pointerdown', ampPress);
  canvas.addEventListener('pointermove', ampMove);
  canvas.addEventListener('pointerup', () => amp.drag = null);
  canvas.addEventListener('pointercancel', () => amp.drag = null);
  canvas.addEventListener('dblclick', ampDoubleClick);
  canvas.addEventListener('wheel', ampWheel, {passive: false});
  canvas.addEventListener('contextmenu', e => e.preventDefault());
  requestAnimationFrame(ampTick);
}

// The sound: what the <audio> plays goes through here once played (a user's click: the browser allows sound then)
function ampConnect() {
  if (!amp.geometry) return;
  if (amp.context) { if (amp.context.state === 'suspended') amp.context.resume(); return; }
  const context = amp.context = new (window.AudioContext || window.webkitAudioContext)();
  const source = context.createMediaElementSource($('audio'));
  const stereo = context.createGain();            // a mono song as stereo: both sides, for the balance and the meters
  stereo.channelCount = 2; stereo.channelCountMode = 'explicit'; stereo.channelInterpretation = 'speakers';
  amp.bass = context.createBiquadFilter(); amp.bass.type = 'lowshelf'; amp.bass.frequency.value = 100;
  amp.treble = context.createBiquadFilter(); amp.treble.type = 'highshelf'; amp.treble.frequency.value = 11000;
  const split = context.createChannelSplitter(2), merge = context.createChannelMerger(2);
  amp.sides = [context.createGain(), context.createGain()];
  amp.meters = [context.createAnalyser(), context.createAnalyser()];
  amp.analyser = context.createAnalyser();        // the spectrum: both sides, 512 bands (the app's)
  amp.analyser.fftSize = 1024; amp.analyser.smoothingTimeConstant = 0;
  amp.volume = context.createGain();
  source.connect(stereo); stereo.connect(amp.bass); amp.bass.connect(amp.treble); amp.treble.connect(split);
  for (let side = 0; side < 2; side++) {
    split.connect(amp.sides[side], side);
    amp.sides[side].connect(merge, 0, side);
    amp.meters[side].fftSize = 2048;
    amp.sides[side].connect(amp.meters[side]);
  }
  merge.connect(amp.analyser);
  merge.connect(amp.volume); amp.volume.connect(context.destination);
  amp.samples = new Float32Array(2048);
  amp.magnitudes = new Float32Array(amp.analyser.frequencyBinCount);
  ampApply(true);
  if (context.state === 'suspended') context.resume();
}

function ampApply(now) {
  if (!amp.context) return;
  const time = amp.context.currentTime, set = (param, value) => now ? param.setValueAtTime(value, time) : param.setTargetAtTime(value, time, 0.015);
  set(amp.bass.gain, ampKnob('bass').value);
  set(amp.treble.gain, ampKnob('treble').value);
  const balance = ampKnob('balance').value;
  set(amp.sides[0].gain, balance > 0 ? 1 - balance : 1);
  set(amp.sides[1].gain, balance < 0 ? 1 + balance : 1);
  const volume = ampKnob('volume').value;
  set(amp.volume.gain, (Math.min(100, volume) / 100) ** 3 * 10 ** (ampExtra(volume) / 20));
}

// a download with the selector on Download: made with the bass, treble and balance (web.py's _amp_download)
function ampDownload(href) {
  const url = new URL(href, location.href);
  ['bass', 'treble', 'balance'].forEach(key => url.searchParams.delete(key));
  if (ampOutput() === 'download') ['bass', 'treble', 'balance'].forEach(key => {
    const value = ampKnob(key).value;
    if (Math.abs(value) > 1e-6) url.searchParams.set(key, String(value));
  });
  return url.href;
}

// The meters (amp.py's _tick, visuals.VUMeter's _tick)
function ampNeedleRatio(level) {
  const vu = amp.geometry.vu;
  level = Math.max(vu.VU_MIN - 1.5, Math.min(vu.VU_MAX + 1, level));
  const low = 10 ** (vu.VU_MIN / 20), high = 10 ** (vu.VU_MAX / 20);
  return (10 ** (level / 20) - low) / (high - low);
}

function ampTick(milliseconds) {
  requestAnimationFrame(ampTick);
  const now = milliseconds / 1000, vu = amp.geometry.vu;
  const elapsed = amp.last ? Math.min(now - amp.last, 0.5) : 0;
  amp.last = now;
  const live = amp.context && !$('audio').paused;
  for (let side = 0; side < 2; side++) {
    let rms = vu.FLOOR, peak = VU.FLOOR;
    if (live) {
      amp.meters[side].getFloatTimeDomainData(amp.samples);
      let sum = 0, top = 0;
      for (const sample of amp.samples) { sum += sample * sample; top = Math.max(top, Math.abs(sample)); }
      const level = 10 * Math.log10(sum / amp.samples.length);
      rms = isFinite(level) ? Math.max(vu.FLOOR, level) : vu.FLOOR;
      peak = top > 0 ? Math.max(VU.FLOOR, 20 * Math.log10(top)) : VU.FLOOR;
    }
    amp.target[side] = rms;
    amp.levels[side] = Math.max(VU.FLOOR, rms);
    amp.top[side] = peak;
  }
  if (live && amp.meter === 'spectrum') { amp.analyser.getFloatFrequencyData(amp.magnitudes); amp.spectrum = amp.magnitudes; }
  else amp.spectrum = null;
  let moved = false;
  if (amp.meter === 'analog') moved = ampTickNeedles(now, elapsed, vu);
  else if (amp.meter === 'bars') moved = ampTickLeds(now, elapsed);
  else moved = ampTickSpectrum(now, elapsed);
  if (amp.dirty || moved || (amp.meter === 'analog' && amp.peakLit.some(lit => now - lit < 0.6))) ampDraw(now);
}

function ampTickNeedles(now, elapsed, vu) {
  const follow = 1 - Math.exp(-elapsed / 0.3);
  for (let side = 0; side < 2; side++) amp.rms[side] += (amp.target[side] - amp.rms[side]) * follow;
  const loudness = Math.max(...amp.rms);
  if (loudness > -40) {       // not in the silences and fades: they would turn it all the way up
    const target = Math.min(vu.TRIM_RANGE[1], Math.max(vu.TRIM_RANGE[0], loudness + vu.HEADROOM));
    const speed = target > amp.reference ? vu.TRIM_UP : vu.TRIM_DOWN;
    amp.reference += (target - amp.reference) * Math.min(1, elapsed / speed);
  }
  const volume = ampKnob('volume').value, level = Math.min(100, volume);
  const gain = (level > 0 ? 20 * Math.log10(level / 100) : -60) + ampExtra(volume);
  const steps = Math.max(1, Math.ceil(Math.min(elapsed, 0.1) / 0.004)), dt = Math.min(elapsed, 0.1) / steps;
  let moved = false;
  for (let side = 0; side < 2; side++) {
    const input = amp.target[side];
    const goal = input > vu.FLOOR ? ampNeedleRatio(input + gain - amp.reference) : ampNeedleRatio(vu.VU_MIN - 2);
    let position = amp.needles[side], speed = amp.speeds[side];
    for (let step = 0; step < steps; step++) {
      speed += (vu.SPRING ** 2 * (goal - position) - 2 * vu.DAMPING * vu.SPRING * speed) * dt;
      position += speed * dt;
    }
    if (Math.abs(position - amp.needles[side]) > 0.0004 || Math.abs(speed) > 0.002) moved = true;
    amp.needles[side] = position; amp.speeds[side] = speed;
    if (position >= ampNeedleRatio(vu.PEAK_VU)) amp.peakLit[side] = now;
  }
  return moved;
}

function ampTickLeds(now, elapsed) {
  const fall = Math.exp(-elapsed / VU.RELEASE);
  let changed = false;
  for (let side = 0; side < 2; side++) {
    const target = amp.levels[side], value = amp.leds[side];
    const fresh = target > value ? target : target + (value - target) * fall;
    if (Math.abs(fresh - value) > 0.01) changed = true;
    amp.leds[side] = fresh;
    const [level, since] = amp.peaks[side], peak = amp.top[side];
    if (peak >= level || now - since > VU.HOLD) { if (peak !== level) changed = true; amp.peaks[side] = [peak, now]; }
    else if (now - since > VU.HOLD && level > VU.FLOOR) { amp.peaks[side] = [Math.max(VU.FLOOR, level - 30 * elapsed), since]; changed = true; }
  }
  return changed;
}

function ampSpectrumBars(count) {
  if (!amp.spectrum) return new Array(count).fill(VU.DB_LOW);
  const magnitudes = amp.spectrum, width = amp.context.sampleRate / 2 / magnitudes.length;
  const low = Math.log2(VU.FREQUENCIES[0]), high = Math.log2(VU.FREQUENCIES[1]), bars = [];
  const at = index => Math.max(-90, magnitudes[index]);       // the app's floor (SPECTRUM_THRESHOLD)
  for (let index = 0; index < count; index++) {
    const f0 = 2 ** (low + (high - low) * index / count), f1 = 2 ** (low + (high - low) * (index + 1) / count);
    const i0 = f0 / width, i1 = f1 / width;
    let db;
    if (i1 - i0 < 1) {
      const centre = Math.min(magnitudes.length - 1.001, Math.max(0, (i0 + i1) / 2 - 0.5)), below = Math.floor(centre);
      db = at(below) + (at(below + 1) - at(below)) * (centre - below);
    } else {
      db = -Infinity;
      for (let i = Math.floor(i0); i < Math.min(magnitudes.length, Math.ceil(i1)); i++) db = Math.max(db, at(i));
    }
    bars.push(db + VU.TILT * Math.log2(Math.sqrt(f0 * f1) / 1000));
  }
  return bars;
}

function ampBarCount() { return Math.floor(Math.max(16, Math.min(48, (amp.geometry.meter[2] - 24) / 14))); }

function ampTickSpectrum(now, elapsed) {
  const count = ampBarCount(), target = ampSpectrumBars(count);
  if (amp.bars.length !== count) { amp.bars = new Array(count).fill(VU.DB_LOW); amp.barPeaks = new Array(count).fill([VU.DB_LOW, now]); }
  const fall = Math.exp(-elapsed / VU.RELEASE);
  let changed = false;
  target.forEach((value, index) => {
    value = Math.max(VU.DB_LOW, value);
    const old = amp.bars[index], fresh = value > old ? value : value + (old - value) * fall;
    if (Math.abs(fresh - old) > 0.05) changed = true;
    amp.bars[index] = fresh;
    const [peak, since] = amp.barPeaks[index];
    if (fresh >= peak) amp.barPeaks[index] = [fresh, now];
    else if (now - since > VU.HOLD * 0.5 && peak > VU.DB_LOW) { amp.barPeaks[index] = [Math.max(VU.DB_LOW, peak - 24 * elapsed), since]; changed = true; }
  });
  return changed;
}

// Drawing (amp.py's, vufaces.py's and visuals.py's, in the panel's coordinates)
function ampCircle(context, x, y, radius, fill, stroke, width) {
  context.beginPath(); context.arc(x, y, radius, 0, 2 * Math.PI);
  if (fill) { context.fillStyle = fill; context.fill(); }
  if (stroke) { context.strokeStyle = stroke; context.lineWidth = width; context.stroke(); }
}
function ampRadial(context, x, y, radius, stops) {
  const gradient = context.createRadialGradient(x, y, 0, x, y, radius);
  stops.forEach(([stop, color]) => gradient.addColorStop(stop, color));
  return gradient;
}
function ampLine(context, x1, y1, x2, y2, color, width, cap) {
  context.beginPath(); context.moveTo(x1, y1); context.lineTo(x2, y2);
  context.strokeStyle = color; context.lineWidth = width; context.lineCap = cap || 'butt'; context.stroke();
}
function ampText(context, text, [x, y, width, height], color, font, align) {
  context.save();
  context.font = font; context.fillStyle = color; context.textBaseline = 'middle';
  context.textAlign = align || 'center';
  context.fillText(text, align === 'left' ? x : align === 'right' ? x + width : x + width / 2, y + height / 2, width);
  context.restore();
}

function ampDraw(now) {
  const canvas = $('ampCanvas'), context = canvas.getContext('2d'), geometry = amp.geometry;
  if (!canvas.width) return;
  const scale = canvas.width / geometry.width;
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.setTransform(scale, 0, 0, scale, 0, 0);
  if (amp.meter === 'analog') geometry.faces.forEach((face, side) => ampDrawNeedle(context, face, amp.needles[side], now - amp.peakLit[side] < 0.35));
  else {
    const [x, y, width, height] = geometry.meter;
    context.save();
    context.translate(x, y);
    context.beginPath(); context.rect(0, 0, width, height); context.clip();
    context.fillStyle = '#101318'; context.fillRect(0, 0, width, height);
    (amp.meter === 'bars' ? ampDrawLeds : ampDrawSpectrum)(context, width, height);
    context.restore();
  }
  ampDrawLever(context);
  ampDrawSelector(context);
  amp.knobs.forEach(knob => ampDrawKnob(context, knob));
  amp.dirty = false;
}

function ampDrawNeedle(context, [x, y, width, height], ratio, peak) {
  const sweep = amp.geometry.vu.SWEEP, small = Math.max(6, height * 0.085), radius = Math.min(width * 0.6, height * 1.5);
  const px = x + width / 2, py = y + small * 1.7 + radius * 1.02;
  const angle = (-90 - sweep + 2 * sweep * Math.max(-0.02, ratio)) * Math.PI / 180;
  context.save();
  context.beginPath(); context.rect(x, y, width, height); context.clip();
  ampLine(context, px + radius * 0.36 * Math.cos(angle), py + radius * 0.36 * Math.sin(angle),
          px + radius * 0.97 * Math.cos(angle), py + radius * 0.97 * Math.sin(angle), '#16110b', Math.max(1.4, height * 0.018), 'square');
  context.restore();
  ampDrawLamp(context, x + width - small * 1.6, y + height - small * 1.1, small * 0.42, peak);
}

function ampDrawLamp(context, x, y, size, lit) {
  const lamp = amp.geometry.lamp, [r, g, b] = lamp.glow;
  if (lit) ampCircle(context, x, y, size * 2.6, ampRadial(context, x, y, size * 2.6, [[0, `rgba(${r},${g},${b},0.59)`], [1, `rgba(${r},${g},${b},0)`]]));
  const [inner, outer] = lit ? lamp.lit : lamp.off;
  ampCircle(context, x, y, size, ampRadial(context, x - size * 0.3, y - size * 0.3, size * 1.2, [[0, inner], [1, outer]]), '#3a3d42', 0.8);
}

function ampMeterFont() { return `${amp.geometry.meterFont}px system-ui, -apple-system, "Segoe UI", Ubuntu, sans-serif`; }
function ampDbRatio(db) { return (Math.min(0, Math.max(VU.FLOOR, db)) - VU.FLOOR) / -VU.FLOOR; }

function ampDrawLeds(context, width, height) {       // the amplifier's LED BAR (VUMeter._paint_bars, compact)
  const margin = 36, bar = Math.min(46, (height - 56) / 2.8), total = bar * 2.8, top = (height - total) / 2, wide = width - 2 * margin;
  const font = ampMeterFont(), dim = '#8a90a0';
  ['L', 'R'].forEach((label, side) => {
    const y = top + side * bar * 1.8;
    context.fillStyle = '#1c2029';
    context.beginPath(); context.roundRect ? context.roundRect(margin, y, wide, bar, 4) : context.rect(margin, y, wide, bar); context.fill();
    const segments = 40, step = wide / segments, lit = ampDbRatio(amp.leds[side]) * segments;
    const peak = amp.peaks[side][0] > VU.FLOOR ? Math.floor(ampDbRatio(amp.peaks[side][0]) * segments - 0.001) : -1;
    for (let index = 0; index < segments; index++) {
      const db = VU.FLOOR + (index + 1) * -VU.FLOOR / segments;
      let color = db <= -18 ? [0x5c, 0xdc, 0x9e] : db <= -6 ? [0xff, 0xcc, 0x55] : [0xf4, 0x57, 0x4a];
      if (!(index < lit || index === peak)) color = color.map(c => Math.floor(c * 22 / 100));
      context.fillStyle = `rgb(${color})`;
      context.fillRect(margin + index * step + 1, y + 1, step - 2, bar - 2);
    }
    ampText(context, label, [margin - 28, y, 22, bar], dim, font);
  });
  for (const db of [-60, -50, -40, -30, -20, -12, -6, -3, 0]) {
    const x = margin + wide * ampDbRatio(db);
    ampLine(context, x, top - 6, x, top - 2, dim, 1);
    ampText(context, String(db), [x - 20, top + total + 6, 40, 20], dim, font);
  }
}

function ampDrawSpectrum(context, width, height) {     // VUMeter._paint_spectrum
  const count = amp.bars.length || ampBarCount(), bars = amp.bars.length ? amp.bars : new Array(count).fill(VU.DB_LOW);
  const peaks = amp.barPeaks.length ? amp.barPeaks : new Array(count).fill([VU.DB_LOW, 0]);
  const side = 12, label = Math.max(6, Math.min(11, height * 0.05)), labels = height >= 120;
  const top = 10, bottom = height - (labels ? label * 2.2 : 8), tall = bottom - top;
  const step = (width - 2 * side) / count, gap = Math.max(1, step * 0.18), span = VU.DB_HIGH - VU.DB_LOW;
  const gradient = context.createLinearGradient(0, bottom, 0, top);
  [[0, '#2e8f6c'], [0.55, '#5cdc9e'], [0.8, '#ffcc55'], [1, '#f4574a']].forEach(([stop, color]) => gradient.addColorStop(stop, color));
  const y = db => bottom - Math.max(0, Math.min(1, (db - VU.DB_LOW) / span)) * tall;
  for (let index = 0; index < count; index++) {
    const x = side + index * step + gap / 2;
    context.fillStyle = '#1a1e26'; context.fillRect(x, top, step - gap, tall);
    const level = y(bars[index]);
    if (level < bottom) { context.fillStyle = gradient; context.fillRect(x, level, step - gap, bottom - level); }
    const peak = y(peaks[index][0]);
    if (peak < bottom - 1) { context.fillStyle = '#e8eaf0'; context.fillRect(x, peak - 1, step - gap, 2); }
  }
  if (!labels) return;
  const low = Math.log2(VU.FREQUENCIES[0]), high = Math.log2(VU.FREQUENCIES[1]), wide = width - 2 * side;
  for (const [frequency, text] of VU.LABELS) {
    const x = side + (Math.log2(frequency) - low) / (high - low) * wide;
    ampText(context, text, [x - label * 2, bottom + label * 0.4, label * 4, label * 1.6], '#8a90a0', `${Math.round(label)}px system-ui, sans-serif`);
  }
}

function ampDrawLever(context) {         // amp.py's _draw_lever: a metal rod across the slot, at the style chosen
  const lever = amp.geometry.lever, span = lever.points[1] - lever.points[0];
  const y = lever.points[Math.max(0, lever.styles.indexOf(amp.meter))];
  const length = span * 0.7, thickness = Math.max(4, span * 0.2), left = lever.x - length / 2, top = y - thickness / 2;
  context.fillStyle = 'rgba(0,0,0,0.35)';
  context.fillRect(left + span * 0.04, top + span * 0.08, length, thickness);
  const metal = context.createLinearGradient(0, top, 0, top + thickness);
  [[0, '#7d8188'], [0.3, '#ffffff'], [0.65, '#b4b8be'], [1, '#3f4349']].forEach(([stop, color]) => metal.addColorStop(stop, color));
  context.fillStyle = metal;
  context.fillRect(left, top, length, thickness);
}

function ampDrawSelector(context) {      // amp.py's _draw_selector_scale's marks and name, and the knob
  const geometry = amp.geometry.selector, selector = amp.selector, {x, y, r} = selector;
  geometry.names.forEach((name, index) => {
    const angle = (ampAngle(selector, index) - 90) * Math.PI / 180, current = index === selector.value;
    const dx = Math.cos(angle), dy = Math.sin(angle);
    ampLine(context, x + dx * r * 1.16, y + dy * r * 1.16, x + dx * r * (current ? 1.38 : 1.28), y + dy * r * (current ? 1.38 : 1.28),
            amp.geometry.ink, current ? 1.6 : 1.2);
  });
  ampText(context, geometry.titles[selector.value], geometry.nameBox, amp.geometry.print,
          `500 ${geometry.nameFont}px system-ui, -apple-system, "Segoe UI", Ubuntu, sans-serif`);
  ampDrawKnob(context, selector);
}

const AMP_SPUN = [[0, '#f4f5f7'], [0.12, '#a9adb3'], [0.25, '#e9ebee'], [0.38, '#8f939a'], [0.5, '#f4f5f7'],
                  [0.62, '#a9adb3'], [0.75, '#e9ebee'], [0.88, '#8f939a'], [1, '#f4f5f7']];
function ampDrawKnob(context, knob) {
  const {x, y, r} = knob, rotation = ampAngle(knob);
  // its shadow, then the knurled black skirt
  ampCircle(context, x, y + r * 0.18, r * 1.25, ampRadial(context, x, y + r * 0.18, r * 1.25, [[0.75, 'rgba(0,0,0,0.43)'], [1, 'rgba(0,0,0,0)']]));
  const skirt = context.createLinearGradient(0, y - r, 0, y + r);
  skirt.addColorStop(0, '#3c3f45'); skirt.addColorStop(1, '#0d0e10');
  ampCircle(context, x, y, r, skirt);
  for (let index = 0; index < 36; index++) {
    const angle = (rotation + index * 10) * Math.PI / 180, cos = Math.cos(angle), sin = Math.sin(angle);
    ampLine(context, x + r * 0.82 * cos, y + r * 0.82 * sin, x + r * 0.98 * cos, y + r * 0.98 * sin, 'rgba(255,255,255,0.15)', 1);
  }
  // the spun aluminium cap: a conical gradient (light from above-left), fixed while the knob turns (Qt's turns the
  // other way round: its stops mirrored)
  const cap = r * 0.72;
  let spun;
  if (context.createConicGradient) {
    spun = context.createConicGradient(-135 * Math.PI / 180, x, y);
    AMP_SPUN.forEach(([stop, color]) => spun.addColorStop(1 - stop, color));
  } else spun = ampRadial(context, x - cap * 0.3, y - cap * 0.3, cap * 1.4, [[0, '#f4f5f7'], [1, '#8f939a']]);
  ampCircle(context, x, y, cap, spun, '#5e6268', 0.8);
  ampCircle(context, x, y, cap, ampRadial(context, x, y, cap, [[0.85, 'rgba(0,0,0,0)'], [1, 'rgba(0,0,0,0.24)']]));
  // the pointer: a line on the cap, a white mark on the skirt
  const angle = (rotation - 90) * Math.PI / 180, dx = Math.cos(angle), dy = Math.sin(angle);
  ampLine(context, x + dx * cap * 0.3, y + dy * cap * 0.3, x + dx * cap * 0.88, y + dy * cap * 0.88, '#2b2e33', Math.max(1.5, r * 0.07), 'round');
  ampLine(context, x + dx * r * 0.84, y + dy * r * 0.84, x + dx * r * 0.97, y + dy * r * 0.97, '#f2f2f2', Math.max(1.5, r * 0.08), 'round');
}

// Turning (amp.py's mouse and wheel)
function ampPoint(event) {
  const box = $('ampCanvas').getBoundingClientRect(), scale = amp.geometry.width / box.width;
  return [(event.clientX - box.left) * scale, (event.clientY - box.top) * scale];
}
function ampDistance(knob, [x, y]) { return Math.hypot(x - knob.x, y - knob.y); }
function ampKnobAt(point) { return amp.knobs.find(knob => ampDistance(knob, point) <= knob.r * 1.6); }
function ampIn(box, [x, y], pad) { pad = pad || 0; return !!box && x >= box[0] - pad && x <= box[0] + box[2] + pad && y >= box[1] - pad && y <= box[1] + box[3] + pad; }
function ampOnSelector(point) { return ampDistance(amp.selector, point) <= amp.selector.r * 1.5; }
function ampRatioAt(knob, [x, y], last) {
  const angle = Math.atan2(x - knob.x, knob.y - y) * 180 / Math.PI;     // clockwise from the top
  return Math.abs(angle) <= knob.sweep ? (angle + knob.sweep) / (2 * knob.sweep) : last >= 0.5 ? 1 : 0;
}
function ampTurn(knob, value) {
  value = Math.round(Math.max(knob.low, Math.min(knob.limit, value)) / knob.step) * knob.step;
  if (Math.abs(value - knob.value) < 1e-9) return;
  knob.value = value; ampKeep(knob.key, value); ampApply(); amp.dirty = true;
}
function ampSay(knob) {
  const value = knob.value;
  if (knob.key === 'volume') return `Volume ${(value / 10).toFixed(1)}` + (value === 100 ? ' (max)' : '');
  if (knob.key === 'balance') return Math.abs(value) < 0.025 ? 'Balance: centre' : `Balance: ${Math.round(Math.abs(value) * 100)}% ${value > 0 ? 'right' : 'left'}`;
  return `${knob.label[0]}${knob.label.slice(1).toLowerCase()} ${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(1)} dB`;
}
function ampTip(text, event) {
  const tip = $('ampTip');
  tip.textContent = text; tip.style.left = (event.clientX + 14) + 'px'; tip.style.top = (event.clientY + 18) + 'px';
  tip.classList.remove('hidden');
  clearTimeout(amp.tipTimer); amp.tipTimer = setTimeout(() => tip.classList.add('hidden'), 1500);
}
const AMP_STYLES = {analog: 'Analog VU meters', bars: 'LED bars', spectrum: 'Spectrum'};
const AMP_OUTPUTS = {streaming: 'Streaming: downloads as they are', download: 'Download: downloads with the bass, treble and balance'};
function ampSetMeter(style, event) {
  if (style !== amp.meter) { amp.meter = style; ampKeep('meter', style); amp.dirty = true; }
  if (event) ampTip(AMP_STYLES[style], event);
}
function ampSetOutput(index, event) {
  const names = amp.geometry.selector.names;
  index = Math.max(0, Math.min(names.length - 1, index));
  if (index !== amp.selector.value) { amp.selector.value = index; ampKeep('output', names[index]); amp.dirty = true; }
  if (event) ampTip(AMP_OUTPUTS[names[index]], event);
}

function ampPress(event) {
  if (event.button !== 0) return;
  const point = ampPoint(event), geometry = amp.geometry;
  if (ampIn(geometry.why, point, 4)) { window.open(geometry.whyUrl, '_blank', 'noopener'); return; }
  if (ampIn(geometry.lever.hit, point)) {
    const points = geometry.lever.points;
    const nearest = points.reduce((best, y, index) => Math.abs(y - point[1]) < Math.abs(points[best] - point[1]) ? index : best, 0);
    ampSetMeter(geometry.lever.styles[nearest], event);
    return;
  }
  if (ampOnSelector(point)) {         // a position chosen; on its cap: the next one, round to the first
    const selector = amp.selector, count = geometry.selector.names.length;
    if (ampDistance(selector, point) > selector.r * geometry.vu.CLICK_RING) {
      const angle = Math.atan2(point[0] - selector.x, selector.y - point[1]) * 180 / Math.PI;
      ampSetOutput([...Array(count).keys()].reduce((best, index) =>
        Math.abs(ampAngle(selector, index) - angle) < Math.abs(ampAngle(selector, best) - angle) ? index : best, 0), event);
    } else ampSetOutput((selector.value + 1) % count, event);
    return;
  }
  const knob = ampKnobAt(point);
  if (!knob) return;
  event.preventDefault();
  $('ampCanvas').setPointerCapture(event.pointerId);
  const around = ampDistance(knob, point) > knob.r * geometry.vu.CLICK_RING;
  if (around) {
    let value = knob.low + ampRatioAt(knob, point, ampRatio(knob)) * (knob.high - knob.low);
    if (knob.key === 'volume' && knob.value <= 100) value = Math.min(100, value);     // 11 is turned to, not jumped to
    ampTurn(knob, value);
  }
  amp.drag = {knob, start: point, value: knob.value, around};
  ampTip(ampSay(knob), event);
}

function ampMove(event) {
  const point = ampPoint(event), canvas = $('ampCanvas'), geometry = amp.geometry;
  if (!amp.drag) {
    const why = ampIn(geometry.why, point, 4), lever = ampIn(geometry.lever.hit, point), selector = ampOnSelector(point);
    canvas.style.cursor = why || lever || selector || ampKnobAt(point) ? 'pointer' : 'default';
    canvas.title = why ? geometry.whyUrl : lever ? 'Meters: ' + AMP_STYLES[amp.meter]
                 : selector ? 'Output: ' + AMP_OUTPUTS[ampOutput()] + ' (click: the other one)' : '';
    return;
  }
  const drag = amp.drag, knob = drag.knob;
  if (drag.around && !event.shiftKey && ampDistance(knob, point) > knob.r * geometry.vu.CLICK_RING) {
    ampTurn(knob, knob.low + ampRatioAt(knob, point, ampRatio(knob)) * (knob.high - knob.low));
  } else {        // up and down (Shift: fine), from where it is now
    if (drag.around) Object.assign(drag, {start: point, value: knob.value, around: false});
    const moved = (drag.start[1] - point[1]) + (point[0] - drag.start[0]);
    ampTurn(knob, drag.value + moved / (220 * (event.shiftKey ? 5 : 1)) * (knob.high - knob.low));
  }
  ampTip(ampSay(knob), event);
}

function ampDoubleClick(event) {
  const knob = ampKnobAt(ampPoint(event));
  if (knob && knob.default != null) { ampTurn(knob, knob.default); ampTip(ampSay(knob), event); }
}

function ampWheel(event) {
  if (event.ctrlKey) return;      // the browser's zoom
  event.preventDefault();
  const point = ampPoint(event), geometry = amp.geometry, inner = geometry.inner, window_ = geometry.window;
  const notches = -(event.deltaY || event.deltaX) / (event.deltaMode === 1 ? 3 : event.deltaMode === 2 ? 0.1 : 100);
  const lever = ampIn(geometry.lever.hit, point), selector = ampOnSelector(point);
  // in the knobs' row (under the meters): the knob whose column it is in; the lever, the selector; elsewhere, the volume
  const row = point[0] >= inner[0] && point[0] <= inner[0] + inner[2] && point[1] > window_[1] + window_[3] && point[1] <= inner[1] + inner[3];
  const knob = lever ? 'lever' : selector ? 'selector' : row ? amp.knobs.reduce((best, knob) => Math.abs(knob.x - point[0]) < Math.abs(best.x - point[0]) ? knob : best)
             : ampKnob('volume');
  if (knob !== amp.wheelKnob) { amp.wheelRest = 0; amp.wheelKnob = knob; }
  amp.wheelRest += notches * (knob.key === 'volume' ? 5 : 1);      // a notch a step (5 for the volume)
  const steps = Math.trunc(amp.wheelRest);
  if (!steps) return;
  amp.wheelRest -= steps;
  if (knob === 'lever') {             // up: the one above
    const styles = geometry.lever.styles;
    ampSetMeter(styles[Math.max(0, Math.min(styles.length - 1, styles.indexOf(amp.meter) - steps))], event);
  } else if (knob === 'selector') ampSetOutput(amp.selector.value + steps, event);
  else { ampTurn(knob, knob.value + steps * knob.step); ampTip(ampSay(knob), event); }
}

// its size: a share of the page's width (the app's sizes), one smaller than the whole at first; kept in this browser
const AMP_SIZES = [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0];
function ampSize(step) {
  let size = 0.9;
  try { size = +localStorage.getItem('amp/size') || 0.9; } catch (e) {}
  let index = AMP_SIZES.reduce((best, value, i) => Math.abs(value - size) < Math.abs(AMP_SIZES[best] - size) ? i : best, 0);
  index = Math.max(0, Math.min(AMP_SIZES.length - 1, index + step));
  if (step) try { localStorage.setItem('amp/size', String(AMP_SIZES[index])); } catch (e) {}
  document.documentElement.style.setProperty('--amp-size', AMP_SIZES[index]);
  $('ampSmaller').disabled = index === 0; $('ampBigger').disabled = index === AMP_SIZES.length - 1;
}
ampSize(0);
$('ampSmaller').onclick = () => ampSize(-1);
$('ampBigger').onclick = () => ampSize(1);
ampStart();

// logged in before in this browser: as it was; else the two ways in, whatever the address says (a token's link,
// #12345: its token ready in Retrieve, once chosen)
const first = location.hash.slice(1) || stored();
if (/^\d{5}$/.test(first) && first === stored() && storedKey()) {
  let name = ''; try { name = localStorage.getItem('ownerName') || ''; } catch (e) {}
  enter(first, name, storedKey());
} else {
  if (/^\d{5}$/.test(first)) $('token').value = first;
  landing();
}
</script>
</body>
</html>
'''
