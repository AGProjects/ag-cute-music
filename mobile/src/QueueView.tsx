// The queue: what plays, in order; tap: play from there; long press: Remove, Play next, Clear the rest.

import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import TrackPlayer, {
  Event,
  useActiveTrack,
  useTrackPlayerEvents,
} from 'react-native-track-player';

import { QueuedTrack } from './player';
import { formatDuration } from './protocol';
import { usePalette } from './theme';
import { ui } from './log';

export function QueueView() {
  const p = usePalette();
  const active = useActiveTrack() as QueuedTrack | undefined;
  const [queue, setQueue] = useState<QueuedTrack[]>([]);
  const [current, setCurrent] = useState<number | undefined>(undefined);
  const refresh = useCallback(async () => {
    setQueue((await TrackPlayer.getQueue()) as QueuedTrack[]);
    setCurrent(await TrackPlayer.getActiveTrackIndex());
  }, []);
  useEffect(() => {
    refresh();
  }, [refresh, active]);
  useTrackPlayerEvents(
    [Event.PlaybackActiveTrackChanged, Event.PlaybackQueueEnded],
    refresh,
  );

  const menu = (index: number, track: QueuedTrack) =>
    Alert.alert(track.title || '', track.artist, [
      {
        text: 'Remove',
        style: 'destructive',
        onPress: () => {
          ui('queue remove', index);
          TrackPlayer.remove(index).then(refresh);
        },
      },
      ...(current !== undefined && index > current + 1
        ? [
            {
              text: 'Play next',
              onPress: () => {
                ui('queue play next', index);
                TrackPlayer.move(index, current + 1).then(refresh);
              },
            },
          ]
        : []),
      ...(current !== undefined
        ? [
            {
              text: 'Clear what comes after',
              onPress: () => {
                ui('queue clear after', current);
                TrackPlayer.removeUpcomingTracks().then(refresh);
              },
            },
          ]
        : []),
      { text: 'Cancel', style: 'cancel' },
    ]);

  return (
    <FlatList
      style={{ backgroundColor: p.window }}
      data={queue}
      keyExtractor={(item, index) => `${index}:${item.id}`}
      ListEmptyComponent={
        <Text style={[styles.empty, { color: p.dim }]}>The queue is empty</Text>
      }
      renderItem={({ item, index }) => {
        const playing = index === current;
        return (
          <Pressable
            onPress={() => {
              ui('queue play', `${index} ${item.title}`);
              TrackPlayer.skip(index).then(() => TrackPlayer.play());
            }}
            onLongPress={() => menu(index, item)}
            style={({ pressed }) => [
              styles.row,
              {
                backgroundColor: pressed
                  ? p.highlight
                  : index % 2
                  ? p.alternate
                  : p.base,
                borderBottomColor: p.line,
                opacity: current !== undefined && index < current ? 0.55 : 1,
              },
            ]}
          >
            <View style={{ flex: 1 }}>
              <Text
                numberOfLines={1}
                style={{
                  color: playing ? p.accent : p.text,
                  fontSize: 15,
                  fontWeight: playing ? '600' : '400',
                }}
              >
                {item.title}
              </Text>
              <Text
                numberOfLines={1}
                style={{ color: p.dim, fontSize: 12, marginTop: 2 }}
              >
                {[item.artist, item.album].filter(Boolean).join(' — ')}
              </Text>
            </View>
            <Text style={[styles.detail, { color: p.dim }]}>
              {formatDuration(item.duration || 0)}
            </Text>
          </Pressable>
        );
      }}
    />
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 9,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  detail: { fontSize: 13, marginLeft: 8, fontVariant: ['tabular-nums'] },
  empty: { textAlign: 'center', marginTop: 40 },
});
