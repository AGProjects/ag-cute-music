// Line icons, as the desktop's (cutemusic/icons.py): strokes in the text color.

import React from 'react';
import Svg, { Circle, Line, Path, Polygon, Rect } from 'react-native-svg';

export type IconName =
  | 'play'
  | 'pause'
  | 'next'
  | 'previous'
  | 'shuffle'
  | 'repeat'
  | 'back'
  | 'close'
  | 'search'
  | 'lan'
  | 'settings'
  | 'more'
  | 'queue'
  | 'chevron'
  | 'disc'
  | 'lyrics'
  | 'browse'
  | 'meter'
  | 'speaker'
  | 'saved'
  | 'bars'
  | 'needles'
  | 'leds'
  | 'spectrum';

export function Icon({
  name,
  size = 22,
  color,
}: {
  name: IconName;
  size?: number;
  color: string;
}) {
  const stroke = {
    stroke: color,
    strokeWidth: 1.8,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    fill: 'none',
  };
  const solid = {
    fill: color,
    stroke: color,
    strokeWidth: 1.2,
    strokeLinejoin: 'round' as const,
  };
  const shapes: Record<IconName, React.ReactNode> = {
    play: <Polygon points="7,4.5 19.5,12 7,19.5" {...solid} />,
    pause: (
      <>
        <Rect x="6" y="4.5" width="4" height="15" rx="1" {...solid} />
        <Rect x="14" y="4.5" width="4" height="15" rx="1" {...solid} />
      </>
    ),
    next: (
      <>
        <Polygon points="4,5 14,12 4,19" {...solid} />
        <Line x1="18" y1="5" x2="18" y2="19" {...stroke} strokeWidth={2.4} />
      </>
    ),
    previous: (
      <>
        <Polygon points="20,5 10,12 20,19" {...solid} />
        <Line x1="6" y1="5" x2="6" y2="19" {...stroke} strokeWidth={2.4} />
      </>
    ),
    shuffle: (
      <Path
        d="M3 7h4c5 0 5 10 10 10h4M17 14l3 3-3 3M3 17h4c1.6 0 2.6-1 3.4-2.3M13.6 9.3C14.4 8 15.4 7 17 7h4M17 4l3 3-3 3"
        {...stroke}
      />
    ),
    repeat: (
      <Path
        d="M4 11V9a3 3 0 0 1 3-3h12M16 3l3 3-3 3M20 13v2a3 3 0 0 1-3 3H5M8 21l-3-3 3-3"
        {...stroke}
      />
    ),
    back: <Path d="M15 4l-8 8 8 8" {...stroke} strokeWidth={2.2} />,
    chevron: <Path d="M9 5l7 7-7 7" {...stroke} />,
    close: <Path d="M6 6l12 12M18 6L6 18" {...stroke} />,
    search: (
      <>
        <Circle cx="10.5" cy="10.5" r="6" {...stroke} />
        <Line x1="15" y1="15" x2="20" y2="20" {...stroke} />
      </>
    ),
    lan: (
      <>
        <Rect x="3" y="4" width="18" height="11" rx="1.5" {...stroke} />
        <Path d="M12 15v4M7 20h10" {...stroke} />
      </>
    ),
    settings: (
      <>
        <Circle cx="12" cy="12" r="3" {...stroke} />
        <Path
          d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1"
          {...stroke}
        />
      </>
    ),
    more: (
      <>
        <Circle cx="5" cy="12" r="1.6" fill={color} />
        <Circle cx="12" cy="12" r="1.6" fill={color} />
        <Circle cx="19" cy="12" r="1.6" fill={color} />
      </>
    ),
    queue: <Path d="M4 6h16M4 12h10M4 18h10M17 14v6l4-3z" {...stroke} />,
    disc: (
      <>
        <Circle cx="12" cy="12" r="9" {...stroke} />
        <Circle cx="12" cy="12" r="2.5" {...stroke} />
      </>
    ),
    lyrics: <Path d="M5 6h14M5 10h14M5 14h9M5 18h6" {...stroke} />,
    browse: (
      <Path
        d="M4 5h6M4 10h6M4 15h6M4 20h6M14 5v15M14 5l6 2.5v4L14 9"
        {...stroke}
      />
    ),
    saved: <Path d="M12 4v11M7 10l5 5 5-5M5 20h14" {...stroke} />,
    speaker: (
      <>
        <Rect x="5" y="3" width="14" height="18" rx="2" {...stroke} />
        <Circle cx="12" cy="14" r="3.2" {...stroke} />
        <Circle cx="12" cy="7.5" r="1.1" fill={color} />
      </>
    ),
    meter: (
      <>
        <Rect x="3" y="5" width="18" height="14" rx="2" {...stroke} />
        <Path d="M7 15a5 5 0 0 1 10 0M12 15l3-4" {...stroke} />
      </>
    ),
    bars: <Path d="M3 8h14M3 15h9" {...stroke} strokeWidth={3} />,
    needles: <Path d="M3 17a9 9 0 0 1 18 0M12 17l4-7" {...stroke} />,
    leds: (
      <Path
        d="M7 4v2M7 9v2M7 14v2M7 19v1M17 9v2M17 14v2M17 19v1"
        {...stroke}
        strokeWidth={2.6}
      />
    ),
    spectrum: (
      <Path
        d="M4 20v-6M8 20V8M12 20v-9M16 20V5M20 20v-7"
        {...stroke}
        strokeWidth={2.4}
      />
    ),
  };
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24">
      {shapes[name]}
    </Svg>
  );
}
