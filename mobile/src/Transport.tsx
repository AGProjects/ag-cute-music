// What plays and the play bar, as the dock's (dock.py _now_playing): the title, artist · album, the position with
// a seek bar, previous / play / next; either side a button of the views'.

import React, { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import Slider from '@react-native-community/slider';
import {
  useActiveTrack,
  useIsPlaying,
  useProgress,
} from 'react-native-track-player';

import { IconName } from './icons';
import { QueuedTrack, next, previous, seek, togglePlay } from './player';
import { formatDuration } from './protocol';
import { OutputButton } from './OutputButton';
import { usePalette } from './theme';
import { IconButton } from './ui';

export function Transport({
  left,
  right,
  titles = true,
  output = false,
}: {
  left?: [IconName, string, () => void];
  right?: [IconName, string, () => void];
  titles?: boolean;
  output?: boolean; // the OUTPUT button beside the right one
}) {
  const p = usePalette();
  const track = useActiveTrack() as QueuedTrack | undefined;
  const { playing } = useIsPlaying();
  const { position, duration } = useProgress(500);
  const [seeking, setSeeking] = useState<number | null>(null);
  const shown = seeking ?? position;
  const total = duration || track?.duration || 0;
  return (
    <View style={styles.box}>
      {titles && (
        <>
          <Text
            numberOfLines={1}
            style={[styles.title, { color: p.visualText }]}
          >
            {track ? track.title : 'Nothing playing'}
          </Text>
          <Text
            numberOfLines={1}
            style={[styles.subtitle, { color: p.visualDim }]}
          >
            {track
              ? [track.artist, track.album].filter(Boolean).join(' · ')
              : ' '}
          </Text>
        </>
      )}
      <View style={styles.row}>
        <Text style={[styles.time, { color: p.visualDim }]}>
          {formatDuration(shown)}
        </Text>
        <Slider
          style={{ flex: 1, marginHorizontal: 4 }}
          minimumValue={0}
          maximumValue={Math.max(1, total)}
          value={shown}
          disabled={!track}
          minimumTrackTintColor={p.visualText}
          maximumTrackTintColor="#3a404d"
          thumbTintColor={p.visualText}
          onSlidingStart={setSeeking}
          onValueChange={value => seeking !== null && setSeeking(value)}
          onSlidingComplete={async value => {
            await seek(value);
            setSeeking(null);
          }}
        />
        <Text style={[styles.time, { color: p.visualDim }]}>
          {formatDuration(total)}
        </Text>
      </View>
      <View style={styles.buttons}>
        {left ? (
          <IconButton
            name={left[0]}
            label={left[1]}
            onPress={left[2]}
            color={p.visualDim}
          />
        ) : (
          <View style={styles.side} />
        )}
        <View style={styles.middle}>
          <IconButton
            name="previous"
            size={28}
            color={p.visualText}
            onPress={previous}
            label="Previous"
          />
          <IconButton
            name={playing ? 'pause' : 'play'}
            size={34}
            color={p.visualText}
            onPress={() => togglePlay(!!playing)}
            label="Play or pause"
            style={{ marginHorizontal: 18 }}
          />
          <IconButton
            name="next"
            size={28}
            color={p.visualText}
            onPress={next}
            label="Next"
          />
        </View>
        <View style={styles.ends}>
          {output && <OutputButton color={p.visualDim} />}
          {right ? (
            <IconButton
              name={right[0]}
              label={right[1]}
              onPress={right[2]}
              color={p.visualDim}
            />
          ) : (
            !output && <View style={styles.side} />
          )}
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  box: { paddingHorizontal: 14, paddingTop: 6, paddingBottom: 8 },
  title: { fontSize: 18, fontWeight: '700' },
  subtitle: { fontSize: 14, marginTop: 2 },
  row: { flexDirection: 'row', alignItems: 'center', marginTop: 4 },
  time: {
    fontSize: 12,
    minWidth: 38,
    textAlign: 'center',
    fontVariant: ['tabular-nums'],
  },
  buttons: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  middle: { flexDirection: 'row', alignItems: 'center' },
  side: { width: 34 },
  ends: { flexDirection: 'row', alignItems: 'center' },
});
