# PyInstaller spec of the Windows app (windows/make-standalone.ps1): a folder, "AG CuteMusic\AG CuteMusic.exe".
#
# GStreamer (its wheels, gi with them) is left out: make-standalone.ps1 copies its packages next to the
# executable as they are (<app>\gstreamer), and windows/standalone.py sets their environment up at start.

import ast
import importlib.util
import os
import re
import sys

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



def stdlib_imports(*packages):
    """The standard library modules the packages' sources import: those left out above are copied as they are, so
    PyInstaller never sees what they import (gi: optparse, asyncio...) and would not pack it. gi and cairo are inside
    gstreamer_python (Lib/site-packages), not importable before standalone.py sets it up: walking it finds them."""
    found = set()
    for package in packages:
        spec = importlib.util.find_spec(package)
        if spec is None or not spec.submodule_search_locations:
            continue
        for folder in spec.submodule_search_locations:
            for top, dirs, files in os.walk(folder):
                for name in files:
                    if not name.endswith('.py'):
                        continue
                    try:
                        with open(os.path.join(top, name), encoding='utf-8') as file:
                            tree = ast.parse(file.read())
                    except (OSError, SyntaxError, UnicodeDecodeError):
                        continue
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            names = [alias.name for alias in node.names]
                        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                            names = [node.module]
                        else:
                            continue
                        found.update(n for n in names if n.split('.')[0] in sys.stdlib_module_names)
    found -= {'tkinter', 'test', 'idlelib', 'turtledemo'}
    print(f'gi and GStreamer import {len(found)} standard library modules', file=sys.stderr)
    return sorted(found | {'optparse'})


analysis = Analysis(
    [os.path.join(ROOT, 'windows', 'standalone.py')],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, 'icons'), 'icons')],         # the window icon: cutemusic\..\icons
    # every module (the window imports many of them inside functions), and zeroconf's compiled ones
    hiddenimports=collect_submodules('cutemusic') + collect_submodules('zeroconf') + ['ifaddr']
                  + stdlib_imports('gi', 'cairo', 'gstreamer_libs', 'gstreamer_python'),
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
