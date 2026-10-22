import { useEffect, useRef } from 'react';
import type { MissionMode } from '../lib/missionMode';
import { parseMissionMode } from '../lib/missionMode';
import { useBridge } from './useBridge';

/** What a launch document asks for beyond the profile: the return plan and the launch surface. */
export interface LaunchPlan {
  rtlEnabled: boolean;
  takeoffSurface: 'platform' | 'ground';
}

export interface ModeEchoOptions {
  /** The profile of the launch in progress; null while no launch has been requested. */
  requested: MissionMode | null;
  /** The plan of the same launch; compared only when the mission reports it. */
  plan?: LaunchPlan | null;
  /**
   * Called once per mission when what the mission reports differs from what
   * was requested. Both arguments name the profile; with a plan they also
   * name the return and the surface.
   */
  onMismatch: (reported: string, requested: string) => void;
}

function describe(mode: string, plan: LaunchPlan | null): string {
  if (plan === null) return mode;
  return `${mode}, ${plan.rtlEnabled ? 'com RTL' : 'sem RTL'}, ${plan.takeoffSurface === 'ground' ? 'solo' : 'totem'}`;
}

/** The plan `payload` reports, each field falling back to `wanted`'s when the payload lacks it. */
function reportedPlan(payload: Record<string, unknown>, wanted: LaunchPlan): LaunchPlan {
  const rtl = payload.rtl_enabled;
  const surface = payload.takeoff_surface;
  return {
    rtlEnabled: typeof rtl === 'boolean' ? rtl : wanted.rtlEnabled,
    takeoffSurface: surface === 'ground' || surface === 'platform' ? surface : wanted.takeoffSurface,
  };
}

/**
 * Confirm that the mission flies the profile and the plan the operator selected.
 *
 * The mission raises `mission.mode` (`{mode, rtl_enabled, takeoff_surface,
 * target_altitude_m}`) before `mission.parameters` and before Stage 1, so a
 * divergence is always found during the countdown, with nothing in the air.
 * Only a divergence acts: a mission that never reports the milestone, or a
 * field of it (an older backend, a truncated stream), is not an error here.
 */
export function useModeEcho({ requested, plan = null, onMismatch }: ModeEchoOptions): void {
  const bridge = useBridge();
  const requestedRef = useRef(requested);
  requestedRef.current = requested;
  const planRef = useRef(plan);
  planRef.current = plan;
  const handlerRef = useRef(onMismatch);
  handlerRef.current = onMismatch;

  const reportedFor = useRef<MissionMode | null>(null);

  useEffect(() => {
    reportedFor.current = null;
  }, [requested, plan?.rtlEnabled, plan?.takeoffSurface]);

  useEffect(() => {
    if (!bridge?.onMilestone) return;
    return bridge.onMilestone((event) => {
      if (event.kind === 'alert' || event.key !== 'mission.mode') return;
      const wanted = requestedRef.current;
      if (wanted === null || reportedFor.current === wanted) return;
      const payload = event.payload ?? {};
      const raw = payload.mode;
      const reported = parseMissionMode(raw);
      const wantedPlan = planRef.current;
      const echoed = wantedPlan === null ? null : reportedPlan(payload, wantedPlan);
      const samePlan =
        wantedPlan === null ||
        (echoed !== null &&
          echoed.rtlEnabled === wantedPlan.rtlEnabled &&
          echoed.takeoffSurface === wantedPlan.takeoffSurface);
      if (reported === wanted && samePlan) return;
      reportedFor.current = wanted;
      const reportedMode = typeof raw === 'string' ? raw : String(raw);
      handlerRef.current(describe(reportedMode, echoed), describe(wanted, wantedPlan));
    });
  }, [bridge]);
}
