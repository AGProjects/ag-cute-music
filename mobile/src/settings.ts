// What is kept between runs: the library last used, the addresses typed, the browser's view, the theme.

import AsyncStorage from '@react-native-async-storage/async-storage';

export type Saved = {
  id?: string;
  name: string;
  address: string;
  folder?: string;
}; // address 'local': the phone's own

async function get<T>(key: string, fallback: T): Promise<T> {
  try {
    const value = await AsyncStorage.getItem(key);
    return value === null ? fallback : (JSON.parse(value) as T);
  } catch {
    return fallback;
  }
}

function set(key: string, value: unknown) {
  AsyncStorage.setItem(key, JSON.stringify(value)).catch(() => {});
}

export const settings = {
  get,
  set,
  lastLibrary: () => get<Saved | null>('library/last', null),
  setLastLibrary: (library: Saved) => set('library/last', library),
  typed: () => get<Saved[]>('library/typed', []),
  setTyped: (libraries: Saved[]) => set('library/typed', libraries),
};
