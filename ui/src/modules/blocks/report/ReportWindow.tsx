/**
 * The window «Содержание–тоннаж · <модель>» (ТЗ 7.31; ADR 0023 §10): on the left the request — the model, the grades (up to
 * 20; by default the fields `*_CUT`), the density (automatic: the varying field SG / a field / a number), the cut-offs and
 * the cut-off field, the filter of the library and the quick restrictor, the selection by categories and the groups, the
 * curve; on the right the run (the stage, «Отменить») and the result (`ResultView.tsx`: the table, the curve, the CSV).
 * «Построить» runs the command `blocks.report` through the registry (the log, the notice, the question of the assistant);
 * a wrong form is told under its field in Russian before anything is sent. Floating, not modal.
 */
import { useMemo, useState, useSyncExternalStore } from "react";
import { useApp } from "../../../shell/AppContext";
import css from "../../../shell/PropsParts.module.css";
import { errorText } from "../../../shell/props";
import type { Store } from "../../../shell/store";
import { closeWindow } from "../../../shell/windows";
import { jobsStore, STATUS_TITLES } from "../../jobs/state";
import { sceneObjects } from "../../scene/objects";
import { blocksStore } from "../state";
import { RuleEditor, type ObjectOption } from "../filters/RuleEditor";
import { filtersByName, filtersStore } from "../filters/state";
import { cancelReport, chooseModel, modelFields } from "./bind";
import { MAX_GRADES } from "./api";
import mod from "./Report.module.css";
import { ResultView } from "./ResultView";
import { argsOfForm, autoDensity, reportStore, setForm, setIssue, type FormField, type ReportForm } from "./state";

function useSel<T, R>(store: Store<T>, select: (s: T) => R): R {
  const get = () => select(store.get());
  return useSyncExternalStore(store.subscribe, get, get);
}

const label = (name: string, unit: string | null) => (unit ? `${name}, ${unit}` : name);

/** The words under a field of the form (the first wrong thing of the form or the refusal of the core). */
function Issue({ field }: { field: FormField }) {
  const issue = useSel(reportStore, (s) => s.issue);
  if (!issue || issue.field !== field) return null;
  return (
    <p className={css.error} role="alert" data-testid={`report-issue-${field}`}>
      {issue.message} {issue.hint}
    </p>
  );
}

export function ReportWindow({ winId }: { winId: string }) {
  const app = useApp();
  const form = useSel(reportStore, (s) => s.form);
  const run = useSel(reportStore, (s) => s.run);
  const models = useSel(blocksStore, (s) => s.list);
  const details = useSel(blocksStore, (s) => s.details);
  const library = useSel(filtersStore, (s) => s.rows);
  const wireframes = useSel(sceneObjects, (s) => s.wireframes);
  const live = useSel(jobsStore, (s) => (run.job ? s.jobs.find((j) => j.source === run.job?.source && j.id === run.job?.id) : undefined));
  const issueField = useSel(reportStore, (s) => s.issue?.field);
  const [note, setNote] = useState<string | null>(null);
  const reason = app.registry.reason("blocks.report");
  const running = run.phase === "running";

  const fields = useMemo(() => modelFields(form.modelId), [form.modelId, models, details]);
  const numbers = fields.filter((f) => f.kind === "number");
  const cats = fields.filter((f) => f.kind === "category");
  const catLabels = cats.find((f) => f.name === form.catField)?.labels ?? [];
  const filters = useMemo(() => filtersByName(library), [library]);
  const options = useMemo<ObjectOption[]>(() => wireframes.map((w) => ({ id: w.id, name: w.name, dtm: w.dtm !== null })), [wireframes]);
  const auto = autoDensity(fields);
  const set = (patch: Partial<ReportForm>) => setForm(patch);

  const build = async () => {
    setNote(null);
    const got = argsOfForm(form);
    if (!got.ok) {
      setIssue(got.issue);
      return;
    }
    const r = await app.registry.execute("blocks.report", got.args, "user");
    if (!r.ok && !reportStore.get().issue) setNote(r.result);
  };
  const cancel = () => void cancelReport().catch((e: unknown) => setNote(errorText(e)));
  const toggle = (list: string[], v: string, on: boolean) => (on ? (list.includes(v) ? list : [...list, v]) : list.filter((x) => x !== v));
  const pct = Math.round((live?.progress ?? 0) * 100);

  return (
    <div className={mod.window} data-testid="report-window">
      <div className={mod.layout}>
        <div className={mod.form}>
          <fieldset className={css.group}>
            <legend>Модель</legend>
            <select className={css.input} value={form.modelId ?? ""} aria-label="Блочная модель" data-testid="report-model" disabled={running} onChange={(e) => chooseModel(e.target.value ? Number(e.target.value) : null)}>
              <option value="">{models.length ? "— выберите модель —" : "нет блочных моделей"}</option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
            <Issue field="model" />
          </fieldset>

          <fieldset className={css.group} disabled={running || form.modelId === null}>
            <legend>Содержания</legend>
            <div className={mod.list} role="group" aria-label="Содержания" data-testid="report-grades">
              {numbers.map((f) => (
                <label key={f.name} title={f.role === "grade" ? "Поле с ролью «содержание»" : undefined}>
                  <input type="checkbox" checked={form.grades.includes(f.name)} data-testid={`report-grade-${f.name}`} onChange={(e) => set({ grades: toggle(form.grades, f.name, e.target.checked) })} />
                  <span>
                    {f.name}
                    {f.unit && <span className={mod.unit}>, {f.unit}</span>}
                  </span>
                </label>
              ))}
              {!numbers.length && <span className={css.note}>У модели нет числовых полей</span>}
            </div>
            <span className={form.grades.length > MAX_GRADES ? css.error : css.note}>
              Выбрано {form.grades.length} из {MAX_GRADES}
              {!form.grades.length && " — не выбрано: возьмутся поля *_CUT"}
            </span>
            <Issue field="grades" />
            <label className={css.field}>
              <span>Поле борта</span>
              <select className={css.input} value={form.cutoffField} aria-label="Поле борта" data-testid="report-cutoff-field" onChange={(e) => set({ cutoffField: e.target.value })}>
                <option value="">первое из содержаний</option>
                {numbers.map((f) => (
                  <option key={f.name} value={f.name}>
                    {label(f.name, f.unit)}
                  </option>
                ))}
              </select>
            </label>
            <Issue field="cutoff_field" />
          </fieldset>

          <fieldset className={css.group} disabled={running || form.modelId === null}>
            <legend>Плотность</legend>
            <div className={mod.radios} role="radiogroup" aria-label="Плотность">
              <label>
                <input type="radio" name="report-density" checked={form.densityMode === "auto"} data-testid="report-density-auto" onChange={() => set({ densityMode: "auto" })} />
                Автоматически{auto ? `: поле ${auto}` : ": поле SG или поле с ролью «плотность» не найдено"}
              </label>
              <label>
                <input type="radio" name="report-density" checked={form.densityMode === "field"} data-testid="report-density-field-on" onChange={() => set({ densityMode: "field" })} />
                Поле модели
              </label>
              {form.densityMode === "field" && (
                <select className={css.input} value={form.densityField} aria-label="Поле плотности" data-testid="report-density-field" onChange={(e) => set({ densityField: e.target.value })}>
                  <option value="">— выберите поле —</option>
                  {numbers.map((f) => (
                    <option key={f.name} value={f.name}>
                      {f.name}
                      {f.constant ? " (постоянное)" : ""}
                    </option>
                  ))}
                </select>
              )}
              <label>
                <input type="radio" name="report-density" checked={form.densityMode === "value"} data-testid="report-density-value-on" onChange={() => set({ densityMode: "value" })} />
                Число, т/м³
              </label>
              {form.densityMode === "value" && <input className={css.input} value={form.densityValue} inputMode="decimal" aria-label="Плотность, т/м³" data-testid="report-density-value" onChange={(e) => set({ densityValue: e.target.value })} />}
            </div>
            <Issue field="density" />
          </fieldset>

          <fieldset className={css.group} disabled={running}>
            <legend>Борта</legend>
            <input className={css.input} value={form.cutoffs} spellCheck={false} aria-label="Борта через точку с запятой" aria-invalid={issueField === "cutoffs"} data-testid="report-cutoffs" onChange={(e) => set({ cutoffs: e.target.value })} />
            <span className={css.note}>Через «;», например 0; 0,5; 1. Борт 0 — весь тоннаж блоков со значением поля борта.</span>
            <Issue field="cutoffs" />
          </fieldset>

          <fieldset className={css.group} disabled={running || form.modelId === null}>
            <legend>Ограничение</legend>
            <label className={css.field}>
              <span>Фильтр библиотеки</span>
              <select className={css.input} value={form.filterId ?? ""} aria-label="Фильтр библиотеки" data-testid="report-filter" onChange={(e) => set({ filterId: e.target.value ? Number(e.target.value) : null })}>
                <option value="">без фильтра</option>
                {form.filterId !== null && !filters.some((f) => f.id === form.filterId) && <option value={form.filterId}>№{form.filterId} (нет в проекте)</option>}
                {filters.map((f) => (
                  <option key={f.id} value={f.id} title={f.title}>
                    {f.name}
                  </option>
                ))}
              </select>
            </label>
            <Issue field="filter" />
            <label className={css.check}>
              <input type="checkbox" checked={form.restrictOn} data-testid="report-restrict-on" onChange={(e) => set({ restrictOn: e.target.checked })} />
              Быстрый ограничитель
            </label>
            {form.restrictOn && <RuleEditor draft={form.restrict} onChange={(restrict) => set({ restrict })} issue={null} fields={numbers.concat(cats).map((f) => f.name)} wireframes={options} testId="report-restrict" />}
            <Issue field="restrict" />
          </fieldset>

          <fieldset className={css.group} disabled={running || form.modelId === null}>
            <legend>Категории и группы</legend>
            <label className={css.check}>
              <input type="checkbox" checked={form.catOn} data-testid="report-cat-on" onChange={(e) => set({ catOn: e.target.checked })} />
              Отбор по категориям
            </label>
            {form.catOn && (
              <>
                <select className={css.input} value={form.catField} aria-label="Поле категорий" data-testid="report-cat-field" onChange={(e) => set({ catField: e.target.value, catValues: [] })}>
                  <option value="">— выберите поле —</option>
                  {cats.map((f) => (
                    <option key={f.name} value={f.name}>
                      {f.name}
                    </option>
                  ))}
                </select>
                <div className={mod.list} role="group" aria-label="Категории" data-testid="report-cat-values">
                  {catLabels.map((l, i) => (
                    <label key={l}>
                      <input type="checkbox" checked={form.catValues.includes(l)} data-testid={`report-cat-${i}`} onChange={(e) => set({ catValues: toggle(form.catValues, l, e.target.checked) })} />
                      <span>{l}</span>
                    </label>
                  ))}
                  {form.catField && !catLabels.length && <span className={css.note}>У поля нет подписей категорий</span>}
                </div>
              </>
            )}
            <Issue field="categories" />
            <label className={css.field}>
              <span>Группы по полю</span>
              <select className={css.input} value={form.groups} aria-label="Группы по полю" data-testid="report-groups" onChange={(e) => set({ groups: e.target.value })}>
                <option value="">без групп</option>
                {cats.map((f) => (
                  <option key={f.name} value={f.name}>
                    {f.name}
                  </option>
                ))}
              </select>
            </label>
            <Issue field="groups" />
          </fieldset>

          <fieldset className={css.group} disabled={running}>
            <legend>Кривая</legend>
            <label className={css.check}>
              <input type="checkbox" checked={form.curveOn} data-testid="report-curve-on" onChange={(e) => set({ curveOn: e.target.checked })} />
              Строить кривую «содержание–тоннаж»
            </label>
            {form.curveOn && (
              <label className={css.field}>
                <span>Точек, от 2 до 500</span>
                <input className={css.input} value={form.curvePoints} inputMode="numeric" aria-label="Точек кривой" data-testid="report-curve-points" onChange={(e) => set({ curvePoints: e.target.value })} />
              </label>
            )}
            <Issue field="curve" />
          </fieldset>
          <Issue field="general" />
        </div>

        <div className={mod.result}>
          {run.phase === "idle" && (
            <p className={mod.empty} data-testid="report-idle">
              Выберите модель и параметры, нажмите «Построить»: таблица по бортам, кривая и CSV появятся здесь.
            </p>
          )}
          {running && (
            <div data-testid="report-progress" data-status={live?.status ?? "queued"}>
              <h4 className={mod.head}>Отчёт по модели «{run.modelName}»</h4>
              <p className={css.note} data-testid="report-stage">
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
            <p className={css.note} data-testid="report-cancelled">
              Отчёт отменён: ничего не записано.
            </p>
          )}
          {run.phase === "failed" && run.error && (
            <p className={css.error} role="alert" data-testid="report-failed">
              {run.error.message} {run.error.hint}
            </p>
          )}
          {run.result && !running && <ResultView result={run.result} modelName={run.modelName} />}
        </div>
      </div>
      {note && (
        <p className={css.error} style={{ margin: "0 12px" }} role="alert" data-testid="report-note">
          {note}
        </p>
      )}
      <div className={mod.footer}>
        <button type="button" className={css.primary} disabled={running || !!reason || form.modelId === null} title={reason ?? "Построить отчёт по выбранной модели"} data-testid="report-build" onClick={() => void build()}>
          Построить
        </button>
        {running && (
          <button type="button" className={css.btn} disabled={!run.job || !!live?.cancel_requested} title="Остановить расчёт: отчёт ничего не записывает" data-testid="report-cancel" onClick={cancel}>
            {live?.cancel_requested ? "Отмена…" : "Отменить"}
          </button>
        )}
        <span className={mod.grow} />
        <button type="button" className={css.btn} data-testid="report-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
