/**
 * One mission launch at a time, and an abort that reaches a launch still being
 * prepared.
 *
 * `startMissionProcess` awaits the readiness checks before it spawns (or
 * promotes the standby). An abort arriving in that window found no process to
 * stop and returned, and the launch then spawned the mission anyway: the
 * station had reset to pre-flight while a mission counted down to takeoff, the
 * late `running` locked the launch control, and the cockpit opened on its own
 * when that mission reached its flight stages. The abort now marks the launch
 * in preparation, and the launch checks the mark before it spawns.
 */
function createLaunchGate() {
  let starting = false;
  let cancelled = false;
  return {
    /** Claim the gate; false when a launch is already being prepared. */
    begin() {
      if (starting) return false;
      starting = true;
      cancelled = false;
      return true;
    },
    /** Cancel the launch being prepared; false when there is none. */
    cancel() {
      if (!starting) return false;
      cancelled = true;
      return true;
    },
    cancelled() {
      return starting && cancelled;
    },
    starting() {
      return starting;
    },
    end() {
      starting = false;
      cancelled = false;
    },
  };
}

module.exports = { createLaunchGate };
