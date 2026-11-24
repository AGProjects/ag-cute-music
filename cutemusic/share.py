"""AG CuteMusic's mixer as a sound device for Blink, on Linux: the PipeWire nodes (python3-sipsimple
docs/PodcastMixerLink.md), installed by the package (pipewire/ag-podcast-mixer.conf), always there:

    ag_podcast_mixer_feed  "AG Podcast Mixer Feed"   a sink: the mix played into it (link.py, NodeOutput)
    ag_podcast_mixer       "AG Podcast Mixer"        its sound (a loopback): Blink's microphone, the mic and the music
                                                     while TO CALLS is on
    ag_podcast_mixer_call  "AG Podcast Mixer Call"   what Blink plays: the friends; its monitor is the mixer's BLINK
                                                     strip, played on our output (the headset), never into the karaoke
    ag_podcast_streaming_feed -> ag_podcast_streaming ("AG Podcast Streaming"): the recorder's mix while STREAMING
                                                     is on, for a streaming app (always there, silent otherwise)

Blink's SIP audio reaches them through ALSA (the SDK has no other backend on Linux): the PCM ag_cutemusic_mixer
described "AG Podcast Mixer" (alsa/60-ag-cute-music.conf, in /etc/alsa/conf.d by the package; run from the source tree,
set up in the user's ALSA configuration: ensure_alsa_device). While it is chosen in Blink, its echo cancelling is off.

Also: calls_of(), a Blink's calls as the control channel gives them (control.py), for the BLINK strip.
"""

import os
import shutil
import subprocess

from cutemusic.util import log


__all__ = ['SOURCE', 'CALL_SINK', 'FEED', 'STREAM_FEED', 'STREAM_SOURCE', 'calls_of', 'pipewire_nodes', 'streaming_nodes',
           'ensure_alsa_device']


SOURCE = 'ag_podcast_mixer'             # Blink's microphone (the Feed's loopback)
CALL_SINK = 'ag_podcast_mixer_call'     # what Blink plays
FEED = 'ag_podcast_mixer_feed'          # the sink the mix is played into, its sound ag_podcast_mixer
STREAM_FEED = 'ag_podcast_streaming_feed'      # the sink the recorder's mix is played into while STREAMING is on
STREAM_SOURCE = 'ag_podcast_streaming'         # its sound: "AG Podcast Streaming", a streaming app's input
ALSA_DEVICE = 'ag_cutemusic_mixer'
USER_ALSA = os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), 'alsa')
ALSA_CONFIGS = ('/etc/alsa/conf.d/60-ag-cute-music.conf', '/etc/asound.conf', os.path.expanduser('~/.asoundrc'),
                os.path.join(USER_ALSA, 'asoundrc'))
ALSA_DEFINITION = '''pcm.ag_cutemusic_mixer {
    type pipewire
    playback_node "ag_podcast_mixer_call"
    capture_node "ag_podcast_mixer"
    hint {
        show on
        description "AG Podcast Mixer"
    }
}
'''
def _pactl(*args):
    """pactl's output, stripped; raises RuntimeError with its error."""
    try:
        done = subprocess.run(['pactl', *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f'pactl {args[0]}: {e}')
    if done.returncode != 0:
        raise RuntimeError(f'pactl {args[0]}: {done.stderr.strip() or done.returncode}')
    return done.stdout.strip()


def pipewire_nodes():
    """Whether the PipeWire fragment's nodes are there (pipewire/ag-podcast-mixer.conf, read by PipeWire): the Feed
    and the call sinks, Blink's microphone (the Feed's loopback)."""
    import sys
    if not sys.platform.startswith('linux') or shutil.which('pactl') is None:
        return False
    try:
        sources = [line.split('\t')[1] for line in _pactl('list', 'short', 'sources').splitlines() if '\t' in line]
        sinks = [line.split('\t')[1] for line in _pactl('list', 'short', 'sinks').splitlines() if '\t' in line]
    except (RuntimeError, IndexError):
        return False
    return SOURCE in sources and CALL_SINK in sinks and FEED in sinks


def streaming_nodes():
    """Whether the fragment's streaming nodes are there: the sink ag_podcast_streaming_feed and its loopback, the
    source ag_podcast_streaming."""
    import sys
    if not sys.platform.startswith('linux') or shutil.which('pactl') is None:
        return False
    try:
        sources = [line.split('\t')[1] for line in _pactl('list', 'short', 'sources').splitlines() if '\t' in line]
        sinks = [line.split('\t')[1] for line in _pactl('list', 'short', 'sinks').splitlines() if '\t' in line]
    except (RuntimeError, IndexError):
        return False
    return STREAM_SOURCE in sources and STREAM_FEED in sinks


def _mentions(path, text):
    try:
        with open(path, errors='replace') as file:
            return text in file.read()
    except OSError:
        return False


DESCRIPTION = 'AG Podcast Mixer'     # the device's name in Blink (blink/cutemusic.py DEVICE: the same)


def _alsa_file():
    return ('# AG CuteMusic\'s mixer as one sound device, for Blink (Devices -> AG Podcast Mixer).\n'
            '# Written by AG CuteMusic; included from asoundrc.\n\n'
            + ALSA_DEFINITION)


def ensure_alsa_device():
    """The ALSA device there, or set up in the user's ALSA configuration (no root): the definition written to
    ~/.config/alsa/ag-cute-music.conf, included from ~/.config/alsa/asoundrc (made when there is none; one line added
    to it otherwise). True when it was set up now. Linux only."""
    import sys
    if not sys.platform.startswith('linux'):
        return False
    definition = os.path.join(USER_ALSA, 'ag-cute-music.conf')
    if os.path.isfile(definition) and not _mentions(definition, CALL_SINK):     # another definition: brought up to date
        try:
            with open(definition, 'w') as file:
                file.write(_alsa_file())
            log.info(f'[devices] the ALSA device {ALSA_DEVICE} brought up to date in {definition}')
            return True
        except OSError as e:
            log.warning(f'[devices] cannot update {definition}: {e}')
    if any(_mentions(path, ALSA_DEVICE) or _mentions(path, '60-ag-cute-music.conf') or _mentions(path, 'ag-cute-music.conf')
           for path in ALSA_CONFIGS):
        return False
    asoundrc = os.path.join(USER_ALSA, 'asoundrc')
    try:
        os.makedirs(USER_ALSA, exist_ok=True)
        with open(definition, 'w') as file:
            file.write(_alsa_file())
        with open(asoundrc, 'a') as file:
            file.write(f'\n# AG CuteMusic\'s mixer as a sound device for Blink (added by AG CuteMusic)\n<{definition}>\n')
    except OSError as e:
        log.warning(f'[devices] cannot set up the ALSA device {ALSA_DEVICE} in {USER_ALSA}: {e}: Blink cannot list the mixer')
        return False
    log.info(f'[devices] the ALSA device {ALSA_DEVICE} set up: {definition}, included from {asoundrc}')
    return True


def calls_of(state, channel):
    """A Blink's calls and rooms: [(title, detail, held, quality, target)], a call's party and its address, a room's
    name and who is in it; quality (rtt ms, loss received %, loss sent %) when Blink measured it, else None; target
    ({"pid", "id", "channel"}: the control channel, the driver's 'agrq' or D-Bus) what HANG UP asks, its id None when
    the Blink gives none (no button then)."""
    found = []
    pid = state.get('pid')
    for call in state.get('calls') or []:
        if not isinstance(call, dict):
            continue
        progress = str(call.get('state') or '')     # dialing, calling, ringing, incoming, hanging up; none: connected
        target = {'pid': pid, 'id': call.get('id') if pid is not None else None, 'channel': channel, 'state': progress or None,
                  'address': str(call.get('address') or ''),        # (the recorder's artist: the party's SIP URI)
                  'started': call.get('started') if isinstance(call.get('started'), (int, float)) else None,  # connected since (epoch)
                  'participants': [p for p in call.get('participants') or [] if isinstance(p, dict) and p.get('id') is not None]}
        if call.get('kind') == 'room':
            people = [str(name) for name in call.get('participants') or []]
            found.append((str(call.get('name') or 'Room'), ', '.join(people) if people else 'nobody else yet', False, _quality(call), target))
        else:
            name, address = str(call.get('name') or ''), str(call.get('address') or '')
            detail = address if address and address != name else ''
            if progress:
                detail = f'{progress}\u2026 \u00b7 {detail}' if detail else f'{progress}\u2026'
            found.append((name or address, detail, bool(call.get('held')), _quality(call), target))
    return found


def _quality(call):
    """(rtt ms, loss received %, loss sent %) from a call's entry, None when Blink did not give them."""
    try:
        if 'rtt' not in call:
            return None
        return (float(call['rtt']), float(call.get('loss_rx') or 0), float(call.get('loss_tx') or 0))
    except (TypeError, ValueError):
        return None
