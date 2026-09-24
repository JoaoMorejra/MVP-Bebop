import { useCallback, useEffect, useRef, useState } from 'react';
import { useBridge } from './useBridge';

/**
 * The camera gimbal, commanded in real time.
 *
 * `tilt` is the angle the bridge confirmed publishing — never the position of
 * the handle that asked for it. A control that reports its own input as though
 * it were the state of the aircraft is the one thing a gimbal readout must not
 * do: the operator has to be able to tell a command that was sent from a
 * command that arrived.
 *
 * Commands are coalesced to one per frame. A dragged slider fires sixty changes
 * a second and only the last of each frame describes where the handle now is;
 * sending the intermediate ones would queue up a drag for the camera to replay.
 */
export function useCameraTilt(observed?: number | null) {
  const bridge = useBridge();
  const [tilt, setTilt] = useState<number | null>(null);
  const frameRef = useRef<number | null>(null);
  const queuedRef = useRef<number | null>(null);

  useEffect(() => {
    if (!bridge) return;
    void bridge
      .getCameraTilt()
      .then((state) => setTilt(state.tilt))
      .catch(() => undefined);
    return bridge.onCameraTiltChanged((event) => setTilt(event.tilt));
  }, [bridge]);

  /**
   * What the aircraft's camera is actually doing.
   *
   * The station is not the only thing commanding this gimbal — the mission
   * drives it through its own stages, to the search attitude on the sweep and
   * to nadir for the capture — and those commands never pass through this hook.
   * The telemetry bridge watches `/bebop/move_camera` itself and reports every
   * angle on it, whoever sent it, which is what lets the slider track a flight
   * it is not driving.
   */
  useEffect(() => {
    if (observed === undefined || observed === null) return;
    setTilt(observed);
  }, [observed]);

  useEffect(
    () => () => {
      if (frameRef.current !== null) window.cancelAnimationFrame(frameRef.current);
    },
    []
  );

  const set = useCallback(
    (degrees: number) => {
      if (!bridge || !Number.isFinite(degrees)) return;
      queuedRef.current = degrees;
      if (frameRef.current !== null) return;
      frameRef.current = window.requestAnimationFrame(() => {
        frameRef.current = null;
        const value = queuedRef.current;
        queuedRef.current = null;
        if (value === null) return;
        void bridge.setCameraTilt(value).catch(() => undefined);
      });
    },
    [bridge]
  );

  /** Forget the last reading. Used when a mission ends and the bridge goes with it. */
  const reset = useCallback(() => setTilt(null), []);

  return { tilt, set, reset, available: Boolean(bridge) };
}
