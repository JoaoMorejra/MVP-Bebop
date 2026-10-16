// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { TrackPoint } from '../../types/mission';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

vi.mock('../../hooks/useReverseGeocode', () => ({ useReverseGeocode: () => ({ city: null }) }));
const operatorLocation = vi.fn();
vi.mock('../../hooks/useOperatorLocation', () => ({ useOperatorLocation: operatorLocation }));

const { TacticalMap } = await import('./TacticalMap');

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    disconnect() {}
  };
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const point = (x: number, y: number): TrackPoint => ({ x, y, alt: 1, at: 0 });

function render(track: TrackPoint[]) {
  act(() =>
    root.render(<TacticalMap track={track} heading={0} arrivalRadius={0.2} stale={false} flyingState={null} />)
  );
}

const baseLabel = () =>
  Array.from(container.querySelectorAll('text')).find((t) => t.textContent === 'BASE · DECOLAGEM (0, 0)')!;
const tilePositions = () =>
  Array.from(container.querySelectorAll('img')).map((img) => `${img.style.left}|${img.style.top}|${img.src}`);
const scaleLabel = () =>
  Array.from(container.querySelectorAll('svg text'))
    .map((t) => t.textContent ?? '')
    .find((text) => /^\d+ (m|cm)$/.test(text));
const zoomInDisabled = () => (container.querySelector('[aria-label="Aproximar o mapa"]') as HTMLButtonElement).disabled;

describe('TacticalMap anchored at the site', () => {
  it('keeps the basemap, the scale and the base label still while the aircraft flies away', () => {
    render([point(0, 0)]);
    const before = { tiles: tilePositions(), x: baseLabel().getAttribute('x'), y: baseLabel().getAttribute('y') };
    const scaleBefore = scaleLabel();
    expect(scaleBefore).toBeDefined();
    expect(before.tiles.length).toBeGreaterThan(0);

    render([point(0, 0), point(40, 25), point(180, -120)]);
    expect(tilePositions()).toEqual(before.tiles);
    expect(baseLabel().getAttribute('x')).toBe(before.x);
    expect(baseLabel().getAttribute('y')).toBe(before.y);
    expect(scaleLabel()).toBe(scaleBefore);
    expect(zoomInDisabled()).toBe(false);
  });

  it('draws the aircraft at the base at rest and away from it in flight', () => {
    render([point(0, 0)]);
    const marker = () => container.querySelector('[data-aircraft]')!.getAttribute('transform');
    const label = baseLabel();
    const atRest = marker();
    expect(atRest).toBe(`translate(${label.getAttribute('x')}, ${Number(label.getAttribute('y')) - 27})`);
    render([point(0, 0), point(30, 0)]);
    expect(marker()).not.toBe(atRest);
  });

  it('prints no metric position over the aircraft', () => {
    render([point(0, 0), point(12.3, 4.5)]);
    expect(container.textContent).not.toMatch(/E -?\d+\.\d · N -?\d+\.\d m/);
  });

  it('reads the fixed base and never the operator position', () => {
    render([point(0, 0)]);
    expect(operatorLocation).not.toHaveBeenCalled();
    expect(container.textContent).toContain('BASE FIXA');
    expect(container.textContent).toContain('-23.648891, -46.718712');
    expect(container.textContent).toContain('Transamerica Expo Center');
    expect(container.querySelector('[data-coarse-base]')).toBeNull();
  });
});
