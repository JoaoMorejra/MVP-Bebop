// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useFlightNarration, useNarrationQueue, type Copilot, type LaunchSpeechEvent } from './useCopilot';
import { reserveLaunchPhrase } from '../lib/copilotPhrases';
import { readSpeechMs } from '../lib/speechTiming';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

type Milestone = { kind: 'milestone' | 'alert'; key: string; payload: Record<string, unknown>; at: number; source: string };

let emit: (event: Milestone) => void = () => undefined;
let container: HTMLDivElement;
let root: Root;
let heard = true;
const spoken: string[] = [];
const events: LaunchSpeechEvent[] = [];

const copilot: Copilot = {
  say: async (text) => {
    spoken.push(text);
    return heard;
  },
  prepare: () => undefined,
  cancel: () => undefined,
  available: true,
};

const Probe: React.FC<{ observe?: (event: LaunchSpeechEvent) => void }> = ({ observe }) => {
  const queue = useNarrationQueue(copilot);
  useFlightNarration(queue, true, 1, observe);
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

function mount(observe?: (event: LaunchSpeechEvent) => void) {
  act(() => root.render(<Probe observe={observe} />));
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  heard = true;
  spoken.length = 0;
  events.length = 0;
  window.localStorage.clear();
  (window as unknown as { bmgAPI: unknown }).bmgAPI = {
    onMilestone: (listener: (event: Milestone) => void) => {
      emit = listener;
      return () => undefined;
    },
  };
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete (window as unknown as { bmgAPI?: unknown }).bmgAPI;
});

describe('the launch line observer', () => {
  it('emits start and end for mission.countdown_3 only', async () => {
    mount((event) => events.push(event));
    emit(milestone('mission.start'));
    await settle();
    emit(milestone('mission.countdown_3'));
    await settle();
    emit(milestone('mission.takeoff'));
    await settle();
    expect(events.map((event) => event.phase)).toEqual(['start', 'end']);
    expect(events[1].heard).toBe(true);
    expect(events[0].at).toBeLessThanOrEqual(events[1].at);
  });

  it('end carries heard false when the voice did not answer', async () => {
    heard = false;
    mount((event) => events.push(event));
    emit(milestone('mission.start'));
    emit(milestone('mission.countdown_3'));
    await settle();
    expect(events[events.length - 1]).toMatchObject({ phase: 'end', heard: false });
  });

  it('the spoken text is the reserved launch line', async () => {
    const line = reserveLaunchPhrase('mission.countdown_3');
    mount((event) => events.push(event));
    emit(milestone('mission.start'));
    emit(milestone('mission.countdown_3'));
    await settle();
    expect(spoken).toContain(line);
    expect(events[0].text).toBe(line);
    expect(events[1].text).toBe(line);
  });

  it('records the measured duration of a heard line', async () => {
    mount((event) => events.push(event));
    emit(milestone('mission.start'));
    emit(milestone('mission.countdown_3'));
    await settle();
    const text = events[0].text;
    const measured = events[1].at - events[0].at;
    expect(readSpeechMs(window.localStorage, text)).toBe(measured >= 300 ? measured : null);
  });

  it('no observer given changes nothing in the existing assertions', async () => {
    mount();
    emit(milestone('mission.start'));
    emit(milestone('mission.countdown_3'));
    await settle();
    expect(spoken.length).toBe(2);
  });

  it('a new mission.start discards a pending observer state', async () => {
    let finish: (ok: boolean) => void = () => undefined;
    const slow: Copilot = {
      ...copilot,
      say: (text) =>
        new Promise<boolean>((resolve) => {
          spoken.push(text);
          finish = resolve;
        }),
    };
    const Slow: React.FC = () => {
      const queue = useNarrationQueue(slow);
      useFlightNarration(queue, true, 1, (event) => events.push(event));
      return null;
    };
    act(() => root.render(<Slow />));
    emit(milestone('mission.start'));
    await settle();
    finish(true);
    await settle();
    emit(milestone('mission.countdown_3'));
    await settle();
    expect(events.map((event) => event.phase)).toEqual(['start']);
    emit(milestone('mission.start'));
    await settle();
    finish(true);
    await settle();
    expect(events.map((event) => event.phase)).toEqual(['start']);
  });
});
