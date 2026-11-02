/**
 * The cockpit keyboard nudge keys, caught in the main process.
 *
 * Same reason as `abortShortcut.cjs`: the video feed is an `<iframe>`, which
 * never hands its key events to the window, and the operator's focus is on the
 * feed while flying. `before-input-event` sees every key the window receives
 * before the page does.
 *
 * While the cockpit declares the nudge armed (`bmg:cockpit-nudge-armed`),
 * W A S D Q R (by physical key, without Ctrl, Meta or Alt) are consumed here
 * and handed to the renderer as `bmg:nudge-key` with `{type, code}`, where
 * `useNudge` handles them exactly like a window key. Auto-repeats are consumed
 * but not delivered: the renderer's heartbeat, not the keyboard's repeat rate,
 * paces the intent. A key-up is always delivered when its key-down was, even
 * if the nudge was disarmed in between, so a held key cannot be left pressed.
 * Disarmed, every other key passes untouched.
 */

const NUDGE_CODES = new Set(['KeyW', 'KeyA', 'KeyS', 'KeyD', 'KeyQ', 'KeyR']);

/**
 * Whether `input` is a nudge key, without modifiers.
 *
 * @param {{type?: string, code?: string, control?: boolean, meta?: boolean, alt?: boolean}} input
 */
function isNudgeKeyInput(input) {
  if (!input || (input.type !== 'keyDown' && input.type !== 'keyUp')) return false;
  if (!NUDGE_CODES.has(input.code)) return false;
  return !(input.control || input.meta || input.alt);
}

/**
 * @param {object} options
 * @param {(key: {type: 'keydown' | 'keyup', code: string}) => void} options.deliver
 *   Sends `bmg:nudge-key` to the renderer.
 */
function createNudgeShortcutGuard({ deliver }) {
  let armed = false;
  const held = new Set();
  return {
    /** @param {unknown} value  The `armed` flag the cockpit published. */
    setArmed(value) {
      armed = value === true;
    },
    isArmed: () => armed,
    /**
     * `before-input-event` handler.
     *
     * @param {{preventDefault: () => void}} event
     * @param {object} input
     * @returns {boolean} Whether the key was taken as a nudge key.
     */
    handle(event, input) {
      if (!isNudgeKeyInput(input)) return false;
      if (input.type === 'keyUp') {
        if (!held.has(input.code)) return false;
        held.delete(input.code);
        event.preventDefault();
        deliver({ type: 'keyup', code: input.code });
        return true;
      }
      if (!armed) return false;
      event.preventDefault();
      if (input.isAutoRepeat || held.has(input.code)) return true;
      held.add(input.code);
      deliver({ type: 'keydown', code: input.code });
      return true;
    },
  };
}

module.exports = { createNudgeShortcutGuard, isNudgeKeyInput };
