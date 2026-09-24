import { useCallback, useEffect, useMemo, useState } from 'react';
import { TRACKED_PATHS } from '../lib/parameterSchema';
import { deepEqual, getPath, setPath } from '../lib/paths';
import { useBridge } from './useBridge';

export type ParamsDoc = Record<string, unknown>;

const LOCAL_PRESET_KEY = 'bmg.operator-preset.v2';
const LOCAL_WORKING_KEY = 'bmg.working-parameters.v2';

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

  const [committed, setCommitted] = useState<ParamsDoc | null>(null);
  const [working, setWorking] = useState<ParamsDoc | null>(null);
  const [factory, setFactory] = useState<ParamsDoc | null>(null);
  const [preset, setPreset] = useState<ParamsDoc | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'saving' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const restoreLocal = () => {
      try {
        const stored = window.localStorage.getItem(LOCAL_WORKING_KEY);
        if (stored) {
          const parsed = JSON.parse(stored) as ParamsDoc;
          if (!cancelled) {
            setCommitted(parsed);
            setWorking(parsed);
            setFactory(parsed);
            setStatus('ready');
            return true;
          }
        }
      } catch {
        /* a corrupt cache must not block the screen */
      }
      return false;
    };

    try {
      const storedPreset = window.localStorage.getItem(LOCAL_PRESET_KEY);
      if (storedPreset) setPreset(JSON.parse(storedPreset) as ParamsDoc);
    } catch {
      /* ignore */
    }

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
    // that is sitting on disk.
    void (async () => {
      try {
        const current = await bridge.getParameters();
        if (cancelled) return;
        if (current.success && current.params) {
          setCommitted(current.params);
          setWorking(current.params);
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
      .getDefaultParameters()
      .then((defaults) => {
        if (!cancelled && defaults.success && defaults.params) setFactory(defaults.params);
      })
      .catch(() => undefined);

    return () => {
      cancelled = true;
    };
  }, [bridge]);

  const edit = useCallback((path: string, value: unknown) => {
    setWorking((prev) => (prev ? setPath(prev, path, value) : prev));
  }, []);

  const changedPaths = useMemo(() => {
    if (!working || !committed) return new Set<string>();
    const set = new Set<string>();
    for (const path of TRACKED_PATHS) {
      if (!deepEqual(getPath(working, path), getPath(committed, path))) {
        set.add(path);
      }
    }
    return set;
  }, [working, committed]);

  const dirty = changedPaths.size > 0;

  const save = useCallback(async () => {
    if (!working) return false;
    setStatus('saving');
    try {
      window.localStorage.setItem(LOCAL_WORKING_KEY, JSON.stringify(working));
    } catch {
      /* quota or private mode: the backend write below is the real store */
    }
    if (bridge) {
      const result = await bridge.saveParameters(working);
      if (!result.success) {
        setStatus('error');
        setError(result.error ?? 'Falha ao gravar mission_config.json');
        return false;
      }
    }
    setCommitted(working);
    setStatus('ready');
    setError(null);
    return true;
  }, [bridge, working]);

  const discard = useCallback(() => {
    setWorking(committed);
  }, [committed]);

  /** Store the working values as this operator's preset. */
  const saveAsPreset = useCallback(() => {
    if (!working) return;
    setPreset(working);
    try {
      window.localStorage.setItem(LOCAL_PRESET_KEY, JSON.stringify(working));
    } catch {
      /* ignore */
    }
  }, [working]);

  const applyPreset = useCallback(() => {
    if (preset) setWorking(preset);
  }, [preset]);

  const applyFactory = useCallback(() => {
    if (factory) setWorking(factory);
  }, [factory]);

  return {
    committed,
    working,
    factory,
    preset,
    status,
    error,
    dirty,
    changedPaths,
    edit,
    save,
    discard,
    saveAsPreset,
    applyPreset,
    applyFactory,
  };
}
