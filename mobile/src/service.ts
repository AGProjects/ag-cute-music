// The playback service: the lock screen, the notification, headphones and the car.

import TrackPlayer, { Event } from 'react-native-track-player';

import { next, previous } from './player';

export default async function PlaybackService() {
  TrackPlayer.addEventListener(Event.RemotePlay, () => TrackPlayer.play());
  TrackPlayer.addEventListener(Event.RemotePause, () => TrackPlayer.pause());
  TrackPlayer.addEventListener(Event.RemoteStop, () => TrackPlayer.stop());
  TrackPlayer.addEventListener(Event.RemoteNext, () => next());
  TrackPlayer.addEventListener(Event.RemotePrevious, () => previous());
  TrackPlayer.addEventListener(Event.RemoteSeek, event =>
    TrackPlayer.seekTo(event.position),
  );
  // (interruptions, ducking: handled by the player itself, autoHandleInterruptions)
  // what the player does, in the log (release-logs.sh: ReactNativeJS)
  TrackPlayer.addEventListener(Event.PlaybackError, event =>
    console.warn(`[player] error ${event.code}: ${event.message}`),
  );
  TrackPlayer.addEventListener(Event.PlaybackState, event =>
    console.log(
      `[player] state ${event.state}${
        'error' in event && event.error ? ` ${event.error.message}` : ''
      }`,
    ),
  );
  TrackPlayer.addEventListener(Event.PlaybackActiveTrackChanged, event =>
    console.log(`[player] now ${event.index}: ${event.track?.url ?? '-'}`),
  );
}
