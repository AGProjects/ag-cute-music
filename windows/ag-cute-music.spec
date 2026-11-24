# PyInstaller spec of the Windows app (windows/make-standalone.ps1): a folder, "AG CuteMusic\AG CuteMusic.exe".
#
# GStreamer (its wheels, gi with them) is left out: make-standalone.ps1 copies its packages next to the
# executable as they are (<app>\gstreamer), and windows/standalone.py sets their environment up at start.

import os
import re

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))
with open(os.path.join(ROOT, 'cutemusic', '__init__.py'), encoding='utf-8') as file:
    source = file.read()
APP_NAME = re.search(r"^APP_NAME = '(.*)'", source, re.M).group(1)
ICON = os.environ.get('APP_ICO', os.path.join(ROOT, 'build', 'app.ico'))

GSTREAMER = ['gi', 'cairo', 'gstreamer_bundle', 'gstreamer_libs', 'gstreamer_plugins', 'gstreamer_plugins_libs',
             'gstreamer_python', 'gstreamer_plugins_restricted', 'gstreamer_plugins_gpl',
             'gstreamer_plugins_gpl_restricted', 'gstreamer_cli', 'gstreamer_gtk', 'gstreamer_ext_runtime']
# macOS only (cutemusic/nowplaying.py, dockicon.py's Dock), and what the app does not use
EXCLUDES = GSTREAMER + ['AppKit', 'Foundation', 'MediaPlayer', 'objc', 'tkinter', 'numpy', 'PyQt5', 'PySide2', 'PySide6']

analysis = Analysis(
    [os.path.join(ROOT, 'windows', 'standalone.py')],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, 'icons'), 'icons')],         # the window icon: cutemusic\..\icons
    # every module (the window imports many of them inside functions), and zeroconf's compiled ones
    hiddenimports=collect_submodules('cutemusic') + collect_submodules('zeroconf') + ['ifaddr'],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    icon=ICON if os.path.exists(ICON) else None,
    console=False,
    upx=False,
)
coll = COLLECT(exe, analysis.binaries, analysis.datas, name=APP_NAME, upx=False)
