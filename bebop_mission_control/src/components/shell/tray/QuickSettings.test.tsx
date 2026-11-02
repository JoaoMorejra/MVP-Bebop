// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { TelemetryView } from '../../../types/mission';
import type { LinkReadiness, TopicReadiness, WifiNetwork } from '../../../types/bmg';
import { QuickSettings, ROS2_CONFIRM_MS, type QuickSettingsProps } from './QuickSettings';

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
  vi.useRealTimers();
});

const TOPICS = [
  '/bebop/odom',
  '/bebop/camera/image_raw',
  '/bebop/camera/camera_info',
  '/bebop/states/battery',
  '/bebop/cmd_vel',
  '/bebop/takeoff',
  '/bebop/land',
  '/bebop/move_camera',
];

const topic = (name: string, receiving: boolean): TopicReadiness => ({
  topic: name,
  direction: 'in',
  present: true,
  receiving,
  publishers: 1,
  subscribers: 0,
  ageSec: null,
});

const readiness = (flowing: number) =>
  ({ ready: flowing === TOPICS.length, topics: TOPICS.map((name, i) => topic(name, i < flowing)) } as unknown as LinkReadiness);

const NETWORKS: WifiNetwork[] = [
  { ssid: 'Escritorio', signal: 80, active: true, isBebop: false, secure: true },
  { ssid: 'Bebop2-123456', signal: 60, active: false, isBebop: true, secure: false },
];

function props(overrides: Partial<QuickSettingsProps> = {}): QuickSettingsProps {
  return {
    telemetry: { connected: true, battery_known: true, battery_pct: 78, data_fresh: false } as unknown as TelemetryView,
    networks: NETWORKS,
    currentSsid: 'Escritorio',
    scanning: false,
    busy: false,
    readiness: readiness(8),
    flightReady: true,
    driverRunning: true,
    frozen: false,
    onScan: vi.fn(),
    onConnect: vi.fn(),
    onStartDriver: vi.fn(),
    onStopDriver: vi.fn(),
    failsafe: { enabled: true, thresholdPct: 20 },
    onFailsafeChange: vi.fn(),
    failsafeTriggered: false,
    voice: { volume: 0.8, muted: false },
    onVolume: vi.fn(),
    onToggleMute: vi.fn(),
    ...overrides,
  };
}

const render = (p: QuickSettingsProps) => act(() => root.render(<QuickSettings {...p} />));
const q = <T extends Element = HTMLElement>(selector: string) => host.querySelector<T>(selector as string) as unknown as T;
const click = (el: Element | null) => act(() => (el as HTMLElement).click());
const openMenu = () => click(q('[data-qs-cluster]'));
const range = (label: string) => q<HTMLInputElement>(`input[aria-label="${label}"]`);
const slide = (input: HTMLInputElement, value: number) =>
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
    setter.call(input, String(value));
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });

describe('QuickSettings', () => {
  it('groups ROS 2, Wi-Fi, volume and battery in one pill', () => {
    render(props());
    const items = Array.from(host.querySelectorAll('[data-qs-cluster] [data-qs-item]')).map((i) => i.getAttribute('data-qs-item'));
    expect(items).toEqual(['ros2', 'wifi', 'volume', 'battery']);
    expect(q('[data-qs-panel]')).toBeNull();
  });

  it('opens one menu with the charge, the failsafe switch, the sliders and two tiles', () => {
    render(props());
    openMenu();
    expect(q('[data-qs-charge]').textContent).toContain('78%');
    expect(q('[role="switch"][aria-label="Failsafe de bateria"]')).not.toBeNull();
    expect(range('Volume do copiloto')).not.toBeNull();
    expect(range('Limiar do failsafe de bateria')).not.toBeNull();
    expect(Array.from(host.querySelectorAll('[data-qs-tile]')).map((t) => t.getAttribute('data-qs-tile'))).toEqual(['wifi', 'ros2']);
  });

  it('shows the threshold slider only while the failsafe is on', () => {
    render(props({ failsafe: { enabled: false, thresholdPct: 20 } }));
    openMenu();
    expect(range('Volume do copiloto')).not.toBeNull();
    expect(range('Limiar do failsafe de bateria')).toBeNull();
  });

  it('the switch turns the failsafe on and the threshold slider moves it', () => {
    const p = props({ failsafe: { enabled: false, thresholdPct: 20 } });
    render(p);
    openMenu();
    click(q('[role="switch"]'));
    expect(p.onFailsafeChange).toHaveBeenCalledWith({ enabled: true, thresholdPct: 20 });

    const on = props();
    render(on);
    slide(range('Limiar do failsafe de bateria'), 35);
    expect(on.onFailsafeChange).toHaveBeenCalledWith({ enabled: true, thresholdPct: 35 });
  });

  it('volume at 0 mutes and the icon level follows', () => {
    const p = props();
    render(p);
    openMenu();
    slide(range('Volume do copiloto'), 0);
    expect(p.onVolume).toHaveBeenCalledWith(0);
    expect(p.onToggleMute).toHaveBeenCalledTimes(1);
    render(props({ voice: { volume: 0, muted: true } }));
    expect(q('[data-qs-item="volume"]').getAttribute('data-level')).toBe('0');
  });

  it('draws the battery red at 20 percent and white above it', () => {
    render(props({ telemetry: { connected: true, battery_known: true, battery_pct: 20 } as unknown as TelemetryView }));
    expect(q('[data-qs-item="battery"] [data-alert]')).not.toBeNull();
    render(props({ telemetry: { connected: true, battery_known: true, battery_pct: 21 } as unknown as TelemetryView }));
    expect(q('[data-qs-item="battery"] [data-alert]')).toBeNull();
  });

  it('clicking the main Wi-Fi button toggles Wi-Fi power on/off like Ubuntu', () => {
    const p = props({
      wifiPower: true,
      onToggleWifiPower: vi.fn(),
    });
    render(p);
    openMenu();
    click(q('[aria-label="Desligar Wi-Fi"]'));
    expect(p.onToggleWifiPower).toHaveBeenCalledWith(false);

    const off = props({
      wifiPower: false,
      onToggleWifiPower: vi.fn(),
    });
    render(off);
    click(q('[aria-label="Ligar Wi-Fi"]'));
    expect(off.onToggleWifiPower).toHaveBeenCalledWith(true);
  });

  it('the Wi-Fi arrow opens the network list, rescans, lists the aircraft first, and clicking active network disconnects', () => {
    const p = props({
      onDisconnectWifi: vi.fn(),
    });
    render(p);
    openMenu();
    click(q('[aria-label="Abrir redes Wi-Fi"]'));
    expect(p.onScan).toHaveBeenCalled();
    const rows = Array.from(host.querySelectorAll('[data-qs-submenu="wifi"] [data-network]')).map((r) => r.getAttribute('data-network'));
    expect(rows).toEqual(['Bebop2-123456', 'Escritorio']);
    expect(q('[data-network="Bebop2-123456"][data-bebop="true"]')).not.toBeNull();
    click(q('[data-network="Bebop2-123456"]'));
    expect(p.onConnect).toHaveBeenCalledWith('Bebop2-123456');
    expect(q('[aria-label="Atualizar redes"]')).not.toBeNull();

    // Clicking connected network disconnects it (Ubuntu style)
    click(q('[data-network="Escritorio"]'));
    expect(p.onDisconnectWifi).toHaveBeenCalled();
  });

  it('shows the loading ring and sweeping arcs while the radio searches', () => {
    render(props({ scanning: true }));
    expect(q('[data-qs-item="wifi"] svg[data-seeking]')).not.toBeNull();
    openMenu();
    click(q('[aria-label="Abrir redes Wi-Fi"]'));
    expect(q('[data-qs-submenu="wifi"] [data-qs-spinner]')).not.toBeNull();
    expect(q<HTMLButtonElement>('[aria-label="Atualizar redes"]').disabled).toBe(true);
  });

  it('the ROS 2 arrow lists the eight topics and the stop button', () => {
    const p = props();
    render(p);
    openMenu();
    click(q('[aria-label="Abrir tópicos do ROS 2"]'));
    expect(host.querySelectorAll('[data-qs-submenu="ros2"] [data-topic]')).toHaveLength(8);
    const stop = Array.from(host.querySelectorAll('button')).find((b) => b.textContent === 'Parar')!;
    click(stop);
    expect(p.onStopDriver).toHaveBeenCalled();
  });

  it('scrambles the topics still loading and runs the matrix loader on the mark', () => {
    render(props({ busy: true, flightReady: false, readiness: readiness(3) }));
    expect(q('[data-qs-item="ros2"] svg.ros-matrix')).not.toBeNull();
    openMenu();
    click(q('[aria-label="Abrir tópicos do ROS 2"]'));
    expect(host.querySelectorAll('[data-qs-submenu="ros2"] [data-scrambling]')).toHaveLength(5);
  });

  it('confirms with a tick when the nodes finish coming up', () => {
    vi.useFakeTimers();
    render(props({ busy: true, flightReady: false, readiness: readiness(6) }));
    render(props({ busy: false, flightReady: true, readiness: readiness(8) }));
    expect(q('[data-qs-item="ros2"] .t-success-check')).not.toBeNull();
    act(() => {
      vi.advanceTimersByTime(ROS2_CONFIRM_MS + 10);
    });
    expect(q('[data-qs-item="ros2"] .t-success-check')).toBeNull();
  });

  it('the ROS 2 tile body starts the driver when it is down', () => {
    const p = props({ driverRunning: false, flightReady: false, readiness: readiness(0) });
    render(p);
    openMenu();
    click(q('[aria-label="Iniciar ROS 2"]'));
    expect(p.onStartDriver).toHaveBeenCalled();
  });

  it('freezes everything but the volume during the launch sequence', () => {
    render(props({ frozen: true }));
    openMenu();
    expect(q<HTMLButtonElement>('[role="switch"]').disabled).toBe(true);
    expect(range('Limiar do failsafe de bateria').disabled).toBe(true);
    expect(range('Volume do copiloto').disabled).toBe(false);
    expect(q<HTMLButtonElement>('[aria-label="Parar ROS 2"]').disabled).toBe(true);
  });
});
