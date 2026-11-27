// The library as a tree, by year, artist, album or folder, as the desktop's browser builds it (cutemusic/browser.py).

import { MARKERS } from './theme';
import { Track, albumArtist, displayTitle, formatDuration } from './protocol';

export type Mode = 'year' | 'artist' | 'album' | 'folder';
export const MODES: [Mode, string][] = [
  ['year', 'Year'],
  ['artist', 'Artist'],
  ['album', 'Album'],
  ['folder', 'Folder'],
];
export const UNKNOWN_GENRE = 'Unknown genre';

export type Node = {
  key: string; // unique among its siblings: the path of keys finds it again after a rebuild
  name: string;
  detail: string; // the time column
  tracks: Track[]; // a group: its tracks in play order; a song: its siblings
  track?: Track; // a song
  children?: Node[];
  colors: string[];
};

const lower = (s: string) => s.toLowerCase();
const cmp = (a: any[], b: any[]): number => {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if (a[i] < b[i]) {
      return -1;
    }
    if (a[i] > b[i]) {
      return 1;
    }
  }
  return 0;
};
const sortKey = (t: Track) => [
  t.disc || 0,
  t.track || 0,
  lower(displayTitle(t)),
];
const bySortKey = (a: Track, b: Track) => cmp(sortKey(a), sortKey(b));
const dirname = (rel: string) =>
  rel.includes('/') ? rel.slice(0, rel.lastIndexOf('/')) : '';
const basename = (rel: string) => rel.split('/').pop() || rel;

export function playOrder(tracks: Track[]) {
  const key = (t: Track) => [
    lower(albumArtist(t)),
    t.year || 0,
    lower(t.album || ''),
    ...sortKey(t),
  ];
  return [...tracks].sort((a, b) => cmp(key(a), key(b)));
}

function commonColors(tracks: Track[]): string[] {
  if (!tracks.length) {
    return [];
  }
  let common = new Set(tracks[0].colors || []);
  for (const t of tracks.slice(1)) {
    common = new Set([...common].filter(c => (t.colors || []).includes(c)));
    if (!common.size) {
      return [];
    }
  }
  return MARKERS.map(([name]) => name).filter(name => common.has(name));
}

const total = (tracks: Track[]) =>
  formatDuration(tracks.reduce((sum, t) => sum + (t.duration || 0), 0));

function group(
  name: string,
  members: Track[],
  children: Node[],
  ordered?: Track[],
): Node {
  return {
    key: name,
    name,
    detail: `${members.length}`,
    tracks: ordered || playOrder(members),
    children,
    colors: commonColors(members),
  };
}

function song(t: Track, siblings: Track[]): Node {
  let title = displayTitle(t);
  if (t.artist && t.artist !== albumArtist(t)) {
    title += ` – ${t.artist}`;
  }
  const number = t.track ? `${String(t.track).padStart(2, '0')}  ` : '';
  return {
    key: t.rel,
    name: number + title,
    detail: formatDuration(t.duration),
    tracks: siblings,
    track: t,
    colors: t.colors || [],
  };
}

// what joins artists: the first one leads (a / or a dash only with spaces around it: AC/DC and Jay-Z are one)
const JOINS =
  /\s*(?:&|\+|,|;|\s\/\s|\s[-–—]\s|\s(?:and|with|feat\.?|ft\.?|featuring|vs\.?|y|e|et|und)\s)\s*/i;
const leadArtist = (name: string) =>
  (' ' + name + ' ').split(JOINS)[0].trim() || name;

const DISC_FOLDER = /^(cd|dis[ck])\s*\d+$/i;
const albumFolder = (t: Track) => {
  const folder = dirname(t.rel);
  return DISC_FOLDER.test(basename(folder)) ? dirname(folder) : folder;
};

function albums(tracks: Track[], how: 'year' | 'artist' | 'title'): Node[] {
  const groups = new Map<string, Track[]>();
  for (const t of tracks) {
    const key =
      how === 'title'
        ? `${lower(t.album || '')}\u0000${albumFolder(t)}`
        : `${lower(albumArtist(t))}\u0000${lower(t.album || '')}`;
    groups.set(key, [...(groups.get(key) || []), t]);
  }
  const keys = [...groups.keys()];
  const order = (k: string): any[] => {
    const [a, b] = k.split('\u0000');
    if (how === 'title') {
      return [a || '￿', lower(albumArtist(groups.get(k)![0])), b];
    }
    if (how === 'artist') {
      return [a, b];
    }
    return [groups.get(k)![0].year || 0, b];
  };
  keys.sort((x, y) => cmp(order(x), order(y)));
  return keys.map(k => {
    const members = [...groups.get(k)!].sort(bySortKey);
    const first = members[0];
    let name = first.album || 'Unknown album';
    if (how === 'title') {
      const names = new Set(members.map(t => lower(albumArtist(t))));
      name = `${name} – ${
        names.size === 1 ? albumArtist(first) : 'Various artists'
      }`;
    } else if (how === 'artist') {
      name = `${albumArtist(first)} – ${name}`;
    } else if (first.year) {
      name = `${name} (${first.year})`;
    }
    const node = group(
      name,
      members,
      members.map(t => song(t, members)),
      members,
    );
    node.key = k;
    node.detail = total(members);
    return node;
  });
}

function artists(tracks: Track[]): Node[] {
  const byArtist = new Map<string, [string, Track[]]>();
  for (const t of tracks) {
    const key = lower(albumArtist(t));
    const entry = byArtist.get(key) || [albumArtist(t), []];
    entry[1].push(t);
    byArtist.set(key, entry);
  }
  const leads = new Map<string, [string, string[]]>();
  for (const [key, [name]] of byArtist) {
    const lead = leadArtist(name);
    const entry = leads.get(lower(lead)) || [lead, []];
    entry[1].push(key);
    leads.set(lower(lead), entry);
  }
  const entries: [string, string, string[], boolean][] = [];
  for (const [leadKey, [lead, keys]] of leads) {
    if (keys.length > 1) {
      const exact = keys.find(k => k === leadKey);
      entries.push([
        leadKey,
        exact ? byArtist.get(exact)![0] : lead,
        keys,
        true,
      ]);
    } else {
      entries.push([keys[0], byArtist.get(keys[0])![0], keys, false]);
    }
  }
  entries.sort((a, b) => cmp([a[0]], [b[0]]));
  return entries.map(([sortKey_, name, keys, grouped]) => {
    if (!grouped) {
      const members = byArtist.get(keys[0])![1];
      return group(name, members, albums(members, 'year'));
    }
    const members = keys.flatMap(k => byArtist.get(k)![1]);
    const subs = [...keys]
      .sort((a, b) =>
        cmp([a !== sortKey_ ? 1 : 0, a], [b !== sortKey_ ? 1 : 0, b]),
      )
      .map(k => {
        const [subName, subMembers] = byArtist.get(k)!;
        return group(subName, subMembers, albums(subMembers, 'year'));
      });
    return group(name, members, subs);
  });
}

function years(tracks: Track[]): Node[] {
  const groups = new Map<number, Track[]>();
  for (const t of tracks) {
    groups.set(t.year || 0, [...(groups.get(t.year || 0) || []), t]);
  }
  return [...groups.keys()]
    .sort((a, b) => b - a)
    .map(year => {
      const members = groups.get(year)!;
      return group(
        year ? String(year) : 'Unknown year',
        members,
        albums(members, 'artist'),
      );
    });
}

function folders(tracks: Track[]): Node[] {
  type Folder = { node: Node; sub: Map<string, Folder> };
  const root: Folder = { node: group('', [], []), sub: new Map() };
  const byFolder = new Map<string, Track[]>();
  for (const t of tracks) {
    byFolder.set(dirname(t.rel), [...(byFolder.get(dirname(t.rel)) || []), t]);
  }
  const all: [string, Folder][] = [];
  const folderOf = (path: string): Folder => {
    if (!path) {
      return root;
    }
    const parent = folderOf(dirname(path));
    const name = basename(path);
    let found = parent.sub.get(name);
    if (!found) {
      found = { node: group(name, [], []), sub: new Map() };
      parent.sub.set(name, found);
      parent.node.children!.push(found.node);
      all.push([path, found]);
    }
    return found;
  };
  for (const path of [...byFolder.keys()].sort((a, b) =>
    cmp([lower(a)], [lower(b)]),
  )) {
    const members = [...byFolder.get(path)!].sort(bySortKey);
    folderOf(path).node.children!.push(...members.map(t => song(t, members)));
  }
  for (const [path, folder] of all) {
    const under = [...byFolder.entries()]
      .filter(([p]) => p === path || p.startsWith(path + '/'))
      .flatMap(([, members]) => members)
      .sort((a, b) =>
        cmp(
          [lower(dirname(a.rel)), ...sortKey(a)],
          [lower(dirname(b.rel)), ...sortKey(b)],
        ),
      );
    Object.assign(folder.node, {
      tracks: under,
      detail: `${under.length}`,
      colors: commonColors(under),
    });
  }
  return root.node.children!;
}

export function buildTree(tracks: Track[], mode: Mode): Node[] {
  switch (mode) {
    case 'year':
      return years(tracks);
    case 'artist':
      return artists(tracks);
    case 'album':
      return albums(tracks, 'title');
    default:
      return folders(tracks);
  }
}

export type Filter = { text: string; genres: Set<string>; colors: Set<string> };

export function filterTracks(tracks: Track[], filter: Filter): Track[] {
  let result = tracks;
  if (filter.genres.size) {
    const wanted = new Set([...filter.genres].map(lower));
    result = result.filter(t =>
      (t.genres.length ? t.genres : [UNKNOWN_GENRE]).some(g =>
        wanted.has(lower(g)),
      ),
    );
  }
  if (filter.colors.size) {
    result = result.filter(t =>
      (t.colors || []).some(c => filter.colors.has(c)),
    );
  }
  const words = filter.text.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length) {
    result = result.filter(t => {
      const text = [
        displayTitle(t),
        t.artist,
        t.albumartist,
        t.album,
        t.genres.join(' '),
        t.year || '',
        t.rel,
      ]
        .join(' ')
        .toLowerCase();
      return words.every(w => text.includes(w));
    });
  }
  return result;
}

export function genresOf(tracks: Track[]): string[] {
  const all = new Set<string>();
  for (const t of tracks) {
    (t.genres.length ? t.genres : [UNKNOWN_GENRE]).forEach(g => all.add(g));
  }
  return [...all].sort((a, b) =>
    a === UNKNOWN_GENRE ? 1 : b === UNKNOWN_GENRE ? -1 : a.localeCompare(b),
  );
}

// The node a path of keys leads to, after a rebuild (null: gone)
export function resolve(roots: Node[], path: string[]): Node[] | null {
  const chain: Node[] = [];
  let level = roots;
  for (const key of path) {
    const found = level.find(n => n.key === key);
    if (!found || !found.children) {
      return null;
    }
    chain.push(found);
    level = found.children;
  }
  return chain;
}
