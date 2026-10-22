/**
 * The cockpit abort shortcut, caught in the main process (D8).
 *
 * The renderer's `keydown` listener misses the key in two places the operator
 * actually is: an `<input type="range">` (the camera tilt or the volume slider
 * keeps the focus after a drag, and the listener took any input for a text
 * field) and an `<iframe>` (the video feed), which never hands its key events
 * to the window. `before-input-event` sees every key the window receives
 * before the page does.
 *
 * While the cockpit declares the abort available (`bmg:cockpit-abort-armed`),
 * Ctrl/Cmd+C (by physical key, without Shift or Alt) and Escape are consumed
 * here -- `preventDefault` also stops the hidden menu's Copy -- and handed to
 * the renderer as `bmg:abort-shortcut`, which runs the same handler as the
 * abort button. A held key fires once. Disarmed, every key passes untouched.
 */

/**
 * Whether `input` is the abort shortcut.
 *
 * @param {{type?: string, key?: string, code?: string, control?: boolean, meta?: boolean, shift?: boolean, alt?: boolean, isAutoRepeat?: boolean}} input
 *   Electron's `before-input-event` input.
 */
function isAbortShortcutInput(input) {
  if (!input || input.type !== 'keyDown' || input.isAutoRepeat) return false;
  if (input.key === 'Escape') return true;
  return Boolean((input.control || input.meta) && input.code === 'KeyC' && !input.shift && !input.alt);
}

/**
 * @param {object} options
 * @param {() => void} options.deliver  Sends `bmg:abort-shortcut` to the renderer.
 */
function createAbortShortcutGuard({ deliver }) {
  let armed = false;
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
     * @returns {boolean} Whether the key was taken as an abort.
     */
    handle(event, input) {
      if (!armed || !isAbortShortcutInput(input)) return false;
      event.preventDefault();
      deliver();
      return true;
    },
  };
}

module.exports = { createAbortShortcutGuard, isAbortShortcutInput };
