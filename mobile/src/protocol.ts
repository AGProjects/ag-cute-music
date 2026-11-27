// A library shared on the LAN by AG CuteMusic (cutemusic/sharing.py), over HTTP:
//   GET /info                 {name, id, app, version, tracks, endpoints?}
//   GET /library              {name, id, tracks: [{tags..., path: relative to its music folder}]}
//   GET /file?path=<rel>      a file (ranges served); /file/<rel> on newer ones (AVPlayer goes by the extension)
//   GET /extras?path=<rel>    the .lrc/.txt of the same name and the images of its folder
//   GET /cover?path=<rel>     its cover as the player shows it (newer ones)
//   GET /lyrics?path=<rel>    its lyrics as the player reads them (newer ones)

import { Platform } from 'react-native';

import { isLocal, localLyrics, localUrl } from './local';
import { savedCover, savedEntry, savedLyrics, savedUrl } from './saved';

export const SERVICE_TYPE = 'agcutemusic'; // _agcutemusic._tcp
export const PORT = 47100;
const TIMEOUT = 20000;

export type Track = {
  rel: string; // its path in the library: the key
  title: string;
  artist: string;
  albumartist: string;
  album: string;
  genres: string[];
  year: number;
  track: number;
  disc: number;
  duration: number;
  colors: string[];
  mediaId?: number; // the phone's own (local.ts): its MediaStore id, album, time
  albumId?: number;
  modified?: number;
};

export type Library = {
  name: string;
  id: string;
  address: string; // http://host:port
  endpoints: string[];
  tracks: Track[];
  kind?: 'remote' | 'local' | 'saved'; // on the LAN (the default), the phone's own (local.ts), or saved from a LAN one (saved.ts)
  savedFrom?: string; // a saved library: the id of the LAN library its songs come from
};

// The id under which a library's songs are saved on the phone (saved.ts): its own, or the one it was saved from
const savedId = (library: Library) =>
  library.kind === 'saved' ? library.savedFrom! : library.id;

export const albumArtist = (t: Track) =>
  t.albumartist || t.artist || 'Unknown artist';
export const displayTitle = (t: Track) =>
  t.title || (t.rel.split('/').pop() || t.rel).replace(/\.[^.]+$/, '');
export const extension = (rel: string) =>
  (rel.match(/\.[^./]+$/)?.[0] || '').toLowerCase();

// What the platform's player plays (AVPlayer, ExoPlayer); the others are shown dimmed and skipped
const PLAYABLE: Record<string, Set<string>> = {
  ios: new Set([
    '.mp3',
    '.m4a',
    '.mp4',
    '.aac',
    '.alac',
    '.wav',
    '.aif',
    '.aiff',
    '.flac',
  ]),
  android: new Set([
    '.mp3',
    '.m4a',
    '.mp4',
    '.aac',
    '.wav',
    '.flac',
    '.ogg',
    '.oga',
    '.opus',
  ]),
};
// (the phone's own: what its media index holds, Android plays)
export const playable = (t: Track) =>
  t.mediaId !== undefined ||
  (PLAYABLE[Platform.OS] || PLAYABLE.android).has(extension(t.rel));

// A library's track by its path (the views know the path)
const byRel = new WeakMap<Library, Map<string, Track>>();
export function trackOf(library: Library, rel: string): Track | undefined {
  let found = byRel.get(library);
  if (!found) {
    found = new Map(library.tracks.map(t => [t.rel, t]));
    byRel.set(library, found);
  }
  return found.get(rel);
}

export function normalizeAddress(text: string): string {
  let value = text.trim();
  if (!value) {
    return '';
  }
  if (!/^https?:\/\//i.test(value)) {
    value = 'http://' + value;
  }
  const match = value.match(/^(https?:\/\/)(\[[^\]]+\]|[^/:]+)(:\d+)?/i);
  if (!match) {
    return '';
  }
  return `${match[1].toLowerCase()}${match[2]}${match[3] || ':' + PORT}`;
}

async function request(url: string, timeout = TIMEOUT): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) {
      throw new Error(`${response.status} ${url}`);
    }
    return response;
  } finally {
    clearTimeout(timer);
  }
}

const query = (address: string, endpoint: string, rel?: string) =>
  `${address}/${endpoint}` +
  (rel !== undefined ? `?path=${encodeURIComponent(rel)}` : '');

export async function info(address: string, timeout = 5000) {
  const response = await request(`${address}/info`, timeout);
  return (await response.json()) as {
    name: string;
    id: string;
    version: string;
    tracks: number;
    endpoints?: string[];
  };
}

function toTrack(entry: any): Track | null {
  const rel = typeof entry?.path === 'string' ? entry.path : '';
  if (!rel || rel.startsWith('/') || rel.split('/').includes('..')) {
    return null;
  }
  return {
    rel,
    title: entry.title || '',
    artist: entry.artist || '',
    albumartist: entry.albumartist || '',
    album: entry.album || '',
    genres: Array.isArray(entry.genres) ? entry.genres : [],
    year: Number(entry.year) || 0,
    track: Number(entry.track) || 0,
    disc: Number(entry.disc) || 0,
    duration: Number(entry.duration) || 0,
    colors: Array.isArray(entry.colors) ? entry.colors : [],
  };
}

export async function fetchLibrary(address: string): Promise<Library> {
  const about = await info(address).catch(() => null);
  const response = await request(`${address}/library`, 60000);
  const data = await response.json();
  const tracks = (data.tracks || []).map(toTrack).filter(Boolean) as Track[];
  return {
    name: data.name || about?.name || address,
    id: data.id || about?.id || address,
    address,
    endpoints: about?.endpoints || [],
    tracks,
  };
}

export function fileUrl(library: Library, rel: string): string {
  if (isLocal(library)) {
    const track = trackOf(library, rel);
    return track ? localUrl(track) : '';
  }
  // saved on the phone: played from there (the saved library's songs, and a LAN library's saved ones)
  const saved = savedUrl(savedId(library), rel);
  if (saved || library.kind === 'saved') {
    return saved || '';
  }
  if (library.endpoints.includes('file/')) {
    return `${library.address}/file/${rel
      .split('/')
      .map(encodeURIComponent)
      .join('/')}`;
  }
  return query(library.address, 'file', rel);
}

export const coverUrl = (library: Library, rel: string) =>
  savedCover(savedId(library), rel) ||
  (library.endpoints.includes('cover')
    ? query(library.address, 'cover', rel)
    : null);

export async function extras(library: Library, rel: string): Promise<string[]> {
  try {
    return (await (
      await request(query(library.address, 'extras', rel))
    ).json()) as string[];
  } catch {
    return [];
  }
}

const COVER_NAMES = [
  'cover',
  'folder',
  'front',
  'album',
  'albumart',
  'albumartsmall',
];
const IMAGE = /\.(jpe?g|png|webp|bmp|gif)$/i;

// An older library (no /cover): cover.jpg and the like in the song's folder, else its only image
export async function folderCover(
  library: Library,
  rel: string,
): Promise<string | null> {
  const folder = rel.includes('/') ? rel.slice(0, rel.lastIndexOf('/')) : '';
  const images = (await extras(library, rel)).filter(
    path =>
      IMAGE.test(path) &&
      (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '') ===
        folder,
  );
  const stem = (path: string) =>
    (path.split('/').pop() || '').replace(/\.[^.]+$/, '').toLowerCase();
  const found = COVER_NAMES.map(name =>
    images.find(path => stem(path) === name),
  ).find(Boolean);
  const chosen = found || (images.length === 1 ? images[0] : null);
  return chosen ? query(library.address, 'file', chosen) : null;
}

export async function lyrics(
  library: Library,
  rel: string,
): Promise<string | null> {
  if (isLocal(library)) {
    const track = trackOf(library, rel);
    return track ? localLyrics(track) : null;
  }
  if (savedEntry(savedId(library), rel) || library.kind === 'saved') {
    return savedLyrics(savedId(library), rel);
  }
  if (library.endpoints.includes('lyrics')) {
    try {
      return await (
        await request(query(library.address, 'lyrics', rel))
      ).text();
    } catch {
      return null;
    }
  }
  const sidecar = (await extras(library, rel)).find(path =>
    /\.(lrc|txt)$/i.test(path),
  );
  if (!sidecar) {
    return null;
  }
  try {
    return await (
      await request(query(library.address, 'file', sidecar))
    ).text();
  } catch {
    return null;
  }
}

// [(seconds, line)] of a synced .lrc, or null when it has no time stamps (library.py parse_lrc)
export function parseLrc(text: string): [number, string][] | null {
  const stamp = /\[(\d+):(\d+(?:[.:]\d+)?)\]/g;
  const lines: [number, string][] = [];
  for (const raw of text.split(/\r?\n/)) {
    const stamps = [...raw.matchAll(stamp)];
    if (!stamps.length) {
      continue;
    }
    const words = raw.replace(stamp, '').trim();
    for (const [, minutes, seconds] of stamps) {
      lines.push([
        Number(minutes) * 60 + Number(seconds.replace(':', '.')),
        words,
      ]);
    }
  }
  return lines.length ? lines.sort((a, b) => a[0] - b[0]) : null;
}

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds || 0));
  const mm = String(Math.floor(s / 60) % 60).padStart(2, '0');
  const ss = String(s % 60).padStart(2, '0');
  return s >= 3600
    ? `${Math.floor(s / 3600)}:${mm}:${ss}`
    : `${Math.floor(s / 60)}:${ss}`;
}
