// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { TelemetryView } from '../../types/mission';
import { TopBar } from './TopBar';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

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

function render(frozen = false) {
  const noop = vi.fn();
  act(() =>
    root.render(
      <TopBar
        screen="preflight"
        onNavigate={noop}
        live={false}
        locked={false}
        frozen={frozen}
        telemetry={{ connected: true, battery_known: true, battery_pct: 78 } as unknown as TelemetryView}
        networks={[]}
        currentSsid=""
        scanning={false}
        linkPhase="idle"
        readiness={null}
        flightReady={false}
        driverRunning={false}
        onScan={noop}
        onConnect={noop}
        onStartDriver={noop}
        onStopDriver={noop}
        failsafe={{ enabled: true, thresholdPct: 20 }}
        onFailsafeChange={noop}
        failsafeTriggered={false}
        voice={{ volume: 1, muted: false }}
        onVolume={noop}
        onToggleMute={noop}
      />
    )
  );
}

describe('TopBar', () => {
  it('holds the wordmark, the tabs at the centre and the tray in order', () => {
    render();
    expect(host.querySelector('[data-logo-tilt] img[alt="Tech for Humans"]')).not.toBeNull();
    expect(host.querySelector('[role="tablist"]')).not.toBeNull();
    const items = Array.from(host.querySelectorAll('[data-tray] [data-qs-cluster] [data-qs-item]')).map((item) =>
      item.getAttribute('data-qs-item')
    );
    expect(items).toEqual(['ros2', 'wifi', 'volume', 'battery']);
    expect(host.querySelector('[data-qs-item="battery"]')?.textContent).toContain('78%');
  });

  it('draws the wordmark at least 28 px tall', () => {
    render();
    const logo = host.querySelector<HTMLImageElement>('img[alt="Tech for Humans"]')!;
    expect(Number.parseFloat(logo.style.height)).toBeGreaterThanOrEqual(28);
  });

  it('freezes the tabs while the launch sequence runs', () => {
    render(true);
    const tabs = Array.from(host.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
    expect(tabs.every((tab) => tab.disabled)).toBe(true);
  });
});
