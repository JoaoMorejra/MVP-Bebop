import { useState } from 'react';
import type { StreamStatus } from '../types/bmg';
import { useBridge } from './useBridge';
import { useInterval } from './useInterval';

const DOWN: StreamStatus = {
  running: false,
  live: false,
  frames: 0,
  fps: 0,
  ageSec: null,
  source: '',
  width: 0,
  height: 0,
};

/**
 * Health of the MJPEG bridge on port 9090.
 *
 * Asked over IPC rather than fetched from the renderer: the page is served
 * from an ephemeral localhost port, so a direct `fetch` to 9090 is a
 * cross-origin request and fails silently.
 *
 * `running` and `live` are different facts and the cockpit needs both. The
 * bridge process can be perfectly healthy while the aircraft sends no video,
 * and conflating the two is what made the panel latch on a stale frame rate.
 */
export function useStreamHealth(active: boolean) {
  const bridge = useBridge();
  const [status, setStatus] = useState<StreamStatus>(DOWN);

  useInterval(
    () => {
      if (!bridge) return;
      void bridge
        .getStreamStatus()
        .then((next) => setStatus({ ...DOWN, ...next }))
        .catch(() => setStatus(DOWN));
    },
    active && bridge ? 1000 : null
  );

  return status;
}
