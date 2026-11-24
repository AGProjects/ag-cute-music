"""The control channel between the Blinks and AG CuteMusic on Linux: D-Bus, the session bus (python3-sipsimple
docs/PodcastMixerLink.md, v2). The same data as the AG Podcast Mixer driver's properties on macOS (control.py):

    org.agprojects.PodcastMixer at /org/agprojects/PodcastMixer, owned by AG CuteMusic, interface
    org.agprojects.PodcastMixer:

    SetCalls(i pid, s json)   a Blink's calls, as on 'agbc' ({"pid", "calls", "redial"}), every 2 s and at once on a
                              change of call state; an empty json: that Blink is gone (on quit)
    GetCalls() -> s           {"<pid>": <json>, ...}, the Blinks heard from lately
    ShowMixer()               the window in front on its Mixer view (Blink's notice about the device)
    Request(s json)           signal: our request, as on 'agrq' ({"seq", "pid", "op": "hangup" with "id", or
                              "redial"}); the Blink it names acts on a seq it has not seen

An entry not set again for 10 s, or whose process is gone, is left out (a Blink that crashed). A Flatpak'd Blink needs
--talk-name=org.agprojects.PodcastMixer.

Needs PyQt6's QtDBus (in python3-pyqt6); without it or a session bus, no Blink is seen (the device works all the
same: the BLINK strip shows no calls, HANG UP and REDIAL do nothing).
"""

import json
import os
import time

from PyQt6.QtCore import QObject, pyqtSlot

from cutemusic.util import log

try:
    from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusConnection, QDBusMessage
except ImportError:
    QDBusAbstractAdaptor = None


__all__ = ['SERVICE', 'PATH', 'INTERFACE', 'start', 'stop', 'blinks', 'request']


SERVICE = 'org.agprojects.PodcastMixer'
PATH = '/org/agprojects/PodcastMixer'
INTERFACE = 'org.agprojects.PodcastMixer'
STALE = 10.0        # s: an entry not set again for this long is left out (the driver's rule)


class _Service(QObject):
    """The Blinks' entries, kept as set: {pid: (json text, when)}."""

    def __init__(self, changed=None, show_mixer=None):
        super().__init__()
        self.entries = {}
        self.changed = changed
        self.show_mixer = show_mixer

    def set_calls(self, pid, text):
        if pid <= 0:
            return
        if text:
            try:
                state = json.loads(text)
            except ValueError:
                log.warning(f'[bus] SetCalls from {pid}: not json')
                return
            if not isinstance(state, dict):
                return
            known = pid in self.entries
            self.entries[pid] = (state, time.monotonic())
            if not known:
                log.info(f'[bus] a Blink on the control channel (pid {pid})')
        elif self.entries.pop(pid, None) is not None:
            log.info(f'[bus] the Blink {pid} is gone from the control channel')
        if self.changed is not None:
            self.changed()

    def current(self):
        now = time.monotonic()
        for pid in [pid for pid, (state, when) in self.entries.items() if now - when > STALE or not _alive(pid)]:
            del self.entries[pid]
            log.info(f'[bus] the Blink {pid} no longer heard from: left out')
        return {pid: state for pid, (state, when) in self.entries.items()}


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


if QDBusAbstractAdaptor is not None:
    from PyQt6.QtCore import pyqtClassInfo

    @pyqtClassInfo('D-Bus Interface', INTERFACE)
    class _Adaptor(QDBusAbstractAdaptor):

        def __init__(self, service):
            super().__init__(service)
            self.service = service
            self.setAutoRelaySignals(False)

        @pyqtSlot(int, str)
        def SetCalls(self, pid, text):
            self.service.set_calls(int(pid), text)

        @pyqtSlot()
        def ShowMixer(self):
            log.info('[bus] Blink asked for the mixer')
            if self.service.show_mixer is not None:
                self.service.show_mixer()

        @pyqtSlot(result=str)
        def GetCalls(self):
            return json.dumps({str(pid): state for pid, state in self.service.current().items()})


_service = None


def start(changed=None, show_mixer=None):
    """The service on the session bus; changed() whenever a Blink sets its calls, show_mixer() when one asks for the
    mixer. True when it is there."""
    global _service
    if _service is not None:
        _service.changed, _service.show_mixer = changed, show_mixer
        return True
    if QDBusAbstractAdaptor is None:
        log.info('[bus] no QtDBus: no Blink will be seen')
        return False
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        log.info('[bus] no session bus: no Blink will be seen')
        return False
    service = _Service(changed, show_mixer)
    service.adaptor = _Adaptor(service)
    if not bus.registerObject(PATH, service):
        log.warning(f'[bus] cannot register {PATH}')
        return False
    if not bus.registerService(SERVICE):
        bus.unregisterObject(PATH)
        log.warning(f'[bus] cannot own {SERVICE} (another AG CuteMusic?): {bus.lastError().message()}')
        return False
    _service = service
    log.info(f'[bus] {SERVICE} on the session bus: the Blinks\' calls, HANG UP and REDIAL')
    return True


def stop():
    global _service
    if _service is None:
        return
    bus = QDBusConnection.sessionBus()
    bus.unregisterService(SERVICE)
    bus.unregisterObject(PATH)
    _service = None


def blinks():
    """{pid: state} of the Blinks on the bus, {} without the service."""
    return _service.current() if _service is not None else {}


def request(text):
    """Our request (Request signal); True when sent."""
    if _service is None:
        return False
    message = QDBusMessage.createSignal(PATH, INTERFACE, 'Request')
    message.setArguments([text])
    return QDBusConnection.sessionBus().send(message)
