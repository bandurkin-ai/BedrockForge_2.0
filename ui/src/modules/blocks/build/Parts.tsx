/**
 * The parts the windows of the building share (ТЗ 8.7.3, 8.7.5): the words under a field of the form (with «Перечитать»
 * for a 409), three numbers X / Y / Z, the list of wireframes with the code and the priority of each, the course of a
 * run (the stages, «Отменить», the refusal, the cancel) and the warnings. Everything is told in Russian; a window is
 * floating, not modal.
 */
import { useSyncExternalStore, type ReactNode } from "react";
import css from "../../../shell/PropsParts.module.css";
import type { Store } from "../../../shell/store";
import { jobsStore, STATUS_TITLES } from "../../jobs/state";
import { sceneObjects } from "../../scene/objects";
import { loadBlocks } from "../state";
import type { WireRow } from "./fillForm";
import mod from "./Build.module.css";
import { stageIndex } from "./format";
import { buildStore, setIssue, type OpKind } from "./state";
import { cancelOp } from "./run";

export function useSel<T, R>(store: Store<T>, select: (s: T) => R): R {
  const get = () => select(store.get());
  return useSyncExternalStore(store.subscribe, get, get);
}

/** The words under a field of the form: the first wrong thing of the form or the refusal of the core. */
export function Issue({ kind, field, index }: { kind: OpKind; field: string; index?: number }) {
  const issue = useSel(buildStore, (s) => s.issues[kind]);
  if (!issue || issue.field !== field || (index !== undefined && issue.index !== index)) return null;
  const again = async () => {
    await loadBlocks();
    setIssue(kind, null);
  };
  return (
    <p className={css.error} role="alert" data-testid={`build-issue-${kind}-${field}`}>
      {issue.message} {issue.hint}
      {issue.reread && (
        <button type="button" className={`${css.btn} ${mod.reread}`} data-testid={`build-reread-${kind}`} onClick={() => void again()}>
          Перечитать
        </button>
      )}
    </p>
  );
}

/** Three numbers (X, Y, Z or the corners of a box). */
export function Triple({ labels, values, onChange, testId, disabled, invalid }: { labels: readonly string[]; values: readonly string[]; onChange: (next: string[]) => void; testId: string; disabled?: boolean; invalid?: boolean }) {
  return (
    <div className={mod.row3}>
      {labels.map((l, i) => (
        <label key={l}>
          <span>{l}</span>
          <input className={css.input} value={values[i]} inputMode="decimal" spellCheck={false} disabled={disabled} aria-label={l} aria-invalid={invalid} data-testid={`${testId}-${i}`} onChange={(e) => onChange(values.map((v, k) => (k === i ? e.target.value : v)))} />
        </label>
      ))}
    </div>
  );
}

/** The list of the wireframes of a fill / a coding: the order, the code and the priority of each (a bigger number is the main one). */
export function WireList({ kind, rows, onChange, disabled }: { kind: "fill" | "code"; rows: WireRow[]; onChange: (rows: WireRow[]) => void; disabled?: boolean }) {
  const wireframes = useSel(sceneObjects, (s) => s.wireframes).filter((w) => w.dtm === null);
  const issue = useSel(buildStore, (s) => s.issues[kind]);
  const byId = new Map(wireframes.map((w) => [w.id, w]));
  const free = wireframes.filter((w) => !rows.some((r) => r.id === w.id));
  const move = (i: number, d: number) => {
    const next = [...rows];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    onChange(next);
  };
  const patch = (i: number, p: Partial<WireRow>) => onChange(rows.map((r, k) => (k === i ? { ...r, ...p } : r)));
  return (
    <div className={mod.wires} data-testid={`build-${kind}-wires`}>
      {rows.map((r, i) => {
        const w = byId.get(r.id);
        const open = w?.stats?.closed === false;
        const bad = issue?.field === "wireframes" && issue.index === i;
        return (
          <div key={r.id} className={mod.wire} aria-invalid={bad} data-testid={`build-${kind}-wire-${r.id}`}>
            <span className={mod.name} title={w ? w.name : `Каркас №${r.id} не найден в проекте`}>
              {w ? w.name : `№${r.id} (нет в проекте)`}
              {open && <span className={mod.open} title="Каркас не замкнут: заполнять можно только замкнутыми телами"> · не замкнут</span>}
            </span>
            <input className={css.input} value={r.code} placeholder={w?.name ?? "код"} disabled={disabled} aria-label={`Код каркаса ${w?.name ?? r.id}`} data-testid={`build-${kind}-code-${r.id}`} onChange={(e) => patch(i, { code: e.target.value })} />
            <input className={css.input} value={r.priority} placeholder="приор." inputMode="numeric" disabled={disabled} aria-label={`Приоритет каркаса ${w?.name ?? r.id}`} data-testid={`build-${kind}-priority-${r.id}`} onChange={(e) => patch(i, { priority: e.target.value })} />
            <span className={mod.tools}>
              <button type="button" disabled={disabled || i === 0} aria-label="Выше" title="Выше: при равных приоритетах главнее верхний" data-testid={`build-${kind}-up-${r.id}`} onClick={() => move(i, -1)}>
                ↑
              </button>
              <button type="button" disabled={disabled || i === rows.length - 1} aria-label="Ниже" data-testid={`build-${kind}-down-${r.id}`} onClick={() => move(i, 1)}>
                ↓
              </button>
              <button type="button" disabled={disabled} aria-label="Убрать" title="Убрать каркас из списка" data-testid={`build-${kind}-remove-${r.id}`} onClick={() => onChange(rows.filter((_, k) => k !== i))}>
                ×
              </button>
            </span>
            {bad && <Issue kind={kind} field="wireframes" index={i} />}
          </div>
        );
      })}
      <select className={css.input} value="" aria-label="Добавить каркас" disabled={disabled || !free.length} data-testid={`build-${kind}-add`} onChange={(e) => e.target.value && onChange([...rows, { id: Number(e.target.value), code: "", priority: "" }])}>
        <option value="">{wireframes.length ? (free.length ? "+ добавить каркас…" : "все каркасы уже в списке") : "в проекте нет каркасов"}</option>
        {free.map((w) => (
          <option key={w.id} value={w.id}>
            {w.name}
            {w.stats?.closed === false ? " (не замкнут)" : ""}
          </option>
        ))}
      </select>
      <p className={css.note}>Приоритет — целое число: больше — главнее; пусто — по порядку списка. Код по умолчанию — имя каркаса.</p>
      {issue?.field === "wireframes" && issue.index === undefined && <Issue kind={kind} field="wireframes" />}
    </div>
  );
}

/** The warnings of a job («Каркас не пересекает сетку в плане» and the rest): as the core wrote them. */
export function Warnings({ list, testId }: { list: readonly string[]; testId: string }) {
  if (!list.length) return null;
  return (
    <ul className={mod.warn} data-testid={testId} role="status">
      {list.map((w) => (
        <li key={w}>{w}</li>
      ))}
    </ul>
  );
}

/** The course and the end of the run of an operation: the stages, «Отменить», the refusal, the cancel; the result goes to `children`. */
export function RunPanel<R>({ kind, stages, idle, children }: { kind: OpKind; stages: readonly string[]; idle: string; children: (result: R) => ReactNode }) {
  const run = useSel(buildStore, (s) => s.runs[kind]);
  const live = useSel(jobsStore, (s) => (run.job ? s.jobs.find((j) => j.source === run.job?.source && j.id === run.job?.id) : undefined));
  const pct = Math.round((live?.progress ?? 0) * 100);
  const at = stageIndex(stages, live?.message);
  return (
    <>
      {run.phase === "idle" && (
        <p className={mod.empty} data-testid={`build-${kind}-idle`}>
          {idle}
        </p>
      )}
      {run.phase === "running" && (
        <div data-testid={`build-${kind}-progress`} data-status={live?.status ?? "queued"}>
          <h4 className={mod.head}>{run.label}</h4>
          <p className={css.note} data-testid={`build-${kind}-stage`}>
            {live?.cancel_requested ? "Отмена…" : (STATUS_TITLES[live?.status ?? "queued"] ?? "")}
            {live?.status === "running" && ` · ${pct}%${live.message ? ` · ${live.message}` : ""}`}
          </p>
          <div className={mod.progress}>
            <div style={{ width: `${pct}%` }} />
          </div>
          <ol className={mod.stages} data-testid={`build-${kind}-stages`}>
            {stages.map((s, i) => (
              <li key={s} aria-current={i === at ? "step" : undefined} data-done={at > i}>
                {s}
              </li>
            ))}
          </ol>
          {live?.status === "queued" && live.why_waiting && <p className={css.note}>{live.why_waiting}</p>}
          <button type="button" className={css.btn} disabled={!run.job || !!live?.cancel_requested} title="Остановить расчёт: ничего не будет записано" data-testid={`build-${kind}-cancel`} onClick={() => void cancelOp(kind)}>
            {live?.cancel_requested ? "Отмена…" : "Отменить"}
          </button>
        </div>
      )}
      {run.phase === "cancelled" && (
        <p className={css.note} data-testid={`build-${kind}-cancelled`}>
          Отменено: ничего не записано.
        </p>
      )}
      {run.phase === "failed" && run.error && (
        <p className={css.error} role="alert" data-testid={`build-${kind}-failed`}>
          {run.error.message} {run.error.hint}
        </p>
      )}
      {run.phase === "done" && run.result !== null && children(run.result as R)}
    </>
  );
}
