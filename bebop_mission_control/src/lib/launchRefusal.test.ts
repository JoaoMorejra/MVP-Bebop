import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { EXIT_LAUNCH_REFUSED, createLaunchRefusalTracker } from '../../electron/launchRefusal.cjs';
import { LAUNCH_REFUSED_ALERT } from '../../electron/milestones.cjs';
import { EXIT_LAUNCH_REFUSED as RENDERER_EXIT_LAUNCH_REFUSED } from './missionOutcome';

const ALERT = `2026-10-20 22:00:00 [WARNING] [Milestone] [ALERT ${LAUNCH_REFUSED_ALERT}] {"field": "kinematics.target_altitude_m", "reason": "0.50 m abaixo do mínimo de 1.30 m sobre o totem de 1.00 m"}\n`;
const LINE = '[LAUNCH_REFUSED] kinematics.target_altitude_m: 0.50 m abaixo do mínimo\n';

describe('createLaunchRefusalTracker', () => {
  it('reads the alert of a refused document and reports it at exit 78', () => {
    const tracker = createLaunchRefusalTracker();
    tracker.push(ALERT.slice(0, 40));
    tracker.push(ALERT.slice(40) + LINE);
    tracker.flush();
    expect(tracker.refusal(EXIT_LAUNCH_REFUSED)).toEqual({
      field: 'kinematics.target_altitude_m',
      reason: '0.50 m abaixo do mínimo de 1.30 m sobre o totem de 1.00 m',
    });
  });

  it('falls back on the plain line when the alert is unreadable', () => {
    const tracker = createLaunchRefusalTracker();
    tracker.push(`[ALERT ${LAUNCH_REFUSED_ALERT}] {not json}\n${LINE}`);
    expect(tracker.refusal(78)).toEqual({ field: 'kinematics.target_altitude_m', reason: '0.50 m abaixo do mínimo' });
  });

  it('still reports a refusal when the process said nothing', () => {
    expect(createLaunchRefusalTracker().refusal(78)).toMatchObject({ field: 'mission_mode' });
  });

  it('reports nothing for any other exit', () => {
    const tracker = createLaunchRefusalTracker();
    tracker.push(ALERT);
    for (const code of [0, 1, 3, 4, 5, 6, 130, null]) expect(tracker.refusal(code)).toBeNull();
  });

  it('shares the exit status with the renderer and the mission', () => {
    expect(EXIT_LAUNCH_REFUSED).toBe(78);
    expect(RENDERER_EXIT_LAUNCH_REFUSED).toBe(EXIT_LAUNCH_REFUSED);
  });
});

describe('main.cjs wiring', () => {
  const source = readFileSync(resolve(__dirname, '../../electron/main.cjs'), 'utf-8');

  it('sends bmg:launch-refused before bmg:mission-exit on the mission close', () => {
    const close = source.slice(source.indexOf("missionProcess.on('close'"));
    const refused = close.indexOf("send('bmg:launch-refused', refused)");
    const exit = close.indexOf("send('bmg:mission-exit', { code, signal })");
    expect(refused).toBeGreaterThan(0);
    expect(exit).toBeGreaterThan(refused);
    expect(source).toContain('refusals.push(text);');
  });
});
