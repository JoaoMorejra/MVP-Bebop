/**
 * Altitude envelope of a launch document, per launch surface (D1).
 *
 * Exact mirror of `mvp_mission_bebop/launch_envelope.py`: the same constants and
 * the same rules, checked against the same cases
 * (`test/fixtures/altitude_envelope_cases.json`) by both test suites, so a
 * document the station lets through is a document the mission accepts.
 *
 * The operator states the altitude over the floor. On the pad top
 * (`platform.height_m > 0` and `platform.takeoff_from_ground` not true) the
 * lowest altitude is the pad top plus {@link padClearanceM}; on the floor (the
 * flag true, or no pad) it is {@link ALTITUDE_MIN_M}. The highest is
 * {@link ALTITUDE_MAX_M}, capped {@link VENUE_HEADROOM_M} under a known venue
 * ceiling. Pure.
 */
import { getPath } from './paths';

export const ALTITUDE_MIN_M = 0.1;
export const ALTITUDE_MAX_M = 3.0;
export const ALTITUDE_STEP_M = 0.05;
/** Least height kept above the pad top; the factory `kinematics.takeoff_settle_min_altitude_m`. */
export const PAD_CLEARANCE_M = 0.3;
export const VENUE_HEADROOM_M = 0.1;
/** Height at which the firmware ends its launch profile; `NATIVE_HOVER_HEIGHT_M` of the mission. */
export const NATIVE_HOVER_HEIGHT_M = 1.0;
/** Fixed headroom of the pad-top ceiling; `estimation/ceiling_schedule.TOTEM_CEILING_HEADROOM_M` (D2). */
export const TOTEM_CEILING_HEADROOM_M = 0.6;
/** Altitude over the floor above which the pad top applies the operator margin; `launch_envelope.TOTEM_MARGIN_UNLOCK_ABOVE_M`. */
export const TOTEM_MARGIN_UNLOCK_ABOVE_M = 2.0;

export const ALTITUDE_PATH = 'kinematics.target_altitude_m';
export const PLATFORM_HEIGHT_PATH = 'platform.height_m';
export const TAKEOFF_FROM_GROUND_PATH = 'platform.takeoff_from_ground';
export const RTL_ENABLED_PATH = 'rtl.enabled';
const VENUE_CEILING_PATH = 'platform.venue_ceiling_agl_m';
const SETTLE_FLOOR_PATH = 'kinematics.takeoff_settle_min_altitude_m';
const MARGIN_PATH = 'kinematics.altitude_ceiling_margin_m';

/** Comparison slack, metres: `1.0 + 0.3` must equal the 1.3 the slider offers. */
const TOLERANCE_M = 1e-9;

export interface AltitudeRange {
  min: number;
  max: number;
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/** Configured pad height, metres; 0 when absent or not a finite number. */
export function padHeightM(doc: unknown): number {
  const raw = getPath(doc, PLATFORM_HEIGHT_PATH);
  return finite(raw) ? raw : 0;
}

/** Whether the airframe takes off from the floor: the flag set, or no pad configured. */
export function launchesFromGround(doc: unknown): boolean {
  return getPath(doc, TAKEOFF_FROM_GROUND_PATH) === true || padHeightM(doc) <= 0;
}

/**
 * Whether the ceiling applies `kinematics.altitude_ceiling_margin_m` rather than
 * {@link TOTEM_CEILING_HEADROOM_M}: from the floor, and on the pad top above
 * {@link TOTEM_MARGIN_UNLOCK_ABOVE_M}. A target that is not a finite number keeps
 * the pad top locked. Mirror of `launch_envelope.margin_unlocked`.
 */
export function marginUnlocked(doc: unknown): boolean {
  if (launchesFromGround(doc)) return true;
  const target = getPath(doc, ALTITUDE_PATH);
  return finite(target) && target > TOTEM_MARGIN_UNLOCK_ABOVE_M + TOLERANCE_M;
}

/** `max(PAD_CLEARANCE_M, kinematics.takeoff_settle_min_altitude_m)`. */
export function padClearanceM(doc: unknown): number {
  const settle = getPath(doc, SETTLE_FLOOR_PATH);
  return Math.max(PAD_CLEARANCE_M, finite(settle) ? settle : PAD_CLEARANCE_M);
}

/**
 * Accepted range of `kinematics.target_altitude_m` over the floor, both bounds
 * inclusive. Empty (min above max) when a low venue leaves no room above the pad.
 */
export function altitudeEnvelope(doc: unknown): AltitudeRange {
  const min = launchesFromGround(doc) ? ALTITUDE_MIN_M : Math.max(ALTITUDE_MIN_M, padHeightM(doc) + padClearanceM(doc));
  const venue = getPath(doc, VENUE_CEILING_PATH);
  const max = finite(venue) ? Math.min(ALTITUDE_MAX_M, venue - VENUE_HEADROOM_M) : ALTITUDE_MAX_M;
  return { min, max };
}

/**
 * The envelope rounded inward to the slider resolution: the lowest bound up and
 * the highest down to a multiple of {@link ALTITUDE_STEP_M}. Every value the
 * slider offers is then inside {@link altitudeEnvelope}.
 */
export function altitudeSliderRange(doc: unknown): AltitudeRange {
  const { min, max } = altitudeEnvelope(doc);
  const steps = (value: number) => Math.round(value / ALTITUDE_STEP_M * 1e6) / 1e6;
  const up = Math.ceil(steps(min) - TOLERANCE_M) * ALTITUDE_STEP_M;
  const down = Math.floor(steps(max) + TOLERANCE_M) * ALTITUDE_STEP_M;
  return { min: Math.round(up * 100) / 100, max: Math.round(down * 100) / 100 };
}

/** Whether `value` lies inside the envelope of `doc`. */
export function inAltitudeEnvelope(doc: unknown, value: number): boolean {
  const { min, max } = altitudeEnvelope(doc);
  return finite(value) && value >= min - TOLERANCE_M && value <= max + TOLERANCE_M;
}

/**
 * The path the mission would refuse `doc` on, in the order of
 * `validate_launch_envelope`, or `null` when it accepts it. An absent boolean
 * is the default the station backfills; a present one must be `true` or `false`.
 */
export function altitudeEnvelopeViolation(doc: unknown): string | null {
  for (const path of [RTL_ENABLED_PATH, TAKEOFF_FROM_GROUND_PATH]) {
    const value = getPath(doc, path);
    if (value !== undefined && typeof value !== 'boolean') return path;
  }
  const height = getPath(doc, PLATFORM_HEIGHT_PATH);
  if (height !== undefined && (!finite(height) || height < 0)) return PLATFORM_HEIGHT_PATH;
  const altitude = getPath(doc, ALTITUDE_PATH);
  if (!finite(altitude) || !inAltitudeEnvelope(doc, altitude)) return ALTITUDE_PATH;
  return null;
}

/** `value` moved into the slider range of `doc`. */
export function clampAltitude(doc: unknown, value: number): number {
  const { min, max } = altitudeSliderRange(doc);
  return Math.min(max, Math.max(min, value));
}

/**
 * The nominal altitude ceiling of `doc`, in metres over the floor; `null` when
 * the altitude (or, with {@link marginUnlocked}, the margin) is not a finite
 * number.
 *
 * Mirror of `estimation/ceiling_schedule.CeilingSchedule` after its takeoff
 * transient, restated over the floor: from the floor `target + margin`; on the
 * pad top `pad + max(target - pad, NATIVE_HOVER_HEIGHT_M) + m`, with `m` the
 * margin above {@link TOTEM_MARGIN_UNLOCK_ABOVE_M} and
 * {@link TOTEM_CEILING_HEADROOM_M} at or below it (D2). A known venue ceiling
 * caps every branch.
 */
export function ceilingFloorFrame(doc: unknown): number | null {
  const target = getPath(doc, ALTITUDE_PATH);
  if (!finite(target)) return null;
  let margin: unknown = TOTEM_CEILING_HEADROOM_M;
  if (marginUnlocked(doc)) {
    margin = getPath(doc, MARGIN_PATH);
    if (!finite(margin)) return null;
  }
  let ceiling: number;
  if (launchesFromGround(doc)) {
    ceiling = target + (margin as number);
  } else {
    const pad = padHeightM(doc);
    ceiling = pad + Math.max(target - pad, NATIVE_HOVER_HEIGHT_M) + (margin as number);
  }
  const venue = getPath(doc, VENUE_CEILING_PATH);
  return finite(venue) ? Math.min(ceiling, venue) : ceiling;
}
