// Portrait: two views stacked over the play bar fixed at the bottom — the cover on top; under it the VU meter
// (its style by the pills over it, or a tap on it) with the lyrics. The play bar's left button: the browser, to
// find a song; its right one: where the music comes from.

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { PanResponder, Pressable, StyleSheet, View } from 'react-native';
import { useActiveTrack } from 'react-native-track-player';

import { Cover } from './Cover';
import { Icon } from './icons';
import { useLiveLevels } from './levels';
import { useMeter } from './meter';
import { QueuedTrack, libraryOf } from './player';
import { LyricsStrip, useLyrics } from './LyricsStrip';
import { settings } from './settings';
import { usePalette } from './theme';
import { Transport } from './Transport';
import { VUMeter, VUStyle, VU_STYLES } from './VUMeter';
import { ui } from './log';

export function useVUStyle(): [VUStyle, (style: VUStyle) => void] {
  const [style, setStyle] = useState<VUStyle>('bars');
  useEffect(() => {
    settings
      .get<VUStyle>('visuals/vu_style', 'bars')
      .then(s => VU_STYLES.some(([k]) => k === s) && setStyle(s));
  }, []);
  return [
    style,
    (value: VUStyle) => {
      ui('vu style', value);
      setStyle(value);
      settings.set('visuals/vu_style', value);
    },
  ];
}

// Two views stacked: the cover on top and the VU meter under it; with lyrics, a swipe left or right goes to the
// next pair (cover + lyrics, VU meter + lyrics), the dots under them telling which
type Pane = 'cover' | 'vu' | 'lyrics';
const PAIRS: [Pane, Pane][] = [
  ['cover', 'vu'],
  ['cover', 'lyrics'],
  ['vu', 'lyrics'],
];
const LINE = 26; // a lyrics line, about (LyricsStrip at its first size)
const DOTS = 16;

export function Docked({
  onBrowse,
  onLibraries,
  active,
}: {
  onBrowse: () => void;
  onLibraries: () => void;
  active: boolean;
}) {
  const p = usePalette();
  const track = useActiveTrack() as QueuedTrack | undefined;
  const [style, setStyle] = useVUStyle();
  const [stack, setStack] = useState({ width: 0, height: 0 });
  const lyrics = useLyrics(track);
  const hasLyrics = !!(lyrics.timed?.length || lyrics.plain?.length);
  const [pair, setPair] = useState(0);
  useEffect(() => {
    settings.get('dock/pair', 0).then(value => setPair(value % PAIRS.length));
    settings.get('dock/cover', true).then(setCoverShown);
  }, []);
  // the cover shown or not (the disc at the top left): without it, the VU meter (and the lyrics) alone
  const [coverShown, setCoverShown] = useState(true);
  const showCover = (on: boolean) => {
    ui('cover', on ? 'shown' : 'hidden');
    setCoverShown(on);
    settings.set('dock/cover', on);
  };
  const swiping = hasLyrics && coverShown; // the pairs to swipe through: with the cover only
  const shown: Pane[] = coverShown
    ? hasLyrics
      ? PAIRS[pair]
      : PAIRS[0]
    : hasLyrics
    ? ['vu', 'lyrics']
    : ['vu'];
  const turn = (step: number) => {
    const next = (pair + step + PAIRS.length) % PAIRS.length;
    ui('views', PAIRS[next].join(' + '));
    setPair(next);
    settings.set('dock/pair', next);
  };
  // a swipe sideways (not a tap, not a scroll): to the next pair or back
  const turnRef = useRef(turn);
  turnRef.current = turn;
  const swipes = useRef(swiping);
  swipes.current = swiping;
  const pan = useMemo(
    () =>
      PanResponder.create({
        onMoveShouldSetPanResponderCapture: (_, g) =>
          swipes.current &&
          Math.abs(g.dx) > 24 &&
          Math.abs(g.dx) > Math.abs(g.dy) * 2,
        onPanResponderRelease: (_, g) => {
          if (Math.abs(g.dx) > 60) {
            turnRef.current(g.dx < 0 ? 1 : -1);
          }
        },
      }),
    [],
  );
  const read = useLiveLevels();
  const meter = useMeter(
    read,
    style === 'spectrum' ? 32 : 0,
    active && stack.width > 0 && shown.includes('vu'),
  );
  const nextStyle = () =>
    setStyle(
      VU_STYLES[
        (VU_STYLES.findIndex(([k]) => k === style) + 1) % VU_STYLES.length
      ][0],
    );

  const width = stack.width;
  const half = Math.max(
    0,
    (stack.height - (swiping ? DOTS : 0)) / shown.length,
  );
  const lines = Math.max(3, Math.floor((half - 50) / LINE));

  const pane = (which: Pane) => {
    if (which === 'cover') {
      return (
        <View key="cover" style={[styles.centre, { height: half }]}>
          <Cover
            library={libraryOf(track)}
            rel={track?.rel}
            size={Math.max(0, Math.min(width, half - 8))}
            artist={track?.source?.artist || track?.artist}
            album={track?.source?.album}
            radius={6}
          />
        </View>
      );
    }
    if (which === 'vu') {
      return (
        <View key="vu" style={{ height: half }}>
          <VUMeter
            style={style}
            width={width}
            height={half}
            meter={meter}
            onPress={nextStyle}
          />
          {/* its style: the next one (as a tap on the meter) */}
          <Pressable
            accessibilityLabel="Next VU meter style"
            onPress={nextStyle}
            hitSlop={8}
            style={styles.styleButton}
          >
            <Icon name={style} size={16} color={p.visualDim} />
          </Pressable>
        </View>
      );
    }
    return (
      <View key="lyrics" style={[styles.centre, { height: half }]}>
        <View style={{ alignSelf: 'stretch' }}>
          <LyricsStrip
            track={track}
            found={lyrics}
            lines={lines}
            before={Math.floor((lines - 1) / 2)}
          />
        </View>
      </View>
    );
  };

  return (
    <View style={[styles.column, { backgroundColor: p.visual }]}>
      <View
        style={styles.flex}
        onLayout={e => setStack(e.nativeEvent.layout)}
        {...pan.panHandlers}
      >
        {width > 0 && (
          <>
            {shown.map(pane)}
            {/* the cover on and off */}
            <Pressable
              accessibilityLabel={
                coverShown ? 'Hide the cover' : 'Show the cover'
              }
              onPress={() => showCover(!coverShown)}
              hitSlop={8}
              style={[styles.coverButton, coverShown && styles.coverButtonOn]}
            >
              <Icon
                name="disc"
                size={16}
                color={coverShown ? p.visualText : p.visualDim}
              />
            </Pressable>
            {swiping && (
              <View style={[styles.dots, { height: DOTS }]}>
                {PAIRS.map((_, index) => (
                  <View
                    key={index}
                    style={[
                      styles.dot,
                      {
                        backgroundColor:
                          index === pair ? p.visualText : p.visualDim,
                        opacity: index === pair ? 1 : 0.4,
                      },
                    ]}
                  />
                ))}
              </View>
            )}
          </>
        )}
      </View>
      <Transport
        left={['browse', 'Browser', onBrowse]}
        right={['lan', 'Libraries', onLibraries]}
        output
      />
    </View>
  );
}

const styles = StyleSheet.create({
  column: { flex: 1 },
  flex: { flex: 1 },
  centre: { alignItems: 'center', justifyContent: 'center' },
  styleButton: {
    position: 'absolute',
    top: 6,
    right: 8,
    width: 30,
    height: 30,
    borderRadius: 15,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(255,255,255,0.08)',
  },
  coverButton: {
    position: 'absolute',
    top: 6,
    left: 8,
    width: 30,
    height: 30,
    borderRadius: 15,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(255,255,255,0.08)',
  },
  coverButtonOn: { backgroundColor: 'rgba(255,255,255,0.18)' },
  dots: {
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'center',
  },
  dot: { width: 6, height: 6, borderRadius: 3, marginHorizontal: 4 },
});
