import { useMemo } from 'react';
import type { BmgAPI } from '../types/bmg';

/**
 * The Electron bridge, or null when the renderer is running in a plain browser
 * (`npm run dev`). Every consumer must handle null: the UI degrades to a
 * synthetic source rather than pretending hardware is present.
 */
export function useBridge(): BmgAPI | null {
  return useMemo(() => (typeof window === 'undefined' ? null : window.bmgAPI ?? null), []);
}
