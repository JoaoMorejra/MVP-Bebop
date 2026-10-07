import { describe, expect, it } from 'vitest';
import { createOrphanReaper, parseOrphanCandidates } from '../../electron/orphanMissions.cjs';

describe('parseOrphanCandidates', () => {
  it('classifies a standby by its command line', () => {
    const output = '501 python3 /x/mission.py --config /x/mission_config.json --standby --no-fly\n';
    const [candidate] = parseOrphanCandidates(output, { ownPid: 1, missionPid: null, isTracked: () => false });
    expect(candidate).toEqual({ pid: 501, standby: true });
  });

  it('classifies a flight run (no --standby) as not forceable', () => {
    const output = '502 python3 /x/mission.py --config /x/mission_config.json --fly\n';
    const [candidate] = parseOrphanCandidates(output, { ownPid: 1, missionPid: null, isTracked: () => false });
    expect(candidate).toEqual({ pid: 502, standby: false });
  });

  it('excludes this process, the tracked mission, and the tracked standby', () => {
    const output = ['1 self', '7 mission', '9 tracked-standby', '501 python3 /x/mission.py --standby'].join('\n');
    const result = parseOrphanCandidates(output, {
      ownPid: 1,
      missionPid: 7,
      isTracked: (pid) => pid === 9,
    });
    expect(result.map((c) => c.pid)).toEqual([501]);
  });

  it('ignores blank lines and non-numeric noise', () => {
    const output = '\n  \nnot-a-pid stray line\n501 python3 /x/mission.py --standby\n';
    const result = parseOrphanCandidates(output, { ownPid: 1, missionPid: null, isTracked: () => false });
    expect(result.map((c) => c.pid)).toEqual([501]);
  });
});

describe('createOrphanReaper', () => {
  function harness(candidates: Array<{ pid: number; standby: boolean }>, graceMs = 4000) {
    const signals: Array<[number, string]> = [];
    const lines: string[] = [];
    let clock = 0;
    const reaper = createOrphanReaper({
      listCandidates: () => candidates,
      signal: (pid, sig) => signals.push([pid, sig]),
      log: (text) => lines.push(text),
      graceMs,
      now: () => clock,
    });
    return { reaper, signals, lines, advance: (ms: number) => (clock += ms) };
  }

  it('sends SIGINT once to a newly seen orphan and blocks the launch on it', () => {
    const { reaper, signals } = harness([{ pid: 501, standby: true }]);
    const { blocking } = reaper.sweep();
    expect(signals).toEqual([[501, 'SIGINT']]);
    expect(blocking).toEqual([501]);
  });

  it('does not re-signal an orphan already asked, within its grace period', () => {
    const { reaper, signals, advance } = harness([{ pid: 501, standby: true }]);
    reaper.sweep();
    advance(1000);
    const { blocking } = reaper.sweep();
    expect(signals).toEqual([[501, 'SIGINT']]);
    expect(blocking).toEqual([501]);
  });

  it('forces a standby that outlives its grace period, and stops blocking on it', () => {
    const { reaper, signals, lines, advance } = harness([{ pid: 501, standby: true }], 4000);
    reaper.sweep();
    advance(4000);
    const { blocking } = reaper.sweep();
    expect(signals).toEqual([
      [501, 'SIGINT'],
      [501, 'SIGKILL'],
    ]);
    expect(blocking).toEqual([]);
    expect(lines[1]).toMatch(/finalizada/);
  });

  it('never forces a flight run past the grace period: it may be mid-air', () => {
    const { reaper, signals, advance } = harness([{ pid: 502, standby: false }], 4000);
    reaper.sweep();
    advance(60000);
    const { blocking } = reaper.sweep();
    expect(signals).toEqual([[502, 'SIGINT']]);
    expect(blocking).toEqual([502]);
  });

  it('forgets a pid once it is no longer reported, so a later reuse is treated as new', () => {
    let candidates: Array<{ pid: number; standby: boolean }> = [{ pid: 501, standby: true }];
    const signals: Array<[number, string]> = [];
    let clock = 0;
    const reaper = createOrphanReaper({
      listCandidates: () => candidates,
      signal: (pid, sig) => signals.push([pid, sig]),
      log: () => undefined,
      graceMs: 4000,
      now: () => clock,
    });
    reaper.sweep();
    candidates = [];
    clock += 60000;
    expect(reaper.sweep().blocking).toEqual([]);
    candidates = [{ pid: 501, standby: true }];
    const { blocking } = reaper.sweep();
    expect(signals).toEqual([
      [501, 'SIGINT'],
      [501, 'SIGINT'],
    ]);
    expect(blocking).toEqual([501]);
  });

  it('clears on the same sweep that already caught the grace period expiring', () => {
    // A launch click calling sweep() directly should self-heal in one call
    // when the standby's grace period has already elapsed since boot.
    const { reaper, advance } = harness([{ pid: 501, standby: true }]);
    reaper.sweep(); // e.g. the app's own boot-time sweep
    advance(5000); // the operator opens the preflight screen and clicks
    const { blocking } = reaper.sweep(); // the launch's own sweep
    expect(blocking).toEqual([]);
  });
});
