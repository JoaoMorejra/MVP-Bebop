/**
 * Keyboard shortcuts that abort the mission from the cockpit.
 *
 * Escape (R11) and Ctrl/Cmd+C. The physical key (`code`) is read for C so a
 * layout that types another character on that key still aborts. A held key
 * never fires twice, and Ctrl+Shift+C (the inspector) and Alt combinations are
 * not captured.
 */
export type AbortShortcut = 'escape' | 'copy';

export function isAbortShortcut(
  event: Pick<KeyboardEvent, 'key' | 'code' | 'ctrlKey' | 'metaKey' | 'shiftKey' | 'altKey' | 'repeat'>
): AbortShortcut | null {
  if (event.repeat) return null;
  if (event.key === 'Escape') return 'escape';
  if (event.code === 'KeyC' && (event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey) {
    return 'copy';
  }
  return null;
}
