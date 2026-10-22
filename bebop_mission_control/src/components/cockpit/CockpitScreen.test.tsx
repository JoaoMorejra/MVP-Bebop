// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CockpitScreen } from './CockpitScreen';
import type { TelemetryView } from '../../types/mission';
import type { MissionMode } from '../../lib/missionMode';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

if (typeof globalThis.PointerEvent === 'undefined') {
  // @ts-expect-error test polyfill
  globalThis.PointerEvent = class PointerEvent extends MouseEvent {};
}

vi.mock('./OpticalFeed', () => ({ OpticalFeed: () => <div data-testid="optical-feed" /> }));
vi.mock('./TacticalMap', () => ({ TacticalMap: () => <div data-testid="tactical-map" /> }));
vi.mock('./StageBar', () => ({ StageBar: () => <div data-testid="stage-bar" /> }));
vi.mock('./ForensicPanel', () => ({ ForensicPanel: () => <div data-testid="forensic-panel" /> }));

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const defaultTelemetry: TelemetryView = {
  connected: true,
  driver_running: true,
  flying_state: 2, // hovering
  flying_state_label: 'hovering',
  battery_pct: 80,
  battery_known: true,
  battery_source: 'aircraft',
  battery_age_sec: 0.1,
  altitude: 1.5,
  speed: 0,
  flight_time_sec: 10,
  latitude: 0,
  longitude: 0,
  heading: 0,
  gps_fix: true,
  base_known: false,
  base_latitude: null,
  base_longitude: null,
  base_source: 'none',
  camera_tilt_deg: 0,
  wifi_ssid: 'Bebop2-test',
  wifi_signal_dbm: -50,
  signal_source: 'aircraft',
  node_present: true,
  topics_ready: true,
  data_fresh: true,
  nav_fresh: true,
  simulated: false,
  source: 'hardware',
  ageSec: 0.1,
};

function renderCockpit(props: {
  onAbort?: () => void;
  missionState?: any;
  flyingState?: number | null;
  landProgress?: any;
  missionMode?: MissionMode;
}) {
  const onAbort = props.onAbort ?? vi.fn();
  const telemetry = {
    ...defaultTelemetry,
    flying_state: props.flyingState !== undefined ? props.flyingState : defaultTelemetry.flying_state,
  };

  act(() => {
    root.render(
      <CockpitScreen
        telemetry={telemetry}
        track={[]}
        stale={false}
        missionState={props.missionState ?? 'running'}
        stage={1}
        stageName="Decolagem"
        missionMode={props.missionMode}
        streamFps={30}
        streamBridgeUp={true}
        streamLive={true}
        streamWidth={856}
        streamHeight={480}
        streamSource="test"
        captureFlash={false}
        captureCount={0}
        latestCapture={null}
        arrivalRadius={null}
        landed={false}
        benchMode={false}
        benchStage={null}
        onRunStage={vi.fn()}
        onGotoStage={vi.fn()}
        pendingStage={null}
        voice={{ volume: 1, muted: false }}
        onVolume={vi.fn()}
        onToggleMute={vi.fn()}
        cameraTilt={0}
        cameraAvailable={true}
        onCameraTilt={vi.fn()}
        report={null}
        reportRevealed={0}
        reportAnalyzing={false}
        reportClosing={null}
        onAbort={onAbort}
        onFinish={vi.fn()}
        finishLock={{ state: 'in_flight', enabled: false, reason: 'Em voo', requiresConfirm: false }}
        landProgress={props.landProgress}
      />
    );
  });

  return { onAbort };
}

function unmountCockpit() {
  act(() => root.unmount());
}

describe('CockpitScreen Global Escape Shortcut (R11)', () => {
  it('triggers onAbort on Escape when mission is running', () => {
    const { onAbort } = renderCockpit({ missionState: 'running' });
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(onAbort).toHaveBeenCalledTimes(1);
  });

  it('triggers onAbort on Escape when unconfirmed even if process is aborted', () => {
    const { onAbort } = renderCockpit({
      missionState: 'aborted',
      flyingState: 0,
      landProgress: 'unconfirmed',
    });
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(onAbort).toHaveBeenCalledTimes(1);
  });

  it('does NOT trigger onAbort when focus is inside an input', () => {
    const { onAbort } = renderCockpit({ missionState: 'running' });
    const input = document.createElement('input');
    host.appendChild(input);
    input.focus();

    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(onAbort).not.toHaveBeenCalled();
  });

  it('does NOT trigger onAbort when focus is inside a terminal element', () => {
    const { onAbort } = renderCockpit({ missionState: 'running' });
    const term = document.createElement('div');
    term.className = 'xterm';
    const inner = document.createElement('span');
    term.appendChild(inner);
    host.appendChild(term);

    inner.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(onAbort).not.toHaveBeenCalled();
  });

  it('does NOT trigger onAbort when on the ground and mission is idle', () => {
    const { onAbort } = renderCockpit({
      missionState: 'idle',
      flyingState: 0,
      landProgress: null,
    });
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(onAbort).not.toHaveBeenCalled();
  });
});

describe('Ctrl+C abort shortcut', () => {
  const press = (init: KeyboardEventInit & { target?: Element }) => {
    const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
    (init.target ?? document.body).dispatchEvent(event);
    return event;
  };

  it.each(['running', 'arming'] as const)('aborts with Ctrl+C while the mission is %s, exactly like the button', (state) => {
    const { onAbort } = renderCockpit({ missionState: state });
    const event = press({ key: 'c', code: 'KeyC', ctrlKey: true });
    expect(onAbort).toHaveBeenCalledTimes(1);
    expect(event.defaultPrevented).toBe(true);
  });

  it('aborts with Cmd+C as well', () => {
    const { onAbort } = renderCockpit({ missionState: 'running' });
    press({ key: 'c', code: 'KeyC', metaKey: true });
    expect(onAbort).toHaveBeenCalledTimes(1);
  });

  it('does nothing, and leaves copy alone, when the abort is not available', () => {
    const { onAbort } = renderCockpit({ missionState: 'idle', flyingState: 0 });
    const event = press({ key: 'c', code: 'KeyC', ctrlKey: true });
    expect(onAbort).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  it('leaves Ctrl+C to a text field', () => {
    const { onAbort } = renderCockpit({ missionState: 'running' });
    const input = document.createElement('input');
    document.body.appendChild(input);
    press({ key: 'c', code: 'KeyC', ctrlKey: true, target: input });
    expect(onAbort).not.toHaveBeenCalled();
    input.remove();
  });

  it('fires once for a held key', () => {
    const { onAbort } = renderCockpit({ missionState: 'running' });
    press({ key: 'c', code: 'KeyC', ctrlKey: true });
    press({ key: 'c', code: 'KeyC', ctrlKey: true, repeat: true });
    press({ key: 'c', code: 'KeyC', ctrlKey: true, repeat: true });
    expect(onAbort).toHaveBeenCalledTimes(1);
  });

  it('does not capture Ctrl+Shift+C', () => {
    const { onAbort } = renderCockpit({ missionState: 'running' });
    press({ key: 'C', code: 'KeyC', ctrlKey: true, shiftKey: true });
    expect(onAbort).not.toHaveBeenCalled();
  });

  it('stops listening when the cockpit unmounts', () => {
    const { onAbort } = renderCockpit({ missionState: 'running' });
    unmountCockpit();
    press({ key: 'c', code: 'KeyC', ctrlKey: true });
    expect(onAbort).not.toHaveBeenCalled();
  });

  it.each(['simple', 'advanced'] as const)('aborts the same way in the %s mode', (mode) => {
    const { onAbort } = renderCockpit({ missionState: 'running', missionMode: mode });
    press({ key: 'c', code: 'KeyC', ctrlKey: true });
    expect(onAbort).toHaveBeenCalledTimes(1);
  });
});

describe('abort shortcut focus rules (D8)', () => {
  const pressOn = (target: Element) => {
    const event = new KeyboardEvent('keydown', { key: 'c', code: 'KeyC', ctrlKey: true, bubbles: true, cancelable: true });
    target.dispatchEvent(event);
    return event;
  };

  it.each(['range', 'checkbox', 'radio', 'button'])('aborts with Ctrl+C while an input[type=%s] holds the focus', (type) => {
    const { onAbort } = renderCockpit({ missionState: 'running' });
    const input = document.createElement('input');
    input.type = type;
    document.body.appendChild(input);
    input.focus();
    pressOn(input);
    expect(onAbort).toHaveBeenCalledTimes(1);
    input.remove();
  });

  it('aborts with Ctrl+C while a select holds the focus', () => {
    const { onAbort } = renderCockpit({ missionState: 'running' });
    const select = document.createElement('select');
    document.body.appendChild(select);
    pressOn(select);
    expect(onAbort).toHaveBeenCalledTimes(1);
    select.remove();
  });

  it.each(['text', 'search', 'email', 'password', 'url', 'tel', 'number'])('leaves Ctrl+C to an input[type=%s]', (type) => {
    const { onAbort } = renderCockpit({ missionState: 'running' });
    const input = document.createElement('input');
    input.type = type;
    document.body.appendChild(input);
    pressOn(input);
    expect(onAbort).not.toHaveBeenCalled();
    input.remove();
  });
});

describe('abort shortcut through the main process (D8)', () => {
  let deliver: (() => void) | null;
  let setArmed: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    deliver = null;
    setArmed = vi.fn();
    (window as unknown as { bmgAPI: unknown }).bmgAPI = {
      setCockpitAbortArmed: setArmed,
      onAbortShortcut: (callback: () => void) => {
        deliver = callback;
        return () => {
          deliver = null;
        };
      },
    };
  });

  afterEach(() => {
    delete (window as unknown as { bmgAPI?: unknown }).bmgAPI;
  });

  it('publishes the armed state and clears it on unmount', () => {
    renderCockpit({ missionState: 'running' });
    expect(setArmed).toHaveBeenLastCalledWith(true);
    unmountCockpit();
    expect(setArmed).toHaveBeenLastCalledWith(false);
    expect(deliver).toBeNull();
  });

  it('publishes false while the abort is not available', () => {
    renderCockpit({ missionState: 'idle', flyingState: 0 });
    expect(setArmed).toHaveBeenLastCalledWith(false);
  });

  it('aborts exactly once for the shortcut the main process delivers, the window listener being off', () => {
    const { onAbort } = renderCockpit({ missionState: 'running' });
    act(() => deliver!());
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', code: 'KeyC', ctrlKey: true, bubbles: true }));
    expect(onAbort).toHaveBeenCalledTimes(1);
  });

  it('ignores a delivered shortcut once the abort is no longer available', () => {
    const { onAbort } = renderCockpit({ missionState: 'idle', flyingState: 0 });
    act(() => deliver!());
    expect(onAbort).not.toHaveBeenCalled();
  });
});
