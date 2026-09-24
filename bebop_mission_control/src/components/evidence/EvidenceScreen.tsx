import React, { useEffect, useMemo, useState } from 'react';
import { FileDown, FolderOpen, RefreshCw } from 'lucide-react';
import type { EvidenceItem } from '../../types/bmg';
import { Plate, PlateHead } from '../ui/Plate';
import { Button } from '../ui/Button';
import { DualFidelityViewer } from './DualFidelityViewer';
import { MetadataSheet } from './MetadataSheet';
import { buildDossier } from '../../lib/dossier';
import { cn, stampLabel } from '../../lib/format';
import { useBridge } from '../../hooks/useBridge';

interface EvidenceScreenProps {
  items: EvidenceItem[];
  status: 'loading' | 'ready' | 'error';
  error: string | null;
  onReload: () => void;
  selectedStamp: string | null;
  onSelect: (stamp: string) => void;
}

export const EvidenceScreen: React.FC<EvidenceScreenProps> = ({
  items,
  status,
  error,
  onReload,
  selectedStamp,
  onSelect,
}) => {
  const bridge = useBridge();
  const [exporting, setExporting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const selected = useMemo(
    () => items.find((i) => i.stamp === selectedStamp) ?? items[0] ?? null,
    [items, selectedStamp]
  );

  useEffect(() => {
    if (!selectedStamp && items.length > 0) onSelect(items[0].stamp);
  }, [items, selectedStamp, onSelect]);

  useEffect(() => {
    if (!notice) return;
    const id = window.setTimeout(() => setNotice(null), 5000);
    return () => window.clearTimeout(id);
  }, [notice]);

  const exportDossier = async () => {
    if (!selected || !bridge) return;
    setExporting(true);
    try {
      const html = buildDossier(selected, { operator: 'Estação BMG' });
      const result = await bridge.exportDossier({ html, stamp: selected.stamp });
      setNotice(
        result.success
          ? `Dossiê gravado em ${result.path}`
          : result.error === 'cancelled'
          ? 'Exportação cancelada'
          : `Falha ao exportar: ${result.error ?? 'motivo desconhecido'}`
      );
    } finally {
      setExporting(false);
    }
  };

  if (status === 'error') {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <Plate className="max-w-[52ch] p-6">
          <h2 className="text-base text-frost">Não foi possível ler as evidências</h2>
          <p className="mt-2 text-sm leading-relaxed text-haze">{error}</p>
          <Button className="mt-4" onClick={onReload} icon={<RefreshCw size={13} />}>
            Tentar de novo
          </Button>
        </Plate>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="max-w-[52ch] text-center">
          <div className="mx-auto mb-5 h-16 w-24 border border-strut" aria-hidden />
          <h2 className="text-base text-frost">Nenhuma evidência registrada ainda</h2>
          <p className="mt-2 text-sm leading-relaxed text-haze">
            A etapa 4 grava um par de imagens sobre o sinistro: um PNG sem perdas direto do sensor e
            um JPEG com as caixas da rede neural. Os dois aparecem aqui assim que a aeronave os
            escrever no diretório da missão.
          </p>
          <Button className="mx-auto mt-5" onClick={onReload} icon={<RefreshCw size={13} />}>
            Procurar de novo
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="grid h-full min-h-0 grid-cols-[212px_minmax(0,1fr)_312px] gap-2 p-2">
      <Plate className="flex min-h-0 flex-col">
        <PlateHead
          title="Capturas"
          aside={
            <button
              type="button"
              onClick={onReload}
              aria-label="Recarregar capturas"
              className="rounded-bezel p-1 text-haze transition-colors hover:bg-hull-raise hover:text-frost"
            >
              <RefreshCw size={13} className={cn(status === 'loading' && 'animate-spin')} />
            </button>
          }
        />
        <ul className="scroll-thin min-h-0 flex-1 overflow-y-auto p-1.5">
          {items.map((item) => {
            const active = selected?.stamp === item.stamp;
            const thumb = item.annotatedUrl ?? item.rawUrl;
            return (
              <li key={item.stamp}>
                <button
                  type="button"
                  onClick={() => onSelect(item.stamp)}
                  className={cn(
                    'mb-1 flex w-full items-center gap-2.5 rounded-bezel border p-1.5 text-left transition-colors',
                    active
                      ? 'border-mint/50 bg-mint/[0.07]'
                      : 'border-transparent hover:border-strut hover:bg-hull-deck'
                  )}
                >
                  {thumb ? (
                    <img src={thumb} alt="" className="h-11 w-16 shrink-0 border border-strut object-cover" />
                  ) : (
                    <span className="h-11 w-16 shrink-0 border border-strut" />
                  )}
                  <span className="min-w-0">
                    <span className="block truncate font-mono text-2xs text-frost">{item.stamp}</span>
                    <span className="block truncate text-[10px] text-haze-deep">
                      {item.metadata?.detections?.length
                        ? `${item.metadata.detections.length} ${
                            item.metadata.detections.length === 1 ? 'detecção' : 'detecções'
                          }`
                        : 'sem detecção'}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </Plate>

      <Plate className="flex min-h-0 flex-col">
        <PlateHead
          title={selected ? stampLabel(selected.stamp) : 'Evidência'}
          aside={
            <>
              {bridge && selected ? (
                <button
                  type="button"
                  onClick={() => void bridge.revealPath(`${selected.stamp}`)}
                  aria-label="Abrir o diretório da missão"
                  className="rounded-bezel p-1 text-haze transition-colors hover:bg-hull-raise hover:text-frost"
                >
                  <FolderOpen size={13} />
                </button>
              ) : null}
              <Button
                variant="quiet"
                onClick={exportDossier}
                disabled={!selected || !bridge || exporting}
                icon={<FileDown size={13} />}
              >
                {exporting ? 'Exportando' : 'Exportar dossiê'}
              </Button>
            </>
          }
        />
        <div className="min-h-0 flex-1 p-2.5">
          {selected ? <DualFidelityViewer item={selected} /> : null}
        </div>
        {notice ? (
          <p className="anim-rise border-t border-strut-soft px-3 py-2 text-2xs text-haze" role="status">
            {notice}
          </p>
        ) : null}
      </Plate>

      <Plate className="flex min-h-0 flex-col">
        <PlateHead title="Laudo técnico" />
        <div className="min-h-0 flex-1">{selected ? <MetadataSheet item={selected} /> : null}</div>
      </Plate>
    </div>
  );
};
