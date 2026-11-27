// The analog VU meters' window (cutemusic/vufaces.py): one face across a black-bezelled window for both channels,
// lit from behind by a bulb under each, the VU scale (-20..+3), the needles and the PEAK lamps. The face is drawn
// once a size; only the needles and lamps move.

import React, { memo } from 'react';
import { View } from 'react-native';
import Svg, {
  Circle,
  ClipPath,
  Defs,
  G,
  Line,
  LinearGradient,
  RadialGradient,
  Rect,
  Stop,
  Text as SvgText,
} from 'react-native-svg';

import { SWEEP, needleRatio } from './meter';

type Box = { x: number; y: number; w: number; h: number };

export function faceRects(window: Box, stacked: boolean): Box[] {
  const gap = (stacked ? window.h : window.w) * 0.015;
  if (stacked) {
    const h = (window.h - 2 * gap) / 2;
    return [0, 1].map(i => ({
      x: window.x + gap,
      y: window.y + gap + i * h,
      w: window.w - 2 * gap,
      h,
    }));
  }
  const w = (window.w - 2 * gap) / 2;
  return [0, 1].map(i => ({
    x: window.x + gap + i * w,
    y: window.y + gap,
    w,
    h: window.h - 2 * gap,
  }));
}

const small = (f: Box) => Math.max(6, f.h * 0.085);
function pivot(f: Box) {
  const radius = Math.min(f.w * 0.6, f.h * 1.5);
  return { x: f.x + f.w / 2, y: f.y + small(f) * 1.7 + radius * 1.02, radius };
}
function point(p: { x: number; y: number }, ratio: number, r: number) {
  const a = ((-90 - SWEEP + 2 * SWEEP * ratio) * Math.PI) / 180;
  return { x: p.x + r * Math.cos(a), y: p.y + r * Math.sin(a) };
}

const INK = '#2a2219';
const RED = '#b3261e';

function Face({ f, label, id }: { f: Box; label: string; id: string }) {
  const p = pivot(f);
  const s = small(f);
  const scale = [];
  const steps = 48;
  const zero = needleRatio(0);
  for (let i = 0; i < steps; i++) {
    const a = point(p, i / steps, p.radius * 0.86);
    const b = point(p, (i + 1) / steps, p.radius * 0.86);
    const red = i / steps >= zero;
    scale.push(
      <Line
        key={`a${i}`}
        x1={a.x}
        y1={a.y}
        x2={b.x}
        y2={b.y}
        stroke={red ? RED : INK}
        strokeWidth={red ? 3 : 1.4}
      />,
    );
  }
  const ticks = [-20, -10, -7, -5, -3, -2, -1, 0, 1, 2, 3].map(vu => {
    const long = [-20, -10, -5, 0, 3].includes(vu);
    const a = point(p, needleRatio(vu), p.radius * 0.86);
    const b = point(p, needleRatio(vu), p.radius * (long ? 0.94 : 0.9));
    return (
      <Line
        key={`t${vu}`}
        x1={a.x}
        y1={a.y}
        x2={b.x}
        y2={b.y}
        stroke={vu > 0 ? RED : INK}
        strokeWidth={1.6}
      />
    );
  });
  // the numbers: fewer on a small dial
  const wanted =
    f.w > 220
      ? [0, -20, 3, -10, -5, -7, -3]
      : f.w > 130
      ? [0, -20, 3, -10, -5]
      : [0, -20, 3];
  const numbers = wanted.map(vu => {
    const at = point(p, needleRatio(vu), p.radius * 1.02);
    return (
      <SvgText
        key={`n${vu}`}
        x={at.x}
        y={at.y + s * 0.35}
        fontSize={s}
        fill={vu > 0 ? RED : INK}
        textAnchor="middle"
      >
        {vu > 0 ? `+${vu}` : String(Math.abs(vu))}
      </SvgText>
    );
  });
  const row = f.y + f.h - s * 0.75;
  return (
    <G clipPath={`url(#${id})`}>
      {scale}
      {ticks}
      {numbers}
      {f.h > 50 && (
        <SvgText
          x={f.x + f.w / 2}
          y={f.y + f.h * 0.66}
          fontSize={f.h * 0.15}
          fontWeight="bold"
          fill={INK}
          textAnchor="middle"
        >
          VU
        </SvgText>
      )}
      {f.w > 120 && (
        <>
          <SvgText
            x={f.x + s * 0.6}
            y={row}
            fontSize={s * 0.85}
            fontWeight="bold"
            letterSpacing={1}
            fill={INK}
          >
            {label}
          </SvgText>
          <SvgText
            x={f.x + f.w - s * 2.6}
            y={row}
            fontSize={s * 0.85}
            fontWeight="bold"
            letterSpacing={1}
            fill={RED}
            textAnchor="end"
          >
            PEAK
          </SvgText>
        </>
      )}
    </G>
  );
}

// The window, its faces and scales: what does not move
export const MeterFaces = memo(function MeterFaces({
  width,
  height,
  stacked,
}: {
  width: number;
  height: number;
  stacked: boolean;
}) {
  const window = { x: 2, y: 2, w: width - 4, h: height - 4 };
  const faces = faceRects(window, stacked);
  const whole = {
    x: faces[0].x,
    y: faces[0].y,
    w: faces[1].x + faces[1].w - faces[0].x,
    h: faces[1].y + faces[1].h - faces[0].y,
  };
  return (
    <Svg width={width} height={height}>
      <Defs>
        {faces.map((f, i) => (
          <RadialGradient
            key={`b${i}`}
            id={`bulb${i}`}
            cx={f.x + f.w / 2}
            cy={f.y + f.h * 0.7}
            r={f.w * 0.62}
            gradientUnits="userSpaceOnUse"
          >
            <Stop offset="0" stopColor="rgb(255,230,168)" stopOpacity={1} />
            <Stop
              offset="0.35"
              stopColor="rgb(246,204,120)"
              stopOpacity={0.92}
            />
            <Stop
              offset="0.75"
              stopColor="rgb(214,158,72)"
              stopOpacity={0.47}
            />
            <Stop offset="1" stopColor="rgb(185,127,47)" stopOpacity={0} />
          </RadialGradient>
        ))}
        <LinearGradient
          id="glare"
          x1="0"
          y1={window.y}
          x2="0"
          y2={window.y + window.h}
          gradientUnits="userSpaceOnUse"
        >
          <Stop offset="0" stopColor="#ffffff" stopOpacity={0.18} />
          <Stop offset="0.45" stopColor="#ffffff" stopOpacity={0.03} />
          <Stop offset="0.46" stopColor="#ffffff" stopOpacity={0} />
        </LinearGradient>
        {faces.map((f, i) => (
          <ClipPath key={`c${i}`} id={`face${i}`}>
            <Rect x={f.x} y={f.y} width={f.w} height={f.h} />
          </ClipPath>
        ))}
      </Defs>
      <Rect
        x={0.6}
        y={0.6}
        width={width - 1.2}
        height={height - 1.2}
        rx={5}
        fill="#14161a"
        stroke="#7d8188"
        strokeWidth={1.2}
      />
      <Rect
        x={whole.x}
        y={whole.y}
        width={whole.w}
        height={whole.h}
        rx={3}
        fill="#8c5f26"
      />
      {faces.map((f, i) => (
        <Rect
          key={`l${i}`}
          x={whole.x}
          y={whole.y}
          width={whole.w}
          height={whole.h}
          rx={3}
          fill={`url(#bulb${i})`}
        />
      ))}
      {faces.map((f, i) => (
        <Face
          key={`f${i}`}
          f={f}
          label={i ? 'RIGHT' : 'LEFT'}
          id={`face${i}`}
        />
      ))}
      <Rect
        x={window.x}
        y={window.y}
        width={window.w}
        height={window.h}
        rx={4}
        fill="url(#glare)"
      />
    </Svg>
  );
});

// The needles and the PEAK lamps, over the faces
export function Needles({
  width,
  height,
  stacked,
  needles,
  lit,
}: {
  width: number;
  height: number;
  stacked: boolean;
  needles: [number, number];
  lit: [boolean, boolean];
}) {
  const faces = faceRects({ x: 2, y: 2, w: width - 4, h: height - 4 }, stacked);
  return (
    <Svg
      width={width}
      height={height}
      style={{ position: 'absolute', left: 0, top: 0 }}
    >
      <Defs>
        {faces.map((f, i) => (
          <ClipPath key={i} id={`n${i}`}>
            <Rect x={f.x} y={f.y} width={f.w} height={f.h} />
          </ClipPath>
        ))}
      </Defs>
      {faces.map((f, i) => {
        const p = pivot(f);
        const ratio = Math.max(-0.02, needles[i]);
        const a = point(p, ratio, p.radius * 0.36);
        const b = point(p, ratio, p.radius * 0.97);
        const s = small(f);
        const lamp = {
          x: f.x + f.w - s * 1.6,
          y: f.y + f.h - s * 1.1,
          r: s * 0.42,
        };
        return (
          <G key={i}>
            <G clipPath={`url(#n${i})`}>
              <Line
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke="#16110b"
                strokeWidth={Math.max(1.4, f.h * 0.018)}
              />
            </G>
            {lit[i] && (
              <Circle
                cx={lamp.x}
                cy={lamp.y}
                r={lamp.r * 2.6}
                fill="#ff3b30"
                opacity={0.35}
              />
            )}
            <Circle
              cx={lamp.x}
              cy={lamp.y}
              r={lamp.r}
              fill={lit[i] ? '#ff6b5e' : '#4c1210'}
              stroke="#3a3d42"
              strokeWidth={0.8}
            />
          </G>
        );
      })}
    </Svg>
  );
}

export function AnalogMeter({
  width,
  height,
  stacked,
  needles,
  lit,
}: {
  width: number;
  height: number;
  stacked: boolean;
  needles: [number, number];
  lit: [boolean, boolean];
}) {
  if (width < 20 || height < 20) {
    return null;
  }
  return (
    <View style={{ width, height }}>
      <MeterFaces width={width} height={height} stacked={stacked} />
      <Needles
        width={width}
        height={height}
        stacked={stacked}
        needles={needles}
        lit={lit}
      />
    </View>
  );
}
