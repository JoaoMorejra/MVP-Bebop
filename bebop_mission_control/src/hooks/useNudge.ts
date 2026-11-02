import { useCallback, useEffect, useRef, useState } from 'react';
import type { BmgAPI, NudgeIntent, NudgeKeyEvent } from '../types/bmg';
import { isTextInput } from '../lib/textInput';
import {
  NUDGE_HEARTBEAT_MS,
  NUDGE_RELEASE,
  isNudgeKey,
  nudgeIntent,
  reduceNudgeKeys,
  sameIntent,
} from '../lib/nudge';

type KeyLike = Pick<KeyboardEvent, 'type' | 'code' | 'repeat' | 'ctrlKey' | 'metaKey' | 'altKey'>;

const EMPTY: ReadonlySet<string> = new Set();

/**
 * Keyboard nudge of the cockpit (`lib/nudge.ts`).
 *
 * The intent is sent on every change and re-sent every
 * {@link NUDGE_HEARTBEAT_MS} while any key is held, never once per keyboard
 * auto-repeat. Releasing every key sends the all-zero intent at once and stops
 * the heartbeat. Losing the window (blur), hiding it, disabling the hook or
 * unmounting it releases the keys the same way; the mission's own deadman
 * covers the case where nothing arrives at all (a frozen renderer).
 *
 * Keys typed into a text field are left alone, and `preventDefault` is called only on the six mapped keys while enabled.
 * With the bridge's main-process interception (`electron/nudgeShortcut.cjs`)
 * the keys taken before the page (the video iframe never hands them to the
 * window) arrive through `onNudgeKey` and are handled identically.
 */
export function useNudge(
  bridge: Partial<BmgAPI> | null | undefined,
  enabled: boolean,
): { pressed: ReadonlySet<string>; intent: NudgeIntent; active: boolean } {
  const [pressed, setPressed] = useState<ReadonlySet<string>>(EMPTY);
  const pressedRef = useRef<ReadonlySet<string>>(EMPTY);
  const sentRef = useRef<NudgeIntent>(NUDGE_RELEASE);
  const enabledRef = useRef(enabled);
  const bridgeRef = useRef(bridge);
  const heartbeatRef = useRef<ReturnType<typeof setInterval> | null>(null);
  bridgeRef.current = bridge;

  const send = useCallback((intent: NudgeIntent) => {
    sentRef.current = intent;
    bridgeRef.current?.sendNudge?.(intent);
  }, []);

  const stopHeartbeat = useCallback(() => {
    if (heartbeatRef.current !== null) {
      clearInterval(heartbeatRef.current);
      heartbeatRef.current = null;
    }
  }, []);

  const apply = useCallback(
    (next: ReadonlySet<string>) => {
      if (next === pressedRef.current) return;
      pressedRef.current = next;
      setPressed(next);
      const intent = nudgeIntent(next);
      if (!sameIntent(intent, sentRef.current)) send(intent);
      if (sameIntent(intent, NUDGE_RELEASE)) {
        stopHeartbeat();
      } else if (heartbeatRef.current === null) {
        heartbeatRef.current = setInterval(() => send(nudgeIntent(pressedRef.current)), NUDGE_HEARTBEAT_MS);
      }
    },
    [send, stopHeartbeat],
  );

  const release = useCallback(() => {
    stopHeartbeat();
    if (pressedRef.current.size > 0) {
      pressedRef.current = EMPTY;
      setPressed(EMPTY);
    }
    send(NUDGE_RELEASE);
  }, [send, stopHeartbeat]);

  const handleKey = useCallback(
    (event: KeyLike, target: EventTarget | null, preventDefault?: () => void) => {
      if (!isNudgeKey(event.code)) return;
      if (event.type === 'keydown') {
        if (!enabledRef.current || isTextInput(target)) return;
        if (event.ctrlKey || event.metaKey || event.altKey) return;
        preventDefault?.();
      }
      apply(reduceNudgeKeys(pressedRef.current, event));
    },
    [apply],
  );

  useEffect(() => {
    enabledRef.current = enabled;
    bridgeRef.current?.setCockpitNudgeArmed?.(enabled);
    if (!enabled && (pressedRef.current.size > 0 || !sameIntent(sentRef.current, NUDGE_RELEASE))) release();
  }, [enabled, release]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => handleKey(event, event.target, () => event.preventDefault());
    const onBlur = () => release();
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') release();
    };
    window.addEventListener('keydown', onKey);
    window.addEventListener('keyup', onKey);
    window.addEventListener('blur', onBlur);
    document.addEventListener('visibilitychange', onVisibility);
    const unsubscribe = bridgeRef.current?.onNudgeKey?.((event: NudgeKeyEvent) =>
      handleKey({ type: event.type, code: event.code, repeat: false, ctrlKey: false, metaKey: false, altKey: false }, null),
    );
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('keyup', onKey);
      window.removeEventListener('blur', onBlur);
      document.removeEventListener('visibilitychange', onVisibility);
      unsubscribe?.();
      bridgeRef.current?.setCockpitNudgeArmed?.(false);
      release();
    };
  }, [handleKey, release]);

  const intent = nudgeIntent(pressed);
  return { pressed, intent, active: enabled && !sameIntent(intent, NUDGE_RELEASE) };
}
