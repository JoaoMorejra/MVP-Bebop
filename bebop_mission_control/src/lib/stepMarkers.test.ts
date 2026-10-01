import { describe, expect, it, vi } from 'vitest';
import { STEP_LINE, createStepMarkerParser } from '../../electron/milestones.cjs';

function collect() {
  const steps: number[] = [];
  const parser = createStepMarkerParser((stepNumber: number) => steps.push(stepNumber));
  return { steps, parser };
}

describe('createStepMarkerParser', () => {
  it('matches the marker mission.py logs', () => {
    const { steps, parser } = collect();
    parser.push('2026-09-30 [INFO] --- [STEP 2: Varredura Retilínea] ---\n');
    expect(steps).toEqual([2]);
  });

  it('emits every marker of a chunk, in order', () => {
    // Measured live: a Stage 3 skipped without a target logs [STEP 3 and
    // [STEP 4 in the same millisecond, and they arrive in one chunk.
    const { steps, parser } = collect();
    parser.push('--- [STEP 3: Aproximação IBVS] ---\n--- [STEP 4: Inspeção Nadir] ---\n');
    expect(steps).toEqual([3, 4]);
  });

  it('reassembles a marker split across chunks', () => {
    const { steps, parser } = collect();
    parser.push('--- [STE');
    expect(steps).toEqual([]);
    parser.push('P 5: RTL] ---\n');
    expect(steps).toEqual([5]);
  });

  it('holds a trailing partial line until its newline or flush', () => {
    const { steps, parser } = collect();
    parser.push('--- [STEP 1: Decolagem] ---');
    expect(steps).toEqual([]);
    parser.flush();
    expect(steps).toEqual([1]);
    parser.flush();
    expect(steps).toEqual([1]);
  });

  it('ignores stage numbers outside the five-stage contract and milestone lines', () => {
    const { steps, parser } = collect();
    parser.push('[STEP 6: nope]\n[STEP 0: nope]\n[MILESTONE mission.landing] {}\n');
    expect(steps).toEqual([]);
  });

  it('handles CRLF line endings', () => {
    const emit = vi.fn();
    const parser = createStepMarkerParser(emit);
    parser.push('--- [STEP 2: Varredura] ---\r\n');
    expect(emit).toHaveBeenCalledWith(2);
  });

  it('keeps the matcher test_contracts.py pins', () => {
    expect(STEP_LINE.source).toBe('\\[STEP ([1-5]):');
  });
});
