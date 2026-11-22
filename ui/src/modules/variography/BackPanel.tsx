/**
 * The block «Обратное преобразование» of the tab «Нормальные оценки» (ТЗ 8.6.4; ADR 0022 §8, §15, decision 12;
 * e16-api.md `POST /back`): the model of the window in the units of Y (normal scores) → the model of Z by Hermite
 * polynomials (200 nodes, 30 terms) over the table of normal scores of the calculation; the tails of `backtr`
 * (linear by default). Shown: the model of Z (C0, the structures), the share Σφ_n² / Var(Z) (< 0,95 — the warning
 * of the core), the residual, and the curves of the three axes — γ of Z by Hermite (points) with the model of Z
 * (the formulas of `model.ts`). The answer goes with «Сохранить» (`back`); an edit of the model drops it.
 */
import { useRef, useState } from "react";
import { ChartToolbar } from "../../charts/core/ChartToolbar";
import { curvesCsv } from "../../charts/variogram/scale";
import { VariogramChart, type VariogramSeries } from "../../charts/variogram/VariogramChart";
import css from "../../shell/PropsParts.module.css";
import { parseNumber } from "../composites/params";
import { backTransform, readBack, type BackAnswer, type Tails } from "./api";
import { readRefusal, type Refusal } from "./calc";
import { axisOf, MODEL_TITLES, modelBody, STRUCTURE_TITLES } from "./model";
import { setWork, workOf, type Work } from "./state";
import own from "./Variography.module.css";

export type LowerTail = "linear" | "power" | "none";
export type UpperTail = "linear" | "power" | "hyperbolic" | "none";
export const LOWER_TAILS: Record<LowerTail, string> = { linear: "линейный", power: "степенной", none: "без хвоста" };
export const UPPER_TAILS: Record<UpperTail, string> = { linear: "линейный", power: "степенной", hyperbolic: "гиперболический", none: "без хвоста" };

export interface TailsForm {
  ltail: LowerTail;
  utail: UpperTail;
  ltpar: string;
  utpar: string;
}

export const DEFAULT_TAILS: TailsForm = { ltail: "linear", utail: "linear", ltpar: "", utpar: "" };

/** The tails of the request, or why not: a power tail needs ω > 0, a hyperbolic one ω ≥ 1 (`check_tails`). */
export function tailsBody(t: TailsForm): { tails: Tails | null; error: string | null } {
  const out: Tails = { ltail: t.ltail === "none" ? null : t.ltail, utail: t.utail === "none" ? null : t.utail };
  if (t.ltail === "power") {
    const v = parseNumber(t.ltpar);
    if (v === null || !(v > 0)) return { tails: null, error: "Нижний хвост: для степенного нужен показатель больше 0" };
    out.ltpar = v;
  }
  if (t.utail === "power" || t.utail === "hyperbolic") {
    const v = parseNumber(t.utpar);
    const least = t.utail === "hyperbolic" ? 1 : 0;
    if (v === null || !(t.utail === "hyperbolic" ? v >= least : v > least)) return { tails: null, error: `Верхний хвост: для ${t.utail === "hyperbolic" ? "гиперболического нужен показатель не меньше 1" : "степенного нужен показатель больше 0"}` };
    out.utpar = v;
  }
  if (t.ltail === "linear" && t.utail === "linear") return { tails: {}, error: null }; // the default of the core
  return { tails: out, error: null };
}

/** Why the back-transform cannot go now; null — it can. */
export function backReason(w: Pick<Work, "data" | "result" | "expKey" | "model">): string | null {
  if (w.data.transform !== "nscore") return "Обратное преобразование — для расчёта с нормальными оценками: выберите преобразование «нормальные оценки» в данных";
  if (!w.expKey || w.result?.transform !== "nscore") return "Сначала рассчитайте вариограммы нормальных оценок (кнопка «Рассчитать»)";
  if (w.model.type === "pairwise") return "Парная относительная модель не преобразуется";
  if (!w.model.structures.length && !(w.model.c0 > 0)) return "Задайте или подберите модель нормальных оценок";
  return null;
}

const f = (x: number | null) => (x === null ? "—" : x.toLocaleString("ru-RU", { maximumSignificantDigits: 4 }));

/** The curves of an axis: γ of Z by Hermite as points (N unknown — no counts), the model of Z along the axis. */
export function backSeries(b: BackAnswer): VariogramSeries[] {
  return b.curves.map((c) => ({
    label: `Ось ${c.axis}: γ(Z)`,
    curve: { lags: c.h.map((h) => h ?? NaN), h: c.h, n: c.h.map(() => 0), gamma: c.gamma_z, rho: c.h.map(() => null), pairwise: c.h.map(() => null), n_pw: c.h.map(() => 0), few: c.h.map(() => false) },
    u: axisOf(b.model, (c.axis === 2 || c.axis === 3 ? c.axis : 1) as 1 | 2 | 3),
  }));
}

export function BackPanel({ work, winId, run = backTransform }: { work: Work; winId: string; run?: typeof backTransform }) {
  const [tails, setTails] = useState<TailsForm>(DEFAULT_TAILS);
  const [error, setError] = useState<Refusal | null>(null);
  const [busy, setBusy] = useState(false);
  const chart = useRef<SVGSVGElement | null>(null);
  const reason = backReason(work);
  const t = tailsBody(tails);
  const back = work.back ? readBack(work.back) : null;
  const go = async () => {
    if (reason || !t.tails || !work.expKey) return;
    setBusy(true);
    setError(null);
    try {
      const got = await run(work.expKey, modelBody(work.model), t.tails);
      if (workOf(winId)) setWork(winId, { back: got.raw });
    } catch (e) {
      setError(readRefusal(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <fieldset className={css.group} data-testid="variography-back">
      <legend>Обратное преобразование (Эрмит)</legend>
      <div className={own.row}>
        <label className={own.withFix}>
          <span className={css.note}>Нижний хвост</span>
          <select className={css.input} aria-label="Нижний хвост" value={tails.ltail} onChange={(e) => setTails({ ...tails, ltail: e.target.value as LowerTail })}>
            {(Object.keys(LOWER_TAILS) as LowerTail[]).map((k) => (
              <option key={k} value={k}>
                {LOWER_TAILS[k]}
              </option>
            ))}
          </select>
        </label>
        {tails.ltail === "power" && <input className={`${css.input} ${own.cell}`} aria-label="Показатель нижнего хвоста" placeholder="ω" value={tails.ltpar} onChange={(e) => setTails({ ...tails, ltpar: e.target.value })} />}
        <label className={own.withFix}>
          <span className={css.note}>Верхний хвост</span>
          <select className={css.input} aria-label="Верхний хвост" value={tails.utail} onChange={(e) => setTails({ ...tails, utail: e.target.value as UpperTail })}>
            {(Object.keys(UPPER_TAILS) as UpperTail[]).map((k) => (
              <option key={k} value={k}>
                {UPPER_TAILS[k]}
              </option>
            ))}
          </select>
        </label>
        {(tails.utail === "power" || tails.utail === "hyperbolic") && <input className={`${css.input} ${own.cell}`} aria-label="Показатель верхнего хвоста" placeholder="ω" value={tails.utpar} onChange={(e) => setTails({ ...tails, utpar: e.target.value })} />}
        <button type="button" className={css.btn} data-testid="variography-back-run" disabled={reason !== null || !t.tails || busy} title={reason ?? t.error ?? "Модель в единицах Z по модели нормальных оценок"} onClick={() => void go()}>
          Преобразовать
        </button>
      </div>
      {(reason ?? t.error) && <p className={css.note}>{reason ?? t.error}</p>}
      {error && (
        <p className={css.error} role="alert">
          {error.text}
        </p>
      )}
      {back && (
        <>
          <p className={css.note} data-testid="variography-back-share">
            Модель Z ({MODEL_TITLES[back.model.type]}): C0 = {f(back.model.c0)}
            {back.model.structures.map((s, i) => `; ${i + 1}: ${STRUCTURE_TITLES[s.type]}, c = ${f(s.c)}, a1 = ${f(s.a1)}, a2 = ${f(s.a2)}, a3 = ${f(s.a3)} м`).join("")}. Доля Σφ² / Var(Z) = {back.share === null ? "—" : `${(back.share * 100).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} %`}, невязка {f(back.residual)}
          </p>
          {back.warning && <p className={css.warn}>{back.warning}</p>}
          {back.curves.length > 0 && (
            <figure className={own.chart}>
              <VariogramChart ref={chart} series={backSeries(back)} type="semivariogram" model={back.model} sigma2={work.result?.sigma2_z ?? null} title="Модель в единицах Z" width={520} height={300} counts={false} />
              <ChartToolbar svgRef={chart} fileName={`Обратное преобразование ${work.data.field ?? ""}`.trim()} csv={() => curvesCsv(backSeries(back), "semivariogram")} />
            </figure>
          )}
        </>
      )}
    </fieldset>
  );
}
