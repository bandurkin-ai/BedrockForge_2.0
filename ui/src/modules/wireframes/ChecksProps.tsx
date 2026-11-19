/**
 * «Проверки» and «Источник» of «Свойства» of a wireframe (ТЗ 7.21; ADR 0013 §6): the measures taken when it was
 * stored, what the job `wireframe.check` found, «Проверить» — the command `wireframe.check`; while its job is
 * queued or runs the button waits and tells how far it is. The data — `checks.ts`.
 */
import { Facts } from "../../shell/PropsParts";
import { useStore } from "../../shell/useStore";
import css from "../../shell/PropsParts.module.css";
import { jobsStore } from "../jobs/state";
import type { WireframeSource, WireframeStats } from "../scene/objects";
import { activeCheck, checkError, checkFacts, checkNotes, checkProgress, checksLayerStore, doneChecks, measureFacts, sourceFacts } from "./checks";

type Run = (id: string, args?: Record<string, unknown>) => Promise<boolean>;

export function ChecksBlock({ id, stats, run }: { id: number; stats: WireframeStats | undefined; run: Run }) {
  const job = useStore(jobsStore, (s) => activeCheck(s.jobs, id));
  const missing = useStore(checksLayerStore, (s) => s.missing.includes(id));
  const failed = checkError(stats);
  const done = doneChecks(stats);
  return (
    <fieldset className={css.group} data-testid="props-checks" data-state={job ? "running" : failed ? "failed" : done ? "done" : "none"} data-pairs={done?.pairs ?? ""} data-conflicts={done?.normal_conflicts ?? ""}>
      <legend>Проверки</legend>
      <Facts facts={[...measureFacts(stats), ...checkFacts(stats)]} />
      {failed && (
        <p className={css.error} role="alert">
          {failed}
        </p>
      )}
      {checkNotes(stats, missing).map((note) => (
        <p key={note} className={css.note}>
          {note}
        </p>
      ))}
      <div className={css.row}>
        <button type="button" className={css.btn} disabled={job !== null} title="Задание «Проверка и копии для показа»: согласованность нормалей, самопересечения, упрощённые копии для 3D-окна" onClick={() => void run("wireframe.check", { wireframe_id: id })}>
          Проверить
        </button>
        {job && (
          <span className={css.note} role="status">
            {checkProgress(job)}
          </span>
        )}
      </div>
    </fieldset>
  );
}

export function SourceBlock({ source }: { source: WireframeSource | null | undefined }) {
  const facts = sourceFacts(source);
  if (!facts.length) return null;
  return (
    <fieldset className={css.group} data-testid="props-source">
      <legend>Источник</legend>
      <Facts facts={facts} />
    </fieldset>
  );
}
