"""The AG Podcast Mixer driver on macOS (macos/driver/, python3-sipsimple docs/PodcastMixerLink.md, v2): a CoreAudio
driver with two devices, "AG Podcast Mixer Feed" (hidden, an output: the mix is played into it, link.py's FeedOutput)
and "AG Podcast Mixer" (an input: Blink's microphone, any app's), shown only while the Feed plays.

The driver comes in the app (Contents/Resources/AGPodcastMixer.driver; from a checkout: build/driver/, built by
macos/driver/build.sh) and is installed from the Play menu into /Library/Audio/Plug-Ins/HAL, with the
administrator's password (osascript), coreaudiod restarted. An installed driver older than ours is offered an update,
once per version at start (offer_at_start()).
"""

import ctypes
import os
import plistlib
import shlex
import struct
import sys

from cutemusic import util
from cutemusic.util import log


__all__ = ['supported', 'bundled', 'installed_version', 'bundled_version', 'state', 'feed_device', 'install',
           'uninstall', 'INSTALLED', 'FEED_UID', 'INPUT_NAME']


NAME = 'AGPodcastMixer.driver'
INSTALLED = '/Library/Audio/Plug-Ins/HAL/' + NAME
FEED_UID = 'com.ag-projects.podcast-mixer.feed'
STREAM_FEED_UID = 'com.ag-projects.podcast-mixer.streaming-feed'      # (there while streaming)
INPUT_NAME = 'AG Podcast Mixer'


def supported():
    return sys.platform == 'darwin'


def bundled():
    """The driver we carry: in the app's Resources, else the checkout's build; None when there is none."""
    places = []
    if os.environ.get('RESOURCEPATH'):      # py2app
        places.append(os.path.join(os.environ['RESOURCEPATH'], NAME))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    places.append(os.path.join(root, 'build', 'driver', NAME))
    for path in places:
        if os.path.isfile(os.path.join(path, 'Contents', 'Info.plist')):
            return path
    return None


def _version(path):
    try:
        with open(os.path.join(path, 'Contents', 'Info.plist'), 'rb') as file:
            return str(plistlib.load(file).get('CFBundleVersion') or '')
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None


def _key(version):
    return tuple(int(part) if part.isdigit() else 0 for part in (version or '0').split('.'))


def installed_version():
    return _version(INSTALLED) if os.path.isdir(INSTALLED) else None


def bundled_version():
    path = bundled()
    return _version(path) if path else None


def state():
    """'unsupported' (not macOS), 'missing' (not installed, ours to install), 'older' (installed, ours newer),
    'installed', or 'unavailable' (not installed and we carry none)."""
    if not supported():
        return 'unsupported'
    have, ours = installed_version(), bundled_version()
    if have is None:
        return 'missing' if ours else 'unavailable'
    if ours and _key(ours) > _key(have):
        return 'older'
    return 'installed'


# The Feed's device: CoreAudio's kAudioHardwarePropertyTranslateUIDToDevice, through ctypes

class _Address(ctypes.Structure):
    _fields_ = [('selector', ctypes.c_uint32), ('scope', ctypes.c_uint32), ('element', ctypes.c_uint32)]


def _fourcc(code):
    return struct.unpack('>I', code.encode('ascii'))[0]


_frameworks = None


def _load():
    global _frameworks
    if _frameworks is None:
        core_audio = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreAudio.framework/CoreAudio')
        core_foundation = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        core_foundation.CFStringCreateWithCString.restype = ctypes.c_void_p
        core_foundation.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
        core_audio.AudioObjectGetPropertyData.restype = ctypes.c_int32
        core_audio.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32,
                                                          ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        _frameworks = core_audio, core_foundation
    return _frameworks


def device_id(uid):
    """The AudioDeviceID of the device with that UID (hidden ones too), else None."""
    if not supported():
        return None
    try:
        core_audio, core_foundation = _load()
    except OSError as e:
        log.warning(f'[driver] cannot load CoreAudio: {e}')
        return None
    string = core_foundation.CFStringCreateWithCString(None, uid.encode('utf-8'), 0x08000100)     # UTF-8
    if not string:
        return None
    try:
        address = _Address(_fourcc('uidd'), _fourcc('glob'), 0)
        qualifier = ctypes.c_void_p(string)
        device = ctypes.c_uint32(0)
        size = ctypes.c_uint32(ctypes.sizeof(device))
        status = core_audio.AudioObjectGetPropertyData(1, ctypes.byref(address), ctypes.sizeof(qualifier),
                                                       ctypes.byref(qualifier), ctypes.byref(size), ctypes.byref(device))
    finally:
        core_foundation.CFRelease(string)
    return device.value if status == 0 and device.value else None


def feed_device():
    """The Feed's AudioDeviceID: the driver installed and loaded by coreaudiod; else None."""
    return device_id(FEED_UID) if os.path.isdir(INSTALLED) else None


# Installing, with the administrator's password

def _script(source):
    """The shell commands, as macos/driver/install.sh: copied beside, owned by root, swapped in, coreaudiod
    restarted. source None: removed."""
    dest = shlex.quote(INSTALLED)
    new = shlex.quote(INSTALLED + '.new')
    restart = 'killall coreaudiod'        # (launchd starts it again; launchctl kickstart is refused with SIP on)
    if source is None:
        return f'rm -rf {dest} && ({restart})'
    return ' && '.join([f'mkdir -p {shlex.quote(os.path.dirname(INSTALLED))}', f'rm -rf {new}',
                        f'ditto {shlex.quote(source)} {new}', f'chown -R root:wheel {new}', f'chmod -R u=rwX,go=rX {new}',
                        f'rm -rf {dest}', f'mv {new} {dest}', f'({restart})'])


def _run_as_admin(commands, prompt, done):
    """commands run as root after macOS asks for the administrator's password: done(error) on the main thread, error
    None when it worked, '' when the user cancelled.

    In this process (NSAppleScript, on the main thread: the window waits meanwhile), so the password dialog is AG
    CuteMusic's, with its name and icon; osascript (in a QProcess) only without PyObjC, its dialog then osascript's."""
    escape = lambda text: text.replace('\\', '\\\\').replace('"', '\\"')
    source = f'do shell script "{escape(commands)}" with prompt "{escape(prompt)}" with administrator privileges'
    from PyQt6.QtCore import QTimer
    try:
        from Foundation import NSAppleScript
    except ImportError:
        NSAppleScript = None
    if NSAppleScript is not None:
        def run():
            result, error = NSAppleScript.alloc().initWithSource_(source).executeAndReturnError_(None)
            if result is not None:
                done(None)
            elif error is not None and int(error.get('NSAppleScriptErrorNumber', 0)) == -128:      # User canceled.
                done('')
            else:
                done(str(error.get('NSAppleScriptErrorMessage', error)) if error is not None else 'failed')
        QTimer.singleShot(0, run)       # (the menu or the dialog that asked closed first)
        return

    from PyQt6.QtCore import QProcess
    process = QProcess()

    def finished(code, status):
        output = bytes(process.readAllStandardError()).decode('utf-8', 'replace').strip()
        process.deleteLater()
        _running.discard(process)
        if code == 0:
            done(None)
        elif '-128' in output:      # User canceled.
            done('')
        else:
            done(output or f'osascript exited with {code}')

    process.finished.connect(finished)
    _running.add(process)
    process.start('/usr/bin/osascript', ['-e', source])


_running = set()        # the QProcesses under way (kept from the garbage collector)
run_as_admin = _run_as_admin       # (updater.py's swap too)


def install(done):
    """Install (or update) the driver we carry; done(error) as _run_as_admin()'s."""
    source = bundled()
    if source is None:
        done('this AG CuteMusic carries no driver (macos/driver/build.sh builds it)')
        return
    version = _version(source)
    log.info(f'[driver] installing {source} {version} into {INSTALLED}')

    def finished(error):
        if error is None:
            log.info(f'[driver] installed {version}, coreaudiod restarted')
        elif error:
            log.warning(f'[driver] not installed: {error}')
        done(error)
    _run_as_admin(_script(source), f'Install the AG Podcast Mixer audio driver ({version}), for Blink.', finished)


def uninstall(done):
    log.info(f'[driver] removing {INSTALLED}')

    def finished(error):
        if error is None:
            log.info('[driver] removed, coreaudiod restarted')
        elif error:
            log.warning(f'[driver] not removed: {error}')
        done(error)
    _run_as_admin(_script(None), 'Remove the AG Podcast Mixer audio driver.', finished)


def offer_at_start():
    """Whether to offer the install (or the update) now: once per version we carry, asked or not."""
    current = state()
    if current not in ('missing', 'older'):
        return False
    settings = util.settings()
    ours = bundled_version()
    if settings.value('driver/offered') == ours:
        return False
    settings.setValue('driver/offered', ours)
    return True
