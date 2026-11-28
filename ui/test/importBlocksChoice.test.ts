// «Открыть мастер с выбором» (Э27, подшаг 27.12; ADR 0030 решения 10–13): a job `import.objects` that loaded some files
// and refused a block file with `choices` (`blocks_unaligned` with `diag`, `rotation_mismatch`) — the row of the file in
// «Задания» has the button, the command `import.blocks_choice` opens the wizard with that file, and the wizard's
// composition (the same refusal comes from `inspect`) holds the panel of the choice at once.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { unalignedOf, type BlocksInfo } from "../src/modules/blocks/import/model";
import { choiceFailures, jobFolder } from "../src/modules/importing/choice";
import { commands as importCommands } from "../src/modules/importing/commands";
import { ObjectsWizard, type Composition, type FileOk } from "../src/modules/importing/objects";
import { jobsStore, type Job } from "../src/modules/jobs/state";
import { ChoiceRows } from "../src/modules/jobs/ChoiceRows";
import { projectStore, type ProjectInfo } from "../src/modules/projects/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp } from "../src/shell/app";
import { startOpLog } from "../src/shell/opLog";
import { windowStore } from "../src/shell/windows";

const UNALIGNED = {
  path: "/d/model.csv",
  file: "model.csv",
  code: "blocks_unaligned",
  message: "Блоков, которые нельзя уложить в сетку модели: 6.",
  hint: "Задайте родителя и начало сетки вручную или отмените импорт файла.",
  count: 6,
  rows: [3],
  diag: { rows: 10, sample: 10, columns: {}, axes: [], candidates: [], causes: ["off_lattice"], text: ["По Z нет общего деления."] },
  choices: [
    { id: "grid", label: "Задать сетку вручную", grid: { corner: [0, 0, 0], size: [10, 10, 10], div: [5, 5, 1], tol: 0.01, rotation: 0 }, aligned: 0.62 },
    { id: "cancel", label: "Не импортировать этот файл" },
  ],
};
const ROTATION = { path: "/d/turned.dat", file: "turned.dat", code: "rotation_mismatch", message: "Три центра не совпали с поворотом.", hint: "Задайте поворот.", choices: [{ id: "grid", label: "Задать сетку вручную" }, { id: "cancel", label: "Не импортировать этот файл" }] };
const PLAIN = { path: "/d/noroles.csv", file: "noroles.csv", code: "no_coordinates", message: "Нет столбцов центров.", hint: "Назначьте роли." };

const job = (p: Partial<Job> & { failed?: unknown[] } = {}): Job => ({
  id: 7, source: "p1", kind: "import.objects", title: "Импорт файлов", queue: "heavy", status: "done", progress: 1, message: null, error: null,
  params: { folder: "Импорт/Карьер" }, result: { files: [{ file: "ok.csv", kind: "block_models" }], failed: p.failed ?? [UNALIGNED, ROTATION, PLAIN] },
  attempts: 1, max_attempts: 1, cancel_requested: false, created_at: "", started_at: null, finished_at: "2026-11-28T10:00:00Z", why_waiting: null, ...p,
});

const open = () => {
  vi.unstubAllGlobals();
  const app = createApp({ persist: false });
  vi.stubGlobal("window", { innerWidth: 1280, innerHeight: 800 });
  projectStore.set({ current: { uid: "p1" } as ProjectInfo });
  app.store.set({ projectName: "Проект" });
  return app;
};

beforeEach(() => windowStore.set({ windows: [] }));
afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  jobsStore.set({ jobs: [], workers: null, error: null });
});

describe("the failed files with a choice", () => {
  it("only the rows with `choices` count: the others (no_coordinates) have no button", () => {
    expect(choiceFailures(job()).map((f) => [f.file, f.code, f.diag, f.choices])).toEqual([
      ["model.csv", "blocks_unaligned", true, ["grid", "cancel"]],
      ["turned.dat", "rotation_mismatch", false, ["grid", "cancel"]],
    ]);
    expect(choiceFailures(job({ failed: [PLAIN] }))).toEqual([]);
    expect(choiceFailures(job({ failed: [{ ...UNALIGNED, choices: [] }] }))).toEqual([]);
    expect(choiceFailures(job({ result: null }))).toEqual([]);
    expect(choiceFailures(job({ kind: "export.objects" }))).toEqual([]);
    expect(jobFolder(job())).toBe("Импорт/Карьер");
    expect(jobFolder(job({ params: null }))).toBeNull();
  });
});

describe("the row of the job in «Задания»", () => {
  const render = (j: Job) => renderToStaticMarkup(createElement(ChoiceRows, { job: j, run: () => undefined }));

  it("the button «Открыть мастер с выбором» stands by each file with a choice, and by no other", () => {
    const html = render(job());
    expect(html.match(/data-testid="job-choice-open"/g)?.length).toBe(2);
    expect(html).toContain("Открыть мастер с выбором");
    expect(html).toContain("«model.csv» не загружен: Блоков, которые нельзя уложить в сетку модели: 6.");
    expect(html).not.toContain("noroles.csv");
  });

  it("no button when the core offered no choice", () => {
    const html = render(job({ failed: [PLAIN] }));
    expect(html).not.toContain("job-choice-open");
    expect(html).not.toContain("Открыть мастер с выбором");
  });

  it("the button runs the command with the job and the file", () => {
    const run = vi.fn();
    const rows = ChoiceRows({ job: job(), run }) as ReactElement<{ children: ReactElement<{ children: ReactNode[] }>[] }>;
    const buttons = rows.props.children.map((row) => (row.props.children as ReactElement<{ onClick: () => void }>[]).find((c) => isValidElement(c) && c.type === "button")!);
    buttons[1].props.onClick();
    expect(run).toHaveBeenCalledWith("import.blocks_choice", { job_id: 7, source: "p1", path: "/d/turned.dat" });
  });

  it("JobsPanel draws these rows", () => {
    expect(readFileSync(resolve(__dirname, "../src/modules/jobs/JobsPanel.tsx"), "utf-8")).toContain("<ChoiceRows job={j}");
  });
});

describe("the command import.blocks_choice", () => {
  it("is in the registry, in Russian, for the job and the file", () => {
    const cmd = importCommands.find((c) => c.id === "import.blocks_choice")!;
    expect(cmd.title).toBe("Открыть мастер с выбором");
    expect(Object.keys(cmd.params ?? {})).toEqual(["job_id", "source", "path"]);
    expect(cmd.required).toEqual(["job_id"]);
  });

  it("opens the wizard of block files with that file, the folder of the job and the note of the refusal", async () => {
    const app = open();
    jobsStore.set({ jobs: [job()], workers: null, error: null });
    expect(app.registry.reason("import.blocks_choice")).toBeNull();
    const r = await app.registry.execute("import.blocks_choice", { job_id: 7, source: "p1", path: "/d/turned.dat" });
    expect(r).toEqual({ ok: true, result: "Открыт мастер импорта блочной модели с выбором для «turned.dat»" });
    const win = windowStore.get().windows.find((w) => w.kind === "import-objects");
    expect(win?.props).toEqual({ paths: ["/d/turned.dat"], pick: "blocks", folder: "Импорт/Карьер", choice: { file: "turned.dat", code: "rotation_mismatch", message: "Три центра не совпали с поворотом." } });
  });

  it("without `path` — the first file with a choice", async () => {
    const app = open();
    jobsStore.set({ jobs: [job()], workers: null, error: null });
    await app.registry.execute("import.blocks_choice", { job_id: 7 });
    expect(windowStore.get().windows.find((w) => w.kind === "import-objects")?.props?.paths).toEqual(["/d/model.csv"]);
  });

  it("refuses with the reason: a job of another project, a job without a choice", async () => {
    const app = open();
    jobsStore.set({ jobs: [job({ source: "other" })], workers: null, error: null });
    const other = await app.registry.execute("import.blocks_choice", { job_id: 7, source: "other" });
    expect(other.ok).toBe(false);
    expect(JSON.stringify(other)).toContain("другому проекту");
    jobsStore.set({ jobs: [job({ failed: [PLAIN] })], workers: null, error: null });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ jobs: [], workers: null }), { status: 200, headers: { "Content-Type": "application/json" } })));
    const none = await app.registry.execute("import.blocks_choice", { job_id: 7, source: "p1" });
    expect(none.ok).toBe(false);
    expect(windowStore.get().windows.some((w) => w.kind === "import-objects")).toBe(false);
  });
});

describe("the wizard opened with the file shows the panel of the choice at once", () => {
  it("the composition of `inspect` holds the same refusal: `unalignedOf` gives the panel", async () => {
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    const blocks = { format: "csv", label: "CSV", rows: 10, rows_exact: true, parents: 0, sub: true, columns: [], fields: [], warnings: [], error: { code: UNALIGNED.code, message: UNALIGNED.message, hint: UNALIGNED.hint, diag: UNALIGNED.diag, choices: UNALIGNED.choices } } as unknown as BlocksInfo;
    const file = { path: UNALIGNED.path, file: UNALIGNED.file, ok: true, kind: "block_model", format: "csv", label: "CSV", wireframes: [], rasters: [], layers: [], sidecars: [], choices: {}, warnings: [], left_out: [], totals: { layers: 0, lines: 0, points: 0, vertices: 0, rasters: 0, wireframes: 0, triangles: 0 }, unread: 0, preliminary: false, encoding: null, crs: null, size: 1, blocks } as unknown as FileOk;
    const composition: Composition = { files: [file], rest: [], totals: { files: 1, failed: 0, layers: 0, lines: 0, points: 0, vertices: 0, rasters: 0, wireframes: 0, triangles: 0 }, raster_z: { applies: false, elevation: null, shift: 0 }, section: null };
    const bodies: Record<string, unknown>[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return new Response(JSON.stringify(composition), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    const wizard = new ObjectsWizard(startOpLog("Импорт файлов", "import", {}));
    await wizard.add([UNALIGNED.path]);
    expect(bodies[0]).toMatchObject({ files: [UNALIGNED.path] });
    expect(wizard.store.get().error).toBeNull();
    const shown = wizard.store.get().composition!.files[0] as FileOk;
    const un = unalignedOf(shown.blocks!.error);
    expect(un?.diag.causes).toEqual(["off_lattice"]);
    expect(un?.grid?.label).toBe("Задать сетку вручную");
    expect(un?.cancel.label).toBe("Не импортировать этот файл");
  });
});
