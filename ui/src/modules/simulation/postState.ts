/**
 * The post-processing of the realizations as the window sees it (ТЗ 8.8.1, Д-23; docs/progress/e18-api.md §9): what is written to the
 * block model — for a continuous variable E-type (MEAN), STD, P10, P50, P90 and the share of the realizations not below a threshold
 * `PROB_GT_<t>`; for the categories of SIS the share of every category `PROB_<code>` and the most frequent one `MODE` — the prefix,
 * the question «Заменить поля» (409 `field_exists`). The form is checked here with the words of the core; the run is the command
 * `simulate.post` (`bind.ts`). No React.
 */
import { Store } from "../../shell/store";
import { numberText, parseNumber } from "../composites/params";
import { defaultPrefix } from "../estimation/formSearch";
import { bad, isIssue, type Issue } from "../estimation/formBase";
import { CAT_STATS, POST_STATS, categoriesOf, thresholdsOfMeta, type ContStat, type PostBody, type PostResult, type PostStat, type Refusal, type SimKind, type Started } from "./api";

export const POST_WINDOW = "simulation-post";
export const POST_TITLE = "Постобработка реализаций";

export const THRESHOLDS_MAX = 20;
export const PREFIX_MAX = 40;

/** The words of the statistics (the check boxes of the window). */
export const STAT_TITLES: Record<ContStat, string> = {
  mean: "E-type — среднее по реализациям (MEAN)",
  std: "Стандартное отклонение по реализациям (STD)",
  p10: "Квантиль 10 % (P10)",
  p50: "Медиана (P50)",
  p90: "Квантиль 90 % (P90)",
  prob: "Вероятность: доля реализаций не ниже порога t (PROB_GT_t)",
};
export const CAT_STAT_TITLES: Record<"prob" | "mode", string> = {
  prob: "Вероятность каждой категории: доля реализаций (PROB_код)",
  mode: "Мода — самая частая категория (MODE)",
};

export type PostPhase = "idle" | "running" | "done" | "failed";

export interface PostState {
  /** the simulation the window is about */
  simId: number | null;
  /** "" — the core's default «‹ПОЛЕ›_SGS_» */
  prefix: string;
  /** null — the defaults of the kind */
  stats: PostStat[] | null;
  /** «0,5; 1; 2,5» */
  thresholds: string;
  replace: boolean;
  phase: PostPhase;
  job: Started | null;
  result: PostResult | null;
  error: Refusal | null;
  /** the issue under a field of the form */
  issue: Issue | null;
}

export const EMPTY_POST: PostState = { simId: null, prefix: "", stats: null, thresholds: "", replace: false, phase: "idle", job: null, result: null, error: null, issue: null };

export const postStore = new Store<PostState>(EMPTY_POST);

/** A change of the form: the old refusal goes. */
export const setPost = (patch: Partial<PostState>): void => postStore.set({ ...patch, issue: null });
export const resetPost = (): void => postStore.set(EMPTY_POST);
/** Another simulation: the form of the old one does not go over (the result stays until the next run). */
export const choosePost = (simId: number | null): void => {
  // a running job belongs to its simulation: another one is not chosen under it (its result would land in the form of the other)
  if (postStore.get().phase === "running") return;
  postStore.set({ ...EMPTY_POST, simId });
};

/** What the form says of a stored row: its kind, the field, the categories, the thresholds of the SIS. */
export interface PostSource {
  kind: SimKind;
  field: string;
  categories: { codes: number[]; labels: string[] } | null;
  thresholds: number[];
}

export function sourceOf(meta: Record<string, unknown>): PostSource {
  const kind = meta.kind === "sis" || meta.kind === "cosgs" ? meta.kind : "sgs";
  const samples = (meta.samples ?? {}) as { field?: unknown };
  return { kind, field: typeof samples.field === "string" ? samples.field : "", categories: categoriesOf(meta), thresholds: thresholdsOfMeta(meta) };
}

/** «AU_SGS_» — the prefix the core takes without one. */
export const defaultPostPrefix = (s: PostSource): string => defaultPrefix(s.field, s.kind);

/** The statistics the form offers for the source, in the order of the core. */
export const statsFor = (s: PostSource): readonly PostStat[] => (s.categories ? CAT_STATS : POST_STATS);

/** The statistics written: the ones chosen, else the defaults of the kind (a continuous variable — with the probability once thresholds are typed, as the core does). */
export function effectiveStats(s: PostSource, chosen: readonly PostStat[] | null, thresholds = ""): PostStat[] {
  if (chosen) return statsFor(s).filter((k) => chosen.includes(k));
  if (s.categories) return ["prob", "mode"];
  return thresholds.trim() ? ["mean", "std", "p10", "p50", "p90", "prob"] : ["mean", "std", "p10", "p50", "p90"];
}

/** «0,5; 1; 2,5» → the thresholds (≤ 20, distinct); the words under the field otherwise. */
export function postThresholds(text: string): number[] | Issue {
  const parts = text.split(/[;\n]/).map((x) => x.trim()).filter((x) => x !== "");
  if (parts.length > THRESHOLDS_MAX) return bad("thresholds", `Порогов больше ${THRESHOLDS_MAX}.`, `Оставьте не больше ${THRESHOLDS_MAX}.`);
  const out: number[] = [];
  for (let i = 0; i < parts.length; i++) {
    const v = parseNumber(parts[i]);
    if (v === null) return bad(`thresholds.${i}`, "Порог: не число.", "Введите числа через «;», например 0,5; 1; 2,5.");
    if (out.includes(v)) return bad("thresholds", `Порог ${numberText(v)} задан дважды.`, "Оставьте один из них.");
    out.push(v);
  }
  return out;
}

const PREFIX_RE = /^[\p{L}\p{N}_]{1,40}$/u;

/** The body of `POST /simulations/{id}/post` (what is not typed is absent: the core gives its defaults); the words under the field otherwise. */
export function postBodyOf(p: Pick<PostState, "prefix" | "stats" | "thresholds" | "replace">, src: PostSource, modelVersion: number): PostBody | Issue {
  const prefix = p.prefix.trim();
  if (prefix && !PREFIX_RE.test(prefix)) return bad("prefix", prefix.length > PREFIX_MAX ? `Префикс полей длиннее ${PREFIX_MAX} знаков.` : "Префикс полей: допустимы буквы, цифры и «_».", "Например, AU_SGS_.");
  const stats = effectiveStats(src, p.stats, p.thresholds);
  if (!stats.length) return bad("stats", "Не выбрано ни одной статистики.", "Отметьте хотя бы одну.");
  const body: PostBody = { expected_version: modelVersion, ...(prefix ? { prefix } : {}), ...(p.stats ? { stats } : {}), ...(p.replace ? { replace: true } : {}) };
  if (src.categories) {
    if (p.thresholds.trim()) return bad("thresholds", "У категориального моделирования порогов нет.", "Уберите пороги.");
    return body;
  }
  if (stats.includes("prob")) {
    const t = postThresholds(p.thresholds);
    if (isIssue(t)) return t;
    if (!t.length) return bad("thresholds", "Для вероятности превышения не заданы пороги.", "Задайте хотя бы один порог.");
    return { ...body, stats, thresholds: t };
  }
  return body;
}

/** Python's `format(t, "g")`: six significant digits, no trailing zeros, the exponent («1e-05», «1e+06», «1.23457e+06») below 1e-4 and from 1e6. */
export function formatG(t: number): string {
  if (Number.isNaN(t)) return "nan";
  if (!Number.isFinite(t)) return t < 0 ? "-inf" : "inf";
  if (t === 0) return Object.is(t, -0) ? "-0" : "0";
  const [mant, exp] = t.toExponential(5).split("e");
  const x = Number(exp);
  const trim = (v: string): string => (v.includes(".") ? v.replace(/0+$/, "").replace(/\.$/, "") : v);
  if (x < -4 || x >= 6) return `${trim(mant)}e${x < 0 ? "-" : "+"}${String(Math.abs(x)).padStart(2, "0")}`;
  return trim(t.toFixed(5 - x));
}

/** `PROB_GT_<t>`: «format(t, "g")» of the core with «.» → «_» (the name of a field has no dot; the sign and the exponent stay). */
export const thresholdName = (t: number): string => formatG(t).replace(".", "_");

/** The names of the fields the post-processing will write (what the window shows before the run; the core's answer is the truth). */
export function fieldNames(prefix: string, stats: readonly PostStat[], thresholds: readonly number[], cats: PostSource["categories"]): string[] {
  const out: string[] = [];
  if (cats) {
    if (stats.includes("prob")) out.push(...cats.codes.map((c) => `${prefix}PROB_${c}`));
    if (stats.includes("mode")) out.push(`${prefix}MODE`);
    return out;
  }
  for (const k of ["mean", "std", "p10", "p50", "p90"] as const) if (stats.includes(k)) out.push(`${prefix}${k.toUpperCase()}`);
  if (stats.includes("prob")) out.push(...thresholds.map((t) => `${prefix}PROB_GT_${thresholdName(t)}`));
  return out;
}

/** The fields of the list that the model has already (the question «Заменить поля» comes before the run). */
export const existingFields = (modelFields: readonly string[], names: readonly string[]): string[] => names.filter((n) => modelFields.includes(n));
