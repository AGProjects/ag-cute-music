"""The standalone app bundle's script (macos/make-standalone.sh): AG CuteMusic from the bundle itself.

GStreamer comes from its Python wheels (pip install gstreamer-bundle), copied into the bundle by make-standalone.sh
(Contents/Resources/gstreamer), not packed by py2app: their libraries find each other by relative paths. Their
environment (plugins, typelibs, gi) is set up here, before anything imports gi, as their .pth file does in a venv.

Only the bundle's own Python packages are used: sys.path is cut down to what is inside the bundle, before anything
is imported. Else a PyQt6 installed by pip --user (~/Library/Python/3.x) comes first and its Qt libraries are
loaded next to the bundle's: a crash, or a symbol not found at the first PyQt6 module the bundle has and it lacks.

AG_CUTEMUSIC_SELFTEST=1 (make-standalone.sh, which makes no image of an app failing it): GStreamer's version and the
elements playing needs; every cutemusic module imported (each Qt module the app uses loaded, as at start);
cutemusic/selftest.py (a missing element gives None, the app's own audio sink, a test song played to its end); no
library loaded from outside the bundle and the system; then exit, 0 when all is well.
"""

import os
import sys

_contents = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # <app>/Contents
os.environ['AG_CUTEMUSIC_BUNDLE'] = os.path.dirname(_contents)     # the standalone app: updater.py updates it
sys.path[:] = [path for path in sys.path if os.path.abspath(path).startswith(_contents + os.sep)]

_gstreamer = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gstreamer')
if os.path.isdir(_gstreamer):
    sys.path.insert(0, _gstreamer)
    # GStreamer's variables as another process left them (the updater's shell, a Terminal that ran an older one):
    # gstreamer_libs prepends to them, and joined so its "gi on sys.path" is one path that does not exist ("No
    # module named 'gi'"). Ours only
    for _name in list(os.environ):
        if _name.startswith(('GST_', 'GI_', 'GIO_')) or _name in ('PYTHONPATH', 'LD_LIBRARY_PATH'):
            del os.environ[_name]
    import gstreamer_libs
    gstreamer_libs.setup_python_environment()       # GST_PLUGIN_PATH, GI_TYPELIB_PATH, the registry, gi on sys.path
    # gi's own folder (gstreamer_python's site-packages), whatever the environment said
    for _root, _dirs, _files in os.walk(os.path.join(_gstreamer, 'gstreamer_python')):
        if 'gi' in _dirs:
            if _root not in sys.path:
                sys.path.append(_root)
            break

if os.environ.get('AG_CUTEMUSIC_SELFTEST'):
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init([])
    missing = [name for name in ('playbin', 'decodebin', 'audioconvert', 'audioresample', 'volume', 'level', 'osxaudiosink', 'audiomixer') if Gst.ElementFactory.find(name) is None]
    decoders = [name for name in ('avdec_mp3', 'mpg123audiodec', 'flacdec', 'avdec_aac', 'vorbisdec', 'opusdec') if Gst.ElementFactory.find(name) is not None]
    print(f'{Gst.version_string()}; audio decoders: {", ".join(decoders) or "none"}'
          + (f'; MISSING: {", ".join(missing)}' if missing else ''))
    from PyQt6.QtCore import QT_VERSION_STR
    print(f'Qt {QT_VERSION_STR}, Python {sys.version.split()[0]}, {os.uname().machine}')
    import importlib
    import pkgutil
    import traceback
    import cutemusic
    failed = []
    for module in sorted(info.name for info in pkgutil.iter_modules(cutemusic.__path__)):
        try:
            importlib.import_module(f'cutemusic.{module}')
        except Exception:
            failed.append(module)
            print(f'cannot import cutemusic.{module}:')
            traceback.print_exc()
    checked, played = [], False
    if not failed:
        import tempfile
        from cutemusic import selftest      # a missing element, the app's own sink, a test song played to its end
        checked, played = selftest.check(tempfile.mkdtemp(prefix='cutemusic-selftest-'))
        for line in checked:
            print(line)
    import ctypes
    dyld = ctypes.CDLL(None)
    dyld._dyld_image_count.restype = ctypes.c_uint32
    dyld._dyld_get_image_name.restype = ctypes.c_char_p
    dyld._dyld_get_image_name.argtypes = [ctypes.c_uint32]
    app = os.path.dirname(_contents) + os.sep
    images = [dyld._dyld_get_image_name(i).decode('utf-8', 'replace') for i in range(dyld._dyld_image_count())]
    outside = [image for image in images if not image.startswith((app, '/System/', '/usr/lib/', '/usr/libexec/', '/Library/Apple/'))]
    for image in outside:
        print(f'loaded from outside the app: {image}')
    print(f'{len(images)} libraries loaded, {len(outside)} from outside the app; cutemusic modules not importable: '
          f'{", ".join(failed) or "none"}')
    sys.exit(1 if missing or not decoders or failed or outside or not played else 0)

from cutemusic.app import main

sys.exit(main())
