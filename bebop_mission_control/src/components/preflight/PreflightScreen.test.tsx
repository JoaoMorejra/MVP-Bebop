// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PreflightScreen } from './PreflightScreen';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

vi.mock('../brand/VideoBackdrop', () => ({ VideoBackdrop: () => null }));
vi.mock('../shell/StatusBar', () => ({ StatusBar: () => null }));
vi.mock('./ConnectionSheet', () => ({ ConnectionSheet: () => null }));
vi.mock('./ParameterSheet', () => ({ ParameterSheet: () => null }));

type Props = React.ComponentProps<typeof PreflightScreen>;

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

function render(overrides: Partial<Props>): void {
  const noop = () => undefined;
  const props = {
    telemetry: { connected: false, battery_known: false, battery_pct: 0 },
    stale: false,
    networks: [],
    currentSsid: '',
    scanning: false,
    linkPhase: 'idle',
    linkMessage: null,
    linkProgress: [],
    readiness: null,
    flightReady: false,
    driverRunning: true,
    onScan: noop,
    onConnect: noop,
    onStartDriver: noop,
    onStopDriver: noop,
    params: { no_fly: true, mission_mode: 'simple' },
    paramDefaults: null,
    paramsStatus: 'ready',
    paramsError: null,
    changedPaths: new Set<string>(),
    dirty: false,
    presets: [],
    activePresetId: null,
    onEdit: noop,
    onModeChange: noop,
    onSave: noop,
    onDiscard: noop,
    onSavePreset: () => null,
    onRenamePreset: () => null,
    onDeletePreset: noop,
    onApplyPreset: noop,
    failsafe: { enabled: true, thresholdPct: 20 },
    onFailsafeChange: noop,
    failsafeTriggered: false,
    voice: { volume: 1, muted: false },
    onVolume: noop,
    onToggleMute: noop,
    launchError: null,
    onLaunch: noop,
    onOpenDiagnostics: noop,
    ...overrides,
  } as unknown as Props;
  act(() => root.render(<PreflightScreen {...props} />));
}

const radios = () => Array.from(host.querySelectorAll<HTMLElement>('[role="radio"]'));
const dial = () => host.querySelector<HTMLButtonElement>('button[aria-label^="Iniciar"]')!;

describe('PreflightScreen mission mode selector', () => {
  it('seats the selector above the launch dial, showing the mode of the working document', () => {
    render({});
    const group = host.querySelector('[role="radiogroup"]')!;
    expect(group).not.toBeNull();
    expect(group.compareDocumentPosition(dial()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(radios().map((r) => r.getAttribute('aria-checked'))).toEqual(['true', 'false']);
  });

  it('reads a document without a mode as the full mission', () => {
    render({ params: { no_fly: true } });
    expect(radios()[1].getAttribute('aria-checked')).toBe('true');
  });

  it('hands the picked mode to onModeChange', () => {
    const onModeChange = vi.fn();
    render({ onModeChange });
    act(() => radios()[1].click());
    expect(onModeChange).toHaveBeenCalledWith('advanced');
  });

  it('locks the selector while modeLocked holds', () => {
    const onModeChange = vi.fn();
    render({ onModeChange, modeLocked: true });
    expect(radios().every((r) => r.getAttribute('aria-disabled') === 'true')).toBe(true);
    act(() => radios()[0].click());
    expect(onModeChange).not.toHaveBeenCalled();
  });
});
