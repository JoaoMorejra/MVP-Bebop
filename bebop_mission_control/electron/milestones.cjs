/**
 * Flight-script milestones, as the main process forwards them on `bmg:milestone`.
 *
 * Two producers feed one channel so the renderer's narration queue sees the
 * whole script in order:
 *
 * - the mission process, which logs `[MILESTONE <key>] <json>` on stdout
 *   (`mvp_mission_bebop/telemetry/milestones.py`; the pattern is pinned by
 *   `test/test_contracts.py::MILESTONE_LINE`);
 * - this process, which owns the two milestones that precede the mission's
 *   first line: `mission.start` when the operator's launch has spawned the
 *   mission, and `mission.countdown_3` three seconds before the countdown
 *   `mission.py --countdown` is running ends.
 */

/** A milestone line on the mission's stdout. IPC contract with `milestones.py`. */
const MILESTONE_LINE = /\[MILESTONE ([a-z]+\.[a-z0-9_]+)\] (\{.*\})$/;

/**
 * An alert line: a failure, abort or failsafe the mission hands to the station
 * to speak, because under the station it has no voice of its own
 * (`announcer.station_narrates`). Same contract, pinned by
 * `test/test_contracts.py::ALERT_LINE`.
 */
const ALERT_LINE = /\[ALERT ([a-z]+\.[a-z0-9_]+)\] (\{.*\})$/;

/**
 * The stage marker each step logs on entry, `--- [STEP N: <name>] ---`. IPC
 * contract pinned by `test/test_contracts.py::STEP_LINE`.
 */
const STEP_LINE = /\[STEP ([1-5]):/;

/** Seconds before liftoff at which the countdown call is made. */
const COUNTDOWN_CALL_SEC = 3;

/**
 * Longest partial line kept between chunks. A milestone line is a few hundred
 * bytes; anything this long without a newline is not one, and holding it would
 * only grow the buffer for the life of the process.
 */
const MAX_PARTIAL_LINE = 64 * 1024;

/**
 * Reassemble stdout chunks into lines.
 *
 * A chunk boundary can fall anywhere, including inside a marker, and one chunk
 * can carry several lines; matching on chunks lost the first case and kept only
 * the first match of the second. `onLine` receives each complete line without
 * its `\n` or trailing `\r`, in order; `flush` delivers a trailing partial line.
 */
function createLineBuffer(onLine) {
  let partial = '';
  const deliver = (raw) => onLine(raw.endsWith('\r') ? raw.slice(0, -1) : raw);
  return {
    push(chunk) {
      const lines = (partial + String(chunk)).split('\n');
      partial = lines.pop() ?? '';
      if (partial.length > MAX_PARTIAL_LINE) partial = '';
      for (const raw of lines) deliver(raw);
    },
    flush() {
      if (partial) deliver(partial);
      partial = '';
    },
  };
}

/**
 * Build a line-oriented stage-marker matcher for one mission process.
 *
 * Emits the stage number of every `[STEP N:` line, in the order the lines
 * arrive, including several in one chunk: a Stage 3 skipped without a target
 * logs its marker and Stage 4's in the same millisecond.
 */
function createStepMarkerParser(emit) {
  return createLineBuffer((text) => {
    const match = STEP_LINE.exec(text);
    if (match) emit(Number(match[1]));
  });
}

/**
 * Build a line-oriented milestone matcher for one mission process.
 *
 * `push` takes raw stdout chunks; matching is done on reassembled lines (see
 * `createLineBuffer`). A payload that does not parse still yields the
 * milestone, with an empty payload: the narration it drives needs the key, not
 * the detail.
 */
function createMilestoneParser(emit, now = Date.now) {
  const matchLine = (text) => {
    const milestone = MILESTONE_LINE.exec(text);
    const alert = milestone ? null : ALERT_LINE.exec(text);
    const match = milestone || alert;
    if (!match) return;
    let payload = {};
    try {
      const parsed = JSON.parse(match[2]);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) payload = parsed;
    } catch (_error) {
      payload = {};
    }
    emit({ kind: milestone ? 'milestone' : 'alert', key: match[1], payload, at: now(), source: 'mission' });
  };

  return createLineBuffer(matchLine);
}

/**
 * Raise the station-owned script milestones for one launch.
 *
 * `mission.start` goes out immediately; `mission.countdown_3` when
 * `COUNTDOWN_CALL_SEC` seconds of the countdown remain, or immediately after
 * `mission.start` when the countdown is shorter than that. Returns a cancel
 * function, called when the mission process ends so a killed launch does not
 * announce a takeoff that will never happen.
 */
function scheduleScriptMilestones(countdownSec, emit, now = Date.now) {
  const countdown = Number.isFinite(Number(countdownSec)) ? Math.max(0, Number(countdownSec)) : 0;
  const payload = { countdown_sec: countdown };
  emit({ kind: 'milestone', key: 'mission.start', payload, at: now(), source: 'station' });

  const delayMs = Math.max(0, countdown - COUNTDOWN_CALL_SEC) * 1000;
  let timer = setTimeout(() => {
    timer = null;
    emit({ kind: 'milestone', key: 'mission.countdown_3', payload, at: now(), source: 'station' });
  }, delayMs);

  return () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };
}

/**
 * The one milestone a launch owns: `mission.start`, when the operator's launch
 * has spawned the mission. The countdown belongs to the mission process
 * (`TakeoffStep._countdown` raises `mission.countdown` every whole second and
 * `mission.countdown_3` at the clearance call): timed here from the spawn it
 * ran about 14 s ahead of the real one.
 */
function emitLaunchMilestone(countdownSec, emit, now = Date.now) {
  const countdown = Number.isFinite(Number(countdownSec)) ? Math.max(0, Number(countdownSec)) : 0;
  emit({ kind: 'milestone', key: 'mission.start', payload: { countdown_sec: countdown }, at: now(), source: 'station' });
}

/**
 * `mission.countdown` ticks for a countdown the station itself runs: a bench
 * routine for a later stage has no countdown hold of its own, so the station
 * counts (`benchCountdown.cjs`) and raises the same ticks the mission would.
 * Returns a cancel function.
 */
function scheduleCountdownTicks(countdownSec, emit, now = Date.now) {
  const total = Number.isFinite(Number(countdownSec)) ? Math.max(0, Math.ceil(Number(countdownSec))) : 0;
  const tick = (remaining) =>
    emit({ kind: 'milestone', key: 'mission.countdown', payload: { remaining_sec: remaining }, at: now(), source: 'station' });
  tick(total);
  let remaining = total;
  if (remaining <= 0) return () => undefined;
  let timer = setInterval(() => {
    remaining -= 1;
    tick(remaining);
    if (remaining <= 0) {
      clearInterval(timer);
      timer = null;
    }
  }, 1000);
  return () => {
    if (timer !== null) clearInterval(timer);
    timer = null;
  };
}

module.exports = {
  ALERT_LINE,
  COUNTDOWN_CALL_SEC,
  MILESTONE_LINE,
  STEP_LINE,
  createLineBuffer,
  createMilestoneParser,
  createStepMarkerParser,
  emitLaunchMilestone,
  scheduleCountdownTicks,
  scheduleScriptMilestones,
};
