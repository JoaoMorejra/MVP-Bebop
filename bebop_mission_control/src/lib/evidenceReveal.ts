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

/** Forget every record (a new cycle, or a test). */
export function clearRevealed(): void {
  revealedAt.clear();
}
