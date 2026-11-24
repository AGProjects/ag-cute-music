"""Trim the standalone app (make-standalone.sh) down to what AG CuteMusic uses.

    prune_app.py <app>

GStreamer (Contents/Resources/gstreamer, its wheels as they are): the plugins in PLUGINS, the typelibs in TYPELIBS,
gi and the plugin scanner. Qt (PyQt6, packed whole by py2app): the PyQt6 modules in QT_MODULES and the Qt plugins in
QT_PLUGINS. Everything those load is kept, followed through their Mach-O load commands (macholib, from py2app); every
other library goes, and with it the duplicate copies the GStreamer wheels carry (libfoo.dylib next to libfoo.N.dylib).
"""

import glob
import os
import re
import shutil
import sys

from macholib import mach_o
from macholib.MachO import MachO

# playing music files: playbin with its parsers and decoders (libav, flac, mpg123, vorbis, opus), the level meter
# (the VU meter), spectrum (the spectrum analyser), equalizer and audiofx (the amplifier's tone and balance), and
# the macOS audio output; app (appsrc / appsink) is also the AG Podcast Mixer link's (cutemusic/link.py)
PLUGINS = {
    'coreelements', 'app', 'typefindfunctions', 'playback', 'autodetect', 'pbtypes',
    'audioparsers', 'id3demux', 'apetag', 'isomp4', 'ogg', 'wavparse', 'aiff', 'matroska',
    'libav', 'flac', 'mpg123', 'vorbis', 'opus', 'wavpack',
    'audioconvert', 'audioresample', 'volume', 'level', 'spectrum', 'equalizer', 'audiofx', 'osxaudio',
    'audiomixer',       # the mixer's inputs on one output (cutemusic/mic.py _Output)
    'audiotestsrc',     # the self test's tone (cutemusic/selftest.py), AG_CUTEMUSIC_MIC_TEST=source (mic.py)
}
TYPELIBS = {
    'GLib-2.0', 'GObject-2.0', 'GModule-2.0', 'GIRepository-2.0', 'Gst-1.0', 'GstBase-1.0', 'GstApp-1.0',
    'GstVideo-1.0', 'GstAudio-1.0', 'GstTag-1.0', 'GstPbutils-1.0', 'GstController-1.0',
}
GIO_MODULES = set()
GSTREAMER_DIRECTORIES = ['gstreamer_libs/bin', 'gstreamer_libs/lib/gst-validate-launcher',
                         'gstreamer_python/lib/python3.11/site-packages/pygtkcompat']

QT_MODULES = {'QtCore', 'QtGui', 'QtWidgets', 'QtSvg'}
QT_PLUGINS = {'platforms', 'imageformats', 'iconengines', 'styles'}
QT_PLUGINS_NOT = {'libqpdf.dylib'}      # PDF as an image format: brings QtPdf
PYQT_DIRECTORIES = ['bindings', 'lupdate', 'uic', 'Qt6/qml', 'Qt6/qsci', 'Qt6/metatypes', 'Qt6/mkspecs', 'Qt6/libexec']

LOADS = {mach_o.LC_LOAD_DYLIB, mach_o.LC_LOAD_WEAK_DYLIB, mach_o.LC_REEXPORT_DYLIB, mach_o.LC_LAZY_LOAD_DYLIB,
         mach_o.LC_LOAD_UPWARD_DYLIB}


def tree_size(root):
    return sum(os.path.getsize(os.path.join(directory, name)) for directory, _, names in os.walk(root) for name in names
               if not os.path.islink(os.path.join(directory, name)))


def binaries(root):
    """The Mach-O libraries under root: .dylib, .so and framework binaries (X.framework/Versions/A/X)."""
    found = []
    for directory, _, names in os.walk(root):
        for name in names:
            path = os.path.join(directory, name)
            if os.path.islink(path):
                continue
            if name.endswith(('.dylib', '.so')) or re.search(rf'/{re.escape(name)}\.framework/Versions/[^/]+$', directory):
                found.append(path)
    return found


def closure(roots, candidates, root):
    """roots and every candidate they load, directly or not (matched by file name: @rpath, @loader_path)."""
    by_name = {}
    for path in candidates:
        by_name.setdefault(os.path.basename(path), []).append(path)
    keep, todo = set(), list(roots)
    while todo:
        path = todo.pop()
        if path in keep:
            continue
        keep.add(path)
        try:
            macho = MachO(path)
        except Exception:
            continue
        for header in macho.headers:
            for command, _, data in header.commands:
                if command.cmd not in LOADS:
                    continue
                name = data.rstrip(b'\0').decode()
                if name.startswith(('/usr/', '/System/')):
                    continue
                found = by_name.get(os.path.basename(name))
                if found:
                    todo.extend(found)
                else:
                    print(f'  warning: {os.path.relpath(path, root)} loads {name}: not in the app')
    return keep


def remove(paths):
    for path in paths:
        framework = re.match(r'(.*?\.framework)/', path)
        if framework:
            shutil.rmtree(framework.group(1), ignore_errors=True)
        elif os.path.exists(path):
            os.remove(path)


def remove_empty_folders(root):
    for directory, folders, names in os.walk(root, topdown=False):
        if not os.listdir(directory):
            os.rmdir(directory)


def prune_gstreamer(root):
    for directory in GSTREAMER_DIRECTORIES:
        shutil.rmtree(os.path.join(root, directory), ignore_errors=True)
    candidates = binaries(root)
    by_name = {os.path.basename(path): path for path in candidates}
    roots = []
    for path in candidates:
        directory, name = os.path.split(path)
        if os.path.basename(directory) == 'gstreamer-1.0':
            if name.startswith('libgst') and name[len('libgst'):-len('.dylib')] in PLUGINS:
                roots.append(path)
        elif directory.endswith('/gio/modules'):
            if name in GIO_MODULES:
                roots.append(path)
        elif '/site-packages/' in path or '/libexec/' in path:      # gi, cairo; the plugin scanner
            roots.append(path)
    for path in glob.glob(os.path.join(root, '*', 'lib', 'girepository-1.0', '*.typelib')):
        if os.path.basename(path)[:-len('.typelib')] not in TYPELIBS:
            os.remove(path)
            continue
        for library in set(re.findall(rb'lib[\w.+-]+?\.dylib', open(path, 'rb').read())):
            roots += [candidate for candidate in candidates if os.path.basename(candidate) == library.decode()]
    keep = closure(roots, candidates, root)
    remove(path for path in candidates if path not in keep)
    remove_empty_folders(root)
    plugins = {os.path.basename(path)[6:-6] for path in keep if os.path.basename(os.path.dirname(path)) == 'gstreamer-1.0'}
    if PLUGINS - plugins:
        print(f'  GStreamer plugins NOT FOUND: {", ".join(sorted(PLUGINS - plugins))}')


def prune_qt(root):
    for directory in PYQT_DIRECTORIES:
        shutil.rmtree(os.path.join(root, directory), ignore_errors=True)
    for path in glob.glob(os.path.join(root, 'Qt6', 'plugins', '*')):
        if os.path.basename(path) not in QT_PLUGINS:
            shutil.rmtree(path)
    for path in glob.glob(os.path.join(root, 'Qt6', 'plugins', '*', '*')):
        if os.path.basename(path) in QT_PLUGINS_NOT:
            os.remove(path)
    for path in glob.glob(os.path.join(root, 'Qt6', 'translations', '*')):
        if not os.path.basename(path).startswith('qtbase_'):
            os.remove(path)
    for path in glob.glob(os.path.join(root, 'Qt*.abi3.so')) + glob.glob(os.path.join(root, 'Qt*.pyi')):
        if os.path.basename(path).split('.')[0] not in QT_MODULES:
            os.remove(path)
    candidates = binaries(root)
    roots = [path for path in candidates if os.path.dirname(path) == root or '/Qt6/plugins/' in path]
    keep = closure(roots, candidates, root)
    remove(path for path in candidates if path not in keep)
    remove_empty_folders(root)
    fix_framework_layout(root)


def fix_framework_layout(root):
    """Qt's wheels carry their frameworks flattened (wheels have no symlinks): X.framework/Versions/A/X with
    Resources/ next to Versions/. codesign then seals such a framework as a plain folder, its binary left out of
    the signature, and the hardened runtime kills the process when it loads it: back to the standard layout."""
    for framework in glob.glob(os.path.join(root, 'Qt6', 'lib', '*.framework')):
        versions = os.path.join(framework, 'Versions')
        if not os.path.isdir(versions) or os.path.islink(os.path.join(versions, 'Current')):
            continue
        version = sorted(os.listdir(versions))[0]
        resources = os.path.join(framework, 'Resources')
        if os.path.isdir(resources) and not os.path.islink(resources):
            shutil.rmtree(os.path.join(versions, version, 'Resources'), ignore_errors=True)
            os.rename(resources, os.path.join(versions, version, 'Resources'))
        shutil.rmtree(os.path.join(framework, '_CodeSignature'), ignore_errors=True)
        os.symlink(version, os.path.join(versions, 'Current'))
        for entry in os.listdir(os.path.join(versions, version)):
            link = os.path.join(framework, entry)
            if entry != '_CodeSignature' and not os.path.lexists(link):
                os.symlink(os.path.join('Versions', 'Current', entry), link)


def main(app):
    resources = os.path.join(os.path.abspath(app), 'Contents', 'Resources')
    gstreamer = os.path.join(resources, 'gstreamer')
    pyqt = glob.glob(os.path.join(resources, 'lib', 'python3.*', 'PyQt6'))
    for name, root, prune in [('GStreamer', gstreamer, prune_gstreamer)] + [('Qt', path, prune_qt) for path in pyqt]:
        if os.path.isdir(root):
            before = tree_size(root)
            prune(root)
            print(f'  {name}: {before // 2**20} MB -> {tree_size(root) // 2**20} MB')


if __name__ == '__main__':
    main(sys.argv[1])
