"""The Windows app's script (windows/make-standalone.ps1, PyInstaller): AG CuteMusic from its own folder.

GStreamer comes from its Python wheels (pip install gstreamer-bundle), copied next to the executable by
make-standalone.ps1 (<app>\\gstreamer), not packed by PyInstaller: their DLLs find each other by relative paths.
Their environment (plugins, typelibs, gi, PYGI_DLL_DIRS for the DLLs) is set up here, before anything imports gi, as
macos/standalone.py does in the Mac app.

AG_CUTEMUSIC_SELFTEST=<file>: GStreamer's version and the elements playing needs written to <file>, then exit
(make-standalone.ps1; a windowed program has no console to print to).
"""

import os
import sys

if sys.stdout is None or sys.stderr is None:        # windowed: no console, Python's streams are None
    _null = open(os.devnull, 'w')
    sys.stdout = sys.stdout or _null
    sys.stderr = sys.stderr or _null

_gstreamer = os.path.join(os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__)),
                          'gstreamer')
if os.path.isdir(_gstreamer):
    sys.path.insert(0, _gstreamer)
    import gstreamer_libs
    gstreamer_libs.setup_python_environment()       # GST_PLUGIN_PATH, GI_TYPELIB_PATH, PYGI_DLL_DIRS, gi on sys.path



def _selftest_play(Gst, folder):
    """A song played to its end by the app's own Player (its playbin, audio filter, meters), into a fakesink (a build
    machine has no sound card): a 3 s tone, encoded by the first encoder there of mp3, FLAC and WAV."""
    import time
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
    if message is None or message.type == Gst.MessageType.ERROR or not os.path.getsize(song):
        return f'play: the test song ({encoder}) not made'
    from PyQt6.QtCore import QCoreApplication, QTimer
    from cutemusic.player import Player
    application = QCoreApplication.instance() or QCoreApplication([])
    # the sink the app picks itself (each candidate of AUDIO_SINKS made in turn: one not there must not raise)
    sink = Player().pipeline.get_property('audio-sink')
    chosen = sink.get_factory().get_name() if sink is not None and sink.get_factory() else 'none'
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
    if result.get('end') == 'finished':
        return f'played {extension} to its end in {time.monotonic() - begun:.1f} s (Player, fakesink; its own sink: {chosen})'
    return f'play {extension}: {result.get("end")}' + ('' if result.get('started') else ', never started')


_selftest = os.environ.get('AG_CUTEMUSIC_SELFTEST')
if _selftest:
    lines, failed = [], True
    try:
        import gi
        gi.require_version('Gst', '1.0')
        from gi.repository import Gst
        Gst.init([])
        needed = ('playbin', 'decodebin', 'audioconvert', 'audioresample', 'volume', 'level', 'spectrum',
                  'equalizer-3bands', 'audiopanorama', 'wasapi2sink')
        missing = [name for name in needed if Gst.ElementFactory.find(name) is None]
        decoders = [name for name in ('avdec_mp3', 'mpg123audiodec', 'flacdec', 'avdec_aac', 'vorbisdec', 'opusdec')
                    if Gst.ElementFactory.find(name) is not None]
        lines.append(f'{Gst.version_string()}; audio decoders: {", ".join(decoders) or "none"}'
                     + (f'; MISSING: {", ".join(missing)}' if missing else ''))
        from PyQt6.QtCore import QT_VERSION_STR
        lines.append(f'Qt {QT_VERSION_STR}, Python {sys.version.split()[0]}')
        import cutemusic.app      # every module the window needs imports on Windows
        import cutemusic.window
        lines.append('cutemusic: imports ok')
        played = _selftest_play(Gst, os.path.dirname(os.path.abspath(_selftest)))
        lines.append(played)
        failed = bool(missing or not decoders or not played.startswith('played'))
    except Exception as e:
        import traceback
        lines.append(f'FAILED: {e}\n{traceback.format_exc()}')
    with open(_selftest, 'w', encoding='utf-8') as file:
        file.write('\n'.join(lines) + '\n')
    sys.exit(1 if failed else 0)

from cutemusic.app import main

sys.exit(main())
