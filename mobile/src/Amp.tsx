// Landscape: the Amplifier view (cutemusic/amp.py), a retro integrated amplifier's front panel: the backlit VU
// meters with the lever left of them (ANALOG, LED BAR, SPECTRUM), the VOLUME knob (the player's volume) and MUTE;
// the lyrics and the play bar under it. (No BASS, TREBLE, BALANCE nor OUTPUT here: the phone's player has none.)
// The plate dark with a dark theme, brushed aluminium with a light one.

import React, { memo, useEffect, useMemo, useRef, useState } from 'react';
import {
  NativeModules,
  PanResponder,
  Pressable,
  StatusBar,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import Svg, {
  Circle,
  Defs,
  G,
  Line,
  LinearGradient,
  RadialGradient,
  Rect,
  Stop,
  Text as SvgText,
} from 'react-native-svg';
import Slider from '@react-native-community/slider';
import TrackPlayer, {
  useActiveTrack,
  useIsPlaying,
  useProgress,
} from 'react-native-track-player';

import { AnalogMeter } from './AnalogFaces';
import { useLiveLevels } from './levels';
import { useMeter } from './meter';
import { Icon, IconName } from './icons';
import { QueuedTrack, next, previous, seek, togglePlay } from './player';
import { useOutputName } from './output';
import { OutputButton } from './OutputButton';
import { formatDuration } from './protocol';
import { LyricsStrip } from './LyricsStrip';
import { settings } from './settings';
import { usePalette } from './theme';
import { VUMeter } from './VUMeter';
import { ui } from './log';

type MeterStyle = 'analog' | 'bars' | 'spectrum';
const METERS: [MeterStyle, string][] = [
  ['analog', 'ANALOG'],
  ['bars', 'LED BAR'],
  ['spectrum', 'SPECTRUM'],
];

const PLATES = {
  aluminium: {
    metal: ['#e4e6e9', '#c9ccd1', '#aeb2b8'],
    grain: [40, 22],
    ink: '#2b2e33',
    print: '#4a4e55',
    red: 'rgb(179,38,30)',
    escutcheon: ['#f2f3f5', '#9da1a8'],
    rim: '#6d7178',
    screw: ['#b9bcc2', '#55595f'],
  },
  dark: {
    metal: ['#34373c', '#25272b', '#18191c'],
    grain: [14, 30],
    ink: '#d8dbdf',
    print: '#a4a8af',
    red: 'rgb(232,72,60)',
    escutcheon: ['#4b4e54', '#1d1f22'],
    rim: '#0b0c0e',
    screw: ['#8c9097', '#2a2c30'],
  },
};
type Colors = (typeof PLATES)['dark'];

const BUTTONS = 0.2; // the push buttons' row under the plate, of its height
const ASPECT = 3.4; // the panel's width to its height (the desktop's 2.8 holds four knobs; this one, one)
const KNOB_SWEEP = 135;

// the layout, in the plate's units (h: its height)
function layout(w: number, h: number) {
  const span = h * 0.13; // the lever's step
  // the lever close to its labels (ANALOG, LED BAR, SPECTRUM: about 0.2 of the height wide, from the left edge),
  // the meters close to the lever
  const lever = { x: h * 0.35, y: h * 0.5, span };
  // the meters as big as the panel lets them: a small VOLUME knob right of them, faces squarer than the
  // desktop's (1.6 wide to 1 high a channel, not 2.6)
  const left = lever.x + span * 0.4 + h * 0.08;
  const window = { x: left, y: 0, w: w - left - h * 0.56, h: 0 };
  window.h = Math.min(h * 0.6, window.w / ((2 * 1.6) / 0.97));
  window.y = h * 0.5 - window.h / 2;
  // VOLUME and MUTE in one column right of them: nothing else takes the meters' width
  const knob = { x: window.x + window.w + h * 0.3, y: h * 0.42, r: h * 0.12 };
  const mute = { x: knob.x, y: h * 0.85, size: h * 0.11 };
  // the position along the plate's bottom strip, right under the meters, as wide as they are
  const bar = {
    y: h * 0.895,
    left: window.x, // as wide as the meters
    right: window.x + window.w,
  };
  // the browser's and the libraries' buttons at the top right, after the model's name
  const corner = {
    y: h * 0.07,
    size: h * 0.075,
    xs: [w - h * 0.2, w - h * 0.09],
  };
  return { lever, window, knob, mute, bar, corner };
}

// brushed aluminium, its lettering, the lever's plate and the knob's scale: what does not move
const Panel = memo(function Panel({
  w,
  h,
  colors,
  style,
}: {
  w: number;
  h: number;
  colors: Colors;
  style: MeterStyle;
}) {
  const L = layout(w, h);
  const grain = useMemo(() => {
    const lines = [];
    let seed = 3;
    const random = () =>
      ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;
    for (let y = 0; y < h; y += 0.8 + random() * 1.2) {
      const light = random() < 0.5;
      const alpha =
        (light ? colors.grain[0] : colors.grain[1]) * (0.25 + random() * 0.75);
      lines.push(
        <Line
          key={y}
          x1={0}
          y1={y}
          x2={w}
          y2={y}
          stroke={light ? '#ffffff' : '#000000'}
          strokeOpacity={alpha / 255}
          strokeWidth={0.7}
        />,
      );
    }
    return lines;
  }, [w, h, colors]);
  const { knob, lever } = L;
  const at = (ratio: number, r: number) => {
    const a = ((-KNOB_SWEEP + 2 * KNOB_SWEEP * ratio - 90) * Math.PI) / 180;
    return { x: knob.x + r * Math.cos(a), y: knob.y + r * Math.sin(a) };
  };
  const ticks = Array.from({ length: 12 }, (_, i) => {
    const ratio = i / 11;
    const long = [0, 5, 10, 11].includes(i);
    const a = at(ratio, knob.r * 1.18);
    const b = at(ratio, knob.r * (long ? 1.34 : 1.27));
    return (
      <Line
        key={i}
        x1={a.x}
        y1={a.y}
        x2={b.x}
        y2={b.y}
        stroke={i === 11 ? colors.red : colors.ink}
        strokeWidth={1.3}
      />
    );
  });
  const label = (ratio: number, text: string, color: string) => {
    const p = at(ratio, knob.r * 1.72);
    return (
      <SvgText
        key={text}
        x={p.x}
        y={p.y + h * 0.014}
        fontSize={h * 0.04}
        fontWeight="bold"
        fill={color}
        textAnchor="middle"
      >
        {text}
      </SvgText>
    );
  };
  const positions = METERS.map((_, i) => lever.y + (i - 1) * lever.span);
  const plateRect = {
    x: lever.x - lever.span * 0.4,
    y: positions[0] - lever.span * 0.6,
    w: lever.span * 0.8,
    h: positions[2] - positions[0] + lever.span * 1.2,
  };
  return (
    <Svg width={w} height={h} style={StyleSheet.absoluteFill}>
      <Defs>
        <LinearGradient
          id="metal"
          x1="0"
          y1="0"
          x2="0"
          y2={h}
          gradientUnits="userSpaceOnUse"
        >
          <Stop offset="0" stopColor={colors.metal[0]} />
          <Stop offset="0.5" stopColor={colors.metal[1]} />
          <Stop offset="1" stopColor={colors.metal[2]} />
        </LinearGradient>
        <LinearGradient
          id="escutcheon"
          x1={plateRect.x}
          y1={plateRect.y}
          x2={plateRect.x + plateRect.w}
          y2={plateRect.y + plateRect.h}
          gradientUnits="userSpaceOnUse"
        >
          <Stop offset="0" stopColor={colors.escutcheon[0]} />
          <Stop offset="1" stopColor={colors.escutcheon[1]} />
        </LinearGradient>
      </Defs>
      <Rect
        x={0}
        y={0}
        width={w}
        height={h}
        rx={Math.max(1.5, h * 0.007)}
        fill="url(#metal)"
      />
      <G>{grain}</G>
      <Line
        x1={0}
        y1={0.5}
        x2={w}
        y2={0.5}
        stroke="#ffffff"
        strokeOpacity={0.5}
      />
      <Line
        x1={0}
        y1={h - 0.5}
        x2={w}
        y2={h - 0.5}
        stroke="#000000"
        strokeOpacity={0.5}
      />
      <SvgText
        x={h * 0.08}
        y={h * 0.1}
        fontSize={h * 0.05}
        fontWeight="bold"
        fill={colors.print}
        letterSpacing={-0.5}
      >
        AG Projects
      </SvgText>
      <SvgText
        x={w - h * 0.27}
        y={h * 0.095}
        fontSize={h * 0.034}
        fill={colors.print}
        letterSpacing={h * 0.005}
        textAnchor="end"
      >
        STEREO CLASS-CUTE AMPLIFIER
      </SvgText>
      {/* the lever's escutcheon, its slot and screws, the positions' names left of it */}
      <Rect
        x={plateRect.x}
        y={plateRect.y}
        width={plateRect.w}
        height={plateRect.h}
        rx={lever.span * 0.18}
        fill="url(#escutcheon)"
        stroke={colors.rim}
        strokeWidth={0.8}
      />
      {[
        plateRect.y + lever.span * 0.22,
        plateRect.y + plateRect.h - lever.span * 0.22,
      ].map(y => (
        <G key={y}>
          <Circle
            cx={lever.x}
            cy={y}
            r={lever.span * 0.08}
            fill={colors.screw[0]}
            stroke={colors.screw[1]}
            strokeWidth={0.7}
          />
          <Line
            x1={lever.x - lever.span * 0.06}
            y1={y}
            x2={lever.x + lever.span * 0.06}
            y2={y}
            stroke={colors.screw[1]}
            strokeWidth={0.7}
          />
        </G>
      ))}
      <Rect
        x={lever.x - lever.span * 0.12}
        y={positions[0] - lever.span * 0.12}
        width={lever.span * 0.24}
        height={positions[2] - positions[0] + lever.span * 0.24}
        rx={lever.span * 0.12}
        fill="#1b1d21"
      />
      {METERS.map(([key, name], i) => (
        <SvgText
          key={key}
          x={plateRect.x - lever.span * 0.3}
          y={positions[i] + h * 0.01}
          fontSize={h * 0.027}
          fontWeight={key === style ? 'bold' : '600'}
          fill={colors.ink}
          letterSpacing={h * 0.003}
          textAnchor="end"
        >
          {name}
        </SvgText>
      ))}
      {ticks}
      {label(0, '0', colors.ink)}
      {label(10 / 11, '10', colors.ink)}
      {label(1, '11', colors.red)}
      <SvgText
        x={knob.x}
        y={knob.y + knob.r * 1.42 + h * 0.06}
        fontSize={h * 0.032}
        fill={colors.ink}
        letterSpacing={h * 0.005}
        textAnchor="middle"
      >
        VOLUME
      </SvgText>
      <SvgText
        x={L.mute.x}
        y={L.mute.y - L.mute.size * 0.75}
        fontSize={h * 0.027}
        fill={colors.ink}
        letterSpacing={h * 0.003}
        textAnchor="middle"
      >
        MUTE
      </SvgText>
    </Svg>
  );
});

// the knob: a knurled black skirt, a spun aluminium cap, its pointer
function Knob({
  x,
  y,
  r,
  ratio,
  dark,
}: {
  x: number;
  y: number;
  r: number;
  ratio: number;
  dark: boolean;
}) {
  const rotation = -KNOB_SWEEP + 2 * KNOB_SWEEP * ratio;
  const a = ((rotation - 90) * Math.PI) / 180;
  const d = { x: Math.cos(a), y: Math.sin(a) };
  const cap = r * 0.72;
  const knurl = Array.from({ length: 36 }, (_, i) => {
    const t = ((rotation + i * 10) * Math.PI) / 180;
    return (
      <Line
        key={i}
        x1={x + r * 0.82 * Math.cos(t)}
        y1={y + r * 0.82 * Math.sin(t)}
        x2={x + r * 0.98 * Math.cos(t)}
        y2={y + r * 0.98 * Math.sin(t)}
        stroke="#ffffff"
        strokeOpacity={0.15}
      />
    );
  });
  const size = r * 2.8;
  const ox = x - size / 2;
  const oy = y - size / 2;
  return (
    <Svg
      width={size}
      height={size}
      style={{ position: 'absolute', left: ox, top: oy }}
      viewBox={`${ox} ${oy} ${size} ${size}`}
    >
      <Defs>
        <RadialGradient
          id="shadow"
          cx={x}
          cy={y + r * 0.18}
          r={r * 1.25}
          gradientUnits="userSpaceOnUse"
        >
          <Stop offset="0.75" stopColor="#000" stopOpacity={0.43} />
          <Stop offset="1" stopColor="#000" stopOpacity={0} />
        </RadialGradient>
        <LinearGradient
          id="skirt"
          x1="0"
          y1={y - r}
          x2="0"
          y2={y + r}
          gradientUnits="userSpaceOnUse"
        >
          <Stop offset="0" stopColor="#3c3f45" />
          <Stop offset="1" stopColor="#0d0e10" />
        </LinearGradient>
        <RadialGradient
          id="cap"
          cx={x - cap * 0.35}
          cy={y - cap * 0.45}
          r={cap * 1.6}
          gradientUnits="userSpaceOnUse"
        >
          <Stop offset="0" stopColor={dark ? '#5a5e65' : '#f4f5f7'} />
          <Stop offset="0.55" stopColor={dark ? '#2b2d31' : '#b8bcc2'} />
          <Stop offset="1" stopColor={dark ? '#1a1b1e' : '#8f939a'} />
        </RadialGradient>
      </Defs>
      <Circle cx={x} cy={y + r * 0.18} r={r * 1.25} fill="url(#shadow)" />
      <Circle cx={x} cy={y} r={r} fill="url(#skirt)" />
      {knurl}
      <Circle
        cx={x}
        cy={y}
        r={cap}
        fill="url(#cap)"
        stroke={dark ? '#0b0c0e' : '#5e6268'}
        strokeWidth={0.8}
      />
      <Line
        x1={x + d.x * cap * 0.3}
        y1={y + d.y * cap * 0.3}
        x2={x + d.x * cap * 0.88}
        y2={y + d.y * cap * 0.88}
        stroke={dark ? '#e6e8eb' : '#2b2e33'}
        strokeWidth={Math.max(1.5, r * 0.07)}
        strokeLinecap="round"
      />
      <Line
        x1={x + d.x * r * 0.84}
        y1={y + d.y * r * 0.84}
        x2={x + d.x * r * 0.97}
        y2={y + d.y * r * 0.97}
        stroke="#f2f2f2"
        strokeWidth={Math.max(1.5, r * 0.08)}
        strokeLinecap="round"
      />
    </Svg>
  );
}

function Mute({
  x,
  y,
  size,
  muted,
  onPress,
}: {
  x: number;
  y: number;
  size: number;
  muted: boolean;
  onPress: () => void;
}) {
  const box = size * 3;
  const r = size / 2 - size * 0.04;
  const c = box / 2;
  const lamp = size * 0.17;
  return (
    <Pressable
      onPress={onPress}
      accessibilityLabel="Mute"
      style={{
        position: 'absolute',
        left: x - box / 2,
        top: y - box / 2,
        width: box,
        height: box,
      }}
    >
      <Svg width={box} height={box}>
        <Defs>
          <RadialGradient
            id="mute"
            cx={c}
            cy={c - r * 0.5}
            r={r * 1.5}
            gradientUnits="userSpaceOnUse"
          >
            <Stop offset="0" stopColor={muted ? '#3a3d43' : '#555961'} />
            <Stop offset="1" stopColor="#0f1012" />
          </RadialGradient>
        </Defs>
        <Circle
          cx={c}
          cy={c + (muted ? size * 0.03 : 0)}
          r={r}
          fill="url(#mute)"
        />
        {muted && (
          <Circle cx={c} cy={c} r={lamp * 2.6} fill="#e0281b" opacity={0.45} />
        )}
        <Circle
          cx={c}
          cy={c}
          r={lamp}
          fill={muted ? '#ff4a3d' : '#4a1714'}
          stroke="#0a0a0a"
          strokeWidth={0.8}
        />
      </Svg>
    </Pressable>
  );
}

// A round black push button of the plate (as MUTE), a symbol on it
function PushButton({
  x,
  y,
  size,
  icon,
  label,
  onPress,
}: {
  x: number;
  y: number;
  size: number;
  icon: IconName;
  label: string;
  onPress: () => void;
}) {
  const box = size * 1.6;
  const c = box / 2;
  const r = size / 2;
  return (
    <Pressable
      onPress={onPress}
      accessibilityLabel={label}
      hitSlop={6}
      style={({ pressed }) => ({
        position: 'absolute',
        left: x - c,
        top: y - c + (pressed ? size * 0.03 : 0),
        width: box,
        height: box,
        alignItems: 'center',
        justifyContent: 'center',
      })}
    >
      <Svg width={box} height={box} style={StyleSheet.absoluteFill}>
        <Defs>
          <RadialGradient
            id={`push-${icon}`}
            cx={c}
            cy={c - r * 0.5}
            r={r * 1.5}
            gradientUnits="userSpaceOnUse"
          >
            <Stop offset="0" stopColor="#555961" />
            <Stop offset="1" stopColor="#0f1012" />
          </RadialGradient>
        </Defs>
        <Circle
          cx={c}
          cy={c + r * 0.12}
          r={r * 1.08}
          fill="#000"
          opacity={0.3}
        />
        <Circle cx={c} cy={c} r={r} fill={`url(#push-${icon})`} />
      </Svg>
      <Icon name={icon} size={size * 0.48} color="#e6e8eb" />
    </Pressable>
  );
}

// The position: a slider along the plate's bottom, its times at its ends, printed in the plate's ink
function PlateSeek({
  left,
  right,
  y,
  h,
  colors,
}: {
  left: number;
  right: number;
  y: number;
  h: number;
  colors: Colors;
}) {
  const { position, duration } = useProgress(500);
  const [seeking, setSeeking] = useState<number | null>(null);
  const shown = seeking ?? position;
  const font = Math.max(9, h * 0.04);
  const time = {
    color: colors.ink,
    fontSize: font,
    width: font * 3,
    fontVariant: ['tabular-nums' as const],
  };
  return (
    <View
      style={{
        position: 'absolute',
        left,
        width: Math.max(0, right - left),
        top: y - h * 0.06,
        height: h * 0.12,
        flexDirection: 'row',
        alignItems: 'center',
      }}
    >
      <Text style={[time, { textAlign: 'left' }]}>{formatDuration(shown)}</Text>
      <Slider
        style={{ flex: 1 }}
        minimumValue={0}
        maximumValue={Math.max(1, duration)}
        value={shown}
        minimumTrackTintColor={colors.ink}
        maximumTrackTintColor={colors.print}
        thumbTintColor={colors.ink}
        onSlidingStart={setSeeking}
        onValueChange={value => seeking !== null && setSeeking(value)}
        onSlidingComplete={async value => {
          await seek(value);
          setSeeking(null);
        }}
      />
      <Text style={[time, { textAlign: 'right' }]}>
        {formatDuration(duration)}
      </Text>
    </View>
  );
}

export function Amp({
  onBrowse,
  onLibraries,
  active,
}: {
  onBrowse: () => void;
  onLibraries: () => void;
  active: boolean;
}) {
  const p = usePalette();
  const track = useActiveTrack() as QueuedTrack | undefined;
  const colors = p.dark ? PLATES.dark : PLATES.aluminium;
  const [area, setArea] = useState({ width: 0, height: 0 });
  const [below, setBelow] = useState(80);
  const { playing } = useIsPlaying();
  const outputName = useOutputName();
  // landscape is full screen: the status bar and Android's buttons hidden as long as the amplifier is shown
  // (SystemBars.kt; a swipe from the edge shows them for a moment), back in portrait
  useEffect(() => {
    NativeModules.SystemBars?.setImmersive(true);
    return () => NativeModules.SystemBars?.setImmersive(false);
  }, []);
  const [style, setStyle] = useState<MeterStyle>('analog');
  const [volume, setVolume] = useState(0.8);
  const [muted, setMuted] = useState(false);
  const read = useLiveLevels();
  const meter = useMeter(
    read,
    style === 'spectrum' ? 32 : 0,
    active && area.width > 0,
  );
  useEffect(() => {
    settings
      .get<MeterStyle>('amp/meter', 'analog')
      .then(s => METERS.some(([k]) => k === s) && setStyle(s));
    settings.get('player/volume', 0.8).then(v => {
      setVolume(v);
      TrackPlayer.setVolume(v).catch(() => {});
    });
  }, []);
  const chooseStyle = (value: MeterStyle) => {
    ui('amp meter', value);
    setStyle(value);
    settings.set('amp/meter', value);
  };
  const turn = (value: number) => {
    const v = Math.max(0, Math.min(1, value));
    setVolume(v);
    settings.set('player/volume', v);
    if (!muted) {
      TrackPlayer.setVolume(v).catch(() => {});
    }
  };
  const mute = () => {
    ui('mute', muted ? 'off' : 'on');
    TrackPlayer.setVolume(muted ? volume : 0).catch(() => {});
    setMuted(!muted);
  };
  // the knob: dragged up (or right) to turn it up
  const start = useRef(0);
  const current = useRef(volume);
  current.current = volume;
  const turnRef = useRef(turn);
  turnRef.current = turn;
  const pan = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponder: () => true,
        onPanResponderGrant: () => (start.current = current.current),
        onPanResponderRelease: () =>
          ui('volume', `${Math.round(current.current * 100)}%`),
        onPanResponderMove: (_, g) =>
          turnRef.current(start.current + (g.dx - g.dy) / 220),
      }),
    [],
  );

  // the plate, and under it its row of push buttons (BUTTONS of its height)
  const roomH = area.height - below - 16;
  const w = Math.min(area.width - 28, (roomH / (1 + BUTTONS)) * ASPECT);
  const h = w / ASPECT;
  const row = h * BUTTONS;
  const size = row * 0.62;
  const L = layout(w, h);
  const positions = METERS.map((_, i) => L.lever.y + (i - 1) * L.lever.span);
  const tip = positions[METERS.findIndex(([k]) => k === style)];
  const rod = {
    length: L.lever.span * 0.7,
    thickness: Math.max(4, L.lever.span * 0.2),
  };
  const nextStyle = () =>
    chooseStyle(
      METERS[(METERS.findIndex(([k]) => k === style) + 1) % METERS.length][0],
    );

  return (
    <View
      style={[styles.view, { backgroundColor: p.visual }]}
      onLayout={e => setArea(e.nativeEvent.layout)}
    >
      <StatusBar hidden animated />
      <View style={styles.centre}>
        {h > 60 && (
          <View style={{ width: w, height: h }}>
            <Panel w={w} h={h} colors={colors} style={style} />
            {/* what plays, printed on the plate between the maker and the model (no titles in the play bar) */}
            <Text
              numberOfLines={1}
              style={{
                position: 'absolute',
                left: h * 0.6,
                width: Math.max(0, w - h * 0.6 - h * 1.15),
                top: h * 0.035,
                textAlign: 'center',
                color: colors.ink,
                fontSize: Math.max(10, h * 0.05),
                fontWeight: '600',
              }}
            >
              {track
                ? [track.title, track.artist].filter(Boolean).join('  ·  ')
                : ''}
            </Text>
            {/* the lever's rod at the position chosen; each position a tap */}
            <Svg
              width={w}
              height={h}
              style={StyleSheet.absoluteFill}
              pointerEvents="none"
            >
              <Defs>
                <LinearGradient
                  id="rod"
                  x1="0"
                  y1={tip - rod.thickness / 2}
                  x2="0"
                  y2={tip + rod.thickness / 2}
                  gradientUnits="userSpaceOnUse"
                >
                  <Stop offset="0" stopColor="#7d8188" />
                  <Stop offset="0.3" stopColor="#ffffff" />
                  <Stop offset="0.65" stopColor="#b4b8be" />
                  <Stop offset="1" stopColor="#3f4349" />
                </LinearGradient>
              </Defs>
              <Rect
                x={L.lever.x - rod.length / 2 + L.lever.span * 0.04}
                y={tip - rod.thickness / 2 + L.lever.span * 0.08}
                width={rod.length}
                height={rod.thickness}
                fill="#000"
                opacity={0.35}
              />
              <Rect
                x={L.lever.x - rod.length / 2}
                y={tip - rod.thickness / 2}
                width={rod.length}
                height={rod.thickness}
                fill="url(#rod)"
              />
            </Svg>
            {METERS.map(([key], i) => (
              <Pressable
                key={key}
                onPress={() => chooseStyle(key)}
                style={{
                  position: 'absolute',
                  left: 0,
                  top: positions[i] - L.lever.span / 2,
                  width: L.lever.x + L.lever.span * 0.5,
                  height: L.lever.span,
                }}
              />
            ))}
            {/* the meter window */}
            <Pressable
              onPress={nextStyle}
              style={{
                position: 'absolute',
                left: L.window.x,
                top: L.window.y,
                width: L.window.w,
                height: L.window.h,
              }}
            >
              {style === 'analog' ? (
                <AnalogMeter
                  width={L.window.w}
                  height={L.window.h}
                  stacked={false}
                  needles={meter.needles}
                  lit={meter.peakLit}
                />
              ) : (
                <View
                  style={[
                    styles.window,
                    { width: L.window.w, height: L.window.h },
                  ]}
                >
                  <VUMeter
                    style={style === 'bars' ? 'bars' : 'spectrum'}
                    width={L.window.w - 8}
                    height={L.window.h - 8}
                    meter={meter}
                    compact
                  />
                </View>
              )}
            </Pressable>
            <View
              {...pan.panHandlers}
              style={{
                position: 'absolute',
                left: L.knob.x - L.knob.r * 1.5,
                top: L.knob.y - L.knob.r * 1.5,
                width: L.knob.r * 3,
                height: L.knob.r * 3,
              }}
            >
              <Knob
                x={L.knob.r * 1.5}
                y={L.knob.r * 1.5}
                r={L.knob.r}
                ratio={(volume * 10) / 11}
                dark={false}
              />
            </View>
            <Mute
              x={L.mute.x}
              y={L.mute.y}
              size={L.mute.size}
              muted={muted}
              onPress={mute}
            />
            {/* the position, along the plate's bottom */}
            <PlateSeek
              left={L.bar.left}
              right={L.bar.right}
              y={L.bar.y}
              h={h}
              colors={colors}
            />
            {/* the browser (to find a song) and the libraries, top right */}
            {(
              [
                ['browse', 'Browser', onBrowse],
                ['lan', 'Libraries', onLibraries],
              ] as [IconName, string, () => void][]
            ).map(([icon, label, action], index) => (
              <Pressable
                key={icon}
                accessibilityLabel={label}
                onPress={() => {
                  ui(label);
                  action();
                }}
                hitSlop={10}
                style={{
                  position: 'absolute',
                  left: L.corner.xs[index] - L.corner.size / 2,
                  top: L.corner.y - L.corner.size / 2,
                }}
              >
                <Icon name={icon} size={L.corner.size} color={colors.print} />
              </Pressable>
            ))}
          </View>
        )}
        {h > 60 && (
          /* previous / play / next: the amplifier's push buttons, under it */
          <View style={{ width: w, height: row }}>
            <PushButton
              x={w / 2 - size * 1.9}
              y={row / 2}
              size={size}
              icon="previous"
              label="Previous"
              onPress={previous}
            />
            <PushButton
              x={w / 2}
              y={row / 2}
              size={size * 1.15}
              icon={playing ? 'pause' : 'play'}
              label="Play or pause"
              onPress={() => togglePlay(!!playing)}
            />
            <PushButton
              x={w / 2 + size * 1.9}
              y={row / 2}
              size={size}
              icon="next"
              label="Next"
              onPress={next}
            />
            {/* OUTPUT, at the row's right end, the output's name beside it */}
            <View style={[styles.output, { height: row }]}>
              {!!outputName && (
                <Text
                  numberOfLines={1}
                  style={[
                    styles.outputName,
                    { color: p.visualDim, fontSize: Math.max(11, row * 0.22) },
                  ]}
                >
                  {outputName}
                </Text>
              )}
              <OutputButton
                size={Math.max(18, row * 0.36)}
                color={p.visualDim}
              />
            </View>
          </View>
        )}
      </View>
      <View onLayout={e => setBelow(e.nativeEvent.layout.height)}>
        <LyricsStrip track={track} lines={3} before={1} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  output: {
    position: 'absolute',
    right: 0,
    top: 0,
    flexDirection: 'row',
    alignItems: 'center',
    maxWidth: '30%',
  },
  outputName: { marginRight: 4, flexShrink: 1 },
  view: { flex: 1 },
  centre: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  window: {
    backgroundColor: '#101318',
    borderRadius: 5,
    borderWidth: 1.2,
    borderColor: '#7d8188',
    padding: 3,
  },
});
