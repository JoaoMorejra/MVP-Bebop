import { useCallback, useEffect, useState } from 'react';
import type { VoiceLevel } from '../types/bmg';
import { useBridge } from './useBridge';

const STORAGE_KEY = 'bmg.voice-level.v1';

const DEFAULT: VoiceLevel = { volume: 1, muted: false };

function restore(): VoiceLevel {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT;
    const parsed = JSON.parse(raw) as Partial<VoiceLevel>;
    return {
      volume:
        typeof parsed.volume === 'number' && Number.isFinite(parsed.volume)
          ? Math.max(0, Math.min(1, parsed.volume))
          : DEFAULT.volume,
      muted: Boolean(parsed.muted),
    };
  } catch {
    return DEFAULT;
  }
}

/**
 * How loud the copilot is, and whether it speaks at all.
 *
 * The level is a real setting, not a UI affordance: it reaches
 * `announcer.py`, which scales the PCM samples on their way to the device. That
 * keeps the station's voice independent of everything else the machine is
 * playing, which is what an operator turning down "the copilot" means — and it
 * is why this does not touch the host mixer.
 *
 * Muting is honoured before synthesis rather than after. A muted copilot does
 * not spend a Live session and several seconds producing audio nobody will
 * hear; the station falls back to its own beat for the report's cadence.
 *
 * Remembered per operator across sessions, and re-pushed whenever the daemon is
 * replaced, so a level set before the copilot ever started is not lost.
 */
export function useVoiceLevel() {
  const bridge = useBridge();
  const [level, setLevel] = useState<VoiceLevel>(restore);

  // The host is the authority once it has one; this reconciles a level the
  // daemon already holds with the one restored from storage.
  useEffect(() => {
    if (!bridge) return;
    void bridge.setVoiceLevel(level).catch(() => undefined);
    // Deliberately once, on connect: later changes go through `apply` below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bridge]);

  const apply = useCallback(
    (next: Partial<VoiceLevel>) => {
      setLevel((previous) => {
        const merged: VoiceLevel = {
          volume:
            typeof next.volume === 'number' && Number.isFinite(next.volume)
              ? Math.max(0, Math.min(1, next.volume))
              : previous.volume,
          muted: typeof next.muted === 'boolean' ? next.muted : previous.muted,
        };
        try {
          window.localStorage.setItem(STORAGE_KEY, JSON.stringify(merged));
        } catch {
          /* private mode or quota: the host still has the live value */
        }
        if (bridge) void bridge.setVoiceLevel(merged).catch(() => undefined);
        return merged;
      });
    },
    [bridge]
  );

  const setVolume = useCallback((volume: number) => apply({ volume }), [apply]);
  const toggleMute = useCallback(() => apply({ muted: !level.muted }), [apply, level.muted]);

  return { level, setVolume, toggleMute, available: Boolean(bridge) };
}
