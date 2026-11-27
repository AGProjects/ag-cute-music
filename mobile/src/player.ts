// Playback (react-native-track-player): the songs streamed from the library over HTTP, the queue as the desktop's
// (Play: the group from the song chosen; Play next; Add to queue), shuffle and repeat kept between runs.

import TrackPlayer, {
  AppKilledPlaybackBehavior,
  Capability,
  RepeatMode,
  Track as PlayerTrack,
  useActiveTrack,
  useIsPlaying,
} from 'react-native-track-player';
import { useEffect, useState } from 'react';
import { AppState } from 'react-native';

import { isLocal, localCover } from './local';

import {
  Library,
  Track,
  albumArtist,
  coverUrl,
  displayTitle,
  fileUrl,
  playable,
} from './protocol';
import { playOrder } from './library';
import { settings } from './settings';
import { ui } from './log';

let ready: Promise<void> | null = null;

export function setupPlayer(): Promise<void> {
  if (!ready) {
    ready = (async () => {
      try {
        await TrackPlayer.setupPlayer({ autoHandleInterruptions: true });
      } catch (e: any) {
        // already set up (a reload in development)
        if (!String(e?.message || e).includes('already')) {
          throw e;
        }
      }
      await TrackPlayer.updateOptions({
        android: {
          appKilledPlaybackBehavior:
            AppKilledPlaybackBehavior.StopPlaybackAndRemoveNotification,
        },
        capabilities: [
          Capability.Play,
          Capability.Pause,
          Capability.SkipToNext,
          Capability.SkipToPrevious,
          Capability.SeekTo,
          Capability.Stop,
        ],
        compactCapabilities: [
          Capability.Play,
          Capability.Pause,
          Capability.SkipToNext,
        ],
        progressUpdateEventInterval: 1,
      });
      await TrackPlayer.setRepeatMode(
        (await settings.get('player/repeat', false))
          ? RepeatMode.Queue
          : RepeatMode.Off,
      );
    })();
  }
  return ready;
}

export type QueuedTrack = PlayerTrack & {
  rel: string;
  library: string;
  source: Track;
};

const libraries = new Map<string, Library>(); // by address: the queue may mix libraries
export const libraryOf = (track: QueuedTrack | undefined | null) =>
  track ? libraries.get(track.library) : undefined;

function toPlayer(library: Library, t: Track): QueuedTrack {
  libraries.set(library.address, library);
  const year = t.year ? ` (${t.year})` : '';
  return {
    id: `${library.address}|${t.rel}`,
    url: fileUrl(library, t.rel),
    title: displayTitle(t),
    artist: t.artist || albumArtist(t),
    album: (t.album || '') + year,
    genre: t.genres.join(', '),
    duration: t.duration || undefined,
    artwork: coverUrl(library, t.rel) || undefined,
    rel: t.rel,
    library: library.address,
    source: t,
  };
}

function mixed<T>(items: T[]): T[] {
  const result = [...items];
  for (let i = result.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [result[i], result[j]] = [result[j], result[i]];
  }
  return result;
}

let shuffle = false;
settings.get('player/shuffle', false).then(value => (shuffle = value));
export const isShuffle = () => shuffle;
export function setShuffle(on: boolean) {
  ui('shuffle', on ? 'on' : 'off');
  shuffle = on;
  settings.set('player/shuffle', on);
}

export async function setRepeat(on: boolean) {
  ui('repeat', on ? 'on' : 'off');
  settings.set('player/repeat', on);
  await TrackPlayer.setRepeatMode(on ? RepeatMode.Queue : RepeatMode.Off);
}

// Tracks played from index on: the queue replaced (shuffled after the first one when shuffle is on)
export async function playTracks(
  library: Library,
  tracks: Track[],
  index = 0,
  shuffled = shuffle,
) {
  try {
    await setupPlayer();
    const first = tracks[index];
    let order = tracks.filter(playable);
    if (!order.length) {
      console.warn(
        `[player] none of the ${tracks.length} songs can be played here`,
      );
      return;
    }
    let start = first && playable(first) ? order.indexOf(first) : 0;
    if (shuffled) {
      const lead = order[start];
      order = [lead, ...mixed(order.filter(t => t !== lead))];
      start = 0;
    }
    const items = order.map(t => toPlayer(library, t));
    console.log(
      `[player] play ${items.length} from ${start}: ${items[start].url}`,
    );
    await TrackPlayer.reset();
    await TrackPlayer.add(items);
    if (start > 0) {
      await TrackPlayer.skip(start);
    }
    await TrackPlayer.play();
  } catch (e: any) {
    console.warn(`[player] play failed: ${e?.code || ''} ${e?.message || e}`);
  }
}

// Added after the one playing (next) or at the end; played at once when nothing is queued
export async function enqueue(
  library: Library,
  tracks: Track[],
  next: boolean,
) {
  ui(next ? 'play next' : 'add to queue', `${tracks.length} songs`);
  await setupPlayer();
  const queue = await TrackPlayer.getQueue();
  const items = tracks.filter(playable).map(t => toPlayer(library, t));
  if (!items.length) {
    return;
  }
  if (!queue.length) {
    await playTracks(library, tracks, 0);
    return;
  }
  const active = await TrackPlayer.getActiveTrackIndex();
  await TrackPlayer.add(
    items,
    next && active !== undefined ? active + 1 : undefined,
  );
}

export async function togglePlay(playing: boolean) {
  ui(playing ? 'pause' : 'play');
  await setupPlayer();
  if (!playing && !(await TrackPlayer.getQueue()).length && current) {
    // nothing queued: the library shown, from its start (as the browser orders it), shuffled when shuffle is on
    const order = playOrder(current.tracks);
    await playTracks(
      current,
      order,
      shuffle ? Math.floor(Math.random() * order.length) : 0,
    );
    return;
  }
  await (playing ? TrackPlayer.pause() : TrackPlayer.play());
}

// Previous: the start of the song after its first seconds, as players do
export async function previous() {
  ui('previous');
  const { position } = await TrackPlayer.getProgress();
  if (position > 3) {
    await TrackPlayer.seekTo(0);
  } else {
    await TrackPlayer.skipToPrevious().catch(() => TrackPlayer.seekTo(0));
  }
}

export const next = () => {
  ui('next');
  return TrackPlayer.skipToNext().catch(() => {});
};

export const seek = (seconds: number) => {
  ui('seek', `${Math.round(seconds)}s`);
  return TrackPlayer.seekTo(seconds);
};

// The phone's own songs have their covers from the native side, later: the lock screen and the notification get
// the playing one's when it starts
export function useLocalArtwork() {
  const track = useActiveTrack() as QueuedTrack | undefined;
  useEffect(() => {
    if (!track || track.artwork || !isLocal(libraryOf(track))) {
      return;
    }
    let live = true;
    localCover(track.source, 512).then(async uri => {
      const index = await TrackPlayer.getActiveTrackIndex();
      if (live && uri && index !== undefined) {
        await TrackPlayer.updateMetadataForTrack(index, { artwork: uri }).catch(
          () => {},
        );
      }
    });
    return () => {
      live = false;
    };
  }, [track]);
}

// The libraries the queue's songs come from: known as they load (App), so a queue restored at start, or a song
// played from the history, has its covers, lyrics and levels
let current: Library | null = null; // the library shown: Play with nothing queued plays it

export function registerLibrary(library: Library) {
  libraries.set(library.address, library);
  current = library;
}

// Songs as they were queued (the history's, the queue kept), played: from index on
export async function playItems(items: QueuedTrack[], index = 0) {
  try {
    await setupPlayer();
    if (!items.length) {
      return;
    }
    console.log(
      `[player] play ${items.length} kept from ${index}: ${items[index]?.url}`,
    );
    await TrackPlayer.reset();
    await TrackPlayer.add(items);
    if (index > 0) {
      await TrackPlayer.skip(index);
    }
    await TrackPlayer.play();
  } catch (e: any) {
    console.warn(`[player] play failed: ${e?.code || ''} ${e?.message || e}`);
  }
}

export async function enqueueItems(items: QueuedTrack[], next: boolean) {
  ui(next ? 'play next' : 'add to queue', `${items.length} songs`);
  await setupPlayer();
  const queue = await TrackPlayer.getQueue();
  if (!queue.length) {
    await playItems(items, 0);
    return;
  }
  const active = await TrackPlayer.getActiveTrackIndex();
  await TrackPlayer.add(
    items,
    next && active !== undefined ? active + 1 : undefined,
  );
}

// What was played: newest first, HISTORY_SIZE kept (settings: player/history)
export type Played = { at: number; track: QueuedTrack };
const HISTORY_SIZE = 500;
let history: Played[] | null = null;
const historyListeners = new Set<(items: Played[]) => void>();

export async function loadHistory(): Promise<Played[]> {
  if (history === null) {
    history = await settings.get<Played[]>('player/history', []);
  }
  return history;
}

async function remember(track: QueuedTrack) {
  const items = await loadHistory();
  if (items[0]?.track.id === track.id && Date.now() - items[0].at < 60000) {
    return; // the same one again at once (a skip back to its start)
  }
  history = [{ at: Date.now(), track }, ...items].slice(0, HISTORY_SIZE);
  settings.set('player/history', history);
  historyListeners.forEach(listener => listener(history!));
}

export async function forgetPlayed(at: number) {
  history = (await loadHistory()).filter(item => item.at !== at);
  settings.set('player/history', history);
  historyListeners.forEach(listener => listener(history!));
}

export function useHistory(): Played[] {
  const [items, setItems] = useState<Played[]>(history || []);
  useEffect(() => {
    loadHistory().then(setItems);
    historyListeners.add(setItems);
    return () => {
      historyListeners.delete(setItems);
    };
  }, []);
  return items;
}

// The queue kept (settings: player/queue): its songs, which plays and where, saved as it changes and every few
// seconds while playing; given back at the next start, paused there
type Kept = { items: QueuedTrack[]; index: number; position: number };
const QUEUE_SIZE = 3000;

export async function saveQueue() {
  try {
    const items = (await TrackPlayer.getQueue()) as QueuedTrack[];
    const index = (await TrackPlayer.getActiveTrackIndex()) ?? 0;
    const { position } = await TrackPlayer.getProgress();
    // a very long queue: the part around the one playing
    const from = Math.max(
      0,
      Math.min(index - QUEUE_SIZE / 2, items.length - QUEUE_SIZE),
    );
    const kept: Kept = {
      items: items.slice(from, from + QUEUE_SIZE),
      index: index - from,
      position,
    };
    settings.set('player/queue', kept);
  } catch {
    // (the player not there: nothing to keep)
  }
}

export async function restoreQueue() {
  await setupPlayer();
  if ((await TrackPlayer.getQueue()).length) {
    return; // the player kept it (the app's JS reloaded, its service went on)
  }
  const kept = await settings.get<Kept | null>('player/queue', null);
  if (!kept?.items?.length) {
    return;
  }
  const index = Math.min(Math.max(0, kept.index), kept.items.length - 1);
  console.log(
    `[player] restore ${kept.items.length} at ${index} ${Math.round(
      kept.position,
    )}s`,
  );
  await TrackPlayer.add(kept.items);
  if (index > 0) {
    await TrackPlayer.skip(index);
  }
  if (kept.position > 1) {
    await TrackPlayer.seekTo(kept.position);
  }
}

// In the app: the history and the queue kept as songs start, every 5 s while playing, and when the app goes to
// the background
export function useKeepPlayback() {
  const track = useActiveTrack() as QueuedTrack | undefined;
  const { playing } = useIsPlaying();
  useEffect(() => {
    if (track) {
      remember(track);
      saveQueue();
    }
  }, [track]);
  useEffect(() => {
    if (!playing) {
      saveQueue();
      return;
    }
    const timer = setInterval(saveQueue, 5000);
    return () => clearInterval(timer);
  }, [playing]);
  useEffect(() => {
    const subscription = AppState.addEventListener(
      'change',
      state => state !== 'active' && saveQueue(),
    );
    return () => subscription.remove();
  }, []);
}
