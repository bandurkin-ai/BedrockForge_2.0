/**
 * The refusals of `import.objects` that come with a choice (Э27 27.12; ADR 0030 decisions 10–13): a block file the
 * grid cannot hold (`blocks_unaligned`, with `diag`) or whose three centres do not fit the turn (`rotation_mismatch`)
 * lands in `result.failed[]` with `choices`. The window of the job offers «Открыть мастер с выбором»: the wizard
 * of the files is opened with that file, and its composition tells the same refusal (the same object is in
 * `inspect`, `files[].blocks.error`), so the panel of the choice is there at once. No React.
 */
import type { Job } from "../jobs/state";

export const IMPORT_OBJECTS = "import.objects";
export const CHOICE_COMMAND = "import.blocks_choice";

export interface ChoiceFailure {
  path: string;
  file: string;
  code: string;
  message: string;
  hint: string;
  /** the ids of the choices the core offered («grid», «cancel») */
  choices: string[];
  /** the refusal carries a diagnosis (`blocks_unaligned`) */
  diag: boolean;
}

const text = (v: unknown): string => (typeof v === "string" ? v : "");

/** The failed files of a job `import.objects` that carry `choices`; [] — none (or not such a job). */
export function choiceFailures(job: Pick<Job, "kind" | "result">): ChoiceFailure[] {
  if (job.kind !== IMPORT_OBJECTS) return [];
  const failed = job.result?.failed;
  if (!Array.isArray(failed)) return [];
  const out: ChoiceFailure[] = [];
  for (const raw of failed as unknown[]) {
    if (!raw || typeof raw !== "object") continue;
    const f = raw as Record<string, unknown>;
    const choices = Array.isArray(f.choices) ? f.choices.map((c) => text((c as { id?: unknown } | null)?.id)).filter(Boolean) : [];
    const path = text(f.path);
    if (!choices.length || !path) continue;
    out.push({ path, file: text(f.file) || path, code: text(f.code), message: text(f.message), hint: text(f.hint), choices, diag: !!f.diag && typeof f.diag === "object" });
  }
  return out;
}

/** The folder of the tree the job was started with (it goes with the file into the wizard); null — none. */
export function jobFolder(job: Pick<Job, "params">): string | null {
  const folder = job.params?.folder;
  return typeof folder === "string" && folder ? folder : null;
}

/** What the window of the wizard is told: why it was opened (a note above the composition). */
export interface ChoiceNote {
  file: string;
  code: string;
  message: string;
}

export const noteOf = (f: ChoiceFailure): ChoiceNote => ({ file: f.file, code: f.code, message: f.message });
