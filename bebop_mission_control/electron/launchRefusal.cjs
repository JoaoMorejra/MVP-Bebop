/**
 * A launch the mission refused before commanding anything.
 *
 * `mission.py` judges the launch document once it is merged (the mission mode
 * and the altitude envelope, `mvp_mission_bebop/launch_envelope.py`). A
 * document it cannot fly is reported twice on stdout -- the
 * `mission.launch_refused` alert, `{field, reason}`, and the plain line
 * `[LAUNCH_REFUSED] <field>: <reason>` -- and the process exits with
 * `EXIT_LAUNCH_REFUSED` (78, `EX_CONFIG`). The alert is preferred; the line is
 * the fallback when the alert could not be encoded or parsed.
 *
 * Before this, the same refusal left as a plain `SystemExit` (exit 1): the
 * overlay closed on a cockpit that never moved and the reason was only in the
 * diagnostics terminal.
 */
const { ALERT_LINE, LAUNCH_REFUSED_ALERT, createLineBuffer } = require('./milestones.cjs');

/** Exit status of a refused launch document (`engine/exit_codes.py`). */
const EXIT_LAUNCH_REFUSED = 78;

/** The fallback line `refuse_launch` prints in `mission.py`. */
const REFUSED_LINE = /^\[LAUNCH_REFUSED\] ([^:\s]+): (.+)$/;

/** Reason given when the process exited 78 and neither report was read. */
const UNKNOWN_REASON = 'documento de lançamento recusado pela missão';

/**
 * `{field, reason}` from an alert payload, or null when either is missing.
 *
 * @param {unknown} payload
 */
function refusalFromPayload(payload) {
  if (!payload || typeof payload !== 'object') return null;
  const { field, reason } = /** @type {Record<string, unknown>} */ (payload);
  if (typeof field !== 'string' || !field.trim() || typeof reason !== 'string' || !reason.trim()) return null;
  return { field: field.trim(), reason: reason.trim() };
}

/**
 * Follow one mission process's stdout and say, at its exit, whether it refused
 * its launch document and why.
 *
 * `push` takes raw stdout chunks (reassembled into lines, `createLineBuffer`).
 * `refusal(code)` is null for any exit other than {@link EXIT_LAUNCH_REFUSED};
 * for 78 it is the last alert read, else the last fallback line, else a
 * generic reason charged to `mission_mode`.
 */
function createLaunchRefusalTracker() {
  let fromAlert = null;
  let fromLine = null;
  const buffer = createLineBuffer((text) => {
    const alert = ALERT_LINE.exec(text);
    if (alert && alert[1] === LAUNCH_REFUSED_ALERT) {
      try {
        fromAlert = refusalFromPayload(JSON.parse(alert[2])) ?? fromAlert;
      } catch (_error) {
        // a payload that does not parse leaves the fallback line to speak
      }
      return;
    }
    const line = REFUSED_LINE.exec(text);
    if (line) fromLine = { field: line[1], reason: line[2].trim() };
  });
  return {
    push: (chunk) => buffer.push(chunk),
    flush: () => buffer.flush(),
    refusal(code) {
      if (code !== EXIT_LAUNCH_REFUSED) return null;
      return fromAlert ?? fromLine ?? { field: 'mission_mode', reason: UNKNOWN_REASON };
    },
  };
}

module.exports = { EXIT_LAUNCH_REFUSED, REFUSED_LINE, createLaunchRefusalTracker, refusalFromPayload };
