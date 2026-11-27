// The meters' movement, as the desktop's (visuals.VUMeter, amp.AmpView): the level with VU-like ballistics (fast
// up, ~300 ms down), the peak held a moment, the needles a mass on a spring with their 0 VU following the music's
// loudness, the spectrum's bars falling slowly. Run 30 times a second while shown and something moves.

import { useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';

import { FLOOR, Frame } from './levels';

export const VU_MIN = -20;
export const VU_MAX = 3;
export const SWEEP = 42;
export const PEAK_VU = 1;

export function needleRatio(vu: number) {
  const v = Math.max(VU_MIN - 1.5, Math.min(VU_MAX + 1, vu));
  const low = 10 ** (VU_MIN / 20);
  const high = 10 ** (VU_MAX / 20);
  return (10 ** (v / 20) - low) / (high - low);
}

const HOLD = 1.2;
const RELEASE = 0.3;
const TRIM_UP = 1;
const TRIM_DOWN = 10;
const HEADROOM = 3;
const SPRING = 16;
const DAMPING = 0.78;
export const DB_LOW = -72;
export const DB_HIGH = -12;
const TILT = 3;

export type MeterState = {
  rms: [number, number];
  peaks: [number, number];
  needles: [number, number];
  peakLit: [boolean, boolean];
  bars: number[]; // the spectrum's, dB, tilted
  barPeaks: number[];
};

const rest = (bars: number): MeterState => ({
  rms: [FLOOR, FLOOR],
  peaks: [FLOOR, FLOOR],
  needles: [needleRatio(VU_MIN - 2), needleRatio(VU_MIN - 2)],
  peakLit: [false, false],
  bars: Array(bars).fill(DB_LOW),
  barPeaks: Array(bars).fill(DB_LOW),
});

// bars: how many spectrum bars are drawn (0: none)
export function useMeter(
  read: () => Frame | null,
  bars: number,
  active = true,
): MeterState {
  const [state, setState] = useState<MeterState>(() => rest(bars));
  const s = useRef({
    rms: [FLOOR, FLOOR],
    peaks: [
      [FLOOR, 0],
      [FLOOR, 0],
    ] as [number, number][],
    needles: [needleRatio(VU_MIN - 2), needleRatio(VU_MIN - 2)],
    speeds: [0, 0],
    lit: [0, 0],
    reference: -18,
    bars: [] as number[],
    barPeaks: [] as [number, number][],
    last: Date.now() / 1000,
  });
  const [foreground, setForeground] = useState(
    AppState.currentState === 'active',
  );
  useEffect(() => {
    const sub = AppState.addEventListener('change', value =>
      setForeground(value === 'active'),
    );
    return () => sub.remove();
  }, []);

  useEffect(() => {
    if (!active || !foreground) {
      return; // off screen, in the background: nothing computed nor drawn
    }
    let frame = 0;
    let quiet = 0;
    const m = s.current;
    m.last = Date.now() / 1000;
    const tick = () => {
      const now = Date.now() / 1000;
      const elapsed = Math.min(0.1, now - m.last);
      m.last = now;
      const level = read();
      const target = level ? level.rms : [FLOOR, FLOOR];
      const fall = Math.exp(-elapsed / RELEASE);
      let moved = false;
      for (let c = 0; c < 2; c++) {
        const value = m.rms[c];
        const next =
          target[c] > value
            ? target[c]
            : target[c] + (value - target[c]) * fall;
        moved ||= Math.abs(next - value) > 0.01;
        m.rms[c] = next;
        const peak = level ? level.peak[c] : FLOOR;
        if (peak >= m.peaks[c][0] || now - m.peaks[c][1] > HOLD) {
          if (peak >= m.peaks[c][0]) {
            m.peaks[c] = [peak, now];
          } else if (m.peaks[c][0] > FLOOR) {
            m.peaks[c] = [
              Math.max(FLOOR, m.peaks[c][0] - 30 * elapsed),
              m.peaks[c][1],
            ];
            moved = true;
          }
        }
      }
      const loudness = Math.max(...m.rms);
      if (loudness > -40) {
        const goal = Math.min(0, Math.max(-24, loudness + HEADROOM));
        m.reference +=
          (goal - m.reference) *
          Math.min(1, elapsed / (goal > m.reference ? TRIM_UP : TRIM_DOWN));
      }
      // the needles: a spring after the latest level, in small steps
      const steps = Math.max(1, Math.ceil(elapsed / 0.004));
      const dt = elapsed / steps;
      for (let c = 0; c < 2; c++) {
        const goal =
          target[c] > FLOOR
            ? needleRatio(target[c] - m.reference)
            : needleRatio(VU_MIN - 2);
        let position = m.needles[c];
        let speed = m.speeds[c];
        for (let i = 0; i < steps; i++) {
          speed +=
            (SPRING ** 2 * (goal - position) - 2 * DAMPING * SPRING * speed) *
            dt;
          position += speed * dt;
        }
        moved ||=
          Math.abs(position - m.needles[c]) > 0.0004 || Math.abs(speed) > 0.002;
        m.needles[c] = position;
        m.speeds[c] = speed;
        if (position >= needleRatio(PEAK_VU)) {
          m.lit[c] = now;
        }
      }
      // the spectrum, grouped into the bars drawn, tilted +3 dB an octave about 1 kHz
      if (bars > 0) {
        if (m.bars.length !== bars) {
          m.bars = Array(bars).fill(DB_LOW);
          m.barPeaks = Array(bars).fill([DB_LOW, now]);
        }
        const spectrum = level?.spectrum;
        for (let i = 0; i < bars; i++) {
          let value = DB_LOW;
          if (spectrum && spectrum.length) {
            const from = Math.floor((i * spectrum.length) / bars);
            const to = Math.max(
              from + 1,
              Math.floor(((i + 1) * spectrum.length) / bars),
            );
            const db = Math.max(...spectrum.slice(from, to));
            const lo = Math.log2(level!.low);
            const hi = Math.log2(level!.high);
            const centre = 2 ** (lo + ((hi - lo) * (i + 0.5)) / bars);
            value = Math.max(DB_LOW, db + TILT * Math.log2(centre / 1000));
          }
          const old = m.bars[i];
          const next = value > old ? value : value + (old - value) * fall;
          moved ||= Math.abs(next - old) > 0.05;
          m.bars[i] = next;
          const [peak, since] = m.barPeaks[i];
          if (next >= peak) {
            m.barPeaks[i] = [next, now];
          } else if (now - since > HOLD * 0.5 && peak > DB_LOW) {
            m.barPeaks[i] = [Math.max(DB_LOW, peak - 24 * elapsed), since];
            moved = true;
          }
        }
      }
      const lit = [now - m.lit[0] < 0.35, now - m.lit[1] < 0.35] as [
        boolean,
        boolean,
      ];
      quiet = moved || level ? 0 : quiet + 1;
      if (quiet < 3) {
        setState({
          rms: [m.rms[0], m.rms[1]],
          peaks: [m.peaks[0][0], m.peaks[1][0]],
          needles: [m.needles[0], m.needles[1]],
          peakLit: lit,
          bars: [...m.bars],
          barPeaks: m.barPeaks.map(p => p[0]),
        });
      }
      // at rest: looked at 5 times a second, for the music to come back
      frame = setTimeout(tick, quiet < 3 ? 33 : 200) as unknown as number;
    };
    tick();
    return () => clearTimeout(frame);
  }, [read, bars, active, foreground]);
  return state;
}
