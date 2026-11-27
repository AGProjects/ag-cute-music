// A menu of choices for a long press: iOS's own action sheet; on Android a sheet from the bottom (its Alert shows
// three buttons at most and drops the rest). showActions() from anywhere; <ActionSheetHost /> once, in the app.

import React, { useEffect, useState } from 'react';
import {
  ActionSheetIOS,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { usePalette } from './theme';

export type Action = {
  text: string;
  onPress: () => void;
  destructive?: boolean;
};
type Sheet = { title: string; message?: string; actions: Action[] };

const DESTRUCTIVE = '#ff3b30';
let show: ((sheet: Sheet | null) => void) | null = null;

export function showActions(
  title: string,
  actions: Action[],
  message?: string,
) {
  if (!actions.length) {
    return;
  }
  if (Platform.OS === 'ios') {
    const destructive = actions
      .map((a, i) => (a.destructive ? i : -1))
      .filter(i => i >= 0);
    ActionSheetIOS.showActionSheetWithOptions(
      {
        title,
        message,
        options: [...actions.map(a => a.text), 'Cancel'],
        cancelButtonIndex: actions.length,
        destructiveButtonIndex: destructive.length ? destructive : undefined,
      },
      index => index < actions.length && actions[index].onPress(),
    );
  } else {
    show?.({ title, message, actions });
  }
}

export function ActionSheetHost() {
  const p = usePalette();
  const insets = useSafeAreaInsets();
  const [sheet, setSheet] = useState<Sheet | null>(null);
  useEffect(() => {
    show = setSheet;
    return () => {
      show = null;
    };
  }, []);
  const close = () => setSheet(null);
  const choose = (action: Action) => {
    setSheet(null);
    action.onPress();
  };

  return (
    <Modal
      visible={!!sheet}
      transparent
      animationType="fade"
      statusBarTranslucent
      onRequestClose={close}
      supportedOrientations={['portrait', 'landscape']}
    >
      <Pressable style={styles.backdrop} onPress={close}>
        <Pressable
          style={[
            styles.sheet,
            { backgroundColor: p.window, paddingBottom: insets.bottom + 8 },
          ]}
        >
          {sheet && (
            <>
              <Text numberOfLines={2} style={[styles.title, { color: p.text }]}>
                {sheet.title}
              </Text>
              {!!sheet.message && (
                <Text
                  numberOfLines={2}
                  style={[styles.message, { color: p.dim }]}
                >
                  {sheet.message}
                </Text>
              )}
              <ScrollView style={{ maxHeight: 420 }}>
                {sheet.actions.map(action => (
                  <Pressable
                    key={action.text}
                    onPress={() => choose(action)}
                    style={({ pressed }) => [
                      styles.row,
                      { borderTopColor: p.line },
                      pressed && { backgroundColor: p.button },
                    ]}
                  >
                    <Text
                      style={[
                        styles.label,
                        { color: action.destructive ? DESTRUCTIVE : p.text },
                      ]}
                    >
                      {action.text}
                    </Text>
                  </Pressable>
                ))}
                <Pressable
                  onPress={close}
                  style={({ pressed }) => [
                    styles.row,
                    { borderTopColor: p.line },
                    pressed && { backgroundColor: p.button },
                  ]}
                >
                  <Text style={[styles.label, styles.cancel, { color: p.dim }]}>
                    Cancel
                  </Text>
                </Pressable>
              </ScrollView>
            </>
          )}
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: 'rgba(0,0,0,0.45)',
  },
  sheet: {
    borderTopLeftRadius: 14,
    borderTopRightRadius: 14,
    paddingTop: 14,
  },
  title: {
    fontSize: 15,
    fontWeight: '600',
    textAlign: 'center',
    marginHorizontal: 16,
  },
  message: {
    fontSize: 13,
    textAlign: 'center',
    marginHorizontal: 16,
    marginTop: 2,
  },
  row: {
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingVertical: 14,
    paddingHorizontal: 16,
    marginTop: 0,
  },
  label: { fontSize: 16, textAlign: 'center' },
  cancel: { fontWeight: '600' },
});
