AG CuteMusic for phones and tablets
===================================

A React Native music player. It plays the phone's own music (Android's media index, a folder: Music/ at first,
android/app/.../MediaLibrary.kt) and, as an option, the music of an AG CuteMusic sharing its library on the LAN
(File -> Share my library on the LAN, or `--headless` on the NAS): found by Bonjour (`_agcutemusic._tcp`) or by
address, streamed over HTTP (cutemusic/sharing.py). Music playback only: no mixer, negatives, web tokens nor Blink.
The source is chosen in Libraries (the play bar's right button); a LAN library used last that is not there (away
from home) falls back to the phone's own music.

- Portrait: the play bar fixed at the bottom, two views stacked over it: the cover on top, the VU meter (Bars,
  Needles, LEDs, Spectrum: the pills over it, or a tap on it) with the lyrics under it.
- Landscape: the Amplifier view (amp.py): the VU meters with the ANALOG / LED BAR / SPECTRUM lever, VOLUME (the
  player's volume) and MUTE. No BASS, TREBLE, BALANCE nor OUTPUT: the phone's player has none.
- The browser view (the play bar's left button), to find a song: Year, Artist, Album, Folder as the desktop's
  tree, one level at a time; filter, genres, color markers; a song plays its group from it, a long press: Play,
  Play next, Add to queue. The Queue tab; the library chosen from the pill at its top left.

The VU meter's levels are worked out ahead, since the phone's player gives none: for the phone's own songs on the
phone (MediaCodec + an FFT, MediaLibrary.kt), for a LAN library's by the library (GET /levels, cutemusic/levels.py,
GStreamer), both kept in a cache. A library from an older AG CuteMusic plays, without levels,
covers from the tags or the lyrics in the tags.

Building
--------

    npm install
    cd ios && bundle install && bundle exec pod install && cd ..
    npm run ios         # or: npm run android

Dependency changes go in patches/ (patch-package, run by postinstall), never by hand in node_modules.

Formats: iOS plays MP3, AAC/ALAC (m4a), WAV, AIFF and FLAC; Android also Ogg Vorbis and Opus. Others (WMA, APE,
WavPack, DSF, Musepack) are shown dimmed and skipped.
