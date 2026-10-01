// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useMissionCountdown, type MissionCountdown } from './useMissionCountdown';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

type Event = { kind: 'milestone' | 'alert'; key: string; payload: Record<string, unknown> };
let emit: (event: Event) => void = () => undefined;
let container: HTMLDivElement;
let root: Root;
let seen: MissionCountdown | null = null;

const Probe: React.FC = () => {
  seen = useMissionCountdown();
  return null;
};

const send = (key: string, payload: Record<string, unknown> = {}) =>
  act(() => emit({ kind: 'milestone', key, payload }));

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  (window as unknown as { bmgAPI: unknown }).bmgAPI = {
    onMilestone: (listener: (event: Event) => void) => {
      emit = listener;
      return () => undefined;
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

describe('useMissionCountdown', () => {
  it('follows the mission ticks and its clearance call', () => {
    expect(seen).toEqual({ remaining: null, clearance: false });
    send('mission.start', { countdown_sec: 5 });
    send('mission.countdown', { remaining_sec: 5 });
    expect(seen).toEqual({ remaining: 5, clearance: false });
    send('mission.countdown_3', { remaining_sec: 3.2 });
    send('mission.countdown', { remaining_sec: 3 });
    expect(seen).toEqual({ remaining: 3, clearance: true });
  });

  it('a new launch starts from nothing', () => {
    send('mission.countdown', { remaining_sec: 2 });
    send('mission.countdown_3', {});
    send('mission.start', { countdown_sec: 10 });
    expect(seen).toEqual({ remaining: null, clearance: false });
  });

  it('ignores a malformed tick', () => {
    send('mission.countdown', { remaining_sec: 'x' });
    expect(seen?.remaining).toBeNull();
  });
});
