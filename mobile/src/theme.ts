// The desktop's palettes (cutemusic/theme.py, visuals.py): dark the default, light and Follow system as settings.

import { createContext, useContext } from 'react';

export type ThemeName = 'system' | 'light' | 'dark';

export type Palette = {
  dark: boolean;
  window: string;
  base: string;
  alternate: string;
  button: string;
  text: string;
  dim: string;
  placeholder: string;
  line: string;
  highlight: string;
  highlightedText: string;
  accent: string; // the amber of the Original | Negative buttons, the Web tab
  visual: string; // the visualization pane: always dark, as on the desktop
  visualText: string;
  visualDim: string;
};

const visual = {
  visual: '#101318',
  visualText: '#e8eaf0',
  visualDim: '#8a90a0',
  accent: '#f0a030',
};

export const DARK: Palette = {
  dark: true,
  window: '#1b1e25',
  base: '#14171c',
  alternate: '#1f232b',
  button: '#2a2f3a',
  text: '#e8eaf0',
  dim: '#8a90a0',
  placeholder: '#6c7282',
  line: '#30353f',
  highlight: '#3d6fb8',
  highlightedText: '#ffffff',
  ...visual,
};

export const LIGHT: Palette = {
  dark: false,
  window: '#efefef',
  base: '#ffffff',
  alternate: '#f7f7f7',
  button: '#e2e2e2',
  text: '#1b1e25',
  dim: '#5c6270',
  placeholder: '#8a90a0',
  line: '#d4d4d4',
  highlight: '#308cc6',
  highlightedText: '#ffffff',
  ...visual,
};

// The color markers (cutemusic/colors.py): Finder's six
export const MARKERS: [string, string][] = [
  ['red', '#ff3b30'],
  ['orange', '#ff9500'],
  ['yellow', '#ffcc00'],
  ['green', '#34c759'],
  ['blue', '#007aff'],
  ['purple', '#af52de'],
];
export const MARKER_COLOR: Record<string, string> = Object.fromEntries(MARKERS);

export const ThemeContext = createContext<Palette>(DARK);
export const usePalette = () => useContext(ThemeContext);
