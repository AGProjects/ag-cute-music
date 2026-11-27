// Songs of a LAN library saved on the phone (Save to this phone, in the browser): each with its cover, lyrics and
// VU levels, played from the phone (offline, and at home too), the saved ones of a library a Library of their own
// (the Libraries sheet: Saved on this phone). Kept under the app's documents: saved/<library id>/ — files/<path>,
// covers/<path>.jpg, lyrics/<path>.txt, levels/<path>.json — and a catalog.json of what is there.

import { useEffect, useState } from 'react';
import {
  DocumentDirectoryPath,
  downloadFile,
  exists,
  mkdir,
  moveFile,
  readFile,
  stopDownload,
  unlink,
  writeFile,
} from '@dr.pogodin/react-native-fs';

import { ui } from './log';
import { fileUrl } from './protocol';
import type { Library, Track } from './protocol';

const ROOT = `${DocumentDirectoryPath}/saved`;
export const SAVED = 'saved'; // a saved library's address: saved:<library id>

type Entry = Track & {
  cover?: boolean;
  lyricsText?: boolean;
  levelsJson?: boolean;
};
type Catalog = {
  id: string;
  name: string;
  address: string;
  tracks: Record<string, Entry>;
};

const catalogs = new Map<string, Catalog>();
let loaded: Promise<void> | null = null;
const listeners = new Set<() => void>();
const changed = () => listeners.forEach(listener => listener());

const safe = (id: string) => id.replace(/[^\w.-]/g, '_');
const dir = (id: string) => `${ROOT}/${safe(id)}`;
const parent = (path: string) => path.slice(0, path.lastIndexOf('/'));
export const savedFile = (id: string, rel: string) => `${dir(id)}/files/${rel}`;
const coverFile = (id: string, rel: string) => `${dir(id)}/covers/${rel}.jpg`;
const lyricsFile = (id: string, rel: string) => `${dir(id)}/lyrics/${rel}.txt`;
const levelsFile = (id: string, rel: string) => `${dir(id)}/levels/${rel}.json`;
const uri = (path: string) =>
  `file://${path.split('/').map(encodeURIComponent).join('/')}`;

export function loadSaved(): Promise<void> {
  if (!loaded) {
    loaded = (async () => {
      try {
        const index = JSON.parse(
          await readFile(`${ROOT}/index.json`, 'utf8'),
        ) as string[];
        for (const id of index) {
          try {
            catalogs.set(
              id,
              JSON.parse(await readFile(`${dir(id)}/catalog.json`, 'utf8')),
            );
          } catch {
            // (a catalog gone: its library forgotten)
          }
        }
      } catch {
        // nothing saved yet
      }
      changed();
    })();
  }
  return loaded;
}

let writing: ReturnType<typeof setTimeout> | null = null;
function persist() {
  if (writing) {
    clearTimeout(writing);
  }
  writing = setTimeout(async () => {
    writing = null;
    try {
      await mkdir(ROOT);
      await writeFile(
        `${ROOT}/index.json`,
        JSON.stringify([...catalogs.keys()]),
        'utf8',
      );
      for (const catalog of catalogs.values()) {
        await mkdir(dir(catalog.id));
        await writeFile(
          `${dir(catalog.id)}/catalog.json`,
          JSON.stringify(catalog),
          'utf8',
        );
      }
    } catch (e: any) {
      console.warn(`[saved] cannot keep the catalog: ${e?.message || e}`);
    }
  }, 1000);
}

// What a library (by its id) has saved: its entry of rel, or undefined
export const savedEntry = (id: string, rel: string): Entry | undefined =>
  catalogs.get(id)?.tracks[rel];
export const savedCount = (id: string, tracks: Track[]) =>
  tracks.filter(t => savedEntry(id, t.rel)).length;
export const savedCover = (id: string, rel: string) =>
  savedEntry(id, rel)?.cover ? uri(coverFile(id, rel)) : null;
export const savedUrl = (id: string, rel: string) =>
  savedEntry(id, rel) ? uri(savedFile(id, rel)) : null;

export async function savedLyrics(
  id: string,
  rel: string,
): Promise<string | null> {
  return savedEntry(id, rel)?.lyricsText
    ? readFile(lyricsFile(id, rel), 'utf8').catch(() => null)
    : null;
}

export async function savedLevels(
  id: string,
  rel: string,
): Promise<any | null> {
  if (!savedEntry(id, rel)?.levelsJson) {
    return null;
  }
  try {
    return JSON.parse(await readFile(levelsFile(id, rel), 'utf8'));
  } catch {
    return null;
  }
}

// The libraries with songs saved: [{id, name, count}]
export function savedLibraries() {
  return [...catalogs.values()]
    .map(c => ({ id: c.id, name: c.name, count: Object.keys(c.tracks).length }))
    .filter(c => c.count > 0)
    .sort((a, b) => a.name.localeCompare(b.name));
}

// A library's saved songs, as a Library (played and shown from the phone)
export function savedLibrary(address: string): Library {
  const id = address.slice(SAVED.length + 1);
  const catalog = catalogs.get(id);
  if (!catalog) {
    throw new Error('Nothing saved from this library');
  }
  return {
    name: `${catalog.name} · saved`,
    id: address,
    address,
    endpoints: [],
    tracks: Object.values(catalog.tracks),
    kind: 'saved',
    savedFrom: id,
  };
}

// The saving: one song after the other, each with its cover, lyrics and levels

type Job = { library: Library; track: Track };
const queue: Job[] = [];
let running = false;
let current: { jobId: number } | null = null;
let progress = { done: 0, total: 0, title: '' };

async function get(url: string, path: string): Promise<boolean> {
  await mkdir(parent(path));
  const part = `${path}.part`;
  const job = downloadFile({ fromUrl: url, toFile: part });
  current = { jobId: job.jobId };
  try {
    const result = await job.promise;
    if (result.statusCode !== 200) {
      await unlink(part).catch(() => {});
      return false;
    }
    if (await exists(path)) {
      await unlink(path);
    }
    await moveFile(part, path);
    return true;
  } finally {
    current = null;
  }
}

const query = (library: Library, endpoint: string, rel: string) =>
  `${library.address}/${endpoint}?path=${encodeURIComponent(rel)}`;

async function run() {
  if (running) {
    return;
  }
  running = true;
  while (queue.length) {
    const { library, track } = queue.shift()!;
    progress = { ...progress, title: track.title || track.rel };
    changed();
    const id = library.id;
    try {
      if (!savedEntry(id, track.rel)) {
        if (await get(fileUrl(library, track.rel), savedFile(id, track.rel))) {
          const entry: Entry = { ...track };
          if (library.endpoints.includes('cover')) {
            entry.cover = await get(
              query(library, 'cover', track.rel),
              coverFile(id, track.rel),
            ).catch(() => false);
          }
          if (library.endpoints.includes('lyrics')) {
            entry.lyricsText = await get(
              query(library, 'lyrics', track.rel),
              lyricsFile(id, track.rel),
            ).catch(() => false);
          }
          if (library.endpoints.includes('levels')) {
            entry.levelsJson = await get(
              query(library, 'levels', track.rel),
              levelsFile(id, track.rel),
            ).catch(() => false);
          }
          let catalog = catalogs.get(id);
          if (!catalog) {
            catalog = {
              id,
              name: library.name,
              address: library.address,
              tracks: {},
            };
            catalogs.set(id, catalog);
          }
          catalog.name = library.name;
          catalog.address = library.address;
          catalog.tracks[track.rel] = entry;
          persist();
        } else {
          console.warn(`[saved] ${track.rel}: not saved`);
        }
      }
    } catch (e: any) {
      console.warn(`[saved] ${track.rel}: ${e?.message || e}`);
    }
    progress = { ...progress, done: progress.done + 1 };
    changed();
  }
  running = false;
  progress = { done: 0, total: 0, title: '' };
  changed();
}

export function saveTracks(library: Library, tracks: Track[]) {
  const wanted = tracks.filter(
    t =>
      !savedEntry(library.id, t.rel) && !queue.some(j => j.track.rel === t.rel),
  );
  ui('save to this phone', `${wanted.length} songs`);
  queue.push(...wanted.map(track => ({ library, track })));
  progress = { ...progress, total: progress.total + wanted.length };
  changed();
  run();
}

export function stopSaving() {
  ui('stop saving');
  queue.length = 0;
  if (current) {
    stopDownload(current.jobId);
  }
}

export async function removeSaved(id: string, tracks: Track[]) {
  ui('remove from this phone', `${tracks.length} songs`);
  const catalog = catalogs.get(id);
  if (!catalog) {
    return;
  }
  for (const t of tracks) {
    if (catalog.tracks[t.rel]) {
      for (const path of [
        savedFile(id, t.rel),
        coverFile(id, t.rel),
        lyricsFile(id, t.rel),
        levelsFile(id, t.rel),
      ]) {
        await unlink(path).catch(() => {});
      }
      delete catalog.tracks[t.rel];
    }
  }
  if (!Object.keys(catalog.tracks).length) {
    catalogs.delete(id);
    await unlink(dir(id)).catch(() => {});
  }
  persist();
  changed();
}

// For the views: re-rendered as songs are saved or removed; the saving's progress
export function useSaved() {
  const [, setTick] = useState(0);
  useEffect(() => {
    const listener = () => setTick(n => n + 1);
    listeners.add(listener);
    loadSaved();
    return () => {
      listeners.delete(listener);
    };
  }, []);
  return progress;
}
