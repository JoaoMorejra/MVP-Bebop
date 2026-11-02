import React, { useEffect, useRef, useState } from 'react';
import { cn } from '../../lib/format';
import { prefersReducedMotion } from '../shell/motion';

/**
 * Pace of the grid against the flight it follows: how fast the light runs over
 * the cells, a cell opens, and the grid catches up with the flight. Set to 4x
 * the previous tempo so the grid animation starts right from takeoff and builds
 * proportion rapidly until the capture arrives.
 */
export const GRID_TEMPO = 12;

/** How long the reticle takes the shot -- focus, the bounce, the shutter -- before the grid takes the photograph, ms. */
export const SHOT_MS = 560;
/** How long the reticle takes to leave the grid after the shutter, ms. */
export const RETICLE_EXIT_MS = 300;
/** How long the photograph takes to come over the finished mosaic, ms. */
export const PHOTO_IN_MS = 500;

/** Time constant the grid follows the flight with, ms: quick, never a jump. */
const FOLLOW_TAU_MS = 1050 / GRID_TEMPO;
/** The shorter one it closes on when resolving the capture mosaic into the photograph. */
const CLOSE_TAU_MS = 420 / GRID_TEMPO;
/** How long a new cell takes to open out of its parent, ms. */
const SPLIT_IN_MS = 840 / GRID_TEMPO;
/** Period of the light running across the waiting cells, ms (scaled by 2 pi). */
const SHIMMER_MS = 1950 / GRID_TEMPO;
/** How long the cells take to start leaving their tone for the photograph's colours, ms. */
const TONE_IN_MS = 1080 / GRID_TEMPO;
/** Time constant for autonomous progressive grid growth from the start of the flight, ms. */
const FLIGHT_GROWTH_TAU_MS = 32000 / (GRID_TEMPO / 3);
/** Cells while waiting: 4 at the start of the flight, doubling six times by the capture. */
const WAIT_DOUBLINGS = 6;
/** Smallest cell the reveal goes down to, px^2. */
const MIN_CELL_AREA_PX = 30;
/** Gap between cells, px. */
const GAP_PX = 2;
/** The optical centre, as the reticle sits on it: fractions of the frame. */
const CENTRE = { x: 0.5, y: 0.46 } as const;
/** The cell tone at rest, and the light it takes on: the panel's hull and the station's mint. */
const BASE_RGB = [3, 33, 54] as const;
const MINT_RGB = [18, 179, 130] as const;
/** Letterbox colour around the photograph, as the frame shows it. */
const FIELD_RGB = 'rgb(0,19,31)';
/** Columns of the offscreen copy the cell colours are averaged from. */
const SAMPLE_COLUMNS = 192;

const clamp01 = (value: number): number => (Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0);
const easeOut = (t: number): number => 1 - Math.pow(1 - clamp01(t), 3);

/** A deterministic 0..1 from a number, so each cell keeps its own phase. */
function hash(n: number): number {
  const s = Math.sin(n * 127.1) * 43758.5453;
  return s - Math.floor(s);
}

/** One cell, in fractions of the frame, so a resize rescales it instead of rebuilding the grid. */
interface Cell {
  x: number;
  y: number;
  w: number;
  h: number;
  born: number;
  seed: number;
  /** Tone of its parent when it was split off, so it opens out of it. */
  from: number;
  /** The photograph under it, once there is one. */
  rgb: [number, number, number] | null;
  /** How busy the photograph is under it: busy cells split first. */
  detail: number;
  /** Distance from the optical centre, as a fraction of the diagonal: the colours spread from the reticle. */
  reach: number;
}

/** Averages of the photograph over any rectangle of the frame, from summed-area tables. */
type Sampler = (x: number, y: number, w: number, h: number) => { rgb: [number, number, number]; detail: number };

/**
 * Draw `image` as `object-contain` into a frame of aspect `aspect` and build
 * summed-area tables of it, so the average colour and the busyness of any cell
 * is four lookups.
 */
function buildSampler(image: HTMLImageElement, aspect: number): Sampler | null {
  if (!(image.naturalWidth > 0 && image.naturalHeight > 0 && aspect > 0)) return null;
  const sw = SAMPLE_COLUMNS;
  const sh = Math.max(1, Math.round(SAMPLE_COLUMNS / aspect));
  const canvas = document.createElement('canvas');
  canvas.width = sw;
  canvas.height = sh;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  if (!ctx) return null;
  ctx.fillStyle = FIELD_RGB;
  ctx.fillRect(0, 0, sw, sh);
  const k = Math.min(sw / image.naturalWidth, sh / image.naturalHeight);
  const dw = image.naturalWidth * k;
  const dh = image.naturalHeight * k;
  ctx.drawImage(image, (sw - dw) / 2, (sh - dh) / 2, dw, dh);
  let data: Uint8ClampedArray;
  try {
    data = ctx.getImageData(0, 0, sw, sh).data;
  } catch {
    return null;
  }
  const stride = sw + 1;
  const tables = [0, 1, 2, 3].map(() => new Float64Array(stride * (sh + 1)));
  for (let y = 0; y < sh; y += 1) {
    const row = [0, 0, 0, 0];
    for (let x = 0; x < sw; x += 1) {
      const i = (y * sw + x) * 4;
      const luma = 0.3 * data[i] + 0.59 * data[i + 1] + 0.11 * data[i + 2];
      row[0] += data[i];
      row[1] += data[i + 1];
      row[2] += data[i + 2];
      row[3] += luma * luma;
      for (let c = 0; c < 4; c += 1) tables[c][(y + 1) * stride + x + 1] = tables[c][y * stride + x + 1] + row[c];
    }
  }
  const sum = (c: number, x0: number, y0: number, x1: number, y1: number) =>
    tables[c][y1 * stride + x1] - tables[c][y0 * stride + x1] - tables[c][y1 * stride + x0] + tables[c][y0 * stride + x0];
  return (x, y, w, h) => {
    const x0 = Math.min(sw - 1, Math.max(0, Math.floor(x * sw)));
    const y0 = Math.min(sh - 1, Math.max(0, Math.floor(y * sh)));
    const x1 = Math.max(x0 + 1, Math.min(sw, Math.ceil((x + w) * sw)));
    const y1 = Math.max(y0 + 1, Math.min(sh, Math.ceil((y + h) * sh)));
    const n = (x1 - x0) * (y1 - y0);
    const r = sum(0, x0, y0, x1, y1) / n;
    const g = sum(1, x0, y0, x1, y1) / n;
    const b = sum(2, x0, y0, x1, y1) / n;
    const luma = 0.3 * r + 0.59 * g + 0.11 * b;
    return { rgb: [r, g, b], detail: Math.sqrt(Math.max(0, sum(3, x0, y0, x1, y1) / n - luma * luma)) };
  };
}

/** A max-heap of cells by split priority: the biggest cell, weighted by how busy it is once the photograph is in. */
class SplitQueue {
  private heap: Array<{ cell: Cell; score: number }> = [];

  get size(): number {
    return this.heap.length;
  }

  push(cell: Cell, score: number): void {
    const heap = this.heap;
    heap.push({ cell, score });
    let i = heap.length - 1;
    while (i > 0) {
      const parent = (i - 1) >> 1;
      if (heap[parent].score >= heap[i].score) break;
      [heap[parent], heap[i]] = [heap[i], heap[parent]];
      i = parent;
    }
  }

  pop(): Cell | null {
    const heap = this.heap;
    if (heap.length === 0) return null;
    const top = heap[0].cell;
    const last = heap.pop()!;
    if (heap.length > 0) {
      heap[0] = last;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1;
        const r = l + 1;
        let m = i;
        if (l < heap.length && heap[l].score > heap[m].score) m = l;
        if (r < heap.length && heap[r].score > heap[m].score) m = r;
        if (m === i) break;
        [heap[m], heap[i]] = [heap[i], heap[m]];
        i = m;
      }
    }
    return top;
  }

  clear(): void {
    this.heap = [];
  }
}

/** The reticle of the flight feed (`OpticalFeed`), at 112 px: ring, four arms, the pip. */
const Reticle: React.FC = () => (
  <svg className="evgrid-reticle" viewBox="0 0 112 112" aria-hidden>
    <circle className="ring" cx="56" cy="56" r="34" />
    <line className="arm n" x1="56" y1="6" x2="56" y2="34" />
    <line className="arm s" x1="56" y1="78" x2="56" y2="106" />
    <line className="arm w" x1="6" y1="56" x2="34" y2="56" />
    <line className="arm e" x1="78" y1="56" x2="106" y2="56" />
    <circle className="pip" cx="56" cy="56" r="2.2" />
  </svg>
);

interface EvidenceGridProps {
  /** The flight's progress toward the capture, 0 to 1: how far the waiting grid has split. */
  waitProgress: number;
  /** The capture, once there is one. `key` names it; a new key is a new capture. */
  capture: { key: string; src: string } | null;
  /** The flight's progress from the capture to touchdown, 0 to 1: how much of the photograph the grid has become. */
  reveal: number;
  /** The aircraft is down: the grid closes on the whole photograph, however far it had got. */
  closing: boolean;
  /** The aircraft is over the target: the caption says so. */
  inspecting: boolean;
  /** Called once the photograph is whole on screen. */
  onRevealed: () => void;
}

/**
 * The evidence as it comes in, from the first second of the flight to touchdown.
 *
 * Waiting, the frame is a grid that starts as four cells and splits its biggest
 * cell in two as the flight nears the capture, a light running across it, with
 * the reticle of the flight feed over it. On the capture the reticle takes the
 * shot as a phone camera does -- focus, the bounce, the shutter, silent -- and
 * the cells take on the photograph's colours from the reticle outward, then
 * keep splitting, busiest regions first, until at touchdown they are the
 * picture and the photograph itself comes over them.
 *
 * It never jumps. Every target -- how far the flight is, how much of the
 * picture to show -- is followed with a time constant, so a leap in the
 * flight's progress plays as a quicker transition, and an aircraft down before
 * the picture is whole closes on it fast but continuously. A capture already
 * there when the panel mounts (back from another tab) is not taken again.
 * Under `prefers-reduced-motion` it holds still frames.
 */
export const EvidenceGrid: React.FC<EvidenceGridProps> = ({ waitProgress, capture, reveal, closing, inspecting, onRevealed }) => {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const props = useRef({ waitProgress, capture, reveal, closing, onRevealed });
  props.current = { waitProgress, capture, reveal, closing, onRevealed };
  const firstKey = useRef(capture?.key ?? null);
  const [shot, setShot] = useState<{ key: string; at: number } | null>(null);
  const [whole, setWhole] = useState<string | null>(null);
  /** No canvas to draw on (a host without one): the grid is skipped and the photograph is whole on touchdown. */
  const [noCanvas, setNoCanvas] = useState(false);
  /** The capture whose reticle has finished taking it and left the grid. */
  const [reticleGone, setReticleGone] = useState<string | null>(null);

  const captureKey = capture?.key ?? null;
  useEffect(() => {
    if (captureKey === null) {
      setShot(null);
      setWhole(null);
      return;
    }
    // A capture that was there at mount is shown as it stands, without the shot.
    const instant = captureKey === firstKey.current || prefersReducedMotion();
    setShot({ key: captureKey, at: instant ? performance.now() - SHOT_MS : performance.now() });
    // The reticle leaves after the shutter, once its exit has played.
    const timer = window.setTimeout(() => setReticleGone(captureKey), instant ? 0 : SHOT_MS + RETICLE_EXIT_MS);
    return () => window.clearTimeout(timer);
  }, [captureKey]);

  const shotRef = useRef(shot);
  shotRef.current = shot;

  useEffect(() => {
    const host = hostRef.current;
    const canvas = canvasRef.current;
    let ctx: CanvasRenderingContext2D | null = null;
    // The unit tests run on jsdom, which has no canvas to draw on.
    if (import.meta.env.MODE !== 'test') {
      try {
        ctx = canvas?.getContext('2d') ?? null;
      } catch {
        ctx = null;
      }
    }
    if (!host || !canvas || !ctx) {
      setNoCanvas(true);
      return;
    }

    let w = 0;
    let h = 0;
    let cells: Cell[] = [];
    const queue = new SplitQueue();
    let sampler: Sampler | null = null;
    let image: HTMLImageElement | null = null;
    let imageKey: string | null = null;
    let waitStart = performance.now();
    let wait = clamp01(props.current.waitProgress);
    let waitAtCapture = wait;
    let shown = 0;
    let doneKey: string | null = null;
    let last = performance.now();
    let frame = 0;
    const reduced = prefersReducedMotion();

    const score = (cell: Cell, mosaic: boolean) => cell.w * cell.h * (mosaic ? 0.35 + cell.detail / 40 : 1);
    const describe = (cell: Cell) => {
      cell.reach = Math.hypot(cell.x + cell.w / 2 - CENTRE.x, (cell.y + cell.h / 2 - CENTRE.y) * (h / Math.max(1, w)));
      if (sampler) {
        const { rgb, detail } = sampler(cell.x, cell.y, cell.w, cell.h);
        cell.rgb = rgb;
        cell.detail = detail;
      }
    };
    const requeue = (mosaic: boolean) => {
      queue.clear();
      for (const cell of cells) queue.push(cell, score(cell, mosaic));
    };
    const reset = (now: number) => {
      waitStart = now;
      cells = [];
      for (const [x, y] of [[0, 0], [0.5, 0], [0, 0.5], [0.5, 0.5]] as const) {
        const cell: Cell = { x, y, w: 0.5, h: 0.5, born: now - SPLIT_IN_MS, seed: x * 3 + y * 7 + 1, from: 0, rgb: null, detail: 0, reach: 0 };
        describe(cell);
        cells.push(cell);
      }
      requeue(false);
    };
    const resize = () => {
      const rect = host.getBoundingClientRect();
      w = rect.width;
      h = rect.height;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.round(w * dpr));
      canvas.height = Math.max(1, Math.round(h * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (image && w > 0 && h > 0) {
        sampler = buildSampler(image, w / h);
        for (const cell of cells) describe(cell);
      }
    };
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(resize) : null;
    observer?.observe(host);
    resize();
    reset(performance.now());

    const tone = (cell: Cell, now: number) => {
      // A light running across the cells on the diagonal, each cell a little out of phase.
      const wave = Math.sin(now / SHIMMER_MS - ((cell.x + cell.y) * Math.max(w, h)) / 220 + hash(cell.seed) * 1.6);
      return 0.05 + 0.07 * Math.pow(0.5 + 0.5 * wave, 2);
    };
    const split = (cell: Cell, now: number) => {
      const from = tone(cell, now);
      const seed = cell.seed * 1.37 + 0.71;
      const wide = cell.w * w >= cell.h * h;
      const halves: Cell[] = wide
        ? [
            { ...cell, w: cell.w / 2, born: now, seed, from },
            { ...cell, x: cell.x + cell.w / 2, w: cell.w / 2, born: now, seed: seed + 0.33, from },
          ]
        : [
            { ...cell, h: cell.h / 2, born: now, seed, from },
            { ...cell, y: cell.y + cell.h / 2, h: cell.h / 2, born: now, seed: seed + 0.33, from },
          ];
      for (const half of halves) describe(half);
      return halves;
    };

    const draw = (now: number) => {
      const dt = Math.min(100, Math.max(0, now - last));
      last = now;
      const { waitProgress: p, capture: cap } = props.current;
      const currentShot = shotRef.current;

      // A new capture: load it; the photograph's colours come in once the shot is taken and it is decoded.
      const key = cap?.key ?? null;
      if (key !== imageKey) {
        imageKey = key;
        image = null;
        sampler = null;
        shown = 0;
        doneKey = null;
        if (cap === null) {
          reset(now);
        } else {
          waitAtCapture = wait;
          const img = new Image();
          img.decoding = 'async';
          img.onload = () => {
            if (imageKey !== cap.key) return;
            image = img;
            sampler = w > 0 && h > 0 ? buildSampler(img, w / h) : null;
            for (const cell of cells) describe(cell);
            requeue(true);
          };
          img.src = cap.src;
        }
      }
      const shotDone = currentShot !== null && currentShot.key === key && now - currentShot.at >= SHOT_MS;
      const mosaic = key !== null && sampler !== null && shotDone;

      // Build proportion progressively right from the start of the flight at 4x tempo,
      // and reveal immediately once the capture is taken without waiting for landing.
      const timeWait = 1 - Math.exp(-Math.max(0, now - waitStart) / FLIGHT_GROWTH_TAU_MS);
      const waitTarget = Math.max(clamp01(p), timeWait);
      wait += (waitTarget - wait) * (reduced ? 1 : 1 - Math.exp(-dt / FOLLOW_TAU_MS));
      const goal = mosaic ? 1 : 0;
      shown += (Math.max(shown, goal) - shown) * (reduced ? 1 : 1 - Math.exp(-dt / CLOSE_TAU_MS));
      if (goal >= 1 && 1 - shown < 0.002) shown = 1;

      const waitCells = 4 * Math.pow(2, (key === null ? wait : waitAtCapture) * WAIT_DOUBLINGS);
      const maxCells = Math.max(waitCells, (w * h) / MIN_CELL_AREA_PX);
      const target = Math.round(mosaic ? waitCells * Math.pow(maxCells / waitCells, Math.pow(shown, 1.7)) : waitCells);
      while (cells.length < target && queue.size > 0) {
        const parent = queue.pop()!;
        const index = cells.indexOf(parent);
        if (index < 0) continue;
        const halves = split(parent, now);
        cells[index] = halves[0];
        cells.push(halves[1]);
        for (const half of halves) queue.push(half, score(half, mosaic));
      }

      ctx.clearRect(0, 0, w, h);
      const toneMix = mosaic ? clamp01(shown / 0.45 + easeOut((now - (currentShot?.at ?? now) - SHOT_MS) / TONE_IN_MS) * 0.15) : 0;
      const gapScale = mosaic ? 1 - easeOut((shown - 0.65) / 0.35) : 1;
      for (const cell of cells) {
        const grow = reduced ? 1 : easeOut((now - cell.born) / SPLIT_IN_MS);
        const t = cell.from + (tone(cell, now) - cell.from) * grow;
        let r = BASE_RGB[0] + (MINT_RGB[0] - BASE_RGB[0]) * t;
        let g = BASE_RGB[1] + (MINT_RGB[1] - BASE_RGB[1]) * t;
        let b = BASE_RGB[2] + (MINT_RGB[2] - BASE_RGB[2]) * t;
        if (toneMix > 0 && cell.rgb) {
          const mix = easeOut(toneMix * 1.5 - cell.reach * 1.6);
          r += (cell.rgb[0] - r) * mix;
          g += (cell.rgb[1] - g) * mix;
          b += (cell.rgb[2] - b) * mix;
        }
        const gap = GAP_PX * gapScale * (0.4 + 0.6 * grow);
        const px = cell.x * w + gap / 2;
        const py = cell.y * h + gap / 2;
        const pw = cell.w * w - gap;
        const ph = cell.h * h - gap;
        if (pw <= 0 || ph <= 0) continue;
        ctx.fillStyle = `rgb(${r | 0},${g | 0},${b | 0})`;
        const radius = Math.min(7, pw / 5, ph / 5) * gapScale;
        if (radius < 0.6 || typeof ctx.roundRect !== 'function') {
          ctx.fillRect(px, py, pw, ph);
        } else {
          ctx.beginPath();
          ctx.roundRect(px, py, pw, ph, radius);
          ctx.fill();
        }
      }

      if (key !== null && doneKey !== key && (shown >= 1 || (shotDone && sampler === null && image === null))) {
        doneKey = key;
        setWhole(key);
      }
      frame = window.requestAnimationFrame(draw);
    };
    frame = window.requestAnimationFrame(draw);
    return () => {
      window.cancelAnimationFrame(frame);
      observer?.disconnect();
    };
  }, []);

  useEffect(() => {
    if (!noCanvas || captureKey === null) return;
    if (closing || captureKey === firstKey.current || prefersReducedMotion()) {
      setWhole(captureKey);
      return;
    }
    const wholeTimer = window.setTimeout(() => setWhole(captureKey), 10);
    const revealTimer = window.setTimeout(() => {
      setWhole(captureKey);
      props.current.onRevealed();
    }, PHOTO_IN_MS);
    return () => {
      window.clearTimeout(wholeTimer);
      window.clearTimeout(revealTimer);
    };
  }, [noCanvas, captureKey, closing]);

  // The photograph comes over the finished mosaic, then the panel is told.
  const revealed = capture !== null && whole === capture.key;
  useEffect(() => {
    if (!revealed) return;
    const timer = window.setTimeout(() => props.current.onRevealed(), prefersReducedMotion() ? 0 : PHOTO_IN_MS);
    return () => window.clearTimeout(timer);
  }, [revealed]);

  const shooting = shot !== null && capture !== null && shot.key === capture.key;
  return (
    <div
      ref={hostRef}
      data-evidence-grid={revealed ? 'revealed' : capture ? 'revealing' : 'waiting'}
      data-inspecting={inspecting || undefined}
      data-shooting={shooting || undefined}
      className="evgrid"
    >
      <canvas ref={canvasRef} aria-hidden className="evgrid-canvas" />
      {capture ? (
        <img
          className={cn('evgrid-photo', revealed && 'evgrid-photo-in')}
          src={capture.src}
          alt="Captura pericial com a câmera na posição vertical"
        />
      ) : null}
      {capture !== null && reticleGone === capture.key ? null : (
        <>
          <Reticle />
          <p className="evgrid-caption">Aguardando evidência</p>
        </>
      )}
    </div>
  );
};
