// The levels of the song playing, worked out ahead by the library (GET /levels, cutemusic/levels.py): the phone's
// player gives none. Read at the playing position, extrapolated between the player's progress reports.

import { useCallback, useEffect, useRef } from 'react';
import TrackPlayer, {
  useActiveTrack,
  useIsPlaying,
} from 'react-native-track-player';

import { isLocal, localLevels } from './local';
import { QueuedTrack, libraryOf } from './player';

export type Levels = {
  interval: number; // ms a frame
  frames: number;
  rms: Uint8Array; // L R a frame: -dB x 2
  peak: Uint8Array;
  bands: number;
  low: number;
  high: number;
  spectrum: Uint8Array; // bands a frame: -dB x 2
};

declare function atob(data: string): string; // (Hermes has it; the types do not)

export const FLOOR = -60;
export const SPECTRUM_FLOOR = -90;

function decode(text: string): Uint8Array {
  const binary = atob(text || '');
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

const cache = new Map<string, Promise<Levels | null>>(); // by track id; a few kept

function toLevels(data: any): Levels | null {
  return data && data.frames
    ? {
        interval: data.interval,
        frames: data.frames,
        rms: decode(data.rms),
        peak: decode(data.peak),
        bands: data.bands,
        low: data.low,
        high: data.high,
        spectrum: decode(data.spectrum),
      }
    : null;
}

// From the library on the LAN (GET /levels), or worked out on the phone for its own songs (local.ts)
export function fetchLevels(track: QueuedTrack): Promise<Levels | null> {
  const id = String(track.id);
  let found = cache.get(id);
  if (!found) {
    const library = libraryOf(track);
    if (!library) {
      // its library not loaded yet (a queue restored at start): asked again later, nothing kept
      return Promise.resolve(null);
    }
    const started = Date.now();
    found = isLocal(library)
      ? localLevels(track.source).then(toLevels)
      : library && library.endpoints.includes('levels')
      ? fetch(`${library.address}/levels?path=${encodeURIComponent(track.rel)}`)
          .then(r => (r.ok ? r.json() : null))
          .then(toLevels)
          .catch(() => null)
      : Promise.resolve(null);
    found.then(levels =>
      console.log(
        levels
          ? `[levels] ${track.title}: ${levels.frames} frames, ${(
              (levels.frames * levels.interval) /
              1000
            ).toFixed(1)}s of ${Math.round(track.duration || 0)}s, in ${(
              (Date.now() - started) /
              1000
            ).toFixed(1)}s`
          : `[levels] ${track.title}: none (${
              isLocal(library)
                ? 'not worked out on the phone'
                : 'the library has no /levels'
            })`,
      ),
    );
    cache.set(id, found);
    while (cache.size > 6) {
      cache.delete(cache.keys().next().value as string);
    }
  }
  return found;
}

export type Frame = {
  rms: [number, number];
  peak: [number, number];
  spectrum: number[] | null;
  low: number;
  high: number;
};

export function frameAt(levels: Levels, seconds: number): Frame | null {
  const index = Math.floor((seconds * 1000) / levels.interval);
  if (index < 0 || index >= levels.frames) {
    return null;
  }
  const db = (b: number, floor: number) => Math.max(floor, -b / 2);
  const spectrum: number[] = [];
  for (let i = 0; i < levels.bands; i++) {
    spectrum.push(
      db(levels.spectrum[index * levels.bands + i] ?? 255, SPECTRUM_FLOOR),
    );
  }
  return {
    rms: [
      db(levels.rms[index * 2], FLOOR),
      db(levels.rms[index * 2 + 1], FLOOR),
    ],
    peak: [
      db(levels.peak[index * 2], FLOOR),
      db(levels.peak[index * 2 + 1], FLOOR),
    ],
    spectrum: levels.bands ? spectrum : null,
    low: levels.low,
    high: levels.high,
  };
}

// The frame playing now, for the meters' animation (a ref: read each frame, no re-render): null while
// paused, stalled, or with no levels (an older library, GStreamer missing there)
export function useLiveLevels() {
  const track = useActiveTrack() as QueuedTrack | undefined;
  const { playing } = useIsPlaying();
  const levels = useRef<Levels | null>(null);
  const playingNow = useRef(false);
  const pastEnd = useRef(false);
  playingNow.current = !!playing;
  const clock = useRef({ position: 0, at: 0 });
  useEffect(() => {
    let live = true;
    levels.current = null;
    pastEnd.current = false;
    if (track) {
      fetchLevels(track).then(found => live && (levels.current = found));
      // the next one's ahead (worked out on the library while this one plays)
      TrackPlayer.getActiveTrackIndex().then(async index => {
        const queue = await TrackPlayer.getQueue();
        const following =
          index !== undefined
            ? (queue[index + 1] as QueuedTrack | undefined)
            : undefined;
        if (live && following) {
          fetchLevels(following);
        }
      });
    }
    return () => {
      live = false;
    };
  }, [track]);
  useEffect(() => {
    if (!playing) {
      return;
    }
    let live = true;
    const poll = async () => {
      const { position } = await TrackPlayer.getProgress();
      if (live) {
        clock.current = { position, at: Date.now() };
      }
      // no levels yet (the song's library loaded after it was queued): asked again
      if (live && !levels.current && track) {
        const found = await fetchLevels(track);
        if (live && found) {
          levels.current = found;
        }
      }
    };
    poll();
    const timer = setInterval(poll, 1000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [playing, track]);
  return useCallback(() => {
    if (!playingNow.current || !levels.current || !clock.current.at) {
      return null;
    }
    const seconds =
      clock.current.position + (Date.now() - clock.current.at) / 1000;
    const frame = frameAt(levels.current, seconds);
    if (!frame && !pastEnd.current) {
      // (once a song) playing beyond what was worked out: the meters stop here
      pastEnd.current = true;
      console.warn(
        `[levels] at ${seconds.toFixed(1)}s, past the ${(
          (levels.current.frames * levels.current.interval) /
          1000
        ).toFixed(1)}s worked out`,
      );
    }
    return frame;
  }, []);
}
