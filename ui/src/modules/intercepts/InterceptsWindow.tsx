/**
 * The window «Рудные пересечения» (ТЗ 8.5.3–8.5.4; ADR 0021 §7, §11, решения 10–14, 17–19) on the frame of the tool
 * windows of Э12: the source (set, interval table, grade field), the rule — c, the criterion (L_min by length or
 * m_min by true thickness), L_п, c2, the density, missing values, the other grade fields — with the ranges of 8.5.4
 * (a value out of range is marked and never sent), the block «Ориентировка» (may be switched off: TT empty,
 * TT_SRC «нет»), the preview on one hole (`POST /intercepts/run/preview`, the newest answer only), «Применить» →
 * the job `intercept.run` followed in the window; 409 `table_exists` → «Заменить?» in our own window. A refusal of
 * the core with `param` marks its field (e15-api.md). When the job is done, the table opens in the bottom panel and
 * its display «Рудные пересечения» (`chart_id` of the result) is switched on in 3D — as «Рудные интервалы» of Д-26.
 */
import { useContext, useEffect, useId, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { App } from "../../shell/app";
import { AppContext } from "../../shell/AppContext";
import { errorText } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import { jobsStore, refresh as refreshJobs } from "../jobs/state";
import { tablesStore } from "../tables/state";
import { createPreviewer, type FieldRow, type Previewer, type Started } from "../composites/api";
import { NO_SOURCE, parseNames, sourceFromArgs, valuesFromArgs, type FormValues, type ParamSpec, type SourceState } from "../composites/params";
import { showOreResult } from "../composites/OreWindow";
import { loadSetResults } from "../composites/results";
import { SourcePicker } from "../composites/SourcePicker";
import { askReplace, HolePicker, JobView, ParamForm, ToolFooter } from "../composites/ToolWindow";
import { runPreview, startRun, type RunPreview } from "./api";
import { InterceptsPreview } from "./InterceptsPreview";
import own from "./Intercepts.module.css";
import { OrientationForm } from "./OrientationForm";
import { coreErrors, DEFAULT_ORIENTATION, interceptBody, orientationFromArgs, RUN_TOOL, TT_COLUMNS, type OrientationState } from "./params";

/** The other grade fields (`fields`): null — every numeric field (the core's default), a list — these only. */
export type Grades = string[] | null;

/** `fields` of the arguments («CU, AG» or a list; «Параметры…» gives the list of the result) → `Grades`. */
export function gradesFromArgs(v: unknown): Grades {
  if (Array.isArray(v)) return v.map(String).filter((x) => x.trim());
  if (typeof v === "string" && v.trim()) return parseNames(v);
  return null;
}

/** The state of the window from the arguments of the command (the assistant, «Параметры…»). */
export function runInitial(args: Record<string, unknown>): { src: SourceState; values: FormValues; orient: OrientationState; grades: Grades; note: string | null } {
  const { fields: extra, ...rest } = args; // `fields` of `/run` are the other grades, not the source field
  const o = orientationFromArgs(args, true);
  return {
    src: { ...NO_SOURCE, ...sourceFromArgs(rest) },
    values: valuesFromArgs(RUN_TOOL.params, args),
    orient: typeof o === "string" ? { ...DEFAULT_ORIENTATION, enabled: false } : o,
    grades: gradesFromArgs(extra),
    note: typeof o === "string" ? o : null,
  };
}

/** The parameters of the form: L_min is m_min when the criterion is the true thickness. */
export function runParams(values: FormValues): ParamSpec[] {
  const byTt = values.min_by === "tt";
  return RUN_TOOL.params.map((p) => (p.key === "l_min" && byTt ? { ...p, label: "Мин. истинная мощность m_min", hint: "без ориентировки — проверка по длине тем же числом" } : p));
}

/** The numeric fields that may be other grades: not the grade, not the density, not the fields of TT. */
export function gradeChoices(fields: readonly FieldRow[], field: string | null, density: string): string[] {
  const skip = new Set([field ?? "", density, ...TT_COLUMNS].map((x) => x.toLowerCase()));
  return fields.filter((f) => f.kind === "number" && !skip.has(f.name.toLowerCase())).map((f) => f.name);
}

function GradesPicker({ choices, value, onChange }: { choices: readonly string[]; value: Grades; onChange: (g: Grades) => void }) {
  const list = value ?? [];
  const radio = useId();
  return (
    <fieldset className={css.group} data-testid="intercepts-run-grades">
      <legend>Поля содержаний сверх основного</legend>
      <label className={css.check}>
        <input type="radio" name={radio} checked={value === null} onChange={() => onChange(null)} />
        все числовые (кроме плотности и полей истинной мощности)
      </label>
      <label className={css.check}>
        <input type="radio" name={radio} checked={value !== null} onChange={() => onChange(list.filter((x) => choices.includes(x)))} />
        только отмеченные (ни одного — только основное поле)
      </label>
      {value !== null && (
        <div className={own.grades}>
          {choices.map((f) => (
            <label key={f} className={css.check}>
              <input type="checkbox" checked={list.includes(f)} onChange={(e) => onChange(e.target.checked ? [...list, f] : list.filter((x) => x !== f))} />
              {f}
            </label>
          ))}
          {!choices.length && <p className={css.note}>В таблице нет других числовых полей</p>}
        </div>
      )}
    </fieldset>
  );
}

/**
 * Once the job of a window of Э15 is done: the results of its set are read again (the rows `tt` / `intercepts` of
 * «Обработка проб» in the tree — also when the tree forgot the set), then `then` (the run window: the table in the
 * bottom panel and the display switched on, `showOreResult`).
 */
export function useWhenDone(job: Started | null, then?: (app: App | null, result: Record<string, unknown>) => Promise<void>): void {
  const app = useContext(AppContext);
  const get = () => (job ? jobsStore.get().jobs.find((j) => j.source === job.source && j.id === job.id) : undefined);
  const live = useSyncExternalStore(jobsStore.subscribe, get, get);
  const shownFor = useRef<string | null>(null);
  useEffect(() => {
    if (!job || live?.status !== "done" || !live.result) return;
    const key = `${job.source}:${job.id}`;
    if (shownFor.current === key) return;
    shownFor.current = key;
    const result = live.result as Record<string, unknown>;
    if (typeof result.set_id === "number") void loadSetResults(result.set_id).catch(() => undefined);
    if (then) void then(app, result).catch(() => undefined);
  }, [job, live, app]);
}

export function InterceptsWindow({ winId, args = {} }: { winId: string; args?: Record<string, unknown> }) {
  const [init] = useState(() => runInitial(args));
  const [src, setSrc] = useState<SourceState>(init.src);
  const [values, setValues] = useState<FormValues>(init.values);
  const [orient, setOrient] = useState<OrientationState>(init.orient);
  const [grades, setGrades] = useState<Grades>(init.grades);
  const [fields, setFields] = useState<FieldRow[]>([]);
  const [hole, setHole] = useState<{ id: number; name: string } | null>(null);
  const [answer, setAnswer] = useState<RunPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(init.note);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<Started | null>(null);
  useWhenDone(job, showOreResult);
  const [activeSet] = useState(() => tablesStore.get().setId);
  const field = src.fields[0] ?? null;
  const density = typeof values.density === "string" ? values.density : "";
  const choices = useMemo(() => gradeChoices(fields, field, density), [fields, field, density]);
  // a field gone from the table is not sent (the list is kept by name while the fields are read)
  const sentGrades = grades === null ? null : fields.length ? grades.filter((g) => choices.includes(g)) : grades;
  const checked = useMemo(() => interceptBody(RUN_TOOL, { setId: src.setId, table: src.table, field, grades: sentGrades }, values, orient), [src, field, JSON.stringify(sentGrades), values, orient]);
  const [refused, setRefused] = useState<Record<string, string>>({});
  useEffect(() => setRefused({}), [checked]);
  const errors = useMemo(() => ({ ...refused, ...checked.errors }), [refused, checked]);
  const previewer = useRef<Previewer | null>(null);
  const orientRef = useRef(orient);
  orientRef.current = orient;

  useEffect(() => {
    const p = createPreviewer<RunPreview>(
      (a) => {
        setAnswer(a);
        setPreviewError(null);
      },
      (e) => {
        setPreviewError(errorText(e));
        setRefused(coreErrors(e, orientRef.current));
      },
      (_op, params, holeId) => runPreview(params, holeId),
    );
    previewer.current = p;
    return () => p.stop();
  }, []);
  useEffect(() => {
    if (!checked.body || !hole) return;
    previewer.current?.request("run", checked.body, hole.id);
  }, [checked, hole]);

  const apply = async () => {
    if (!checked.body || busy) return;
    setBusy(true);
    setError(null);
    try {
      const s = await startRun(checked.body, askReplace);
      if (s) {
        setJob(s);
        await refreshJobs().catch(() => undefined);
      }
    } catch (e) {
      setError(errorText(e));
      setRefused(coreErrors(e, orient));
    } finally {
      setBusy(false);
    }
  };

  const name = (typeof values.name === "string" && values.name.trim()) || "Рудные пересечения";
  if (job) return <JobView title={RUN_TOOL.title} job={job} winId={winId} onBack={() => setJob(null)} />;
  return (
    <div className={css.win} data-testid="intercepts-run">
      <div className={css.winBody}>
        <p className={css.note}>{RUN_TOOL.note}</p>
        <SourcePicker needs={RUN_TOOL.source} value={src} onChange={setSrc} onFields={setFields} activeSet={activeSet} />
        <ParamForm params={runParams(values)} values={values} errors={errors} fields={fields} onChange={setValues} />
        <GradesPicker choices={choices} value={grades} onChange={setGrades} />
        <OrientationForm setId={src.setId} table={src.table} fields={fields} value={orient} errors={errors} onChange={setOrient} optional />
        <fieldset className={css.group} data-testid="intercepts-run-preview-box">
          <legend>Предпросмотр</legend>
          <HolePicker setId={src.setId} value={hole?.id ?? null} onChange={(id, n) => setHole(id === null ? null : { id, name: n })} />
          {!checked.body && <p className={css.note}>Предпросмотр — когда заданы параметры: {checked.reason}</p>}
          {previewError && <p className={css.warn}>{previewError}</p>}
          {answer ? <InterceptsPreview answer={answer} field={field} /> : checked.body && <p className={css.note}>{hole ? "Расчёт предпросмотра…" : "Выберите скважину"}</p>}
        </fieldset>
        {error && (
          <p className={css.error} role="alert">
            {error}
          </p>
        )}
      </div>
      <ToolFooter winId={winId} reason={checked.reason} busy={busy} onApply={() => void apply()} extra={`Новая таблица «${name}» в наборе и отображение по стволу; исходная таблица не меняется.`} />
    </div>
  );
}
