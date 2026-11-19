/**
 * The window «Фильтр земли» as data (ТЗ 7.35 Д-22; ADR 0014 §4, §6): the clouds of the project, the profiles
 * «Карьер» / «Равнина» with their numbers (as `geo/ground.py` `PROFILES` has them — preliminary constants), the
 * profile «Свой» (a profile with its numbers changed), the ranges of the parameters, the body of
 * `POST /lines/layers/{id}/classify` and the arguments of the command `ground.classify`. Numbers are typed with a
 * comma or a point; an empty field is the value of the profile — never 0. Pure.
 */
import { num } from "../../shell/props";
import { count } from "../../shell/treeActions";
import { parseNumber } from "../importing/objects";
import { className } from "../lines/classes";
import type { LineFileInfo, LineLayerInfo } from "../scene/objects";

export type ProfileId = "quarry" | "plain";
/** «Свой» — a profile (`base`) with its numbers */
export type ProfileChoice = ProfileId | "custom";

export interface ProfileValues {
  cr: number;
  ri: number;
  ct: number;
  /** null — faces are not looked for */
  r_b: number | null;
  /** null — noise is not looked for */
  noise: { r: number; k_min: number } | null;
}

/** `geo/ground.py` `PROFILES` (ADR 0014 §4, the table of parameters). */
export const PROFILES: Readonly<Record<ProfileId, ProfileValues>> = {
  quarry: { cr: 1, ri: 2, ct: 0.5, r_b: 5, noise: { r: 2, k_min: 3 } },
  plain: { cr: 1, ri: 3, ct: 0.5, r_b: null, noise: { r: 2, k_min: 3 } },
};
export const PROFILE_TITLES: Readonly<Record<ProfileChoice, string>> = { quarry: "Карьер", plain: "Равнина", custom: "Свой" };
export const PROFILE_HINTS: Readonly<Record<ProfileChoice, string>> = {
  quarry: "Уступы, бермы, откосы и бровки: ткань мягче, рост по склону до 55°, вертикальные грани с землёй сверху и снизу — земля",
  plain: "Пологий рельеф: жёсткая ткань, рост по склону до 30°, грани не ищутся",
  custom: "Профиль с изменёнными числами: пустое поле — значение профиля",
};

/** Ranges of ТЗ 7.35 Д-22 (`geo/ground.py` `_RANGES`). */
export const RANGES = {
  cr: { lo: 0.1, hi: 50, title: "Шаг ткани CR", unit: "м" },
  ct: { lo: 0.05, hi: 5, title: "Порог земли CT", unit: "м" },
  r_b: { lo: 1, hi: 20, title: "Радиус поиска земли у грани R_б", unit: "м" },
  noise_r: { lo: 0.5, hi: 20, title: "Радиус шума", unit: "м" },
} as const;
/** R_б when faces are switched on over «Равнина» (which has none): the default of the range */
export const DEFAULT_R_B = 5;

export interface GroundForm {
  cloudId: number | null;
  profile: ProfileChoice;
  /** the profile «Свой» starts from */
  base: ProfileId;
  cr: string;
  ri: string;
  ct: string;
  faces: boolean;
  rb: string;
  noise: boolean;
  noiseR: string;
  noiseK: string;
}

const text = (v: number | null | undefined): string => (typeof v === "number" ? String(v).replace(".", ",") : "");

/** The fields of a profile: its numbers as text. */
export function profileFields(id: ProfileId): Pick<GroundForm, "cr" | "ri" | "ct" | "faces" | "rb" | "noise" | "noiseR" | "noiseK"> {
  const p = PROFILES[id];
  return { cr: text(p.cr), ri: text(p.ri), ct: text(p.ct), faces: p.r_b !== null, rb: text(p.r_b ?? DEFAULT_R_B), noise: p.noise !== null, noiseR: text(p.noise?.r ?? 2), noiseK: text(p.noise?.k_min ?? 3) };
}

/** A new profile chosen: «Карьер» / «Равнина» show their numbers; «Свой» keeps the numbers in the fields. */
export function withProfile(form: GroundForm, profile: ProfileChoice): GroundForm {
  if (profile === "custom") return { ...form, profile };
  return { ...form, ...profileFields(profile), profile, base: profile };
}

/** The clouds of the project (a layer with `cloud`), in the order of the tree. */
export function cloudLayers(files: readonly LineFileInfo[]): LineLayerInfo[] {
  return files.flatMap((f) => f.layers).filter((l) => !!l.cloud);
}

/** The cloud of the window: the one asked, else the selected layer when it is a cloud, else the only cloud. */
export function pickCloud(files: readonly LineFileInfo[], asked: number | null, selected: number | null): number | null {
  const clouds = cloudLayers(files);
  for (const id of [asked, selected]) if (id !== null && clouds.some((l) => l.id === id)) return id;
  return clouds.length === 1 ? clouds[0].id : null;
}

/** The parameters of the last filter of a cloud (`cloud.ground.params` of the core), if it has one. */
export interface LastGround {
  profile: ProfileId;
  cr: number;
  ri: number;
  ct: number;
  r_b: number | null;
  noise_r: number | null;
  noise_k: number;
  counts: Record<string, number>;
  at: string | null;
}

export function lastGround(l: Pick<LineLayerInfo, "cloud"> | null | undefined): LastGround | null {
  const g = l?.cloud?.ground as { params?: Record<string, unknown>; counts?: Record<string, number>; at?: string } | null | undefined;
  const p = g?.params;
  if (!p || typeof p.cr !== "number" || typeof p.ri !== "number" || typeof p.ct !== "number") return null;
  return {
    profile: p.profile === "plain" ? "plain" : "quarry",
    cr: p.cr,
    ri: p.ri,
    ct: p.ct,
    r_b: typeof p.r_b === "number" ? p.r_b : null,
    noise_r: typeof p.noise_r === "number" ? p.noise_r : null,
    noise_k: typeof p.noise_k === "number" ? p.noise_k : 3,
    counts: g?.counts ?? {},
    at: typeof g?.at === "string" ? g.at : null,
  };
}

/** The form when the window opens: «Карьер», or the parameters of the last filter of the cloud. */
export function initialGround(files: readonly LineFileInfo[], cloudId: number | null): GroundForm {
  const form: GroundForm = { cloudId, profile: "quarry", base: "quarry", ...profileFields("quarry") };
  const last = lastGround(cloudLayers(files).find((l) => l.id === cloudId));
  return last ? fromLast(form, last) : form;
}

function fromLast(form: GroundForm, last: LastGround): GroundForm {
  const p = PROFILES[last.profile];
  const same = p.cr === last.cr && p.ri === last.ri && p.ct === last.ct && p.r_b === last.r_b && (p.noise?.r ?? null) === last.noise_r && (last.noise_r === null || p.noise?.k_min === last.noise_k);
  return {
    ...form,
    profile: same ? last.profile : "custom",
    base: last.profile,
    cr: text(last.cr),
    ri: text(last.ri),
    ct: text(last.ct),
    faces: last.r_b !== null,
    rb: text(last.r_b ?? DEFAULT_R_B),
    noise: last.noise_r !== null,
    noiseR: text(last.noise_r ?? 2),
    noiseK: text(last.noise_k),
  };
}

/** The body of `POST /lines/layers/{id}/classify` (its fields are the arguments of `ground.classify` too). */
export interface GroundBody {
  profile: ProfileId;
  cr?: number | null;
  ri?: number | null;
  ct?: number | null;
  /** false — faces off */
  r_b?: number | false | null;
  /** false — noise off */
  noise?: { r: number | null; k_min: number | null } | false | null;
}

const fmt = (v: number): string => num(v, 2);

function inRange(raw: string, key: keyof typeof RANGES): { value: number | null; error: string | null } {
  const r = RANGES[key];
  const v = parseNumber(raw);
  if (v === null) return { value: null, error: null };
  if (v === undefined || v < r.lo || v > r.hi) return { value: null, error: `«${r.title}» — число от ${fmt(r.lo)} до ${fmt(r.hi)} ${r.unit}, например ${fmt(r.lo * 10)}.` };
  return { value: v, error: null };
}

function whole(raw: string, lo: number, hi: number, title: string): { value: number | null; error: string | null } {
  const v = parseNumber(raw);
  if (v === null) return { value: null, error: null };
  if (v === undefined || !Number.isInteger(v) || v < lo || v > hi) return { value: null, error: `«${title}» — целое от ${lo} до ${hi}.` };
  return { value: v, error: null };
}

/** The body of the request, or why the filter cannot start. «Карьер» / «Равнина» send only the profile. */
export function groundBody(form: GroundForm): { body: GroundBody | null; error: string | null } {
  if (form.profile !== "custom") return { body: { profile: form.profile }, error: null };
  const cr = inRange(form.cr, "cr");
  const ri = whole(form.ri, 1, 3, "Жёсткость ткани RI");
  const ct = inRange(form.ct, "ct");
  const rb = form.faces ? inRange(form.rb, "r_b") : { value: null, error: null };
  const nr = form.noise ? inRange(form.noiseR, "noise_r") : { value: null, error: null };
  const nk = form.noise ? whole(form.noiseK, 1, 20, "Число соседей для шума") : { value: null, error: null };
  const error = [cr, ri, ct, rb, nr, nk].find((x) => x.error)?.error ?? null;
  if (error) return { body: null, error };
  const base = PROFILES[form.base];
  return {
    body: {
      profile: form.base,
      cr: cr.value,
      ri: ri.value,
      ct: ct.value,
      // faces on over a profile without them: an empty R_б is the default of the range, not «off»
      r_b: form.faces ? (rb.value ?? (base.r_b === null ? DEFAULT_R_B : null)) : false,
      noise: form.noise ? { r: nr.value, k_min: nk.value } : false,
    },
    error: null,
  };
}

/** The arguments of the command `ground.classify` for a body (null fields are left out: the profile's). */
export function groundArgs(cloudId: number, body: GroundBody): Record<string, unknown> {
  const a: Record<string, unknown> = { cloud_id: cloudId, profile: body.profile };
  for (const k of ["cr", "ri", "ct"] as const) if (typeof body[k] === "number") a[k] = body[k];
  if (body.r_b === false) a.r_b = null;
  else if (typeof body.r_b === "number") a.r_b = body.r_b;
  if (body.noise === false) a.noise = false;
  else if (body.noise) {
    if (body.noise.r !== null) a.noise_r = body.noise.r;
    if (body.noise.k_min !== null) a.noise_k = body.noise.k_min;
  }
  return a;
}

/** The body of the request for the arguments of `ground.classify` (the reverse of `groundArgs`). */
export function bodyOfArgs(a: Record<string, unknown>): GroundBody {
  const n = (v: unknown): number | null => (typeof v === "number" ? v : null);
  const body: GroundBody = { profile: a.profile === "plain" ? "plain" : "quarry", cr: n(a.cr), ri: n(a.ri), ct: n(a.ct) };
  if ("r_b" in a) body.r_b = a.r_b === null ? false : n(a.r_b);
  if (a.noise === false) body.noise = false;
  else if (a.noise_r !== undefined || a.noise_k !== undefined) body.noise = { r: n(a.noise_r), k_min: n(a.noise_k) };
  return body;
}

/** «профиль «Равнина», CR 1 м, RI 3, CT 0,5 м, грани выключены, шум: 2 м, < 3 соседей» */
export function paramsText(last: LastGround): string {
  const faces = last.r_b === null ? "грани выключены" : `R_б ${fmt(last.r_b)} м`;
  const noise = last.noise_r === null ? "шум не ищется" : `шум: радиус ${fmt(last.noise_r)} м, соседей меньше ${last.noise_k}`;
  return `профиль «${PROFILE_TITLES[last.profile]}»: CR ${fmt(last.cr)} м, RI ${last.ri}, CT ${fmt(last.ct)} м, ${faces}, ${noise}`;
}

/** «земля — 2 627, шум — 82» of the counts of the last filter */
export function countsText(counts: Record<string, number>): string {
  const rows = Object.entries(counts)
    .map(([k, n]) => [Number(k), n] as const)
    .filter(([k, n]) => Number.isInteger(k) && n > 0)
    .sort((a, b) => a[0] - b[0]);
  return rows.length ? rows.map(([k, n]) => `${className(k)} — ${count(n)}`).join(", ") : "точек нет";
}
