/**
 * What the map of the support (ТЗ 7.35 Д-32 п. 2) is made of, without React and three.js: the colour of a distance, the colours of all
 * the vertices of a surface (the frame of the repaint when D_оп moves: one pass over the distances, no allocation when the array is
 * given), the match of the vertices of the core with the vertices of the part of the 3D window, and the words of «Свойства».
 *
 * The colour: Cobalt where the surface lies near the data, Light Stone half way to D_оп, Copper Orange at D_оп; beyond it the copper
 * darkens (to Anthracite at twice the threshold) — «экстраполяция». A vertex without data points (NaN) is Slate, never a colour of a
 * distance (8.0.1). The ramp is a table of 256 steps: the same colour for the same distance on every machine.
 */
import { BRAND, mix } from "../../charts/column/classes";
import { num } from "../../shell/props";
import type { SupportSurface } from "./checkApi";

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

// ---- the vertices of the core and of the 3D window ----

/**
 * The distances come in the order of the vertices of the wireframe; the part of the 3D window lists the vertices its triangles use, in the
 * same order (`np.unique` of the core), so a wireframe of ONE part whose every vertex is used is read as it is. For a wireframe of several
 * parts the core does not say which vertices of the wireframe are the vertices of a part — the map is not drawn (null is «можно»; the
 * text is the reason).
 */
export function vertexMatch(parts: number, vertices: number, values: number): string | null {
  if (parts !== 1)
    return `Каркас состоит из ${parts} частей (до 64 000 треугольников каждая): ядро отдаёт расстояния по порядку вершин каркаса, а окно 3D делит его на части и вершин частей не нумерует. Таблица и доля площади за порогом доступны; раскраска — для поверхностей из одной части.`;
  if (vertices !== values) return `У поверхности ${num(values, 0)} вершин, у части в окне 3D — ${num(vertices, 0)}: вершины не сопоставить, карта не рисуется.`;
  return null;
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
