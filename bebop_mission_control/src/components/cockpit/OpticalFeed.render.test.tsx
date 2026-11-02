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

describe('OpticalFeed without redundancy', () => {
  const renderFeed = (overrides: Partial<React.ComponentProps<typeof OpticalFeed>> = {}) =>
    act(() =>
      root.render(
        <OpticalFeed
          fps={24}
          bridgeUp
          live
          width={856}
          height={480}
          telemetry={telemetry}
          running
          flash={false}
          gimbalTilt={-45}
          cameraTilt={null}
          cameraAvailable
          onCameraTilt={() => undefined}
          {...overrides}
        />
      )
    );
  const offline: Array<[string, Partial<React.ComponentProps<typeof OpticalFeed>>]> = [
    ['no-link', { telemetry: { ...telemetry, connected: false } as TelemetryView, live: false }],
    ['no-bridge', { bridgeUp: false, live: false }],
    ['stalled', { live: false }],
  ];
  const panel = () => container.querySelector('.mesh-fine') as HTMLElement;

  it.each(offline)('offline feed shows only the glyph and "Sem sinal óptico" (%s)', (_state, overrides) => {
    renderFeed(overrides);
    expect(panel().textContent).toBe('Sem sinal óptico');
    expect(container.textContent).not.toContain('Sem enlace com a aeronave');
  });

  it.each(offline)('offline feed keeps data-feed-state and exposes the per-state reason as aria-label without a hover title (%s)', (state, overrides) => {
    renderFeed(overrides);
    const node = container.querySelector(`[data-feed-state="${state}"]`) as HTMLElement;
    expect(node).not.toBeNull();
    expect(node.title).toBe('');
    expect((node.getAttribute('aria-label') ?? '').length).toBeGreaterThan(0);
  });

  it('no-bridge reason does not mention Diagnóstico', () => {
    renderFeed({ bridgeUp: false, live: false });
    const node = container.querySelector('[data-feed-state="no-bridge"]') as HTMLElement;
    expect(node.title).toBe('');
    expect(node.getAttribute('aria-label')).toBe('A ponte MJPEG na porta 9090 não respondeu.');
    expect(node.getAttribute('aria-label')).not.toContain('Diagnóstico');
  });

  it('top right of the feed has no signal bars and no battery chip', () => {
    renderFeed();
    expect(container.textContent).not.toMatch(/Sinal:/);
    expect(container.textContent).not.toContain('80%');
    expect(container.querySelector('.right-3.top-3')).toBeNull();
    expect(container.textContent).toContain('480p · 24 FPS');
  });

  it('shows nothing about the detector', () => {
    renderFeed();
    expect(container.textContent).not.toMatch(/YOLO/);
    renderFeed({ live: true, running: true });
    expect(container.textContent).not.toMatch(/YOLO/);
  });

  it('keeps the flight time alone at the top centre, without an icon', () => {
    renderFeed();
    const timer = container.querySelector('[data-flight-timer]') as HTMLElement;
    expect(timer.className).toContain('left-1/2');
    expect(timer.querySelector('svg')).toBeNull();
    expect(timer.textContent).toMatch(/^(\d+:\d{2}|—)$/);
    renderFeed({ telemetry: { ...telemetry, connected: true, flight_time_sec: 75 } as TelemetryView });
    expect((container.querySelector('[data-flight-timer]') as HTMLElement).textContent).not.toContain('NaN');
  });

  it('puts the altitude and the speed in one chip, like the others', () => {
    renderFeed();
    const figures = container.querySelector('[data-flight-figures]') as HTMLElement;
    expect(figures.textContent).toContain('alt');
    expect(figures.textContent).toContain('vel');
    expect(figures.querySelector('.rounded-full')).not.toBeNull();
  });
});
