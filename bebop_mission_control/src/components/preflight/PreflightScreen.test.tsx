// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PreflightScreen } from './PreflightScreen';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

vi.mock('../brand/VideoBackdrop', () => ({ VideoBackdrop: () => null }));
vi.mock('./ParameterSheet', () => ({
  ParameterSheet: (props: { nudgeSave?: number }) => <span data-sheet-probe data-nudge={props.nudgeSave ?? 0} />,
}));

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
    readiness: null,
    flightReady: false,
    driverRunning: true,
    params: { no_fly: true, mission_mode: 'simple' },
    paramDefaults: null,
    paramsStatus: 'ready',
    paramsError: null,
    changedPaths: new Set<string>(),
    dirty: false,
    favoriteActive: false,
    hasFavorite: false,
    onEdit: noop,
    flightMode: 'test',
    onFlightModeChange: noop,
    onSave: noop,
    onDiscard: noop,
    onSaveFavorite: noop,
    onRestoreFavorite: noop,
    batteryThresholdPct: 20,
    batteryFailsafeEnabled: true,
    launchError: null,
    onLaunch: noop,
    ...overrides,
  } as unknown as Props;
  act(() => root.render(<PreflightScreen {...props} />));
}

const trigger = () => host.querySelector<HTMLButtonElement>('[aria-haspopup="listbox"]')!;
const options = () => Array.from(host.querySelectorAll<HTMLElement>('[role="option"]'));
const paramsButton = () =>
  Array.from(host.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent === 'Parâmetros')!;
const dial = () => host.querySelector<HTMLButtonElement>('button[aria-label^="Iniciar"]')!;

describe('PreflightScreen flight mode selector', () => {
  it('seats the selector beside Parâmetros beneath the launch dial, showing the flight mode', () => {
    render({ flightMode: 'simple', params: { no_fly: false, mission_mode: 'simple' } });
    expect(dial().compareDocumentPosition(trigger()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(paramsButton().compareDocumentPosition(trigger()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(trigger().textContent).toBe('Simples');
  });

  it('has no Bancada switch', () => {
    render({});
    expect(host.querySelector('[role="switch"]')).toBeNull();
    expect(host.textContent).not.toContain('Bancada');
  });

  it('hands the picked mode to onFlightModeChange', () => {
    const onFlightModeChange = vi.fn();
    render({ onFlightModeChange });
    act(() => trigger().click());
    act(() => options()[1].click());
    expect(onFlightModeChange).toHaveBeenCalledWith('advanced');
  });

  it('locks the selector while modeLocked holds', () => {
    const onFlightModeChange = vi.fn();
    render({ onFlightModeChange, modeLocked: true });
    expect(trigger().getAttribute('aria-disabled')).toBe('true');
    act(() => trigger().click());
    expect(options()).toEqual([]);
    expect(onFlightModeChange).not.toHaveBeenCalled();
  });

  it('disables Parâmetros in Teste, without a hover title', () => {
    render({ flightMode: 'test' });
    expect(paramsButton().disabled).toBe(true);
    expect(paramsButton().title).toBe('');
  });

  it('enables Parâmetros in a flight mode, without a title', () => {
    render({ flightMode: 'advanced', params: { no_fly: false, mission_mode: 'advanced' } });
    expect(paramsButton().disabled).toBe(false);
    expect(paramsButton().title).toBe('');
  });
});

describe('PreflightScreen parameters modal and Escape', () => {
  const flying = { flightMode: 'advanced' as const, params: { no_fly: false, mission_mode: 'advanced' } };
  const sheetOpen = () => host.querySelector('[role="dialog"][aria-label="Parâmetros"]') !== null;
  const discardOpen = () => document.querySelector('[aria-labelledby="discard-changes-title"]') !== null;
  const escape = () =>
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
  const openSheet = () => act(() => paramsButton().click());

  it('Escape closes the sheet directly when nothing changed', () => {
    render({ ...flying, dirty: false });
    openSheet();
    expect(sheetOpen()).toBe(true);
    escape();
    expect(sheetOpen()).toBe(false);
    expect(discardOpen()).toBe(false);
  });

  it('leaving with unsaved edits shakes Salvar and keeps the sheet open, with no dialog', () => {
    const onDiscard = vi.fn();
    render({ ...flying, dirty: true, onDiscard });
    openSheet();
    escape();
    expect(sheetOpen()).toBe(true);
    expect(discardOpen()).toBe(false);
    expect(host.querySelector('[data-sheet-probe]')!.getAttribute('data-nudge')).toBe('1');
    expect(onDiscard).not.toHaveBeenCalled();
  });

  it('a second attempt to leave discards the edits and closes the sheet', () => {
    const onDiscard = vi.fn();
    render({ ...flying, dirty: true, onDiscard });
    openSheet();
    escape();
    escape();
    expect(onDiscard).toHaveBeenCalledTimes(1);
    expect(sheetOpen()).toBe(false);
  });

  it('the close button follows the same two steps', () => {
    const onDiscard = vi.fn();
    render({ ...flying, dirty: true, onDiscard });
    openSheet();
    const close = () => act(() => Array.from(host.querySelectorAll<HTMLButtonElement>('button[aria-label="Fechar"]')).pop()!.click());
    close();
    expect(sheetOpen()).toBe(true);
    close();
    expect(onDiscard).toHaveBeenCalledTimes(1);
    expect(sheetOpen()).toBe(false);
  });
});

describe('PreflightScreen without its own header', () => {
  it('holds no status bar, no link sheet and no diagnostics button', () => {
    render({});
    expect(host.querySelector('header')).toBeNull();
    expect(host.querySelector('[aria-label="Bateria da aeronave"]')).toBeNull();
    expect(host.querySelector('[aria-label="Redes Wi-Fi"]')).toBeNull();
    expect(host.textContent).not.toContain('Detalhes do enlace');
  });
});
