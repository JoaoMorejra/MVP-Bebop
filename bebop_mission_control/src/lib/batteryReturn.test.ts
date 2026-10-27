import { describe, expect, it, vi } from 'vitest';
import * as batteryReturn from './batteryReturn';
import { BATTERY_LANDING_CALL, runCriticalBatteryReturn } from './batteryReturn';

function harness(jump: { success: boolean } | Error) {
  const order: string[] = [];
  const hooks = {
    say: vi.fn((label: string, text: string) => order.push(`say:${label}:${text}`)),
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
  it('asks for the return and says nothing when the request reaches the mission', async () => {
    const { order, hooks } = harness({ success: true });
    await expect(runCriticalBatteryReturn('rtl', hooks)).resolves.toBe('rtl');
    expect(order).toEqual(['goto']);
    expect(hooks.say).not.toHaveBeenCalled();
    expect(hooks.land).not.toHaveBeenCalled();
  });

  it('a refused jump says the emergency landing, then lands', async () => {
    const { order, hooks } = harness({ success: false });
    await expect(runCriticalBatteryReturn('rtl', hooks)).resolves.toBe('land');
    expect(order).toEqual(['goto', `say:battery.critical:${BATTERY_LANDING_CALL}`, 'land']);
  });

  it('a jump that throws is a refused jump', async () => {
    const { order, hooks } = harness(new Error('ipc down'));
    await expect(runCriticalBatteryReturn('rtl', hooks)).resolves.toBe('land');
    expect(order).toEqual(['goto', `say:battery.critical:${BATTERY_LANDING_CALL}`, 'land']);
  });

  it('lands without asking when there is no mission to take the jump', async () => {
    const { order, hooks } = harness({ success: true });
    await expect(runCriticalBatteryReturn('land', hooks)).resolves.toBe('land');
    expect(order).toEqual([`say:battery.critical:${BATTERY_LANDING_CALL}`, 'land']);
    expect(hooks.gotoStage).not.toHaveBeenCalled();
  });

  it('stays silent when the return is already under way', async () => {
    const { order, hooks } = harness({ success: true });
    await expect(runCriticalBatteryReturn('none', hooks)).resolves.toBe('none');
    expect(order).toEqual([]);
  });
});

describe('the station battery call', () => {
  it('is the single emergency-landing sentence', () => {
    expect(BATTERY_LANDING_CALL).toBe('Bateria crítica. Pouso de emergência iniciado.');
  });

  it('never names a landing in place', () => {
    expect(BATTERY_LANDING_CALL).not.toMatch(/no local/);
  });

  it('no longer exports the per-plan call sets', () => {
    const exported = Object.keys(batteryReturn);
    for (const name of ['batteryCalls', 'BATTERY_CALLS', 'IN_PLACE_CALLS', 'SIMPLE_CALLS', 'ADVANCED_CALLS']) {
      expect(exported).not.toContain(name);
    }
  });
});

describe('an undelivered jump with a grace window', () => {
  it('stands down without a word when the window ends with the return acknowledged', async () => {
    const { order, hooks } = harness({ success: false });
    const graceWindow = vi.fn(async () => true);
    await expect(runCriticalBatteryReturn('rtl', { ...hooks, graceWindow })).resolves.toBe('none');
    expect(graceWindow).toHaveBeenCalledTimes(1);
    expect(order).toEqual(['goto']);
  });

  it('says the emergency landing and lands when the window ends without it', async () => {
    const { order, hooks } = harness(new Error('bridge down'));
    const graceWindow = vi.fn(async () => {
      order.push('wait');
      return false;
    });
    await expect(runCriticalBatteryReturn('rtl', { ...hooks, graceWindow })).resolves.toBe('land');
    expect(order).toEqual(['goto', 'wait', `say:battery.critical:${BATTERY_LANDING_CALL}`, 'land']);
  });

  it('is not used for a delivered jump nor for a planned landing', async () => {
    const graceWindow = vi.fn(async () => true);
    await runCriticalBatteryReturn('rtl', { ...harness({ success: true }).hooks, graceWindow });
    await runCriticalBatteryReturn('land', { ...harness({ success: true }).hooks, graceWindow });
    expect(graceWindow).not.toHaveBeenCalled();
  });
});
