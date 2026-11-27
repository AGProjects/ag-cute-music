// The phone's own audio (android/.../MediaLibrary.kt): every audio file Android's media index knows, made into
// sources (SOURCES: Music, Messenger audio, Recordings, Other audio; never the system's sounds), each a Library like
// a LAN one, played from content:// URIs. Their covers, lyrics and VU levels come from the native module.

import { NativeModules, PermissionsAndroid, Platform } from 'react-native';

import type { Library, Track } from './protocol';

const native = NativeModules.MediaLibrary as
  | {
      tracks(folder: string): Promise<any[]>;
      cover(id: number, albumId: number, size: number): Promise<string | null>;
      lyrics(id: number): Promise<string | null>;
      levels(id: number, modified: number): Promise<string>;
    }
  | undefined;

export const LOCAL = 'local'; // the local library's id and address
export const localAvailable = Platform.OS === 'android' && !!native;

export async function permitted(ask: boolean): Promise<boolean> {
  if (Platform.OS !== 'android') {
    return false;
  }
  const permission =
    Number(Platform.Version) >= 33
      ? PermissionsAndroid.PERMISSIONS.READ_MEDIA_AUDIO
      : PermissionsAndroid.PERMISSIONS.READ_EXTERNAL_STORAGE;
  if (await PermissionsAndroid.check(permission)) {
    return true;
  }
  return (
    ask &&
    (await PermissionsAndroid.request(permission)) ===
      PermissionsAndroid.RESULTS.GRANTED
  );
}

// The sources the phone's audio is sorted into (address: local, local:messages...)
export type Source = 'music' | 'messages' | 'recordings' | 'other';
export const SOURCES: [Source, string, string][] = [
  ['music', 'Music', 'This phone'],
  ['messages', 'Messenger audio', 'WhatsApp, Telegram, Signal…'],
  ['recordings', 'Recordings', 'the recorder, voice notes, calls'],
  ['other', 'Other audio', 'apps, podcasts, audiobooks, other files'],
];
export const sourceAddress = (source: Source) =>
  source === 'music' ? LOCAL : `${LOCAL}:${source}`;
export const sourceOf = (address: string): Source | null => {
  if (address === LOCAL) {
    return 'music';
  }
  const source = address.startsWith(`${LOCAL}:`)
    ? address.slice(LOCAL.length + 1)
    : '';
  return SOURCES.some(([key]) => key === source) ? (source as Source) : null;
};

const MESSENGERS =
  /(^|\/)(WhatsApp|Telegram|Signal|Viber|Messenger)( [^/]*)?\//i;
const RECORDINGS =
  /^Recordings\/|(^|\/)(Call ?recordings?|Voice ?(notes|recorder|recordings?)|Sound ?recordings?)\//i;
const SYSTEM = /^(Notifications|Ringtones|Alarms)\//i;
const OTHER = /^(Android|Podcasts|Audiobooks)\//i;

// Where a file goes, by its path and what Android says it is (kinds); null: the system's sounds, nowhere
function sourceOfRow(path: string, kinds: string[]): Source | null {
  if (
    SYSTEM.test(path) ||
    ['ringtone', 'notification', 'alarm'].some(k => kinds.includes(k))
  ) {
    return null;
  }
  if (MESSENGERS.test(path)) {
    return 'messages';
  }
  if (RECORDINGS.test(path) || kinds.includes('recording')) {
    return 'recordings';
  }
  if (kinds.includes('music') && !OTHER.test(path)) {
    return 'music';
  }
  return 'other';
}
export const isMusicPath = (path: string) =>
  sourceOfRow(path, ['music']) === 'music';

let rowsCache: { at: number; rows: any[] } | null = null;
async function allRows(): Promise<any[]> {
  if (rowsCache && Date.now() - rowsCache.at < 5000) {
    return rowsCache.rows;
  }
  const rows = await native!.tracks('');
  rowsCache = { at: Date.now(), rows };
  return rows;
}

// How many files each source has (the Libraries sheet: only those with some)
export async function localCounts(): Promise<Record<Source, number> | null> {
  if (!native || !(await permitted(false))) {
    return null;
  }
  const counts: Record<Source, number> = {
    music: 0,
    messages: 0,
    recordings: 0,
    other: 0,
  };
  for (const row of await allRows()) {
    const source = sourceOfRow(row.path, row.kinds || []);
    if (source) {
      counts[source]++;
    }
  }
  return counts;
}

export async function loadLocal(address: string = LOCAL): Promise<Library> {
  if (!native) {
    throw new Error('This phone’s music is not available here yet');
  }
  if (!(await permitted(true))) {
    throw new Error(
      'AG CuteMusic may not read the music on this phone (Settings → Apps → AG CuteMusic → Permissions)',
    );
  }
  const source = sourceOf(address) || 'music';
  const rows = (await allRows()).filter(
    row => sourceOfRow(row.path, row.kinds || []) === source,
  );
  const tracks: Track[] = rows.map(row => ({
    rel: row.path,
    title: row.title || '',
    artist: row.artist || '',
    albumartist: row.albumartist || '',
    album: row.album || '',
    genres: row.genre
      ? String(row.genre)
          .split(/\s*[;/]\s*/)
          .filter(Boolean)
      : [],
    year: row.year || 0,
    track: row.track || 0,
    disc: row.disc || 0,
    duration: row.duration || 0,
    colors: [],
    mediaId: row.id,
    albumId: row.albumId,
    modified: row.modified,
  }));
  const [, label] = SOURCES.find(([key]) => key === source)!;
  return {
    name: source === 'music' ? 'This phone' : label,
    id: sourceAddress(source),
    address: sourceAddress(source),
    endpoints: [],
    tracks,
    kind: 'local',
  };
}

export const isLocal = (library: Library | undefined | null) =>
  library?.kind === 'local';

export function localUrl(track: Track) {
  return `content://media/external/audio/media/${track.mediaId}`;
}

export async function localCover(
  track: Track,
  size: number,
): Promise<string | null> {
  return native && track.mediaId
    ? native
        .cover(track.mediaId, track.albumId || 0, Math.round(size))
        .catch(() => null)
    : null;
}

export async function localLyrics(track: Track): Promise<string | null> {
  return native && track.mediaId
    ? native.lyrics(track.mediaId).catch(() => null)
    : null;
}

export async function localLevels(track: Track): Promise<any | null> {
  if (!native || !track.mediaId) {
    return null;
  }
  try {
    return JSON.parse(await native.levels(track.mediaId, track.modified || 0));
  } catch {
    return null;
  }
}
