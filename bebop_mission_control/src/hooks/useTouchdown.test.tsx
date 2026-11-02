// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { MilestoneEvent } from '../types/bmg';
import { useTouchdown } from './useTouchdown';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let container: HTMLDivElement;
let root: Root;
let emit: (event: Pick<MilestoneEvent, 'kind' | 'key'>) => void;
let reset: () => void;
const seen = { down: false };

function Probe() {
  seen.down = useTouchdown();
  return null;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  (window as unknown as { bmgAPI: unknown }).bmgAPI = {
    onMilestone: (cb: (event: MilestoneEvent) => void) => {
      emit = (event) => cb({ payload: {}, at: Date.now(), source: 'mission', ...event });
      return () => undefined;
    },
    onMissionReset: (cb: () => void) => {
      reset = cb;
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

describe('useTouchdown', () => {
  it.each(['mission.touchdown', 'mission.touchdown_in_place', 'mission.touchdown_unconfirmed', 'mission.touchdown_battery'])(
    'is set by %s',
    (key) => {
      expect(seen.down).toBe(false);
      act(() => emit({ kind: 'milestone', key: 'mission.landing' }));
      expect(seen.down).toBe(false);
      act(() => emit({ kind: 'milestone', key }));
      expect(seen.down).toBe(true);
    }
  );

  it('is cleared by a new mission and by a host reset', () => {
    act(() => emit({ kind: 'milestone', key: 'mission.touchdown' }));
    act(() => emit({ kind: 'milestone', key: 'mission.start' }));
    expect(seen.down).toBe(false);
    act(() => emit({ kind: 'milestone', key: 'mission.touchdown' }));
    act(() => reset());
    expect(seen.down).toBe(false);
  });

  it('ignores alerts', () => {
    act(() => emit({ kind: 'alert', key: 'mission.touchdown' }));
    expect(seen.down).toBe(false);
  });
});
