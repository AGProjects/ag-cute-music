// The VU meter (visuals.VUMeter) in its styles: Bars, Needles, LEDs, Spectrum; its style chosen by the pills over
// it (the dock's) or a tap on it.

import React from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import Svg, {
  Defs,
  G,
  Line,
  LinearGradient,
  Rect,
  Stop,
  Text as SvgText,
} from 'react-native-svg';

import { AnalogMeter } from './AnalogFaces';
import { FLOOR } from './levels';
import { DB_HIGH, DB_LOW, MeterState } from './meter';

export type VUStyle = 'bars' | 'needles' | 'leds' | 'spectrum';
export const VU_STYLES: [VUStyle, string][] = [
  ['bars', 'Bars'],
  ['needles', 'Needles'],
  ['leds', 'LEDs'],
  ['spectrum', 'Spectrum'],
];

const DIM = '#8a90a0';
const ratio = (db: number) =>
  (Math.min(0, Math.max(FLOOR, db)) - FLOOR) / -FLOOR;
const ledColor = (db: number, lit: boolean) => {
  const color =
    db <= -18
      ? [0x5c, 0xdc, 0x9e]
      : db <= -6
      ? [0xff, 0xcc, 0x55]
      : [0xf4, 0x57, 0x4a];
  const [r, g, b] = lit ? color : color.map(c => Math.floor((c * 22) / 100));
  return `rgb(${r},${g},${b})`;
};

// The height a style wants at a width (at most available): the dock's column arranges by it (content_height)
export function vuHeight(style: VUStyle, width: number, available: number) {
  const frame = Math.max(12, width * 0.05);
  const wanted =
    style === 'bars'
      ? 150
      : style === 'needles'
      ? needlesBox(width).h + 2 * frame
      : style === 'spectrum'
      ? Math.min(280, width * 0.35)
      : width * 0.55;
  return Math.max(60, Math.min(available, wanted + 2 * frame));
}

const FACE_ASPECT = 2.6;
function needlesBox(width: number, height?: number) {
  const margin = Math.max(12, width * 0.08);
  const stacked = width < 520;
  const aspect = stacked ? (FACE_ASPECT / 2) * 0.97 : (2 * FACE_ASPECT) / 0.97;
  let w = width - 2 * margin;
  if (height !== undefined) {
    w = Math.min(w, (height - 2 * margin) * aspect);
  }
  return { w, h: w / aspect, stacked };
}

function Bars({
  width,
  height,
  m,
  compact,
}: {
  width: number;
  height: number;
  m: MeterState;
  compact: boolean;
}) {
  const margin = 30;
  const bar = Math.max(8, Math.min(90, (height - 44) / 2.8));
  const total = bar * 2.8;
  const top = (height - total - 18) / 2;
  const w = width - margin - 12;
  return (
    <Svg width={width} height={height}>
      <Defs>
        <LinearGradient
          id="levels"
          x1={margin}
          y1="0"
          x2={margin + w}
          y2="0"
          gradientUnits="userSpaceOnUse"
        >
          {[
            [-60, '#2e8f6c'],
            [-18, '#8fe3c0'],
            [-9, '#ffcf7a'],
            [-3, '#e2683c'],
            [0, '#ff3b30'],
          ].map(([db, color]) => (
            <Stop
              key={db}
              offset={String(((db as number) + 60) / 60)}
              stopColor={color as string}
            />
          ))}
        </LinearGradient>
      </Defs>
      {[0, 1].map(c => {
        const y = top + c * bar * 1.8;
        const segments = 30;
        const step = w / segments;
        const lit = ratio(m.rms[c]) * segments;
        const peak =
          m.peaks[c] > FLOOR
            ? Math.floor(ratio(m.peaks[c]) * segments - 0.001)
            : -1;
        return (
          <G key={c}>
            <Rect
              x={margin}
              y={y}
              width={w}
              height={bar}
              rx={4}
              fill="#1c2029"
            />
            {compact
              ? Array.from({ length: segments }, (_, i) => (
                  <Rect
                    key={i}
                    x={margin + i * step + 1}
                    y={y + 1}
                    width={step - 2}
                    height={bar - 2}
                    fill={ledColor(
                      FLOOR + ((i + 1) * -FLOOR) / segments,
                      i < lit || i === peak,
                    )}
                  />
                ))
              : [
                  <Rect
                    key="l"
                    x={margin}
                    y={y}
                    width={w * ratio(m.rms[c])}
                    height={bar}
                    rx={4}
                    fill="url(#levels)"
                  />,
                  m.peaks[c] > FLOOR && (
                    <Rect
                      key="p"
                      x={margin + w * ratio(m.peaks[c]) - 2}
                      y={y}
                      width={3}
                      height={bar}
                      fill="#ffffff"
                    />
                  ),
                ]}
            <SvgText
              x={margin - 14}
              y={y + bar / 2 + 4}
              fontSize={11}
              fill={DIM}
              textAnchor="middle"
            >
              {c ? 'R' : 'L'}
            </SvgText>
          </G>
        );
      })}
      {(width > 300
        ? [-60, -50, -40, -30, -20, -12, -6, -3, 0]
        : [-60, -40, -20, -6, 0]
      ).map(db => {
        const x = margin + w * ratio(db);
        return (
          <G key={db}>
            <Line x1={x} y1={top - 6} x2={x} y2={top - 2} stroke={DIM} />
            <SvgText
              x={x}
              y={top + total + 12}
              fontSize={10}
              fill={DIM}
              textAnchor="middle"
            >
              {db}
            </SvgText>
          </G>
        );
      })}
    </Svg>
  );
}

function Leds({
  width,
  height,
  m,
}: {
  width: number;
  height: number;
  m: MeterState;
}) {
  const segments = 30;
  const margin = 10;
  const h = height - 2 * margin - 18;
  const step = h / segments;
  const w = Math.min(90, width / 6);
  const centre = width / 2;
  const scale: number[] = [];
  let last: number | null = null;
  for (const db of [0, -6, -12, -18, -30, -40, -50, -60]) {
    const y = margin + h - ratio(db) * h;
    if (last === null || y - last >= 12) {
      scale.push(db);
      last = y;
    }
  }
  return (
    <Svg width={width} height={height}>
      {[0, 1].map(c => {
        const left = centre + (c === 0 ? -w - 30 : 30);
        const lit = ratio(m.rms[c]) * segments;
        const peak =
          m.peaks[c] > FLOOR
            ? Math.floor(ratio(m.peaks[c]) * segments - 0.001)
            : -1;
        return (
          <G key={c}>
            {Array.from({ length: segments }, (_, i) => (
              <Rect
                key={i}
                x={left}
                y={margin + h - (i + 1) * step + 1.5}
                width={w}
                height={Math.max(1, step - 3)}
                fill={ledColor(
                  FLOOR + ((i + 1) * -FLOOR) / segments,
                  i < lit || i === peak,
                )}
              />
            ))}
            <SvgText
              x={left + w / 2}
              y={margin + h + 14}
              fontSize={11}
              fill={DIM}
              textAnchor="middle"
            >
              {c ? 'R' : 'L'}
            </SvgText>
          </G>
        );
      })}
      {scale.map(db => (
        <SvgText
          key={db}
          x={centre}
          y={margin + h - ratio(db) * h + 4}
          fontSize={10}
          fill={DIM}
          textAnchor="middle"
        >
          {db}
        </SvgText>
      ))}
    </Svg>
  );
}

function Spectrum({
  width,
  height,
  m,
}: {
  width: number;
  height: number;
  m: MeterState;
}) {
  const count = m.bars.length;
  const labels = height >= 100;
  const top = 6;
  const bottom = height - (labels ? 16 : 6);
  const step = (width - 8) / Math.max(1, count);
  const gap = Math.max(1, step * 0.2);
  const y = (db: number) =>
    bottom -
    ((Math.max(DB_LOW, Math.min(DB_HIGH, db)) - DB_LOW) / (DB_HIGH - DB_LOW)) *
      (bottom - top);
  const freqs: [number, string][] = [
    [100, '100'],
    [1000, '1k'],
    [10000, '10k'],
  ];
  const lo = Math.log2(30);
  const hi = Math.log2(16000);
  return (
    <Svg width={width} height={height}>
      <Defs>
        <LinearGradient
          id="spectrum"
          x1="0"
          y1={bottom}
          x2="0"
          y2={top}
          gradientUnits="userSpaceOnUse"
        >
          <Stop offset="0" stopColor="#2e8f6c" />
          <Stop offset="0.55" stopColor="#5cdc9e" />
          <Stop offset="0.8" stopColor="#ffcc55" />
          <Stop offset="1" stopColor="#f4574a" />
        </LinearGradient>
      </Defs>
      {m.bars.map((db, i) => {
        const x = 4 + i * step;
        return (
          <G key={i}>
            <Rect
              x={x}
              y={top}
              width={step - gap}
              height={bottom - top}
              fill="#1a1e26"
            />
            <Rect
              x={x}
              y={y(db)}
              width={step - gap}
              height={bottom - y(db)}
              fill="url(#spectrum)"
            />
            {m.barPeaks[i] > DB_LOW && (
              <Rect
                x={x}
                y={y(m.barPeaks[i]) - 1}
                width={step - gap}
                height={2}
                fill="#e8eaf0"
              />
            )}
          </G>
        );
      })}
      {labels &&
        freqs.map(([f, label]) => (
          <SvgText
            key={f}
            x={4 + ((Math.log2(f) - lo) / (hi - lo)) * (width - 8)}
            y={height - 3}
            fontSize={10}
            fill={DIM}
            textAnchor="middle"
          >
            {label}
          </SvgText>
        ))}
    </Svg>
  );
}

export function VUMeter({
  style,
  width,
  height,
  meter,
  onPress,
  compact = false,
}: {
  style: VUStyle;
  width: number;
  height: number;
  meter: MeterState;
  onPress?: () => void;
  compact?: boolean; // the amplifier's window: bars as a row of LEDs
}) {
  const frame = compact ? 0 : Math.max(10, width * 0.04);
  const w = width - 2 * frame;
  const h = height - 2 * frame;
  let content: React.ReactNode = null;
  if (w > 20 && h > 20) {
    if (style === 'needles') {
      const box = needlesBox(width, height);
      content = (
        <View style={styles.centre}>
          <AnalogMeter
            width={box.w}
            height={box.h}
            stacked={box.stacked}
            needles={meter.needles}
            lit={meter.peakLit}
          />
        </View>
      );
    } else if (style === 'leds') {
      content = <Leds width={w} height={h} m={meter} />;
    } else if (style === 'spectrum') {
      content = <Spectrum width={w} height={h} m={meter} />;
    } else {
      content = <Bars width={w} height={h} m={meter} compact={compact} />;
    }
  }
  return (
    <Pressable
      onPress={onPress}
      disabled={!onPress}
      style={{ width, height, padding: style === 'needles' ? 0 : frame }}
    >
      {content}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  centre: { flex: 1, alignItems: 'center', justifyContent: 'center' },
});
