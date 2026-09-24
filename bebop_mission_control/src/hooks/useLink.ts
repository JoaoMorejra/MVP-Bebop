import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  LinkProgressEvent,
  LinkReadiness,
  LinkStage,
  WifiNetwork,
} from '../types/bmg';
import { cues } from '../audio/cues';
import { useBridge } from './useBridge';
import { useInterval } from './useInterval';
import { isBebopSsid } from '../lib/wifi';

export type LinkPhase =
  | 'idle'
  | 'scanning'
  | 'connecting'
  | 'starting-driver'
  | 'validating'
  | 'ready'
  | 'error';

const STAGE_PHASE: Record<LinkStage, LinkPhase> = {
  wifi: 'connecting',
  ping: 'connecting',
  bridges: 'starting-driver',
  driver: 'starting-driver',
  topics: 'validating',
  ready: 'ready',
  error: 'error',
};

/** Retry cadence for an automatic bring-up that did not reach `ready`. */
const AUTO_RETRY_MS = 15000;

/**
 * Wi-Fi and ROS 2 driver lifecycle.
 *
 * The whole chain — join the network, reach 192.168.42.1, start
 * `make driver-bebop`, then prove the driver is exchanging every essential
 * topic with the airframe — runs in one `bmg:ensure-link` call in the main
 * process. This hook drives it and renders its progress; it does not sequence
 * the steps itself.
 *
 * That replaces the previous reactive effect, which fired at most once per
 * link session behind a ref, never retried after a failure, and declared the
 * driver "no ar" from a process probe that says nothing about whether the
 * aircraft is actually answering.
 */
export function useLink(opts: { connected: boolean; driverRunning: boolean }) {
  const bridge = useBridge();
  const [networks, setNetworks] = useState<WifiNetwork[]>([]);
  const [currentSsid, setCurrentSsid] = useState('');
  const [phase, setPhase] = useState<LinkPhase>('idle');
  const [message, setMessage] = useState<string | null>(null);
  const [driverProbe, setDriverProbe] = useState(false);
  const [readiness, setReadiness] = useState<LinkReadiness | null>(null);
  const [progress, setProgress] = useState<LinkProgressEvent[]>([]);
  /** A forced rescan is in flight; routine polls do not count. */
  const [scanning, setScanning] = useState(false);

  // Serialises bring-up against the polling loops; the main process refuses a
  // concurrent `ensure-link` anyway, this keeps the UI honest about it.
  const busyRef = useRef(false);
  const lastAutoAttemptRef = useRef(0);
  const manualStopRef = useRef(false);

  const scan = useCallback(
    async (forceRescan = false) => {
      if (!bridge) return;
      if (forceRescan) setScanning(true);
      try {
        const result = await bridge.scanWifi({ forceRescan });
        setNetworks(result.networks);
        setCurrentSsid(result.currentSsid);
      } catch (err) {
        setMessage(err instanceof Error ? err.message : 'Falha ao varrer redes');
      } finally {
        if (forceRescan) setScanning(false);
      }
    },
    [bridge]
  );

  const runEnsureLink = useCallback(
    async (ssid?: string) => {
      if (!bridge || busyRef.current) return null;
      busyRef.current = true;
      manualStopRef.current = false;
      setProgress([]);
      setPhase(ssid ? 'connecting' : 'starting-driver');
      setMessage(ssid ? `Entrando em ${ssid}` : 'Preparando a aeronave');

      try {
        const result = await bridge.ensureLink(ssid ? { ssid } : {});
        setReadiness(result.readiness);
        setPhase(result.success ? 'ready' : STAGE_PHASE[result.stage] ?? 'error');
        setMessage(result.message);
        if (result.success) {
          setDriverProbe(true);
          cues.play('link');
        } else {
          setPhase('error');
          cues.play('fault');
        }
        return result;
      } catch (err) {
        setPhase('error');
        setMessage(err instanceof Error ? err.message : 'Falha na preparação do enlace');
        return null;
      } finally {
        busyRef.current = false;
        void scan(false);
      }
    },
    [bridge, scan]
  );

  /**
   * Join a network.
   *
   * The aircraft's own network goes through the full bring-up — join, ping,
   * driver, topics. Any other network is only joined: there is no drone behind
   * it to wait for, and running the link orchestration against an office
   * router would end in a timeout that reads like a fault.
   */
  const connect = useCallback(
    async (ssid: string) => {
      const target = networks.find((n) => n.ssid === ssid);
      const isBebop = target ? target.isBebop : isBebopSsid(ssid);
      if (isBebop) {
        setCurrentSsid(ssid);
        await runEnsureLink(ssid);
        return;
      }
      if (!bridge || busyRef.current) return;
      busyRef.current = true;
      setPhase('connecting');
      setMessage(`Entrando em ${ssid}`);
      try {
        const result = await bridge.connectWifi(ssid);
        if (result.success) {
          setCurrentSsid(ssid);
          setPhase('idle');
          setMessage(`Conectado a ${ssid}`);
        } else {
          setPhase('error');
          setMessage(
            result.error
              ? `Não foi possível entrar em ${ssid}: ${result.error}`
              : `Não foi possível entrar em ${ssid}`
          );
        }
      } catch (err) {
        setPhase('error');
        setMessage(err instanceof Error ? err.message : `Falha ao entrar em ${ssid}`);
      } finally {
        busyRef.current = false;
        void scan(false);
      }
    },
    [bridge, networks, runEnsureLink, scan]
  );

  const startDriver = useCallback(async () => {
    await runEnsureLink();
  }, [runEnsureLink]);

  const stopDriver = useCallback(async () => {
    if (!bridge) return;
    // An explicit stop must not be undone by the automatic bring-up on the
    // next tick.
    manualStopRef.current = true;
    await bridge.stopDriver();
    setDriverProbe(false);
    setReadiness(null);
    setPhase('idle');
    setMessage('Driver encerrado');
  }, [bridge]);

  // Live progress from the main process, so the operator sees which stage the
  // bring-up is on rather than a spinner.
  useEffect(() => {
    if (!bridge) return;
    return bridge.onLinkProgress((event) => {
      setProgress((previous) => [...previous.slice(-11), event]);
      if (event.status === 'running') {
        setPhase(STAGE_PHASE[event.stage] ?? 'idle');
        setMessage(event.message);
      }
    });
  }, [bridge]);

  useEffect(() => {
    void scan(false);
  }, [scan]);

  useInterval(() => void scan(false), bridge ? 8000 : null);

  // Readiness poll. Cheap, and the only thing the flight gate should consult.
  //
  // Deliberately *not* suspended while a bring-up is in flight. Suspending it
  // froze every readiness panel at its initial value for the whole attempt, so
  // a run that spent twenty seconds waiting on a ping reported "node not in the
  // ROS 2 graph" and "MJPEG bridge did not answer" while both were plainly
  // working — the operator was reading a snapshot from before the attempt
  // started. Only the phase label is left to the progress events, which own it.
  useInterval(
    () => {
      if (!bridge) return;
      void bridge
        .getLinkReadiness()
        .then((snapshot) => {
          setReadiness(snapshot);
          setDriverProbe(snapshot.driverRunning || snapshot.nodePresent);
          if (snapshot.ready && !busyRef.current) {
            setPhase('ready');
          }
        })
        .catch(() => undefined);
    },
    bridge ? 1500 : null
  );

  useInterval(
    () => {
      if (!bridge) return;
      void bridge.checkDriverStatus().then((s) => setDriverProbe((p) => s.running || p));
    },
    bridge ? 5000 : null
  );

  /**
   * Automatic bring-up.
   *
   * Runs whenever the aircraft's network is reachable and the link is not
   * ready, with a cooling period between attempts. Unlike the ref-guarded
   * effect it replaces, a failed attempt is retried — the common real failure
   * is the driver losing the ARSDK session, which no amount of "we already
   * tried once" will recover from.
   */
  useEffect(() => {
    if (!bridge || busyRef.current || manualStopRef.current) return;
    if (!opts.connected) return;
    if (readiness?.ready) return;

    const now = Date.now();
    if (now - lastAutoAttemptRef.current < AUTO_RETRY_MS) return;
    lastAutoAttemptRef.current = now;
    void runEnsureLink();
  }, [bridge, opts.connected, readiness?.ready, runEnsureLink]);

  return {
    networks,
    currentSsid,
    scanning,
    phase,
    message,
    progress,
    readiness,
    driverRunning: opts.driverRunning || driverProbe,
    /** The only gate the launch control should consult. */
    flightReady: Boolean(readiness?.ready),
    scan,
    connect,
    startDriver,
    stopDriver,
    ensureLink: runEnsureLink,
  };
}
