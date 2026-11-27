// A few lines of the lyrics, the one sung among them (visuals.LyricsStrip): synced ones by their time stamps,
// plain ones by where the song is (the line at the same fraction of the text). Nothing shown when there are none.
// A− A+ at its bottom right, its size kept.

import React, { useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useProgress } from 'react-native-track-player';

import { QueuedTrack, libraryOf } from './player';
import { lyrics as fetchLyrics, parseLrc } from './protocol';
import { settings } from './settings';
import { usePalette } from './theme';
import { ui } from './log';

export type Lyrics = {
  timed: [number, string][] | null;
  plain: string[] | null;
};

export function useLyrics(track: QueuedTrack | undefined): Lyrics {
  const [found, setFound] = useState<Lyrics>({ timed: null, plain: null });
  useEffect(() => {
    let live = true;
    setFound({ timed: null, plain: null });
    const library = libraryOf(track);
    if (track && library) {
      fetchLyrics(library, track.rel).then(text => {
        if (!live || !text) {
          return;
        }
        const timed = parseLrc(text);
        setFound({
          timed,
          plain: timed
            ? null
            : text
                .split(/\r?\n/)
                .map(l => l.trim())
                .filter(Boolean),
        });
      });
    }
    return () => {
      live = false;
    };
  }, [track]);
  return found;
}

let scale = 1;
settings.get('visuals/lyrics_scale', 1).then(value => (scale = value));
const SCALES = [0.6, 2.5];
const STEP = 1.12;

export function LyricsStrip({
  track,
  lines = 4,
  before = 2,
  found,
}: {
  track: QueuedTrack | undefined;
  lines?: number;
  before?: number;
  found?: Lyrics; // already looked up (the docked view's, which needs to know whether there are any)
}) {
  const p = usePalette();
  const own = useLyrics(found ? undefined : track);
  const { timed, plain } = found ?? own;
  const { position, duration: played } = useProgress(250);
  const [size, setSize] = useState(scale);
  const all = timed ? timed.map(([, words]) => words) : plain || [];
  const duration = played || track?.duration || 0;
  if (!all.length) {
    return null;
  }
  let current = -1;
  if (timed) {
    for (let i = 0; i < timed.length && timed[i][0] <= position + 0.15; i++) {
      current = i;
    }
    if (current === timed.length - 1) {
      const starts = timed.map(([start]) => start);
      const gaps = starts
        .slice(1)
        .map((b, i) => b - starts[i])
        .filter(g => g > 0)
        .sort((a, b) => a - b);
      const line = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 5;
      if (
        position >
        starts[starts.length - 1] + Math.min(10, Math.max(4, line * 1.5))
      ) {
        current = all.length;
      }
    }
  } else if (duration) {
    current = Math.min(
      all.length - 1,
      Math.floor((position / duration) * all.length),
    );
  }
  if (duration && position >= duration - 1) {
    current = all.length;
  }
  const rescale = (factor: number) => {
    ui('lyrics size', factor > 1 ? 'A+' : 'A−');
    scale = Math.max(SCALES[0], Math.min(SCALES[1], scale * factor));
    settings.set('visuals/lyrics_scale', scale);
    setSize(scale);
  };
  const font = 17 * size;
  return (
    <View style={styles.strip}>
      {Array.from({ length: lines }, (_, row) => {
        const index = current - before + row;
        const words = index >= 0 && index < all.length ? all[index] : '';
        const sung = index === current;
        return (
          <Text
            key={row}
            numberOfLines={1}
            style={{
              color: sung ? p.visualText : p.visualDim,
              opacity: sung ? 1 : index < current ? 0.55 : 0.8,
              fontSize: font,
              fontWeight: sung ? '700' : '400',
              lineHeight: font * 1.5,
              textAlign: 'center',
            }}
          >
            {words || ' '}
          </Text>
        );
      })}
      <View style={styles.buttons}>
        {(
          [
            ['A−', 1 / STEP],
            ['A+', STEP],
          ] as [string, number][]
        ).map(([label, factor]) => (
          <Pressable
            key={label}
            hitSlop={4}
            onPress={() => rescale(factor)}
            style={styles.button}
          >
            <Text
              style={{ color: p.visualDim, fontSize: 17, fontWeight: '700' }}
            >
              {label}
            </Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  strip: { paddingHorizontal: 12, paddingBottom: 34 },
  buttons: { position: 'absolute', right: 8, bottom: 0, flexDirection: 'row' },
  // big enough for a finger, apart from each other
  button: {
    paddingHorizontal: 12,
    paddingVertical: 5,
    marginLeft: 14,
    borderRadius: 14,
    backgroundColor: 'rgba(255,255,255,0.08)',
  },
});
