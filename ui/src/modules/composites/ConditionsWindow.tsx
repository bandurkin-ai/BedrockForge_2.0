/**
 * The window «Анализ кондиций» (ТЗ 7.35 Д-26 «Анализ кондиций», Т8.2-20; ADR 0018 §12, решение 23): the source
 * (set, table, grade field, domain), the lists Сб, Мрт, Мпп (at most 200 variants — the reason is shown and nothing
 * starts), the mode, the thickness, the gaps, samples left out by a field (`exclude`); «Применить» → the job; the
 * table of the variants in the form И1, сл. 24 — число интервалов, среднее содержание = Σ MC / Σ M, Σ M, средняя M,
 * Σ MC — with the grade of the прирезка between neighbouring cutoffs (сл. 26) and the mark of neighbours whose
 * Σ M differ not by 10–15 % (сл. 25); «Сохранить CSV…» and «Копировать». The table is that of the finished job,
 * else of the last analysis of the table and field in the set (`dh_results.report`).
 */
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { bridge, saveFile } from "../../bridge";
import { errorText } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import { jobsStore } from "../jobs/state";
import { tablesStore } from "../tables/state";
import own from "./Composites.module.css";
import type { ResultRow } from "./api";
import { staleText } from "./results";
import { SourcePicker } from "./SourcePicker";
import { JobView, ParamForm, ToolFooter, useTool } from "./ToolWindow";
import { useSetResults } from "./TopcutWindow";
import { cellText, conditions, MAX_VARIANTS, toVariants, variantColumns, variantCount, variantCountText, variantRows, variantsCsv, variantsFileName, variantsUnit, type Variant } from "./tools/conditions";

const CELL = { padding: "1px 6px", textAlign: "right" as const, whiteSpace: "nowrap" as const };

/** The table of the variants with its export; `field` names the file. */
export function VariantsTable({ variants, unit, field }: { variants: readonly Variant[]; unit: string | null; field: string | null }) {
  const rows = useMemo(() => variantRows(variants), [variants]);
  const cols = variantColumns(unit);
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null);
  if (!rows.length) return <p className={css.note}>В отчёте нет вариантов.</p>;
  const marked = rows.filter((r) => r.mark).length;
  const save = async () => {
    setNote(null);
    try {
      const path = await saveFile(variantsFileName(field), "csv", variantsCsv(rows, unit));
      if (path) setNote({ text: `Сохранено: ${path}`, error: false });
    } catch (e) {
      setNote({ text: errorText(e), error: true });
    }
  };
  const copy = () => {
    const b = bridge();
    if (!b) return setNote({ text: "Копирование доступно только в приложении", error: true });
    b.copyText(variantsCsv(rows, unit).replace(/;/g, "\t"));
    setNote({ text: "Таблица скопирована", error: false });
  };
  return (
    <div data-testid="conditions-variants">
      <div className={css.tableWrap} style={{ overflowX: "auto" }}>
        <table className={own.summary} data-testid="conditions-table">
          <caption>Варианты кондиций (И1, сл. 24): {rows.length}</caption>
          <thead>
            <tr>
              {cols.map((c) => (
                <th key={c.key} scope="col">
                  {c.title}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} data-testid="conditions-row" data-marked={r.mark ? "1" : undefined} title={r.mark ?? undefined} style={{ borderTop: r.first ? "2px solid var(--border-strong)" : undefined }}>
                {cols.map((c) => (
                  <td key={c.key} style={{ ...CELL, color: r.mark && c.key === "step_pct" ? "var(--warn)" : undefined, fontWeight: r.mark && c.key === "step_pct" ? 600 : undefined }}>
                    {cellText(r, c.key)}
                    {r.mark && c.key === "step_pct" ? " ⚠" : ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className={css.note}>
        Содержание в прирезке — (Σ MC₁ − Σ MC₂) / (Σ M₁ − Σ M₂) к соседнему меньшему борту при тех же Мрт и Мпп (И1, сл. 26). Изменение Σ M ≈ изменение запасов руды, Σ MC —
        металла (сл. 23).
        {marked > 0 && ` Помечено вариантов: ${marked} — соседние борта различаются по Σ M не на 10–15 % (сл. 25); подробность — в подсказке строки.`}
      </p>
      <div className={css.inline}>
        <span className={css.grow} />
        <button type="button" className={css.btn} onClick={copy} data-testid="conditions-copy">
          Копировать
        </button>
        <button type="button" className={css.btn} onClick={() => void save()} data-testid="conditions-save">
          Сохранить CSV…
        </button>
      </div>
      {note && (
        <p className={note.error ? css.error : css.note} role={note.error ? "alert" : undefined}>
          {note.text}
        </p>
      )}
    </div>
  );
}

/** The last analysis of the table and field in the set (the newest first). */
export function lastAnalysis(results: readonly ResultRow[], table: string | null, field: string | null): ResultRow | null {
  const hits = results.filter((r) => r.op === "conditions" && (table === null || String(r.params?.table ?? "") === table) && (field === null || r.params?.field === field));
  return hits.sort((a, b) => b.id - a.id)[0] ?? null;
}

export function ConditionsWindow({ winId, args = {} }: { winId: string; args?: Record<string, unknown> }) {
  const t = useTool(conditions, args);
  const [activeSet] = useState(() => tablesStore.get().setId); // the set active when the window opened
  const [fresh, setFresh] = useState<{ variants: Variant[]; unit: string | null } | null>(null);
  const job = t.job;
  const get = () => (job ? jobsStore.get().jobs.find((j) => j.source === job.source && j.id === job.id) : undefined);
  const live = useSyncExternalStore(jobsStore.subscribe, get, get); // a server snapshot too: the window renders statically in tests
  const results = useSetResults(t.src.setId);
  const field = t.src.fields[0] ?? null;
  const last = lastAnalysis(results, t.src.table, field);
  const count = variantCount(t.values);

  useEffect(() => {
    if (!job || live?.status !== "done") return;
    const v = toVariants(live.result);
    setFresh(v.length ? { variants: v, unit: variantsUnit(live.result) } : null);
    t.setJob(null); // back to the form: the table is shown under it
  }, [job, live?.status]);

  if (job) return <JobView title={conditions.title} job={job} winId={winId} onBack={() => t.setJob(null)} />;
  const shown = fresh ?? (last ? { variants: toVariants(last.report), unit: variantsUnit(last.report) } : null);
  const tooMany = count.total !== null && count.total > MAX_VARIANTS;
  return (
    <div className={css.win} data-testid="composites-conditions">
      <div className={css.winBody}>
        <p className={css.note}>{conditions.note}</p>
        <SourcePicker needs={conditions.source} value={t.src} onChange={t.setSrc} onFields={t.setFields} activeSet={activeSet} />
        <ParamForm params={conditions.params} values={t.values} errors={t.checked.errors} fields={t.fields} onChange={t.setValues} />
        <p className={tooMany ? css.warn : css.note} data-testid="conditions-count" role={tooMany ? "alert" : undefined}>
          {variantCountText(count)}
          {tooMany && ` — больше ${MAX_VARIANTS}: расчёт не запускается, сократите списки Сб, Мрт или Мпп.`}
        </p>
        {t.error && (
          <p className={css.error} role="alert">
            {t.error}
          </p>
        )}
        {shown && (
          <fieldset className={css.group} data-testid="conditions-result">
            <legend>{fresh ? "Результат анализа" : `Последний анализ${last ? ` · ${last.created_at ? new Date(last.created_at).toLocaleString("ru-RU") : `№${last.id}`}` : ""}`}</legend>
            {!fresh && last?.stale && <p className={css.warn}>Результат {staleText(last)}. «Применить» посчитает варианты заново.</p>}
            <VariantsTable variants={shown.variants} unit={shown.unit} field={field} />
          </fieldset>
        )}
      </div>
      <ToolFooter winId={winId} reason={t.checked.reason} busy={t.busy} onApply={() => void t.apply()} extra="Результат — таблица вариантов без новой таблицы набора; сохраняется в «Обработке проб»." />
    </div>
  );
}
