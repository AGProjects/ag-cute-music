"""The control channel between the Blinks and AG CuteMusic on "AG Podcast Mixer" (the AG Podcast Mixer driver, v2,
macos/driver/AGPodcastMixer.cpp): two custom CFString properties of the device, reached through CoreAudio by any app,
a sandboxed one (Blink Pro) too. The same code is in Blink for macOS (CuteMusic.py); keep them in step.

- 'agbc': each Blink sets "<pid> <json>" every 2 s ({"pid", "calls", "redial"}); read, {"<pid>": <json>, ...}.
- 'agrq': our last request ({"seq", "pid", "op": "hangup" with "id", or "redial"}); the Blink it names acts once.

On Linux the same over D-Bus (bus.py: SetCalls, the Request signal): blinks() and request() go there, so window.py
is the same on both.
"""

import ctypes
import ctypes.util
import json
import os
import shutil
import struct
import subprocess
import sys

CONTROL_DEVICE_UID = 'com.ag-projects.podcast-mixer.input'
STREAMING_DEVICE_UID = 'com.ag-projects.podcast-mixer.streaming'       # "AG Podcast Streaming" (there while streaming)


def _fourcc(code):
    return struct.unpack('>I', code.encode('ascii'))[0]


BLINK_CALLS = _fourcc('agbc')
REQUEST = _fourcc('agrq')
_TRANSLATE_UID = _fourcc('uidd')        # kAudioHardwarePropertyTranslateUIDToDevice
_SCOPE_GLOBAL = _fourcc('glob')
_SCOPE_INPUT = _fourcc('inpt')
_RUNNING_SOMEWHERE = _fourcc('gone')        # kAudioDevicePropertyDeviceIsRunningSomewhere
_PROCESSES = _fourcc('prs#')                # kAudioHardwarePropertyProcessObjectList (macOS 14.2+)
_PROCESS_PID = _fourcc('ppid')              # kAudioProcessPropertyPID
_PROCESS_BUNDLE = _fourcc('pbid')           # kAudioProcessPropertyBundleID
_PROCESS_READING = _fourcc('piri')          # kAudioProcessPropertyIsRunningInput
_PROCESS_DEVICES = _fourcc('pdv#')          # kAudioProcessPropertyDevices (input scope: the devices it reads)
_SYSTEM_OBJECT = 1
_UTF8 = 0x08000100


class _Address(ctypes.Structure):
    _fields_ = [('mSelector', ctypes.c_uint32), ('mScope', ctypes.c_uint32), ('mElement', ctypes.c_uint32)]


_ListenerProc = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_void_p)


class _Libraries(object):
    def __init__(self):
        self.ca = ctypes.CDLL('/System/Library/Frameworks/CoreAudio.framework/CoreAudio')
        self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        cf, ca = self.cf, self.ca
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringGetLength.restype = ctypes.c_long
        cf.CFStringGetLength.argtypes = [ctypes.c_void_p]
        cf.CFStringGetMaximumSizeForEncoding.restype = ctypes.c_long
        cf.CFStringGetMaximumSizeForEncoding.argtypes = [ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFRelease.restype = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
        ca.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32, ctypes.c_void_p,
                                                  ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        ca.AudioObjectSetPropertyData.restype = ctypes.c_int32
        ca.AudioObjectSetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32, ctypes.c_void_p,
                                                  ctypes.c_uint32, ctypes.c_void_p]
        ca.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
        ca.AudioObjectGetPropertyDataSize.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32, ctypes.c_void_p,
                                                      ctypes.POINTER(ctypes.c_uint32)]
        ca.AudioObjectAddPropertyListener.restype = ctypes.c_int32
        ca.AudioObjectAddPropertyListener.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address), _ListenerProc, ctypes.c_void_p]
        ca.AudioObjectRemovePropertyListener.restype = ctypes.c_int32
        ca.AudioObjectRemovePropertyListener.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address), _ListenerProc, ctypes.c_void_p]

    def cfstring(self, text):
        return self.cf.CFStringCreateWithCString(None, text.encode('utf-8'), _UTF8)

    def text(self, cfstring):
        size = self.cf.CFStringGetMaximumSizeForEncoding(self.cf.CFStringGetLength(cfstring), _UTF8) + 1
        buffer = ctypes.create_string_buffer(size)
        if not self.cf.CFStringGetCString(cfstring, buffer, size, _UTF8):
            return None
        return buffer.value.decode('utf-8', 'replace')


_libraries = None


def _libs():
    global _libraries
    if _libraries is None and sys.platform == 'darwin':
        try:
            _libraries = _Libraries()
        except OSError:
            _libraries = False
    return _libraries or None


def control_device():
    """The AudioObjectID of "AG Podcast Mixer" (the driver installed and loaded), else None."""
    return _device(CONTROL_DEVICE_UID)


def _device(uid_text):
    """A device's AudioObjectID by its UID, None when it is not there."""
    libs = _libs()
    if libs is None:
        return None
    uid = ctypes.c_void_p(libs.cfstring(uid_text))
    device = ctypes.c_uint32(0)
    size = ctypes.c_uint32(ctypes.sizeof(device))
    address = _Address(_TRANSLATE_UID, _SCOPE_GLOBAL, 0)
    try:
        status = libs.ca.AudioObjectGetPropertyData(_SYSTEM_OBJECT, ctypes.byref(address), ctypes.sizeof(uid),
                                                    ctypes.byref(uid), ctypes.byref(size), ctypes.byref(device))
    finally:
        libs.cf.CFRelease(uid)
    return device.value if status == 0 and device.value else None


def get_property(device, selector):
    """A control property's text, None when it cannot be read (an older driver: no such property)."""
    libs = _libs()
    if libs is None or device is None:
        return None
    value = ctypes.c_void_p(0)
    size = ctypes.c_uint32(ctypes.sizeof(value))
    address = _Address(selector, _SCOPE_GLOBAL, 0)
    if libs.ca.AudioObjectGetPropertyData(device, ctypes.byref(address), 0, None, ctypes.byref(size), ctypes.byref(value)) != 0 or not value.value:
        return None
    try:
        return libs.text(value)
    finally:
        libs.cf.CFRelease(value)


def set_property(device, selector, text):
    """Set a control property; the OSStatus (0: done)."""
    libs = _libs()
    if libs is None or device is None:
        return -1
    value = ctypes.c_void_p(libs.cfstring(text))
    address = _Address(selector, _SCOPE_GLOBAL, 0)
    try:
        return libs.ca.AudioObjectSetPropertyData(device, ctypes.byref(address), 0, None, ctypes.sizeof(value), ctypes.byref(value))
    finally:
        libs.cf.CFRelease(value)


class PropertyListener(object):
    """callback() whenever the property is set (called on a CoreAudio thread: hand it over to your own)."""

    def __init__(self, device, selector, callback):
        self.device = device
        self._address = _Address(selector, _SCOPE_GLOBAL, 0)
        self._callback = callback
        self._proc = _ListenerProc(self._changed)       # kept: ctypes must not free it while CoreAudio holds it
        libs = _libs()
        self.active = libs is not None and libs.ca.AudioObjectAddPropertyListener(device, ctypes.byref(self._address), self._proc, None) == 0

    def _changed(self, device, count, addresses, data):
        try:
            self._callback()
        except Exception:
            pass
        return 0

    def remove(self):
        libs = _libs()
        if self.active and libs is not None:
            libs.ca.AudioObjectRemovePropertyListener(self.device, ctypes.byref(self._address), self._proc, None)
        self.active = False


def start_bus(changed, show_mixer=None):
    """Linux: the D-Bus service (bus.py), changed() when a Blink sets its calls, show_mixer() when one asks for the
    mixer; elsewhere nothing (the driver's property listener does it). True when it is there."""
    if not sys.platform.startswith('linux'):
        return False
    from cutemusic import bus
    return bus.start(changed, show_mixer)


def stop_bus():
    if sys.platform.startswith('linux'):
        from cutemusic import bus
        bus.stop()


def blinks():
    """{pid: state} of the Blinks on the control channel ('agbc'; Linux: D-Bus), {} without it."""
    import json
    if sys.platform.startswith('linux'):
        from cutemusic import bus
        return bus.blinks()
    text = get_property(control_device(), BLINK_CALLS)
    try:
        found = json.loads(text) if text else {}
    except ValueError:
        return {}
    return {int(pid): state for pid, state in found.items() if str(pid).isdigit() and isinstance(state, dict)}


def request(text):
    """Our request on 'agrq' (Linux: the Request signal); True when set."""
    if sys.platform.startswith('linux'):
        from cutemusic import bus
        return bus.request(text)
    return set_property(control_device(), REQUEST, text) == 0


STREAMING = _fourcc('agst')     # on the Feed device: "1" the streaming devices there, "0" not


VISIBLE = _fourcc('agvs')       # on the Feed device: "1" "AG Podcast Mixer" listed for every app, "0" hidden
SYLK_DESKTOP = 'com.agprojects.Sylk'        # Sylk Desktop (Electron): opens only listed devices


def sylk_running():
    """Whether Sylk Desktop runs (it then needs "AG Podcast Mixer" listed)."""
    try:
        from AppKit import NSWorkspace
    except ImportError:
        return False
    return any(application.bundleIdentifier() == SYLK_DESKTOP
               for application in NSWorkspace.sharedWorkspace().runningApplications())


def set_mixer_visible(on):
    """"AG Podcast Mixer" listed for every app (Sylk Desktop runs) or hidden again; True when asked (False: no
    driver, or one older than 1.3.0)."""
    from cutemusic import driver
    feed = driver.feed_device()
    return feed is not None and set_property(feed, VISIBLE, '1' if on else '0') == 0


def set_streaming(on):
    """The driver's streaming devices ("AG Podcast Streaming" and its Feed) added or removed; True when asked."""
    from cutemusic import driver
    feed = driver.feed_device()
    return feed is not None and set_property(feed, STREAMING, '1' if on else '0') == 0


# Who else reads "AG Podcast Mixer" and "AG Podcast Streaming" (Zoom, a browser, OBS...): the console is on air with them

OURS = ('com.ag-projects.ag-cute-music', 'com.agprojects.Blink', 'com.agprojects.BlinkLite', 'com.agprojects.SIP2SIP')
_NODES = {'mixer': 'ag_podcast_mixer', 'streaming': 'ag_podcast_streaming'}       # Linux: the PipeWire sources


def listeners(blink_pids=()):
    """{'mixer': [app], 'streaming': [app]}: the apps reading "AG Podcast Mixer" and "AG Podcast Streaming" now, other
    than us and the Blinks (blink_pids; their calls say whether they are on air); an app '' when who it is is not known
    (macOS before 14.2). {} when it cannot be told."""
    excluded = set(blink_pids) | {os.getpid()}
    try:
        if sys.platform == 'darwin':
            return _listeners_macos(excluded)
        if sys.platform.startswith('linux'):
            return _listeners_pipewire(excluded)
    except Exception:
        pass
    return {}


def _get(obj, selector, scope, value):
    libs = _libs()
    size = ctypes.c_uint32(ctypes.sizeof(value))
    address = _Address(selector, scope, 0)
    return libs.ca.AudioObjectGetPropertyData(obj, ctypes.byref(address), 0, None, ctypes.byref(size), ctypes.byref(value)) == 0


def _number(obj, selector, scope=_SCOPE_GLOBAL):
    value = ctypes.c_uint32(0)
    return value.value if _get(obj, selector, scope, value) else None


def _objects(obj, selector, scope=_SCOPE_GLOBAL):
    """An AudioObjectID array property, None when it cannot be read."""
    libs = _libs()
    address = _Address(selector, scope, 0)
    size = ctypes.c_uint32(0)
    if libs.ca.AudioObjectGetPropertyDataSize(obj, ctypes.byref(address), 0, None, ctypes.byref(size)) != 0:
        return None
    array = (ctypes.c_uint32 * (size.value // 4))()
    if libs.ca.AudioObjectGetPropertyData(obj, ctypes.byref(address), 0, None, ctypes.byref(size), array) != 0:
        return None
    return list(array[:size.value // 4])


def _text(obj, selector):
    libs = _libs()
    value = ctypes.c_void_p(0)
    if not _get(obj, selector, _SCOPE_GLOBAL, value) or not value.value:
        return None
    try:
        return libs.text(value)
    finally:
        libs.cf.CFRelease(value)


def _listeners_macos(excluded):
    libs = _libs()
    if libs is None:
        return {}
    devices = {key: _device(uid) for key, uid in (('mixer', CONTROL_DEVICE_UID), ('streaming', STREAMING_DEVICE_UID))}
    found = {key: [] for key in devices}
    running = {key: device for key, device in devices.items() if device is not None and _number(device, _RUNNING_SOMEWHERE)}
    if not running:         # (no client has IO started on either: no need to ask who)
        return found
    processes = _objects(_SYSTEM_OBJECT, _PROCESSES)
    if processes is None:   # before 14.2: only that someone reads (a Blink, perhaps, on "AG Podcast Mixer")
        for key in running:
            if key == 'streaming' or len(excluded) == 1:
                found[key].append('')
        return found
    for process in processes:
        if not _number(process, _PROCESS_READING):
            continue
        pid = ctypes.c_int32(_number(process, _PROCESS_PID) or 0).value
        bundle = _text(process, _PROCESS_BUNDLE) or ''
        if pid in excluded or any(bundle == ours or bundle.startswith(ours + '.') for ours in OURS):
            continue
        reads = _objects(process, _PROCESS_DEVICES, _SCOPE_INPUT) or []
        for key, device in running.items():
            if device in reads:
                name = _app_name(pid, bundle)
                if name not in found[key]:
                    found[key].append(name)
    return found


def _app_name(pid, bundle):
    """An app's name: its own, else its parent app's (a helper: Chrome's, Zoom's), else its bundle id's last part."""
    try:
        from AppKit import NSRunningApplication
        application = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if application is not None and application.activationPolicy() == 0 and application.localizedName():
            return str(application.localizedName())
        parts = bundle.split('.')
        while len(parts) > 2:
            parts.pop()
            for application in NSRunningApplication.runningApplicationsWithBundleIdentifier_('.'.join(parts)):
                if application.localizedName():
                    return str(application.localizedName())
        if application is not None and application.localizedName():
            return str(application.localizedName())
    except Exception:
        pass
    return bundle.rsplit('.', 1)[-1] if bundle else ''


def _listeners_pipewire(excluded):
    """From pw-dump: the active links out of ag_podcast_mixer and ag_podcast_streaming, and the nodes they go to."""
    if shutil.which('pw-dump') is None:
        return {}
    done = subprocess.run(['pw-dump'], capture_output=True, text=True, timeout=5)
    if done.returncode != 0:
        return {}
    objects = json.loads(done.stdout)
    nodes = {item['id']: (item.get('info') or {}).get('props') or {} for item in objects
             if item.get('type') == 'PipeWire:Interface:Node'}
    sources = {node: key for key, name in _NODES.items() for node, props in nodes.items() if props.get('node.name') == name}
    found = {key: [] for key in _NODES}
    for item in objects:
        info = item.get('info') or {}
        if item.get('type') != 'PipeWire:Interface:Link' or info.get('output-node-id') not in sources or info.get('state') != 'active':
            continue
        props = nodes.get(info.get('input-node-id')) or {}
        if str(props.get('node.name', '')).startswith('ag_podcast'):        # (our own loopbacks)
            continue
        try:
            pid = int(props.get('application.process.id'))
        except (TypeError, ValueError):
            pid = None
        if pid in excluded:
            continue
        name = str(props.get('application.name') or props.get('node.description') or props.get('node.name') or '')
        key = sources[info['output-node-id']]
        if name not in found[key]:
            found[key].append(name)
    return found
