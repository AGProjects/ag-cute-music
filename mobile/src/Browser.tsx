// The library: by Year, Artist, Album or Folder, the desktop's tree (a group opened in place, the others still
// there), with a filter, the genres and the color markers. A song: its group played from it; long press: Play, Play next, Add to queue.

import React, { useEffect, useMemo, useState } from 'react';
import {
  ActionSheetIOS,
  Alert,
  FlatList,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import {
  Mode,
  MODES,
  Node,
  buildTree,
  filterTracks,
  genresOf,
} from './library';
import { enqueue, isShuffle, playTracks } from './player';
import { Library, Track, playable } from './protocol';
import { settings } from './settings';
import { MARKERS, usePalette } from './theme';
import { Dots, IconButton, Segmented } from './ui';
import { Icon } from './icons';
import { ui } from './log';

function actions(title: string, choices: [string, () => void][]) {
  if (Platform.OS === 'ios') {
    ActionSheetIOS.showActionSheetWithOptions(
      {
        title,
        options: [...choices.map(([label]) => label), 'Cancel'],
        cancelButtonIndex: choices.length,
      },
      index => index < choices.length && choices[index][1](),
    );
  } else {
    Alert.alert(
      title,
      undefined,
      [
        ...choices.map(([text, onPress]) => ({ text, onPress })),
        { text: 'Cancel', style: 'cancel' as const },
      ],
      {
        cancelable: true,
      },
    );
  }
}

type Row = { node: Node; depth: number; id: string; open: boolean };

export function Browser({
  library,
  playingRel,
}: {
  library: Library;
  playingRel?: string;
}) {
  const p = usePalette();
  const [mode, setMode] = useState<Mode>('artist');
  const [text, setText] = useState('');
  const [genres, setGenres] = useState<Set<string>>(new Set());
  const [colors, setColors] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showGenres, setShowGenres] = useState(false);

  useEffect(() => {
    settings
      .get<Mode>('browser/mode', 'artist')
      .then(m => MODES.some(([k]) => k === m) && setMode(m));
  }, []);
  useEffect(() => setExpanded(new Set()), [library.id]);

  const allGenres = useMemo(() => genresOf(library.tracks), [library.tracks]);
  const shown = useMemo(
    () => filterTracks(library.tracks, { text, genres, colors }),
    [library.tracks, text, genres, colors],
  );
  const roots = useMemo(() => buildTree(shown, mode), [shown, mode]);
  // the tree as the desktop's: every group in the list, opened in place (a tap), its albums and songs under it;
  // filtering opens what matches (when it is not too much)
  const filtering = !!text.trim() || genres.size > 0 || colors.size > 0;
  const rows = useMemo(() => {
    const all: Row[] = [];
    const openAll = filtering && shown.length <= 300;
    const walk = (nodes: Node[], depth: number, parent: string) => {
      for (const node of nodes) {
        const id = `${parent}\u0000${node.key}`;
        const open = !!node.children && (openAll || expanded.has(id));
        all.push({ node, depth, id, open });
        if (open) {
          walk(node.children!, depth + 1, id);
        }
      }
    };
    walk(roots, 0, '');
    return all;
  }, [roots, expanded, filtering, shown.length]);
  const flip = (id: string) => {
    const next = new Set(expanded);
    next.has(id) ? next.delete(id) : next.add(id);
    setExpanded(next);
  };

  const chooseMode = (m: Mode) => {
    setMode(m);
    setExpanded(new Set());
    settings.set('browser/mode', m);
  };
  const toggle = (
    set: Set<string>,
    value: string,
    apply: (s: Set<string>) => void,
  ) => {
    const copy = new Set(set);
    copy.has(value) ? copy.delete(value) : copy.add(value);
    apply(copy);
  };

  const menu = (node: Node) => {
    ui('menu', node.name.trim());
    const tracks: Track[] = node.track ? [node.track] : node.tracks;
    actions(node.name.trim(), [
      ['Play', () => playTracks(library, tracks, 0)],
      ['Play next', () => enqueue(library, tracks, true)],
      ['Add to queue', () => enqueue(library, tracks, false)],
    ]);
  };

  const press = (row: Row) => {
    const node = row.node;
    ui(
      node.children ? (row.open ? 'close' : 'open') : 'play song',
      node.name.trim(),
    );
    if (node.children) {
      flip(row.id);
    } else if (node.track) {
      playTracks(library, node.tracks, node.tracks.indexOf(node.track));
    }
  };

  const playAll = (shuffled: boolean) => {
    ui(
      shuffled ? 'shuffle all' : 'play all',
      filtering ? 'filtered' : 'library',
    );
    const tracks = rows.length ? roots.flatMap(n => n.tracks) : shown;
    playTracks(
      library,
      tracks,
      shuffled ? Math.floor(Math.random() * tracks.length) : 0,
      shuffled || isShuffle(),
    );
  };

  const renderItem = ({ item: row, index }: { item: Row; index: number }) => {
    const item = row.node;
    const disabled = !!item.track && !playable(item.track);
    const playing = !!item.track && item.track.rel === playingRel;
    return (
      <Pressable
        onPress={() => !disabled && press(row)}
        onLongPress={() => !disabled && menu(item)}
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
        <View style={{ width: row.depth * 18 }} />
        {item.children ? (
          <View
            style={{
              transform: [{ rotate: row.open ? '90deg' : '0deg' }],
              marginRight: 6,
            }}
          >
            <Icon name="chevron" size={14} color={p.dim} />
          </View>
        ) : (
          <View style={{ width: 20 }} />
        )}
        <Text
          numberOfLines={1}
          style={[
            styles.name,
            {
              color: disabled ? p.placeholder : playing ? p.accent : p.text,
              fontWeight:
                playing || (row.depth === 0 && item.children) ? '600' : '400',
            },
          ]}
        >
          {item.name}
        </Text>
        <Dots colors={item.colors} />
        <Text style={[styles.detail, { color: p.dim }]}>{item.detail}</Text>
        {item.children ? (
          <>
            {/* an artist, an album, a year, a folder: played at once (its name opens it in place) */}
            <Pressable
              accessibilityLabel={`Play ${item.name}`}
              hitSlop={8}
              onPress={() => {
                ui('play group', item.name.trim());
                playTracks(library, item.tracks, 0);
              }}
              style={({ pressed }) => [
                styles.groupPlay,
                { opacity: pressed ? 0.5 : 1, borderColor: p.line },
              ]}
            >
              <Icon name="play" size={14} color={p.text} />
            </Pressable>
          </>
        ) : null}
      </Pressable>
    );
  };

  return (
    <View style={{ flex: 1, backgroundColor: p.window }}>
      <View style={styles.top}>
        <Segmented
          items={MODES}
          value={mode}
          onChange={chooseMode}
          style={{ flex: 1 }}
        />
      </View>
      <View style={[styles.filterRow]}>
        <View
          style={[
            styles.filter,
            { backgroundColor: p.base, borderColor: p.line },
          ]}
        >
          <Icon name="search" size={16} color={p.placeholder} />
          <TextInput
            value={text}
            onChangeText={setText}
            onEndEditing={() => ui('filter', text)}
            placeholder="Filter"
            placeholderTextColor={p.placeholder}
            autoCorrect={false}
            autoCapitalize="none"
            clearButtonMode="while-editing"
            style={[styles.input, { color: p.text }]}
          />
        </View>
        <Pressable
          onPress={() => {
            ui('genres', showGenres ? 'closed' : 'open');
            setShowGenres(!showGenres);
          }}
          style={[
            styles.genresButton,
            { backgroundColor: genres.size ? p.highlight : p.button },
          ]}
        >
          <Text
            style={{
              color: genres.size ? p.highlightedText : p.text,
              fontSize: 13,
            }}
          >
            {genres.size ? `Genres (${genres.size})` : 'Genres'}
          </Text>
        </Pressable>
        {MARKERS.map(([name, color]) => (
          <Pressable
            key={name}
            hitSlop={4}
            onPress={() => {
              ui('color filter', name);
              toggle(colors, name, setColors);
            }}
            style={styles.dotButton}
          >
            <View
              style={{
                width: 12,
                height: 12,
                borderRadius: 6,
                backgroundColor: colors.has(name) ? color : 'transparent',
                borderWidth: 1.5,
                borderColor: color,
              }}
            />
          </Pressable>
        ))}
      </View>
      {showGenres && (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.chips}
        >
          {genres.size > 0 && (
            <Pressable
              onPress={() => {
                ui('genres', 'all');
                setGenres(new Set());
              }}
              style={[styles.chip, { borderColor: p.line }]}
            >
              <Text style={{ color: p.dim, fontSize: 13 }}>All</Text>
            </Pressable>
          )}
          {allGenres.map(g => {
            const on = genres.has(g);
            return (
              <Pressable
                key={g}
                onPress={() => {
                  ui('genre', g);
                  toggle(genres, g, setGenres);
                }}
                style={[
                  styles.chip,
                  {
                    borderColor: on ? p.highlight : p.line,
                    backgroundColor: on ? p.highlight : 'transparent',
                  },
                ]}
              >
                <Text
                  style={{
                    color: on ? p.highlightedText : p.text,
                    fontSize: 13,
                  }}
                >
                  {g}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>
      )}
      {/* the songs shown (all, or those the filter leaves): played, shuffled */}
      <View style={[styles.crumbs, { borderBottomColor: p.line }]}>
        <Text numberOfLines={1} style={[styles.count, { color: p.dim }]}>
          {shown.length} songs{filtering ? ' match' : ''}
        </Text>
        <IconButton
          name="play"
          size={18}
          onPress={() => playAll(false)}
          label="Play all"
        />
        <IconButton
          name="shuffle"
          size={18}
          onPress={() => playAll(true)}
          label="Shuffle all"
        />
      </View>
      <FlatList
        data={rows}
        keyExtractor={row => row.id}
        renderItem={renderItem}
        keyboardShouldPersistTaps="handled"
        initialNumToRender={30}
        windowSize={11}
        ListEmptyComponent={
          <Text style={[styles.empty, { color: p.dim }]}>
            {library.tracks.length
              ? 'Nothing matches the filter'
              : 'No songs in this library'}
          </Text>
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  top: { flexDirection: 'row', paddingHorizontal: 10, paddingTop: 8 },
  filterRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 10,
    paddingVertical: 8,
  },
  filter: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 8,
    paddingHorizontal: 8,
  },
  input: {
    flex: 1,
    paddingVertical: Platform.OS === 'ios' ? 7 : 3,
    paddingHorizontal: 6,
    fontSize: 15,
  },
  genresButton: {
    marginLeft: 8,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 7,
  },
  dotButton: { paddingHorizontal: 3, paddingVertical: 6 },
  chips: { paddingHorizontal: 10, paddingBottom: 8 },
  chip: {
    borderWidth: 1,
    borderRadius: 14,
    paddingHorizontal: 10,
    paddingVertical: 4,
    marginRight: 6,
  },
  crumbs: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 4,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  count: { flex: 1, fontSize: 13, paddingLeft: 8 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 11,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  name: { flex: 1, fontSize: 15 },
  groupPlay: {
    marginLeft: 10,
    marginRight: 6,
    width: 30,
    height: 30,
    borderRadius: 15,
    borderWidth: StyleSheet.hairlineWidth,
    alignItems: 'center',
    justifyContent: 'center',
  },
  detail: { fontSize: 13, marginLeft: 8, fontVariant: ['tabular-nums'] },
  empty: { textAlign: 'center', marginTop: 40 },
});
