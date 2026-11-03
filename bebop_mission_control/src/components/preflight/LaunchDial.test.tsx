// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LaunchView } from '../../lib/launchSequence';
import { LaunchDial } from './LaunchDial';

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
  vi.useFakeTimers();
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
});

type Props = React.ComponentProps<typeof LaunchDial>;

function render(overrides: Partial<Props> = {}) {
  const props: Props = {
    view: null,
    ready: true,
    blockedCode: null,
    blockedShort: null,
    blockedReason: null,
    errorShort: null,
    benchMode: false,
    onLaunch: vi.fn(),
    ...overrides,
  };
  act(() => root.render(<LaunchDial {...props} />));
  return props;
}

const dial = () => host.querySelector<HTMLButtonElement>('button[data-launch-dial]')!;
const ring = () => host.querySelector('[data-progress]')!;
const view = (overrides: Partial<LaunchView>): LaunchView => ({
  phase: 'loading',
  percent: 0,
  stepId: 'params',
  label: 'Carregando ajustes',
  message: null,
  ...overrides,
});

describe('LaunchDial', () => {
  it('ready and idle shows only "Iniciar" in both flight and Teste modes', () => {
    render();
    expect(dial().textContent).toBe('Iniciar');
    expect(dial().getAttribute('data-theme')).toBe('flight');
    render({ benchMode: true });
    expect(dial().textContent).toBe('Iniciar');
    expect(dial().getAttribute('data-theme')).toBe('bench');
    expect(host.textContent).not.toMatch(/VOO REAL|Motores desligados/);
    expect(host.querySelector('svg.lucide-play')).toBeNull();
  });

  it('blocked shows the short reason in white inside the dial and the long reason in aria-label without a hover title', () => {
    render({
      ready: false,
      blockedCode: 'link',
      blockedShort: 'Sem enlace',
      blockedReason: 'Conecte-se à rede da aeronave para liberar o comando',
    });
    expect(dial().textContent).toBe('Sem enlace');
    expect(dial().title).toBe('');
    expect(dial().getAttribute('aria-label')).toContain('Conecte-se à rede da aeronave');
    expect(dial().disabled).toBe(true);
    const label = host.querySelector('[data-dial-label]')!;
    expect(label.className).toContain('text-frost');
    expect(label.className).not.toContain('text-amber');
  });

  it('launchError shows "Falha ao iniciar" in white in the dial and the long text only in aria-label without a hover title', () => {
    render({ errorShort: 'Falha ao iniciar', blockedReason: 'A missão não iniciou: o processo não subiu' });
    expect(dial().textContent).toBe('Falha ao iniciar');
    expect(dial().title).toBe('');
    expect(dial().getAttribute('aria-label')).toContain('A missão não iniciou: o processo não subiu');
    expect(host.textContent).not.toContain('o processo não subiu');
    const label = host.querySelector('[data-dial-label]')!;
    expect(label.className).toContain('text-frost');
    expect(label.className).not.toContain('text-amber');
  });

  it('keeps one continuous orbital carrier without DOM cuts as 2 beams at 180deg converge and merge into 1 beam at ready', () => {
    render({
      ready: false,
      blockedCode: 'driver',
      blockedShort: 'Sem driver',
      blockedReason: 'Driver ROS 2 fora do ar',
    });
    const carrierBefore = host.querySelector('[data-orbit-carrier]')!;
    expect(dial().getAttribute('data-dial-state')).toBe('no-driver');
    expect(host.querySelector('[data-perimeter-light="no-driver"]')!.getAttribute('data-beam-gap')).toBe('180');
    expect(host.querySelector<HTMLElement>('[data-beam-gap-carrier]')!.style.transform).toBe('rotate(180deg)');

    render({
      ready: false,
      blockedCode: 'link',
      blockedShort: 'Sem enlace',
      blockedReason: 'Conecte-se à rede da aeronave para liberar o comando',
    });
    expect(host.querySelector('[data-orbit-carrier]')).toBe(carrierBefore);
    expect(dial().getAttribute('data-dial-state')).toBe('blocked');
    expect(host.querySelector('[data-perimeter-light="blocked"]')!.getAttribute('data-beam-gap')).toBe('105');
    expect(host.querySelector<HTMLElement>('[data-beam-gap-carrier]')!.style.transform).toBe('rotate(105deg)');

    render({
      ready: false,
      blockedCode: 'topics',
      blockedShort: 'Sem tópicos',
      blockedReason: 'Tópicos sem tráfego: /bebop/odom',
    });
    expect(host.querySelector('[data-orbit-carrier]')).toBe(carrierBefore);
    expect(host.querySelector('[data-perimeter-light="blocked"]')!.getAttribute('data-beam-gap')).toBe('70');
    expect(host.querySelector<HTMLElement>('[data-beam-gap-carrier]')!.style.transform).toBe('rotate(70deg)');
    expect(host.querySelector('[data-beam-secondary]')!.className).toContain('dial-beam-converging');

    render({ ready: true });
    expect(host.querySelector('[data-orbit-carrier]')).toBe(carrierBefore);
    expect(dial().getAttribute('data-dial-state')).toBe('ready');
    expect(host.querySelector('[data-perimeter-light="ready"]')!.getAttribute('data-beam-gap')).toBe('0');
    expect(host.querySelector<HTMLElement>('[data-beam-gap-carrier]')!.style.transform).toBe('rotate(0deg)');
    expect(host.querySelector('[data-ready-halo]')!.className).toContain('opacity-100');
  });

  it('with a view it shows a large percent, a legible step label, and no inner radar or scan overlays', () => {
    render({ view: view({ percent: 37, stepId: 'imu', label: 'Nivelando sensores' }) });
    const pct = host.querySelector('[data-percent]')!;
    expect(pct.textContent).toBe('37%');
    expect(pct.className).toContain('text-5xl');
    const stepLabel = host.querySelector('[data-step-label]:not([data-leaving])')!;
    expect(stepLabel.textContent).toBe('Nivelando sensores');
    expect(stepLabel.className).toContain('text-base');
    expect(stepLabel.className).toContain('t-texts-reveal');
    expect(stepLabel.querySelectorAll('.t-reveal-item').length).toBe('Nivelando sensores'.length);
    expect(host.querySelector('[data-dial-circle-fill]')).toBeNull();
    expect(host.querySelector('.dial-fill-sector')).toBeNull();
    expect(host.querySelector('.dial-fill-horizon')).toBeNull();
  });

  it('at announcing it shows "Tudo Pronto!" and 90 or more', () => {
    render({ view: view({ phase: 'announcing', percent: 92, stepId: null, label: null, message: 'Tudo Pronto!' }) });
    expect(host.querySelector('[data-step-label]:not([data-leaving])')!.textContent).toBe('Tudo Pronto!');
    expect(Number(ring().getAttribute('data-progress'))).toBeGreaterThanOrEqual(90);
  });

  it('smoothly transitions into a proportional perimeter light during launch progression', () => {
    render({ view: view({ percent: 45, stepId: 'imu', label: 'Nivelando sensores' }) });
    expect(dial().getAttribute('data-dial-state')).toBe('running');
    const fillLight = host.querySelector<HTMLElement>('[data-dial-fill-light]')!;
    expect(fillLight.getAttribute('data-fill-progress')).toBe('45');
    expect(fillLight.className).toContain('opacity-100');
    expect(host.querySelector('[data-dial-perimeter-fill]')).not.toBeNull();
  });

  it('the ring offset follows the percent', () => {
    render({ view: view({ percent: 25 }) });
    expect(ring().getAttribute('data-progress')).toBe('25');
    const circle = host.querySelector<SVGCircleElement>('circle[data-progress-arc]')!;
    const length = Number(circle.getAttribute('stroke-dasharray'));
    expect(Number(circle.getAttribute('stroke-dashoffset'))).toBeCloseTo(length * 0.75, 3);
    render();
    expect(ring().getAttribute('data-progress')).toBe('0');
  });

  it('step label changes with text-states-swap enter and leave animation', () => {
    render({ view: view({ stepId: 'params', label: 'Carregando ajustes' }) });
    render({ view: view({ stepId: 'link', label: 'Conectando ao drone', percent: 10 }) });
    const leaving = host.querySelector('[data-step-label][data-leaving]');
    expect(leaving?.textContent).toBe('Carregando ajustes');
    expect(leaving?.className).toContain('anim-step-out');
    expect(leaving?.className).toContain('t-text-swap-out');
    const entering = host.querySelector('[data-step-label]:not([data-leaving])')!;
    expect(entering.className).toContain('anim-step-in');
    expect(entering.className).toContain('t-text-swap-in');
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(host.querySelector('[data-step-label][data-leaving]')).toBeNull();
  });

  it('announces the step, never the percent, to assistive technology', () => {
    render({ view: view({ percent: 37, stepId: 'imu', label: 'Nivelando sensores' }) });
    const live = host.querySelector('[aria-live="polite"]')!;
    expect(live.textContent).toBe('Nivelando sensores');
  });

  it('is not clickable while a view is active', () => {
    const props = render({ view: view({ percent: 10 }) });
    act(() => dial().click());
    expect(props.onLaunch).not.toHaveBeenCalled();
  });

  it('a double activation within 1 s launches once', () => {
    const props = render();
    act(() => {
      dial().click();
      dial().click();
    });
    expect(props.onLaunch).toHaveBeenCalledTimes(1);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    act(() => dial().click());
    expect(props.onLaunch).toHaveBeenCalledTimes(2);
  });

  it('bench theme keeps the cyan ring', () => {
    render({ benchMode: true, view: view({ percent: 40 }) });
    expect(host.querySelector('circle[data-progress-arc]')!.getAttribute('stroke')).toBe('#00E5FF');
    render({ benchMode: false, view: view({ percent: 40 }) });
    expect(host.querySelector('circle[data-progress-arc]')!.getAttribute('stroke')).toBe('#01D5A3');
  });

  it('renders the beneath slot after the dial', () => {
    act(() =>
      root.render(
        <LaunchDial
          view={null}
          ready
          blockedShort={null}
          blockedReason={null}
          errorShort={null}
          benchMode={false}
          onLaunch={() => undefined}
          beneath={<span data-slot>x</span>}
        />
      )
    );
    expect(dial().compareDocumentPosition(host.querySelector('[data-slot]')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
