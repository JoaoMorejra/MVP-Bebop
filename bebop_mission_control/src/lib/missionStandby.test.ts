import { EventEmitter } from 'node:events';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { STANDBY_READY_LINE, createStandbyManager, standbyKey } from '../../electron/missionStandby.cjs';

class FakeChild extends EventEmitter {
  pid: number;
  stdout = Object.assign(new EventEmitter(), { setEncoding: () => undefined });
  stdin = { writes: [] as string[], write(text: string) { this.writes.push(text); return true; } };
  signals: string[] = [];
  constructor(pid: number) {
    super();
    this.pid = pid;
  }
  kill(signal: string) {
    this.signals.push(signal);
    return true;
  }
  ready() {
    this.stdout.emit('data', `noise\n${STANDBY_READY_LINE}\n`);
  }
}

const doc = (overrides: Record<string, unknown> = {}) => ({
  no_fly: true,
  network: { drone_ip: '192.168.42.1', namespace: 'bebop', camera_raw_topic: '/bebop/camera/image_raw' },
  vision: { model_path: 'yolov8n.pt', inference_device: 'AUTO', inference_imgsz: 480 },
  kinematics: { countdown_sec: 10, target_altitude_m: 1.8, control_loop_hz: 15 },
  ...overrides,
});

let children: FakeChild[];
let manager: ReturnType<typeof createStandbyManager>;

beforeEach(() => {
  vi.useFakeTimers();
  children = [];
  manager = createStandbyManager({
    spawnStandby: () => {
      const child = new FakeChild(1000 + children.length);
      children.push(child);
      return child as never;
    },
    log: () => undefined,
    retryMs: 5000,
  });
});
afterEach(() => {
  vi.useRealTimers();
});

describe('standbyKey', () => {
  it('ignores tuning and follows start-up fields and the driver', () => {
    expect(standbyKey(doc({ kinematics: { countdown_sec: 3, target_altitude_m: 2.4, control_loop_hz: 15 } }), true)).toBe(
      standbyKey(doc(), true)
    );
    expect(standbyKey(doc({ no_fly: false }), true)).not.toBe(standbyKey(doc(), true));
    expect(standbyKey(doc(), false)).not.toBe(standbyKey(doc(), true));
    expect(standbyKey(doc({ vision: { model_path: 'yolov8n.pt', inference_device: 'auto', inference_imgsz: '480' } }), true)).toBe(
      standbyKey(doc(), true)
    );
  });
});

describe('createStandbyManager', () => {
  it('hands a prepared mission the go and lets it go', () => {
    manager.ensure(doc(), true);
    children[0].ready();
    const child = manager.take(doc({ kinematics: { countdown_sec: 10, target_altitude_m: 2.0, control_loop_hz: 15 } }), true, 1234);
    expect(child).toBe(children[0]);
    const go = JSON.parse(children[0].stdin.writes[0]);
    expect(go).toEqual({ op: 'go', params: expect.objectContaining({ kinematics: expect.objectContaining({ target_altitude_m: 2.0 }) }), launch_at_ms: 1234 });
    expect(manager.isStandby(children[0].pid)).toBe(false);
  });

  it('gives nothing before the mission is prepared', () => {
    manager.ensure(doc(), true);
    expect(manager.take(doc(), true, 1)).toBeNull();
    expect(children[0].stdin.writes).toEqual([]);
  });

  it('gives nothing for a launch whose start-up fields differ', () => {
    manager.ensure(doc(), true);
    children[0].ready();
    expect(manager.take(doc({ no_fly: false }), true, 1)).toBeNull();
    expect(children[0].stdin.writes).toEqual([]);
  });

  it('says why a launch finds no prepared mission', () => {
    expect(manager.unavailableReason(doc(), true)).toBe('nenhuma missão em espera');
    manager.ensure(doc(), true);
    expect(manager.unavailableReason(doc(), true)).toBe('missão em espera ainda em preparo');
    children[0].ready();
    expect(manager.unavailableReason(doc(), true)).toBeNull();
    expect(manager.unavailableReason(doc({ no_fly: false }), true)).toBe('missão em espera preparada para outro no_fly');
    expect(manager.unavailableReason(doc(), false)).toBe('missão em espera preparada com o driver em outro estado');
  });

  it('pauses: no standby beside a mission spawned fresh, until the next ensure', () => {
    manager.ensure(doc(), true);
    manager.pause();
    expect(children[0].signals).toEqual(['SIGTERM']);
    children[0].emit('close', null, 'SIGTERM');
    vi.advanceTimersByTime(60000);
    expect(children).toHaveLength(1);
    manager.ensure(doc(), true);
    expect(children).toHaveLength(2);
  });

  it('prepares nothing while paused by a taken mission', () => {
    manager.ensure(doc(), true);
    children[0].ready();
    manager.take(doc(), true, 1);
    children[0].emit('close', 0, null);
    vi.advanceTimersByTime(60000);
    expect(children).toHaveLength(1);
  });

  it('keeps one prepared mission per key', () => {
    manager.ensure(doc(), true);
    manager.ensure(doc({ kinematics: { countdown_sec: 4, target_altitude_m: 3, control_loop_hz: 15 } }), true);
    expect(children).toHaveLength(1);
    manager.ensure(doc(), false);
    expect(children[0].signals).toEqual(['SIGTERM']);
    expect(children).toHaveLength(2);
  });

  it('re-prepares after a standby that exited, after a pause', () => {
    manager.ensure(doc(), true);
    children[0].emit('close', 1, null);
    expect(children).toHaveLength(1);
    vi.advanceTimersByTime(5000);
    expect(children).toHaveLength(2);
  });

  it('knows its pid and stops on dispose', () => {
    manager.ensure(doc(), true);
    expect(manager.isStandby(children[0].pid)).toBe(true);
    manager.dispose();
    expect(children[0].signals).toEqual(['SIGTERM']);
    children[0].emit('close', null, 'SIGTERM');
    vi.advanceTimersByTime(60000);
    expect(children).toHaveLength(1);
  });
});
