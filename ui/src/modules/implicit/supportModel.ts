/**
 * What the map of the support (ТЗ 7.35 Д-32 п. 2) is made of, without React and three.js: the colour of a distance, the colours of all
 * the vertices of a surface (the frame of the repaint when D_оп moves: one pass over the distances, no allocation when the array is
 * given), the surface of the core as the layer takes it (the whole wireframe, any number of parts), and the words of «Свойства».
 *
 * The colour: Cobalt where the surface lies near the data, Light Stone half way to D_оп, Copper Orange at D_оп; beyond it the copper
 * darkens (to Anthracite at twice the threshold) — «экстраполяция». A vertex without data points (NaN) is Slate, never a colour of a
 * distance (8.0.1). The ramp is a table of 256 steps: the same colour for the same distance on every machine.
 */
import { BRAND, mix } from "../../charts/column/classes";
import { num } from "../../shell/props";
import type { SupportMeshData } from "../../viz/support/layer";
import type { SupportMesh, SupportSurface } from "./checkApi";

const STEPS = 256;
/** the top of the ramp, in thresholds: 1 — Copper Orange, SPAN — the darkest copper */
const SPAN = 2;

const channels = (hex: string): [number, number, number] => [parseInt(hex.slice(1, 3), 16) / 255, parseInt(hex.slice(3, 5), 16) / 255, parseInt(hex.slice(5, 7), 16) / 255];

const NEAR = BRAND.cobalt;
const MID = BRAND.stone;
const FAR = BRAND.copper;
const OVER = mix(BRAND.copper, BRAND.anthracite, 0.7);
export const NO_DATA_COLOR = BRAND.slate;

/** The ramp by the share of the threshold: [0; SPAN] in STEPS steps. */
const RAMP: Float32Array = (() => {
  const out = new Float32Array((STEPS + 1) * 3);
  for (let k = 0; k <= STEPS; k++) {
    const t = (k / STEPS) * SPAN; // in thresholds
    const hex = t <= 0.5 ? mix(NEAR, MID, t / 0.5) : t <= 1 ? mix(MID, FAR, (t - 0.5) / 0.5) : mix(FAR, OVER, (t - 1) / (SPAN - 1));
    out.set(channels(hex), k * 3);
  }
  return out;
})();
const NO_DATA = channels(NO_DATA_COLOR);

/** The colour of a distance for a threshold, 0…1 a channel. A distance that is not a number is the colour of «нет данных». */
export function supportColor(distance: number, threshold: number): [number, number, number] {
  if (!Number.isFinite(distance) || !(threshold > 0)) return [...NO_DATA];
  const k = Math.min(STEPS, Math.max(0, Math.round((distance / threshold / SPAN) * STEPS))) * 3;
  return [RAMP[k], RAMP[k + 1], RAMP[k + 2]];
}

/** The colours of all the vertices (three numbers each) into `out` (a new array when none is given). */
export function colorsOf(values: Float32Array, threshold: number, out: Float32Array = new Float32Array(values.length * 3)): Float32Array {
  const scale = threshold > 0 ? STEPS / (threshold * SPAN) : 0;
  for (let i = 0, j = 0; i < values.length; i++, j += 3) {
    const d = values[i];
    if (!(d >= 0) || !scale || d === Infinity) {
      out[j] = NO_DATA[0];
      out[j + 1] = NO_DATA[1];
      out[j + 2] = NO_DATA[2];
      continue;
    }
    const k = Math.min(STEPS, Math.round(d * scale)) * 3;
    out[j] = RAMP[k];
    out[j + 1] = RAMP[k + 1];
    out[j + 2] = RAMP[k + 2];
  }
  return out;
}

/** The legend: the colour stops of the ramp as CSS, from 0 to SPAN thresholds. */
export const RAMP_CSS = `linear-gradient(to right, ${NEAR}, ${MID} 25%, ${FAR} 50%, ${OVER})`;
/** The labels under the legend, in thresholds: 0, 1, 2. */
export const RAMP_TICKS: readonly number[] = [0, 1, SPAN];

/** The vertices beyond the threshold and with no data, counted on the distances (the check of the colours, not the share of the area). */
export function countBeyond(values: Float32Array, threshold: number): { beyond: number; noData: number } {
  let beyond = 0;
  let noData = 0;
  for (let i = 0; i < values.length; i++) {
    const d = values[i];
    if (!(d >= 0) || d === Infinity) noData++;
    else if (d > threshold) beyond++;
  }
  return { beyond, noData };
}

// ---- the surface of the core as the 3D window draws it ----

/**
 * The surface of the answer `f4mesh` as the layer takes it: the vertices and triangles of the WHOLE wireframe (any number of parts and
 * triangles; the indices are 32-bit) in the order of the distances, so a colour of a vertex is the colour of the distance with the same
 * number — nothing is matched. `bbox` is absolute, [xmin, ymin, zmin, xmax, ymax, zmax] (null — no vertices).
 */
export function meshData(mesh: SupportMesh): SupportMeshData {
  const { origin: o, positions: p } = mesh;
  let box: number[] | null = null;
  if (p.length >= 3) {
    const r = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
    for (let i = 0; i < p.length; i += 3)
      for (let k = 0; k < 3; k++) {
        const v = p[i + k];
        if (v < r[k]) r[k] = v;
        if (v > r[k + 3]) r[k + 3] = v;
      }
    box = [r[0] + o.x, r[1] + o.y, r[2] + o.z, r[3] + o.x, r[4] + o.y, r[5] + o.z];
  }
  return { origin: o, positions: p, index: mesh.index, bbox: box };
}

// ---- the words ----

export const percent = (share: number | null): string => (share === null || !Number.isFinite(share) ? "—" : `${num(share * 100, share > 0 && share < 0.001 ? 3 : 1)} %`);
const metres = (v: number | null, digits = 1): string => (v === null || !Number.isFinite(v) ? "—" : `${num(v, digits)} м`);

/** «Свойства»: what the map says of a surface (the share of the area beyond D_оп is the first). */
export function supportFacts(s: SupportSurface, threshold: number): { label: string; value: string }[] {
  return [
    { label: "Порог D_оп", value: metres(threshold) },
    { label: "Площадь за порогом", value: percent(s.share_beyond) },
    { label: "Площадь за порогом, м²", value: s.area_beyond === null ? "—" : `${num(s.area_beyond, 0)} м²` },
    { label: "Площадь поверхности", value: s.area === null ? "—" : `${num(s.area, 0)} м²` },
    { label: "Наибольшее расстояние", value: metres(s.max_distance) },
    { label: "Вершин", value: num(s.vertices, 0) },
  ];
}

/** The line of the text of a command: «Литология · SS · поверхность: за порогом 12,3 % площади (наибольшее расстояние 143,2 м)». */
export function surfaceLine(s: SupportSurface): string {
  return `${s.name}: за порогом ${percent(s.share_beyond)} площади, наибольшее расстояние ${metres(s.max_distance)}`;
}
