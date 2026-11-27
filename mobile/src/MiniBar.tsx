// The play bar under the library on a phone: what plays, play/pause, next; a tap opens the pane.

import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import {
  useActiveTrack,
  useIsPlaying,
  useProgress,
} from 'react-native-track-player';

import { Cover } from './Cover';
import { QueuedTrack, libraryOf, next, togglePlay } from './player';
import { usePalette } from './theme';
import { IconButton } from './ui';

export function MiniBar({ onOpen }: { onOpen: () => void }) {
  const p = usePalette();
  const track = useActiveTrack() as QueuedTrack | undefined;
  const { playing } = useIsPlaying();
  const { position, duration } = useProgress(1000);
  if (!track) {
    return null;
  }
  return (
    <Pressable
      onPress={onOpen}
      style={[styles.bar, { backgroundColor: p.visual }]}
    >
      <View
        style={[
          styles.progress,
          {
            width: `${duration ? (100 * position) / duration : 0}%`,
            backgroundColor: p.accent,
          },
        ]}
      />
      <Cover library={libraryOf(track)} rel={track.rel} size={44} />
      <View style={styles.words}>
        <Text
          numberOfLines={1}
          style={{ color: p.visualText, fontSize: 15, fontWeight: '600' }}
        >
          {track.title}
        </Text>
        <Text numberOfLines={1} style={{ color: p.visualDim, fontSize: 13 }}>
          {track.artist}
        </Text>
      </View>
      <IconButton
        name={playing ? 'pause' : 'play'}
        size={26}
        color={p.visualText}
        onPress={() => togglePlay(!!playing)}
        label="Play or pause"
      />
      <IconButton
        name="next"
        size={24}
        color={p.visualText}
        onPress={next}
        label="Next"
      />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  bar: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 10,
    paddingVertical: 8,
  },
  progress: { position: 'absolute', top: 0, left: 0, height: 2 },
  words: { flex: 1, marginHorizontal: 10 },
});
