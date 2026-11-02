import React, { useEffect, useRef, useState } from 'react';
import ReactDOM from 'react-dom/client';
import './styles/globals.css';

/* ------------------------------------------------------------------ the brand wave (ParticleField), slower */
type RGB = [number, number, number];
const GREEN: RGB[] = [[168, 255, 228], [46, 232, 182], [1, 199, 152], [0, 145, 122], [0, 92, 98], [0, 52, 74]];
const BLUE: RGB[] = [[150, 222, 255], [64, 170, 226], [26, 118, 182], [12, 78, 138], [6, 48, 96], [2, 30, 62]];
function rampAt(ramp: RGB[], t: number): RGB {
  const c = Math.min(0.999, Math.max(0, t)) * (ramp.length - 1);
  const i = Math.floor(c);
  const f = c - i;
  const a = ramp[i];
  const b = ramp[i + 1] ?? a;
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

/* ------------------------------------------------------------------ the drone, as 3-D line art */
type V3 = [number, number, number];
/** Forward is +x, up is +y, right is +z (drone frame). */
const BODY_A = 0.64;
const BODY_B = 0.36;
const ROTOR_R = 0.6;
const HUBS: Array<{ at: V3; dir: 1 | -1 }> = [
  { at: [0.98, 0.1, -1.0], dir: 1 },
  { at: [0.98, 0.1, 1.0], dir: -1 },
  { at: [-0.96, 0.02, -1.02], dir: -1 },
  { at: [-0.96, 0.02, 1.02], dir: 1 },
];

function superellipse(a: number, b: number, y: number, n = 4, steps = 72, dx = 0): V3[] {
  const pts: V3[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const t = (i / steps) * Math.PI * 2;
    const c = Math.cos(t);
    const s = Math.sin(t);
    pts.push([dx + a * Math.sign(c) * Math.pow(Math.abs(c), 2 / n), y, b * Math.sign(s) * Math.pow(Math.abs(s), 2 / n)]);
  }
  return pts;
}
function circleXZ(cx: number, cy: number, cz: number, r: number, steps = 48): V3[] {
  const pts: V3[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const t = (i / steps) * Math.PI * 2;
    pts.push([cx + r * Math.cos(t), cy, cz + r * Math.sin(t)]);
  }
  return pts;
}
function circleYZ(cx: number, cy: number, cz: number, r: number, steps = 36): V3[] {
  const pts: V3[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const t = (i / steps) * Math.PI * 2;
    pts.push([cx, cy + r * Math.cos(t), cz + r * Math.sin(t)]);
  }
  return pts;
}

/** The airframe's fixed lines: shell, arms, motors, gimbal, legs. */
function airframe(): V3[][] {
  const lines: V3[][] = [];
  // Shell: a waisted body, its top deck, the belly, the shoulder line between.
  lines.push(superellipse(BODY_A * 0.86, BODY_B * 0.8, 0.2, 4, 72, -0.02));
  lines.push(superellipse(BODY_A, BODY_B, 0.05));
  lines.push(superellipse(BODY_A * 0.9, BODY_B * 0.86, -0.13, 4, 72, 0.02));
  for (const t of [0, 0.25, 0.5, 0.75, 0.125, 0.375, 0.625, 0.875]) {
    const a = t * Math.PI * 2;
    const c = Math.cos(a);
    const s = Math.sin(a);
    const pe = (k: number) => [Math.sign(c) * Math.pow(Math.abs(c), 0.5) * k, Math.sign(s) * Math.pow(Math.abs(s), 0.5)] as const;
    const [x1, z1] = pe(BODY_A * 0.86);
    const [x2, z2] = pe(BODY_A);
    const [x3, z3] = pe(BODY_A * 0.9);
    lines.push([[x1 - 0.02, 0.2, z1 * BODY_B * 0.8], [x2, 0.05, z2 * BODY_B], [x3 + 0.02, -0.13, z3 * BODY_B * 0.86]]);
  }
  // Deck detail: a battery seam along the top.
  lines.push([[-0.55, 0.2, -0.12], [0.35, 0.2, -0.12]]);
  lines.push([[-0.55, 0.2, 0.12], [0.35, 0.2, 0.12]]);
  // Gimbal and camera under the nose.
  lines.push(circleYZ(BODY_A + 0.12, -0.12, 0, 0.11));
  lines.push(circleYZ(BODY_A + 0.17, -0.12, 0, 0.055));
  lines.push([[BODY_A - 0.05, -0.02, -0.12], [BODY_A + 0.12, -0.02, -0.12], [BODY_A + 0.12, -0.23, -0.12]]);
  lines.push([[BODY_A - 0.05, -0.02, 0.12], [BODY_A + 0.12, -0.02, 0.12], [BODY_A + 0.12, -0.23, 0.12]]);
  // Arms, each a tube of two edges, out to its motor.
  for (const { at } of HUBS) {
    const side = Math.sign(at[2]);
    const root: V3 = [at[0] > 0 ? 0.42 : -0.46, at[0] > 0 ? 0.06 : -0.02, side * 0.3];
    const tip: V3 = [at[0], at[1] - 0.1, at[2]];
    for (const dy of [0.035, -0.035]) lines.push([[root[0], root[1] + dy, root[2]], [tip[0], tip[1] + dy, tip[2]]]);
    // Motor: two rings and its wall.
    lines.push(circleXZ(at[0], at[1] - 0.02, at[2], 0.1, 28));
    lines.push(circleXZ(at[0], at[1] - 0.16, at[2], 0.11, 28));
    for (const a of [0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2]) {
      lines.push([[at[0] + 0.1 * Math.cos(a), at[1] - 0.02, at[2] + 0.1 * Math.sin(a)], [at[0] + 0.11 * Math.cos(a), at[1] - 0.16, at[2] + 0.11 * Math.sin(a)]]);
    }
    // Hub and shaft.
    lines.push([[at[0], at[1] - 0.02, at[2]], [at[0], at[1] + 0.04, at[2]]]);
    // Front legs under the front motors.
    if (at[0] > 0) lines.push([[at[0], at[1] - 0.16, at[2]], [at[0] - 0.05, at[1] - 0.42, at[2] * 1.02]]);
  }
  // Rear legs under the body.
  lines.push([[-0.5, -0.12, -0.22], [-0.55, -0.4, -0.3]]);
  lines.push([[-0.5, -0.12, 0.22], [-0.55, -0.4, 0.3]]);
  return lines;
}
const AIRFRAME = airframe();

/* ------------------------------------------------------------------ the scene */
function Scene() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = canvasRef.current!;
    const ctx = canvas.getContext('2d', { alpha: false })!;
    let w = 0;
    let h = 0;
    let raf = 0;
    const resize = () => {
      const r = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = Math.max(1, Math.floor(r.width));
      h = Math.max(1, Math.floor(r.height));
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    const COLS = 196;
    const ROWS = 104;
    const YAW = (-32 * Math.PI) / 180;
    const PITCH = (22 * Math.PI) / 180;

    const draw = (ms: number) => {
      const t = ms / 1000;
      ctx.fillStyle = '#00131F';
      ctx.fillRect(0, 0, w, h);
      // The key art's diffuse light behind the points: a green crest high in the middle, a blue swell low left.
      const drift = Math.sin(t * 0.05);
      const crestGlow = ctx.createRadialGradient(w * (0.5 + drift * 0.04), h * 0.3, 0, w * 0.5, h * 0.3, w * 0.62);
      crestGlow.addColorStop(0, 'rgba(1,190,140,0.38)');
      crestGlow.addColorStop(0.45, 'rgba(0,120,105,0.18)');
      crestGlow.addColorStop(1, 'rgba(0,19,31,0)');
      ctx.fillStyle = crestGlow;
      ctx.fillRect(0, 0, w, h);
      const swell = ctx.createRadialGradient(w * 0.18, h * 0.62, 0, w * 0.18, h * 0.62, w * 0.5);
      swell.addColorStop(0, 'rgba(20,110,170,0.26)');
      swell.addColorStop(1, 'rgba(0,19,31,0)');
      ctx.fillStyle = swell;
      ctx.fillRect(0, 0, w, h);

      // The drone: centre, scale and its slow hover.
      const scale = Math.min(w * 0.088, h * 0.16);
      const cx = w * 0.785;
      const cy = h * 0.44 + Math.sin(t * 0.85) * h * 0.008;
      const roll = Math.sin(t * 0.5) * 0.022;
      const yaw = YAW + Math.sin(t * 0.21) * 0.035;
      const cyw = Math.cos(yaw);
      const syw = Math.sin(yaw);
      const cp = Math.cos(PITCH);
      const sp = Math.sin(PITCH);
      const cr = Math.cos(roll);
      const sr = Math.sin(roll);
      const project = (p: V3): [number, number, number] => {
        let [x, y, z] = p;
        [y, z] = [y * cr - z * sr, y * sr + z * cr];
        [x, z] = [x * cyw - z * syw, x * syw + z * cyw];
        [y, z] = [y * cp - z * sp, y * sp + z * cp];
        const k = 4.2 / (4.2 + z);
        return [cx + x * k * scale, cy - y * k * scale, z];
      };

      // Where each rotor's wash lands on the sheet below, in screen space.
      const wash = HUBS.map(({ at, dir }) => {
        const [hx, hy] = project(at);
        return { x: hx + (hx - cx) * 0.4, y: hy + h * 0.2, dir };
      });
      const washCx = wash.reduce((s, p) => s + p.x, 0) / wash.length;
      const washCy = wash.reduce((s, p) => s + p.y, 0) / wash.length;

      // The particle sheet, slowed, with the rotor wash pressed into it.
      const horizon = h * 0.08;
      const depth = h - horizon;
      for (let row = 0; row < ROWS; row += 1) {
        const rowT = row / (ROWS - 1);
        const y = horizon + depth * rowT * rowT;
        const sc = 0.28 + rowT * 1.85;
        const rowWidth = w * (1.06 + rowT * 0.78);
        const left = (w - rowWidth) / 2;
        for (let col = 0; col < COLS; col += 1) {
          let colT = col / (COLS - 1);
          let rT = rowT;
          const x = left + rowWidth * colT;
          if (x < -10 || x > w + 10) continue;

          // Rotor swirl: each footprint turns the surface around it, opposite rotors opposite ways.
          let press = 0;
          let glow = 0;
          for (const p of wash) {
            const dx = x - p.x;
            const dy = (y - p.y) * 2.1;
            const d2 = dx * dx + dy * dy;
            const fall = Math.exp(-d2 / (scale * scale * 0.55));
            if (fall < 0.002) continue;
            const ang = p.dir * fall * (0.9 + 0.25 * Math.sin(t * 0.9));
            const ca = Math.cos(ang);
            const sa = Math.sin(ang);
            const ux = colT - p.x / w;
            const uy = rT - (p.y - horizon) / depth;
            colT = p.x / w + ux * ca - uy * sa * 0.5;
            rT = (p.y - horizon) / depth + ux * sa * 2 + uy * ca;
            press += fall;
            glow = Math.max(glow, fall);
          }
          // Downwash: a soft dimple under the airframe, and rings leaving it slowly.
          const ddx = x - washCx;
          const ddy = (y - washCy) * 2.1;
          const dist = Math.sqrt(ddx * ddx + ddy * ddy);
          const ring = Math.sin(dist / (scale * 0.16) - t * 1.15) * Math.exp(-dist / (scale * 1.6));

          const wave =
            Math.sin(colT * 5.4 + rT * 2.9 - t * 0.18) * 0.56 +
            Math.sin(colT * 10.3 - rT * 4.7 + t * 0.11) * 0.29 +
            Math.sin((colT + rT * 1.4) * 7.8 + t * 0.26) * 0.15 +
            ring * 0.32 -
            press * 0.35;
          const lift = wave * 96 * sc * 0.5;
          const py = y - lift;
          if (py < -10 || py > h + 10) continue;
          const crest = Math.min(1, Math.max(0, wave * 0.5 + 0.5));
          const heightT = Math.pow(1 - crest, 1.35);
          const mix = Math.min(1, Math.max(0, (col / (COLS - 1)) * 1.45 - 0.16 + rowT * 0.22 + Math.sin(t * 0.07) * 0.07));
          const g = rampAt(GREEN, heightT);
          const b = rampAt(BLUE, heightT);
          const distance = Math.min(1, 0.26 + rowT * 1.9);
          const alpha = Math.min(1, distance * (0.7 + Math.pow(crest, 1.5) * 0.9 + glow * 0.6));
          ctx.fillStyle = `rgba(${(b[0] + (g[0] - b[0]) * mix) | 0},${(b[1] + (g[1] - b[1]) * mix) | 0},${(b[2] + (g[2] - b[2]) * mix) | 0},${alpha.toFixed(3)})`;
          const size = Math.max(0.9, 1.3 * sc);
          ctx.fillRect(x, py, size, size);
        }
      }

      // The tactical wash the screen keeps for legibility.
      ctx.fillStyle = 'rgba(0,12,22,0.08)';
      ctx.fillRect(0, 0, w, h);
      const v = ctx.createLinearGradient(0, 0, 0, h);
      v.addColorStop(0, 'rgba(0,19,31,0.7)');
      v.addColorStop(0.26, 'rgba(0,19,31,0.06)');
      v.addColorStop(0.62, 'rgba(0,19,31,0.12)');
      v.addColorStop(1, 'rgba(0,19,31,0.7)');
      ctx.fillStyle = v;
      ctx.fillRect(0, 0, w, h);

      // The drone, in white line.
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';
      ctx.shadowColor = 'rgba(255,255,255,0.35)';
      ctx.shadowBlur = 4;
      const stroke = (pts: V3[], alpha: number, width = 1.1) => {
        ctx.beginPath();
        pts.forEach((p, i) => {
          const [sx, sy] = project(p);
          if (i === 0) ctx.moveTo(sx, sy);
          else ctx.lineTo(sx, sy);
        });
        ctx.strokeStyle = `rgba(255,255,255,${alpha})`;
        ctx.lineWidth = width;
        ctx.stroke();
      };
      for (const line of AIRFRAME) stroke(line, 0.82);

      // Rotors: the blur disc, and two blades with their motion trail.
      HUBS.forEach(({ at, dir }, i) => {
        const top = at[1] + 0.05;
        stroke(circleXZ(at[0], top, at[2], ROTOR_R, 64), 0.16, 1);
        stroke(circleXZ(at[0], top, at[2], ROTOR_R * 0.55, 48), 0.06, 1);
        const spin = dir * t * 2.2 * Math.PI * 0.55 + i;
        for (let k = 0; k < 9; k += 1) {
          const a = spin - dir * k * 0.09;
          const alpha = 0.7 * Math.pow(1 - k / 9, 2);
          for (const off of [0, Math.PI]) {
            const ca = Math.cos(a + off);
            const sa = Math.sin(a + off);
            const tip: V3 = [at[0] + ca * ROTOR_R * 0.96, top, at[2] + sa * ROTOR_R * 0.96];
            const mid: V3 = [at[0] + ca * ROTOR_R * 0.45 - sa * 0.05, top, at[2] + sa * ROTOR_R * 0.45 + ca * 0.05];
            stroke([[at[0], top, at[2]], mid, tip], alpha, k === 0 ? 1.4 : 1);
          }
        }
      });
      ctx.shadowBlur = 0;

      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, []);
  return <canvas ref={canvasRef} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} />;
}

function Sim() {
  const [ui, setUi] = useState(true);
  return (
    <div className="bg-abyss font-ui text-frost" style={{ position: 'relative', height: '100vh', overflow: 'hidden' }}>
      <Scene />
      {ui ? (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 36, pointerEvents: 'none' }}>
          <div style={{ width: 230, height: 230, borderRadius: '50%', border: '1px solid rgba(18,179,130,0.35)', background: 'rgba(0,26,47,0.45)', display: 'grid', placeItems: 'center', color: '#12b382', fontSize: 22 }}>
            Iniciar
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <span className="dock-pill">Parâmetros</span>
            <span className="dock-pill">Avançado</span>
          </div>
        </div>
      ) : null}
      <button
        onClick={() => setUi((v) => !v)}
        className="rounded-bezel border border-strut text-sm text-haze"
        style={{ position: 'absolute', left: 12, top: 12, padding: '4px 10px', background: 'rgba(0,19,31,0.6)' }}
      >
        {ui ? 'Esconder interface' : 'Mostrar interface'}
      </button>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(<Sim />);
