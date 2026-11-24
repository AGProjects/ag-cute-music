"""Other apps' sound (Spotify, a browser...) for the mixer's MUSIC strip: macOS process taps (macOS 14.2+;
python3-sipsimple docs/PodcastMixerLink.md, v2, "Capturing programs"), through PyObjC and ctypes, no extension.

- apps(): the running apps that have used sound (Core Audio's process objects, a browser's helpers counted as the
  browser), by name.
- Capture(app, mute): a tap on the app's processes (CATapDescription, a stereo mixdown) and a private aggregate
  device with the tap as its input: device_id, read by GStreamer's osxaudiosrc (mic.py). mute: the app silent on its
  own output while captured (CATapMuted: heard only through the mixer, with its fader); else it plays there
  as well (an amplifier on that output), the mixer only sending it on. close(): both destroyed.

macOS asks once for "System Audio Recording" (Info.plist NSAudioCaptureUsageDescription), when the tap is first
read. An app's processes that start using sound after the capture began are not in it: picked again, they are.
"""

import ctypes
import os
import platform
import re
import struct
import sys
import uuid

from cutemusic.util import log


__all__ = ['available', 'apps', 'Capture', 'builtin_microphone', 'now_playing', 'remote', 'set_volume', 'input_kinds', 'output_kinds', 'PLAYERS']


class _Address(ctypes.Structure):
    _fields_ = [('selector', ctypes.c_uint32), ('scope', ctypes.c_uint32), ('element', ctypes.c_uint32)]


def _fourcc(code):
    return struct.unpack('>I', code.encode('ascii'))[0]


SYSTEM = 1          # kAudioObjectSystemObject
GLOBAL = _fourcc('glob')
OUTPUT_SCOPE = _fourcc('outp')
PROCESSES = _fourcc('prs#')             # kAudioHardwarePropertyProcessObjectList
PROCESS_PID = _fourcc('ppid')           # kAudioProcessPropertyPID
PROCESS_BUNDLE = _fourcc('pbid')        # kAudioProcessPropertyBundleID
PROCESS_PLAYING = _fourcc('piro')       # kAudioProcessPropertyIsRunningOutput
DEFAULT_SYSTEM_OUTPUT = _fourcc('sOut')     # kAudioHardwarePropertyDefaultSystemOutputDevice
DEVICE_UID = _fourcc('uid ')            # kAudioDevicePropertyDeviceUID
MUTED, MUTED_WHEN_TAPPED, UNMUTED = 1, 2, 0     # CATapMuteBehavior

_lib = None


def _load():
    """CoreAudio (its C functions, and CATapDescription once loaded), CoreFoundation."""
    global _lib
    if _lib is None:
        core_audio = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreAudio.framework/CoreAudio')
        core_foundation = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        u32p = ctypes.POINTER(ctypes.c_uint32)
        core_audio.AudioObjectGetPropertyDataSize.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32, ctypes.c_void_p, u32p]
        core_audio.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32, ctypes.c_void_p, u32p, ctypes.c_void_p]
        core_audio.AudioHardwareCreateProcessTap.argtypes = [ctypes.c_void_p, u32p]
        core_audio.AudioHardwareDestroyProcessTap.argtypes = [ctypes.c_uint32]
        core_audio.AudioHardwareCreateAggregateDevice.argtypes = [ctypes.c_void_p, u32p]
        core_audio.AudioHardwareDestroyAggregateDevice.argtypes = [ctypes.c_uint32]
        for name in ('AudioObjectGetPropertyDataSize', 'AudioObjectGetPropertyData', 'AudioHardwareCreateProcessTap',
                     'AudioHardwareDestroyProcessTap', 'AudioHardwareCreateAggregateDevice', 'AudioHardwareDestroyAggregateDevice'):
            getattr(core_audio, name).restype = ctypes.c_int32
        core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
        _lib = core_audio, core_foundation
    return _lib


def _version(text):
    return tuple(int(part) for part in (text or '0').split('.') if part.isdigit())


def available():
    """Process taps here: macOS 14.2+, PyObjC, CATapDescription."""
    if sys.platform != 'darwin' or _version(platform.mac_ver()[0]) < (14, 2):
        return False
    try:
        _load()
        import objc
        objc.lookUpClass('CATapDescription')
        return True
    except Exception:
        return False


def _data(object_id, selector, size=None, scope=GLOBAL):
    """A property's raw bytes, None when it cannot be read."""
    core_audio, core_foundation = _load()
    address = _Address(selector, scope, 0)
    if size is None:
        needed = ctypes.c_uint32(0)
        if core_audio.AudioObjectGetPropertyDataSize(object_id, ctypes.byref(address), 0, None, ctypes.byref(needed)) != 0:
            return None
        size = needed.value
    buffer = ctypes.create_string_buffer(max(size, 1))
    length = ctypes.c_uint32(size)
    if core_audio.AudioObjectGetPropertyData(object_id, ctypes.byref(address), 0, None, ctypes.byref(length), buffer) != 0:
        return None
    return buffer.raw[:length.value]


def _u32(object_id, selector, scope=GLOBAL):
    data = _data(object_id, selector, 4, scope)
    return struct.unpack('=I', data)[0] if data and len(data) == 4 else None


def _string(object_id, selector):
    """A CFString property, as a str (the reference released)."""
    import objc
    data = _data(object_id, selector, ctypes.sizeof(ctypes.c_void_p))
    if not data:
        return None
    pointer = ctypes.c_void_p.from_buffer_copy(data).value       # (struct's 'P' is native mode only)
    if not pointer:
        return None
    try:
        return str(objc.objc_object(c_void_p=pointer))
    finally:
        _load()[1].CFRelease(pointer)


def _processes():
    """[(process object, pid, bundle id, playing)] of the processes Core Audio knows (they have used sound)."""
    data = _data(SYSTEM, PROCESSES) or b''
    result = []
    for (object_id,) in struct.iter_unpack('=I', data):
        pid = _u32(object_id, PROCESS_PID)
        result.append((object_id, pid, _string(object_id, PROCESS_BUNDLE) or '', bool(_u32(object_id, PROCESS_PLAYING))))
    return result


def _own():
    from Foundation import NSBundle
    return str(NSBundle.mainBundle().bundleIdentifier() or ''), os.getpid()


# never MUSIC's source: AG CuteMusic itself (its sound is the mixer's), Blink (its call is the BLINK strip, and the
# mix goes into its call: it would come back), Blink Lite and SIP2SIP (Blink's other builds), helpers included; the
# terminals AG CuteMusic is run from in development (bin/ag-cute-music: macOS counts its sound as the terminal's)
EXCLUDED = ('com.ag-projects.ag-cute-music', 'com.agprojects.Blink', 'com.agprojects.BlinkLite', 'com.agprojects.SIP2SIP',
            'com.apple.Terminal', 'com.googlecode.iterm2')


def _excluded(bundle_id):
    return any(bundle_id == excluded or bundle_id.startswith(excluded + '.') for excluded in EXCLUDED)


class App:
    def __init__(self, bundle_id, name):
        self.bundle_id, self.name = bundle_id, name
        self.objects = []           # its process objects (itself, its helpers)
        self.playing = False


def apps():
    """[App] of the regular apps running that have processes Core Audio knows, by name; not us, nor Blink (EXCLUDED)."""
    from AppKit import NSWorkspace
    own_bundle, own_pid = _own()
    running = []
    for application in NSWorkspace.sharedWorkspace().runningApplications():
        bundle_id = str(application.bundleIdentifier() or '')
        if application.activationPolicy() != 0 or not bundle_id or bundle_id == own_bundle or _excluded(bundle_id):
            continue
        running.append((bundle_id, str(application.localizedName() or bundle_id), application.processIdentifier()))
    found = {}
    for object_id, pid, bundle_id, playing in _processes():
        if pid == own_pid:
            continue
        # the app itself, or one of its helpers (com.google.Chrome.helper: Chrome's)
        owner = next(((app_id, name) for app_id, name, app_pid in running
                      if pid == app_pid or bundle_id == app_id or (bundle_id and bundle_id.startswith(app_id + '.'))), None)
        if owner is None:
            continue
        app = found.setdefault(owner[0], App(*owner))
        app.objects.append(object_id)
        app.playing = app.playing or playing
    return sorted(found.values(), key=lambda app: app.name.lower())        # (stable: the selector's positions do not jump)


DEVICES = _fourcc('dev#')               # kAudioHardwarePropertyDevices
TRANSPORT = _fourcc('tran')             # kAudioDevicePropertyTransportType
BUILT_IN = _fourcc('bltn')              # kAudioDeviceTransportTypeBuiltIn
STREAMS = _fourcc('stm#')               # kAudioDevicePropertyStreams
NAME = _fourcc('lnam')                  # kAudioObjectPropertyName
INPUT_SCOPE = _fourcc('inpt')


def builtin_microphone():
    """The Mac's built-in microphone's name (as GStreamer lists it: "MacBook Pro Microphone"), None when there is none
    (a Mac mini, the lid closed)."""
    if sys.platform != 'darwin':
        return None
    try:
        for (device,) in struct.iter_unpack('=I', _data(SYSTEM, DEVICES) or b''):
            if _u32(device, TRANSPORT) == BUILT_IN and _data(device, STREAMS, scope=INPUT_SCOPE):
                return _string(device, NAME)
    except Exception as e:
        log.warning(f'[taps] cannot find the built-in microphone: {e}')
    return None


def _default_output_uid():
    device = _u32(SYSTEM, DEFAULT_SYSTEM_OUTPUT)
    return _string(device, DEVICE_UID) if device else None


class Capture:
    """An app's sound as an input device (device_id), while open."""

    def __init__(self, bundle_id, mute=True):
        import objc
        from Foundation import NSArray, NSDictionary, NSNumber
        app = next((app for app in apps() if app.bundle_id == bundle_id), None)
        if app is None:
            raise RuntimeError(f'{bundle_id} plays no sound (start it, play something)')
        self.name, self.bundle_id = app.name, bundle_id
        self.mute = mute            # silent on its own output while captured
        self.tap = self.device_id = None
        core_audio = _load()[0]
        description = objc.lookUpClass('CATapDescription').alloc().initStereoMixdownOfProcesses_(
            NSArray.arrayWithArray_([NSNumber.numberWithUnsignedInt_(object_id) for object_id in app.objects]))
        description.setName_(f'AG CuteMusic: {app.name}')
        description.setPrivate_(True)
        # muted for as long as the tap exists (not only while it is read: MUTED_WHEN_TAPPED let the app be heard
        # alongside the mixer's copy, a few tens of ms apart: a smeared, "distorted" sound)
        description.setMuteBehavior_(MUTED if mute else UNMUTED)
        tap = ctypes.c_uint32(0)
        status = core_audio.AudioHardwareCreateProcessTap(objc.pyobjc_id(description), ctypes.byref(tap))
        if status != 0 or not tap.value:
            raise RuntimeError(f'cannot tap {app.name} (Core Audio error {status})')
        self.tap = tap.value
        settings = {
            'name': f'AG CuteMusic Tap {app.name}',        # (player.OWN_DEVICES: never an output nor a mic)
            'uid': f'com.ag-projects.ag-cute-music.tap.{uuid.uuid4()}',
            'private': True,
            'stacked': False,
            'tapautostart': True,
            # no drift compensation: the app plays on the system's output, the aggregate's clock (master), so the tap
            # does not drift from it; on, its resampler is suspected of the warped sound at the start of a capture.
            # (An app playing on another output than the system's would drift: not handled)
            'taps': [{'uid': str(description.UUID().UUIDString()), 'drift': False}],
        }
        # the clock: the system's output, as a sub-device (as Apple's sample does). AG_CUTEMUSIC_TAP_SUBDEVICE=off: the
        # tap alone (to test whether that sub-device, the output the mic and the capture also play on, is why the
        # capture crackles while the mic plays)
        output = _default_output_uid() if os.environ.get('AG_CUTEMUSIC_TAP_SUBDEVICE') != 'off' else None
        if output:
            settings['master'] = output
            settings['subdevices'] = [{'uid': output}]
        device = ctypes.c_uint32(0)
        status = core_audio.AudioHardwareCreateAggregateDevice(
            objc.pyobjc_id(NSDictionary.dictionaryWithDictionary_(settings)), ctypes.byref(device))
        if status != 0 or not device.value:
            self.close()
            raise RuntimeError(f'cannot make the device for {app.name} (Core Audio error {status})')
        self.device_id = device.value
        log.info(f'[taps] {app.name} ({bundle_id}, {len(app.objects)} process(es)) captured, '
                 f'{"muted on its own output" if mute else "still playing on its own output"}: device {self.device_id}')

    def close(self):
        core_audio = _load()[0]
        if self.device_id:
            core_audio.AudioHardwareDestroyAggregateDevice(self.device_id)
            self.device_id = None
        if self.tap:
            core_audio.AudioHardwareDestroyProcessTap(self.tap)
            self.tap = None
            log.info(f'[taps] {self.name} no longer captured')


# What an app plays, for the MUSIC strip (as the BLINK strip shows who is in the call): the players that tell it over
# AppleScript (macOS asks once whether AG CuteMusic may control each: Info.plist NSAppleEventsUsageDescription, the
# apple-events entitlement); other apps (a browser): nothing, their name only.
_TRACK_SCRIPT = '''with timeout of 2 seconds
    tell application id "%s"
        if player state is playing or player state is paused then
            return (name of current track) & "\n" & (artist of current track) & "\n" & (player state as text) & "\n" & (sound volume as text)
        end if
    end tell
end timeout
return ""'''
PLAYERS = ('com.spotify.client', 'com.apple.Music')
_refused = set()        # players whose control was refused (asked once: not asked again here)


def now_playing(bundle_id):
    """(title, artist, playing, volume 0..100 or None) of what the player bundle_id plays (or has paused), None when
    unknown. Main thread only (NSAppleScript); macOS asks for the permission the first time, the call waiting."""
    if bundle_id not in PLAYERS or bundle_id in _refused or sys.platform != 'darwin':
        return None
    try:
        from Foundation import NSAppleScript
    except ImportError:
        return None
    result, error = NSAppleScript.alloc().initWithSource_(_TRACK_SCRIPT % bundle_id).executeAndReturnError_(None)
    if result is None:
        number = int(error.get('NSAppleScriptErrorNumber', 0)) if error is not None else 0
        if number == -1743:         # not authorized to send Apple events
            _refused.add(bundle_id)
            log.warning(f'[taps] not allowed to ask {bundle_id} what it plays: System Settings -> Privacy & Security -> '
                        f'Automation -> AG CuteMusic')
        return None
    text = str(result.stringValue() or '')
    if not text.strip():
        return None
    title, artist, state, volume = (text.split('\n') + ['', '', ''])[:4]
    volume = volume.strip()
    return title.strip(), artist.strip(), state.strip() == 'playing', int(volume) if volume.isdigit() else None


def set_volume(bundle_id, volume):
    """A player's own volume (0..100: Spotify's, Music's slider): what is heard of it, wherever it plays."""
    return remote(bundle_id, f'set sound volume to {max(0, min(100, int(volume)))}')


def remote(bundle_id, command):
    """A player's buttons (PLAYERS): command 'playpause', 'next track' or 'previous track'. True when sent."""
    if bundle_id not in PLAYERS or bundle_id in _refused or sys.platform != 'darwin':
        return False
    from Foundation import NSAppleScript
    script = f'with timeout of 2 seconds\n    tell application id "{bundle_id}" to {command}\nend timeout'
    result, error = NSAppleScript.alloc().initWithSource_(script).executeAndReturnError_(None)
    if result is None:
        log.warning(f'[taps] {bundle_id}: {command} not done ({error.get("NSAppleScriptErrorMessage", error) if error else "?"})')
        return False
    return True



# The glyphs around MICROPHONE's INPUT (icons.line_path): what an input is, in the console's own style
VIRTUAL, AGGREGATE, BLUETOOTH, BLUETOOTH_LE = _fourcc('virt'), _fourcc('grup'), _fourcc('blue'), _fourcc('blea')


DEFAULT_OUTPUT = _fourcc('dOut')        # kAudioHardwarePropertyDefaultOutputDevice
DATA_SOURCE = _fourcc('ssrc')           # kAudioDevicePropertyDataSource
HEADPHONES_SOURCE = _fourcc('hdpn')     # the built-in output's headphone jack
HEADPHONE_NAME = re.compile(r'head(set|phone)|airpods|earpods|buds', re.IGNORECASE)


def output_kinds():
    """{output device name: 'laptop' (the Mac's speakers), 'headset' (headphones: the jack, a USB headset),
    'bluetooth', 'wave' (virtual, aggregate) or 'speaker' (USB, HDMI, AirPlay...)}; None: the default output's name."""
    kinds = {}
    if sys.platform != 'darwin':
        return kinds
    try:
        default = _u32(SYSTEM, DEFAULT_OUTPUT)
        for (device,) in struct.iter_unpack('=I', _data(SYSTEM, DEVICES) or b''):
            if not _data(device, STREAMS, scope=OUTPUT_SCOPE):
                continue
            transport, name = _u32(device, TRANSPORT), _string(device, NAME) or ''
            if transport in (BLUETOOTH, BLUETOOTH_LE):
                kind = 'bluetooth'
            elif transport in (VIRTUAL, AGGREGATE):
                kind = 'wave'
            elif HEADPHONE_NAME.search(name) or (transport == BUILT_IN and _u32(device, DATA_SOURCE, OUTPUT_SCOPE) == HEADPHONES_SOURCE):
                kind = 'headset'
            elif transport == BUILT_IN:
                kind = 'laptop'
            else:
                kind = 'speaker'
            kinds[name] = kind
            if device == default:
                kinds[None] = name
    except Exception as e:
        log.warning(f'[taps] cannot tell the outputs apart: {e}')
    return kinds


def input_kinds():
    """{input device name: 'laptop' (built in), 'headset' (Bluetooth), 'wave' (virtual, aggregate) or 'mic'}."""
    kinds = {}
    if sys.platform != 'darwin':
        return kinds
    try:
        for (device,) in struct.iter_unpack('=I', _data(SYSTEM, DEVICES) or b''):
            if not _data(device, STREAMS, scope=INPUT_SCOPE):
                continue
            transport = _u32(device, TRANSPORT)
            kinds[_string(device, NAME) or ''] = ('laptop' if transport == BUILT_IN else
                                                 'headset' if transport in (BLUETOOTH, BLUETOOTH_LE) else
                                                 'wave' if transport in (VIRTUAL, AGGREGATE) else 'mic')
    except Exception as e:
        log.warning(f'[taps] cannot tell the inputs apart: {e}')
    return kinds
