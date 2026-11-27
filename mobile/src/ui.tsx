// Small pieces shared by the views: segmented buttons (the desktop's make_segmented), icon buttons, color dots.

import React from 'react';
import { Pressable, StyleSheet, Text, View, ViewStyle } from 'react-native';

import { Icon, IconName } from './icons';
import { MARKER_COLOR, usePalette } from './theme';
import { ui } from './log';

export function Segmented<T extends string>({
  items,
  value,
  onChange,
  dark,
  style,
}: {
  items: [T, string][];
  value: T;
  onChange: (value: T) => void;
  dark?: boolean; // on the visualization pane
  style?: ViewStyle;
}) {
  const p = usePalette();
  const border = dark ? p.visualDim : p.line;
  return (
    <View style={[styles.segmented, { borderColor: border }, style]}>
      {items.map(([key, label], index) => {
        const on = key === value;
        return (
          <Pressable
            key={key}
            onPress={() => {
              ui('select', label);
              onChange(key);
            }}
            style={[
              styles.segment,
              index > 0 && {
                borderLeftWidth: StyleSheet.hairlineWidth,
                borderLeftColor: border,
              },
              {
                backgroundColor: on
                  ? dark
                    ? p.accent
                    : p.highlight
                  : dark
                  ? 'rgba(0,0,0,0.55)'
                  : p.button,
              },
            ]}
          >
            <Text
              numberOfLines={1}
              style={{
                color: on
                  ? dark
                    ? '#101318'
                    : p.highlightedText
                  : dark
                  ? p.visualDim
                  : p.text,
                fontWeight: on && dark ? '700' : '500',
                fontSize: 13,
              }}
            >
              {label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

// (logged by the player itself: src/player.ts)
const PLAYER = new Set(['Play or pause', 'Previous', 'Next']);

export function IconButton({
  name,
  onPress,
  onLongPress,
  size = 22,
  color,
  on,
  style,
  label,
}: {
  name: IconName;
  onPress?: () => void;
  onLongPress?: () => void;
  size?: number;
  color?: string;
  on?: boolean; // a toggle lit
  style?: ViewStyle;
  label?: string;
}) {
  const p = usePalette();
  return (
    <Pressable
      accessibilityLabel={label}
      hitSlop={8}
      onPress={
        onPress &&
        (() => {
          if (label && !PLAYER.has(label)) {
            ui(label);
          }
          onPress();
        })
      }
      onLongPress={onLongPress}
      style={({ pressed }) => [
        styles.iconButton,
        { opacity: pressed ? 0.5 : 1 },
        style,
      ]}
    >
      <Icon name={name} size={size} color={on ? p.accent : color || p.text} />
    </Pressable>
  );
}

export function Dots({
  colors,
  size = 8,
}: {
  colors: string[];
  size?: number;
}) {
  if (!colors?.length) {
    return null;
  }
  return (
    <View style={styles.dots}>
      {colors.map(c => (
        <View
          key={c}
          style={{
            width: size,
            height: size,
            borderRadius: size / 2,
            marginLeft: 3,
            backgroundColor: MARKER_COLOR[c] || '#888',
          }}
        />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  segmented: {
    flexDirection: 'row',
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 8,
    overflow: 'hidden',
  },
  segment: {
    flex: 1,
    paddingVertical: 6,
    paddingHorizontal: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  iconButton: { padding: 6, alignItems: 'center', justifyContent: 'center' },
  dots: { flexDirection: 'row', alignItems: 'center', marginLeft: 4 },
});
