/**
 * Where the flight stood when a capture finished being revealed.
 *
 * The scan over the photograph runs from that point to touchdown. The cockpit
 * is unmounted when the operator goes to INICIO and back, so the point is kept
 * here, outside the component, by capture: coming back mid-flight resumes the
 * scan where it would be instead of restarting it. One entry per capture.
 */
const revealedAt = new Map<string, number>();

const unit = (value: number): number => (Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0);

/** Record the flight progress at which `key` finished its reveal. The first record wins. */
export function recordRevealed(key: string, progress: number): number {
  const known = revealedAt.get(key);
  if (known !== undefined) return known;
  const value = unit(progress);
  revealedAt.set(key, value);
  return value;
}

/** The recorded flight progress for `key`, or `null` when its reveal has not finished. */
export function revealedProgress(key: string | null): number | null {
  return key === null ? null : revealedAt.get(key) ?? null;
}

/**
 * How far through the scan the flight is: 0 when the reveal finished, 1 at
 * touchdown, from the flight's progress between those two points.
 */
export function scanProgress(from: number, flightProgress: number | undefined): number {
  if (flightProgress === undefined || !(from < 1)) return from < 1 ? 0 : 1;
  return unit((flightProgress - from) / (1 - from));
}

/**
 * Time from a capture reaching the station to the start of its inspection, ms.
 *
 * Fixed and unconditional: at this point the photograph rises and the four
 * findings come in, whether the aircraft is still flying or already down.
 */
export const INSPECTION_DELAY_MS = 12000;

/**
 * Time from the flight entering Stage 4 to the start of its inspection when no
 * capture has reached the station by then, ms. Longer than the usual Stage 4
 * (framing window, stillness check and capture, about 8 s) plus
 * {@link INSPECTION_DELAY_MS}, so a capture that does arrive keeps its own
 * 12 s; short enough that a flight with no capture at all still gets its
 * inspection.
 */
export const INSPECTION_FALLBACK_MS = 25000;

/**
 * When the inspection starts, epoch ms, or null when there is nothing to
 * inspect yet: {@link INSPECTION_DELAY_MS} after the capture was announced
 * (`RawEvidence.timestamp`), or {@link INSPECTION_FALLBACK_MS} after Stage 4
 * was entered, whichever is earlier. Absolute instants rather than delays, so
 * a cockpit reopened mid-wait keeps the same deadline. A stamp that is not a
 * finite number is ignored.
 */
export function inspectionDeadline(capturedAt: number | null, stageFourAt: number | null): number | null {
  const candidates: number[] = [];
  if (typeof capturedAt === 'number' && Number.isFinite(capturedAt)) candidates.push(capturedAt + INSPECTION_DELAY_MS);
  if (typeof stageFourAt === 'number' && Number.isFinite(stageFourAt)) candidates.push(stageFourAt + INSPECTION_FALLBACK_MS);
  return candidates.length === 0 ? null : Math.min(...candidates);
}

/** Forget every record (a new cycle, or a test). */
export function clearRevealed(): void {
  revealedAt.clear();
}
