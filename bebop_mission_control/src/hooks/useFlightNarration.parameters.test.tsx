// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useFlightNarration, useNarrationQueue, type Copilot } from './useCopilot';
import { PHRASE_POOLS } from '../lib/copilotPhrases';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

type Milestone = { kind: 'milestone' | 'alert'; key: string; payload: Record<string, unknown>; at: number; source: string };

let emit: (event: Milestone) => void = () => undefined;
let container: HTMLDivElement;
let root: Root;
const spoken: string[] = [];
const cached: string[][] = [];

const copilot: Copilot = {
  say: async (text) => {
    spoken.push(text);
    return true;
  },
  prepare: () => undefined,
  cancel: () => undefined,
  available: true,
};

const Probe: React.FC = () => {
  const queue = useNarrationQueue(copilot);
  useFlightNarration(queue, true, 1);
  return null;
};

const milestone = (key: string, payload: Record<string, unknown> = {}): Milestone => ({
  kind: 'milestone',
  key,
  payload,
  at: Date.now(),
  source: 'mission',
});

async function settle() {
  for (let i = 0; i < 20; i += 1) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  spoken.length = 0;
  cached.length = 0;
  (window as unknown as { bmgAPI: unknown }).bmgAPI = {
    onMilestone: (listener: (event: Milestone) => void) => {
      emit = listener;
      return () => undefined;
    },
    cacheSpeech: async (texts: string[]) => {
      cached.push(texts);
      return { success: true };
    },
  };
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(<Probe />));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete (window as unknown as { bmgAPI?: unknown }).bmgAPI;
});

describe('spoken numbers come from the launched mission (D2)', () => {
  it('a mission launched at 2,3 m and 0,35 m/s says exactly that', async () => {
    emit(milestone('mission.start'));
    emit(
      milestone('mission.parameters', {
        target_altitude_m: 2.3,
        cruise_mps: 0.35,
      })
    );
    // Neither payload carries the number: it must come from mission.parameters.
    emit(milestone('mission.takeoff', {}));
    await settle();
    expect(spoken.some((line) => line.includes('2,3 metros'))).toBe(true);
    expect(spoken.join(' ')).not.toMatch(/1,8|1 metro\b/);
  });

  it('a new mission forgets the previous mission document', async () => {
    emit(milestone('mission.start'));
    emit(milestone('mission.parameters', { target_altitude_m: 2.3 }));
    emit(milestone('mission.start'));
    emit(milestone('mission.takeoff', {}));
    await settle();
    const takeoff = spoken[spoken.length - 1];
    expect(takeoff).not.toMatch(/\d/);
  });

  it('mission.parameters itself is never spoken', async () => {
    emit(milestone('mission.parameters', { target_altitude_m: 2.3 }));
    await settle();
    expect(spoken).toEqual([]);
  });
});

describe('touchdown call (3.2)', () => {
  it('is spoken on the confirmed mission.touchdown milestone', async () => {
    emit(milestone('mission.start'));
    emit(milestone('mission.touchdown', { confirmed: true, at_base: true, method: 'settled' }));
    await settle();
    expect(spoken.some((line) => PHRASE_POOLS['mission.touchdown'].includes(line)), JSON.stringify(spoken)).toBe(true);
  });

  it('nothing about a landing is said when the run never reported a touchdown', async () => {
    emit(milestone('mission.start'));
    emit(milestone('mission.capture_done', {}));
    await settle();
    expect(spoken.join(' ')).not.toMatch(/[Pp]ous/);
  });
});

describe('phrase cache warm-up (3.9)', () => {
  it('sends every sentence of this mission to the copilot once its parameters arrive', async () => {
    emit(milestone('mission.start'));
    emit(milestone('mission.parameters', { target_altitude_m: 2.3, cruise_mps: 0.35 }));
    await settle();
    expect(cached).toHaveLength(1);
    const texts = cached[0];
    expect(texts).toContain('Decolagem autorizada. Subindo para 2,3 metros.');
    expect(texts.some((line) => line.startsWith('Para começar,'))).toBe(true);
    expect(texts).toContain('Missão abortada. Pouso imediato comandado.');
    expect(texts).toContain('Bateria crítica. Retornando à base para pouso.');
  });
});
