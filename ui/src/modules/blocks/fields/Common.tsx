/**
 * The parts the tabs of the window «Поля блочной модели» share (ТЗ 8.7.4, 8.7.5): the words under a field of a form, the
 * choice of the model, the restrictor (a filter of the library and the quick one — `RuleEditor` of the window
 * «Фильтры»), the course and the result of a run (the stage, «Отменить», the counts, a gap said as a gap).
 */
import { useMemo, useSyncExternalStore } from "react";
import css from "../../../shell/PropsParts.module.css";
import type { Store } from "../../../shell/store";
import { jobsStore, STATUS_TITLES } from "../../jobs/state";
import { sceneObjects } from "../../scene/objects";
import { blocksStore } from "../state";
import { RuleEditor, type ObjectOption } from "../filters/RuleEditor";
import { filtersByName, filtersStore } from "../filters/state";
import type { RestrictForm, Tab } from "./common";
import mod from "./Fields.module.css";
import { fieldsStore, type Op } from "./state";
import { countsText, rangeText, runTitle } from "./text";

export function useSel<T, R>(store: Store<T>, select: (s: T) => R): R {
  const get = () => select(store.get());
  return useSyncExternalStore(store.subscribe, get, get);
}

/** The words under a field of a form: the first wrong thing of the form or the refusal of the core, when it belongs here. */
export function IssueLine({ tab, place, row }: { tab: Tab; place: string; row?: string }) {
  const issue = useSel(fieldsStore, (s) => s.issue);
  if (!issue || issue.tab !== tab || issue.place !== place) return null;
  if (row !== undefined && issue.param !== row) return null;
  return (
    <p className={css.error} role="alert" data-testid={`fields-issue-${tab}-${place}${row ? `-${row}` : ""}`}>
      {issue.message} {issue.hint}
    </p>
  );
}

export function ModelPicker({ disabled, onChoose }: { disabled: boolean; onChoose: (id: number | null) => void }) {
  const models = useSel(blocksStore, (s) => s.list);
  const id = useSel(fieldsStore, (s) => s.modelId);
  return (
    <div className={mod.pick}>
      <label htmlFor="fields-model">Модель</label>
      <select id="fields-model" className={css.input} value={id ?? ""} disabled={disabled} data-testid="fields-model" onChange={(e) => onChoose(e.target.value ? Number(e.target.value) : null)}>
        <option value="">{models.length ? "— выберите модель —" : "нет блочных моделей"}</option>
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.name}
          </option>
        ))}
      </select>
    </div>
  );
}

/** The restrictor: a filter of the library and the quick restrictor (the tree of the rules of the window «Фильтры»). */
export function RestrictBox({ tab, value, onChange, fields, disabled, testId }: { tab: Tab; value: RestrictForm; onChange: (patch: Partial<RestrictForm>) => void; fields: readonly string[]; disabled: boolean; testId: string }) {
  const library = useSel(filtersStore, (s) => s.rows);
  const wireframes = useSel(sceneObjects, (s) => s.wireframes);
  const filters = useMemo(() => filtersByName(library), [library]);
  const options = useMemo<ObjectOption[]>(() => wireframes.map((w) => ({ id: w.id, name: w.name, dtm: w.dtm !== null })), [wireframes]);
  return (
    <>
      <label className={css.field}>
        <span>Фильтр библиотеки</span>
        <select className={css.input} value={value.filterId ?? ""} disabled={disabled} aria-label="Фильтр библиотеки" data-testid={`${testId}-filter`} onChange={(e) => onChange({ filterId: e.target.value ? Number(e.target.value) : null })}>
          <option value="">без фильтра</option>
          {value.filterId !== null && !filters.some((f) => f.id === value.filterId) && <option value={value.filterId}>№{value.filterId} (нет в проекте)</option>}
          {filters.map((f) => (
            <option key={f.id} value={f.id} title={f.title}>
              {f.name}
            </option>
          ))}
        </select>
      </label>
      <IssueLine tab={tab} place="filter" />
      <label className={css.check}>
        <input type="checkbox" checked={value.restrictOn} disabled={disabled} data-testid={`${testId}-on`} onChange={(e) => onChange({ restrictOn: e.target.checked })} />
        Быстрый ограничитель
      </label>
      {value.restrictOn && <RuleEditor draft={value.restrict} onChange={(restrict) => onChange({ restrict })} issue={null} fields={fields} wireframes={options} testId={testId} />}
      <IssueLine tab={tab} place="restrict" />
    </>
  );
}

/** The tab a run belongs to: «Перестроить» is the tab of the properties. */
const TAB_OF: Record<Op, Tab> = { calc: "calc", density: "density", classify: "classify", rebuild: "props" };

/** The course of the run (the stage, «Отменить» is in the footer of the tab) and its result; nothing when the run belongs to another tab. */
export function RunView({ tab }: { tab: Tab }) {
  const run = useSel(fieldsStore, (s) => s.run);
  const live = useSel(jobsStore, (s) => (run.job ? s.jobs.find((j) => j.source === run.job?.source && j.id === run.job?.id) : undefined));
  if (run.phase === "idle" || !run.op || TAB_OF[run.op] !== tab) return null;
  const pct = Math.round((live?.progress ?? 0) * 100);
  if (run.phase === "running") {
    return (
      <div className={mod.result} data-testid="fields-progress" data-status={live?.status ?? "queued"}>
        <h4>{runTitle(run.op, run.field, run.modelName)}</h4>
        <p className={css.note} data-testid="fields-stage">
          {live?.cancel_requested ? "Отмена…" : (STATUS_TITLES[live?.status ?? "queued"] ?? "")}
          {live?.status === "running" && ` · ${pct}%${live.message ? ` · ${live.message}` : ""}`}
        </p>
        <div className={mod.progress}>
          <div style={{ width: `${pct}%` }} />
        </div>
        {live?.status === "queued" && live.why_waiting && <p className={css.note}>{live.why_waiting}</p>}
      </div>
    );
  }
  if (run.phase === "cancelled") {
    return (
      <p className={css.note} data-testid="fields-cancelled">
        Расчёт отменён: в модель «{run.modelName}» ничего не записано.
      </p>
    );
  }
  if (run.phase === "failed") {
    return run.error ? (
      <p className={css.error} role="alert" data-testid="fields-failed">
        {run.error.message} {run.error.hint}
      </p>
    ) : null;
  }
  const r = run.result;
  if (!r) return null;
  const range = rangeText(r);
  const cats = countsText(r);
  return (
    <div className={mod.result} data-testid="fields-result">
      <h4>{r.fields.length > 1 ? `Записаны поля «${r.fields.join("», «")}»` : `Записано поле «${r.field}»`} · {run.modelName}</h4>
      <dl className={css.facts}>
        <dt>Блоков в модели</dt>
        <dd data-testid="fields-result-rows">{r.rows.toLocaleString("ru-RU")}</dd>
        {r.passed !== null && (
          <>
            <dt>Прошли ограничитель</dt>
            <dd data-testid="fields-result-passed">{r.passed.toLocaleString("ru-RU")}</dd>
          </>
        )}
        {r.missing !== null && (
          <>
            <dt>Без значения</dt>
            <dd data-testid="fields-result-missing">{r.missing.toLocaleString("ru-RU")}</dd>
          </>
        )}
        {r.defaulted !== null && (
          <>
            <dt>Подставлено по умолчанию</dt>
            <dd data-testid="fields-result-defaulted">{r.defaulted.toLocaleString("ru-RU")}</dd>
          </>
        )}
        {r.empty !== null && (
          <>
            <dt>Без плотности</dt>
            <dd data-testid="fields-result-empty">{r.empty.toLocaleString("ru-RU")}</dd>
          </>
        )}
        {r.unclassified !== null && (
          <>
            <dt>Без категории (оценка есть)</dt>
            <dd data-testid="fields-result-unclassified">{r.unclassified.toLocaleString("ru-RU")}</dd>
          </>
        )}
        {r.noEstimate !== null && (
          <>
            <dt>Без оценки</dt>
            <dd data-testid="fields-result-noestimate">{r.noEstimate.toLocaleString("ru-RU")}</dd>
          </>
        )}
        {r.dtype && (
          <>
            <dt>Тип</dt>
            <dd>{r.dtype}</dd>
          </>
        )}
        {range && (
          <>
            <dt>Значения</dt>
            <dd data-testid="fields-result-range">{range}</dd>
          </>
        )}
        {r.version !== null && (
          <>
            <dt>Версия модели</dt>
            <dd>{r.version}</dd>
          </>
        )}
      </dl>
      {cats && (
        <p className={css.note} data-testid="fields-result-counts">
          {cats}
        </p>
      )}
      {r.warnings.length > 0 && (
        <ul className={css.list} data-testid="fields-result-warnings">
          {r.warnings.map((w) => (
            <li key={w} className={css.warn}>
              {w}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
