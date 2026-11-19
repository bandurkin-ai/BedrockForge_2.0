/**
 * The window of an operation (ТЗ 7.23; ADR 0015 §6): built from the fields the core gives (`GET /operations`;
 * `opForms.ts`, `pickers.tsx`); «Обрезать» — one window of three operations. While the values can be sent,
 * the preconditions are checked (`POST /operations/check`): the sources, the size of the input and the queue,
 * or the refusal with its reason — an open wireframe where a solid is needed names its open edges and offers
 * «Исправить каркас» (`model.repair` with that wireframe). «Выполнить» runs `model.operation` through the
 * registry; then the window follows the job — state, progress, «Отменить», the error — and the report opens in
 * its own window when it is done (`run.ts`).
 */
import { useEffect, useMemo, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { jobsStore, STATUS_TITLES } from "../jobs/state";
import { useStore } from "../../shell/useStore";
import { closeWindow } from "../../shell/windows";
import { count } from "../../shell/treeActions";
import css from "../../shell/PropsParts.module.css";
import { sceneObjects } from "../scene/objects";
import { tablesStore } from "../tables/state";
import type { WireframeInfo } from "../wireframes/state";
import type { OperationWindowProps } from "./bind";
import { dialogOps, initialValues, openWireframeNote, paramsOf, switchValues, visibleFields, type FieldValue, type Fix, type FormValues, type OpSpec } from "./opForms";
import { FieldInput } from "./pickers";
import { checkOperation, loadOperations, openReport, operationsStore, queueText, refusalOf, reportProps, startsStore, type CheckSummary, type Started } from "./run";

const RESULT_NOTES: Record<OpSpec["result"], string> = {
  mesh: "Результат — новый каркас в папке первого исходного; исходные не меняются.",
  lines: "Результат — новый файл линий в папке первого исходного; исходные не меняются.",
  report: "Результат — отчёт и, если не «Только отчёт», тела выемки и насыпи.",
};

type CheckState = { key: string; summary?: CheckSummary; refusal?: { text: string; fix: Fix | null } };

/** «Исходные: «Тело 1», «Тело 2» · 12 400 треугольников · быстрая очередь» */
export function summaryText(s: CheckSummary): string {
  const names = s.sources.map((x) => `«${x.name}»`);
  const shown = names.length > 6 ? [...names.slice(0, 6), `и ещё ${names.length - 6}`] : names;
  return [`Исходные: ${shown.join(", ") || "—"}`, s.size ? `треугольников на входе: ${count(s.size)}` : "", queueText(s.queue)].filter(Boolean).join(" · ");
}

export function OperationWindow({ winId, dialog, op, wireframeId, layerId }: { winId: string } & OperationWindowProps) {
  const list = useStore(operationsStore, (s) => s.list);
  const listError = useStore(operationsStore, (s) => s.error);
  const [follow, setFollow] = useState<Started | null>(null);
  useEffect(() => void loadOperations().catch(() => undefined), []);
  const specs = useMemo(() => (list ? dialogOps(list, dialog) : []), [list, dialog]);
  if (!list) return <div className={css.win}>{listError ? <p className={css.error}>{listError}</p> : <p className={css.note}>Загрузка списка операций…</p>}</div>;
  if (!specs.length) return <p className={css.error}>Окна операции «{dialog}» нет в ядре: обновите программу.</p>;
  if (follow) return <Follow winId={winId} started={follow} onBack={() => setFollow(null)} />;
  return <Form winId={winId} specs={specs} op={op} pre={{ wireframeId, layerId }} onStarted={setFollow} />;
}

function Form({ winId, specs, op, pre, onStarted }: { winId: string; specs: OpSpec[]; op?: string; pre: { wireframeId?: number; layerId?: number }; onStarted: (s: Started) => void }) {
  const app = useApp();
  const wireframes = useStore(sceneObjects, (o) => o.wireframes) as WireframeInfo[];
  const files = useStore(sceneObjects, (o) => o.files);
  const sets = useStore(tablesStore, (s) => s.sets);
  const [opKey, setOpKey] = useState(op && specs.some((s) => s.op === op) ? op : specs[0].op);
  const spec = specs.find((s) => s.op === opKey) ?? specs[0];
  const [form, setForm] = useState<{ op: string; values: FormValues } | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState<CheckState | null>(null);
  const values = form?.op === spec.op ? form.values : form ? switchValues(spec, form.values) : initialValues(spec, pre);
  const set = (key: string, v: FieldValue) => setForm({ op: spec.op, values: { ...values, [key]: v } });
  const built = paramsOf(spec, values);
  const key = built.params ? JSON.stringify([spec.op, built.params]) : null;

  useEffect(() => {
    if (!key || !built.params) return;
    let live = true;
    const params = built.params;
    const timer = setTimeout(() => {
      checkOperation({ op: spec.op, params })
        .then((summary) => live && setCheck({ key, summary }))
        .catch((e: unknown) => live && setCheck({ key, refusal: refusalOf(e) }));
    }, 350);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps -- the key holds the operation and its parameters

  const now = check?.key === key ? check : null;
  const note = openWireframeNote(spec, values, wireframes);
  const refusal = now?.refusal ?? (note ? { text: note.text, fix: note.fix } : null);
  const start = async () => {
    if (!built.params) return;
    setBusy(true);
    setError(null);
    const before = startsStore.get().seq;
    const r = await app.registry.execute("model.operation", { op: spec.op, params: JSON.stringify(built.params), ...(name.trim() ? { name: name.trim() } : {}) });
    setBusy(false);
    const s = startsStore.get();
    if (!r.ok) return setError(r.result);
    if (s.seq > before && s.last) onStarted(s.last);
    else closeWindow(winId);
  };

  return (
    <div className={css.win} data-testid="op-window" data-op={spec.op}>
      <div className={css.winBody}>
        {specs.length > 1 && (
          <div className={css.row} role="radiogroup" aria-label="Операция" data-testid="op-choice">
            {specs.map((s) => (
              <label key={s.op} className={css.check}>
                <input type="radio" name={`op-${winId}`} checked={s.op === spec.op} onChange={() => setOpKey(s.op)} />
                {s.title}
              </label>
            ))}
          </div>
        )}
        {visibleFields(spec, values).map((f) => (
          <FieldInput key={f.key} field={f} value={values[f.key]} data={{ wireframes, files, sets }} invalid={built.field === f.key} onChange={(v) => set(f.key, v)} />
        ))}
        {spec.result !== "report" || values.report_only !== true ? (
          <label className={css.formRow}>
            <span>Название результата</span>
            <input className={css.input} value={name} placeholder="по операции и исходным" aria-label="Название результата" maxLength={400} data-testid="op-name" onChange={(e) => setName(e.target.value)} />
          </label>
        ) : null}
        <p className={css.note}>{RESULT_NOTES[spec.result]}</p>
        {refusal ? (
          <div className={css.group} role="alert" data-testid="op-refusal">
            <p className={css.error}>{refusal.text}</p>
            {refusal.fix && (
              <div className={css.row}>
                <button type="button" className={css.btn} data-testid="op-fix" onClick={() => void app.run("model.repair", { wireframe_id: refusal.fix!.wireframe_id })}>
                  Исправить каркас
                </button>
              </div>
            )}
          </div>
        ) : now?.summary ? (
          <div data-testid="op-check">
            <p className={css.note}>{summaryText(now.summary)}</p>
            {now.summary.warning && <p className={css.warn}>{now.summary.warning}</p>}
          </div>
        ) : null}
        {error && (
          <p className={css.error} role="alert">
            {error}
          </p>
        )}
      </div>
      <div className={css.footer}>
        <span className={`${css.note} ${css.grow}`}>{built.error ?? "Операция идёт как задание: ход и журнал — на вкладке «Задания»."}</span>
        <button type="button" className={css.btn} onClick={() => closeWindow(winId)}>
          Отмена
        </button>
        <button type="button" className={css.primary} disabled={!built.params || busy || !!now?.refusal} data-testid="op-run" onClick={() => void start()}>
          Выполнить
        </button>
      </div>
    </div>
  );
}

/** The job of the window: state, progress, «Отменить»; the report when it is done, the error when it failed. */
function Follow({ winId, started, onBack }: { winId: string; started: Started; onBack: () => void }) {
  const app = useApp();
  const job = useStore(jobsStore, (s) => s.jobs.find((j) => j.source === started.source && j.id === started.id));
  const status = job?.status ?? "queued";
  const active = status === "queued" || status === "running";
  const made = job?.result ? reportProps(job, started) : null;
  return (
    <div className={css.win} data-testid="op-follow" data-status={status}>
      <div className={css.winBody}>
        <p>
          «{started.title}» — задание №{started.id}: <b data-testid="op-follow-status">{STATUS_TITLES[status]}</b>
        </p>
        {active && <progress max={1} value={job?.progress ?? 0} aria-label="Ход операции" style={{ width: "100%" }} />}
        {active && job?.message && <p className={css.note}>{job.message}</p>}
        {active && job?.why_waiting && <p className={css.note}>{job.why_waiting}</p>}
        {started.warning && <p className={css.warn}>{started.warning}</p>}
        {status === "done" && made && (
          <p className={css.note}>
            {made.wireframeIds.length ? `Создано каркасов: ${made.wireframeIds.length} — проверка поставлена в очередь. ` : ""}
            {made.layerIds.length ? `Создано слоёв линий: ${made.layerIds.length}. ` : ""}
            Отчёт — в отдельном окне.
          </p>
        )}
        {status === "failed" && job?.error && (
          <p className={css.error} role="alert">
            {job.error.message} {job.error.hint}
          </p>
        )}
        {status === "cancelled" && <p className={css.note}>Операция отменена: ничего не записано.</p>}
      </div>
      <div className={css.footer}>
        <span className={css.grow} />
        {active && (
          <button type="button" className={css.btn} data-testid="op-cancel" onClick={() => void app.run("jobs.cancel", { job_id: started.id })}>
            Отменить
          </button>
        )}
        {(status === "failed" || status === "cancelled") && (
          <button type="button" className={css.btn} onClick={onBack}>
            К параметрам
          </button>
        )}
        {status === "done" && job && (
          <button type="button" className={css.btn} data-testid="op-report-open" onClick={() => openReport(reportProps(job, started))}>
            Отчёт
          </button>
        )}
        <button type="button" className={css.primary} data-testid="op-close" onClick={() => closeWindow(winId)}>
          Закрыть
        </button>
      </div>
    </div>
  );
}
