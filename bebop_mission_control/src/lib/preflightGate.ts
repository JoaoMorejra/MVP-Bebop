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
}

const PARAMS_REASON: Record<Exclude<PreflightGateInput['paramsStatus'], 'ready'>, string> = {
  loading: 'Carregando parâmetros',
  saving: 'Gravando parâmetros',
  error: 'Parâmetros indisponíveis',
};

export function preflightBlockedReason(input: PreflightGateInput): string | null {
  if (input.paramsStatus !== 'ready') return PARAMS_REASON[input.paramsStatus];
  if (!input.driverRunning) return 'Driver ROS 2 fora do ar';
  if (input.benchMode) return null;
  if (!input.connected) return 'Conecte-se à rede da aeronave para liberar o comando';
  if (!input.flightReady) {
    return input.missingTopics.length
      ? `Tópicos sem tráfego: ${input.missingTopics.join(', ')}`
      : 'Validando tópicos da aeronave';
  }
  if (input.stale) return 'Sem odometria recente';
  return null;
}
