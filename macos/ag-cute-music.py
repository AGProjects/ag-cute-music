"""The app bundle's script (macos/setup.py): AG CuteMusic from the checkout it was built from."""

import os
import sys

# Homebrew's GObject typelibs name their libraries without a folder (libgobject-2.0.0.dylib): dlopen finds them only
# through DYLD_FALLBACK_LIBRARY_PATH, which has /usr/local/lib but not /opt/homebrew/lib, and which dyld reads at
# start only. So the app starts itself again with it set (once: AG_CUTEMUSIC_DYLD), before gi is imported.
if sys.platform == 'darwin' and not os.environ.get('AG_CUTEMUSIC_DYLD'):
    brew = [folder for folder in ('/opt/homebrew/lib', '/usr/local/lib') if os.path.isdir(folder)]
    resources = os.environ.get('RESOURCEPATH')
    if brew and resources:
        contents = os.path.dirname(resources)
        import plistlib
        try:
            with open(os.path.join(contents, 'Info.plist'), 'rb') as f:
                name = plistlib.load(f).get('CFBundleExecutable')
        except (OSError, ValueError):
            name = None
        executable = os.path.join(contents, 'MacOS', name) if name else None
        if executable and os.access(executable, os.X_OK):
            fallback = ':'.join(brew + [folder for folder in (os.path.expanduser('~/lib'), '/usr/local/lib', '/usr/lib')
                                        if folder not in brew])
            environment = dict(os.environ, AG_CUTEMUSIC_DYLD='1', DYLD_FALLBACK_LIBRARY_PATH=fallback)
            os.execve(executable, [executable] + sys.argv[1:], environment)

# alias mode: this file is a symlink into the checkout; macos/.. holds the cutemusic package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from cutemusic.app import main

sys.exit(main())
