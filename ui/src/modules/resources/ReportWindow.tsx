/**
 * The window «Отчёт по ресурсам · <модель>» (ТЗ 8.10; ADR 0027 решения 16, 29): on the left the request — the model, the elements and their units,
 * the cut-off field and the cut-offs (a list or a range), the balance, the density, the restrictor (the filter, «Карьер», «Ниже рельефа», the
 * tree of rules), the keys of the group by dragging («Уступ» among them, up to 3), the curve, the template, the name; on the right the run (the
 * stage, «Отменить») and the summary of the result. «Предпросмотр» builds the report without writing anything (works in a project open for
 * reading), «Сохранить» writes the object «Отчёт по ресурсам»; both run the command `resources.report` through the registry (the log, the
 * notice, the question of the assistant). A wrong form is told under its field in Russian before anything is sent. Floating, not modal.
 * The table, the chart and the control of the result are the panel of the result (20.13): it reads `resourcesStore.run.result`.
 */
import { useMemo, useState } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { closeWindow } from "../../shell/windows";
import type { ObjectOption } from "../blocks/filters/RuleEditor";
import { filtersByName, filtersStore } from "../blocks/filters/state";
import { autoDensity } from "../blocks/report/state";
import { blocksStore } from "../blocks/state";
import { jobsStore, STATUS_TITLES } from "../jobs/state";
import { sceneObjects } from "../scene/objects";
import { DEFAULT_LIMITS } from "./api";
import { cancelReport, chooseModel } from "./bind";
import { argsOfForm } from "./form";
import { firstTotal, fmt, fmtCount, rowTitle, warningText } from "./format";
import { BalancePart, CurvePart, CutoffsPart, DensityPart, ElementsPart, GroupsPart, Issue, RestrictPart, TemplatePart, useSel } from "./ReportParts";
import mod from "./Resources.module.css";
import { modelFields, resourcesStore, setIssue } from "./state";

export function ReportWindow({ winId }: { winId: string }) {
  const app = useApp();
  const form = useSel(resourcesStore, (s) => s.form);
  const run = useSel(resourcesStore, (s) => s.run);
  const defaults = useSel(resourcesStore, (s) => s.defaults);
  const templates = useSel(resourcesStore, (s) => s.templates);
  const models = useSel(blocksStore, (s) => s.list);
  const details = useSel(blocksStore, (s) => s.details);
  const library = useSel(filtersStore, (s) => s.rows);
  const wireframes = useSel(sceneObjects, (s) => s.wireframes);
  const readOnly = useSel(app.store, (s) => s.projectReadOnly);
  const live = useSel(jobsStore, (s) => (run.job ? s.jobs.find((j) => j.source === run.job?.source && j.id === run.job?.id) : undefined));
  const [note, setNote] = useState<string | null>(null);
  const reason = app.registry.reason("resources.report");
  const running = run.phase === "running";

  const fields = useMemo(() => modelFields(form.modelId), [form.modelId, models, details]);
  const numbers = fields.filter((f) => f.kind === "number");
  const cats = fields.filter((f) => f.kind === "category");
  const filters = useMemo(() => filtersByName(library), [library]);
  const closed = useMemo<ObjectOption[]>(() => wireframes.map((w) => ({ id: w.id, name: w.name, dtm: w.dtm !== null })), [wireframes]);
  const dtms = useMemo(() => closed.filter((w) => w.dtm), [closed]);
  const parts = { form, numbers, cats, disabled: running, limits: defaults?.limits ?? DEFAULT_LIMITS };

  const build = async (save: boolean) => {
    setNote(null);
    const got = argsOfForm(form, save);
    if (!got.ok) {
      setIssue(got.issue);
      return;
    }
    const r = await app.registry.execute("resources.report", got.args, "user");
    if (!r.ok && !resourcesStore.get().issue) setNote(r.result);
  };
  const cancel = () => void cancelReport().catch((e: unknown) => setNote(errorText(e)));
  const pct = Math.round((live?.progress ?? 0) * 100);
  const result = run.result;
  const total = result ? firstTotal(result) : null;
  const warnings = result ? [...result.warnings, ...(result.control?.warnings ?? []), ...(result.form?.warnings ?? [])] : [];

  return (
    <div className={mod.window} data-testid="res-window">
      <div className={mod.layout}>
        <div className={mod.form}>
          <fieldset className={css.group}>
            <legend>Модель</legend>
            <select className={css.input} value={form.modelId ?? ""} aria-label="Блочная модель" data-testid="res-model" disabled={running} onChange={(e) => chooseModel(e.target.value ? Number(e.target.value) : null)}>
              <option value="">{models.length ? "— выберите модель —" : "нет блочных моделей"}</option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
            <Issue field="model" />
          </fieldset>
          <ElementsPart {...parts} />
          <CutoffsPart {...parts} />
          <BalancePart {...parts} />
          <DensityPart {...parts} auto={autoDensity(fields)} />
          <RestrictPart {...parts} filters={filters} wireframes={closed.filter((w) => !w.dtm)} dtms={dtms} />
          <GroupsPart {...parts} />
          <CurvePart {...parts} />
          <TemplatePart {...parts} templates={templates} />
          <Issue field="general" />
        </div>

        <div className={mod.result}>
          {run.phase === "idle" && (
            <p className={mod.empty} data-testid="res-idle">
              Выберите модель и параметры, нажмите «Предпросмотр»: итог по бортам и группам, форма отчёта, кривая и контроль появятся здесь. «Сохранить» записывает отчёт в проект.
            </p>
          )}
          {running && (
            <div data-testid="res-progress" data-status={live?.status ?? "queued"}>
              <h4 className={mod.head}>Отчёт по модели «{run.modelName}»</h4>
              <p className={css.note} data-testid="res-stage">
                {live?.cancel_requested ? "Отмена…" : (STATUS_TITLES[live?.status ?? "queued"] ?? "")}
                {live?.status === "running" && ` · ${pct}%${live.message ? ` · ${live.message}` : ""}`}
              </p>
              <div className={mod.progress}>
                <div style={{ width: `${pct}%` }} />
              </div>
              {live?.status === "queued" && live.why_waiting && <p className={css.note}>{live.why_waiting}</p>}
            </div>
          )}
          {run.phase === "cancelled" && (
            <p className={css.note} data-testid="res-cancelled">
              Отчёт отменён: ничего не записано.
            </p>
          )}
          {run.phase === "failed" && run.error && (
            <p className={css.error} role="alert" data-testid="res-failed">
              {run.error.message} {run.error.hint}
            </p>
          )}
          {result && !running && (
            <div className={mod.summary} data-testid="res-summary">
              <h4 className={mod.head}>
                {result.saved ? `Отчёт «${result.name}» записан в проект` : "Предпросмотр: отчёт не записан"} · {result.model.name}
              </h4>
              <dl className={mod.facts}>
                <dt>Строк</dt>
                <dd data-testid="res-rows">
                  {fmtCount(result.rows_total)}
                  {result.truncated ? " (показаны не все: сохраните отчёт, чтобы смотреть все)" : ""}
                </dd>
                <dt>Поле борта</dt>
                <dd>{result.cutoff_field}</dd>
                {total && (
                  <>
                    <dt>Итог, {rowTitle(total)}</dt>
                    <dd data-testid="res-total">
                      блоков {fmtCount(total.blocks)}, объём {fmt(total.volume)} м³, тоннаж {fmt(total.tonnes)} т
                    </dd>
                    {result.elements.map((e) => (
                      <FactRow key={e.field} name={e.field} unit={e.unit_label} base={e.metal_base} cell={total.elements[e.field]} />
                    ))}
                  </>
                )}
                {result.form && (
                  <>
                    <dt>Форма</dt>
                    <dd>{result.form.template_id}: строк {fmtCount(result.form.sections.reduce((n, s) => n + s.rows.length, 0))}</dd>
                  </>
                )}
                {result.curve && (
                  <>
                    <dt>Кривая</dt>
                    <dd>{fmtCount(result.curve.cutoffs.length)} бортов</dd>
                  </>
                )}
              </dl>
              <div>
                <button type="button" className={css.btn} title="Форма отчёта, таблица строк, кривая и контроль — в нижней панели «Ресурсы»" data-testid="res-show-panel" onClick={() => void app.run("resources.open", {})}>
                  Показать в панели «Ресурсы»
                </button>
              </div>
              {!!warnings.length && (
                <ul className={mod.warns} data-testid="res-warnings">
                  {warnings.map((w, i) => (
                    <li key={i}>{warningText(w)}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>
      {note && (
        <p className={css.error} style={{ margin: "0 12px" }} role="alert" data-testid="res-note">
          {note}
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.primary} disabled={running || !!reason || form.modelId === null} title={reason ?? "Построить отчёт без записи в проект"} data-testid="res-preview" onClick={() => void build(false)}>
          Предпросмотр
        </button>
        <button type="button" className={css.btn} disabled={running || !!reason || form.modelId === null || readOnly} title={readOnly ? "Проект открыт только для чтения: сохранить отчёт нельзя, предпросмотр работает" : "Построить отчёт и записать его в проект"} data-testid="res-save" onClick={() => void build(true)}>
          Сохранить
        </button>
        {running && (
          <button type="button" className={css.btn} disabled={!run.job || !!live?.cancel_requested} title="Остановить расчёт: сохранение пишет всё или ничего" data-testid="res-cancel" onClick={cancel}>
            {live?.cancel_requested ? "Отмена…" : "Отменить"}
          </button>
        )}
        <span className={mod.grow} />
        <button type="button" className={css.btn} data-testid="res-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}

function FactRow({ name, unit, base, cell }: { name: string; unit: string; base: string | null; cell: { grade: number | null; metal: number | null } | undefined }) {
  return (
    <>
      <dt>{name}</dt>
      <dd data-testid={`res-element-total-${name}`}>
        {cell?.grade === null || cell === undefined ? "нет значения" : `${fmt(cell.grade)} ${unit}`.trim()}
        {cell && cell.grade !== null ? `, металл ${cell.metal === null ? "не считается (единица не распознана)" : `${fmt(cell.metal)} ${base ?? ""}`.trim()}` : ""}
      </dd>
    </>
  );
}
