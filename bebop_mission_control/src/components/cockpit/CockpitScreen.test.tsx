// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CockpitScreen } from './CockpitScreen';
import type { TelemetryView } from '../../types/mission';

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
