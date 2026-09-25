/**
 * The countdown of a single bench routine, run by the station.
 *
 * A full script holds in TakeoffStep while the launch countdown runs, so its
 * process can be spawned at the start of it. A routine for a later stage has
 * no such hold and would start flying behind the overlay, so the station
 * counts instead: it raises the same two countdown milestones a launch raises
 * (`scheduleScriptMilestones`) and spawns the routine only at zero. Cancelling
 * before then drops both the spawn and any milestone still to come, which is
 * what makes an abort during the countdown leave nothing behind.
 */

const { scheduleScriptMilestones } = require('./milestones.cjs');

/**
 * @param {number} countdownSec  Seconds to count; clamped to at least zero.
 * @param {object} hooks
 * @param {(message: object) => void} hooks.emit  Milestone sink.
 * @param {() => void} hooks.spawn  Called once, at zero, unless cancelled.
 * @returns {() => boolean} Cancel. True when it stopped a pending spawn.
 */
function deferBenchSpawn(countdownSec, { emit, spawn }) {
  const seconds = Number.isFinite(Number(countdownSec)) ? Math.max(0, Number(countdownSec)) : 0;
  let pending = true;
  const cancelMilestones = scheduleScriptMilestones(seconds, emit);
  const timer = setTimeout(() => {
    pending = false;
    spawn();
  }, seconds * 1000);

  return () => {
    cancelMilestones();
    if (!pending) return false;
    pending = false;
    clearTimeout(timer);
    return true;
  };
}

module.exports = { deferBenchSpawn };
