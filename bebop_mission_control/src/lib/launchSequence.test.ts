import { describe, expect, it } from 'vitest';
import {
  LAUNCH_MESSAGE,
  LAUNCH_STEPS,
  LOAD_CEILING_PCT,
  LOAD_MIN_MS,
  MIN_SEQUENCE_MS,
  deriveLaunchView,
  stepForPercent,
  stepStates,
  type LaunchSignals,
} from './launchSequence';

const T0 = 1_000_000;

function signals(overrides: Partial<LaunchSignals> = {}): LaunchSignals {
  return {
    now: T0,
    clickedAt: T0,
    paramsCommitted: false,
    linkReady: false,
    stageReached: false,
    countdownStarted: false,
    trim: null,
    clearance: false,
    countdownZero: false,
    speech: { startedAt: null, endedAt: null, heard: false, expectedMs: 3000 },
    ...overrides,
  };
}

const allSteps = {
  paramsCommitted: true,
  linkReady: true,
  stageReached: true,
  countdownStarted: true,
  trim: 'acked' as const,
};

describe('launchSequence', () => {
  it('weights of the six steps add up to LOAD_CEILING_PCT', () => {
    expect(LAUNCH_STEPS.map((step) => step.id)).toEqual(['params', 'link', 'gimbal', 'imu', 'ground', 'vision']);
    expect(LAUNCH_STEPS.map((step) => step.weight)).toEqual([10, 10, 15, 20, 15, 20]);
    expect(LAUNCH_STEPS.map((step) => step.label)).toEqual(['Parâmetros', 'Enlace', 'Gimbal', 'IMU', 'Solo', 'Visão']);
    expect(LAUNCH_STEPS.reduce((sum, step) => sum + step.weight, 0)).toBe(LOAD_CEILING_PCT);
  });

  it('percent is 0 right after the click and the params step is active even when signals are already ready', () => {
    const view = deriveLaunchView(signals({ ...allSteps, clearance: true }));
    expect(view.percent).toBe(0);
    expect(view.phase).toBe('loading');
    expect(view.stepId).toBe('params');
    expect(view.label).toBe('Parâmetros');
    expect(stepStates(signals()).params).toBe('active');
  });

  it('step order of activity: params, link, then gimbal and ground after stageReached', () => {
    expect(stepStates(signals()).link).toBe('pending');
    const linking = stepStates(signals({ paramsCommitted: true }));
    expect(linking.params).toBe('done');
    expect(linking.link).toBe('active');
    expect(linking.gimbal).toBe('pending');
    const staged = stepStates(signals({ paramsCommitted: true, linkReady: true, stageReached: true }));
    expect(staged.gimbal).toBe('active');
    expect(staged.ground).toBe('active');
    expect(staged.imu).toBe('active');
    expect(staged.vision).toBe('pending');
    const counting = stepStates(signals({ ...allSteps, trim: null }));
    expect(counting.gimbal).toBe('done');
    expect(counting.ground).toBe('done');
    expect(counting.vision).toBe('active');
  });

  it('a skipped trim counts the full weight of the imu step', () => {
    const acked = deriveLaunchView(signals({ ...allSteps, now: T0 + 5_500 }));
    const skipped = deriveLaunchView(signals({ ...allSteps, trim: 'skipped', now: T0 + 5_500 }));
    expect(stepStates(signals({ ...allSteps, trim: 'skipped' })).imu).toBe('skipped');
    expect(skipped.percent).toBe(acked.percent);
    expect(acked.percent).toBeGreaterThanOrEqual(70);
  });

  it('percent never exceeds 89 while clearance has not arrived', () => {
    const view = deriveLaunchView(signals({ ...allSteps, now: T0 + 600_000 }));
    expect(view.percent).toBe(LOAD_CEILING_PCT - 1);
    expect(view.phase).toBe('loading');
    expect(view.label).toBe('Visão');
  });

  it('partial credit inside an active step creeps up to the top of its band without crossing into the next step', () => {
    const view = deriveLaunchView(signals({ ...allSteps, now: T0 + 600_000 }));
    expect(view.percent).toBe(89);
    const early = deriveLaunchView(signals({ now: T0 + 600_000 }));
    expect(early.percent).toBe(9);
    expect(early.stepId).toBe('params');
  });

  it('when a step takes longer than expected, the count slows down and continues ticking inside that step without freezing', () => {
    const t1 = deriveLaunchView(signals({ paramsCommitted: true, linkReady: true, stageReached: true, now: T0 + 2_500 })).percent;
    const t2 = deriveLaunchView(signals({ paramsCommitted: true, linkReady: true, stageReached: true, now: T0 + 5_000 })).percent;
    const t3 = deriveLaunchView(signals({ paramsCommitted: true, linkReady: true, stageReached: true, now: T0 + 12_000 })).percent;
    expect(t1).toBeGreaterThanOrEqual(20);
    expect(t2).toBeGreaterThan(t1);
    expect(t3).toBeGreaterThan(t2);
    expect(t3).toBeLessThan(35);
    expect(deriveLaunchView(signals({ paramsCommitted: true, linkReady: true, stageReached: true, now: T0 + 12_000 })).label).toBe(
      'Gimbal'
    );
  });

  it('percent never decreases when floorPct is passed back', () => {
    expect(deriveLaunchView(signals(), 55).percent).toBeGreaterThanOrEqual(55);
  });

  it('clearance with every checkpoint done enters announcing at 90 with message "Tudo Pronto!" once LOAD_MIN_MS has elapsed', () => {
    const view = deriveLaunchView(signals({ ...allSteps, clearance: true, now: T0 + LOAD_MIN_MS }));
    expect(view.phase).toBe('announcing');
    expect(view.percent).toBe(90);
    expect(view.message).toBe(LAUNCH_MESSAGE);
    expect(view.message).toBe('Tudo Pronto!');
    expect(view.stepId).toBeNull();
  });

  it('announcing interpolates 90..99 over expectedMs of the speech', () => {
    const at = (elapsed: number) =>
      deriveLaunchView(
        signals({
          ...allSteps,
          clearance: true,
          now: T0 + 10_000 + elapsed,
          speech: { startedAt: T0 + 10_000, endedAt: null, heard: true, expectedMs: 3000 },
        })
      ).percent;
    expect(at(0)).toBe(90);
    expect(at(1500)).toBe(94);
    expect(at(2999)).toBe(98);
    expect(at(10_000)).toBe(99);
  });

  it('an unheard speech uses expectedMs, not an instant jump', () => {
    const view = deriveLaunchView(
      signals({
        ...allSteps,
        clearance: true,
        countdownZero: true,
        now: T0 + 10_000,
        speech: { startedAt: T0 + 10_000, endedAt: T0 + 10_000, heard: false, expectedMs: 3000 },
      })
    );
    expect(view.percent).toBeLessThan(100);
  });

  it('never reaches 100 before MIN_SEQUENCE_MS (10 s) even if all steps, speech and countdownZero are ready early', () => {
    const early = deriveLaunchView(
      signals({
        ...allSteps,
        clearance: true,
        countdownZero: true,
        now: T0 + 8_500,
        speech: { startedAt: T0 + 7_000, endedAt: T0 + 8_000, heard: true, expectedMs: 1000 },
      })
    );
    expect(early.percent).toBeLessThan(100);
    expect(early.phase).toBe('announcing');

    const atTenSeconds = deriveLaunchView(
      signals({
        ...allSteps,
        clearance: true,
        countdownZero: true,
        now: T0 + MIN_SEQUENCE_MS,
        speech: { startedAt: T0 + 7_000, endedAt: T0 + 8_000, heard: true, expectedMs: 1000 },
      })
    );
    expect(atTenSeconds.percent).toBe(100);
    expect(atTenSeconds.phase).toBe('done');
  });

  it('100 only when speech ended and countdownZero and 10 s elapsed', () => {
    const view = deriveLaunchView(
      signals({
        ...allSteps,
        clearance: true,
        countdownZero: true,
        now: T0 + 14_000,
        speech: { startedAt: T0 + 10_000, endedAt: T0 + 13_000, heard: true, expectedMs: 3000 },
      })
    );
    expect(view.percent).toBe(100);
    expect(view.phase).toBe('done');
  });

  it('speech ended without countdownZero holds at 99 in releasing', () => {
    const view = deriveLaunchView(
      signals({
        ...allSteps,
        clearance: true,
        now: T0 + 14_000,
        speech: { startedAt: T0 + 10_000, endedAt: T0 + 13_000, heard: true, expectedMs: 3000 },
      })
    );
    expect(view.percent).toBe(99);
    expect(view.phase).toBe('releasing');
    expect(view.message).toBe('Tudo Pronto!');
  });

  it('countdownZero without speech end holds at 99', () => {
    const view = deriveLaunchView(
      signals({
        ...allSteps,
        clearance: true,
        countdownZero: true,
        now: T0 + 11_000,
        speech: { startedAt: T0 + 10_000, endedAt: null, heard: true, expectedMs: 3000 },
      })
    );
    expect(view.percent).toBe(99);
    expect(view.phase).toBe('releasing');
    expect(view.message).toBe('Tudo Pronto!');
  });

  it('done phase at 100 has no label and no message', () => {
    const view = deriveLaunchView(
      signals({
        ...allSteps,
        clearance: true,
        countdownZero: true,
        now: T0 + 20_000,
        speech: { startedAt: T0 + 10_000, endedAt: null, heard: false, expectedMs: 3000 },
      })
    );
    expect(view).toEqual({ phase: 'done', percent: 100, stepId: null, label: null, message: null });
  });

  it('cites every step in LAUNCH_STEPS order as percent progresses from 0 to 89', () => {
    const seenLabels: string[] = [];
    for (let pct = 0; pct < LOAD_CEILING_PCT; pct += 1) {
      const step = stepForPercent(pct);
      if (seenLabels[seenLabels.length - 1] !== step.label) {
        seenLabels.push(step.label);
      }
    }
    expect(seenLabels).toEqual(['Parâmetros', 'Enlace', 'Gimbal', 'IMU', 'Solo', 'Visão']);
  });

  it('a countdown that reaches zero without the clearance call still completes after 10 s', () => {
    const view = deriveLaunchView(signals({ ...allSteps, countdownZero: true, now: T0 + 12_000 }));
    expect(view.phase).toBe('done');
    expect(view.percent).toBe(100);
  });
});
