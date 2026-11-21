// «Настройка QAQC» against the real shapes of the core (`qaqc/normalize.py` `parse_mapping` / `auto_mapping`,
// `jobs.py` `inspect_file` / `inspect_table`, `router.py`, `config.read_crm_file`; docs/progress/e14-api.md): the
// mapping of the form goes to the core flat (roles at the top, `elements[].dl_column`) and comes back the same; the
// answer of `inspect` gives its mapping, its sheets (`info.sheets`) and the samples of the columns (`samples`); the
// source of `inspect` carries its kind; an empty TYPE (`code: null`) is a row «(пусто)»; `waitJob` ends on an abort,
// on another project and when the job is gone; `/crm/read` gives its problems. Fetch stub.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { jobsStore } from "../src/modules/jobs/state";
import { projectStore } from "../src/modules/projects/state";
import { crmProblemLines } from "../src/modules/qaqc/setup/CrmTab";
import { sourceText } from "../src/modules/qaqc/setup/DataTab";
import { codeRows, typeOfCode } from "../src/modules/qaqc/setup/forms";
import { samplesText } from "../src/modules/qaqc/setup/ImportForm";
import { startMapping } from "../src/modules/qaqc/setup/mapping";
import { inspectOf, inspectSource, mappingFromCore, mappingToCore, readCrmFile, sourceBody, waitJob, WAIT_ABORTED, type Mapping } from "../src/modules/qaqc/setupApi";
import { setCoreTarget, type CoreError } from "../src/shell/api";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

// `POST /data/inspect {kind: "file", path}` of a book as `jobs.inspect_file` answers it
const INSPECT_FILE = {
  kind: "file",
  columns: [
    { name: "SAMPLE_ID", kind: "text", samples: ["S1", "S2"] },
    { name: "TYPE", kind: "text", samples: ["ORIG", "STD"] },
    { name: "LAB", kind: "text", samples: ["ALS"] },
    { name: "AU_ppm", kind: "number", samples: ["1.2", "<0.01"] },
    { name: "AU_DL", kind: "number", samples: ["0.01"] },
    { name: "ZONE", kind: "text", samples: ["N"] },
  ],
  rows: 1200,
  rows_exact: false,
  mapping: {
    v: 1, sample_id: "SAMPLE_ID", hole: null, type: "TYPE", parent_id: null, crm_id: null, lab: "LAB", batch: null, date: null, seq: null,
    elements: [{ column: "AU_ppm", name: "AU", unit: "ppm", dl_column: "AU_DL" }], extra: [],
  },
  preview: [["S1", "ORIG", "ALS", "1.2", "0.01", "N"]],
  info: { format: "xlsx", encoding: null, delimiter: null, header_row: 1, sheet: "Контроль", sheets: ["Контроль", "Лист2"] },
  summary: "…",
  warnings: [],
};

describe("the mapping of the core", () => {
  it("the answer of inspect: the flat mapping becomes the form's, the sheets come from `info`, the samples stay", () => {
    const a = inspectOf(INSPECT_FILE as never);
    expect(a.mapping).toEqual({ fields: { sample_id: "SAMPLE_ID", type: "TYPE", lab: "LAB" }, elements: [{ column: "AU_ppm", name: "AU", unit: "ppm", dl: "AU_DL" }], extra: [] });
    expect(a.sheets).toEqual(["Контроль", "Лист2"]);
    expect(a.sheet).toBe("Контроль");
    expect(startMapping(a)).toEqual(a.mapping);
    expect(samplesText(a.columns[3].samples)).toBe("1.2; <0.01");
    expect(samplesText(undefined)).toBe("");
    // a table: no sheets
    expect(inspectOf({ ...INSPECT_FILE, kind: "table", info: { set_id: 3, table_id: 5, set_name: "Север", table_name: "Пробы", rev: 2 } } as never).sheets).toBeUndefined();
  });

  it("the body of `POST /data`: every role (none — null), `dl_column`, no unit — null; and back again", () => {
    const m: Mapping = { fields: { sample_id: "SAMPLE_ID", type: "TYPE", batch: "BATCH" }, elements: [{ column: "AU_ppm", name: " AU ", unit: "ppm", dl: "AU_DL" }, { column: "Ag", name: "Ag", unit: "" }], extra: ["ZONE"] };
    const core = mappingToCore(m);
    expect(core).toEqual({
      v: 1, sample_id: "SAMPLE_ID", hole: null, type: "TYPE", parent_id: null, crm_id: null, lab: null, batch: "BATCH", date: null, seq: null,
      elements: [{ column: "AU_ppm", name: "AU", unit: "ppm", dl_column: "AU_DL" }, { column: "Ag", name: "Ag", unit: null, dl_column: null }], extra: ["ZONE"],
    });
    expect(core).not.toHaveProperty("fields");
    expect(mappingFromCore(core)).toEqual({ ...m, elements: [{ ...m.elements[0], name: "AU" }, { ...m.elements[1], dl: null }] });
    expect(mappingFromCore(null)).toBeNull();
    expect(mappingFromCore([])).toBeNull();
    expect(mappingFromCore({ elements: [{ name: "X" }, { column: "CU" }] })).toEqual({ fields: {}, elements: [{ column: "CU", name: "CU", unit: "", dl: null }], extra: [] });
  });

  it("the source of inspect and of the import carries its kind", () => {
    expect(sourceBody({ kind: "file", path: "C:/qc.xlsx", sheet: "Лист2", encoding: null })).toEqual({ kind: "file", path: "C:/qc.xlsx", sheet: "Лист2" });
    expect(sourceBody({ kind: "table", set_id: 3, table: "5" })).toEqual({ kind: "table", set_id: 3, table: "5" });
    expect(sourceText({ source: { kind: "table", set_id: 3, table_id: 5, set_name: "Север", table_name: "Пробы", rev: 4 } })).toBe("таблица «Пробы» набора «Север», ревизия 4");
    expect(sourceText({ source: { kind: "file", name: "qc.csv", path: "C:/qc.csv" } })).toBe("файл «qc.csv»");
  });

  it("an empty TYPE (`code: null`) is a row of its own «(пусто)»: ORIG when «пустой код — ORIG», else not recognized", () => {
    const data = [{ type_codes: [{ code: "BLANK", n: 60, type: "BLANK" as const }, { code: null, n: 3, type: "ORIG" as const }, { code: "XX", n: 2, type: null }] }];
    const rows = codeRows(data, { BLANK: "BLANK" }, true);
    expect(rows.find((r) => r.code === "")).toEqual({ code: "", n: 3, type: "ORIG" });
    expect(codeRows(data, { BLANK: "BLANK" }, false).find((r) => r.code === "")?.type).toBeNull();
    expect(typeOfCode(null, {}, true)).toBe("ORIG");
    expect(rows[0]).toEqual({ code: "XX", n: 2, type: null }); // not recognized first
  });
});

describe("the requests", () => {
  let sent: { path: string; body: unknown }[];
  let jobsAnswer: () => Response;
  beforeEach(() => {
    sent = [];
    jobsAnswer = () => json({ jobs: [], workers: null });
    jobsStore.set({ jobs: [], workers: null, error: null });
    projectStore.set({ current: { uid: "p1" } as never, version: 1 });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const path = new URL(url).pathname.replace("/api/v1", "");
        sent.push({ path, body: init?.body ? JSON.parse(String(init.body)) : null });
        if (path === "/qaqc/data/inspect") return json(INSPECT_FILE);
        if (path === "/jobs") return jobsAnswer();
        if (path === "/qaqc/crm/read")
          return json({ rows: [{ crm_id: "OREAS 45", element: "AU", mean: 1, sd: 0.05, unit: "ppm", source: null }, { crm_id: "OREAS 47", element: "AU", mean: -1, sd: 0.1, unit: "ppm", source: null }], problems: [{ row: 1, message: "Среднее CRM должно быть больше 0 (строка 2)." }], columns: { crm_id: "Standard", mean: "Mean", sd: "SD" } });
        return json({ code: "not_found", message: "Нет", hint: "" }, 404);
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  });
  afterEach(() => {
    setCoreTarget(null);
    vi.unstubAllGlobals();
    projectStore.set({ current: null, version: 0 });
  });

  it("inspect sends the kind of the source and gives the form's mapping", async () => {
    const a = await inspectSource({ kind: "table", set_id: 3, table: "Пробы" });
    expect(sent[0]).toEqual({ path: "/qaqc/data/inspect", body: { kind: "table", set_id: 3, table: "Пробы" } });
    expect(a.mapping?.elements[0].dl).toBe("AU_DL");
  });

  const job = { source: "project", id: 7 };
  const done = { id: 7, source: "project", kind: "qaqc.import", status: "done", result: { data_id: 1 } };

  it("waitJob: resolves on the end of the job", async () => {
    jobsAnswer = () => json({ jobs: [{ ...done, status: "running" }], workers: null });
    const p = waitJob(job);
    await vi.waitFor(() => expect(jobsStore.get().jobs).toHaveLength(1));
    jobsStore.set({ jobs: [done as never] });
    await expect(p).resolves.toMatchObject({ id: 7, status: "done" });
  });

  it("waitJob: an abort, another project, a job gone — each ends the wait with its code", async () => {
    jobsAnswer = () => json({ jobs: [{ ...done, status: "running" }], workers: null });
    const ctl = new AbortController();
    const aborted = waitJob(job, ctl.signal);
    ctl.abort();
    await expect(aborted).rejects.toMatchObject({ code: WAIT_ABORTED });
    const before = new AbortController();
    before.abort();
    await expect(waitJob(job, before.signal)).rejects.toMatchObject({ code: WAIT_ABORTED });
    const moved = waitJob(job);
    projectStore.set({ current: { uid: "p2" } as never, version: 2 });
    await expect(moved).rejects.toMatchObject({ code: "project_changed" });
    // the core restarted: the list read without an error has no such job
    jobsAnswer = () => json({ jobs: [], workers: null });
    const lost = await waitJob(job).catch((e: CoreError) => e);
    expect((lost as CoreError).code).toBe("job_lost");
    expect((lost as CoreError).message).toContain("Задание №7");
    // the list could not be read (the core is away): the wait goes on
    jobsAnswer = () => json({ code: "core_offline", message: "Нет", hint: "" }, 503);
    let settled = false;
    const waiting = waitJob(job).then(
      () => (settled = true),
      () => (settled = true),
    );
    await vi.waitFor(() => expect(jobsStore.get().error).not.toBeNull());
    expect(settled).toBe(false);
    jobsStore.set({ jobs: [done as never], error: null });
    await waiting;
    expect(settled).toBe(true);
  });

  it("`/crm/read`: the rows, the problems and the columns; the problems in words", async () => {
    const r = await readCrmFile("C:/crm.xlsx");
    expect(r.rows).toHaveLength(2);
    expect(r.columns).toEqual({ crm_id: "Standard", mean: "Mean", sd: "SD" });
    expect(crmProblemLines(r.problems)).toEqual(["Среднее CRM должно быть больше 0 (строка 2)."]);
    expect(crmProblemLines([{ row: 0, message: "Пустой CRM_ID." }])).toEqual(["Строка 1 файла: Пустой CRM_ID."]);
    const many = Array.from({ length: 13 }, (_, i) => ({ row: i, message: `строка ${i + 1}: ошибка` }));
    expect(crmProblemLines(many)).toHaveLength(11);
    expect(crmProblemLines(many)[10]).toBe("…и ещё 3");
  });
});
