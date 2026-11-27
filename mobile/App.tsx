/**
 * AG CuteMusic for phones and tablets: plays the phone's own music (a folder, Music/ at first), or as an
 * option the music of an AG CuteMusic sharing its library on the LAN (found by Bonjour, or by address). The player: in portrait the desktop's docked column (cover, VU meter,
 * lyrics, play bar), in landscape its Amplifier view; the browser view to find a song, back to the player from
 * its play bar.
 *
 * @format
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  BackHandler,
  Modal,
  Pressable,
  StatusBar,
  StyleSheet,
  Text,
  View,
  useColorScheme,
  useWindowDimensions,
} from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { useActiveTrack } from 'react-native-track-player';

import { Amp } from './src/Amp';
import { Browser } from './src/Browser';
import { Docked } from './src/Docked';
import { useDiscovery } from './src/discovery';
import { Libraries } from './src/Libraries';
import { MiniBar } from './src/MiniBar';
import { LOCAL, loadLocal, localAvailable, sourceOf } from './src/local';
import { SAVED, loadSaved, savedLibraries, savedLibrary } from './src/saved';
import { HistoryView } from './src/HistoryView';
import { ActionSheetHost } from './src/ActionSheet';
import {
  QueuedTrack,
  registerLibrary,
  restoreQueue,
  setupPlayer,
  useKeepPlayback,
  useLocalArtwork,
} from './src/player';
import { Library, fetchLibrary } from './src/protocol';
import { QueueView } from './src/QueueView';
import { Saved, settings } from './src/settings';
import { DARK, LIGHT, ThemeContext, ThemeName } from './src/theme';
import { Icon } from './src/icons';
import { ui } from './src/log';

type Tab = 'library' | 'queue' | 'history';
const TABS: [Tab, string][] = [
  ['library', 'Library'],
  ['queue', 'Queue'],
  ['history', 'History'],
];

function Main() {
  const system = useColorScheme();
  const [theme, setTheme] = useState<ThemeName>('dark');
  const palette =
    theme === 'system'
      ? system === 'light'
        ? LIGHT
        : DARK
      : theme === 'light'
      ? LIGHT
      : DARK;
  const { width, height } = useWindowDimensions();
  const landscape = width > height;
  useEffect(
    () => ui('orientation', landscape ? 'landscape' : 'portrait'),
    [landscape],
  );

  const { found, error: discoveryError } = useDiscovery();
  const [library, setLibrary] = useState<Library | null>(null);
  const [connecting, setConnecting] = useState<string | undefined>();
  const [error, setError] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const [view, setView] = useState<'player' | 'browser'>('player');
  const [tab, setTab] = useState<Tab>('library');
  const last = useRef<Saved | null>(null);
  const tried = useRef(new Set<string>());
  const active = useActiveTrack() as QueuedTrack | undefined;
  useLocalArtwork();
  useKeepPlayback(); // the history, and the queue kept for the next start

  const connect = useCallback(async function connect(
    saved: Saved,
    quiet = false,
  ): Promise<void> {
    const local = sourceOf(saved.address) !== null; // this phone's music, or one of its other sources
    const kept = saved.address.startsWith(`${SAVED}:`); // songs of a LAN library saved on the phone
    if (!local && !kept) {
      tried.current.add(saved.address);
    }
    setConnecting(saved.address);
    setError(null);
    try {
      const loaded = local
        ? await loadLocal(saved.address)
        : kept
        ? (await loadSaved(), savedLibrary(saved.address))
        : await fetchLibrary(saved.address);
      registerLibrary(loaded);
      setLibrary(loaded);
      setPicking(false);
      last.current = {
        id: loaded.id,
        name: loaded.name,
        address: saved.address,
        folder: saved.folder,
      };
      settings.setLastLibrary(last.current);
    } catch (e: any) {
      if (!quiet || local) {
        setError(
          `${saved.name}: ${
            e?.name === 'AbortError' ? 'no answer' : e?.message || e
          }`,
        );
      }
      if (quiet && !local && !kept && saved.id) {
        // away from home: what was saved from it, when there is some
        await loadSaved();
        if (savedLibraries().some(c => c.id === saved.id)) {
          setConnecting(undefined);
          connect(
            {
              id: `${SAVED}:${saved.id}`,
              name: saved.name,
              address: `${SAVED}:${saved.id}`,
            },
            true,
          );
          return;
        }
      }
      if (quiet && !local && !kept && localAvailable) {
        // the LAN library used last is not there (away from home): the phone's own music meanwhile
        setConnecting(undefined);
        connect({ id: LOCAL, name: 'This phone', address: LOCAL }, true);
        return;
      }
      setPicking(true);
    } finally {
      setConnecting(undefined);
    }
  },
  []);

  useEffect(() => {
    // the player, then the queue as it was left: the last song, where it was, paused
    setupPlayer()
      .then(restoreQueue)
      .catch(e => setError(`Player: ${e?.message || e}`));
    settings.get<ThemeName>('view/theme', 'dark').then(setTheme);
    settings.lastLibrary().then(saved => {
      last.current = saved;
      if (saved) {
        connect(saved, true);
      } else if (localAvailable) {
        connect({ id: LOCAL, name: 'This phone', address: LOCAL }, true);
      } else {
        setPicking(true);
      }
    });
  }, [connect]);

  // the library used last, found again at another address (DHCP)
  useEffect(() => {
    const wanted = last.current;
    if (library || connecting || !wanted?.id) {
      return;
    }
    const match = found.find(
      f => f.id === wanted.id && !tried.current.has(f.address),
    );
    if (match) {
      connect({ id: match.id, name: match.name, address: match.address }, true);
    }
  }, [found, library, connecting, connect]);

  useEffect(() => {
    if (view !== 'browser') {
      return;
    }
    const subscription = BackHandler.addEventListener(
      'hardwareBackPress',
      () => {
        setView('player');
        return true;
      },
    );
    return () => subscription.remove();
  }, [view]);

  const chooseTheme = (value: ThemeName) => {
    setTheme(value);
    settings.set('view/theme', value);
  };

  const libraries = (
    <Libraries
      found={found}
      discoveryError={discoveryError}
      currentId={library?.id}
      connecting={connecting}
      error={error}
      theme={theme}
      onTheme={chooseTheme}
      onChoose={saved => connect(saved)}
      onClose={library ? () => setPicking(false) : undefined}
    />
  );

  const browserSide = (
    <View style={{ flex: 1, backgroundColor: palette.window }}>
      <View style={[styles.tabs, { borderBottomColor: palette.line }]}>
        <Pressable
          onPress={() => {
            ui('player');
            setView('player');
          }}
          hitSlop={8}
          style={styles.back}
          accessibilityLabel="Player"
        >
          <Icon name="back" size={20} color={palette.text} />
        </Pressable>
        <Pressable
          onPress={() => {
            ui('libraries');
            setPicking(true);
          }}
          style={[styles.source, { backgroundColor: palette.button }]}
        >
          <Icon name="lan" size={16} color={palette.text} />
          <Text
            numberOfLines={1}
            style={[styles.sourceText, { color: palette.text }]}
          >
            {library?.name || 'Libraries'}
          </Text>
          <Text style={{ color: palette.dim, fontSize: 11 }}>▾</Text>
        </Pressable>
        {TABS.map(([key, label]) => (
          <Pressable
            key={key}
            onPress={() => {
              ui('tab', key);
              setTab(key);
            }}
            style={[
              styles.tab,
              tab === key && { borderBottomColor: palette.highlight },
            ]}
          >
            <Text
              style={{
                color: tab === key ? palette.text : palette.dim,
                fontSize: 15,
                fontWeight: tab === key ? '600' : '400',
              }}
            >
              {label}
            </Text>
          </Pressable>
        ))}
      </View>
      {tab === 'queue' ? (
        <QueueView />
      ) : tab === 'history' ? (
        <HistoryView />
      ) : library ? (
        <Browser
          library={library}
          playingRel={
            active?.library === library.address ? active?.rel : undefined
          }
        />
      ) : (
        <View style={styles.center}>
          {connecting ? (
            <ActivityIndicator color={palette.dim} />
          ) : (
            <Text style={{ color: palette.dim }}>No library</Text>
          )}
        </View>
      )}
    </View>
  );

  const player = view === 'player';
  return (
    <ThemeContext.Provider value={palette}>
      <StatusBar
        barStyle={palette.dark || player ? 'light-content' : 'dark-content'}
      />
      <SafeAreaView
        edges={['top', 'left', 'right', 'bottom']}
        style={{
          flex: 1,
          backgroundColor: player ? palette.visual : palette.window,
        }}
      >
        {player ? (
          landscape ? (
            <Amp
              onBrowse={() => setView('browser')}
              onLibraries={() => setPicking(true)}
              active={!picking}
            />
          ) : (
            <Docked onBrowse={() => setView('browser')} active={!picking} />
          )
        ) : (
          <>
            {browserSide}
            <MiniBar
              onOpen={() => {
                ui('player');
                setView('player');
              }}
            />
          </>
        )}
      </SafeAreaView>
      <Modal
        visible={picking}
        animationType="slide"
        onRequestClose={() => {
          if (library) {
            setPicking(false);
          }
        }}
        presentationStyle="pageSheet"
        supportedOrientations={['portrait', 'landscape']}
      >
        <SafeAreaProvider>
          <ThemeContext.Provider value={palette}>
            <SafeAreaView style={{ flex: 1, backgroundColor: palette.window }}>
              {libraries}
            </SafeAreaView>
          </ThemeContext.Provider>
        </SafeAreaProvider>
      </Modal>
      <ActionSheetHost />
    </ThemeContext.Provider>
  );
}

export default function App() {
  return (
    <SafeAreaProvider>
      <Main />
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  back: { paddingRight: 8, paddingVertical: 6 },
  tabs: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  source: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 14,
    paddingHorizontal: 10,
    paddingVertical: 5,
    marginRight: 4,
    maxWidth: 170,
    flexShrink: 1, // the library's name gives way: the tabs always fit
    minWidth: 0,
  },
  sourceText: { fontSize: 13, marginHorizontal: 5, flexShrink: 1 },
  tab: {
    flexShrink: 0,
    paddingHorizontal: 9,
    paddingVertical: 10,
    borderBottomWidth: 2,
    borderBottomColor: 'transparent',
  },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
});
