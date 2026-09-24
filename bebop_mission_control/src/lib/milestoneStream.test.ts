import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  COUNTDOWN_CALL_SEC,
  MILESTONE_LINE,
  createMilestoneParser,
  scheduleScriptMilestones,
  type MilestoneMessage,
} from '../../electron/milestones.cjs';

const line = (key: string, payload: object = {}) =>
  `2026-09-24 17:57:31 [INFO] [Milestone] [MILESTONE ${key}] ${JSON.stringify(payload)}\n`;

describe('mission stdout milestone parser', () => {
  it('matches the pattern the mission contract test pins', () => {
    // test/test_contracts.py::MILESTONE_LINE, verbatim.
    expect(MILESTONE_LINE.source).toBe('\\[MILESTONE ([a-z]+\\.[a-z0-9_]+)\\] (\\{.*\\})$');
  });

  it('emits one event per milestone line, in order, with its payload', () => {
    const seen: MilestoneMessage[] = [];
    const parser = createMilestoneParser((message) => seen.push(message), () => 1000);

    parser.push(line('mission.takeoff', { altitude_m: 1.2 }) + line('mission.scan_start'));

    expect(seen).toEqual([
      { key: 'mission.takeoff', payload: { altitude_m: 1.2 }, at: 1000, source: 'mission' },
      { key: 'mission.scan_start', payload: {}, at: 1000, source: 'mission' },
    ]);
  });

  it('reassembles a line split across stdout chunks', () => {
    const seen: string[] = [];
    const parser = createMilestoneParser((message) => seen.push(message.key));
    const text = line('mission.target_found', { forward_m: 2.7, bearing_deg: 3.6 });

    parser.push(text.slice(0, 40));
    expect(seen).toEqual([]);
    parser.push(text.slice(40, 90));
    parser.push(text.slice(90));

    expect(seen).toEqual(['mission.target_found']);
  });

  it('ignores step markers and ordinary log lines', () => {
    const seen: string[] = [];
    const parser = createMilestoneParser((message) => seen.push(message.key));

    parser.push('2026-09-24 [INFO] [Step1Takeoff] --- [STEP 1: Calibration] ---\n');
    parser.push('2026-09-24 [INFO] [Step2Search] Candidate target (1/3)\n');

    expect(seen).toEqual([]);
  });

  it('keeps the milestone and drops a payload that does not parse', () => {
    const seen: MilestoneMessage[] = [];
    const parser = createMilestoneParser((message) => seen.push(message));

    parser.push('[MILESTONE mission.landing] {"altitude_m": }\n');

    expect(seen.map((message) => [message.key, message.payload])).toEqual([['mission.landing', {}]]);
  });

  it('handles CRLF line endings', () => {
    const seen: string[] = [];
    const parser = createMilestoneParser((message) => seen.push(message.key));
    parser.push('[MILESTONE mission.rtl_start] {}\r\n');
    expect(seen).toEqual(['mission.rtl_start']);
  });

  it('flushes a final line that never received its newline', () => {
    const seen: string[] = [];
    const parser = createMilestoneParser((message) => seen.push(message.key));
    parser.push('[MILESTONE mission.landing] {}');
    expect(seen).toEqual([]);
    parser.flush();
    expect(seen).toEqual(['mission.landing']);
  });

  it('bounds the partial-line buffer against output with no newlines', () => {
    const seen: string[] = [];
    const parser = createMilestoneParser((message) => seen.push(message.key));
    parser.push('x'.repeat(200_000));
    parser.push('\n' + line('mission.landing'));
    expect(seen).toEqual(['mission.landing']);
  });
});

describe('station-owned script milestones', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('raises mission.start at once and mission.countdown_3 three seconds before liftoff', () => {
    const seen: string[] = [];
    scheduleScriptMilestones(10, (message) => seen.push(message.key));

    expect(seen).toEqual(['mission.start']);
    vi.advanceTimersByTime((10 - COUNTDOWN_CALL_SEC) * 1000 - 1);
    expect(seen).toEqual(['mission.start']);
    vi.advanceTimersByTime(1);
    expect(seen).toEqual(['mission.start', 'mission.countdown_3']);
  });

  it('raises both at once when the countdown is shorter than the call', () => {
    const seen: string[] = [];
    scheduleScriptMilestones(0, (message) => seen.push(message.key));
    vi.advanceTimersByTime(0);
    expect(seen).toEqual(['mission.start', 'mission.countdown_3']);
  });

  it('never raises the countdown call once cancelled', () => {
    const seen: string[] = [];
    const cancel = scheduleScriptMilestones(10, (message) => seen.push(message.key));
    cancel();
    vi.advanceTimersByTime(20_000);
    expect(seen).toEqual(['mission.start']);
  });

  it('tags both as station events and carries the countdown', () => {
    const seen: MilestoneMessage[] = [];
    scheduleScriptMilestones(8, (message) => seen.push(message), () => 5);
    vi.advanceTimersByTime(8000);
    expect(seen).toEqual([
      { key: 'mission.start', payload: { countdown_sec: 8 }, at: 5, source: 'station' },
      { key: 'mission.countdown_3', payload: { countdown_sec: 8 }, at: 5, source: 'station' },
    ]);
  });

  it('treats a malformed countdown as none', () => {
    const seen: string[] = [];
    scheduleScriptMilestones(Number.NaN, (message) => seen.push(message.key));
    vi.advanceTimersByTime(0);
    expect(seen).toEqual(['mission.start', 'mission.countdown_3']);
  });
});
