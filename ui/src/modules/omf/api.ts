/**
 * The export to OMF v1 as the core offers it (ТЗ 7.24; ADR 0017 §4; `core/bf_core/exporting/omf_router.py`):
 * `GET /omf/exportable` — the three lists of the window, each object with `available` and the `reason`
 * {message, hint} of one that is not; `POST /omf/export {wireframe_ids, line_file_ids, block_model_ids, path,
 * overwrite?}` → the job `export.omf`. 422 `nothing_to_export` / `omf_not_exportable`, 404 `object_not_found`,
 * 409 `files_exist {files}` — the window asks «Заменить?» in its own window and repeats with `overwrite`.
 * `followExportable` — the lists of the open window, read again on `objects.changed`, the newest answer only.
 */
import { api } from "../../shell/api";
import { onCoreEvent } from "../jobs/state";

export interface Reason {
  message: string;
  hint: string;
}

interface Exportable {
  id: number;
  name: string;
  folder: string | null;
  available: boolean;
  reason: Reason | null;
}

export interface ExportableWireframe extends Exportable {
  dtm: unknown;
  triangles: number;
}

export interface ExportableLineFile extends Exportable {
  layers: number;
  points: number;
}

export interface ExportableBlockModel extends Exportable {
  cells: number;
}

export interface ExportableLists {
  wireframes: ExportableWireframe[];
  line_files: ExportableLineFile[];
  block_models: ExportableBlockModel[];
}

export interface OmfExportBody {
  wireframe_ids: number[];
  line_file_ids: number[];
  block_model_ids: number[];
  path: string;
}

/** What the job `export.omf` gives when it is done. */
export interface OmfExportResult {
  path: string;
  format: string;
  wireframes: number;
  line_layers: number;
  block_models: number;
  cells: number;
  warnings: string[];
}

export async function loadExportable(): Promise<ExportableLists> {
  const r = await api.get<Partial<ExportableLists>>("/omf/exportable");
  return { wireframes: r.wireframes ?? [], line_files: r.line_files ?? [], block_models: r.block_models ?? [] };
}

/**
 * The lists read now and again on every `objects.changed` while the window is open. Only the answer of the newest
 * read is applied (as `datamanager/api.ts`): an older answer that comes later would put back a list without the
 * new object, and `keepChosen` would uncheck it (Э11 11.21). `first` — the first answer applied. Returns the
 * unsubscribe; nothing is applied after it.
 */
export function followExportable(
  apply: (lists: ExportableLists, first: boolean) => void,
  fail: (e: unknown) => void,
  load: () => Promise<ExportableLists> = loadExportable,
  events: (fn: (event: string) => void) => () => void = onCoreEvent,
): () => void {
  let seq = 0;
  let alive = true;
  let first = true;
  const read = () => {
    const mine = ++seq;
    load().then(
      (l) => {
        if (!alive || mine !== seq) return;
        apply(l, first);
        first = false;
      },
      (e: unknown) => {
        if (alive && mine === seq) fail(e);
      },
    );
  };
  read();
  const off = events((event) => {
    if (event === "objects.changed") read();
  });
  return () => {
    alive = false;
    off();
  };
}

export function startOmfExport(body: OmfExportBody, overwrite = false): Promise<{ source: string; id: number }> {
  return api.post("/omf/export", { ...body, overwrite });
}

/** The folder of projects (`GET /projects` → `root`): `Документы\BedrockForge\Проекты` unless «Настройка» chose another. */
export async function projectsRoot(): Promise<string> {
  return (await api.get<{ root: string }>("/projects")).root;
}
