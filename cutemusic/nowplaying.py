"""macOS: the player in the system's Now Playing (MPRemoteCommandCenter, MPNowPlayingInfoCenter), what MPRIS is on
Linux (mpris.py, the same interface: update(), seeked(), close()).

The keyboard's media keys (F7 / F8 / F9, also on Bluetooth headphones) go to the Now Playing app, also when its
window is not active; Qt never gets them as key events. Control Center and the lock screen show the track, its cover
and the position, with play/pause, next, previous and a seek bar.

Needs PyObjC's MediaPlayer framework (pip install pyobjc-framework-MediaPlayer); without it, nothing.
"""

from PyQt6.QtCore import QObject

from cutemusic import util
from cutemusic.library import read_cover
from cutemusic.util import log

try:
    import AppKit
    import Foundation
    import MediaPlayer
except ImportError as e:
    MediaPlayer = None
    _import_error = str(e)


__all__ = ['NowPlaying']


class NowPlaying(QObject):
    """The window's player in macOS's Now Playing. update() after a change of track, state or position; seeked()
    after a jump in the track."""

    _cover_for = None       # the track whose cover was given (window.py clears it when one is found online)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.center = None
        self._sent = None
        self._artwork = None
        if MediaPlayer is None:
            log.info(f'[nowplaying] no PyObjC MediaPlayer ({_import_error}): no media keys')
            return
        commands = MediaPlayer.MPRemoteCommandCenter.sharedCommandCenter()
        self._targets = []
        for command, slot in ((commands.togglePlayPauseCommand(), window.toggle),
                              (commands.playCommand(), self._play),
                              (commands.pauseCommand(), self._pause),
                              (commands.stopCommand(), window.stop),
                              (commands.nextTrackCommand(), window.next),
                              (commands.previousTrackCommand(), window.previous)):
            command.setEnabled_(True)
            self._targets.append((command, command.addTargetWithHandler_(self._handler(slot))))
        command = commands.changePlaybackPositionCommand()
        command.setEnabled_(True)
        self._targets.append((command, command.addTargetWithHandler_(self._seek_handler)))
        for command in (commands.skipForwardCommand(), commands.skipBackwardCommand(), commands.seekForwardCommand(),
                        commands.seekBackwardCommand(), commands.changePlaybackRateCommand(), commands.ratingCommand()):
            command.setEnabled_(False)
        self.center = MediaPlayer.MPNowPlayingInfoCenter.defaultCenter()
        log.info('[nowplaying] media keys through Now Playing')

    @staticmethod
    def _handler(slot):
        def handle(event):
            util.call_in_gui_thread(slot)
            return MediaPlayer.MPRemoteCommandHandlerStatusSuccess
        return handle

    def _seek_handler(self, event):
        util.call_in_gui_thread(self.window._seek, float(event.positionTime()))
        return MediaPlayer.MPRemoteCommandHandlerStatusSuccess

    def _play(self):
        if self.window.player.state != 'playing':
            self.window.toggle()

    def _pause(self):
        if self.window.player.state == 'playing':
            self.window.player.pause()

    def close(self):
        if self.center is None:
            return
        for command, target in self._targets:
            command.removeTarget_(target)
        self._targets = []
        self.center.setNowPlayingInfo_(None)
        self.center.setPlaybackState_(MediaPlayer.MPNowPlayingPlaybackStateStopped)
        self.center = None

    # What is shown

    def _cover(self, track):
        if track.path == self._cover_for:
            return self._artwork
        self._cover_for = track.path
        self._artwork = None
        data, path = read_cover(track)
        image = None
        if data:
            image = AppKit.NSImage.alloc().initWithData_(Foundation.NSData.dataWithBytes_length_(data, len(data)))
        elif path:
            image = AppKit.NSImage.alloc().initWithContentsOfFile_(path)
        if image is not None:
            self._artwork = MediaPlayer.MPMediaItemArtwork.alloc().initWithBoundsSize_requestHandler_(
                image.size(), lambda size: image)
        return self._artwork

    def update(self):
        if self.center is None:
            return
        track = self.window.queue.current
        state = self.window.player.state if track is not None else 'stopped'
        position = self.window._position_seconds
        # the system moves the position on by itself while playing: sent again only when something else changed
        if (track.path if track else None, state, self._cover_for) == self._sent:
            return
        self._send(track, state, position)

    def seeked(self):
        if self.center is not None:
            self._send(self.window.queue.current, self.window.player.state, self.window._position_seconds)

    def _send(self, track, state, position):
        M = MediaPlayer
        self._sent = (None, state, self._cover_for)       # what update() compares with
        if track is None:
            self.center.setNowPlayingInfo_(None)
            self.center.setPlaybackState_(M.MPNowPlayingPlaybackStateStopped)
            return
        info = {
            M.MPMediaItemPropertyTitle: track.display_title,
            M.MPNowPlayingInfoPropertyElapsedPlaybackTime: float(position),
            M.MPNowPlayingInfoPropertyPlaybackRate: 1.0 if state == 'playing' else 0.0,
            M.MPNowPlayingInfoPropertyMediaType: M.MPNowPlayingInfoMediaTypeAudio,
        }
        if track.get('artist') or track.get('albumartist'):
            info[M.MPMediaItemPropertyArtist] = track.get('artist') or track.album_artist
        if track.get('album'):
            info[M.MPMediaItemPropertyAlbumTitle] = track['album']
        if track.get('albumartist'):
            info[M.MPMediaItemPropertyAlbumArtist] = track['albumartist']
        if track.get('genres'):
            info[M.MPMediaItemPropertyGenre] = track['genres'][0]
        if track.get('track'):
            info[M.MPMediaItemPropertyAlbumTrackNumber] = int(track['track'])
        if track.get('duration'):
            info[M.MPMediaItemPropertyPlaybackDuration] = float(track['duration'])
        artwork = self._cover(track)
        if artwork is not None:
            info[M.MPMediaItemPropertyArtwork] = artwork
        self._sent = (track.path, state, self._cover_for)
        self.center.setNowPlayingInfo_(info)
        self.center.setPlaybackState_({'playing': M.MPNowPlayingPlaybackStatePlaying,
                                       'paused': M.MPNowPlayingPlaybackStatePaused}.get(state, M.MPNowPlayingPlaybackStateStopped))
