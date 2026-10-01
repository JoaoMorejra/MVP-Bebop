import { describe, expect, it } from 'vitest';
import { abortEnabled } from './flightState';
import type { MissionState } from '../types/mission';

describe('abortEnabled', () => {
  it.each(['running', 'arming'] as MissionState[])('is enabled while the mission is %s', (state) => {
    expect(abortEnabled(state, 0)).toBe(true);
    expect(abortEnabled(state, null)).toBe(true);
  });

  it.each(['idle', 'finished', 'finished_unconfirmed', 'aborted', 'faulted'] as MissionState[])(
    'stays enabled with the process %s while the aircraft reports hovering',
    (state) => {
      // Measured defect: the process died with the drone in the air and the
      // abort went grey, leaving no landing control on the cockpit.
      expect(abortEnabled(state, 2)).toBe(true);
    }
  );

  it('is disabled on the ground with no mission, and with an unknown state', () => {
    expect(abortEnabled('idle', 0)).toBe(false);
    expect(abortEnabled('finished', 0)).toBe(false);
    expect(abortEnabled('faulted', null)).toBe(false);
  });
});
