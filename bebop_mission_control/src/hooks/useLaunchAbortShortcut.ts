import { useEffect, useRef } from 'react';
import { isAbortShortcut } from '../lib/abortShortcut';
import { isTextInput } from '../lib/textInput';
import { useBridge } from './useBridge';

/**
 * Escape and Ctrl/Cmd+C cancel the launch being prepared on pre-flight (D5).
 *
 * The same channel as the cockpit abort: with the bridge the main process
 * takes the keys before the page while armed (`bmg:cockpit-abort-armed`) and
 * hands them back on `onAbortShortcut`; without it a window listener does the
 * same, leaving text fields alone. Disarming publishes `false`, except when
 * `handoff` says the cockpit now owns the channel: its effects run before
 * this one in the commit that switches tabs, and a late `false` from here
 * would disarm the abort of the flight that just started.
 */
export function useLaunchAbortShortcut(opts: { armed: boolean; handoff: boolean; onAbort: () => void }): void {
  const { armed, handoff } = opts;
  const bridge = useBridge();
  const viaMain = Boolean(bridge?.onAbortShortcut && bridge.setCockpitAbortArmed);
  const armedRef = useRef(armed);
  armedRef.current = armed;
  const handoffRef = useRef(handoff);
  handoffRef.current = handoff;
  const onAbortRef = useRef(opts.onAbort);
  onAbortRef.current = opts.onAbort;
  const published = useRef(false);

  useEffect(() => {
    if (!viaMain) return;
    if (armed) {
      bridge!.setCockpitAbortArmed!(true);
      published.current = true;
    } else if (published.current) {
      published.current = false;
      if (!handoffRef.current) bridge!.setCockpitAbortArmed!(false);
    }
  }, [bridge, viaMain, armed]);

  useEffect(() => {
    if (!viaMain) return;
    return () => {
      if (published.current && !handoffRef.current) bridge!.setCockpitAbortArmed!(false);
      published.current = false;
    };
  }, [bridge, viaMain]);

  useEffect(() => {
    if (!viaMain) return;
    return bridge!.onAbortShortcut!(() => {
      if (armedRef.current) onAbortRef.current();
    });
  }, [bridge, viaMain]);

  useEffect(() => {
    if (viaMain || !armed) return;
    const onKey = (event: KeyboardEvent) => {
      if (isAbortShortcut(event) === null) return;
      if (isTextInput(event.target)) return;
      event.preventDefault();
      onAbortRef.current();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [viaMain, armed]);
}
