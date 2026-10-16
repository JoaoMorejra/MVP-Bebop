import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ALL_PARAMETERS, TRACKED_PATHS } from '../lib/parameterSchema';
import { applySchemaFields, fillMissing, reconcileCoupledPaths, setLinkedPath } from '../lib/launchDocument';
import { activeConflicts } from '../lib/parameterConflicts';
import { parseMissionMode, readMissionMode, DEFAULT_MISSION_MODE, type MissionMode } from '../lib/missionMode';
import {
  ACTIVE_MODE_KEY,
  migrateLegacyKeys,
  presetKey,
  readStoredDoc,
  switchModeDocument,
  workingKey,
  writeStoredDoc,
} from '../lib/parameterModes';
import { deepEqual, getPath, setPath } from '../lib/paths';
import { useBridge } from './useBridge';

export type ParamsDoc = Record<string, unknown>;

/** Mirrors written by each tracked path, so a slider counts as one change. */
const MIRRORS_BY_PATH: ReadonlyMap<string, string[]> = new Map(
  ALL_PARAMETERS.filter((spec) => spec.mirrors).map((spec) => [spec.path, spec.mirrors!.map((m) => m.path)])
);

/**
 * Whether `working` holds the operator's preset.
 *
 * Compared over the six flight parameters the preset is about, not the whole
 * document: `mission.py` rewrites calibration statistics into the file on
 * every run, and `no_fly` is the arming mode rather than a tuning, so either
 * would make a preset read as inactive while every value the operator set is
 * still in place. A field the preset does not hold (saved before the field
 * existed) is not compared, as {@link applySchemaFields} does not apply it.
 */
export function matchesPreset(working: ParamsDoc | null, preset: ParamsDoc | null): boolean {
  if (!working || !preset) return false;
  return ALL_PARAMETERS.every((spec) => {
    const value = getPath(preset, spec.path);
    return value === undefined || deepEqual(getPath(working, spec.path), value);
  });
}

/**
 * Reads, edits and persists the whole `MissionParameters` document.
 *
 * The document round-trips intact: edits are applied by path into a clone, so
 * fields the interface does not expose — PID gains, jerk ceilings, calibration
 * statistics — survive a save untouched. `mission_config.json` is the single
 * shared store between this interface and the Python side.
 */
export function useMissionParameters() {
  const bridge = useBridge();
  const storage = useMemo(() => {
    try {
      return window.localStorage;
    } catch {
      return null;
    }
  }, []);

  const [committed, setCommitted] = useState<ParamsDoc | null>(null);
  const [working, setWorking] = useState<ParamsDoc | null>(null);
  const [factory, setFactory] = useState<ParamsDoc | null>(null);
  const [preset, setPreset] = useState<ParamsDoc | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'saving' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  /** A save was refused for an error conflict; cleared by a save that goes through, or a discard. */
  const [saveAttempted, setSaveAttempted] = useState(false);

  const missionMode: MissionMode = readMissionMode(working);

  useEffect(() => {
    let cancelled = false;

    migrateLegacyKeys(storage);

    const restoreLocal = () => {
      let activeMode: MissionMode = DEFAULT_MISSION_MODE;
      try {
        activeMode = parseMissionMode(storage?.getItem(ACTIVE_MODE_KEY)) ?? DEFAULT_MISSION_MODE;
      } catch {
        /* an unreadable store falls back to the full mission */
      }
      const parsed = readStoredDoc(storage, workingKey(activeMode));
      if (parsed && !cancelled) {
        // No bridge, so no mission to serve its defaults: "Restaurar
        // Padrões" stays unavailable rather than restoring this copy.
        setCommitted(parsed);
        setWorking(reconcileCoupledPaths(parsed));
        setStatus('ready');
        return true;
      }
      return false;
    };

    if (!bridge) {
      if (!restoreLocal()) {
        setStatus('error');
        setError('Sem ponte Electron. Os parâmetros vivem em mission_config.json.');
      }
      return () => {
        cancelled = true;
      };
    }

    // The two reads are deliberately independent. Recovering the factory
    // defaults means importing the Python package, which costs several seconds;
    // awaiting both together would hold the whole screen behind the slower one,
    // and a failure to produce defaults is not a reason to withhold the config
    // that is sitting on disk. Whichever arrives second backfills the fields
    // a store written before them lacks (`fillMissing`), into the committed
    // and the working copy alike, so the backfill never reads as an edit.
    let served: ParamsDoc | null = null;
    void (async () => {
      try {
        const current = await bridge.getParameters();
        if (cancelled) return;
        if (current.success && current.params) {
          // The store may hold a return cruise set apart from the forward
          // cruise (before the two were coupled). The working copy couples it
          // and the difference shows as a pending change, so a launch never
          // sends the pair the operator did not see.
          const stored = served ? fillMissing(current.params, served) : current.params;
          setCommitted(stored);
          setWorking(reconcileCoupledPaths(stored));
          setStatus('ready');
          setError(null);
        } else {
          setStatus('error');
          setError(current.error ?? 'mission_config.json ilegível');
        }
      } catch (err) {
        if (cancelled) return;
        setStatus('error');
        setError(err instanceof Error ? err.message : 'Falha ao ler parâmetros');
      }
    })();

    void bridge
      .getParameterDefaults()
      .then((defaults) => {
        if (cancelled || !defaults.success || !defaults.params) return;
        const factoryDoc = defaults.params;
        served = factoryDoc;
        setFactory(factoryDoc);
        setCommitted((prev) => (prev ? fillMissing(prev, factoryDoc) : prev));
        setWorking((prev) => (prev ? fillMissing(prev, factoryDoc) : prev));
      })
      .catch(() => undefined);

    return () => {
      cancelled = true;
    };
  }, [bridge, storage]);

  useEffect(() => {
    setPreset(readStoredDoc(storage, presetKey(missionMode)));
  }, [storage, missionMode]);

  const edit = useCallback((path: string, value: unknown) => {
    setWorking((prev) => {
      if (!prev) return prev;
      return MIRRORS_BY_PATH.has(path) ? reconcileCoupledPaths(setLinkedPath(prev, path, value)) : setPath(prev, path, value);
    });
  }, []);

  const changedPaths = useMemo(() => {
    if (!working || !committed) return new Set<string>();
    const set = new Set<string>();
    const differs = (path: string) => !deepEqual(getPath(working, path), getPath(committed, path));
    for (const path of TRACKED_PATHS) {
      if (differs(path) || (MIRRORS_BY_PATH.get(path) ?? []).some(differs)) {
        set.add(path);
      }
    }
    return set;
  }, [working, committed]);

  const dirty = changedPaths.size > 0;

  const isPresetActive = useMemo(() => matchesPreset(working, preset), [working, preset]);

  /** Conflicts between the working values, recomputed on every edit. */
  const conflicts = useMemo(() => activeConflicts(working), [working]);

  /**
   * Persist the working document, unless it holds an error conflict.
   *
   * The guard lives here and not in the sheet, so no caller gets around it:
   * the launch's own save of pending edits is refused the same way. Warnings
   * do not block; asking for confirmation is the sheet's job.
   */
  const save = useCallback(async () => {
    if (!working) return false;
    if (conflicts.some((conflict) => conflict.severity === 'error')) {
      setSaveAttempted(true);
      return false;
    }
    setStatus('saving');
    if (bridge) {
      const result = await bridge.saveParameters(working);
      if (!result.success) {
        setStatus('error');
        setError(result.error ?? 'Falha ao gravar mission_config.json');
        return false;
      }
    }
    writeStoredDoc(storage, workingKey(missionMode), working);
    try {
      storage?.setItem(ACTIVE_MODE_KEY, missionMode);
    } catch {
      /* the document is the source of truth */
    }
    setCommitted(working);
    setStatus('ready');
    setError(null);
    setSaveAttempted(false);
    return true;
  }, [bridge, working, conflicts, storage, missionMode]);

  const discard = useCallback(() => {
    setWorking(committed ? reconcileCoupledPaths(committed) : committed);
    setSaveAttempted(false);
  }, [committed]);

  /** Store the working values as this operator's preset. */
  const saveAsPreset = useCallback(() => {
    if (!working) return;
    setPreset(working);
    writeStoredDoc(storage, presetKey(missionMode), working);
  }, [working, storage, missionMode]);

  /**
   * The preset and the factory defaults are whole documents, but applying one
   * moves only the sheet fields: PID gains, the calibration the mission wrote
   * back and the arming mode stay as they are (5.8).
   */
  const applyPreset = useCallback(() => {
    if (preset) setWorking((prev) => (prev ? reconcileCoupledPaths(applySchemaFields(prev, preset)) : prev));
  }, [preset]);

  const applyFactory = useCallback(() => {
    if (factory) setWorking((prev) => (prev ? reconcileCoupledPaths(applySchemaFields(prev, factory)) : prev));
  }, [factory]);

  const performSwitch = useCallback(
    async (next: MissionMode): Promise<boolean> => {
      if (!working || next === missionMode) return false;
      const { doc, stash } = switchModeDocument({
        working,
        from: missionMode,
        to: next,
        stored: readStoredDoc(storage, workingKey(next)),
        factory,
      });
      writeStoredDoc(storage, workingKey(missionMode), stash);
      setWorking(doc);
      setPreset(readStoredDoc(storage, presetKey(next)));
      try { storage?.setItem(ACTIVE_MODE_KEY, next); } catch { /* the document is the source of truth */ }
      if (activeConflicts(doc).some((conflict) => conflict.severity === 'error')) {
        setSaveAttempted(true);
        return false;
      }
      setStatus('saving');
      if (bridge) {
        const result = await bridge.saveParameters(doc);
        if (!result.success) {
          setStatus('error');
          setError(result.error ?? 'Falha ao gravar mission_config.json');
          return false;
        }
      }
      writeStoredDoc(storage, workingKey(next), doc);
      setCommitted(doc);
      setStatus('ready');
      setError(null);
      setSaveAttempted(false);
      return true;
    },
    [bridge, factory, missionMode, storage, working]
  );

  const switching = useRef(false);
  /** Ignores a switch issued while another one is still being written; resolves `false` for it. */
  const switchMode = useCallback(
    async (next: MissionMode): Promise<boolean> => {
      if (switching.current) return false;
      switching.current = true;
      try {
        return await performSwitch(next);
      } finally {
        switching.current = false;
      }
    },
    [performSwitch]
  );

  return {
    committed,
    working,
    factory,
    preset,
    missionMode,
    status,
    error,
    dirty,
    changedPaths,
    isPresetActive,
    conflicts,
    saveAttempted,
    edit,
    save,
    discard,
    saveAsPreset,
    applyPreset,
    applyFactory,
    switchMode,
  };
}
