import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ALL_PARAMETERS, NUMBER_PARAMETERS, TRACKED_PATHS } from '../lib/parameterSchema';
import {
  applySchemaFields,
  fillMissing,
  altitudeAdjustment,
  migrateLegacyMode,
  normalizeForMode,
  reconcileCoupledPaths,
  setLinkedPath,
} from '../lib/launchDocument';
import { activeConflicts } from '../lib/parameterConflicts';
import { MISSION_MODES, parseMissionMode, readMissionMode, DEFAULT_MISSION_MODE, type MissionMode } from '../lib/missionMode';
import {
  activePresetId as findActivePreset,
  addPreset,
  applyPresetValues,
  deletePreset,
  latestPreset,
  migrateSinglePreset,
  readPresets,
  renamePreset,
  singleFavorite,
  writePresets,
  type OperatorPreset,
} from '../lib/operatorPresets';
import {
  ACTIVE_MODE_KEY,
  migrateLegacyIntermediate,
  migrateLegacyKeys,
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
  NUMBER_PARAMETERS.filter((spec) => spec.mirrors).map((spec) => [spec.path, spec.mirrors!.map((m) => m.path)])
);

/** A fresh favourite id: `crypto.randomUUID`, or a time-and-random id where the runtime lacks it. */
function newPresetId(): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  return uuid ?? `preset-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
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
  const [presets, setPresets] = useState<OperatorPreset[]>([]);
  const [status, setStatus] = useState<'loading' | 'ready' | 'saving' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  /** A save was refused for an error conflict; cleared by a save that goes through, or a discard. */
  const [saveAttempted, setSaveAttempted] = useState(false);
  /**
   * The last altitude the envelope of the launch surface moved, in the
   * operator's words (D1): a value below the pad-top minimum is held at it,
   * never silently. Cleared by a save or a discard.
   */
  const [altitudeNotice, setAltitudeNotice] = useState<string | null>(null);

  const missionMode: MissionMode = readMissionMode(working);

  useEffect(() => {
    let cancelled = false;

    migrateLegacyKeys(storage);
    migrateLegacyIntermediate(storage);
    for (const mode of MISSION_MODES) migrateSinglePreset(storage, mode.id, newPresetId);

    const restoreLocal = () => {
      let activeMode: MissionMode = DEFAULT_MISSION_MODE;
      try {
        activeMode = parseMissionMode(storage?.getItem(ACTIVE_MODE_KEY)) ?? DEFAULT_MISSION_MODE;
      } catch {
        /* an unreadable store falls back to the full mission */
      }
      const stored = readStoredDoc(storage, workingKey(activeMode));
      const parsed = stored ? migrateLegacyMode(stored) : null;
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
          const migrated = migrateLegacyMode(current.params);
          const stored = served ? fillMissing(migrated, served) : migrated;
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
    setPresets(readPresets(storage, missionMode));
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

  const activePresetId = useMemo(
    () => findActivePreset(presets, missionMode, working),
    [presets, missionMode, working]
  );

  /** Conflicts between the working values, recomputed on every edit. */
  const conflicts = useMemo(() => activeConflicts(working), [working]);

  /**
   * Persist the working document, unless it holds an error conflict.
   *
   * The guard lives here and not in the sheet, so no caller gets around it:
   * the launch's own save of pending edits is refused the same way. Warnings
   * do not block; asking for confirmation is the sheet's job.
   */
  // Every change of the working document is held inside the altitude envelope
  // of its launch surface: a surface switched to the pad top raises a low
  // altitude to the pad-top minimum, with a notice (section 5.2).
  useEffect(() => {
    if (!working) return;
    const next = normalizeForMode(working);
    if (next === working) return;
    setAltitudeNotice(altitudeAdjustment(working, next));
    setWorking(next);
  }, [working]);

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
    setAltitudeNotice(null);
    return true;
  }, [bridge, working, conflicts, storage, missionMode]);

  const discard = useCallback(() => {
    setWorking(committed ? reconcileCoupledPaths(committed) : committed);
    setSaveAttempted(false);
    setAltitudeNotice(null);
  }, [committed]);

  /**
   * Write `doc` as the active document of `mode`: to `mission_config.json`
   * through the bridge and to the mode's working copy. A document with an
   * error conflict is not written; the sheet marks the fields instead.
   */
  const persistDocument = useCallback(
    async (doc: ParamsDoc, mode: MissionMode): Promise<boolean> => {
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
      writeStoredDoc(storage, workingKey(mode), doc);
      setCommitted(doc);
      setStatus('ready');
      setError(null);
      setSaveAttempted(false);
      return true;
    },
    [bridge, storage]
  );

  /**
   * Save the working values as a favourite of the current mode named `name`.
   * Returns why it was refused (empty or duplicate name, the limit of 12),
   * in Portuguese, or `null` once saved.
   */
  const savePreset = useCallback(
    (name: string): string | null => {
      if (!working) return 'Parâmetros ainda não carregados.';
      const result = addPreset(readPresets(storage, missionMode), name, working, newPresetId());
      if (!result.ok) return result.error;
      writePresets(storage, missionMode, result.presets);
      setPresets(result.presets);
      return null;
    },
    [working, storage, missionMode]
  );

  /** Rename favourite `id`; returns why it was refused, or `null`. */
  const renamePresetById = useCallback(
    (id: string, name: string): string | null => {
      const result = renamePreset(readPresets(storage, missionMode), id, name);
      if (!result.ok) return result.error;
      writePresets(storage, missionMode, result.presets);
      setPresets(result.presets);
      return null;
    },
    [storage, missionMode]
  );

  /** Delete favourite `id` of the current mode. */
  const deletePresetById = useCallback(
    (id: string) => {
      const next = deletePreset(readPresets(storage, missionMode), id);
      writePresets(storage, missionMode, next);
      setPresets(next);
    },
    [storage, missionMode]
  );

  /**
   * Apply favourite `id`: its mode-owned values only (PID gains, the
   * calibration the mission wrote back and the arming mode stay as they are),
   * the altitude moved into the envelope of the launch surface, and the
   * document written to `mission_config.json`, as on a mode switch.
   */
  const applyPresetById = useCallback(
    async (id: string): Promise<boolean> => {
      const preset = presets.find((item) => item.id === id);
      if (!preset || !working) return false;
      const doc = applyPresetValues(working, preset);
      setWorking(doc);
      return persistDocument(doc, missionMode);
    },
    [presets, working, persistDocument, missionMode]
  );

  /** The favourite of the current mode: the single one, or the latest of a legacy list (D8). */
  const favorite = useMemo(() => latestPreset(presets), [presets]);

  /**
   * Save the working values as the single favourite of the current mode,
   * replacing whatever list the mode held. Returns false when there is no
   * document or the store refused.
   */
  const saveFavorite = useCallback((): boolean => {
    if (!working) return false;
    const list = singleFavorite(working, newPresetId());
    if (!writePresets(storage, missionMode, list)) return false;
    setPresets(list);
    return true;
  }, [working, storage, missionMode]);

  /**
   * Bring the favourite's values into the working document. Nothing is
   * persisted: the change reads as pending and the operator saves it from the
   * sheet, as with the factory restore. Returns false without a favourite.
   */
  const restoreFavorite = useCallback((): boolean => {
    if (!favorite) return false;
    setWorking((prev) => (prev ? applyPresetValues(prev, favorite) : prev));
    return true;
  }, [favorite]);

  const applyFactory = useCallback(() => {
    if (factory) setWorking((prev) => (prev ? reconcileCoupledPaths(normalizeForMode(applySchemaFields(prev, factory))) : prev));
  }, [factory]);

  const performSwitch = useCallback(
    async (next: MissionMode): Promise<boolean> => {
      if (!working || next === missionMode) return false;
      const { doc, stash } = switchModeDocument({
        working,
        from: missionMode,
        to: next,
        stored: readStoredDoc(storage, workingKey(next)),
      });
      writeStoredDoc(storage, workingKey(missionMode), stash);
      setWorking(doc);
      setPresets(readPresets(storage, next));
      try { storage?.setItem(ACTIVE_MODE_KEY, next); } catch { /* the document is the source of truth */ }
      return persistDocument(doc, next);
    },
    [missionMode, persistDocument, storage, working]
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
    presets,
    activePresetId,
    missionMode,
    status,
    error,
    dirty,
    changedPaths,
    conflicts,
    saveAttempted,
    altitudeNotice,
    edit,
    save,
    discard,
    savePreset,
    applyPreset: applyPresetById,
    renamePreset: renamePresetById,
    deletePreset: deletePresetById,
    applyFactory,
    switchMode,
    favorite,
    saveFavorite,
    restoreFavorite,
  };
}
