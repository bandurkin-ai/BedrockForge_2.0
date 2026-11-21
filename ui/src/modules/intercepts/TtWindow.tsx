/**
 * The window «Истинная мощность» (ТЗ 8.5.4; ADR 0021 §11) on the frame of the tool windows of Э12: the source (set,
 * interval table), the block «Ориентировка», ε_в, the part of the hole and the angles with the ranges of 8.5.4 (a
 * value out of range is marked and never sent), the preview on one hole (`POST /intercepts/tt/preview`, the newest
 * answer only), «Применить» → the job `intercept.tt` followed in the window. `renderInterceptsWindow` — the windows
 * `intercepts-*` for the shell («Рудные пересечения» — InterceptsWindow.tsx).
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { errorText } from "../../shell/props";
import css from "../../shell/PropsParts.module.css";
import { refresh as refreshJobs } from "../jobs/state";
import { tablesStore } from "../tables/state";
import type { FloatWin } from "../../shell/windows";
import { createPreviewer, type FieldRow, type Previewer, type Started } from "../composites/api";
import { NO_SOURCE, sourceFromArgs, valuesFromArgs, type FormValues, type SourceState } from "../composites/params";
import { SourcePicker } from "../composites/SourcePicker";
import { askReplace, HolePicker, JobView, ParamForm, ToolFooter } from "../composites/ToolWindow";
import { startTt, ttPreview, type TtPreview as Answer } from "./api";
import { RUN_WINDOW, TT_WINDOW } from "./bind";
import { OrientationForm } from "./OrientationForm";
import { InterceptsWindow } from "./InterceptsWindow";
import { coreErrors, DEFAULT_ORIENTATION, interceptBody, orientationFromArgs, TT_TOOL, type OrientationState } from "./params";
import { TtPreview } from "./TtPreview";

const NEEDS = TT_TOOL.source;

/** The state of the window from the arguments of the command (the assistant, «Параметры…»). */
export function ttInitial(args: Record<string, unknown>): { src: SourceState; values: FormValues; orient: OrientationState; note: string | null } {
  const o = orientationFromArgs(args);
  return {
    src: { ...NO_SOURCE, ...sourceFromArgs(args) },
    values: valuesFromArgs(TT_TOOL.params, args),
    orient: typeof o === "string" ? DEFAULT_ORIENTATION : o,
    note: typeof o === "string" ? o : null,
  };
}

export function TtWindow({ winId, args = {} }: { winId: string; args?: Record<string, unknown> }) {
  const [init] = useState(() => ttInitial(args));
  const [src, setSrc] = useState<SourceState>(init.src);
  const [values, setValues] = useState<FormValues>(init.values);
  const [orient, setOrient] = useState<OrientationState>(init.orient);
  const [fields, setFields] = useState<FieldRow[]>([]);
  const [hole, setHole] = useState<{ id: number; name: string } | null>(null);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(init.note);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<Started | null>(null);
  const [activeSet] = useState(() => tablesStore.get().setId);
  const checked = useMemo(() => interceptBody(TT_TOOL, { setId: src.setId, table: src.table }, values, orient), [src, values, orient]);
  // a refusal of the core with `param` marks its field until the next edit (e15-api.md: «orientation.0.alpha»)
  const [refused, setRefused] = useState<Record<string, string>>({});
  useEffect(() => setRefused({}), [checked]);
  const errors = useMemo(() => ({ ...refused, ...checked.errors }), [refused, checked]);
  const previewer = useRef<Previewer | null>(null);
  const orientRef = useRef(orient);
  orientRef.current = orient;

  useEffect(() => {
    const p = createPreviewer<Answer>(
      (a) => {
        setAnswer(a);
        setPreviewError(null);
      },
      (e) => {
        setPreviewError(errorText(e));
        setRefused(coreErrors(e, orientRef.current));
      },
      (_op, params, holeId) => ttPreview(params, holeId),
    );
    previewer.current = p;
    return () => p.stop();
  }, []);
  useEffect(() => {
    if (!checked.body || !hole) return;
    previewer.current?.request("tt", checked.body, hole.id);
  }, [checked, hole]);

  const apply = async () => {
    if (!checked.body || busy) return;
    setBusy(true);
    setError(null);
    try {
      const s = await startTt(checked.body, askReplace);
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

  if (job) return <JobView title={TT_TOOL.title} job={job} winId={winId} onBack={() => setJob(null)} />;
  return (
    <div className={css.win} data-testid="intercepts-tt">
      <div className={css.winBody}>
        <p className={css.note}>{TT_TOOL.note}</p>
        <SourcePicker needs={NEEDS} value={src} onChange={setSrc} onFields={setFields} activeSet={activeSet} />
        <OrientationForm setId={src.setId} table={src.table} fields={fields} value={orient} errors={errors} onChange={setOrient} />
        <ParamForm params={TT_TOOL.params} values={values} errors={errors} fields={fields} onChange={setValues} />
        <fieldset className={css.group} data-testid="intercepts-tt-preview-box">
          <legend>Предпросмотр</legend>
          <HolePicker setId={src.setId} value={hole?.id ?? null} onChange={(id, name) => setHole(id === null ? null : { id, name })} />
          {!checked.body && <p className={css.note}>{checked.reason}</p>}
          {previewError && <p className={css.warn}>{previewError}</p>}
          {answer && <TtPreview answer={answer} />}
        </fieldset>
        {error && (
          <p className={css.error} role="alert">
            {error}
          </p>
        )}
      </div>
      <ToolFooter winId={winId} reason={checked.reason} busy={busy} onApply={() => void apply()} extra="Поля TT, TT_FLAG, TT_SRC (и углы) пишутся в таблицу источника; повтор перезаписывает их. «Отменить» в журнале правок скрывает их." />
    </div>
  );
}

/** The windows `intercepts-*` (the shell's renderer falls through to this). */
export function renderInterceptsWindow(w: FloatWin): ReactNode | null {
  const args = (w.props?.args as Record<string, unknown> | undefined) ?? {};
  if (w.kind === TT_WINDOW) return <TtWindow winId={w.id} args={args} />;
  if (w.kind === RUN_WINDOW) return <InterceptsWindow winId={w.id} args={args} />;
  return null;
}
