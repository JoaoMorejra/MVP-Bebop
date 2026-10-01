import { describe, expect, it, vi } from 'vitest';
import {
  ROS_PROCESS_PATTERN,
  cleanOrphanShm,
  createSsidChangeDetector,
  restartRos2Daemon,
} from '../../electron/rosHousekeeping.cjs';

describe('restartRos2Daemon', () => {
  it('stops then starts the daemon', async () => {
    const run = vi.fn(async () => ({ ok: true }));
    expect(await restartRos2Daemon(run)).toBe(true);
    expect(run.mock.calls.map((call) => call.slice(0, 2))).toEqual([
      ['ros2', ['daemon', 'stop']],
      ['ros2', ['daemon', 'start']],
    ]);
  });

  it('reports a start that failed', async () => {
    const run = vi.fn().mockResolvedValueOnce({ ok: true }).mockResolvedValueOnce({ ok: false });
    expect(await restartRos2Daemon(run)).toBe(false);
  });
});

describe('cleanOrphanShm', () => {
  it('cleans and counts the removed segments with no ROS process alive', async () => {
    const counts = [83, 22];
    const run = vi.fn(async () => ({ ok: true }));
    const result = await cleanOrphanShm({
      rosProcesses: () => [],
      countSegments: () => counts.shift() ?? 0,
      run,
    });
    expect(run).toHaveBeenCalledWith('fastdds', ['shm', 'clean'], expect.anything());
    expect(result).toEqual({ skipped: false, before: 83, after: 22, removed: 61 });
  });

  it('leaves the segments alone while a ROS process is alive', async () => {
    const run = vi.fn(async () => ({ ok: true }));
    const result = await cleanOrphanShm({ rosProcesses: () => [4242], countSegments: () => 10, run });
    expect(run).not.toHaveBeenCalled();
    expect(result).toEqual({ skipped: true, alive: [4242] });
  });

  it('matches the station processes and not the ros2 daemon', () => {
    for (const command of [
      'python3 /ws/src/mvp_mission_bebop/mvp_mission_bebop/mission.py --fly',
      '/ws/install/ros2_bebop_driver/lib/ros2_bebop_driver/bebop_driver --ros-args',
      'python3 /ws/bebop_mission_control/streamer/mjpeg_server.py',
      'python3 telemetry_bridge.py',
      'python3 command_bridge.py',
      'python3 -m mvp_mission_bebop.mission --no-fly',
    ]) {
      expect(ROS_PROCESS_PATTERN.test(command), command).toBe(true);
    }
    expect(ROS_PROCESS_PATTERN.test('/usr/bin/python3 /opt/ros/jazzy/bin/_ros2_daemon --rmw-implementation')).toBe(false);
    expect(ROS_PROCESS_PATTERN.test('electron .')).toBe(false);
  });
});

describe('createSsidChangeDetector', () => {
  it('fires on a change of network, not on the first one or a blank', () => {
    const changed = createSsidChangeDetector();
    expect(changed('Bebop2-A035633')).toBe(false);
    expect(changed('Bebop2-A035633')).toBe(false);
    expect(changed('')).toBe(false);
    expect(changed('Bebop2-B111111')).toBe(true);
    expect(changed('HomeNet')).toBe(true);
    expect(changed('HomeNet')).toBe(false);
  });
});
