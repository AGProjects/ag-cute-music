// Where the music comes from: this phone (a folder of its music, Music/ at first: the default), or a library on
// the LAN, found (Bonjour) or typed (host or host:port) as the desktop's "Connect to a library..."; and the theme.

import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { Found } from './discovery';
import {
  LOCAL,
  SOURCES,
  Source,
  localAvailable,
  localCounts,
  permitted,
  sourceAddress,
} from './local';
import { normalizeAddress } from './protocol';
import { Saved, settings } from './settings';
import { ThemeName, usePalette } from './theme';
import { IconButton, Segmented } from './ui';
import { ui } from './log';
import {
  SAVED,
  removeSaved,
  savedLibraries,
  savedLibrary,
  useSaved,
} from './saved';

const THEMES: [ThemeName, string][] = [
  ['system', 'Follow system'],
  ['light', 'Light'],
  ['dark', 'Dark'],
];

export function Libraries({
  found,
  discoveryError,
  currentId,
  connecting,
  error,
  theme,
  onTheme,
  onChoose,
  onClose,
}: {
  found: Found[];
  discoveryError: string | null;
  currentId?: string;
  connecting?: string;
  error?: string | null;
  theme: ThemeName;
  onTheme: (theme: ThemeName) => void;
  onChoose: (library: Saved) => void;
  onClose?: () => void;
}) {
  const p = usePalette();
  const [typed, setTyped] = useState<Saved[]>([]);
  const [text, setText] = useState('');
  const [counts, setCounts] = useState<Record<Source, number> | null>(null);
  const [allowed, setAllowed] = useState(true);
  useSaved(); // (re-rendered as songs are saved or removed)
  const saved = savedLibraries();
  const refreshCounts = async () => {
    const ok = await permitted(false);
    setAllowed(ok);
    setCounts(ok ? await localCounts() : null);
  };
  useEffect(() => {
    settings.typed().then(setTyped);
    if (localAvailable) {
      refreshCounts();
    }
  }, []);
  const chooseSource = (source: Source) => {
    ui('library', `this phone: ${source}`);
    const address = sourceAddress(source);
    onChoose({
      id: address,
      name: source === 'music' ? 'This phone' : source,
      address,
    });
  };

  const add = () => {
    ui('connect to', text);
    const address = normalizeAddress(text);
    if (!address) {
      Alert.alert(
        'Not an address',
        'A host name or IP address, with :port when not 47100',
      );
      return;
    }
    const entry = { name: address.replace(/^https?:\/\//, ''), address };
    const list = [entry, ...typed.filter(t => t.address !== address)].slice(
      0,
      10,
    );
    setTyped(list);
    settings.setTyped(list);
    setText('');
    onChoose(entry);
  };
  const forget = (address: string) => {
    ui('forget', address);
    const list = typed.filter(t => t.address !== address);
    setTyped(list);
    settings.setTyped(list);
  };

  const row = (
    key: string,
    title: string,
    subtitle: string,
    onPress: () => void,
    current: boolean,
    onLongPress?: () => void,
    indent = 0,
  ) => (
    <Pressable
      key={key}
      onPress={onPress}
      onLongPress={onLongPress}
      style={({ pressed }) => [
        styles.row,
        {
          backgroundColor: pressed ? p.highlight : p.base,
          borderBottomColor: p.line,
        },
      ]}
    >
      <View style={{ flex: 1, paddingLeft: indent }}>
        <Text
          style={{
            color: current ? p.accent : p.text,
            fontSize: 16,
            fontWeight: current ? '600' : '400',
          }}
        >
          {title}
        </Text>
        <Text style={{ color: p.dim, fontSize: 12, marginTop: 2 }}>
          {subtitle}
        </Text>
      </View>
      {(connecting === subtitle ||
        (connecting?.startsWith(LOCAL) && current)) && (
        <ActivityIndicator color={p.dim} />
      )}
    </Pressable>
  );

  return (
    <View style={{ flex: 1, backgroundColor: p.window }}>
      <View style={[styles.header, { borderBottomColor: p.line }]}>
        <Text style={[styles.heading, { color: p.text }]}>Libraries</Text>
        {onClose && <IconButton name="close" onPress={onClose} label="Close" />}
      </View>
      <ScrollView keyboardShouldPersistTaps="handled">
        {!!error && <Text style={[styles.error]}>{error}</Text>}
        {localAvailable && (
          <>
            <Text style={[styles.section, { color: p.dim }]}>THIS PHONE</Text>
            {!allowed ? (
              <Pressable
                onPress={async () => {
                  await permitted(true);
                  refreshCounts();
                }}
                style={[
                  styles.row,
                  { backgroundColor: p.base, borderBottomColor: p.line },
                ]}
              >
                <Text style={{ color: p.text, fontSize: 16 }}>
                  Allow AG CuteMusic to read the music on this phone
                </Text>
              </Pressable>
            ) : counts === null ? (
              <ActivityIndicator color={p.dim} style={styles.waiting} />
            ) : (
              // its music, and its other audio as sources of their own (those with some)
              SOURCES.filter(
                ([source]) => source === 'music' || counts[source] > 0,
              ).map(([source, label, about]) =>
                row(
                  `local:${source}`,
                  source === 'music' ? 'This phone' : label,
                  `${counts[source]} ${
                    source === 'music' ? 'songs' : `files · ${about}`
                  }`,
                  () => chooseSource(source),
                  currentId === sourceAddress(source),
                ),
              )
            )}
          </>
        )}
        {/* songs of LAN libraries saved on the phone: there away from home */}
        {saved.length > 0 && (
          <>
            <Text style={[styles.section, { color: p.dim }]}>
              SAVED ON THIS PHONE
            </Text>
            {saved.map(c =>
              row(
                `saved:${c.id}`,
                c.name,
                `${c.count} songs`,
                () => {
                  ui('library', `saved ${c.name}`);
                  onChoose({
                    id: `${SAVED}:${c.id}`,
                    name: c.name,
                    address: `${SAVED}:${c.id}`,
                  });
                },
                currentId === `${SAVED}:${c.id}`,
                () =>
                  Alert.alert(c.name, `${c.count} songs saved on this phone`, [
                    {
                      text: 'Remove them all from this phone',
                      style: 'destructive',
                      onPress: () =>
                        removeSaved(
                          c.id,
                          savedLibrary(`${SAVED}:${c.id}`).tracks,
                        ),
                    },
                    { text: 'Cancel', style: 'cancel' },
                  ]),
              ),
            )}
          </>
        )}
        <Text style={[styles.section, { color: p.dim }]}>
          ON THE LAN · FOUND
        </Text>
        {found.length === 0 && (
          <View style={styles.looking}>
            <ActivityIndicator color={p.dim} />
            <Text style={{ color: p.dim, marginLeft: 10, flex: 1 }}>
              {discoveryError
                ? `Cannot look for libraries: ${discoveryError}`
                : 'Looking… (AG CuteMusic with File → Share my library on the LAN, or --headless)'}
            </Text>
          </View>
        )}
        {found.map(f =>
          row(
            f.key,
            f.name,
            f.address,
            () => {
              ui('library', `${f.name} ${f.address}`);
              onChoose({ id: f.id, name: f.name, address: f.address });
            },
            f.id === currentId,
          ),
        )}
        <Text style={[styles.section, { color: p.dim }]}>BY ADDRESS</Text>
        <View style={styles.addRow}>
          <TextInput
            value={text}
            onChangeText={setText}
            onSubmitEditing={add}
            placeholder="host or host:port"
            placeholderTextColor={p.placeholder}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
            returnKeyType="go"
            style={[
              styles.input,
              { color: p.text, backgroundColor: p.base, borderColor: p.line },
            ]}
          />
          <Pressable
            onPress={add}
            style={[styles.button, { backgroundColor: p.button }]}
          >
            <Text style={{ color: p.text }}>Connect</Text>
          </Pressable>
        </View>
        {typed.map(t =>
          row(
            t.address,
            t.name,
            t.address,
            () => {
              ui('library', t.address);
              onChoose(t);
            },
            false,
            () =>
              Alert.alert(t.name, undefined, [
                {
                  text: 'Forget',
                  style: 'destructive',
                  onPress: () => forget(t.address),
                },
                { text: 'Cancel', style: 'cancel' },
              ]),
          ),
        )}
        <Text style={[styles.section, { color: p.dim }]}>THEME</Text>
        <Segmented
          items={THEMES}
          value={theme}
          onChange={onTheme}
          style={{ marginHorizontal: 12 }}
        />
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  heading: { fontSize: 17, fontWeight: '600' },
  section: {
    fontSize: 12,
    fontWeight: '600',
    marginTop: 20,
    marginBottom: 6,
    marginHorizontal: 12,
  },
  looking: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 11,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  addRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    marginBottom: 6,
  },
  input: {
    flex: 1,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
    fontSize: 15,
  },
  button: {
    marginLeft: 8,
    borderRadius: 8,
    paddingHorizontal: 14,
    paddingVertical: 9,
  },
  error: { color: '#ff6b5a', marginHorizontal: 12, marginTop: 12 },
  waiting: { margin: 10 },
});
