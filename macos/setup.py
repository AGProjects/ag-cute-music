"""py2app recipe for the macOS app bundle; run through make-app.sh (alias mode) or make-standalone.sh
(STANDALONE=1: the whole application in the bundle, APP_ARCH x86_64 or universal2), not directly."""
import os
import re

from setuptools import setup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, 'cutemusic', '__init__.py'), encoding='utf-8').read()
const = lambda name: re.search(rf"^{name} = '([^']*)'", src, re.M).group(1)
APP_NAME, APP_ID, VERSION = const('APP_NAME'), const('APP_ID'), const('__version__')
STANDALONE = os.environ.get('STANDALONE') == '1'

options = {
    'iconfile': os.environ['APP_ICNS'],
    'argv_emulation': False,
    'excludes': ['tkinter', 'PyQt5', 'PySide2', 'PySide6', 'numpy', 'matplotlib'],
    'plist': {
        'CFBundleName': APP_NAME,
        'CFBundleDisplayName': APP_NAME,
        'CFBundleIdentifier': f'com.ag-projects.{APP_ID}',
        'CFBundleShortVersionString': VERSION,
        'CFBundleVersion': VERSION,
        'LSMinimumSystemVersion': '13.0',
        'LSApplicationCategoryType': 'public.app-category.music',
        'NSHighResolutionCapable': True,
        # the mixer's MIC (macOS asks once; without it the app reads silence, or is stopped)
        # MUSIC's SOURCE: another app's sound (taps.py; macOS asks once, "System Audio Recording")
        'NSAudioCaptureUsageDescription': 'AG CuteMusic plays other apps\' sound (Spotify, a browser) through its mixer, and sends it in Blink\'s calls.',
        # MUSIC: what Spotify or Music plays, shown in the mixer (taps.now_playing; macOS asks once per app)
        'NSAppleEventsUsageDescription': 'AG CuteMusic shows in its mixer what Spotify or Music plays.',
        'NSMicrophoneUsageDescription': 'AG CuteMusic mixes your microphone with the music, for you to hear and for Blink\'s calls.',
    },
}
if STANDALONE:
    # GStreamer (its wheels: gi included) is copied in by make-standalone.sh as it is, not packed by py2app
    options['excludes'] += ['gi', 'cairo', 'gstreamer_libs', 'gstreamer_plugins', 'gstreamer_plugins_libs', 'gstreamer_python',
                            'gstreamer_plugins_restricted', 'gstreamer_plugins_gpl', 'gstreamer_plugins_gpl_restricted',
                            'gstreamer_cli', 'gstreamer_gtk', 'gstreamer_ext_runtime']
    options['packages'] = ['cutemusic', 'PyQt6', 'mutagen', 'certifi', 'objc', 'Foundation', 'AppKit', 'CoreFoundation', 'MediaPlayer', 'AVFoundation', 'zeroconf', 'ifaddr']
    # the standard library modules gi and the GStreamer wheels' setup import: py2app doesn't see them (excluded above)
    options['includes'] = ['asyncio', 'contextlib', 'fcntl', 'inspect', 'optparse', 'pkgutil', 'selectors', 'shutil', 'signal',
                           'socket', 'subprocess', 'sysconfig', 'tempfile', 'termios', 'threading', 'typing', 'weakref']
    if os.environ.get('APP_ARCH'):
        options['arch'] = os.environ['APP_ARCH']
    # which image its updates come from (updater.py: the appcast's ag:arch)
    options['plist']['AGCuteMusicBuild'] = os.environ.get('APP_ARCH') or 'universal2'

setup(
    name=APP_NAME,
    app=[os.path.join(ROOT, 'macos', 'standalone.py' if STANDALONE else f'{APP_ID}.py')],
    options={'py2app': options},
)
