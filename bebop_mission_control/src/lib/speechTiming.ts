/**
 * How long the launch line takes to say (D3).
 *
 * The 90 to 100 % stretch of the launch ring follows the clearance line being
 * spoken. Its length is measured the first time it is heard and kept per text
 * in `localStorage` (`bmg.speech-ms.v1`); before that, or when nothing was
 * heard, it is estimated from the length of the text. Every storage access is
 * guarded and never throws.
 */
import type { StorageLike } from './parameterModes';

export const SPEECH_MS_KEY = 'bmg.speech-ms.v1';

/** Measured pace of the copilot's pt-BR voice, per character of text. */
const MS_PER_CHAR = 68;
const MIN_ESTIMATE_MS = 1500;
const MAX_SPEECH_MS = 14000;
/** A measurement below this is a cut-off or a cache hit, not a spoken line. */
const MIN_MEASURED_MS = 300;
/** Texts kept in the store; the oldest written goes first. */
const MAX_TEXTS = 32;

/** Expected duration of `text`, in ms: 68 ms per character, within [1500, 14000]. */
export function estimateSpeechMs(text: string): number {
  const estimate = Math.round(text.length * MS_PER_CHAR);
  return Math.min(MAX_SPEECH_MS, Math.max(MIN_ESTIMATE_MS, estimate));
}

function readStore(storage: StorageLike | null): Record<string, number> {
  if (!storage) return {};
  try {
    const raw = storage.getItem(SPEECH_MS_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const store: Record<string, number> = {};
    for (const [text, ms] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof ms === 'number' && Number.isFinite(ms)) store[text] = ms;
    }
    return store;
  } catch {
    return {};
  }
}

/** The measured duration of `text`, or `null` when none was recorded. */
export function readSpeechMs(storage: StorageLike | null, text: string): number | null {
  const ms = readStore(storage)[text];
  return typeof ms === 'number' ? ms : null;
}

/** Keep the measured duration of `text`; values outside [300, 14000] ms are ignored. */
export function recordSpeechMs(storage: StorageLike | null, text: string, ms: number): void {
  if (!storage || !Number.isFinite(ms) || ms < MIN_MEASURED_MS || ms > MAX_SPEECH_MS) return;
  const store = readStore(storage);
  delete store[text];
  store[text] = Math.round(ms);
  const keys = Object.keys(store);
  for (const key of keys.slice(0, Math.max(0, keys.length - MAX_TEXTS))) delete store[key];
  try {
    storage.setItem(SPEECH_MS_KEY, JSON.stringify(store));
  } catch {
    /* the estimate stands in for a measurement that could not be kept */
  }
}
