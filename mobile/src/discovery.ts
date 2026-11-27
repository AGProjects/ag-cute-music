// The libraries AG CuteMusic shares on the LAN, found by Bonjour (_agcutemusic._tcp, cutemusic/sharing.py).

import { useEffect, useState } from 'react';
import Zeroconf, { Service } from 'react-native-zeroconf';

import { SERVICE_TYPE, info } from './protocol';

export type Found = {
  key: string;
  id: string;
  name: string;
  address: string;
  version?: string;
};

// Every address announced, IPv4 first (link-local IPv6 last): a computer has some the phone cannot reach (a
// container's bridge, a VPN), and an older AG CuteMusic announces them all
function candidates(service: Service): string[] {
  if (!service.port) {
    return [];
  }
  const ipv6 = [...(service.ipv6 || [])].sort(
    (a, b) =>
      Number(a.toLowerCase().startsWith('fe80')) -
      Number(b.toLowerCase().startsWith('fe80')),
  );
  return [...(service.ipv4 || []), ...ipv6.map(a => `[${a}]`)].map(
    host => `http://${host}:${service.port}`,
  );
}

// The first address that answers /info, tried in order, each for a moment
async function reachable(service: Service): Promise<string | null> {
  for (const where of candidates(service)) {
    try {
      await info(where, 2000);
      return where;
    } catch {
      // the next one
    }
  }
  return null;
}

export function useDiscovery(): { found: Found[]; error: string | null } {
  const [found, setFound] = useState<Record<string, Found>>({});
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let zeroconf: Zeroconf;
    try {
      zeroconf = new Zeroconf();
    } catch (e: any) {
      setError(String(e?.message || e));
      return;
    }
    let live = true;
    zeroconf.on('resolved', async service => {
      const where = await reachable(service);
      if (!where || !live) {
        return;
      }
      const txt = service.txt || {};
      // announced as "<name> (<id>)": the name alone shown
      const name = txt.name || service.name.replace(/\s*\([0-9a-f]+\)$/i, '');
      setFound(previous => ({
        ...previous,
        [service.name]: {
          key: service.name,
          id: txt.id || service.name,
          name,
          address: where,
          version: txt.version,
        },
      }));
    });
    zeroconf.on('remove', name =>
      setFound(previous => {
        const rest = { ...previous };
        delete rest[name];
        return rest;
      }),
    );
    zeroconf.on('error', e => setError(String(e?.message || e)));
    zeroconf.scan({ type: SERVICE_TYPE, protocol: 'tcp', domain: 'local.' });
    return () => {
      live = false;
      zeroconf.stop();
      zeroconf.removeDeviceListeners();
    };
  }, []);
  return {
    found: Object.values(found).sort((a, b) => a.name.localeCompare(b.name)),
    error,
  };
}
