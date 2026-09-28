// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { TelemetryView } from '../../types/mission';
import { OpticalFeed } from './OpticalFeed';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const telemetry = {
  connected: true,
  data_fresh: true,
  altitude: 1.8,
  speed: 0.2,
  battery_known: true,
  battery_pct: 80,
  wifi_signal_dbm: -50,
  camera_tilt_deg: -45,
  source: 'aircraft',
  ageSec: 0.1,
} as unknown as TelemetryView;

describe('OpticalFeed gimbal readout', () => {
  it('shows the tilt once, on the dedicated rail, not beside the speed figure', () => {
    act(() =>
      root.render(
        <OpticalFeed
          fps={24}
          bridgeUp
          live
          width={856}
          height={480}
          source="/bebop/camera/image_raw"
          telemetry={telemetry}
          running
          flash={false}
          gimbalTilt={-45}
          cameraTilt={null}
          cameraAvailable
          onCameraTilt={() => undefined}
        />
      )
    );

    const text = container.textContent ?? '';
    expect(text).toContain('vel');
    expect(text).not.toMatch(/gimbal/i);
    expect(text.match(/-45°/g)).toHaveLength(1);
  });
});
