/**
 * What stops the mission from being commanded on the pre-flight screen.
 *
 * The parameters come first and gate the bench too: a launch reads the
 * document the sheet holds, so one still loading, being written or unreadable
 * is not a document anyone has approved. Bench mode then drops the aircraft
 * link gates: `mission.py --no-fly` still needs the driver, but the kinematic
 * simulator stands in for the airframe, so there is no Wi-Fi link to the
 * aircraft and no /bebop/odom to wait for.
 */
export interface PreflightGateInput {
  paramsStatus: 'loading' | 'ready' | 'saving' | 'error';
  driverRunning: boolean;
  benchMode: boolean;
  connected: boolean;
  flightReady: boolean;
  missingTopics: string[];
  stale: boolean;
  flyingState?: number | null;
  batteryPct?: number | null;
  batteryKnown?: boolean;
  batteryFailsafeThreshold?: number;
  /** The operator's battery failsafe switch; off, the charge gates nothing. Default on. */
  batteryFailsafeEnabled?: boolean;
  bridgeReady?: boolean;
}

const PARAMS_REASON: Record<Exclude<PreflightGateInput['paramsStatus'], 'ready'>, string> = {
  loading: 'Carregando parâmetros',
  saving: 'Gravando parâmetros',
  error: 'Parâmetros indisponíveis',
};

const DEFAULT_BATTERY_THRESHOLD_PCT = 20;

/**
 * Takeoff is refused when the charge is unreadable or at or below the
 * operator threshold, the same `<=` criterion `steps/takeoff.py` applies.
 */
function batteryBlockedReason(pct: number | null | undefined, threshold: number): string | null {
  if (typeof pct !== 'number' || !Number.isFinite(pct) || pct <= threshold) {
    return `Bateria insuficiente para voo real (${pct ?? 0}% <= limiar ${threshold}%)`;
  }
  return null;
}

/** One label per blocking reason, short enough for the centre of the launch dial. */
export type PreflightBlockCode =
  | 'params'
  | 'driver'
  | 'link'
  | 'topics'
  | 'odometry'
  | 'airborne'
  | 'battery-unknown'
  | 'battery-low'
  | 'bridge';

const SHORT_REASON: Record<PreflightBlockCode, string> = {
  params: 'Parâmetros',
  driver: 'Sem driver',
  link: 'Sem enlace',
  topics: 'Sem tópicos',
  odometry: 'Sem odometria',
  airborne: 'Fora do solo',
  'battery-unknown': 'Bateria?',
  'battery-low': 'Bateria baixa',
  bridge: 'Sem ponte',
};

/** The first gate that blocks, in the order of the checks, with its long reason. */
function firstBlock(input: PreflightGateInput): { code: PreflightBlockCode; reason: string } | null {
  if (input.paramsStatus !== 'ready') return { code: 'params', reason: PARAMS_REASON[input.paramsStatus] };
  if (!input.driverRunning) return { code: 'driver', reason: 'Driver ROS 2 fora do ar' };
  if (input.benchMode) return null;
  if (!input.connected) return { code: 'link', reason: 'Conecte-se à rede da aeronave para liberar o comando' };
  if (!input.flightReady) {
    return {
      code: 'topics',
      reason: input.missingTopics.length
        ? `Tópicos sem tráfego: ${input.missingTopics.join(', ')}`
        : 'Validando tópicos da aeronave',
    };
  }
  if (input.stale) return { code: 'odometry', reason: 'Sem odometria recente' };
  if (typeof input.flyingState === 'number' && input.flyingState !== 0) {
    return { code: 'airborne', reason: `Aeronave não está em solo (flying_state ${input.flyingState})` };
  }
  if (input.batteryFailsafeEnabled !== false) {
    if (input.batteryKnown === false) {
      return { code: 'battery-unknown', reason: 'Bateria da aeronave desconhecida' };
    }
    const batteryReason = batteryBlockedReason(
      input.batteryPct,
      input.batteryFailsafeThreshold ?? DEFAULT_BATTERY_THRESHOLD_PCT
    );
    if (batteryReason) return { code: 'battery-low', reason: batteryReason };
  }
  if (input.bridgeReady === false) {
    return { code: 'bridge', reason: 'Ponte de comando não está pronta' };
  }
  return null;
}

export function preflightBlockedCode(input: PreflightGateInput): PreflightBlockCode | null {
  return firstBlock(input)?.code ?? null;
}

export function preflightBlockedReason(input: PreflightGateInput): string | null {
  return firstBlock(input)?.reason ?? null;
}

/** The same decision as {@link preflightBlockedReason}, as a label of at most 18 characters. */
export function preflightBlockedShort(input: PreflightGateInput): string | null {
  const block = firstBlock(input);
  return block ? SHORT_REASON[block.code] : null;
}
