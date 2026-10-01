import { describe, expect, it, vi } from 'vitest';
import { BATTERY_CALLS, runCriticalBatteryReturn } from './batteryReturn';

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
