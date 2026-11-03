import { describe, expect, it } from 'vitest';
import { createLaunchGate } from '../../electron/launchGate.cjs';

describe('createLaunchGate', () => {
  it('admits one launch at a time', () => {
    const gate = createLaunchGate();
    expect(gate.begin()).toBe(true);
    expect(gate.starting()).toBe(true);
    expect(gate.begin()).toBe(false);
    gate.end();
    expect(gate.starting()).toBe(false);
    expect(gate.begin()).toBe(true);
  });

  it('records an abort that lands while the launch is still being prepared', () => {
    const gate = createLaunchGate();
    gate.begin();
    expect(gate.cancel()).toBe(true);
    expect(gate.cancelled()).toBe(true);
  });

  it('has nothing to cancel when no launch is being prepared', () => {
    const gate = createLaunchGate();
    expect(gate.cancel()).toBe(false);
    expect(gate.cancelled()).toBe(false);
  });

  it('does not carry a cancelled launch into the next one', () => {
    const gate = createLaunchGate();
    gate.begin();
    gate.cancel();
    gate.end();
    expect(gate.begin()).toBe(true);
    expect(gate.cancelled()).toBe(false);
  });
});
