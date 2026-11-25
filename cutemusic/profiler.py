"""The built-in profiler (--profile, or CUTEMUSIC_PROFILE=1; CUTEMUSIC_PROFILE=10: a report every 10 s): where the
CPU goes, per component, logged as [profile] tables.

Components: the views' paintEvents, their animation ticks and the levels/spectrum they are given, the player's bus
polling and message parsing, the mic's polling, the link's mixing. Each one's CPU time (the thread's, so time spent
waiting is not counted; a component's own time, what it calls that is measured too taken out), calls per second,
and the longest call in wall time (a frame that ran late).

Threads: every thread of the process (GStreamer's streaming threads, PipeWire's, Qt's, the link's, ...) by the name
the system knows it by, from /proc (Linux) or psutil (macOS, Windows: when installed; else not shown). The main
thread's time not in any component is shown as "main: Qt and the rest" (the event loop, the drawing flushed to the
screen, the widgets not measured).

Percentages are of one core: 100 % is a core kept busy.
"""

import os
import sys
import threading
import time

from cutemusic.util import log

# (module, class or None for a module function, function, label)
TARGETS = [
    ('amp', 'AmpView', 'paintEvent', 'amplifier paint'),
    ('amp', 'AmpView', '_tick', 'amplifier tick'),
    ('amp', 'AmpView', 'set_levels', 'amplifier levels'),
    ('amp', 'AmpView', 'set_spectrum', 'amplifier spectrum'),
    ('visuals', 'VUMeter', 'paintEvent', 'VU meter paint'),
    ('visuals', 'VUMeter', '_tick', 'VU meter tick'),
    ('visuals', 'VUMeter', 'set_levels', 'VU meter levels'),
    ('visuals', 'VUMeter', 'set_spectrum', 'VU meter spectrum'),
    ('visuals', 'VisualPane', 'set_levels', 'visual pane levels'),
    ('visuals', 'CoverView', 'paintEvent', 'cover paint'),
    ('visuals', 'LyricsStrip', 'paintEvent', 'lyrics strip paint'),
    ('mixer', 'MixerView', 'paintEvent', 'mixer paint'),
    ('mixer', 'MixerView', '_tick', 'mixer tick'),
    ('mixer', 'MixerView', '_paint_console', 'mixer console (cached)'),
    ('mixer', 'MixerView', '_paint_moving', 'mixer moving layer'),
    ('mixer', 'MixerView', '_draw_tape_window', 'mixer tape window'),
    ('mixer', 'MixerView', 'set_mic_levels', 'mixer mic levels'),
    ('mixer', 'MixerView', '_draw_button', 'mixer draw button'),
    ('mixer', 'MixerView', '_draw_calls', 'mixer draw calls'),
    ('mixer', 'MixerView', '_draw_fader', 'mixer draw fader'),
    ('mixer', 'MixerView', '_draw_glow', 'mixer draw glow'),
    ('mixer', 'MixerView', '_draw_hangup', 'mixer draw hangup'),
    ('mixer', 'MixerView', '_draw_library', 'mixer draw library'),
    ('mixer', 'MixerView', '_draw_link', 'mixer draw link'),
    ('mixer', 'MixerView', '_draw_meter', 'mixer draw meter'),
    ('mixer', 'MixerView', '_draw_needles', 'mixer draw needles'),
    ('mixer', 'MixerView', '_draw_on_air', 'mixer draw on air'),
    ('mixer', 'MixerView', '_draw_participant_meters', 'mixer draw participant meters'),
    ('mixer', 'MixerView', '_draw_quality', 'mixer draw quality'),
    ('mixer', 'MixerView', '_draw_recorder', 'mixer draw recorder'),
    ('mixer', 'MixerView', '_draw_redial', 'mixer draw redial'),
    ('mixer', 'MixerView', '_draw_scale', 'mixer draw scale'),
    ('mixer', 'MixerView', '_draw_selector', 'mixer draw selector'),
    ('mixer', 'MixerView', '_draw_sign', 'mixer draw sign'),
    ('mixer', 'MixerView', '_draw_tape', 'mixer draw tape'),
    ('mixer', 'MixerView', '_draw_track', 'mixer draw track'),
    ('mixer', 'MixerView', '_label_under', 'mixer label under'),
    ('mixer', 'MixerView', '_font', 'mixer font'),
    ('mixer', None, 'draw_knob', 'mixer draw knob'),
    ('vufaces', 'AnalogFaces', '_draw_needle', 'faces needle'),
    ('vufaces', 'AnalogFaces', '_draw_face', 'faces face'),
    ('vufaces', 'AnalogFaces', '_draw_backlight', 'faces backlight'),
    ('lava', 'LavaLamp', 'paintEvent', 'lava paint'),
    ('lava', 'LavaLamp', '_tick', 'lava tick'),
    ('lava', 'LavaLamp', 'set_levels', 'lava levels'),
    ('lava', 'LavaLamp', 'set_spectrum', 'lava spectrum'),
    ('dockicon', 'DockIcon', '_tick', 'dock icon tick'),
    ('dockicon', 'DockIcon', 'set_levels', 'dock icon levels'),
    ('player', 'Player', '_poll', 'player bus poll'),
    ('player', 'Player', '_message', 'player messages'),
    ('player', None, '_doubles', 'player level/spectrum parsing'),
    ('player', 'Player', '_watch_outputs', 'player outputs probe'),
    ('player', 'Player', '_running_time', 'player clock'),
    ('player', 'Player', '_spectrum_rate', 'player spectrum rate'),
    ('mic', 'Mic', '_poll', 'mic poll'),
    ('mic', '_Output', '_poll', 'mic output poll'),
    ('link', 'LinkSender', '_take_clocked', 'link inputs'),
    ('link', None, '_mix', 'link mixing'),
    ('window', 'MainWindow', '_position', 'window position update'),
]

DEFAULT_INTERVAL = 5        # s between two reports

_stats = {}         # label: [cpu ns (own), calls, longest wall ns, cpu ns (own) in the main thread]
_lock = threading.Lock()
_local = threading.local()      # the stack of the components running in this thread: (label, children's cpu ns)
_started = False


def requested():
    return '--profile' in sys.argv[1:] or bool(os.environ.get('CUTEMUSIC_PROFILE'))


def _interval():
    try:
        value = float(os.environ.get('CUTEMUSIC_PROFILE', ''))
        return value if value >= 1 else DEFAULT_INTERVAL
    except ValueError:
        return DEFAULT_INTERVAL


def _wrap(function, label):
    thread_time, perf = time.thread_time_ns, time.perf_counter_ns
    main = threading.main_thread().ident

    def measured(*args, **kwargs):
        stack = getattr(_local, 'stack', None)
        if stack is None:
            stack = _local.stack = []
        frame = [label, 0]
        stack.append(frame)
        cpu, wall = thread_time(), perf()
        try:
            return function(*args, **kwargs)
        finally:
            cpu, wall = thread_time() - cpu, perf() - wall
            stack.pop()
            if stack:
                stack[-1][1] += cpu         # the caller's own time: without this one's
            own = cpu - frame[1]
            with _lock:
                entry = _stats.get(label)
                if entry is None:
                    entry = _stats[label] = [0, 0, 0, 0]
                entry[0] += own
                if threading.get_ident() == main:
                    entry[3] += own
                entry[1] += 1
                if wall > entry[2]:
                    entry[2] = wall
    measured.__name__ = getattr(function, '__name__', 'measured')
    measured.__wrapped__ = function
    return measured


def install():
    """The components wrapped, and the reports started (the QApplication made, before the main window: the timers
    and signals connect to the methods when the views are made). Nothing when not requested()."""
    global _started
    if _started or not requested():
        return
    _started = True
    import importlib
    missing = []
    for module_name, class_name, name, label in TARGETS:
        try:
            module = importlib.import_module(f'cutemusic.{module_name}')
            owner = getattr(module, class_name) if class_name else module
            function = owner.__dict__[name] if class_name else getattr(module, name)
        except Exception:
            missing.append(f'{module_name}.{class_name + "." if class_name else ""}{name}')
            continue
        setattr(owner, name, _wrap(function, label))
    if missing:
        log.info(f'[profile] not measured (not there): {", ".join(missing)}')
    from PyQt6.QtCore import QCoreApplication, QTimer
    application = QCoreApplication.instance()
    timer = QTimer(application)
    reporter = _Reporter()
    timer.timeout.connect(reporter.report)
    timer.start(int(_interval() * 1000))
    application._profile = (timer, reporter)        # kept: PyQt holds a bound method's object only weakly
    log.info(f'[profile] on: a report every {_interval():g} s (% of one core)')
    from cutemusic import link
    path = ('C (_feedio)' if link._c_mix() is not None else 'audioop' if link.audioop is not None else
            f'numpy {link.numpy.__version__}' if link.numpy is not None else 'plain Python (slow)')
    try:
        import numpy
        found = f'numpy {numpy.__version__} at {numpy.__file__}'
    except Exception as e:
        found = f'numpy not importable: {e!r}'
    log.info(f'[profile] Python {sys.version.split()[0]} ({sys.executable}); the link mixes with {path}; {found}')


class _Reporter:

    def __init__(self):
        self.at = time.monotonic()
        self.process = _process_cpu()
        self.threads = _threads_cpu()

    def report(self):
        now = time.monotonic()
        elapsed = max(now - self.at, 1e-3)
        with _lock:
            stats = {label: list(entry) for label, entry in _stats.items()}
            _stats.clear()
        process, threads = _process_cpu(), _threads_cpu()
        lines = [f'[profile] last {elapsed:.1f} s: the process {100 * (process - self.process) / elapsed:.1f} % '
                 f'({os.cpu_count() or "?"} cores)']
        main_measured = 0
        rows = sorted(stats.items(), key=lambda item: -item[1][0])
        if rows:
            lines.append(f'  {"component":<32} {"cpu %":>7} {"calls/s":>8} {"avg ms":>7} {"max ms":>7}')
        for label, (cpu, calls, longest, main_cpu) in rows:
            main_measured += main_cpu
            lines.append(f'  {label:<32} {100 * cpu / 1e9 / elapsed:7.2f} {calls / elapsed:8.1f} '
                         f'{cpu / 1e6 / max(calls, 1):7.3f} {longest / 1e6:7.2f}')
        if threads is not None and self.threads is not None:
            main = threading.main_thread().native_id
            used = []
            for tid, (name, cpu) in threads.items():
                before = self.threads.get(tid, (name, 0.0))[1]
                used.append((cpu - before, tid, name))
            used.sort(reverse=True)
            lines.append(f'  {"thread":<32} {"cpu %":>7}')
            for cpu, tid, name in used:
                if cpu / elapsed < 0.002:       # under 0.2 %: left out
                    continue
                shown = f'{name} (main)' if tid == main else name
                lines.append(f'  {shown:<32} {100 * cpu / elapsed:7.2f}')
                if tid == main:
                    rest = cpu - main_measured / 1e9
                    lines.append(f'  {"  main: Qt and the rest":<32} {100 * max(rest, 0) / elapsed:7.2f}')
        try:
            from cutemusic.player import output_diagnosis
            lines += [f'  output: {line}' for line in output_diagnosis()]
        except Exception as e:
            lines.append(f'  output: cannot tell ({e})')
        log.info('\n'.join(lines))
        self.at, self.process, self.threads = now, process, threads


def _process_cpu():
    times = os.times()
    return times.user + times.system


_TICKS = os.sysconf('SC_CLK_TCK') if hasattr(os, 'sysconf') else 100


def _threads_cpu():
    """{thread id: (name, cpu s)}, None when the system does not tell."""
    if os.path.isdir('/proc/self/task'):
        threads = {}
        for tid in os.listdir('/proc/self/task'):
            try:
                with open(f'/proc/self/task/{tid}/stat') as f:
                    stat = f.read()
            except OSError:
                continue        # (gone meanwhile)
            name = stat[stat.index('(') + 1:stat.rindex(')')]
            fields = stat[stat.rindex(')') + 2:].split()
            threads[int(tid)] = (name, (int(fields[11]) + int(fields[12])) / _TICKS)      # utime, stime
        return threads
    try:
        import psutil
    except ImportError:
        return None
    names = {thread.native_id: thread.name for thread in threading.enumerate()}
    try:
        return {thread.id: (names.get(thread.id, f'thread {thread.id}'), thread.user_time + thread.system_time)
                for thread in psutil.Process().threads()}
    except Exception:
        return None
