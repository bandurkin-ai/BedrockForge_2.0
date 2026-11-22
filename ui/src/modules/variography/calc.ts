/**
 * The calculation of the window «Вариография» (ТЗ 8.6.4; ADR 0022 §15, decisions 13, 19; e16-api.md): what the window
 * shows of `job.result` of `variography.experimental` (the form of a calculation and `fresh`, `stages`) and of
 * `POST /preview` (the same form, `key: null`, `sampled`), the refusals (`job.error {code, message, hint}`; 422 of the
 * preview with `counts`), the series of the charts of the axes, and the runner: an edit sends the preview 200 ms
 * later (the core samples ≤ 20 000 points by the seed; the newest answer only), a drag waits for the release; the
 * job goes on «Рассчитать» and — after the first one — after every edit settles, the previous unfinished job
 * cancelled. Read against the real answers of the core (`core/tests/data/variography/window_answers.json`). No React.
 */
import { CoreError } from "../../shell/api";
import { errorText } from "../../shell/props";
import type { VariogramSeries } from "../../charts/variogram/VariogramChart";
import { curveDirection } from "../../charts/variogram/scale";
import type { Started } from "../composites/api";
import { readCalc, readCounts, sampledText, type CalcResult, type Counts, type DirectionCurve } from "./api";
import type { ModelType, VModel } from "./model";
import type { AxesAngles } from "./state";

/** The pause of the preview after an edit (decision 19). */
export const PREVIEW_DELAY_MS = 200;
/** The core samples the preview to this many points (`spec.PREVIEW_MAX`). */
export const PREVIEW_MAX = 20_000;

// ---- the answers ----

/** `job.result`: the form of a calculation with `fresh` (read from the cache) and `stages` (seconds). */
export interface JobCalc extends CalcResult {
  fresh: boolean;
  /** `{read, trajectories, nscore?, pairs, downhole?, write, total}`; a repeat has only the stages it read */
  stages: Record<string, number>;
}

export function readJobCalc(raw: unknown): JobCalc {
  const o = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const st = o.stages && typeof o.stages === "object" ? (o.stages as Record<string, unknown>) : {};
  const stages: Record<string, number> = {};
  for (const [k, v] of Object.entries(st)) if (typeof v === "number" && Number.isFinite(v)) stages[k] = v;
  return { ...readCalc(raw), fresh: o.fresh === true, stages };
}

export const STAGE_TITLES: Record<string, string> = {
  read: "чтение таблицы",
  trajectories: "траектории",
  nscore: "нормальные оценки",
  pairs: "пары",
  downhole: "вдоль скважин",
  fan: "веер",
  map: "карта",
  write: "запись",
};

const sec = (s: number) => `${s.toLocaleString("ru-RU", { maximumFractionDigits: s < 10 ? 2 : 1 })} с`;

/** «Расчёт 11,4 с: чтение таблицы 0,01 с · траектории 7,15 с · …»; from the cache — «Итог из кеша …». */
export function stagesText(r: Pick<JobCalc, "fresh" | "stages">): string | null {
  const parts = Object.keys(STAGE_TITLES)
    .filter((k) => k in r.stages)
    .map((k) => `${STAGE_TITLES[k]} ${sec(r.stages[k])}`);
  const total = r.stages.total;
  if (r.fresh) return `Итог из кеша: те же данные и параметры, расчёт не повторялся${total === undefined ? "" : ` (${sec(total)})`}`;
  if (!parts.length && total === undefined) return null;
  return `Расчёт${total === undefined ? "" : ` ${sec(total)}`}${parts.length ? `: ${parts.join(" · ")}` : ""}`;
}

/** The lines under the charts: what is shown, the sample, the warnings of the core. */
export function calcNotes(r: CalcResult | JobCalc, kind: Shown["kind"]): string[] {
  const out: string[] = [];
  if (kind === "preview") out.push(r.sampled ? `Предпросмотр: ${sampledText(r.sampled)!.toLowerCase()}; итог — после расчёта всех проб` : "Предпросмотр по всем пробам");
  else if (r.sampled) out.push(sampledText(r.sampled)!);
  if (kind === "stale") out.push("Данные или параметры изменены: показан прежний расчёт");
  if (kind === "job" && "stages" in r) {
    const t = stagesText(r);
    if (t) out.push(t);
  }
  out.push(...r.warnings);
  return out;
}

const f = (x: number) => x.toLocaleString("ru-RU");

/** «Точек: 452 из 480 (пропуск значения 28)»; the parts that are zero are not written. */
export function countsLine(c: Counts): string {
  const parts = ([[c.missing, "пропуск значения"], [c.no_position, "нет положения"], [c.outside_domain, "вне домена"], [c.no_weight, "без веса"]] as const)
    .filter(([n]) => n > 0)
    .map(([n, t]) => `${t} ${f(n)}`);
  return `Точек: ${f(c.used)} из ${f(c.rows)}${parts.length ? ` (${parts.join(", ")})` : ""}`;
}

export interface Refusal {
  /** the message and the hint */
  text: string;
  code: string | null;
  /** the counts of the points of a refusal of the data (422 of the preview) */
  counts: Counts | null;
  /** the key of the form the core refused («directions.0.az» as it is) */
  param: string | null;
}

/** A refusal of the preview or of `startExperimental` (a `CoreError`), or `job.error` of a failed job. */
export function readRefusal(e: unknown): Refusal {
  if (e instanceof CoreError) {
    const b = e.body ?? {};
    return { text: e.full, code: e.code, counts: b.counts && typeof b.counts === "object" ? readCounts(b.counts) : null, param: typeof b.param === "string" && b.param ? b.param : null };
  }
  const o = e && typeof e === "object" && !(e instanceof Error) ? (e as Record<string, unknown>) : null;
  if (o && typeof o.message === "string") {
    const hint = typeof o.hint === "string" && o.hint ? ` ${o.hint}` : "";
    return { text: `${o.message}${hint}`, code: typeof o.code === "string" ? o.code : null, counts: null, param: null };
  }
  return { text: errorText(e), code: null, counts: null, param: null };
}

// ---- the charts of the axes ----

/** The axes of `experimental`: the angles of the first structure of the model (the axes of 8.6.3), else zero. */
export function axesOf(m: Pick<VModel, "structures">): AxesAngles {
  const s = m.structures[0];
  return s ? { ang1: s.ang1, ang2: s.ang2, ang3: s.ang3 } : { ang1: 0, ang2: 0, ang3: 0 };
}

/** The type the charts plot: the model's, normal scores with that transform. */
export const chartType = (m: Pick<VModel, "type">, transform: "none" | "nscore"): ModelType => (transform === "nscore" ? "nscore" : m.type === "nscore" ? "semivariogram" : m.type);

const deg = (x: number) => `${x.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}°`;

/** «Ось 1 · Аз 30° · Пог 0°»; a direction of `directions` — «Направление 2 · …». */
export function curveLabel(d: Pick<DirectionCurve, "axis" | "az" | "plunge">, i: number): string {
  return `${d.axis === null ? `Направление ${i + 1}` : `Ось ${d.axis}`} · Аз ${deg(d.az)} · Пог ${deg(d.plunge)}`;
}

export const DOWNHOLE_LABEL = "Вдоль скважин";

/** One chart per direction (the model curve along it) and the downhole one (no model curve: its direction varies). */
export function axisSeries(r: CalcResult, model: VModel | null): { axes: VariogramSeries[]; downhole: VariogramSeries | null } {
  return {
    axes: r.directions.map((d, i) => ({ label: curveLabel(d, i), curve: d, u: curveDirection(d, model) })),
    downhole: r.downhole ? { label: DOWNHOLE_LABEL, curve: r.downhole, u: null } : null,
  };
}

/** The variance line of a chart: σ² of the data, 1 for normal scores (σ²(Z) is written under the form). */
export const chartSigma2 = (r: CalcResult): number | null => r.sigma2;

// ---- what is shown ----

export interface Shown {
  calc: CalcResult | JobCalc | null;
  /** job — the result of the current body; preview — its preview; stale — a calculation of other data */
  kind: "job" | "preview" | "stale" | null;
}

/** The result of the current body, else its preview, else the last calculation marked stale. */
export function pickShown(cur: string | null, result: CalcResult | null, resultFor: string | null, preview: CalcResult | null, previewFor: string | null): Shown {
  if (result && cur !== null && resultFor === cur) return { calc: result, kind: "job" };
  if (preview && cur !== null && previewFor === cur) return { calc: preview, kind: "preview" };
  if (result) return { calc: result, kind: resultFor === null ? "job" : "stale" };
  if (preview) return { calc: preview, kind: "stale" };
  return { calc: null, kind: null };
}

// ---- the runner ----

export type Clock = { set: (fn: () => void, ms: number) => unknown; clear: (t: unknown) => void };
const REAL_CLOCK: Clock = { set: (fn, ms) => setTimeout(fn, ms), clear: (t) => clearTimeout(t as ReturnType<typeof setTimeout>) };

export interface RunnerDeps {
  preview: (body: Record<string, unknown>) => Promise<CalcResult>;
  start: (body: Record<string, unknown>) => Promise<Started>;
  cancel: (job: Started) => Promise<void>;
  clock?: Clock;
  delay?: number;
}

export interface RunnerState {
  preview: CalcResult | null;
  /** the body (JSON) of the preview shown */
  previewFor: string | null;
  previewError: Refusal | null;
  previewBusy: boolean;
  /** the last job started and its body (JSON) */
  job: Started | null;
  jobFor: string | null;
  /** the job is queued or running (not finished yet) */
  jobLive: boolean;
  startError: Refusal | null;
  /** after the first «Рассчитать» an edit starts the job too */
  auto: boolean;
}

export interface Runner {
  state(): RunnerState;
  /** the body of the window changed (null — incomplete); while dragging nothing goes until the release */
  edit(body: Record<string, unknown> | null, dragging?: boolean): void;
  /** «Рассчитать»: the job now (the previous unfinished one cancelled) */
  run(body: Record<string, unknown>): Promise<void>;
  /** the job finished (done, failed, cancelled): not cancelled any more */
  finished(job: Started): void;
  /** the window closed: no more requests; the unfinished job cancelled */
  stop(): void;
}

export const INITIAL_RUNNER: RunnerState = { preview: null, previewFor: null, previewError: null, previewBusy: false, job: null, jobFor: null, jobLive: false, startError: null, auto: false };

export function createRunner(deps: RunnerDeps, onChange: (s: RunnerState) => void, initial: Partial<RunnerState> = {}): Runner {
  const clock = deps.clock ?? REAL_CLOCK;
  const delay = deps.delay ?? PREVIEW_DELAY_MS;
  let s: RunnerState = { ...INITIAL_RUNNER, ...initial };
  let alive = true;
  let timer: unknown = null;
  let seq = 0;
  let asked = ""; // the body of the last preview asked
  let pending: Record<string, unknown> | null = null;
  const set = (patch: Partial<RunnerState>) => {
    s = { ...s, ...patch };
    if (alive) onChange(s);
  };
  const same = (a: Started | null, b: Started) => a !== null && a.source === b.source && a.id === b.id;

  const start = async (body: Record<string, unknown>, key: string) => {
    if (s.jobLive && s.job) {
      const prev = s.job;
      void deps.cancel(prev).catch(() => undefined); // the previous one is not wanted any more
    }
    set({ jobFor: key, startError: null, jobLive: false });
    try {
      const job = await deps.start(body);
      if (!alive) return void deps.cancel(job).catch(() => undefined);
      if (s.jobFor !== key) return void deps.cancel(job).catch(() => undefined); // overtaken while it was asked
      set({ job, jobLive: true });
    } catch (e) {
      if (s.jobFor === key) set({ startError: readRefusal(e) });
    }
  };

  const settle = () => {
    timer = null;
    const body = pending;
    if (!body || !alive) return;
    const key = JSON.stringify(body);
    if (s.auto && key !== s.jobFor) void start(body, key);
    if (key === asked) return;
    asked = key;
    const mine = ++seq;
    set({ previewBusy: true });
    deps.preview(body).then(
      (a) => {
        if (alive && mine === seq) set({ preview: a, previewFor: key, previewError: null, previewBusy: false });
      },
      (e: unknown) => {
        if (alive && mine === seq) set({ previewError: readRefusal(e), previewFor: key, preview: null, previewBusy: false });
      },
    );
  };

  return {
    state: () => s,
    edit(body, dragging = false) {
      pending = body;
      if (timer !== null) clock.clear(timer);
      timer = null;
      if (!body) {
        ++seq; // an answer of an older body is not applied
        asked = "";
        if (s.previewBusy || s.preview || s.previewError) set({ previewBusy: false, previewError: null });
        return;
      }
      if (dragging) return; // the release sends it
      timer = clock.set(settle, delay);
    },
    async run(body) {
      if (timer !== null) clock.clear(timer);
      timer = null;
      pending = body;
      set({ auto: true });
      await start(body, JSON.stringify(body));
    },
    finished(job) {
      if (same(s.job, job) && s.jobLive) set({ jobLive: false });
    },
    stop() {
      if (timer !== null) clock.clear(timer);
      timer = null;
      if (s.jobLive && s.job) void deps.cancel(s.job).catch(() => undefined);
      alive = false;
    },
  };
}
