"""Negatives: karaoke backing tracks, the songs without their singer, made offline by a neural source separation
model (BS-RoFormer, audio-separator's default: model_bs_roformer_ep_317_sdr_12.9755), for singing them oneself.

Right-click -> Make negative on songs, albums, artists (the library) or a playlist: they are queued and made one at a
time in the background (minutes a song on a CPU; the GPU when there is an NVIDIA one), the queue kept across restarts
(~/.ag-cute-music/negatives-queue.json); the Negatives tab shows them, made or not (window.py). A negative is a FLAC in the
negatives' folder, next to the music folder with "-Negatives" (~/Music/Player: ~/Music/Player-Negatives; settings:
negatives/folder), at the song's own path in the music folder, its extension .flac: the same tags (the lyrics found
online are then the song's), cover, lyrics files and folder images.

The separator is not part of the application: audio-separator, PyTorch and the model (about 3 GB with the CPU
PyTorch) are installed on first use into a venv of their own (~/.ag-cute-music/separator, the model in
~/.ag-cute-music/separator-models), with the system's python3 (python3-venv).

They are made by a process of their own, not the window's (not on Windows): ag-cute-music --negatives (worker_main),
started when there is something queued, going on when AG CuteMusic quits and ending once the queue is empty. The
window and it share the queue file (edited under a lock, _edit_queue); the worker says what it is doing in
negatives-worker.json (what is being made, how far, what finished), which the window reads every second (Negatives
as a client: the same attributes and signals as when it makes them itself). Stop making negatives: the worker
asked to stop (SIGTERM: the song being made queued again first). Its log: negatives-worker.log.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, pyqtSignal

from cutemusic import util
from cutemusic.library import IMAGE_EXTENSIONS
from cutemusic.util import log


__all__ = ['Negatives', 'QUALITIES', 'DEVICES', 'BATCHES', 'negatives_folder', 'negative_path', 'original_relative', 'quality',
           'set_quality', 'device', 'set_device', 'batch', 'set_batch']


# an application started from the Finder has only /usr/bin:/bin:/usr/sbin:/sbin as its PATH: Homebrew's ffmpeg
# (/opt/homebrew/bin on Apple Silicon, /usr/local/bin on Intel) added, for shutil.which and for the separator, which
# runs it itself
if sys.platform == 'darwin':
    _path = os.environ.get('PATH', '').split(os.pathsep)
    os.environ['PATH'] = os.pathsep.join(_path + [folder for folder in ('/opt/homebrew/bin', '/usr/local/bin')
                                                  if folder not in _path and os.path.isdir(folder)])

MODEL = 'model_bs_roformer_ep_317_sdr_12.9755.ckpt'
ENV_DIR = os.path.join(util.CONFIG_DIR, 'separator')
MODELS_DIR = os.path.join(util.CONFIG_DIR, 'separator-models')
QUEUE_FILE = os.path.join(util.CONFIG_DIR, 'negatives-queue.json')
STATUS_FILE = os.path.join(util.CONFIG_DIR, 'negatives-worker.json')   # the worker's state, read by the window
WORKER_LOCK = os.path.join(util.CONFIG_DIR, 'negatives-worker.lock')   # held while a worker runs
WORKER_LOG = os.path.join(util.CONFIG_DIR, 'negatives-worker.log')
# made in a process of their own, going on after quitting: where processes can be detached and files locked
DETACHED = sys.platform != 'win32'
# what the separator's install is: a new one makes the venv's packages installed again (2: beartype >= 0.21, for
# Python 3.14; audio-separator pins it < 0.19, which cannot read 3.14's "Callable | None" hints)
INSTALL = '2'
# audio-separator's command line, run by the venv's python: on an Apple Silicon Mac its processor reported as "arm"
# (audio-separator uses the GPU, MPS, only then, and Python often says "" or "i386" there)
_RUN = '''
import os, platform, sys
if os.environ.get('AG_CUTEMUSIC_SEPARATOR_CPU'):        # the CPU only: no GPU seen
    import torch
    torch.cuda.is_available = lambda: False
    torch.backends.mps.is_available = lambda: False
elif sys.platform == 'darwin' and platform.machine() == 'arm64':
    from audio_separator.separator import separator as module
    original = module.Separator.get_system_info

    def get_system_info(self):
        info = original(self)
        if info.processor != 'arm':
            fields = {name: getattr(info, name) for name in ('system', 'node', 'release', 'version', 'machine')}
            info = type('SystemInfo', (), dict(fields, processor='arm'))()
        return info
    module.Separator.get_system_info = get_system_info
from audio_separator.utils.cli import main
sys.argv[0] = 'audio-separator'
sys.exit(main())
'''
_PERCENT = re.compile(r'(\d{1,3})%\|')


def _edit_queue(edit=None):
    """The queue file read (and, edit given, changed: edit(queue) -> queue, then written) under a lock: the window
    and the worker both change it."""
    lock = None
    try:
        os.makedirs(util.CONFIG_DIR, exist_ok=True)
        lock = open(QUEUE_FILE + '.lock', 'a')
        if DETACHED:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(QUEUE_FILE, encoding='utf-8') as file:
                queue = [tuple(entry) for entry in json.load(file)]
        except (OSError, ValueError):
            queue = []
        if edit is not None:
            queue = list(edit(queue))
            with open(QUEUE_FILE + '.tmp', 'w', encoding='utf-8') as file:
                json.dump(queue, file)
            os.replace(QUEUE_FILE + '.tmp', QUEUE_FILE)
        return queue
    except OSError as e:
        log.warning(f'[negatives] {QUEUE_FILE}: {e}')
        return []
    finally:
        if lock is not None:
            lock.close()            # (the lock with it)


def _worker_pid():
    """The worker's pid while one runs (it holds WORKER_LOCK), else None."""
    if not DETACHED:
        return None
    import fcntl
    try:
        with open(WORKER_LOCK, 'a+') as file:
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:         # held: one runs; its pid in it
                file.seek(0)
                text = file.read().strip()
                return int(text) if text.isdigit() else -1
            fcntl.flock(file, fcntl.LOCK_UN)
            return None
    except OSError:
        return None


def _start_worker():
    """A worker started, detached (in a session of its own: it goes on when AG CuteMusic quits)."""
    if util.self_executable():
        command = [util.self_executable(), '--negatives']
    else:
        root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        command = [sys.executable, '-c', f'import sys; sys.path.insert(0, {root!r}); from cutemusic.negatives import worker_main; '
                                         f'sys.exit(worker_main())']
    try:
        with open(WORKER_LOG, 'a') as output:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                             start_new_session=True, close_fds=True, cwd=util.CONFIG_DIR)
        log.info('[negatives] the worker started (it goes on after quitting)')
        return True
    except OSError as e:
        log.warning(f'[negatives] the worker not started: {e}')
        return False


def negatives_folder(root=None):
    """The negatives' folder: settings negatives/folder, else next to the music folder, with the same name and
    "-Negatives" (~/Music/Player: ~/Music/Player-Negatives)."""
    folder = util.settings().value('negatives/folder', '', type=str)
    if folder:
        return os.path.expanduser(folder)
    if root is None:
        from cutemusic.library import music_folder
        root = music_folder()
    return os.path.normpath(os.path.expanduser(root)) + '-Negatives'


# the separator's quality (settings negatives/quality): how many overlapping windows each stretch of a song is
# separated in and averaged over (audio-separator's --mdxc_overlap); fast: up to 3-4 times quicker (on a CPU)
QUALITIES = [('best', 'Best (8 overlapping windows)', 8), ('fast', 'Fast (2 overlapping windows)', 2)]


# where the separator runs (settings negatives/device): the GPU when there is one (NVIDIA's, Apple Silicon's), or
# the CPU only; and how many windows it separates at a time (negatives/batch: on a GPU, more a pass is often faster)
DEVICES = [('auto', 'GPU when there is one'), ('cpu', 'CPU only')]
BATCHES = [1, 4, 8]


def quality():
    value = util.settings().value('negatives/quality', 'best', type=str)
    return value if value in [key for key, label, overlap in QUALITIES] else 'best'


def set_quality(value):
    util.settings().setValue('negatives/quality', value)


def device():
    value = util.settings().value('negatives/device', 'auto', type=str)
    return value if value in [key for key, label in DEVICES] else 'auto'


def set_device(value):
    util.settings().setValue('negatives/device', value)


def batch():
    value = util.settings().value('negatives/batch', 1, type=int)
    return value if value in BATCHES else 1


def set_batch(value):
    util.settings().setValue('negatives/batch', int(value))


def negative_path(path, root, folder=None):
    """Where the negative of the song at path (in root, the music folder) is, made or not; None out of root."""
    if not path or not root:
        return None
    path, root = os.path.abspath(path), os.path.abspath(os.path.expanduser(root))
    if os.path.commonpath([path, root]) != root:
        return None
    relative = os.path.relpath(path, root)
    return os.path.join(folder or negatives_folder(root), os.path.splitext(relative)[0] + '.flac')


def original_relative(path, folder=None):
    """A negative's path in the music folder without its extension (the song's, whatever its format)."""
    folder = os.path.abspath(folder or negatives_folder())
    return os.path.splitext(os.path.relpath(os.path.abspath(path), folder))[0]


def _python():
    """The python3 to make the separator's venv with: the system's (the app's own when it is a bundle is not one)."""
    if not getattr(sys, 'frozen', False) and os.path.basename(sys.executable).startswith('python'):
        return sys.executable
    return shutil.which('python3') or 'python3'


def _env_python():
    return os.path.join(ENV_DIR, 'Scripts' if sys.platform == 'win32' else 'bin', 'python.exe' if sys.platform == 'win32' else 'python')


def separator_ready():
    try:
        with open(os.path.join(ENV_DIR, '.ready')) as file:
            return file.read().strip() == INSTALL
    except OSError:
        return False


def copy_metadata(source, destination, final=None):
    """The song's tags, cover and lyrics onto its negative (the FLAC at destination, to be at final), and its lyrics
    files and folder images next to final: the negative is shown, found and sung along as the song."""
    final = final or destination
    try:
        import mutagen
        from mutagen.flac import FLAC, Picture
    except ImportError:
        return
    try:
        easy = mutagen.File(source, easy=True)
        flac = FLAC(destination)
        if easy is not None and easy.tags is not None:
            for key in easy.tags.keys():
                try:
                    values = [str(value) for value in easy.tags[key]]
                except (KeyError, ValueError):
                    continue
                if values:
                    flac[key] = values
        from cutemusic.library import _embedded_cover, _embedded_lyrics
        cover = _embedded_cover(source)
        if cover:
            picture = Picture()
            picture.type = 3
            picture.mime = 'image/png' if cover[:8] == b'\x89PNG\r\n\x1a\n' else 'image/jpeg'
            picture.data = cover
            flac.clear_pictures()
            flac.add_picture(picture)
        lyrics = _embedded_lyrics(source)
        if lyrics:
            flac['LYRICS'] = lyrics
        flac.save()
    except Exception as e:
        log.warning(f'[negatives] tags of {source} not copied: {e}')
    base, folder = os.path.splitext(source)[0], os.path.dirname(final)
    for extension in ('.lrc', '.txt'):
        if os.path.exists(base + extension):
            shutil.copy2(base + extension, os.path.splitext(final)[0] + extension)
    try:
        for name in os.listdir(os.path.dirname(source)):
            if os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS and not os.path.exists(os.path.join(folder, name)):
                shutil.copy2(os.path.join(os.path.dirname(source), name), os.path.join(folder, name))
    except OSError:
        pass


class Negatives(QObject):
    """The queue of songs to make negatives of, and the separator making them. changed(): the queue or what is
    being done changed (status()); made(source, negative): one is there."""

    changed = pyqtSignal()
    made = pyqtSignal(str, str)

    def __init__(self, parent=None, worker=False):
        super().__init__(parent)
        # in the window (DETACHED): a client of the worker; in the worker, or on Windows: the one making them
        self.client = DETACHED and not worker
        self.worker = worker
        self.queue = []             # [(source, negative)]
        self.process = None
        self.stage = None           # 'install', 'separate'
        self.current = None         # (source, negative) being made
        self.percent = 0
        self.message = ''
        self.failed = 0
        self.workdir = None
        self.started = 0.0
        self.quality, self.device, self.batch = 'best', 'auto', 1
        self.finished = []          # [(source, negative, seconds, error)]: made (error None) or not, this session
        self._output = ''
        self._load()
        self._worker = None         # the worker's pid (client)
        self._seen = set()          # what the worker finished, taken already (client): (its pid, index)
        self._forgotten = set()     # sources forgotten here (forget, clear_finished), not taken again
        if self.client:
            from PyQt6.QtCore import QTimer
            self._poll = QTimer(self, interval=1000)
            self._poll.timeout.connect(self._read_worker)
            self._poll.start()
            self._read_worker()

    # The queue

    def _load(self):
        self.queue = _edit_queue()

    def _save(self):
        queue = list(self.queue)
        _edit_queue(lambda old: queue)

    # The client (the window): what the worker does, read from its file every second

    def _read_worker(self):
        pid = _worker_pid()
        try:
            with open(STATUS_FILE, encoding='utf-8') as file:
                status = json.load(file)
        except (OSError, ValueError):
            status = {}
        old = (self.current, self.percent, self.stage, self.message, list(self.queue), self._worker)
        self._worker = pid
        self.queue = _edit_queue()
        alive = pid is not None and status.get('pid') == pid
        if alive:
            current = status.get('current')
            self.current = tuple(current) if current else None
            self.percent, self.stage, self.message = status.get('percent', 0), status.get('stage'), status.get('message', '')
        else:
            self.current, self.percent, self.stage = None, 0, None
            if status.get('message') and not self.queue:
                self.message = status.get('message', '')
        made = []
        for index, entry in enumerate(status.get('finished', [])):
            key = (status.get('pid'), index)
            if key in self._seen:
                continue
            self._seen.add(key)
            source, negative, seconds, error = entry
            if source in self._forgotten:
                continue
            self.finished = [item for item in self.finished if item[0] != source] + [(source, negative, seconds, error)]
            if error is None:
                made.append((source, negative))
        self.failed = sum(1 for entry in self.finished if entry[3])
        for source, negative in made:
            self.made.emit(source, negative)
        if made or old != (self.current, self.percent, self.stage, self.message, list(self.queue), self._worker):
            self.changed.emit()
        if self.queue and pid is None and not getattr(self, '_stopped', False):
            self._spawn()                   # something queued and no worker (it ended as songs came): one again

    def _spawn(self):
        if shutil.which('ffmpeg') is None:     # the separator reads and writes through it: the queue waits
            install = {'darwin': 'brew install ffmpeg', 'win32': 'winget install ffmpeg'}.get(sys.platform, 'sudo apt install ffmpeg')
            message = f'ffmpeg is needed ({install}), then Make negative again'
            if message != self.message:
                self.message = message
                log.warning(f'[negatives] {self.message}')
                self.changed.emit()
            return
        now = time.monotonic()
        if now - getattr(self, '_spawned', 0) < 10:     # (one starting: not another)
            return
        self._spawned = now
        _start_worker()

    def detach(self):
        """Quitting: the worker goes on (made in the window itself, on Windows: stopped)."""
        if not self.client:
            self.stop()

    # The worker: what it does, said in its file

    def _publish(self):
        status = {'pid': os.getpid(), 'current': list(self.current) if self.current else None, 'percent': self.percent,
                  'stage': self.stage, 'message': self.message, 'finished': [list(entry) for entry in self.finished],
                  'updated': time.time()}
        try:
            with open(STATUS_FILE + '.tmp', 'w', encoding='utf-8') as file:
                json.dump(status, file)
            os.replace(STATUS_FILE + '.tmp', STATUS_FILE)
        except OSError as e:
            log.warning(f'[negatives] {STATUS_FILE}: {e}')

    def add(self, paths, root):
        """The songs at paths (in root) queued, the ones with a negative or queued already left out: how many."""
        if self.client:
            entries = [(path, negative_path(path, root)) for path in paths]
            return self.add_jobs([(source, negative) for source, negative in entries if negative is not None])
        queued = {source for source, negative in self.queue}
        if self.current is not None:
            queued.add(self.current[0])
        added = 0
        for path in paths:
            negative = negative_path(path, root)
            if negative is None or path in queued or os.path.exists(negative):
                continue
            self.queue.append((path, negative))
            queued.add(path)
            added += 1
        self._save()
        self.changed.emit()
        self.start()
        return added

    def add_jobs(self, entries):
        """(source, negative) pairs queued as they are, the negative where given (web.py: songs sent from the LAN,
        not in the music folder); the ones queued or made already left out: how many."""
        if self.client:
            added = []

            def edit(queue):
                queued = {source for source, negative in queue} | ({self.current[0]} if self.current else set())
                for source, negative in entries:
                    if source not in queued and not os.path.exists(negative):
                        queue.append((source, negative))
                        queued.add(source)
                        added.append(source)
                return queue
            self.queue = _edit_queue(edit)
            self._forgotten -= set(added)
            self._stopped = False
            self.changed.emit()
            if added:
                self.start()
            return len(added)
        queued = {source for source, negative in self.queue}
        if self.current is not None:
            queued.add(self.current[0])
        added = 0
        for source, negative in entries:
            if source in queued or os.path.exists(negative):
                continue
            self.queue.append((source, negative))
            queued.add(source)
            added += 1
        self._save()
        self.changed.emit()
        self.start()
        return added

    def resume(self):
        """At start: what was left queued goes on."""
        if self.queue:
            self.start()

    @property
    def busy(self):
        return self._worker is not None if self.client else self.process is not None

    def status(self):
        """A line for the status bar: what is being done, how much is left; '' when idle with nothing queued."""
        left = len(self.queue)
        failed = f', {self.failed} failed (see the log)' if self.failed else ''
        if self.stage == 'install':
            return f'Negatives: installing the separator (once, about 3 GB)... {self.message}'
        if self.current is not None:
            name = os.path.splitext(os.path.basename(self.current[0]))[0]
            more = f', {left} more queued' if left else ''
            return f'Negatives: making "{name}" {self.percent}%{more}{failed}'
        if left:
            why = f' — {self.message}' if self.message and self.stage is None else ' (stopped)'
            return f'Negatives: {left} queued{why}{failed}'
        return f'Negatives: {self.failed} failed (see the log)' if self.failed else ''

    def stop(self):
        """The one being made abandoned (queued again first), the rest kept for later (resume, or Make negative)."""
        if self.client:     # the worker asked to stop (it queues it again itself)
            self._stopped = True
            if self._worker and self._worker > 0:
                try:
                    os.kill(self._worker, signal.SIGTERM)
                    log.info('[negatives] the worker asked to stop')
                except OSError:
                    pass
                for _ in range(50):         # gone (the song being made queued again) before the queue is changed here
                    if _worker_pid() is None:
                        break
                    time.sleep(0.1)
                self._read_worker()
            return
        if self.process is None:
            return
        if self.current is not None:
            current = self.current
            self.queue = _edit_queue(lambda queue: [current] + [entry for entry in queue if entry[0] != current[0]])
        process, self.process = self.process, None
        process.kill()
        process.waitForFinished(3000)
        self.current, self.stage = None, None
        self._cleanup()
        self.changed.emit()

    def clear(self):
        """The queue emptied (the one being made stopped)."""
        self.stop()
        self.queue = []
        self._save()
        self.changed.emit()

    # Making them

    def start(self):
        if self.client:         # a worker makes them (one started when none runs)
            self._stopped = False
            self.queue = _edit_queue()
            if self.queue and _worker_pid() is None:
                self._spawn()
            return
        if self.process is not None or not self.queue:
            return
        if shutil.which('ffmpeg') is None:     # the separator reads and writes through it: the queue waits
            install = {'darwin': 'brew install ffmpeg', 'win32': 'winget install ffmpeg'}.get(sys.platform, 'sudo apt install ffmpeg')
            self.message = f'ffmpeg is needed ({install}), then Make negative again'
            log.warning(f'[negatives] {self.message}')
            self.changed.emit()
            return
        self.message = ''
        if not separator_ready():
            self._install()
            return
        self._next()

    def _run(self, stage, program, arguments, finished):
        process = self.process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert('PYTHONUNBUFFERED', '1')
        environment.insert('AUDIO_SEPARATOR_MODEL_DIR', MODELS_DIR)
        environment.insert('PYTORCH_ENABLE_MPS_FALLBACK', '1')     # Metal lacks an operation: that one on the CPU
        if stage == 'separate' and self.device == 'cpu':
            environment.insert('AG_CUTEMUSIC_SEPARATOR_CPU', '1')
        process.setProcessEnvironment(environment)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(lambda: self._read(process))
        process.finished.connect(lambda code, status: self._finished(process, code, status, finished))
        process.errorOccurred.connect(lambda error: self._error(process, error, finished))
        self.stage = stage
        self._output = ''
        if arguments[:1] == ['-c'] and arguments[1:2] == [_RUN]:      # the separator: not its wrapper's code
            log.info(f'[negatives] audio-separator {" ".join(arguments[2:])}')
        else:
            log.info(f'[negatives] {program} {" ".join(arguments)}')
        process.start(program, arguments)

    def _read(self, process):
        text = bytes(process.readAllStandardOutput()).decode('utf-8', 'replace')
        self._output = (self._output + text)[-20000:]
        if self.stage == 'separate':
            found = _PERCENT.findall(text)
            if found:
                percent = int(found[-1])
                if percent != self.percent:
                    self.percent = percent
                    self.changed.emit()
        else:
            lines = [line.strip() for line in re.split(r'[\r\n]', text) if line.strip()]
            if lines:
                self.message = lines[-1][:80]
                self.changed.emit()

    def _error(self, process, error, finished):
        if error == QProcess.ProcessError.FailedToStart and process is self.process:
            self._output += f'\n{process.program()}: cannot start ({process.errorString()})'
            self._finished(process, -1, QProcess.ExitStatus.CrashExit, finished)

    def _finished(self, process, code, status, finished):
        if process is not self.process:     # stopped
            return
        self.process = None
        ok = status == QProcess.ExitStatus.NormalExit and code == 0
        if not ok:
            log.warning(f'[negatives] {self.stage} failed ({code}):\n{self._output[-4000:]}')
        finished(ok)

    # The separator's venv: python3 -m venv, PyTorch (CPU, or CUDA's with an NVIDIA GPU), audio-separator

    def _install(self):
        self.stage, self.message = 'install', 'the venv'
        self.changed.emit()
        if os.path.exists(_env_python()):
            self._install_packages(True)
            return
        os.makedirs(os.path.dirname(ENV_DIR), exist_ok=True)
        self._run('install', _python(), ['-m', 'venv', ENV_DIR], self._install_packages)

    def _install_packages(self, ok):
        if not ok:
            return self._install_failed('cannot make a venv (python3-venv missing?)')
        gpu = shutil.which('nvidia-smi') is not None
        script = ('import subprocess, sys\n'
                  'pip = [sys.executable, "-m", "pip", "install", "--upgrade", "--progress-bar", "off"]\n'
                  'subprocess.check_call(pip + ["pip"])\n'
                  + ('' if gpu or sys.platform == 'darwin' else
                     'subprocess.check_call(pip + ["torch", "--index-url", "https://download.pytorch.org/whl/cpu"])\n')
                  + f'subprocess.check_call(pip + ["audio-separator[{"gpu" if gpu else "cpu"}]", "audioread"])\n'
                  'subprocess.check_call(pip + ["--no-deps", "beartype>=0.21"])\n')
        self._run('install', _env_python(), ['-c', script], self._installed)

    def _installed(self, ok):
        if not ok:
            return self._install_failed('pip could not install audio-separator')
        with open(os.path.join(ENV_DIR, '.ready'), 'w') as file:
            file.write(INSTALL)
        log.info('[negatives] the separator is installed')
        self.stage = None
        self.start()

    def _install_failed(self, why):
        self.stage = None
        self.failed += len(self.queue)
        log.warning(f'[negatives] {why}: nothing made ({ENV_DIR})')
        self.message = why
        self.queue = []
        self._save()
        self.changed.emit()

    # A song

    def _next(self):
        from cutemusic.library import music_folder
        root = music_folder()
        taken = []

        def take(queue):            # (the queue as in its file: the window may have added to it)
            while queue:
                source, negative = queue[0]
                negative = negative_path(source, root) or negative      # where it goes now (the folder may have moved)
                queue.pop(0)        # (gone, or made meanwhile: dropped)
                if os.path.exists(source) and not os.path.exists(negative):
                    taken.append((source, negative))
                    break
            return queue
        self.queue = _edit_queue(take)
        if not taken:
            self.current, self.stage = None, None
            self.changed.emit()
            return
        source, negative = taken[0]
        self.current = (source, negative)
        self.quality, self.device, self.batch = quality(), device(), batch()     # (as set when it starts: for the next song)
        self.percent = 0
        self.started = time.monotonic()
        self.workdir = tempfile.mkdtemp(prefix='ag-cute-music-negative-')
        os.makedirs(MODELS_DIR, exist_ok=True)
        self.changed.emit()
        self._run('separate', _env_python(),
                  ['-c', _RUN, self.current[0], '-m', MODEL, '--model_file_dir', MODELS_DIR, '--output_dir', self.workdir,
                   '--output_format', 'FLAC', '--single_stem', 'Instrumental',
                   '--mdxc_overlap', str(next(overlap for key, label, overlap in QUALITIES if key == self.quality)),
                   '--mdxc_batch_size', str(self.batch)],
                  self._separated)

    def _separated(self, ok):
        source, negative = self.current
        made = None
        if ok:
            found = [name for name in os.listdir(self.workdir) if name.lower().endswith('.flac')]
            made = os.path.join(self.workdir, found[0]) if found else None
        elapsed = time.monotonic() - self.started
        if made is None:
            self.failed += 1
            error = self._error_line()
            self.finished.append((source, negative, elapsed, error))
            log.warning(f'[negatives] no negative made of {source}: {error}')
        else:
            try:
                os.makedirs(os.path.dirname(negative), exist_ok=True)
                partial = negative + '.part'
                shutil.move(made, partial)
                copy_metadata(source, partial, negative)
                os.replace(partial, negative)
                minutes, seconds = divmod(int(time.monotonic() - self.started), 60)
                log.info(f'[negatives] made {negative} in {minutes}:{seconds:02d} ({self._device()}, {self.quality}, {self.batch} a pass)')
                self.finished.append((source, negative, elapsed, None))
                self.made.emit(source, negative)
            except OSError as e:
                self.failed += 1
                self.finished.append((source, negative, elapsed, str(e)))
                log.warning(f'[negatives] {negative}: {e}')
        self._cleanup()
        self.current = None
        self._next()

    def _device(self):
        """What the separator ran on, from its output: "Torch device ... mps / cuda / cpu"."""
        found = re.findall(r'(?:setting|using) Torch device to (\w+)|Torch device: (\w+)', self._output, re.IGNORECASE)
        devices = {(first or second).lower() for first, second in found}
        if devices:
            return 'on ' + ', '.join(sorted(devices))
        if 'running in CPU mode' in self._output:
            return 'on cpu'
        lines = [line.split(' - ')[-1].strip() for line in self._output.splitlines() if 'device' in line.lower()]
        return lines[-1][:120] if lines else 'device not reported'

    def _error_line(self):
        """The separator's last error, in a line (its traceback is in the log)."""
        lines = [line.strip() for line in self._output.splitlines() if line.strip()]
        for line in reversed(lines):
            if 'Error' in line or 'error' in line.split(' - ')[-1:][0]:
                return line.split(' - ')[-1][:200]
        return lines[-1][:200] if lines else 'the separator stopped'

    def remove(self, sources):
        """Songs taken out of the queue (not the one being made)."""
        sources = set(sources)
        self.queue = _edit_queue(lambda queue: [entry for entry in queue if entry[0] not in sources])
        self.changed.emit()

    def forget(self, sources):
        """Songs taken out of what finished (a failed one tried again)."""
        sources = set(sources)
        self._forgotten |= sources
        self.finished = [entry for entry in self.finished if entry[0] not in sources]
        self.failed = sum(1 for entry in self.finished if entry[3])
        self.changed.emit()

    def clear_finished(self):
        self._forgotten |= {entry[0] for entry in self.finished}
        self.finished = []
        self.failed = 0
        self.changed.emit()

    def _cleanup(self):
        if self.workdir:
            shutil.rmtree(self.workdir, ignore_errors=True)
            self.workdir = None



def worker_main():
    """ag-cute-music --negatives: the queue made, in a process of its own (detached: it goes on when the window is
    closed), until it is empty; one at a time (WORKER_LOCK). SIGTERM: the song being made queued again, then it ends."""
    import fcntl
    import logging
    from PyQt6.QtCore import QCoreApplication, QTimer
    application = QCoreApplication(sys.argv[:1])
    os.makedirs(util.CONFIG_DIR, exist_ok=True)
    lock = open(WORKER_LOCK, 'a+')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0                    # one runs already
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    log.setLevel(logging.INFO)
    if not log.handlers:
        handler = logging.StreamHandler(sys.stdout)       # (the worker's log file)
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        log.addHandler(handler)
    log.info(f'[negatives] worker {os.getpid()} started')
    negatives = Negatives(worker=True)
    publish = QTimer(interval=1000)
    publish.timeout.connect(negatives._publish)
    publish.start()

    def changed():
        negatives._publish()
        if negatives.process is None and negatives.stage is None and not negatives.queue:
            QTimer.singleShot(2000, done)       # (a song added meanwhile: taken then)

    def done():
        if negatives.process is None and negatives.stage is None:
            negatives.queue = _edit_queue()
            if negatives.queue:
                negatives.start()
                return
            application.quit()
    negatives.changed.connect(changed)
    stopping = []
    signal.signal(signal.SIGTERM, lambda *arguments: stopping.append(True))
    watch = QTimer(interval=200)            # (python's signal handler runs between Qt events: looked at here)
    watch.timeout.connect(lambda: (negatives.stop(), application.quit()) if stopping else None)
    watch.start()
    negatives.start()
    if not negatives.queue and negatives.process is None and negatives.stage is None:
        changed()
    application.exec()
    negatives.current, negatives.stage, negatives.percent = None, None, 0
    negatives._publish()
    log.info(f'[negatives] worker {os.getpid()} ended')
    return 0
