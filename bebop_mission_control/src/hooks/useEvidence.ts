import { useCallback, useEffect, useState } from 'react';
import type { EvidenceItem } from '../types/bmg';
import { useBridge } from './useBridge';

/**
 * The evidence library: every `accident_raw_*.png` / `accident_inspected_*.jpg`
 * pair in the mission working directory, joined to its forensic sidecar.
 */
export function useEvidence(refreshToken: number) {
  const bridge = useBridge();
  const [items, setItems] = useState<EvidenceItem[]>([]);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!bridge) {
      setStatus('error');
      setError('Sem ponte Electron. As capturas ficam no diretório da missão.');
      return;
    }
    setStatus('loading');
    try {
      const result = await bridge.listEvidence();
      if (result.success) {
        setItems(result.items);
        setStatus('ready');
        setError(null);
      } else {
        setStatus('error');
        setError(result.error ?? 'Falha ao ler o diretório da missão');
      }
    } catch (err) {
      setStatus('error');
      setError(err instanceof Error ? err.message : 'Falha ao ler evidências');
    }
  }, [bridge]);

  useEffect(() => {
    void reload();
  }, [reload, refreshToken]);

  return { items, status, error, reload };
}
