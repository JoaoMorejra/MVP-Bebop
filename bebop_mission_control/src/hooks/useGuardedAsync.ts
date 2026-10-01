import { useCallback, useRef, useState } from 'react';

/**
 * Run an async action at most once at a time.
 *
 * The guard is a ref, checked synchronously: a double click delivers both
 * clicks before React renders the first one's `busy`, so a state flag alone
 * lets the second through. `busy` mirrors the ref for rendering, and both
 * clear when the action settles, whether it resolved or threw.
 */
export function useGuardedAsync(fn: () => Promise<void>): { run: () => Promise<void>; busy: boolean } {
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(false);

  const run = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      await fn();
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }, [fn]);

  return { run, busy };
}
