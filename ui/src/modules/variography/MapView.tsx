/**
 * The tab «Карта» of the window «Вариография» (ТЗ 8.6.4 «Карта вариограмм»; ADR 0022 §5, §15, decision 8) and what
 * it shares with «Веер»: the parameters of the fan and of the map (8.6.4, blank — the default of the core), the job
 * `variography.map` in the plane of the axes of the model (a1, a2; the normal a3) — started by «Рассчитать веер и
 * карту», the previous unfinished one cancelled, its stages, `sampled`, the refusal; the map (`VarMapChart`: the plan
 * seen from above, a1 up, a2 to the left; an empty cell hatched, never the colour of zero). A click on the map or on a
 * ray of the rose turns the model: its a1 goes to that direction of the plane (`fan.turn`; «общие оси» — every
 * structure), the axes are computed again (the preview, then the job after the first «Рассчитать»).
 */
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { ChartToolbar } from "../../charts/core/ChartToolbar";
import { mapCsv } from "../../charts/variogram/scale";
import { VarMapChart } from "../../charts/variogram/VarMapChart";
import css from "../../shell/PropsParts.module.css";
import type { Started } from "../composites/api";
import { cancelJob, jobsStore, refresh as refreshJobs } from "../jobs/state";
import { frameOf, type Angles } from "../../viz/ellipsoid/frame";
import type { Vec3 } from "../../viz/core/vec3";
import { sampledText, startMap } from "./api";
import { axesOf, readRefusal, stagesText, type Refusal } from "./calc";
import { planeText, readMapCalc, samePlane, thetaInPlane, turnModel, type MapCalc } from "./mapCalc";
import { checkValues, mapBody, MAP_KEYS, markEdit, RANGES, setModel, setWork, spanText, valuesFromDefaults, workOf, type Work } from "./state";
import own from "./Variography.module.css";

const deg = (x: number) => `${x.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}°`;

/** «Аз 30° · Пог 10°» of a unit vector (azimuth clockwise from north, plunge positive down). */
export function worldText(u: Vec3): string {
  const az = (((Math.atan2(u[0], u[1]) * 180) / Math.PI) % 360 + 360) % 360;
  const plunge = (Math.asin(Math.max(-1, Math.min(1, -u[2]))) * 180) / Math.PI;
  return `Аз ${deg(Math.abs(az - 360) < 1e-9 ? 0 : az + 0)} · Пог ${deg(Math.abs(plunge) < 1e-9 ? 0 : plunge)}`;
}

/** The world labels of a1 and a2 of a plane (the titles of the rose and the map). */
export function planeAxes(p: Angles): { a1: string; a2: string } {
  const f = frameOf(p);
  return { a1: worldText(f.u1), a2: worldText(f.u2) };
}

/** θ of the a1 of the model in the plane of the calculation (null — no structure, or off the plane). */
export function selectedTheta(w: Pick<Work, "model">, m: Pick<MapCalc, "plane">): number | null {
  return w.model.structures.length ? thetaInPlane(m.plane, axesOf(w.model)) : null;
}

/** A click at θ of the plane of the calculation: the model turned (its a1 to that direction). */
export function turnTo(winId: string, m: Pick<MapCalc, "plane" | "sigma2">, theta: number): void {
  const w = workOf(winId);
  if (!w) return;
  const lag = Number(w.result?.params.lag ?? w.defaults?.defaults.lag ?? 0);
  const nlag = Number(w.result?.params.nlag ?? 15);
  markEdit();
  setModel(winId, turnModel(w.model, m.plane, theta, { sigma2: m.sigma2, range: lag > 0 ? (lag * nlag) / 2 : undefined }));
}

export interface MapJob {
  live: { id: number; status: string; message: string | null; progress: number } | null;
  error: Refusal | null;
  busy: boolean;
  run: (body: Record<string, unknown>) => Promise<void>;
  cancel: () => void;
}

/** The job `variography.map` of a window (kept while the window is open: the window calls it once). */
export function useMapJob(winId: string): MapJob {
  const [job, setJob] = useState<{ job: Started; body: string } | null>(null);
  const [error, setError] = useState<Refusal | null>(null);
  const [busy, setBusy] = useState(false);
  const ref = useRef(job);
  ref.current = job;
  const alive = useRef(true); // the window open; closed — a job started after it is cancelled at once
  const seq = useRef(0); // the newest «Рассчитать» only
  const get = () => (job ? (jobsStore.get().jobs.find((j) => j.source === job.job.source && j.id === job.job.id) ?? null) : null);
  const live = useSyncExternalStore(jobsStore.subscribe, get, get);
  useEffect(() => {
    if (!job || !live) return;
    if (live.status === "done" && live.result) {
      setWork(winId, { mapCalc: readMapCalc(live.result), mapFor: job.body });
      setJob(null);
    } else if (live.status === "failed") {
      setError(readRefusal(live.error ?? { message: "Расчёт не выполнен." }));
      setJob(null);
    } else if (live.status === "cancelled") setJob(null);
  }, [live, job]);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false; // the window closed
      if (ref.current) void cancelJob(ref.current.job).catch(() => undefined);
    };
  }, []);
  return {
    live: job && live ? live : null,
    error,
    busy,
    async run(body) {
      if (ref.current) void cancelJob(ref.current.job).catch(() => undefined); // the previous one is not wanted any more
      const mine = ++seq.current;
      setBusy(true);
      setError(null);
      try {
        const started = await startMap(body);
        if (!alive.current || mine !== seq.current) return void cancelJob(started).catch(() => undefined); // closed or overtaken meanwhile
        ref.current = { job: started, body: JSON.stringify(body) };
        setJob(ref.current);
        await refreshJobs().catch(() => undefined);
      } catch (e) {
        if (alive.current && mine === seq.current) setError(readRefusal(e));
      } finally {
        if (alive.current && mine === seq.current) setBusy(false);
      }
    },
    cancel() {
      if (ref.current) void cancelJob(ref.current.job).catch(() => undefined);
    },
  };
}

/** The body of `/map` of the window: the plane — the axes the axes are computed by (of the first structure). */
export const windowMapBody = (w: Work) => mapBody(w.data, w.values, axesOf(w.model));

/** The parameters of the fan and the map, «Рассчитать веер и карту», the state of the job and of the result. */
export function MapRun({ work, winId, job }: { work: Work; winId: string; job: MapJob }) {
  const defaults = work.defaults ? valuesFromDefaults(work.defaults.defaults) : {};
  const checked = checkValues(work.values, MAP_KEYS);
  const built = windowMapBody(work);
  const cur = built.body ? JSON.stringify(built.body) : null;
  const m = work.mapCalc ?? null;
  const set = (k: string, v: string) => setWork(winId, (w) => ({ values: { ...w.values, [k]: v } }));
  const notes: string[] = [];
  if (m) {
    notes.push(planeText(m.plane));
    if (m.sampled) notes.push(sampledText(m.sampled)!);
    const st = stagesText(m);
    if (st) notes.push(st);
    if (work.mapFor !== cur) notes.push(samePlane(m.plane, axesOf(work.model)) ? "Данные или параметры изменены: показан прежний расчёт" : "Оси модели повернуты: показан расчёт в прежней плоскости — рассчитайте снова");
    notes.push(...m.warnings);
  }
  return (
    <div className={own.notes} data-testid="variography-map-run">
      <div className={own.row}>
        {MAP_KEYS.map((k) => (
          <label key={k} className={own.withFix} title={`${RANGES[k].label}: ${spanText(RANGES[k])}; пусто — по умолчанию`}>
            <span className={css.note}>{RANGES[k].label}</span>
            <input className={`${css.input} ${own.cell}${checked.errors[k] ? ` ${own.bad}` : ""}`} aria-label={RANGES[k].label} placeholder={defaults[k] ?? ""} value={work.values[k] ?? ""} onChange={(e) => set(k, e.target.value)} />
          </label>
        ))}
        <button type="button" className={css.primary} data-testid="variography-map-calc" disabled={!built.body || job.busy} onClick={() => built.body && void job.run(built.body)}>
          Рассчитать веер и карту
        </button>
      </div>
      {built.reason && <p className={css.error}>{built.reason}</p>}
      {job.live && (
        <p className={css.note}>
          Задание №{job.live.id} {job.live.status === "queued" ? "в очереди" : "идёт"}
          {job.live.message ? `: ${job.live.message}` : ""}
          {job.live.progress > 0 ? ` (${Math.round(job.live.progress * 100)} %)` : ""}{" "}
          <button type="button" className={css.btn} onClick={job.cancel}>
            Отменить
          </button>
        </p>
      )}
      {job.error && (
        <p className={css.error} role="alert">
          Веер и карта не рассчитаны: {job.error.text}
        </p>
      )}
      {notes.map((t) => (
        <p key={t} className={m?.warnings.includes(t) ? css.warn : css.note}>
          {t}
        </p>
      ))}
    </div>
  );
}

export function MapView({ work, winId, job }: { work: Work; winId: string; job: MapJob }) {
  const ref = useRef<SVGSVGElement | null>(null);
  const m = work.mapCalc ?? null;
  return (
    <div className={own.charts} data-testid="variography-map">
      <MapRun work={work} winId={winId} job={job} />
      {m?.map ? (
        <figure className={own.chart}>
          <VarMapChart ref={ref} map={m.map} sigma2={m.sigma2} unit="м" axes={planeAxes(m.plane)} selected={selectedTheta(work, m)} onPick={(t) => turnTo(winId, m, t)} />
          <ChartToolbar svgRef={ref} fileName={`Карта вариограммы ${work.data.field ?? ""}`.trim()} csv={() => mapCsv(m.map!)} />
          <p className={css.note}>Щелчок по карте — ось a1 модели в этом направлении плоскости</p>
        </figure>
      ) : (
        <p className={own.empty}>{m ? "Карта не заказана в расчёте" : "Карта не рассчитана: нажмите «Рассчитать веер и карту»"}</p>
      )}
    </div>
  );
}
