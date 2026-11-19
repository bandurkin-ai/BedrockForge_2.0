/**
 * The window «Фильтр земли» as data (ТЗ 7.35 Д-22; ADR 0014 §4, §6): the clouds of the project, the profiles
 * «Карьер» / «Равнина» with their numbers, titles and hints and the ranges of the parameters — the answer of
 * `GET /lines/ground/profiles` (`parseProfiles`; until it comes — `SPARE`), the profile «Свой» (a profile with
 * its numbers changed), the body of `POST /lines/layers/{id}/classify` and the arguments of the command
 * `ground.classify`. Numbers are typed with a comma or a point. Pure.
 *
 * One meaning of an empty value and of a switch — in the arguments of the command, in the body of the request
 * and in a profile alike (as in the core, `lines/ground_job.py`): **null**, an empty field, a value not given
 * — as the profile has it, never 0 and never «off»; **false** — the stage is off (faces, noise); **true** — the
 * stage is on with the numbers of the profile, or the defaults of the kernel when the profile has it off.
 */
import { num } from "../../shell/props";
import { count } from "../../shell/treeActions";
import { parseNumber } from "../importing/objects";
import { className } from "../lines/classes";
import type { LineFileInfo, LineLayerInfo } from "../scene/objects";

export type ProfileId = "quarry" | "plain";
export const PROFILE_IDS: readonly ProfileId[] = ["quarry", "plain"];
/** «Свой» — a profile (`base`) with its numbers */
export type ProfileChoice = ProfileId | "custom";

export interface NoiseValues {
  r: number;
  k_min: number;
}

export interface ProfileValues {
  title: string;
  /** what the profile is for, with the constants of the kernel (the slope its growth climbs) */
  hint: string;
  cr: number;
  ri: number;
  ct: number;
  /** false — faces are not looked for */
  r_b: number | false;
  /** false — noise is not looked for */
  noise: NoiseValues | false;
}

export interface Range {
  lo: number;
  hi: number;
  title: string;
  /** "м", or "" for a number without a unit */
  unit: string;
  whole: boolean;
}
export type RangeKey = "cr" | "ct" | "r_b" | "noise_r" | "ri" | "noise_k";
const RANGE_KEYS: readonly RangeKey[] = ["cr", "ct", "r_b", "noise_r", "ri", "noise_k"];

/** What the kernel has (`GET /lines/ground/profiles`). */
export interface GroundProfiles {
  profiles: Readonly<Record<ProfileId, ProfileValues>>;
  /** the profile of a request that names none */
  default: ProfileId;
  /** the numbers of a stage switched on over a profile that has it off */
  defaults: { r_b: number; noise: NoiseValues };
  /** ranges of ТЗ 7.35 Д-22, as the kernel checks them */
  ranges: Readonly<Record<RangeKey, Range>>;
  /** false — the spare values of the interface: the core has not answered yet */
  loaded: boolean;
}

/** The spare values, shown until the core answers (a test holds them to the answer of the core:
 * `core/tests/data/ground/profiles.json`). The hints name no constant of the kernel — those come with the answer. */
export const SPARE: GroundProfiles = {
  profiles: {
    quarry: { title: "Карьер", hint: "Уступы, бермы, откосы и бровки: ткань мягче, вертикальные грани с землёй сверху и снизу — земля", cr: 1, ri: 2, ct: 0.5, r_b: 5, noise: { r: 2, k_min: 3 } },
    plain: { title: "Равнина", hint: "Пологий рельеф: жёсткая ткань, грани не ищутся", cr: 1, ri: 3, ct: 0.5, r_b: false, noise: { r: 2, k_min: 3 } },
  },
  default: "quarry",
  defaults: { r_b: 5, noise: { r: 2, k_min: 3 } },
  ranges: {
    cr: { lo: 0.1, hi: 50, title: "Шаг ткани CR", unit: "м", whole: false },
    ct: { lo: 0.05, hi: 5, title: "Порог земли CT", unit: "м", whole: false },
    r_b: { lo: 1, hi: 20, title: "Радиус поиска земли у грани R_б", unit: "м", whole: false },
    noise_r: { lo: 0.5, hi: 20, title: "Радиус шума", unit: "м", whole: false },
    ri: { lo: 1, hi: 3, title: "Жёсткость ткани RI", unit: "", whole: true },
    noise_k: { lo: 1, hi: 20, title: "Число соседей для шума", unit: "", whole: true },
  },
  loaded: false,
};

const CUSTOM = { title: "Свой", hint: "Профиль с изменёнными числами: пустое поле — значение профиля" } as const;

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const record = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null);

function noiseOf(v: unknown): NoiseValues | null {
  const n = record(v);
  return n && finite(n.r) && finite(n.k_min) ? { r: n.r, k_min: n.k_min } : null;
}

function profileOf(v: unknown): ProfileValues | null {
  const p = record(v);
  if (!p || typeof p.title !== "string" || !p.title || typeof p.hint !== "string" || !finite(p.cr) || !finite(p.ri) || !finite(p.ct)) return null;
  const rb = p.r_b === false ? false : finite(p.r_b) ? p.r_b : null;
  const noise = p.noise === false ? false : noiseOf(p.noise);
  if (rb === null || noise === null) return null;
  return { title: p.title, hint: p.hint, cr: p.cr, ri: p.ri, ct: p.ct, r_b: rb, noise };
}

function rangeOf(v: unknown): Range | null {
  const r = record(v);
  if (!r || !finite(r.lo) || !finite(r.hi) || r.lo >= r.hi || typeof r.title !== "string" || !r.title) return null;
  return { lo: r.lo, hi: r.hi, title: r.title, unit: typeof r.unit === "string" ? r.unit : "", whole: r.whole === true };
}

/** The answer of `GET /lines/ground/profiles`, checked; null — not what the window can show (the spare stays). */
export function parseProfiles(raw: unknown): GroundProfiles | null {
  const a = record(raw);
  const list = a && Array.isArray(a.profiles) ? a.profiles.map(record) : null;
  const defaults = a && record(a.defaults);
  const given = a && record(a.ranges);
  if (!a || !list || !defaults || !given) return null;
  const profiles: Partial<Record<ProfileId, ProfileValues>> = {};
  for (const id of PROFILE_IDS) {
    const p = profileOf(list.find((x) => x?.id === id));
    if (!p) return null;
    profiles[id] = p;
  }
  const ranges: Partial<Record<RangeKey, Range>> = {};
  for (const key of RANGE_KEYS) {
    const r = rangeOf(given[key]);
    if (!r) return null;
    ranges[key] = r;
  }
  const noise = noiseOf(defaults.noise);
  if (!finite(defaults.r_b) || !noise) return null;
  return {
    profiles: profiles as Record<ProfileId, ProfileValues>,
    default: a.default === "plain" ? "plain" : "quarry",
    defaults: { r_b: defaults.r_b, noise },
    ranges: ranges as Record<RangeKey, Range>,
    loaded: true,
  };
}

/** «Карьер», «Равнина» — as the core names them; «Свой» */
export const profileTitle = (t: GroundProfiles, choice: ProfileChoice): string => (choice === "custom" ? CUSTOM.title : t.profiles[choice].title);
export const profileHint = (t: GroundProfiles, choice: ProfileChoice): string => (choice === "custom" ? CUSTOM.hint : t.profiles[choice].hint);

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

/** The fields of a profile: its numbers as text. A stage the profile has off shows the defaults of the kernel. */
export function profileFields(t: GroundProfiles, id: ProfileId): Pick<GroundForm, "cr" | "ri" | "ct" | "faces" | "rb" | "noise" | "noiseR" | "noiseK"> {
  const p = t.profiles[id];
  const noise = p.noise === false ? t.defaults.noise : p.noise;
  return { cr: text(p.cr), ri: text(p.ri), ct: text(p.ct), faces: p.r_b !== false, rb: text(p.r_b === false ? t.defaults.r_b : p.r_b), noise: p.noise !== false, noiseR: text(noise.r), noiseK: text(noise.k_min) };
}

/** A new profile chosen: «Карьер» / «Равнина» show their numbers; «Свой» keeps the numbers in the fields. */
export function withProfile(t: GroundProfiles, form: GroundForm, profile: ProfileChoice): GroundForm {
  if (profile === "custom") return { ...form, profile };
  return { ...form, ...profileFields(t, profile), profile, base: profile };
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

/** The parameters of the last filter of a cloud (`cloud.ground.params` of the core), if it has one. They are
 * not a request but what the kernel ran with: a stage that was off has no number (null). */
export interface LastGround {
  profile: ProfileId;
  cr: number;
  ri: number;
  ct: number;
  /** null — the faces were off */
  r_b: number | null;
  /** null — the noise was off */
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

/** The form when the window opens: the default profile of the core, or the parameters of the last filter of
 * the cloud. */
export function initialGround(t: GroundProfiles, files: readonly LineFileInfo[], cloudId: number | null): GroundForm {
  const form: GroundForm = { cloudId, profile: t.default, base: t.default, ...profileFields(t, t.default) };
  const last = lastGround(cloudLayers(files).find((l) => l.id === cloudId));
  return last ? fromLast(t, form, last) : form;
}

/** The numbers of the last filter are those of its profile as the core has it now («Карьер», not «Свой»). */
export function sameAsProfile(p: ProfileValues, last: LastGround): boolean {
  const noise = p.noise === false ? null : p.noise;
  const faces = p.r_b === false ? null : p.r_b;
  return p.cr === last.cr && p.ri === last.ri && p.ct === last.ct && faces === last.r_b && (noise?.r ?? null) === last.noise_r && (last.noise_r === null || noise?.k_min === last.noise_k);
}

function fromLast(t: GroundProfiles, form: GroundForm, last: LastGround): GroundForm {
  return {
    ...form,
    profile: sameAsProfile(t.profiles[last.profile], last) ? last.profile : "custom",
    base: last.profile,
    cr: text(last.cr),
    ri: text(last.ri),
    ct: text(last.ct),
    faces: last.r_b !== null,
    rb: text(last.r_b ?? t.defaults.r_b),
    noise: last.noise_r !== null,
    noiseR: text(last.noise_r ?? t.defaults.noise.r),
    noiseK: text(last.noise_k),
  };
}

/** The body of `POST /lines/layers/{id}/classify`: null (or a field left out) — as the profile has it; `r_b`,
 * `noise`: false — the stage is off, true — on with the numbers of the profile (the defaults of the kernel
 * when the profile has it off). */
export interface GroundBody {
  profile: ProfileId;
  cr?: number | null;
  ri?: number | null;
  ct?: number | null;
  r_b?: number | boolean | null;
  noise?: { r: number | null; k_min: number | null } | boolean | null;
}

const fmt = (v: number): string => num(v, 2);

/** «0,1–50 м», «1–3» */
export const rangeText = (r: Range): string => `${fmt(r.lo)}–${fmt(r.hi)}${r.unit ? ` ${r.unit}` : ""}`;

/** A number of a field within its range: null — the field is empty (the value of the profile). */
function inRange(raw: string, r: Range): { value: number | null; error: string | null } {
  const v = parseNumber(raw);
  if (v === null) return { value: null, error: null };
  const bad = v === undefined || v < r.lo || v > r.hi || (r.whole && !Number.isInteger(v));
  if (!bad) return { value: v as number, error: null };
  if (r.whole) return { value: null, error: `«${r.title}» — целое от ${fmt(r.lo)} до ${fmt(r.hi)}.` };
  return { value: null, error: `«${r.title}» — число от ${fmt(r.lo)} до ${fmt(r.hi)} ${r.unit}, например ${fmt(r.lo * 10)}.` };
}

/** The body of the request, or why the filter cannot start. «Карьер» / «Равнина» send only the profile. */
export function groundBody(t: GroundProfiles, form: GroundForm): { body: GroundBody | null; error: string | null } {
  if (form.profile !== "custom") return { body: { profile: form.profile }, error: null };
  const none = { value: null, error: null };
  const cr = inRange(form.cr, t.ranges.cr);
  const ri = inRange(form.ri, t.ranges.ri);
  const ct = inRange(form.ct, t.ranges.ct);
  const rb = form.faces ? inRange(form.rb, t.ranges.r_b) : none;
  const nr = form.noise ? inRange(form.noiseR, t.ranges.noise_r) : none;
  const nk = form.noise ? inRange(form.noiseK, t.ranges.noise_k) : none;
  const error = [cr, ri, ct, rb, nr, nk].find((x) => x.error)?.error ?? null;
  if (error) return { body: null, error };
  return {
    body: {
      profile: form.base,
      cr: cr.value,
      ri: ri.value,
      ct: ct.value,
      // faces on with an empty R_б: `true` — the R_б of the profile, or the default of the kernel over a
      // profile without faces; never null (that is «as the profile»: off over «Равнина»)
      r_b: form.faces ? (rb.value ?? true) : false,
      noise: form.noise ? (nr.value === null && nk.value === null ? true : { r: nr.value, k_min: nk.value }) : false,
    },
    error: null,
  };
}

/** The arguments of the command `ground.classify` for a body: a number — `r_b`, `noise_r`, `noise_k`; a
 * switch — `faces`, `noise` (false — off, true — on); null fields are left out — the profile's. */
export function groundArgs(cloudId: number, body: GroundBody): Record<string, unknown> {
  const a: Record<string, unknown> = { cloud_id: cloudId, profile: body.profile };
  for (const k of ["cr", "ri", "ct"] as const) if (typeof body[k] === "number") a[k] = body[k];
  if (typeof body.r_b === "boolean") a.faces = body.r_b;
  else if (typeof body.r_b === "number") a.r_b = body.r_b;
  if (typeof body.noise === "boolean") a.noise = body.noise;
  else if (body.noise) {
    if (body.noise.r !== null) a.noise_r = body.noise.r;
    if (body.noise.k_min !== null) a.noise_k = body.noise.k_min;
  }
  return a;
}

/** The body of the request for the arguments of `ground.classify` (the reverse of `groundArgs`). A switch set
 * to false wins over a number; `r_b: null` is «as the profile» — nothing is sent. */
export function bodyOfArgs(a: Record<string, unknown>): GroundBody {
  const n = (v: unknown): number | null => (typeof v === "number" ? v : null);
  const body: GroundBody = { profile: a.profile === "plain" ? "plain" : "quarry", cr: n(a.cr), ri: n(a.ri), ct: n(a.ct) };
  if (a.faces === false) body.r_b = false;
  else if (typeof a.r_b === "number") body.r_b = a.r_b;
  else if (a.faces === true) body.r_b = true;
  if (a.noise === false) body.noise = false;
  else if (typeof a.noise_r === "number" || typeof a.noise_k === "number") body.noise = { r: n(a.noise_r), k_min: n(a.noise_k) };
  else if (a.noise === true) body.noise = true;
  return body;
}

/** «профиль «Равнина», CR 1 м, RI 3, CT 0,5 м, грани выключены, шум: 2 м, < 3 соседей» */
export function paramsText(t: GroundProfiles, last: LastGround): string {
  const faces = last.r_b === null ? "грани выключены" : `R_б ${fmt(last.r_b)} м`;
  const noise = last.noise_r === null ? "шум не ищется" : `шум: радиус ${fmt(last.noise_r)} м, соседей меньше ${last.noise_k}`;
  return `профиль «${profileTitle(t, last.profile)}»: CR ${fmt(last.cr)} м, RI ${last.ri}, CT ${fmt(last.ct)} м, ${faces}, ${noise}`;
}

/** «земля — 2 627, шум — 82» of the counts of the last filter */
export function countsText(counts: Record<string, number>): string {
  const rows = Object.entries(counts)
    .map(([k, n]) => [Number(k), n] as const)
    .filter(([k, n]) => Number.isInteger(k) && n > 0)
    .sort((a, b) => a[0] - b[0]);
  return rows.length ? rows.map(([k, n]) => `${className(k)} — ${count(n)}`).join(", ") : "точек нет";
}
