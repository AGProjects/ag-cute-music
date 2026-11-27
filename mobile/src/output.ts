// Where the music plays: the system's choice, for every app (Android's output switcher, iOS's route picker:
// AudioOutput.kt, modules/ag-audio-output), shown by the OUTPUT button with the output's name under or beside it.

import { useEffect, useState } from 'react';
import { AppState, NativeModules, Platform } from 'react-native';

import { ui } from './log';

const native = NativeModules.AudioOutput as
  | { showPicker(): void; current(): Promise<string | null> }
  | undefined;

export const outputAvailable = !!native;

// Android: the switcher opened by a button of ours; iOS: the button is Apple's own view (OutputButton)
export function showOutputPicker() {
  ui('output picker');
  native?.showPicker();
}

// The output's name, kept current: every few seconds while shown, and when back from the picker
export function useOutputName(): string | null {
  const [name, setName] = useState<string | null>(null);
  useEffect(() => {
    if (!native) {
      return;
    }
    let live = true;
    const read = () =>
      native
        .current()
        .then(value => live && setName(value))
        .catch(() => {});
    read();
    const timer = setInterval(read, 3000);
    const subscription = AppState.addEventListener(
      'change',
      state => state === 'active' && read(),
    );
    return () => {
      live = false;
      clearInterval(timer);
      subscription.remove();
    };
  }, []);
  return name;
}

export const isIOS = Platform.OS === 'ios';
