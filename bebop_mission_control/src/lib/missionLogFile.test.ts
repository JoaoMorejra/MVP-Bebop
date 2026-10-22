import { existsSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  KEEP_MISSION_LOGS,
  createMissionLogFile,
  missionLogName,
  rotateMissionLogs,
} from '../../electron/missionLogFile.cjs';

const settle = () => new Promise((done) => setTimeout(done, 50));

describe('missionLogName', () => {
  it('stamps the local time as YYYYMMDD_HHMMSS', () => {
    expect(missionLogName(new Date(2026, 9, 21, 9, 5, 7))).toBe('mission_20261021_090507.log');
  });
});

describe('createMissionLogFile', () => {
  it('appends stdout and tagged stderr of one launch to its own file', async () => {
    const dir = join(mkdtempSync(join(tmpdir(), 'bmg-logs-')), 'logs');
    const log = createMissionLogFile({ directory: () => dir, now: () => new Date(2026, 9, 21, 9, 5, 7) });
    log.write('stdout', 'before open\n');
    const file = log.open();
    expect(file).toBe(join(dir, 'mission_20261021_090507.log'));
    log.write('stdout', '[ALT_PROBE z_rel=0.80]\n');
    log.write('stderr', 'warning one\nwarning two\n');
    log.close();
    await settle();
    expect(readFileSync(file!, 'utf-8')).toBe('[ALT_PROBE z_rel=0.80]\n[stderr] warning one\n[stderr] warning two\n');
    log.write('stdout', 'after close\n');
    await settle();
    expect(readFileSync(file!, 'utf-8')).not.toContain('after close');
  });

  it('never overwrites a launch of the same second', () => {
    const dir = mkdtempSync(join(tmpdir(), 'bmg-logs-'));
    const log = createMissionLogFile({ directory: () => dir, now: () => new Date(2026, 9, 21, 9, 5, 7) });
    const first = log.open();
    const second = log.open();
    log.close();
    expect(second).not.toBe(first);
    expect(second).toMatch(/mission_20261021_090507_1\.log$/);
  });

  it('keeps the 30 most recent files and nothing else of the directory is touched', () => {
    const dir = mkdtempSync(join(tmpdir(), 'bmg-logs-'));
    for (let i = 0; i < 35; i += 1) writeFileSync(join(dir, `mission_202610${String(i % 28 + 1).padStart(2, '0')}_0000${String(i).padStart(2, '0')}.log`), '');
    writeFileSync(join(dir, 'notes.txt'), 'keep');
    const removed = rotateMissionLogs(dir);
    expect(removed).toHaveLength(35 - KEEP_MISSION_LOGS);
    expect(readdirSync(dir).filter((name) => name.startsWith('mission_'))).toHaveLength(KEEP_MISSION_LOGS);
    expect(existsSync(join(dir, 'notes.txt'))).toBe(true);
  });

  it('degrades to no file when the directory cannot be created', () => {
    const warnings: string[] = [];
    const blocker = join(mkdtempSync(join(tmpdir(), 'bmg-logs-')), 'not-a-directory');
    writeFileSync(blocker, '');
    const log = createMissionLogFile({ directory: () => join(blocker, 'logs'), warn: (text) => warnings.push(text) });
    expect(log.open()).toBeNull();
    expect(() => log.write('stdout', 'x')).not.toThrow();
    expect(warnings.join('')).toMatch(/Log de missão/);
  });
});

describe('main.cjs wiring', () => {
  const main = readFileSync(resolve(__dirname, '../../electron/main.cjs'), 'utf-8');

  it('mirrors the mission channel to the file, opened at the launch and closed at the exit', () => {
    expect(main).toContain("if (channel === 'mission') missionLogFile.write(entry.type, entry.text);");
    expect(main).toContain("path.join(app.getPath('userData'), 'logs')");
    const launch = main.indexOf('missionStartedAt = Date.now();\n    missionLogFile.open();');
    expect(launch).toBeGreaterThan(0);
    const close = main.slice(main.indexOf("missionProcess.on('close'"));
    expect(close.indexOf('missionLogFile.close();')).toBeGreaterThan(close.indexOf("send('bmg:mission-exit'"));
  });
});
