// Розділювач «Містечко»: силует міста перед небезпечними діями (Divider у ui.tsx).
// Позаду — бліді кам'яниці й вежі, попереду — хатки, сквер із деревами, лавкою й ліхтарем.
// Усе зелене (колір бренду), і лише одна випадкова хатка біля центру червона — «ось цю й видаляємо».
// Кожне відкриття сторінки — трохи інше місто: розстановку задає випадкове зерно.

import { useState } from "react";

const W = 600; // ширина сцени; на телефоні видно середину, тож червону хатку ставимо ближче до центру
const H = 72;
const GROUND = H - 3;

type Rand = () => number;

function rng(seed: number): Rand {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const between = (r: Rand, a: number, b: number) => a + r() * (b - a);
const pick = <T,>(r: Rand, xs: T[]): T => xs[Math.floor(r() * xs.length)]!;
const n = (v: number) => Math.round(v * 10) / 10;
const poly = (pts: [number, number][]) => "M" + pts.map(([x, y]) => `${n(x)} ${n(y)}`).join("L") + "Z";

type Rect = { x: number; y: number; w: number; h: number; arch?: boolean };

function windowGrid(x: number, w: number, top: number, bottom: number, r: Rand, door: boolean): Rect[] {
  const out: Rect[] = [];
  const ww = 3;
  const wh = 4;
  const cols = Math.max(1, Math.floor((w - 4) / 7));
  const gap = (w - cols * ww) / (cols + 1);
  for (let y = top + 3; y + wh <= bottom - (door ? 8 : 3); y += 8) {
    for (let c = 0; c < cols; c++) out.push({ x: x + gap + c * (ww + gap), y, w: ww, h: wh });
  }
  if (door) out.push({ x: x + w / 2 - 2 + between(r, -w / 6, w / 6), y: bottom - 6.5, w: 4, h: 6.5, arch: true });
  return out;
}

function rectPath({ x, y, w, h, arch }: Rect): string {
  if (!arch) return `M${n(x)} ${n(y)}h${n(w)}v${n(h)}h${n(-w)}Z`;
  const rr = w / 2;
  return `M${n(x)} ${n(y + rr)}a${n(rr)} ${n(rr)} 0 0 1 ${n(w)} 0v${n(h - rr)}h${n(-w)}Z`;
}

type Building = { body: string; windows: string; w: number; cx: number };

function cottage(x: number, r: Rand): Building {
  const w = between(r, 24, 32);
  const wall = between(r, 14, 19);
  const roof = between(r, 10, 14);
  const top = GROUND - wall;
  const over = 2;
  let body = poly([
    [x - over, top],
    [x + w / 2, top - roof],
    [x + w + over, top],
    [x + w, top],
    [x + w, GROUND],
    [x, GROUND],
    [x, top],
  ]);
  if (r() < 0.75) {
    const cx = x + w * pick(r, [0.2, 0.68]);
    const cy = top - roof * 0.78;
    body += poly([
      [cx, cy],
      [cx + 3.4, cy],
      [cx + 3.4, top - 2],
      [cx, top - 2],
    ]);
    body += poly([
      [cx - 0.7, cy - 1.4],
      [cx + 4.1, cy - 1.4],
      [cx + 4.1, cy],
      [cx - 0.7, cy],
    ]);
  }
  let windows = windowGrid(x, w, top, GROUND, r, true).map(rectPath).join("");
  if (r() < 0.6) {
    const cy = top - roof * 0.4;
    windows += `M${n(x + w / 2 - 1.7)} ${n(cy)}a1.7 1.7 0 1 0 3.4 0a1.7 1.7 0 1 0 -3.4 0Z`;
  }
  return { body, windows, w: w + over, cx: x + w / 2 };
}

function townhouse(x: number, r: Rand): Building {
  const w = between(r, 16, 24);
  const h = between(r, 30, 46);
  const top = GROUND - h;
  const kind = pick(r, ["gable", "step", "mansard"]);
  const s = w / 4;
  const roofPts: [number, number][] =
    kind === "gable"
      ? [
          [x, top],
          [x + w / 2, top - between(r, 8, 11)],
          [x + w, top],
        ]
      : kind === "step"
        ? [
            [x, top],
            [x, top - 3],
            [x + s, top - 3],
            [x + s, top - 6],
            [x + w - s, top - 6],
            [x + w - s, top - 3],
            [x + w, top - 3],
            [x + w, top],
          ]
        : [
            [x - 1, top],
            [x + 2, top - 6],
            [x + w - 2, top - 6],
            [x + w + 1, top],
          ];
  const body = poly([...roofPts, [x + w, GROUND], [x, GROUND]]);
  return { body, windows: windowGrid(x, w, top, GROUND, r, false).map(rectPath).join(""), w, cx: x + w / 2 };
}

function tower(x: number, r: Rand): Building {
  const w = between(r, 10, 12);
  const top = GROUND - between(r, 42, 52);
  const body =
    poly([
      [x, top],
      [x + w / 2, top - 15],
      [x + w, top],
      [x + w, GROUND],
      [x, GROUND],
    ]) + `M${n(x + w / 2 - 0.5)} ${n(top - 20)}h1v6h-1Z`;
  const windows = [rectPath({ x: x + w / 2 - 1.6, y: top + 4, w: 3.2, h: 6, arch: true }), rectPath({ x: x + w / 2 - 1.6, y: top + 16, w: 3.2, h: 6, arch: true })].join("");
  return { body, windows, w, cx: x + w / 2 };
}

/** Сквер: листяні дерева, ялинка, лавка й ліхтар. Повертає шляхи крон і «заліза». */
function park(x: number, r: Rand): { trees: string; iron: string; w: number } {
  let trees = "";
  let iron = "";
  let cx = x;
  const items = 3 + Math.floor(r() * 2);
  const bench = Math.floor(r() * items);
  for (let i = 0; i < items; i++) {
    if (pick(r, ["leaf", "leaf", "fir"]) === "leaf") {
      const rad = between(r, 6, 9);
      const th = between(r, 5, 9);
      const cy = GROUND - th - rad;
      trees += `M${n(cx + rad - 0.8)} ${n(GROUND)}h1.6v${n(-th - 2)}h-1.6Z`;
      for (const [dx, dy, k] of [
        [0, 0, 1],
        [-rad * 0.55, rad * 0.35, 0.7],
        [rad * 0.55, rad * 0.3, 0.72],
      ] as const) {
        const rr = rad * k;
        trees += `M${n(cx + rad + dx - rr)} ${n(cy + dy)}a${n(rr)} ${n(rr)} 0 1 0 ${n(rr * 2)} 0a${n(rr)} ${n(rr)} 0 1 0 ${n(-rr * 2)} 0Z`;
      }
      cx += rad * 2 + between(r, 4, 8);
    } else {
      const h = between(r, 16, 24);
      const w = h * 0.36;
      const mid = cx + w;
      trees += poly([
        [mid, GROUND - h],
        [mid + w * 0.6, GROUND - h * 0.6],
        [mid + w * 0.35, GROUND - h * 0.6],
        [mid + w, GROUND - h * 0.2],
        [mid + 0.8, GROUND - h * 0.2],
        [mid + 0.8, GROUND],
        [mid - 0.8, GROUND],
        [mid - 0.8, GROUND - h * 0.2],
        [mid - w, GROUND - h * 0.2],
        [mid - w * 0.35, GROUND - h * 0.6],
        [mid - w * 0.6, GROUND - h * 0.6],
      ]);
      cx += w * 2 + between(r, 4, 8);
    }
    if (i === bench) {
      // лавка
      iron += `M${n(cx)} ${n(GROUND - 4.5)}h11v1.2h-11Z M${n(cx)} ${n(GROUND - 7.5)}h11v1.1h-11Z M${n(cx + 1)} ${n(GROUND - 4)}h1v4h-1Z M${n(cx + 9)} ${n(GROUND - 4)}h1v4h-1Z`;
      cx += 15;
    }
  }
  // ліхтар
  iron += `M${n(cx + 1.5)} ${n(GROUND - 17)}h1v17h-1Z M${n(cx - 0.3)} ${n(GROUND - 19)}a2.3 2.3 0 1 0 4.6 0a2.3 2.3 0 1 0 -4.6 0Z`;
  cx += 6;
  return { trees, iron, w: cx - x };
}

type Scene = { back: string; backWindows: string; front: string; frontWindows: string; red: Building | null; trees: string; iron: string };

function buildScene(seed: number): Scene {
  const r = rng(seed);
  // задній ряд — щільний, з вежами
  let back = "";
  let backWindows = "";
  for (let x = between(r, -6, 0); x < W; ) {
    const b = r() < 0.18 ? tower(x, r) : townhouse(x, r);
    back += b.body;
    backWindows += b.windows;
    x += b.w + between(r, 1, 5);
  }
  // передній ряд — просторий: хатки й сквер десь біля центру
  const houses: Building[] = [];
  let trees = "";
  let iron = "";
  const parkAt = between(r, W * 0.3, W * 0.55);
  let parked = false;
  for (let x = between(r, 0, 10); x < W - 30; ) {
    if (!parked && x >= parkAt) {
      const p = park(x, r);
      trees += p.trees;
      iron += p.iron;
      parked = true;
      x += p.w + between(r, 8, 14);
      continue;
    }
    const c = cottage(x, r);
    houses.push(c);
    x += c.w + between(r, 10, 22);
  }
  const central = houses.filter((h) => h.cx > W / 2 - 110 && h.cx < W / 2 + 110);
  const red = central.length ? pick(r, central) : null;
  const rest = houses.filter((h) => h !== red);
  return {
    back,
    backWindows,
    front: rest.map((h) => h.body).join(""),
    frontWindows: rest.map((h) => h.windows).join(""),
    red,
    trees,
    iron,
  };
}

export function Townscape() {
  const [scene] = useState(() => buildScene(Math.floor(Math.random() * 2 ** 31)));
  return (
    <svg className="townscape" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMax slice" aria-hidden="true">
      <g className="ts-back">
        <path d={scene.back} />
        <path className="ts-window" d={scene.backWindows} />
      </g>
      <path className="ts-ground" d={`M0 ${GROUND}H${W}V${H}H0Z`} />
      <path className="ts-tree" d={scene.trees} />
      <path className="ts-iron" d={scene.iron} />
      <path className="ts-house" d={scene.front} />
      <path className="ts-window" d={scene.frontWindows} />
      {scene.red && (
        <g className="ts-red">
          <path d={scene.red.body} />
          <path className="ts-window" d={scene.red.windows} />
        </g>
      )}
    </svg>
  );
}
