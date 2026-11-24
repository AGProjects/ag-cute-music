"""Another program's sound in the mixer (its CALL strip): Blink's call, say, heard on CuteMusic's output (the headset)
with its own fader, and never in the karaoke shared to calls (share.py: the friends would hear themselves).

A sink of CuteMusic's (module-null-sink, cutemusic_call) the program's streams are moved onto (pactl
move-sink-input) while it is chosen, the new ones as they come (a call's stream is made when the call starts); its
monitor played on the output by a Mic (mic.py) of its own. Off, the streams go back to the sinks they were on, the
sink unloaded. Blink's SIP audio (ALSA, through pipewire-pulse's plugin) and its WebRTC audio (pulsesink) are both
streams of its process: a program is its name, or "Blink" when its command line says so.
"""

import json
import os
import shutil
import subprocess

from PyQt6.QtCore import QObject, QTimer

from cutemusic.share import _pactl
from cutemusic.util import log


__all__ = ['AppRoute', 'SINK']


SINK = 'cutemusic_call'
DESCRIPTION = 'AG CuteMusic Call'


def _program(properties):
    """A stream's program, as shown: "Blink" for Blink (a Python program: its command line), else its name."""
    pid = properties.get('application.process.id')
    if pid:
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as file:
                if b'blink' in file.read().lower():
                    return 'Blink'
        except OSError:
            pass
    name = properties.get('application.name') or properties.get('application.process.binary') or ''
    if name.startswith('ALSA plug-in [') and name.endswith(']'):     # an ALSA program through pulse: its binary
        name = name[len('ALSA plug-in ['):-1]
    return name


class AppRoute(QObject):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.program = None
        self._module = None
        self._moved = {}            # stream index: the sink it was on
        self._timer = QTimer(self, interval=1000)
        self._timer.timeout.connect(self._follow)

    @staticmethod
    def available():
        return shutil.which('pactl') is not None

    @staticmethod
    def _streams():
        """[(index, sink, program)] of the streams playing, not CuteMusic's own."""
        try:
            streams = json.loads(_pactl('-f', 'json', 'list', 'sink-inputs') or '[]')
        except (RuntimeError, ValueError) as e:
            log.warning(f'[apps] cannot list the streams: {e}')
            return []
        own = str(os.getpid())
        found = []
        for stream in streams:
            properties = stream.get('properties') or {}
            if properties.get('application.process.id') == own:
                continue
            program = _program(properties)
            if program:
                found.append((str(stream.get('index')), str(stream.get('sink')), program))
        return found

    def programs(self):
        """The programs playing now, by name."""
        return sorted({program for index, sink, program in self._streams()}, key=str.lower)

    def start(self, program):
        """program's streams onto the call sink (its monitor: monitor()); None, else why not."""
        self.stop()
        if not self.available():
            return 'pactl is not installed (pulseaudio-utils)'
        try:
            for line in _pactl('list', 'short', 'modules').splitlines():      # left by a CuteMusic that did not quit
                fields = line.split('\t')
                if len(fields) > 2 and f'sink_name={SINK}' in fields[2]:
                    _pactl('unload-module', fields[0])
            self._module = _pactl('load-module', 'module-null-sink', f'sink_name={SINK}', 'rate=48000', 'channels=2',
                                  f"sink_properties='device.description=\"{DESCRIPTION}\"'")
        except RuntimeError as e:
            log.warning(f'[apps] {e}')
            return str(e)
        self.program = program
        self._follow()
        self._timer.start()
        log.info(f'[apps] {program} heard in the mixer')
        return None

    @staticmethod
    def monitor():
        return f'{SINK}.monitor'

    def stop(self):
        """The streams back where they were, the sink gone."""
        self._timer.stop()
        sinks = {}
        try:
            for line in _pactl('list', 'short', 'sinks').splitlines():
                fields = line.split('\t')
                if len(fields) > 1:
                    sinks[fields[0]] = fields[1]
        except RuntimeError:
            pass
        for index, sink in self._moved.items():
            try:
                _pactl('move-sink-input', index, sinks.get(sink, '@DEFAULT_SINK@'))
            except RuntimeError:
                pass        # ended meanwhile
        self._moved = {}
        if self._module is not None:
            try:
                _pactl('unload-module', self._module)
            except RuntimeError as e:
                log.warning(f'[apps] {e}')
            log.info(f'[apps] {self.program} no longer heard in the mixer')
        self._module, self.program = None, None

    def _follow(self):
        """The program's streams not on the call sink yet (a new call's) moved onto it."""
        if self.program is None:
            return
        try:
            sinks = {fields[1]: fields[0] for fields in (line.split('\t') for line in _pactl('list', 'short', 'sinks').splitlines()) if len(fields) > 1}
        except RuntimeError:
            return
        target = sinks.get(SINK)
        if target is None:
            return
        for index, sink, program in self._streams():
            if program == self.program and sink != target:
                try:
                    _pactl('move-sink-input', index, SINK)
                    self._moved[index] = sink
                    log.info(f'[apps] {program}: stream {index} moved into the mixer')
                except RuntimeError as e:
                    log.warning(f'[apps] {e}')
