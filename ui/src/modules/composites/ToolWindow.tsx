/**
 * The common frame of a sample-processing tool (ТЗ 8.2.3; ADR 0018 §10–11): the source (`SourcePicker`), the form
 * built from the tool's parameters with the ranges of 8.2.2 (a value out of range is marked and never sent), the
 * preview on one hole (`POST /composites/{op}/preview`, the newest answer only), the summary «до/после»,
 * «Применить» → the job followed in the window. 409 `table_exists` → «Заменить?» in our own window.
 * `renderCompositesWindow` — the windows `composites-<tool>` for the shell.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { askConfirm } from "../../shell/dialogs";
import { errorText } from "../../shell/props";
import { useStore } from "../../shell/useStore";
import { closeWindow, type FloatWin } from "../../shell/windows";
import css from "../../shell/PropsParts.module.css";
import jobsCss from "../jobs/Jobs.module.css";
import { cancelJob, jobsStore, refresh as refreshJobs, STATUS_TITLES } from "../jobs/state";
import { tablesStore } from "../tables/state";
import own from "./Composites.module.css";
import { createPreviewer, loadHoles, startTool, type FieldRow, type HoleRow, type OnExists, type PreviewAnswer, type Previewer, type Started } from "./api";
import { toolValues, WINDOW_PREFIX } from "./bind";
import { CodingWindow } from "./CodingWindow";
import { CompositeWindow } from "./CompositeWindow";
import { ConditionsWindow } from "./ConditionsWindow";
import { DeclusWindow } from "./DeclusWindow";
import { EquivalentWindow } from "./EquivalentWindow";
import { CoordsWindow, MergeWindow } from "./MergeWindow";
import { OreWindow } from "./OreWindow";
import { ResultProps } from "./ResultProps";
import { RESULT_WINDOW } from "./ResultsTree";
import { NO_SOURCE, rangeText, shown, sourceFromArgs, toolBody, type FormValues, type ParamSpec, type SourceState, type ToolSpec } from "./params";
import { SourcePicker } from "./SourcePicker";
import { TopcutWindow } from "./TopcutWindow";
import { summaryRows } from "./tools/composite";
import { toolById } from "./tools";

/** «Заменить?» of 409 `table_exists` — our own window (ADR 0018 §10). */
export const askReplace: OnExists = async (name) =>
  (await askConfirm({ title: "Заменить?", message: `В наборе уже есть таблица «${name}». Заменить её новым результатом?`, okLabel: "Заменить", danger: true })) ? "replace" : "cancel";

export function ParamForm({ params, values, errors, fields, onChange, placeholders = {} }: { params: readonly ParamSpec[]; values: FormValues; errors: Record<string, string>; fields: readonly FieldRow[]; onChange: (v: FormValues) => void; placeholders?: Record<string, string> }) {
  const set = (key: string, v: string | boolean) => onChange({ ...values, [key]: v });
  return (
    <fieldset className={css.group} data-testid="composites-params">
      <legend>Параметры</legend>
      {params
        .filter((p) => shown(p, values))
        .map((p) => {
          const err = errors[p.key];
          const range = rangeText(p);
          const title = [p.hint, range && `диапазон ${range}`].filter(Boolean).join("; ") || undefined;
          if (p.kind === "boolean")
            return (
              <label key={p.key} className={css.check} title={title}>
                <input type="checkbox" checked={values[p.key] === true} onChange={(e) => set(p.key, e.target.checked)} />
                {p.label}
              </label>
            );
          const text = typeof values[p.key] === "string" ? (values[p.key] as string) : "";
          let input: ReactNode;
          if (p.kind === "choice")
            input = (
              <select className={css.input} aria-label={p.label} value={text} onChange={(e) => set(p.key, e.target.value)}>
                {(p.choices ?? []).map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            );
          else if (p.kind === "field")
            input = (
              <select className={css.input} aria-label={p.label} value={text} onChange={(e) => set(p.key, e.target.value)}>
                <option value="">{p.optional ? "—" : "выберите поле"}</option>
                {fields
                  .filter((f) => !p.numeric || f.kind === "number")
                  .map((f) => (
                    <option key={f.key} value={f.name}>
                      {f.name}
                    </option>
                  ))}
              </select>
            );
          else
            input = (
              <input
                className={css.input}
                aria-label={p.label}
                aria-invalid={err ? true : undefined}
                inputMode={p.kind === "number" || p.kind === "integer" ? "decimal" : undefined}
                value={text}
                placeholder={placeholders[p.key] ?? p.hint ?? range}
                onChange={(e) => set(p.key, e.target.value)}
              />
            );
          return (
            <label key={p.key} className={css.formRow} title={title} data-testid={`composites-param-${p.key}`}>
              <span>
                {p.label}
                {p.unit ? `, ${p.unit}` : ""}
              </span>
              <span className={own.paramCell}>
                {input}
                {err && <span className={own.errText}>{err}</span>}
              </span>
            </label>
          );
        })}
    </fieldset>
  );
}

export function HolePicker({ setId, value, onChange }: { setId: number | null; value: number | null; onChange: (id: number | null, name: string) => void }) {
  const [holes, setHoles] = useState<HoleRow[] | null>(null);
  const [filter, setFilter] = useState("");
  useEffect(() => {
    let alive = true;
    setHoles(null);
    if (setId === null) return;
    loadHoles(setId).then(
      (h) => {
        if (!alive) return;
        setHoles(h);
        if (h.length && !h.some((x) => x.id === value)) onChange(h[0].id, h[0].name);
      },
      () => alive && setHoles([]),
    );
    return () => {
      alive = false;
    };
  }, [setId]);
  const shownHoles = (holes ?? []).filter((h) => !filter || h.name.toLowerCase().includes(filter.toLowerCase())).slice(0, 300);
  return (
    <div className={own.holeRow}>
      <span>Скважина</span>
      <input className={css.input} aria-label="Поиск скважины" placeholder="поиск" value={filter} onChange={(e) => setFilter(e.target.value)} />
      <select className={css.input} aria-label="Скважина предпросмотра" value={value ?? ""} onChange={(e) => onChange(Number(e.target.value), e.target.selectedOptions[0]?.text ?? "")}>
        {!shownHoles.length && <option value="">{holes ? "Нет скважин" : "Чтение…"}</option>}
        {shownHoles.map((h) => (
          <option key={h.id} value={h.id}>
            {h.name}
          </option>
        ))}
      </select>
    </div>
  );
}

export function SummaryTable({ summary, title }: { summary: PreviewAnswer["summary"]; title: string }) {
  const rows = summaryRows(summary);
  if (!rows.length) return null;
  return (
    <table className={own.summary} data-testid="composites-summary">
      <caption>{title}</caption>
      <thead>
        <tr>
          <th />
          <th>до</th>
          <th>после</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.label}>
            <th scope="row">{r.label}</th>
            <td>{r.before}</td>
            <td>{r.after}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** The job of the window: its state, progress, «Отменить», then what was made. */
export function JobView({ title, job, winId, onBack }: { title: string; job: Started; winId: string; onBack: () => void }) {
  const live = useStore(jobsStore, (s) => s.jobs.find((j) => j.source === job.source && j.id === job.id));
  const [error, setError] = useState<string | null>(null);
  const status = live?.status ?? "queued";
  const finished = status === "done" || status === "failed" || status === "cancelled";
  const pct = Math.round((live?.progress ?? 0) * 100);
  const result = status === "done" ? live?.result : null;
  return (
    <div className={css.win} data-testid="composites-job">
      <div className={css.winBody}>
        <h4 className={css.name}>
          {title} · задание №{job.id}
        </h4>
        <div data-testid="composites-job-status" data-status={status}>
          {live?.cancel_requested && status === "running" ? "Отмена…" : STATUS_TITLES[status]}
          {status === "running" && ` · ${pct}%${live?.message ? ` · ${live.message}` : ""}`}
        </div>
        {(status === "running" || status === "queued") && (
          <div className={jobsCss.progress}>
            <div style={{ width: `${pct}%` }} />
          </div>
        )}
        {status === "queued" && live?.why_waiting && <p className={css.note}>{live.why_waiting}</p>}
        {status === "failed" && (
          <p className={css.error} role="alert">
            {live?.error ? `${live.error.message} ${live.error.hint}`.trim() : "Задание завершилось ошибкой."}
          </p>
        )}
        {status === "cancelled" && <p className={css.note}>Задание отменено: ничего не записано.</p>}
        {result && <p data-testid="composites-job-result">{typeof result.name === "string" ? `Готово: «${result.name}»` : "Готово"}</p>}
        {error && (
          <p className={css.error} role="alert">
            {error}
          </p>
        )}
      </div>
      <div className={css.footer}>
        <span className={css.grow} />
        {!finished && (
          <button type="button" className={css.btn} disabled={live?.cancel_requested} onClick={() => void cancelJob(job).catch((e: unknown) => setError(errorText(e)))}>
            {live?.cancel_requested ? "Отмена…" : "Отменить"}
          </button>
        )}
        {finished && (
          <button type="button" className={css.btn} onClick={onBack}>
            Назад к параметрам
          </button>
        )}
        <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
          {finished ? "Закрыть" : "Скрыть окно"}
        </button>
      </div>
    </div>
  );
}

/** The state shared by every tool window: source, values, preview, apply. */
export function useTool(tool: ToolSpec, args: Record<string, unknown>) {
  const [src, setSrc] = useState<SourceState>(() => ({ ...NO_SOURCE, ...sourceFromArgs(args) }));
  const [values, setValues] = useState<FormValues>(() => toolValues(tool, args));
  const [fields, setFields] = useState<FieldRow[]>([]);
  const [hole, setHole] = useState<{ id: number; name: string } | null>(null);
  const [answer, setAnswer] = useState<PreviewAnswer | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<Started | null>(null);
  const checked = useMemo(() => toolBody(tool, src, values), [tool, src, values]);
  const previewer = useRef<Previewer | null>(null);

  useEffect(() => {
    const p = createPreviewer<PreviewAnswer>(
      (a) => {
        setAnswer(a);
        setPreviewError(null);
      },
      (e) => setPreviewError(errorText(e)),
    );
    previewer.current = p;
    return () => p.stop();
  }, []);
  useEffect(() => {
    if (!tool.preview || !checked.body || !hole) return;
    previewer.current?.request(tool.preview(values), checked.body, hole.id);
  }, [tool, checked, hole, values]);

  const apply = async () => {
    if (!checked.body || busy) return;
    setBusy(true);
    setError(null);
    try {
      const s = await startTool(tool.endpoint, checked.body, askReplace);
      if (s) {
        setJob(s);
        await refreshJobs().catch(() => undefined);
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return { src, setSrc, values, setValues, fields, setFields, hole, setHole, answer, previewError, error, busy, job, setJob, checked, apply };
}

export function ToolFooter({ winId, reason, busy, onApply, extra }: { winId: string; reason: string | null; busy: boolean; onApply: () => void; extra?: string }) {
  return (
    <div className={css.footer}>
      <span className={`${css.note} ${css.grow}`} data-testid="composites-reason">
        {reason ?? extra ?? "Расчёт идёт заданием: ход — здесь и на вкладке «Задания»."}
      </span>
      <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
        Отмена
      </button>
      <button type="button" className={css.primary} data-testid="composites-apply" disabled={reason !== null || busy} onClick={onApply}>
        Применить
      </button>
    </div>
  );
}

export function ToolWindow({ winId, tool, args = {} }: { winId: string; tool: ToolSpec; args?: Record<string, unknown> }) {
  const t = useTool(tool, args);
  const [activeSet] = useState(() => tablesStore.get().setId); // the set active when the window opened
  if (t.job) return <JobView title={tool.title} job={t.job} winId={winId} onBack={() => t.setJob(null)} />;
  return (
    <div className={css.win} data-testid={`composites-${tool.id}`}>
      <div className={css.winBody}>
        <p className={css.note}>{tool.note}</p>
        <SourcePicker needs={tool.source} value={t.src} onChange={t.setSrc} onFields={t.setFields} activeSet={activeSet} />
        <ParamForm params={tool.params} values={t.values} errors={t.checked.errors} fields={t.fields} onChange={t.setValues} />
        {tool.preview && (
          <fieldset className={css.group} data-testid="composites-preview">
            <legend>Предпросмотр</legend>
            <HolePicker setId={t.src.setId} value={t.hole?.id ?? null} onChange={(id, name) => t.setHole(id === null ? null : { id, name })} />
            {t.previewError && <p className={css.warn}>{t.previewError}</p>}
            {t.answer && (
              <p className={css.note}>
                Скважина {t.answer.hole}: проб {t.answer.samples.from.length}, результат — {t.answer.result.from.length}
              </p>
            )}
            <SummaryTable summary={t.answer?.summary ?? null} title="Скважина" />
            <SummaryTable summary={t.answer?.set_summary ?? null} title="Набор" />
          </fieldset>
        )}
        {t.error && (
          <p className={css.error} role="alert">
            {t.error}
          </p>
        )}
      </div>
      <ToolFooter winId={winId} reason={t.checked.reason} busy={t.busy} onApply={() => void t.apply()} extra={tool.writes === "fields" ? "Поля пишутся в таблицу источника; «Отменить» в журнале правок скрывает их." : undefined} />
    </div>
  );
}

/** The windows `composites-<tool>` (the shell's renderer falls through to this). */
export function renderCompositesWindow(w: FloatWin): ReactNode | null {
  if (!w.kind.startsWith(WINDOW_PREFIX)) return null;
  if (w.kind === RESULT_WINDOW) return <ResultProps winId={w.id} resultId={Number(w.props?.resultId)} setId={typeof w.props?.setId === "number" ? w.props.setId : null} />;
  const tool = toolById(w.kind.slice(WINDOW_PREFIX.length));
  if (!tool) return null;
  const args = (w.props?.args as Record<string, unknown> | undefined) ?? {};
  if (tool.id === "composite") return <CompositeWindow winId={w.id} args={args} />;
  if (tool.id === "coding") return <CodingWindow winId={w.id} args={args} />;
  if (tool.id === "merge") return <MergeWindow winId={w.id} args={args} />;
  if (tool.id === "coords") return <CoordsWindow winId={w.id} args={args} />;
  if (tool.id === "equivalent") return <EquivalentWindow winId={w.id} args={args} />;
  if (tool.id === "topcut") return <TopcutWindow winId={w.id} args={args} />;
  if (tool.id === "declus") return <DeclusWindow winId={w.id} args={args} />;
  if (tool.id === "ore") return <OreWindow winId={w.id} args={args} />;
  if (tool.id === "conditions") return <ConditionsWindow winId={w.id} args={args} />;
  return <ToolWindow winId={w.id} tool={tool} args={args} />;
}
