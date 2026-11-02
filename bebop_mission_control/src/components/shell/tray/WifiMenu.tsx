import React, { useEffect, useState } from 'react';
import { Check, Lock, Plane } from 'lucide-react';
import type { WifiNetwork } from '../../../types/bmg';
import { cn } from '../../../lib/format';
import { sortNetworks, wifiLevel } from '../../../lib/trayState';
import { isBebopSsid } from '../../../lib/wifi';
import { WifiGlyph } from '../glyphs';
import { QsSpinner, QsSubmenu } from './controls';

interface WifiMenuProps {
  networks: WifiNetwork[];
  currentSsid: string;
  /** A forced rescan is running. */
  scanning: boolean;
  /** A join or a bring-up is running; another join waits for it. */
  busy: boolean;
  /** The launch sequence is running (D6). */
  frozen: boolean;
  onScan: () => void;
  onConnect: (ssid: string) => void;
  onDisconnect?: () => void;
}

/**
 * The networks around, as the Wi-Fi submenu of Ubuntu lists them: signal,
 * name, a lock on a protected one, a tick on the one joined. The aircraft's
 * network comes first with a small aircraft mark. The ring after the title
 * turns while the radio searches; the foot keeps the refresh.
 *
 * Ubuntu behavior: clicking the connected network disconnects it; clicking
 * any other network initiates a connection.
 */
export const WifiMenu: React.FC<WifiMenuProps> = ({
  networks,
  currentSsid,
  scanning,
  busy,
  frozen,
  onScan,
  onConnect,
  onDisconnect,
}) => {
  const [joining, setJoining] = useState<string | null>(null);
  useEffect(() => {
    if (!busy) setJoining(null);
  }, [busy]);
  const searching = scanning || joining !== null;

  return (
    <QsSubmenu
      id="wifi"
      title="Wi-Fi"
      active={Boolean(currentSsid)}
      icon={<WifiGlyph level={currentSsid ? 4 : 0} seeking={searching} size={18} />}
      status={searching ? <QsSpinner /> : null}
      footer={
        <button
          type="button"
          onClick={onScan}
          disabled={scanning}
          aria-label="Atualizar redes"
          className="qs-menu-row w-full text-[14px] text-frost disabled:opacity-50"
        >
          Atualizar redes
        </button>
      }
    >
      <ul className="scroll-thin max-h-[264px] overflow-y-auto" data-loading={searching || undefined}>
        {networks.length === 0 ? (
          <li className="px-3 py-3 text-[13px] text-frost/60">{scanning ? 'Procurando redes' : 'Nenhuma rede encontrada'}</li>
        ) : (
          sortNetworks(networks).map((network) => {
            const active = network.active || network.ssid === currentSsid;
            const bebop = network.isBebop || isBebopSsid(network.ssid);
            const pending = joining === network.ssid && busy;
            return (
              <li key={network.ssid}>
                <button
                  type="button"
                  data-network={network.ssid}
                  data-bebop={bebop ? 'true' : undefined}
                  data-active={active || undefined}
                  disabled={busy || frozen}
                  onClick={() => {
                    if (active) {
                      onDisconnect?.();
                    } else {
                      setJoining(network.ssid);
                      onConnect(network.ssid);
                    }
                  }}
                  className={cn('qs-menu-row w-full', (busy || frozen) && 'cursor-not-allowed')}
                  aria-label={active ? `Desconectar de ${network.ssid}` : `Conectar a ${network.ssid}`}
                >
                  <span className="relative shrink-0">
                    <WifiGlyph level={wifiLevel(network.signal)} size={16} />
                    {network.secure ? (
                      <Lock size={7} strokeWidth={3} aria-label="protegida" className="absolute -bottom-0.5 -right-1 text-frost" />
                    ) : null}
                  </span>
                  <span className={cn('min-w-0 flex-1 truncate text-left text-[14px]', active || bebop ? 'text-frost' : 'text-frost/85')}>
                    {network.ssid}
                  </span>
                  {bebop ? <Plane size={12} strokeWidth={2} aria-label="aeronave" className="shrink-0 text-frost/80" /> : null}
                  {pending ? <QsSpinner size={14} /> : active ? <Check size={15} strokeWidth={2.4} aria-label="conectada" className="shrink-0 text-frost" /> : null}
                </button>
              </li>
            );
          })
        )}
      </ul>
    </QsSubmenu>
  );
};
