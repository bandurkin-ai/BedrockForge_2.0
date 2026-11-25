/**
 * What the window «Проверка по скважинам» shows, as pure functions (ТЗ 7.35 Д-32 п. 1): the columns and the cells of the table (a gap is
 * «—», never 0), the cloud of the points of the page in 3D (Cobalt — the model is below the data, Light Stone — equal, Copper Orange — above),
 * the box of «К точке», and the words for the assistant. No React.
 */
import { num } from "../../shell/props";
import { errorColor, type ErrorCloud } from "../estimation/xvalModel";
import type { CheckPage, CheckRow } from "./checkApi";

export type ColumnKey = "i" | "group" | "type" | "source" | "from" | "to" | "f_model" | "f_data" | "residual" | "distance";

export interface Column {
  key: ColumnKey;
  title: string;
  num: boolean;
  hint: string;
}

export const COLUMNS: readonly Column[] = [
  { key: "i", title: "№", num: true, hint: "Номер точки данных в модели" },
  { key: "group", title: "Группа", num: false, hint: "Группа модели, к которой относится точка (крыло или порог — в названии)" },
  { key: "type", title: "Тип", num: false, hint: "Контакт, внутри, снаружи, ручная или значение содержания" },
  { key: "source", title: "Источник", num: false, hint: "Скважина или слой стрингов, из которых взята точка" },
  { key: "from", title: "От, м", num: true, hint: "Начало интервала по стволу (контакт — глубина контакта)" },
  { key: "to", title: "До, м", num: true, hint: "Конец интервала по стволу" },
  { key: "f_model", title: "f модели", num: true, hint: "Значение поля модели в точке" },
  { key: "f_data", title: "f данных", num: true, hint: "Значение, заданное данными в этой точке" },
  { key: "residual", title: "Невязка", num: true, hint: "f модели − f данных. Подсвечены точки, где модуль невязки больше порога подсветки" },
  { key: "distance", title: "До каркаса, м", num: true, hint: "Расстояние от точки до построенной поверхности группы; пусто — у группы нет каркаса" },
];

/** A number of a cell: four digits that matter, a decimal comma, a small one as «3,2e-9»; a gap is «—». */
export function numText(v: number | null): string {
  if (v === null || !Number.isFinite(v)) return "—";
  if (v === 0) return "0";
  const abs = Math.abs(v);
  if (abs < 1e-3 || abs >= 1e7) return v.toExponential(1).replace(".", ",").replace("e+", "e").replace(/-/g, "−");
  return v.toLocaleString("ru-RU", { maximumSignificantDigits: 4 }).replace(/-/g, "−");
}

/** The name of the group of a row: «Дайка», a shell — «Группа ≥ 0,5», a wing — «Разлом, крыло 1». */
export function groupText(r: CheckRow): string {
  const t = r.threshold !== null ? ` ≥ ${numText(r.threshold)}` : "";
  const w = r.wing !== null ? `, крыло ${r.wing}` : "";
  return `${r.group_name || (r.group === null ? "Прочее" : `группа ${r.group}`)}${t}${w}`;
}

export function cellText(r: CheckRow, key: ColumnKey): string {
  switch (key) {
    case "i":
      return String(r.i);
    case "group":
      return groupText(r);
    case "type":
      return r.type || "—";
    case "source":
      return r.source || "—";
    case "from":
      return numText(r.from);
    case "to":
      return r.to === r.from && r.from !== null ? "—" : numText(r.to); // a contact has one depth
    case "f_model":
      return numText(r.f_model);
    case "f_data":
      return numText(r.f_data);
    case "residual":
      return numText(r.residual);
    case "distance":
      return numText(r.distance);
  }
}

export const pagesOf = (total: number, size: number): number => Math.max(1, Math.ceil(total / Math.max(1, size)));

/** «a point at (x, y, z)»: all three coordinates are there. */
export const hasPlace = (r: CheckRow): boolean => r.x !== null && r.y !== null && r.z !== null;

/** The place of a row as «X 10; Y 5; Z −12». */
export const placeText = (r: CheckRow): string => (hasPlace(r) ? `X ${num(r.x as number, 2)}; Y ${num(r.y as number, 2)}; Z ${num(r.z as number, 2)}` : "место не определено");

/** The depth of a point in the hole for the tooltip: «12–14,5 м». */
export function depthText(r: CheckRow): string {
  if (r.from === null) return "";
  return r.to !== null && r.to !== r.from ? `${num(r.from, 2)}–${num(r.to, 2)} м` : `${num(r.from, 2)} м`;
}

/** The colour of the full residual of the cloud: the threshold of the highlight, else the largest residual of the page. */
export function residualLimit(page: CheckPage): number {
  if (page.highlight !== null && page.highlight > 0) return page.highlight;
  const m = page.rows.reduce((a, r) => Math.max(a, Math.abs(r.residual ?? 0)), 0);
  return m > 0 ? m : 1;
}

/** The points of the page that have a place and a residual, as the cloud of the 3D window (`viz/xval/errorPoints.ts`); null — there is none. */
export function pointCloud(page: CheckPage, only: ((r: CheckRow) => boolean) | null = null): ErrorCloud | null {
  const have = page.rows.filter((r) => r.residual !== null && hasPlace(r) && (!only || only(r)));
  if (!have.length) return null;
  const limit = residualLimit(page);
  const xyz = new Float64Array(have.length * 3);
  const rgb = new Float32Array(have.length * 3);
  have.forEach((r, k) => {
    xyz.set([r.x as number, r.y as number, r.z as number], k * 3);
    rgb.set(errorColor(r.residual as number, limit), k * 3);
  });
  return { xyz, rgb, limit, n: have.length };
}

/** The box around a point for `scene.reveal`: the radius is the larger of 10 m and ten thresholds of the highlight. */
export function boxAround(r: CheckRow, highlight: number | null): number[] | null {
  if (!hasPlace(r)) return null;
  const R = Math.max(10, (highlight ?? 0) * 10);
  const [x, y, z] = [r.x as number, r.y as number, r.z as number];
  return [x - R, y - R, z - R, x + R, y + R, z + R];
}

/** The text of the command for the assistant and the log. */
export function checkLine(p: CheckPage): string {
  const worst = p.rows.find((r) => r.residual !== null && (p.sort === "residual" || r.over));
  const head = `Модель «${p.model.name}»: точек ${num(p.n, 0)}, наибольшая невязка ${numText(p.max_abs_residual)}, выше порога подсветки ${num(p.over, 0)}${p.highlight !== null ? ` (порог ${numText(p.highlight)} м)` : ""}`;
  const tail = worst && p.sort === "residual" ? `; первая в таблице — ${worst.source} ${depthText(worst)}, невязка ${numText(worst.residual)}` : "";
  const warn = p.warnings.length ? ` Замечания: ${p.warnings.map((w) => w.message).join(" ")}` : "";
  return `${head}${tail}.${warn}`;
}
