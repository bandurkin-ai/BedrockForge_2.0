import type { CommitResponse, UploadResponse, ValidateRequest, ValidateResponse } from "../api";

/** A file of the drillhole import wizard: how it is read into a table of the set. */
export interface Draft {
  key: string;
  upload: UploadResponse;
  kind: string;
  mapping: Record<string, string>;
  components: Record<string, { checked: boolean; unit: string }>;
  attributes: string[];
  /** interval, event and assay tables: the name in the set */
  tableName: string;
  result: ValidateResponse | null;
  committed: CommitResponse | null;
  error: string | null;
}

/** Kinds of the third step: tables along the hole. */
export const TABLE_KINDS = ["assay", "interval", "event"];
export const MAIN_ASSAYS = "Пробы";

const stem = (filename: string) => filename.replace(/\.[^.]+$/, "");

/** A draft of the file read as `kind`, with the mapping the server suggests. */
export function makeDraft(upload: UploadResponse, kind: string, tableName?: string): Draft {
  const suggestion = upload.suggestions[kind];
  const mapping = { ...(suggestion?.mapping ?? {}) };
  const components: Draft["components"] = {};
  for (const c of suggestion?.components ?? []) components[c.column] = { checked: true, unit: c.unit };
  const mapped = new Set(Object.values(mapping));
  // An interval or event table keeps every column that is not hole / from / to.
  const attributes = kind === "interval" || kind === "event" ? upload.columns.map((c) => c.name).filter((c) => !mapped.has(c)) : [];
  return {
    key: upload.file_id, upload, kind, mapping, components, attributes,
    tableName: TABLE_KINDS.includes(kind) ? tableName ?? stem(upload.filename) : "",
    result: null, committed: null, error: null,
  };
}

/** The kind a file gets on the tables step: what the server detected, or an interval table. */
export const tableKind = (upload: UploadResponse) =>
  upload.detected_kind && TABLE_KINDS.includes(upload.detected_kind) ? upload.detected_kind : "interval";

export function buildRequest(d: Draft): ValidateRequest {
  const mapped = new Set(Object.values(d.mapping).filter(Boolean));
  const comps = d.kind === "assay"
    ? Object.entries(d.components).filter(([column, s]) => s.checked && !mapped.has(column)).map(([column, s]) => ({ column, unit: s.unit }))
    : [];
  return {
    file_id: d.upload.file_id,
    kind: d.kind,
    mapping: Object.fromEntries(Object.entries(d.mapping).filter(([, v]) => v)),
    components: comps,
    attributes: d.attributes.filter((a) => !mapped.has(a) && !comps.some((c) => c.column === a)),
  };
}

/** Labels of the required fields that have no column yet. */
export function missingFields(d: Draft, fields: { key: string; label: string; required: boolean }[]): string[] {
  return fields.filter((f) => f.required && !d.mapping[f.key]).map((f) => f.label);
}

/**
 * Sorts uploaded files over the wizard: the first collar file and the first survey file take their
 * steps (if free), everything else goes to the tables step.
 */
export function distribute(uploads: UploadResponse[], taken: { collar: boolean; survey: boolean }) {
  const out: { collar: UploadResponse | null; survey: UploadResponse | null; tables: UploadResponse[] } = { collar: null, survey: null, tables: [] };
  for (const u of uploads) {
    if (u.detected_kind === "collar" && !taken.collar && !out.collar) out.collar = u;
    else if (u.detected_kind === "survey" && !taken.survey && !out.survey) out.survey = u;
    else out.tables.push(u);
  }
  return out;
}

/** A table name not yet used on the tables step (two files with one name would replace each other). */
export function uniqueName(name: string, used: string[]): string {
  const taken = new Set(used.map((n) => n.trim().toLowerCase()));
  if (!taken.has(name.trim().toLowerCase())) return name;
  for (let i = 2; ; i++) if (!taken.has(`${name} ${i}`.toLowerCase())) return `${name} ${i}`;
}
