// The cover (else the artist's picture), else a disc with the artist and album on it (visuals.py CoverView).

import React, { useEffect, useState } from 'react';
import { Image, StyleSheet, Text, View } from 'react-native';

import { isLocal, localCover } from './local';
import { Library, coverUrl, folderCover, trackOf } from './protocol';
import { usePalette } from './theme';

export function Cover({
  library,
  rel,
  size,
  artist,
  album,
  radius = 12,
}: {
  library?: Library;
  rel?: string;
  size: number;
  artist?: string;
  album?: string;
  radius?: number;
}) {
  const p = usePalette();
  const [uri, setUri] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const big = size > 120;
  useEffect(() => {
    let live = true;
    setFailed(false);
    setUri(null);
    if (library && rel && isLocal(library)) {
      // the phone's own: from its media index (a size for the list, a big one for the view)
      const track = trackOf(library, rel);
      if (track) {
        localCover(track, big ? 768 : 160).then(found => live && setUri(found));
      }
    } else if (library && rel) {
      const direct = coverUrl(library, rel);
      if (direct) {
        setUri(direct);
      } else {
        if (library.kind !== 'saved') {
          folderCover(library, rel).then(found => live && setUri(found));
        }
      }
    }
    return () => {
      live = false;
    };
  }, [library, rel, big]);

  if (uri && !failed) {
    return (
      <Image
        source={{ uri }}
        resizeMode="contain"
        onError={() => setFailed(true)}
        style={{ width: size, height: size, borderRadius: size > 80 ? 0 : 4 }}
      />
    );
  }
  const small = size < 80;
  return (
    <View
      style={[
        styles.square,
        { width: size, height: size, borderRadius: small ? 4 : radius },
      ]}
    >
      <View
        style={[
          styles.round,
          {
            width: size * 0.72,
            height: size * 0.72,
            borderRadius: size * 0.36,
          },
        ]}
      >
        <View
          style={{
            width: size * 0.24,
            height: size * 0.24,
            borderRadius: size * 0.12,
            backgroundColor: '#e2683c',
          }}
        />
        {!small && (
          <View
            style={[
              styles.words,
              { top: size * 0.36 + size * 0.13, width: size * 0.5 },
            ]}
          >
            {!!artist && (
              <Text
                numberOfLines={1}
                style={{
                  color: p.visualText,
                  fontWeight: '700',
                  fontSize: Math.max(7, size * 0.05),
                }}
              >
                {artist}
              </Text>
            )}
            {!!album && (
              <Text
                numberOfLines={1}
                style={{
                  color: p.visualDim,
                  fontSize: Math.max(7, size * 0.042),
                }}
              >
                {album}
              </Text>
            )}
          </View>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  square: {
    backgroundColor: '#1c2029',
    alignItems: 'center',
    justifyContent: 'center',
  },
  round: {
    backgroundColor: '#06070a',
    alignItems: 'center',
    justifyContent: 'center',
  },
  words: { position: 'absolute', alignItems: 'center' },
});
