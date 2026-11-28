import { CHOICE_COMMAND, choiceFailures } from "../importing/choice";
import type { Job } from "./state";
import css from "./Jobs.module.css";

/** The files an import of the job refused with a choice (`failed[].choices`: a block file the grid cannot hold, a turn the
 * three centres do not fit): each with the text of the core and «Открыть мастер с выбором» (`import.blocks_choice`);
 * a file without a choice has no button (Э27 27.12). */
export function ChoiceRows({ job, run }: { job: Job; run: (command: string, args: Record<string, unknown>) => unknown }) {
  return (
    <>
      {choiceFailures(job).map((f) => (
        <div key={f.path} className={css.failed} data-testid="job-choice" data-code={f.code}>
          «{f.file}» не загружен: {f.message}{" "}
          <button
            type="button"
            className={css.btn}
            data-testid="job-choice-open"
            title="Открыть мастер импорта блочной модели с этим файлом и панелью выбора"
            onClick={() => void run(CHOICE_COMMAND, { job_id: job.id, source: job.source, path: f.path })}
          >
            Открыть мастер с выбором
          </button>
        </div>
      ))}
    </>
  );
}
