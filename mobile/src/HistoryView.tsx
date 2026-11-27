// The History tab (the desktop's): every song played, newest first, when; tap: play it; long press: Play next,
// Add to queue, Remove from the history.

import React from 'react';
import { FlatList, Pressable, StyleSheet, Text, View } from 'react-native';

import { ui } from './log';
import { enqueueItems, forgetPlayed, playItems, useHistory } from './player';
import { usePalette } from './theme';
import { showActions } from './ActionSheet';

function when(at: number) {
  const date = new Date(at);
  const time = date.toTimeString().slice(0, 5);
  const today = new Date();
  const days = Math.floor(
    (new Date(today.toDateString()).getTime() -
      new Date(date.toDateString()).getTime()) /
      86400000,
  );
  if (days === 0) {
    return time;
  }
  if (days === 1) {
    return `yesterday ${time}`;
  }
  return `${date.toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
  })} ${time}`;
}

export function HistoryView() {
  const p = usePalette();
  const items = useHistory();
  return (
    <FlatList
      style={{ backgroundColor: p.window }}
      data={items}
      keyExtractor={item => String(item.at)}
      ListEmptyComponent={
        <Text style={[styles.empty, { color: p.dim }]}>Nothing played yet</Text>
      }
      renderItem={({ item, index }) => (
        <Pressable
          onPress={() => {
            ui('history play', item.track.title);
            playItems([item.track]);
          }}
          onLongPress={() =>
            showActions(
              item.track.title || '',
              [
                {
                  text: 'Play next',
                  onPress: () => enqueueItems([item.track], true),
                },
                {
                  text: 'Add to queue',
                  onPress: () => enqueueItems([item.track], false),
                },
                {
                  text: 'Remove from the history',
                  destructive: true,
                  onPress: () => {
                    ui('history remove', item.track.title);
                    forgetPlayed(item.at);
                  },
                },
              ],
              item.track.artist,
            )
          }
          style={({ pressed }) => [
            styles.row,
            {
              backgroundColor: pressed
                ? p.highlight
                : index % 2
                ? p.alternate
                : p.base,
              borderBottomColor: p.line,
            },
          ]}
        >
          <View style={styles.words}>
            <Text numberOfLines={1} style={[styles.title, { color: p.text }]}>
              {item.track.title}
            </Text>
            <Text numberOfLines={1} style={[styles.subtitle, { color: p.dim }]}>
              {[item.track.artist, item.track.album]
                .filter(Boolean)
                .join(' — ')}
            </Text>
          </View>
          <Text style={[styles.when, { color: p.dim }]}>{when(item.at)}</Text>
        </Pressable>
      )}
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
  words: { flex: 1 },
  title: { fontSize: 15 },
  subtitle: { fontSize: 12, marginTop: 2 },
  when: { fontSize: 12, marginLeft: 8, fontVariant: ['tabular-nums'] },
  empty: { textAlign: 'center', marginTop: 40 },
});
