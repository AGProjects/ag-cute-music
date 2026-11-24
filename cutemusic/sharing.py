"""Libraries on the LAN: one AG CuteMusic shares its music folder (read only, over HTTP), the others find it
(Bonjour/mDNS, _agcutemusic._tcp) and browse it, play its songs here (each fetched first into a cache) and copy
artists, albums or songs into their own music folder.

- LibraryServer: the music folder shared (File -> Share my library on the LAN; always with --headless):
    GET /info                   {name, id, app, version, tracks}
    GET /library                {name, id, tracks: [{tags..., path: relative to the music folder}]}
    GET /file?path=<relative>   a music file (or its .lrc/.txt, or an image of its folder); ranges served
    GET /extras?path=<relative> the .lrc/.txt of the same name and the images in its folder: [relative paths]
  Only files under the music folder, of those kinds. No password: for a trusted LAN.
- Discovery: the shared libraries found on the LAN (python3-zeroconf; without it, File -> Connect to a library...
  by address only).
- fetch_library(): a library's tracks, as Tracks whose path is where they go in the cache
  (~/.ag-cute-music/remote/<id>/<relative path>), with 'remote' (the library's address) and 'rel'.
- Downloader: files fetched one after the other in a thread (to the cache to play, or into the music folder).
"""

import http.server
import json
import os
import queue
import socket
import socketserver
import threading
import urllib.parse
import urllib.request
import uuid

from PyQt6.QtCore import QObject, pyqtSignal

from cutemusic import APP_NAME, __version__, util
from cutemusic.library import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, Track
from cutemusic.util import log

try:
    import zeroconf
except ImportError:
    zeroconf = None


__all__ = ['LibraryServer', 'Discovery', 'Downloader', 'fetch_library', 'library_id', 'library_name', 'PORT']


SERVICE = '_agcutemusic._tcp.local.'
PORT = 47100
SIDECARS = ('.lrc', '.LRC', '.txt')
SERVED = AUDIO_EXTENSIONS | set(IMAGE_EXTENSIONS) | {'.lrc', '.txt'}
TIMEOUT = 20
CHUNK = 256 * 1024


def library_id():
    """This instance's id (kept in the settings): its own library is not listed among the remote ones."""
    value = util.settings().value('sharing/id', '', type=str)
    if not value:
        value = uuid.uuid4().hex[:12]
        util.settings().setValue('sharing/id', value)
    return value


def library_name():
    return util.settings().value('sharing/name', '', type=str) or socket.gethostname().split('.')[0]


def cache_root(identifier):
    return os.path.join(util.CONFIG_DIR, 'remote', identifier)


# Sharing

class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = f'AGCuteMusic/{__version__}'
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        pass        # no line a request

    def _json(self, data):
        body = json.dumps(data).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _local(self, relative):
        """The file a relative path names, when under the music folder and of a kind served; else None."""
        root = self.server.library.root
        if not root or not relative:
            return None
        path = os.path.realpath(os.path.join(root, relative))
        if not path.startswith(os.path.realpath(root) + os.sep) or os.path.splitext(path)[1].lower() not in SERVED:
            return None
        return path if os.path.isfile(path) else None

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        relative = (query.get('path') or [''])[0]
        library = self.server.library
        try:
            if url.path == '/info':
                self._json({'name': library.name, 'id': library.id, 'app': APP_NAME, 'version': __version__,
                            'tracks': len(library.tracks)})
            elif url.path == '/library':
                self._json({'name': library.name, 'id': library.id, 'tracks': library.shared_tracks()})
            elif url.path == '/extras':
                path = self._local(relative)
                if path is None:
                    self.send_error(404)
                    return
                self._json(library.extras(path))
            elif url.path == '/file':
                path = self._local(relative)
                if path is None:
                    self.send_error(404)
                    return
                self._file(path)
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _file(self, path):
        size = os.path.getsize(path)
        start, end = 0, size - 1
        ranged = self.headers.get('Range', '')
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
                self.send_error(416)
                return
            self.send_response(206)
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        else:
            self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(end - start + 1))
        self.end_headers()
        with open(path, 'rb') as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                data = f.read(min(CHUNK, left))
                if not data:
                    break
                self.wfile.write(data)
                left -= len(data)


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        """A client that went away (the connection reset or broken): no traceback on the console; anything else logged."""
        import sys
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionError, TimeoutError)):
            return
        log.warning(f'[sharing] request from {client_address[0]}: {error!r}')


class LibraryServer(object):
    """The music folder shared on the LAN, and announced (Bonjour) when python3-zeroconf is there."""

    def __init__(self):
        self.root = ''
        self.tracks = []
        self.name = library_name()
        self.id = library_id()
        self.port = None
        self._server = None
        self._zeroconf = None
        self._info = None
        self._lock = threading.Lock()
        self._shared = None

    def set_library(self, tracks, root):
        with self._lock:
            self.tracks, self.root, self._shared = list(tracks), root, None

    def shared_tracks(self):
        with self._lock:
            if self._shared is None:
                prefix = self.root + os.sep
                self._shared = [dict(track, path=track.path[len(prefix):]) for track in self.tracks
                                if track.path.startswith(prefix)]
            return self._shared

    def extras(self, path):
        folder, base = os.path.dirname(path), os.path.splitext(path)[0]
        found = [base + extension for extension in SIDECARS if os.path.isfile(base + extension)]
        try:
            found += [os.path.join(folder, name) for name in sorted(os.listdir(folder))
                      if name.lower().endswith(IMAGE_EXTENSIONS) and not name.startswith('.')]
        except OSError:
            pass
        return [os.path.relpath(item, self.root) for item in found]

    @property
    def running(self):
        return self._server is not None

    def start(self, port=PORT):
        if self._server is not None:
            return True
        for candidate in (port, 0):         # taken: any free one (it is announced)
            try:
                self._server = _Server(('', candidate), _Handler)
                break
            except OSError as e:
                log.warning(f'[sharing] port {candidate}: {e}')
        if self._server is None:
            return False
        self._server.library = self
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, name='sharing', daemon=True).start()
        log.info(f'[sharing] {self.root} shared as {self.name!r} on port {self.port}')
        self._announce()
        return True

    def _announce(self):
        if zeroconf is None:
            log.info('[sharing] no python3-zeroconf: not announced (others connect by address)')
            return
        try:
            addresses = [socket.inet_aton(address) for address in _addresses()]
            self._info = zeroconf.ServiceInfo(SERVICE, f'{self.name} ({self.id}).{SERVICE}', port=self.port,
                                              addresses=addresses, server=f'{socket.gethostname().split(".")[0]}.local.',
                                              properties={'id': self.id, 'name': self.name, 'version': __version__})
            self._zeroconf = zeroconf.Zeroconf()
            self._zeroconf.register_service(self._info, allow_name_change=True)
            log.info(f'[sharing] announced on {", ".join(_addresses())}')
        except Exception as e:
            log.warning(f'[sharing] cannot announce: {e}')
            self._zeroconf = None

    def stop(self):
        if self._zeroconf is not None:
            try:
                self._zeroconf.unregister_service(self._info)
                self._zeroconf.close()
            except Exception:
                pass
            self._zeroconf = None
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
            log.info('[sharing] stopped')


def _addresses():
    """This computer's IPv4 addresses on the LAN (not loopback)."""
    found = set()
    try:
        import ifaddr
        for adapter in ifaddr.get_adapters():
            for ip in adapter.ips:
                if isinstance(ip.ip, str) and not ip.ip.startswith('127.') and not ip.ip.startswith('169.254.'):
                    found.add(ip.ip)
    except ImportError:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(('10.255.255.255', 1))
            found.add(probe.getsockname()[0])
            probe.close()
        except OSError:
            pass
    return sorted(found)


# Finding

class Discovery(QObject):
    """The libraries shared on the LAN: changed([{name, id, address}]) as they come and go (not this one's)."""

    changed = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.libraries = {}         # service name: {name, id, address}
        self._zeroconf = None
        self._browser = None

    @property
    def available(self):
        return zeroconf is not None

    def start(self):
        if zeroconf is None or self._zeroconf is not None:
            return
        own = library_id()
        discovery = self

        class Listener(object):
            def add_service(self, zc, kind, name):
                info = zc.get_service_info(kind, name, timeout=2000)
                if info is None:
                    return
                properties = {key.decode(): (value or b'').decode() for key, value in (info.properties or {}).items()}
                if properties.get('id') == own:
                    return
                addresses = info.parsed_addresses()
                if not addresses:
                    return
                entry = {'name': properties.get('name') or name.split('.')[0], 'id': properties.get('id', name),
                         'address': f'http://{addresses[0]}:{info.port}'}
                util.call_in_gui_thread(discovery._add, name, entry)

            def update_service(self, zc, kind, name):
                self.add_service(zc, kind, name)

            def remove_service(self, zc, kind, name):
                util.call_in_gui_thread(discovery._remove, name)
        try:
            self._zeroconf = zeroconf.Zeroconf()
            self._browser = zeroconf.ServiceBrowser(self._zeroconf, SERVICE, Listener())
        except Exception as e:
            log.warning(f'[sharing] cannot look for libraries: {e}')
            self._zeroconf = None

    def _add(self, name, entry):
        if self.libraries.get(name) != entry:
            self.libraries[name] = entry
            log.info(f'[sharing] found {entry["name"]} at {entry["address"]}')
            self.changed.emit(list(self.libraries.values()))

    def _remove(self, name):
        if self.libraries.pop(name, None) is not None:
            self.changed.emit(list(self.libraries.values()))

    def stop(self):
        if self._zeroconf is not None:
            try:
                self._zeroconf.close()
            except Exception:
                pass
            self._zeroconf = None


# Using a remote library

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # the LAN directly: no proxy


def _get(address, endpoint, **params):
    url = address.rstrip('/') + endpoint + ('?' + urllib.parse.urlencode(params) if params else '')
    return _opener.open(urllib.request.Request(url, headers={'User-Agent': f'AGCuteMusic/{__version__}'}), timeout=TIMEOUT)


def fetch_library(address):
    """(name, id, [Track]) of the library shared at address (http://host:port)."""
    with _get(address, '/library') as response:
        data = json.loads(response.read())
    identifier = data.get('id') or address
    cache = cache_root(identifier)
    tracks = []
    for entry in data.get('tracks') or []:
        relative = entry.get('path') or ''
        if not relative or relative.startswith('/') or '..' in relative.split('/'):
            continue
        track = Track(entry)
        track['rel'] = relative
        track['remote'] = address
        track['path'] = os.path.join(cache, *relative.split('/'))
        tracks.append(track)
    return data.get('name') or address, identifier, tracks


def download(address, relative, destination):
    """The file relative of the library at address into destination (through a .part file): True when done."""
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    partial = destination + '.part'
    with _get(address, '/file', path=relative) as response, open(partial, 'wb') as f:
        while True:
            data = response.read(CHUNK)
            if not data:
                break
            f.write(data)
    os.replace(partial, destination)
    return True


def extras(address, relative):
    with _get(address, '/extras', path=relative) as response:
        return json.loads(response.read())


class Downloader(QObject):
    """Files fetched one after the other in a thread: fetch(track, destination, tag), with the track's .lrc/.txt and
    its folder's images next to it (when not there yet). done(tag, track, ok) after each; idle() when none is left."""

    done = pyqtSignal(object, object, bool)
    idle = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs = queue.Queue()
        self._thread = None
        self.pending = 0

    def fetch(self, track, destination, tag=None):
        self.pending += 1
        self._jobs.put((track, destination, tag))
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='downloads', daemon=True)
            self._thread.start()

    def _run(self):
        while True:
            track, destination, tag = self._jobs.get()
            ok = True
            try:
                if not os.path.exists(destination):
                    download(track['remote'], track['rel'], destination)
                folder = os.path.dirname(destination)
                remote_folder = os.path.dirname(track['rel'])
                for relative in extras(track['remote'], track['rel']):
                    if os.path.dirname(relative) != remote_folder:
                        continue
                    name = os.path.basename(relative)
                    if os.path.splitext(name)[1] in SIDECARS:       # its lyrics: named after where it went
                        name = os.path.splitext(os.path.basename(destination))[0] + os.path.splitext(name)[1]
                    if not os.path.exists(os.path.join(folder, name)):
                        download(track['remote'], relative, os.path.join(folder, name))
            except Exception as e:          # whatever it is, the thread goes on with the next one
                log.warning(f'[sharing] {track.get("rel")}: {e}')
                ok = False
            util.call_in_gui_thread(self._finished, tag, track, ok)

    def _finished(self, tag, track, ok):
        self.pending -= 1
        self.done.emit(tag, track, ok)
        if self.pending == 0:
            self.idle.emit()
