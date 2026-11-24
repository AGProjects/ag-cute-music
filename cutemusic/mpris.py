"""MPRIS on the session bus (Linux): org.mpris.MediaPlayer2.agcutemusic.

The desktop sends the keyboard's media keys (Play/Pause, Next, Previous, Stop) to the MPRIS player active, also
when the window is not: GNOME's media keys daemon, KDE, playerctl. It also shows the player in the desktop's media
control (GNOME's top bar and lock screen): the track, its cover, play/pause, next and previous, the position.

Needs PyQt6's QtDBus (in python3-pyqt6); without it or a session bus, nothing (the media keys still work while the
window has the focus: window.py).
"""

import os
import tempfile

from PyQt6.QtCore import QObject, QUrl, pyqtProperty, pyqtSlot

from cutemusic import APP_ID, APP_NAME
from cutemusic.library import read_cover
from cutemusic.util import log

try:
    from PyQt6.QtCore import QMetaType
    from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusArgument, QDBusConnection, QDBusMessage, QDBusObjectPath, QDBusVariant
except ImportError:
    QDBusAbstractAdaptor = None


__all__ = ['MPRIS']


SERVICE = 'org.mpris.MediaPlayer2.agcutemusic'
PATH = '/org/mpris/MediaPlayer2'
ROOT = 'org.mpris.MediaPlayer2'
PLAYER = 'org.mpris.MediaPlayer2.Player'
NO_TRACK = '/org/mpris/MediaPlayer2/TrackList/NoTrack'


def _int64(value):
    return QDBusArgument(int(value), QMetaType.Type.LongLong.value)


def _strings(values):
    return QDBusArgument(list(values), QMetaType.Type.QStringList.value)


if QDBusAbstractAdaptor is not None:
    from PyQt6.QtCore import pyqtClassInfo

    @pyqtClassInfo('D-Bus Interface', ROOT)
    class _Root(QDBusAbstractAdaptor):

        def __init__(self, mpris):
            super().__init__(mpris)
            self.mpris = mpris
            self.setAutoRelaySignals(False)

        @pyqtSlot()
        def Raise(self):
            if self.mpris.window.dock.isVisible():
                self.mpris.window.dock.raise_()
                self.mpris.window.dock.activateWindow()
                return
            self.mpris.window.showNormal()
            self.mpris.window.raise_()
            self.mpris.window.activateWindow()

        @pyqtSlot()
        def Quit(self):
            self.mpris.window.close()

        @pyqtProperty(bool)
        def CanQuit(self):
            return True

        @pyqtProperty(bool)
        def CanRaise(self):
            return True

        @pyqtProperty(bool)
        def HasTrackList(self):
            return False

        @pyqtProperty(str)
        def Identity(self):
            return APP_NAME

        @pyqtProperty(str)
        def DesktopEntry(self):
            return APP_ID

        @pyqtProperty('QStringList')
        def SupportedUriSchemes(self):
            return ['file']

        @pyqtProperty('QStringList')
        def SupportedMimeTypes(self):
            return ['audio/mpeg', 'audio/flac', 'audio/ogg', 'audio/mp4', 'audio/x-wav']

    @pyqtClassInfo('D-Bus Interface', PLAYER)
    class _Player(QDBusAbstractAdaptor):

        def __init__(self, mpris):
            super().__init__(mpris)
            self.mpris = mpris
            self.setAutoRelaySignals(False)

        @property
        def window(self):
            return self.mpris.window

        # Methods: the media keys come here

        @pyqtSlot()
        def Next(self):
            self.window.next()

        @pyqtSlot()
        def Previous(self):
            self.window.previous()

        @pyqtSlot()
        def Pause(self):
            self.window.player.pause()

        @pyqtSlot()
        def PlayPause(self):
            self.window.toggle()

        @pyqtSlot()
        def Stop(self):
            self.window.stop()

        @pyqtSlot()
        def Play(self):
            if self.window.player.state != 'playing':
                self.window.toggle()

        @pyqtSlot('qlonglong')
        def Seek(self, offset):
            self.window._seek(self.window._position_seconds + offset / 1e6)

        @pyqtSlot(QDBusObjectPath, 'qlonglong')
        def SetPosition(self, track, position):
            if track.path() == self.mpris.track_id():
                self.window._seek(position / 1e6)

        @pyqtSlot(str)
        def OpenUri(self, uri):
            self.window.open_paths([QUrl(uri).toLocalFile()])

        # Properties

        @pyqtProperty(str)
        def PlaybackStatus(self):
            return self.mpris.status()

        @pyqtProperty(str)
        def LoopStatus(self):
            return 'Playlist' if self.window.repeat_action.isChecked() else 'None'

        @LoopStatus.setter
        def LoopStatus(self, value):
            self.window.repeat_action.setChecked(value != 'None')

        @pyqtProperty(bool)
        def Shuffle(self):
            return self.window.shuffle_action.isChecked()

        @Shuffle.setter
        def Shuffle(self, value):
            self.window.shuffle_action.setChecked(value)

        @pyqtProperty(float)
        def Rate(self):
            return 1.0

        @Rate.setter
        def Rate(self, value):
            pass

        @pyqtProperty(float)
        def MinimumRate(self):
            return 1.0

        @pyqtProperty(float)
        def MaximumRate(self):
            return 1.0

        @pyqtProperty('QVariantMap')
        def Metadata(self):
            return self.mpris.metadata()

        @pyqtProperty(float)
        def Volume(self):
            return self.window.volume.value() / 100

        @Volume.setter
        def Volume(self, value):
            self.window.volume.setValue(round(max(0.0, min(1.0, value)) * 100))

        @pyqtProperty('qlonglong')
        def Position(self):
            return int(self.window._position_seconds * 1e6)

        @pyqtProperty(bool)
        def CanGoNext(self):
            return self.window.queue.peek(1) is not None

        @pyqtProperty(bool)
        def CanGoPrevious(self):
            return self.window.queue.current is not None

        @pyqtProperty(bool)
        def CanPlay(self):
            return True

        @pyqtProperty(bool)
        def CanPause(self):
            return True

        @pyqtProperty(bool)
        def CanSeek(self):
            return self.window.queue.current is not None

        @pyqtProperty(bool)
        def CanControl(self):
            return True


class MPRIS(QObject):
    """The window's player on the session bus. update() after a change of track, state, volume, shuffle or repeat;
    seeked() after a jump in the track."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.bus = None
        self._cover = None          # the cover written for the desktop (mpris:artUrl), removed with the next one
        self._sent = {}
        if QDBusAbstractAdaptor is None:
            log.info('[mpris] no QtDBus: the media keys work only while the window has the focus')
            return
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            log.info('[mpris] no session bus')
            return
        self._root, self._player = _Root(self), _Player(self)
        if not bus.registerObject(PATH, self, QDBusConnection.RegisterOption.ExportAdaptors):
            log.warning(f'[mpris] cannot register {PATH}: {bus.lastError().message()}')
            return
        if not bus.registerService(SERVICE):
            # another instance has the name: this one stays off the bus
            log.warning(f'[mpris] cannot take {SERVICE}: {bus.lastError().message()}')
            bus.unregisterObject(PATH)
            return
        self.bus = bus
        log.info(f'[mpris] {SERVICE}')

    def close(self):
        if self.bus is not None:
            self.bus.unregisterService(SERVICE)
            self.bus.unregisterObject(PATH)
            self.bus = None
        self._remove_cover()

    # What is sent

    def track_id(self):
        current = self.window.queue.current
        if current is None:
            return NO_TRACK
        return f'/org/agprojects/cutemusic/track/{abs(hash(current.path)) % 10 ** 12}'

    def status(self):
        return {'playing': 'Playing', 'paused': 'Paused'}.get(self.window.player.state, 'Stopped')

    def metadata(self):
        track = self.window.queue.current
        if track is None:
            return {'mpris:trackid': QDBusObjectPath(NO_TRACK)}
        data = {
            'mpris:trackid': QDBusObjectPath(self.track_id()),
            'xesam:title': track.display_title,
            'xesam:url': QUrl.fromLocalFile(track.path).toString(),
        }
        if track.get('artist') or track.get('albumartist'):
            data['xesam:artist'] = _strings([track.get('artist') or track.album_artist])
        if track.get('albumartist'):
            data['xesam:albumArtist'] = _strings([track['albumartist']])
        if track.get('album'):
            data['xesam:album'] = track['album']
        if track.get('genres'):
            data['xesam:genre'] = _strings(track['genres'])
        if track.get('track'):
            data['xesam:trackNumber'] = int(track['track'])
        if track.get('duration'):
            data['mpris:length'] = _int64(track['duration'] * 1e6)
        if self._cover_url:
            data['mpris:artUrl'] = self._cover_url
        return data

    _cover_url = None
    _cover_for = None

    def _update_cover(self):
        track = self.window.queue.current
        path = track.path if track is not None else None
        if path == self._cover_for:
            return
        self._cover_for = path
        self._remove_cover()
        self._cover_url = None
        if track is None:
            return
        data, image = read_cover(track)
        if image:
            self._cover_url = QUrl.fromLocalFile(image).toString()
        elif data:
            # a cover inside the file: written out for the desktop, which wants a URL
            try:
                handle, self._cover = tempfile.mkstemp(prefix='ag-cute-music-cover-', suffix='.jpg')
                with os.fdopen(handle, 'wb') as f:
                    f.write(data)
                self._cover_url = QUrl.fromLocalFile(self._cover).toString()
            except OSError as e:
                log.warning(f'[mpris] cannot write the cover: {e}')

    def _remove_cover(self):
        if self._cover is not None:
            try:
                os.remove(self._cover)
            except OSError:
                pass
            self._cover = None

    def update(self):
        """The properties changed since last sent, in one PropertiesChanged."""
        if self.bus is None:
            return
        self._update_cover()
        now = {
            'PlaybackStatus': self.status(),
            'LoopStatus': self._player.LoopStatus,
            'Shuffle': self._player.Shuffle,
            'Volume': self._player.Volume,
            'CanGoNext': self._player.CanGoNext,
            'CanGoPrevious': self._player.CanGoPrevious,
            'CanSeek': self._player.CanSeek,
            'Metadata': (self.track_id(), self._cover_url, (self.window.queue.current or {}).get('duration')),
        }
        changed = {key: value for key, value in now.items() if self._sent.get(key) != value}
        if not changed:
            return
        self._sent.update(changed)
        if 'Metadata' in changed:
            changed['Metadata'] = self.metadata()
        message = QDBusMessage.createSignal(PATH, 'org.freedesktop.DBus.Properties', 'PropertiesChanged')
        message.setArguments([PLAYER, changed, _strings([])])
        self.bus.send(message)

    def seeked(self):
        if self.bus is None:
            return
        message = QDBusMessage.createSignal(PATH, PLAYER, 'Seeked')
        message.setArguments([_int64(self.window._position_seconds * 1e6)])
        self.bus.send(message)
