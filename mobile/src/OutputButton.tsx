// The OUTPUT button: on Android ours, opening the system's output switcher; on iOS Apple's route picker itself
// (AVRoutePickerView: only it may show the routes), tinted as ours.

import React from 'react';
import {
  Pressable,
  requireNativeComponent,
  View,
  ViewStyle,
} from 'react-native';

import { Icon } from './icons';
import { isIOS, outputAvailable, showOutputPicker } from './output';

const RoutePicker = isIOS
  ? requireNativeComponent<{
      style?: ViewStyle;
      color?: string;
      activeColor?: string;
    }>('AGRoutePicker')
  : null;

export function OutputButton({
  size = 22,
  color,
  style,
}: {
  size?: number;
  color: string;
  style?: ViewStyle;
}) {
  if (!outputAvailable) {
    return null;
  }
  if (RoutePicker) {
    return (
      <View
        style={[
          {
            width: size + 12,
            height: size + 12,
            alignItems: 'center',
            justifyContent: 'center',
          },
          style,
        ]}
      >
        <RoutePicker
          style={{ width: size + 8, height: size + 8 }}
          color={color}
          activeColor="#f0a030"
        />
      </View>
    );
  }
  return (
    <Pressable
      accessibilityLabel="Output"
      hitSlop={8}
      onPress={showOutputPicker}
      style={({ pressed }) => [
        { padding: 6, opacity: pressed ? 0.5 : 1 },
        style,
      ]}
    >
      <Icon name="speaker" size={size} color={color} />
    </Pressable>
  );
}
