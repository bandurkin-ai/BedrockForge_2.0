/**
 * The client of the export of a block model (ТЗ 8.7.7, 7.35 Д-17; ADR 0023 §12; docs/progress/e17-api.md §11; core —
 * `blocks/export.py`, `blocks/routes_export.py`; the answers: `core/tests/data/blocks/export_window_answers.json`):
 *
 * - `POST /blocks/{id}/export {format, path, fields?, filter_id?, restrict?, header?, encoding?, delimiter?, decimal?,
 *   empty?, overwrite?}` → `{source, id}` of the job `blocks.export` (404 `block_model_not_found`, `block_field_not_found`,
 *   `filter_not_found`; 409 `files_exist {files}`; 422 `bad_param {param}`, `bad_path`, `omf_subblocks`,
 *   `omf_not_exportable`, `filter_field_missing`);
 * - the job — `GET /jobs/{source}/{id}`: `result` is `ExportResult`; a failure of the job (`export_encoding`,
 *   `empty_code_taken`, `export_empty`, `export_label_too_long`) is its `error {code, message, hint}`.
 * No React.
 */
import { api } from "../../../shell/api";
import { startedOf, type Started } from "../../qaqc/setupApi";
import { startWithOverwrite } from "../../rasters/export";
import { readResult, type ExportBody, type ExportResult } from "./model";

export type { Started };

/** `POST /blocks/{id}/export`; the file is there (409 `files_exist`) — `ask` says whether to replace it, null — not. */
export async function startExport(modelId: number, body: ExportBody, ask: (files: string[]) => Promise<boolean>): Promise<Started | null> {
  return startWithOverwrite(
    async (overwrite) => startedOf(await api.post<Record<string, unknown>>(`/blocks/${modelId}/export`, overwrite || body.overwrite ? { ...body, overwrite: true } : body)),
    ask,
  );
}

const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);

/** The record of the job as the window needs it. */
export interface ExportJob {
  status: string;
  progress: number;
  message: string | null;
  error: { code: string; message: string; hint: string } | null;
  result: ExportResult | null;
}

export async function loadExportJob(job: Started): Promise<ExportJob> {
  const j = obj(await api.get(`/jobs/${job.source}/${job.id}`));
  const e = obj(j.error);
  return {
    status: str(j.status) ?? "",
    progress: typeof j.progress === "number" ? j.progress : 0,
    message: str(j.message),
    error: typeof e.code === "string" ? { code: e.code, message: str(e.message) ?? "", hint: str(e.hint) ?? "" } : null,
    result: readResult(j.result),
  };
}
