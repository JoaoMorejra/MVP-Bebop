import { describe, expect, it } from 'vitest';
import { airborneHandoff, countdownExitHandoff } from './cockpitHandoff';

const LANDED = 0;
const TAKINGOFF = 1;
const HOVERING = 2;

describe('airborneHandoff', () => {
  const onDial = { counting: true, screen: 'preflight' as const };

  it('does not open the cockpit at the click: the process is still arming on the ground', () => {
    expect(airborneHandoff({ ...onDial, missionState: 'arming', flyingState: LANDED, stage: 0 })).toBe(false);
  });

  it('does not open it for a confirmed process that has not left the ground', () => {
    expect(airborneHandoff({ ...onDial, missionState: 'running', flyingState: LANDED, stage: 1 })).toBe(false);
    expect(airborneHandoff({ ...onDial, missionState: 'running', flyingState: null, stage: 0 })).toBe(false);
  });

  it('does not trust an airborne report before the process is confirmed', () => {
    expect(airborneHandoff({ ...onDial, missionState: 'arming', flyingState: HOVERING, stage: 0 })).toBe(false);
  });

  it('opens it when the aircraft reports itself off the ground under a running mission', () => {
    expect(airborneHandoff({ ...onDial, missionState: 'running', flyingState: TAKINGOFF, stage: 1 })).toBe(true);
  });

  it('opens it when the mission reached the flight stages', () => {
    expect(airborneHandoff({ ...onDial, missionState: 'running', flyingState: null, stage: 2 })).toBe(true);
  });

  it('does nothing once the cockpit is already shown', () => {
    expect(
      airborneHandoff({ counting: false, screen: 'cockpit', missionState: 'running', flyingState: HOVERING, stage: 2 })
    ).toBe(false);
  });
});

describe('countdownExitHandoff', () => {
  const exited = { counting: true, spawnRequested: true, missionState: 'faulted' as const, refused: false, bench: false };

  it('keeps a flight that died on the ground on pre-flight, with the reason', () => {
    expect(countdownExitHandoff({ ...exited, flyingState: LANDED })).toBe('preflight');
    expect(countdownExitHandoff({ ...exited, flyingState: null })).toBe('preflight');
  });

  it('shows the cockpit when the process died with the aircraft in the air', () => {
    expect(countdownExitHandoff({ ...exited, flyingState: HOVERING })).toBe('cockpit');
  });

  it('keeps the bench behaviour: the rehearsal outcome is shown in the cockpit', () => {
    expect(countdownExitHandoff({ ...exited, bench: true, flyingState: LANDED })).toBe('cockpit');
  });

  it('leaves a refused document to its own handler', () => {
    expect(countdownExitHandoff({ ...exited, refused: true, flyingState: LANDED })).toBeNull();
  });

  it('does nothing while the process is up or outside the countdown', () => {
    expect(countdownExitHandoff({ ...exited, missionState: 'running', flyingState: LANDED })).toBeNull();
    expect(countdownExitHandoff({ ...exited, counting: false, flyingState: LANDED })).toBeNull();
    expect(countdownExitHandoff({ ...exited, spawnRequested: false, flyingState: LANDED })).toBeNull();
  });
});
