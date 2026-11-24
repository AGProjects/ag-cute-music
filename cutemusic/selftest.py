"""The standalone apps' self test, its part shared by macOS and Windows (macos/standalone.py, windows/standalone.py,
run by make-standalone before packaging): with the GStreamer the app ships (its wheels, their gi), not the one of a
development machine.

check(folder): lines to show and whether all went well:
- Gst.ElementFactory.make of an element not there gives None (the wheels' gi override raises: player.py's wrapper)
- a Player made as the app makes it, its own audio sink picked (each candidate tried: none may raise)
- a 3 s test song played to its end by a Player (its playbin, audio filter, meters) into a fakesink
"""

import os
import time

__all__ = ['check']


def check(folder):
    lines, ok = [], True
    from PyQt6.QtCore import QCoreApplication, QTimer
    from cutemusic import player as _player
    Gst = _player.Gst
    try:
        missing = Gst.ElementFactory.make('ag-cutemusic-no-such-element', None)
    except Exception as e:
        missing = e
    if missing is None:
        lines.append('a missing element: None')
    else:
        lines.append(f'FAILED: a missing element gives {missing!r}, not None')
        ok = False
    application = QCoreApplication.instance() or QCoreApplication([])
    forced = os.environ.pop('CUTEMUSIC_AUDIO_SINK', None)
    try:
        sink = _player.Player().pipeline.get_property('audio-sink')
        factory = sink.get_factory() if sink is not None else None
        lines.append(f'the audio sink picked: {factory.get_name() if factory else sink.get_name() if sink else "none"}')
    except Exception as e:
        lines.append(f'FAILED: a Player as the app makes it: {e!r}')
        ok = False
    played = _play(Gst, _player.Player, application, QTimer, folder)
    if forced is not None:
        os.environ['CUTEMUSIC_AUDIO_SINK'] = forced
    else:
        os.environ.pop('CUTEMUSIC_AUDIO_SINK', None)
    lines.append(played if played.startswith('played') else f'FAILED: {played}')
    ok = ok and played.startswith('played')
    return lines, ok


def _play(Gst, Player, application, QTimer, folder):
    for encoder, extension in (('lamemp3enc', 'mp3'), ('flacenc', 'flac'), ('wavenc', 'wav')):
        if Gst.ElementFactory.find(encoder) is not None:
            break
    else:
        return 'play: no encoder for a test song'
    song = os.path.join(folder, f'selftest-tone.{extension}')
    maker = Gst.parse_launch(f'audiotestsrc num-buffers=130 freq=440 ! audioconvert ! audioresample ! {encoder} '
                             f'! filesink location="{song.replace(os.sep, "/")}"')
    maker.set_state(Gst.State.PLAYING)
    message = maker.get_bus().timed_pop_filtered(30 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    maker.set_state(Gst.State.NULL)
    if message is None or message.type == Gst.MessageType.ERROR or not os.path.exists(song) or not os.path.getsize(song):
        return f'play: the test song ({encoder}) not made'
    os.environ['CUTEMUSIC_AUDIO_SINK'] = 'fakesink sync=false'
    player = Player()
    result = {}
    player.started.connect(lambda uri: result.setdefault('started', True))
    player.finished.connect(lambda: (result.setdefault('end', 'finished'), application.quit()))
    player.error.connect(lambda error: (result.setdefault('end', f'error: {error}'), application.quit()))
    QTimer.singleShot(30000, lambda: (result.setdefault('end', 'no end in 30 s'), application.quit()))
    begun = time.monotonic()
    player.play_file(song)
    application.exec()
    player.stop()
    try:
        os.remove(song)
    except OSError:
        pass
    if result.get('end') == 'finished':
        return f'played {extension} to its end in {time.monotonic() - begun:.1f} s (Player, fakesink)'
    return f'play {extension}: {result.get("end")}' + ('' if result.get('started') else ', never started')
