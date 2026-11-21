/**
 * The bottom tab «Статистика» (ТЗ 7.29; ADR 0019 §7; перенос `StatsPage.tsx` BF): on the left the analysis, the
 * set, the table and the component; on the right the parameters and the result of the analysis. Variograms, the
 * variogram map and the uncertainty map are of Э16: their buttons are unavailable with that stage. The points
 * are the middles of the intervals in plan (X, Y); more than 20 000 intervals — a refusal with the advice to
 * composite. Results are not stored: they are computed on request and dropped on an edit of the data.
 */
import { useEffect, useState } from "react";
import { errorText } from "../../shell/props";
import { loadFields, type StatsFields } from "../charts/api";
import { tablesStore } from "../tables/state";
import { BootstrapPanel } from "./BootstrapPanel";
import { STATS_BUTTONS } from "./commands";
import { DeclusPanel } from "./DeclusPanel";
import { analysisStage, componentChoices, isTabAnalysis, pickContext } from "./forms";
import { NscorePanel } from "./NscorePanel";
import { Field, useWhole } from "./Parts";
import { setAnalysis, setContext, statsStore, tabSetId } from "./state";
import css from "./Stats.module.css";

interface Loaded {
  key: string;
  fields: StatsFields | null;
  error: string | null;
}

export function StatsPanel() {
  const s = useWhole(statsStore);
  const t = useWhole(tablesStore);
  const setId = tabSetId(s);
  const key = JSON.stringify([setId, s.table, s.rev]);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  useEffect(() => {
    if (setId === null) return;
    let live = true;
    loadFields(setId, s.table).then(
      (fields) => live && setLoaded({ key, fields, error: null }),
      (e: unknown) => live && setLoaded({ key, fields: null, error: errorText(e) }),
    );
    return () => {
      live = false;
    };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps -- `key` holds setId, table and rev
  const now = loaded?.key === key ? loaded : null;
  const fields = now?.fields ?? null;
  const ctx = setId !== null && fields?.table ? pickContext(setId, fields, s.component) : null;
  const choices = fields ? componentChoices(fields) : [];
  const unit = fields?.numeric.find((f) => f.name === ctx?.field)?.unit;
  return (
    <div className={css.tab} data-testid="stats-tab">
      <aside className={css.side}>
        <div className={css.analyses} role="tablist" aria-label="Анализ">
          {STATS_BUTTONS.map((b) => {
            const stage = analysisStage(b.analysis);
            const on = b.analysis === s.analysis;
            return (
              <button key={b.analysis} type="button" role="tab" aria-selected={on} className={on ? css.analysisOn : css.analysis} disabled={!!stage} title={stage ?? undefined} onClick={() => isTabAnalysis(b.analysis) && setAnalysis(b.analysis)}>
                {b.label}
                {stage && <span className={css.stage}>Э16</span>}
              </button>
            );
          })}
        </div>
        <Field label="Набор скважин">
          <select aria-label="Набор скважин" value={setId ?? ""} onChange={(e) => setContext({ setId: e.target.value === "" ? null : Number(e.target.value) })}>
            {setId === null && <option value="">—</option>}
            {t.sets.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
                {x.id === t.setId ? " (активный)" : ""}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Таблица">
          <select aria-label="Таблица" value={fields?.table ?? ""} disabled={!fields?.tables.length} onChange={(e) => setContext({ table: e.target.value })}>
            {(fields?.tables ?? []).map((x) => (
              <option key={x.key} value={x.key}>
                {x.name} ({x.rows.toLocaleString("ru-RU")})
              </option>
            ))}
          </select>
        </Field>
        <Field label="Компонент">
          <select aria-label="Компонент" value={ctx?.field ?? ""} disabled={!choices.length} onChange={(e) => setContext({ component: e.target.value })}>
            {ctx?.field && !choices.some((c) => c.name === ctx.field) && <option value={ctx.field}>{ctx.field}</option>}
            {choices.map((c) => (
              <option key={c.name} value={c.name}>
                {c.label}
              </option>
            ))}
          </select>
        </Field>
        <p className={css.note} title="Интервал — точка в середине на траектории скважины; декластеризация — в плане (X, Y)">
          {unit ? `${unit} · ` : ""}по серединам интервалов, в плане. Не больше 20 000 интервалов.
        </p>
      </aside>
      <section className={css.main}>
        {setId === null ? (
          <p className={css.note}>Нет активного набора скважин: выберите набор в дереве проекта.</p>
        ) : now?.error ? (
          <p className={css.problemText}>{now.error}</p>
        ) : !fields ? (
          <p className={css.note}>Загрузка…</p>
        ) : !ctx ? (
          <p className={css.note}>В наборе нет таблиц проб: импортируйте опробование.</p>
        ) : !ctx.field ? (
          <p className={css.note}>В таблице нет числовых полей.</p>
        ) : s.analysis === "declus" ? (
          <DeclusPanel ctx={ctx} />
        ) : s.analysis === "nscore" ? (
          <NscorePanel ctx={ctx} />
        ) : (
          <BootstrapPanel ctx={ctx} />
        )}
      </section>
    </div>
  );
}
