import { describe, expect, it, vi } from 'vitest';
import { BATTERY_CALLS, batteryCalls, runCriticalBatteryReturn } from './batteryReturn';

function harness(jump: { success: boolean } | Error) {
  const order: string[] = [];
  const hooks = {
    say: vi.fn((label: string, text: string) => order.push(`say:${text}`)),
    gotoStage: vi.fn(async () => {
      order.push('goto');
      if (jump instanceof Error) throw jump;
      return jump;
    }),
    land: vi.fn(async () => {
      order.push('land');
    }),
  };
  return { order, hooks };
}

describe('runCriticalBatteryReturn', () => {
  it('speaks the return only after the mission acknowledged the jump', async () => {
    const { order, hooks } = harness({ success: true });
    await expect(runCriticalBatteryReturn('rtl', hooks)).resolves.toBe('rtl');
    expect(order).toEqual(['goto', `say:${BATTERY_CALLS.returning}`]);
    expect(hooks.land).not.toHaveBeenCalled();
  });

  it('a refused jump lands in place and says that, never the return', async () => {
    const { order, hooks } = harness({ success: false });
    await expect(runCriticalBatteryReturn('rtl', hooks)).resolves.toBe('land');
    expect(order).toEqual(['goto', `say:${BATTERY_CALLS.landing}`, 'land']);
  });

  it('a jump that throws is a refused jump', async () => {
    const { order, hooks } = harness(new Error('ipc down'));
    await runCriticalBatteryReturn('rtl', hooks);
    expect(order).toEqual(['goto', `say:${BATTERY_CALLS.landing}`, 'land']);
  });

  it('lands without asking when the plan is to land', async () => {
    const { order, hooks } = harness({ success: true });
    await runCriticalBatteryReturn('land', hooks);
    expect(order).toEqual([`say:${BATTERY_CALLS.landing}`, 'land']);
    expect(hooks.gotoStage).not.toHaveBeenCalled();
  });

  it('only reports when the return is already under way', async () => {
    const { order, hooks } = harness({ success: true });
    await runCriticalBatteryReturn('none', hooks);
    expect(order).toEqual([`say:${BATTERY_CALLS.alreadyReturning}`]);
  });
});

describe('battery calls by mode', () => {
  it('keeps the advanced wording', () => {
    expect(batteryCalls('advanced')).toEqual(BATTERY_CALLS);
    expect(BATTERY_CALLS.returning).toBe('Bateria crítica. Retornando à base para pouso.');
  });

  it('speaks a landing in place for the intermediate mode and never a return', () => {
    const calls = batteryCalls('intermediate');
    for (const text of Object.values(calls)) expect(text).not.toMatch(/retorn|base/i);
    expect(calls.returning).toBe('Bateria crítica. Pouso no local iniciado.');
  });

  it('speaks a landing on the base for the simple mode and never a return', () => {
    const calls = batteryCalls('simple');
    for (const text of Object.values(calls)) expect(text).not.toMatch(/retorn/i);
    expect(calls.returning).toBe('Bateria crítica. Pouso na base iniciado.');
  });

  it('says the call of the mode when the jump to stage 5 is acknowledged', async () => {
    const say = vi.fn();
    await runCriticalBatteryReturn('rtl', { say, gotoStage: async () => ({ success: true }), land: async () => undefined }, 'intermediate');
    expect(say).toHaveBeenCalledWith('battery.critical', 'Bateria crítica. Pouso no local iniciado.');
  });
});
