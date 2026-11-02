/** Input types that take typed text; the others (range, checkbox, radio, button) never do. */
const TEXT_INPUT_TYPES: ReadonlySet<string> = new Set(['text', 'search', 'email', 'password', 'url', 'tel', 'number']);

/**
 * Checks whether the key event target takes typed text: a text-like `INPUT`, a
 * `TEXTAREA` or an editable element, where Ctrl+C is copy. Used by R11 to
 * leave those keys alone.
 *
 * A range slider, a checkbox, a radio, a button and a `SELECT` do not: after a
 * drag of the camera tilt or of the volume the focus stays on the slider, and
 * treating it as a text field ignored the abort without a word.
 */
export function isTextInput(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === 'INPUT') return TEXT_INPUT_TYPES.has(((target as HTMLInputElement).type || 'text').toLowerCase());
  if (tag === 'TEXTAREA') return true;
  if (target.isContentEditable || target.getAttribute('contenteditable') === 'true') return true;
  return false;
}
