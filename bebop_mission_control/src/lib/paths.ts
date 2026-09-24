/** Dotted-path access over the nested `MissionParameters` document. */

export type Doc = Record<string, unknown>;

export function getPath(doc: unknown, path: string): unknown {
  return path.split('.').reduce<unknown>((node, key) => {
    if (node && typeof node === 'object') return (node as Doc)[key];
    return undefined;
  }, doc);
}

/**
 * Returns a structurally shared clone with `path` replaced. Only the objects
 * along the path are copied, so unrelated branches keep their identity and the
 * document round-trips untouched.
 */
export function setPath<T>(doc: T, path: string, value: unknown): T {
  const keys = path.split('.');
  const clone = (node: unknown): Doc =>
    node && typeof node === 'object' && !Array.isArray(node) ? { ...(node as Doc) } : {};

  const root = clone(doc);
  let cursor: Doc = root;
  for (let i = 0; i < keys.length - 1; i += 1) {
    cursor[keys[i]] = clone(cursor[keys[i]]);
    cursor = cursor[keys[i]] as Doc;
  }
  cursor[keys[keys.length - 1]] = value;
  return root as T;
}

export function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (a === null || b === null) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((item, i) => deepEqual(item, b[i]));
  }
  if (typeof a === 'object' && typeof b === 'object') {
    const ka = Object.keys(a as Doc);
    const kb = Object.keys(b as Doc);
    if (ka.length !== kb.length) return false;
    return ka.every((k) => deepEqual((a as Doc)[k], (b as Doc)[k]));
  }
  return false;
}
