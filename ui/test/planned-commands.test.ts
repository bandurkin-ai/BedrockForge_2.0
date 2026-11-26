// The commands `planned.*` of the registry (Э19б, 19.39; ТЗ 7.35 Д-34 ч. 2; e19-api.md §10, §12) on the REAL answers of the core: the declarations (the
// ribbon, Ctrl+K and the assistant read them), `planned.new` (the window; the whole hole writes), `planned.open`, `planned.save` (the version is
// checked), `planned.evaluate`, `planned.compare`, `planned.export` (the path from the dialog or the argument; a file that is there), `planned.delete`,
// `planned.show`, the reasons (no project, a project for viewing only, no holes), the list that follows the project and the events of the core.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "../src/shell/app";
import { commandTools } from "../src/shell/commands";
import { moduleCommands } from "../src/shell/catalog";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { windowStore } from "../src/shell/windows";
import { projectStore } from "../src/modules/projects/state";
import { commands, PLANNED_BUTTONS } from "../src/modules/drillholes/plannedCommands";
import { pickHole } from "../src/modules/drillholes/plannedBind";
import { loadPlanned, plannedCheck, plannedStore, plannedViewStore, plannedWin, PLANNED_WINDOW, rowById, startEdit } from "../src/modules/drillholes/plannedState";
import { AIM, FIRST, installCore, MODEL, newApp, R, SET, teardown, waitFor, type Core } from "./plannedHarness";

// the events of the core: the listeners of `onCoreEvent` are kept here so a test can send an event (the real ones come through SSE)
const heard = vi.hoisted(() => new Set<(event: string, data: string) => void>());
vi.mock("../src/modules/jobs/state", async (orig) => {
  const m = await orig<typeof import("../src/modules/jobs/state")>();
  return {
    ...m,
    onCoreEvent: (fn: (event: string, data: string) => void) => {
      heard.add(fn);
      return () => heard.delete(fn);
    },
  };
});
const emitCoreEvent = (event: string, data: string) => heard.forEach((fn) => fn(event, data));

let app: App;
let core: Core;
beforeEach(async () => {
  core = installCore();
  app = newApp();
  await waitFor(() => plannedStore.get().loaded);
});
afterEach(teardown);

const ids = commands.map((c) => c.id);
const requests = (route: string) => core.sent.filter((r) => `${r.method} ${r.path}` === route);
const flat = (s: string): string => s.replace(/[  ]/g, " ");
const ctx = { hasProject: true, hasSet: true, hasHole: false, hasObject: false, hasSection: false, editing: false, coreOnline: true };
const wins = () => windowStore.get().windows.filter((w) => w.kind === PLANNED_WINDOW);
const run = (id: string, args: Record<string, unknown> = {}, source: "user" | "claude" = "user") => app.registry.execute(id, args, source);
const win = () => plannedWin.get();
/** The question of a dialog (the assistant's confirmation of the registry, or the one of the handler): answered `ok`. */
async function answer(ok: boolean): Promise<string> {
  await waitFor(() => dialogStore.get().queue.length > 0);
  const d = dialogStore.get().queue[0];
  if (d.kind !== "confirm") throw new Error("a confirmation is expected");
  d.resolve(ok);
  closeDialog(d.id);
  return d.options.message;
}

describe("the declarations", () => {
  it("the commands of Д-34 ч. 2 are in the catalog, once, with Russian words", () => {
    for (const id of ["planned.new", "planned.open", "planned.evaluate", "planned.compare", "planned.export", "planned.delete"]) expect(ids).toContain(id);
    expect(new Set(ids).size).toBe(ids.length);
    const catalog = moduleCommands().map((c) => c.id);
    for (const id of ids) expect(catalog.filter((x) => x === id)).toHaveLength(1);
    for (const c of commands) {
      expect(c.title).toMatch(/[а-яё]/i);
      expect(c.description).toMatch(/[а-яё]/i);
      for (const p of Object.values(c.params ?? {})) expect(p.description).toMatch(/[а-яё]/i);
    }
  });

  it("the deletion asks the assistant's confirmation; the pick is for a person only; the tools of the assistant are valid", () => {
    expect(commands.find((c) => c.id === "planned.delete")!.confirm).toBe(true);
    expect(commands.find((c) => c.id === "planned.pick")!.uiOnly).toBe(true);
    const tools = commandTools(commands);
    expect(tools.map((t) => t.name)).not.toContain("ui_planned_pick");
    expect(tools.map((t) => t.name)).toContain("ui_planned_export");
    expect(commands.find((c) => c.id === "planned.export")!.required).toEqual(["format"]);
  });

  it("every button of the ribbon is a command of the module; the first is «Проектная скважина»", () => {
    for (const b of PLANNED_BUTTONS) expect(ids).toContain(b.command);
    expect(PLANNED_BUTTONS[0]).toMatchObject({ command: "planned.new", label: "Проектная скважина" });
  });
});

describe("the reasons", () => {
  it("no project, a project for viewing only, no holes", () => {
    expect(app.registry.reason("planned.new", { ...ctx, hasProject: false })).toBe("Откройте или создайте проект");
    app.store.set({ projectReadOnly: true });
    expect(app.registry.reason("planned.new", ctx)).toBe("Проект открыт только для чтения");
    expect(app.registry.reason("planned.save", ctx)).toBe("Проект открыт только для чтения");
    expect(app.registry.reason("planned.delete", ctx)).toBe("Проект открыт только для чтения");
    expect(app.registry.reason("planned.evaluate", ctx)).toBeNull(); // reads only: a project for viewing may ask
    expect(app.registry.reason("planned.export", ctx)).toBeNull();
    app.store.set({ projectReadOnly: false });
    plannedStore.set({ rows: [], loaded: true });
    for (const id of ["planned.open", "planned.evaluate", "planned.compare", "planned.export", "planned.delete"]) expect(app.registry.reason(id, ctx)).toContain("нет проектных скважин");
    expect(app.registry.reason("planned.new", ctx)).toBeNull();
  });
});

describe("the list", () => {
  it("is read with the paths when the project opens", () => {
    expect(requests("GET /planned_holes").some((r) => r.query.includes("trajectory=true"))).toBe(true);
    const rows = plannedStore.get().rows;
    expect(rows.map((r) => r.id)).toEqual([FIRST.id, AIM.id]);
    expect(rows[0].trajectory!.depth.length).toBe(FIRST.trajectory.depth.length);
  });

  it("follows the events of the core: a planned hole reads the list again, a model does not", async () => {
    const before = requests("GET /planned_holes").length;
    emitCoreEvent("implicit.changed", JSON.stringify({ kind: "implicit_model", id: 3 }));
    emitCoreEvent("implicit.changed", "{");
    await new Promise((r) => setTimeout(r, 30));
    expect(requests("GET /planned_holes")).toHaveLength(before);
    core.holes = [FIRST];
    emitCoreEvent("implicit.changed", JSON.stringify({ kind: "planned_hole", id: AIM.id, deleted: true }));
    await waitFor(() => plannedStore.get().rows.length === 1);
    expect(rowById(AIM.id)).toBeUndefined();
  });

  it("another project — a clean list and no window", async () => {
    await run("planned.open", { id: FIRST.id });
    expect(wins()).toHaveLength(1);
    plannedViewStore.set({ hidden: [AIM.id], chosen: FIRST.id });
    projectStore.set({ current: { uid: "p2", name: "Другой" } as never, version: 2 });
    await waitFor(() => plannedStore.get().loaded && plannedStore.get().rows.length === 2);
    expect(wins()).toHaveLength(0);
    expect(win().id).toBeNull();
    expect(plannedViewStore.get()).toEqual({ hidden: [], chosen: null });
    projectStore.set({ current: null, version: 0 });
    await waitFor(() => !plannedStore.get().loaded);
    expect(plannedStore.get().rows).toEqual([]);
  });

  it("a hole that is gone leaves the view", async () => {
    plannedViewStore.set({ hidden: [AIM.id], chosen: AIM.id });
    core.holes = [FIRST];
    await loadPlanned();
    expect(plannedViewStore.get()).toEqual({ hidden: [], chosen: null });
  });
});

describe("planned.new", () => {
  it("without arguments opens the window with the next free name; the arguments prefill it", async () => {
    const r = await run("planned.new");
    expect(r.ok).toBe(true);
    expect(wins()).toHaveLength(1);
    expect(win()).toMatchObject({ id: null, dirty: false });
    expect(win().form.name).toBe("ПС-2"); // «ПС-1» and «ПС-2 (через цель)» are taken
    expect(core.sent.filter((s) => s.method === "POST")).toHaveLength(0);
    await run("planned.new", { x: 10, y: 20, azimuth: 90 });
    expect(wins()).toHaveLength(1); // the same window
    expect(win().form).toMatchObject({ collar: ["10", "20", ""], azimuth: "90" });
    expect(win().dirty).toBe(true);
  });

  it("with the collar and the direction writes the hole; the list is read again; the hole is chosen", async () => {
    const r = await run("planned.new", { name: "ПС-9", x: 210, y: 210, z: 330, azimuth: 90, dip: -70, length: 100, change_azimuth: 1.5, change_dip: 0.5, set_id: SET });
    expect(r.ok).toBe(true);
    expect(flat(r.result)).toContain("Создана проектная скважина");
    expect(requests("POST /planned_holes")[0].body).toEqual({ name: "ПС-9", method: "minimum_curvature", set_id: SET, collar: [210, 210, 330], azimuth: 90, dip: -70, length: 100, target: null, change: { azimuth: 1.5, dip: 0.5 } });
    expect(wins()).toHaveLength(0); // no window: the command executed
    expect(plannedViewStore.get().chosen).not.toBeNull();
  });

  it("through a target: no azimuth and no dip are sent (the core solves them); the length is empty — to the target", async () => {
    const r = await run("planned.new", { x: 60, y: 60, z: 330, target_x: 110, target_y: 100, target_z: 270 });
    expect(r.ok).toBe(true);
    const sent = requests("POST /planned_holes")[0].body;
    expect(sent).toMatchObject({ collar: [60, 60, 330], target: [110, 100, 270], change: null, length: null });
    expect("azimuth" in sent || "dip" in sent).toBe(false);
  });

  it("a wrong number is a refusal in Russian before the request; a refusal of the core comes as it is", async () => {
    const bad = await run("planned.new", { x: 1, y: 2, z: 3, azimuth: 360, dip: -60, length: 100 });
    expect(bad.ok).toBe(false);
    expect(bad.result).toContain("Азимут");
    expect(requests("POST /planned_holes")).toHaveLength(0);
    core.force("POST /planned_holes", R.planned_target_far);
    const far = await run("planned.new", { x: 0, y: 0, z: 0, target_x: 300, target_y: 0, target_z: 0, length: 100 });
    expect(far.ok).toBe(false);
    expect(far.result).toContain("Длина 100 м не достаёт до цели");
    expect(far.result).toContain("Увеличьте длину");
    const both = await run("planned.new", { x: 1, y: 2, z: 3, target_x: 1, target_y: 2, target_z: 4, change_dip: 1 });
    expect(both.ok).toBe(false);
    expect(both.result).toContain("одно");
  });

  it("a project for viewing only refuses with the reason", async () => {
    app.store.set({ projectReadOnly: true });
    const r = await run("planned.new", { x: 1, y: 2, z: 3, azimuth: 0, dip: -90, length: 10 });
    expect(r).toEqual({ ok: false, result: "Проект открыт только для чтения" });
  });
});

describe("planned.open and planned.save", () => {
  it("opens the window of a hole with its form; without id — the chosen one, else the only one; a hole that is not there is a refusal", async () => {
    const r = await run("planned.open", { id: FIRST.id });
    expect(r.ok).toBe(true);
    expect(wins()).toHaveLength(1);
    expect(win()).toMatchObject({ id: FIRST.id, version: 1, dirty: false });
    expect(win().form.mode).toBe("change");
    expect(plannedViewStore.get().chosen).toBe(FIRST.id);
    expect(pickHole(undefined)).toBe(FIRST.id);
    const gone = await run("planned.open", { id: 777 });
    expect(gone.ok).toBe(false);
    expect(gone.result).toContain("не найдена");
    plannedViewStore.set({ chosen: null });
    await run("planned.new"); // the window is of no hole now
    expect(() => pickHole(undefined)).toThrow(/не выбрана/);
  });

  it("saves a change under the version: PUT with expected_version, the form takes the answer", async () => {
    await run("planned.open", { id: FIRST.id });
    const { setForm } = await import("../src/modules/drillholes/plannedState");
    setForm({ length: "120", name: "ПС-1 (длиннее)" });
    expect(win().dirty).toBe(true);
    const r = await run("planned.save");
    expect(r.ok).toBe(true);
    const put = requests(`PUT /planned_holes/${FIRST.id}`)[0];
    expect(put.body).toMatchObject({ expected_version: 1, length: 120, name: "ПС-1 (длиннее)", azimuth: 90, change: { azimuth: 1.5, dip: 0.5 } });
    expect(win()).toMatchObject({ version: 2, dirty: false, saving: false, issue: null });
    expect(win().saved).toContain("версия 2");
    expect(win().form.name).toBe("ПС-1 (длиннее)");
  });

  it("a version that is not the stored one is a conflict: «Перечитать», nothing is lost", async () => {
    await run("planned.open", { id: FIRST.id });
    plannedWin.set({ version: 5 }); // another window saved meanwhile
    const { setForm, rereadWin } = await import("../src/modules/drillholes/plannedState");
    setForm({ length: "130" });
    const r = await run("planned.save");
    expect(r.ok).toBe(false);
    expect(win().issue).toMatchObject({ code: "version_conflict", reread: true });
    expect(win().form.length).toBe("130");
    await rereadWin();
    expect(win()).toMatchObject({ issue: null, dirty: false, version: 1 });
    expect(win().form.length).toBe("100");
  });

  it("a new hole is written by POST; as_new writes the open one's form next to it; the words of a refusal stand under the field", async () => {
    const { setForm } = await import("../src/modules/drillholes/plannedState");
    await run("planned.new", { x: 10, y: 20, z: 30 });
    setForm({ azimuth: "360", dip: "-60", length: "50" });
    const bad = await run("planned.save");
    expect(bad.ok).toBe(false);
    expect(win().issue).toMatchObject({ param: "azimuth" }); // refused before the request: the words stand under the field
    expect(requests("POST /planned_holes")).toHaveLength(0);
    setForm({ azimuth: "45" });
    expect(win().issue).toBeNull(); // the words go with the change of the form
    const ok = await run("planned.save");
    expect(ok.ok).toBe(true);
    expect(requests("POST /planned_holes")).toHaveLength(1);
    expect(win().id).not.toBeNull();
    expect(win().saved).toContain("записана");
    await run("planned.save", { as_new: true });
    expect(requests("POST /planned_holes")).toHaveLength(2);
    expect((await run("planned.save")).ok).toBe(true);
    expect(requests("POST /planned_holes")).toHaveLength(2); // the open one is changed, not made again
    core.force("POST /planned_holes", R.planned_bad);
    await run("planned.save", { as_new: true });
    expect(win().issue).toMatchObject({ param: "azimuth", code: "bad_param" }); // the refusal of the core, under the same field
  });

  it("without a window there is nothing to save", async () => {
    const r = await run("planned.save");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("сохранять нечего");
  });

  it("a hole deleted elsewhere: the window says so and «Сохранить» writes it again", async () => {
    await run("planned.open", { id: FIRST.id });
    core.holes = [AIM];
    await loadPlanned();
    expect(win().gone).toBe(true);
    expect(win().issue?.code).toBe("planned_hole_not_found");
    const r = await run("planned.save");
    expect(r.ok).toBe(true);
    expect(requests("POST /planned_holes")).toHaveLength(1);
    expect(win().gone).toBe(false);
  });

  it("another hole does not replace a form with changes without asking", async () => {
    await run("planned.open", { id: FIRST.id });
    const { setForm } = await import("../src/modules/drillholes/plannedState");
    setForm({ length: "130" });
    const p = run("planned.open", { id: AIM.id });
    await waitFor(() => dialogStore.get().queue.length > 0);
    const d = dialogStore.get().queue[0];
    if (d.kind === "confirm") d.resolve(false);
    closeDialog(d.id);
    expect(await p).toEqual({ ok: true, result: "Отменено" });
    expect(win().id).toBe(FIRST.id);
    expect(win().form.length).toBe("130");
    expect(rowById(AIM.id)).toBeDefined();
    startEdit(rowById(AIM.id)!);
    expect(win().form.mode).toBe("target");
  });
});

describe("planned.evaluate", () => {
  it("without a source opens the tab; with the model and the set asks the stored hole: the intervals and the nearest holes", async () => {
    const open = await run("planned.evaluate", { id: FIRST.id });
    expect(open.ok).toBe(true);
    expect(open.result).toContain("Прогноз");
    expect(win().tab).toBe("forecast");
    expect(requests(`POST /planned_holes/${FIRST.id}/evaluate`)).toHaveLength(0);
    const r = await run("planned.evaluate", { id: FIRST.id, model_id: MODEL, set_id: SET });
    expect(r.ok).toBe(true);
    expect(requests(`POST /planned_holes/${FIRST.id}/evaluate`)[0].body).toEqual({ model_id: MODEL, set_id: SET });
    expect(flat(r.result)).toContain("SS — 42,93 м");
    expect(r.result).toContain("H057");
    const c = plannedCheck.get();
    expect(c.evaluate.phase).toBe("ok");
    expect(c.evaluate.answer!.intervals!.map((i) => i.code)).toEqual([null, "SS", null]);
    expect(c.source).toMatchObject({ kind: "model", modelId: MODEL });
  });

  it("closed wireframes by their numbers; only the nearest holes by the set", async () => {
    await run("planned.evaluate", { id: FIRST.id, mesh_ids: "50; 51" });
    expect(requests(`POST /planned_holes/${FIRST.id}/evaluate`)[0].body).toEqual({ mesh_ids: [50, 51], set_id: SET }); // the set of the hole: the nearest holes too
    await run("planned.evaluate", { id: AIM.id, set_id: SET });
    const last = requests(`POST /planned_holes/${AIM.id}/evaluate`)[0].body;
    expect(last).toEqual({ set_id: SET });
    expect(plannedCheck.get().evaluate.answer!.intervals).toBeNull();
    expect(plannedCheck.get().evaluate.answer!.nearest).toHaveLength(5);
  });

  it("a hole with unsaved changes is not asked: the core reads the stored one", async () => {
    await run("planned.evaluate", { id: FIRST.id });
    const { setForm } = await import("../src/modules/drillholes/plannedState");
    setForm({ dip: "-50" });
    const r = await run("planned.evaluate", { id: FIRST.id, model_id: MODEL });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("несохранённые");
    expect(requests(`POST /planned_holes/${FIRST.id}/evaluate`)).toHaveLength(0);
  });

  it("a refusal of the core stands in the answer of the command", async () => {
    core.force(`POST /planned_holes/${FIRST.id}/evaluate`, R.planned_evaluate_bad);
    const r = await run("planned.evaluate", { id: FIRST.id, model_id: MODEL });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Нечего проверять");
    expect(plannedCheck.get().evaluate.phase).toBe("error");
  });
});

describe("planned.compare", () => {
  it("the actual hole of the set against the planned one: the largest deviation, the rows", async () => {
    const r = await run("planned.compare", { id: FIRST.id, set_id: SET, hole: "H001" });
    expect(r.ok).toBe(true);
    expect(requests(`POST /planned_holes/${FIRST.id}/compare`)[0].body).toEqual({ set_id: SET, hole: "H001" });
    expect(flat(r.result)).toContain("наибольшее отклонение 281,41 м");
    expect(win().tab).toBe("compare");
    expect(plannedCheck.get().compare.answer!.rows).toHaveLength(121);
  });

  it("without a hole opens the tab and says what to choose; a hole that is not in the set is a refusal", async () => {
    const open = await run("planned.compare", { id: FIRST.id });
    expect(open.ok).toBe(true);
    expect(open.result).toContain("выберите набор и фактическую скважину");
    expect(requests(`POST /planned_holes/${FIRST.id}/compare`)).toHaveLength(0);
    const bad = await run("planned.compare", { id: FIRST.id, set_id: SET, hole: "нет такой" });
    expect(bad.ok).toBe(false);
    expect(bad.result).toContain("Скважина не найдена в наборе");
  });
});

describe("planned.export", () => {
  it("writes the files to the given path; the result says what and where", async () => {
    const r = await run("planned.export", { id: FIRST.id, format: "csv", path: "/tmp/out" });
    expect(r.ok).toBe(true);
    expect(requests(`POST /planned_holes/${FIRST.id}/export`)[0].body).toEqual({ format: "csv", path: "/tmp/out" });
    expect(r.result).toContain("collar.csv; survey.csv");
    const dxf = await run("planned.export", { id: FIRST.id, format: "dxf", path: "/tmp/out/ПС-1.dxf" });
    expect(dxf.ok).toBe(true);
    expect(dxf.result).toContain("DXF");
  });

  it("without a path and without the application there is no dialog: a refusal that names the argument", async () => {
    const r = await run("planned.export", { id: FIRST.id, format: "csv" });
    expect(r.ok).toBe(false);
    expect(r.result).toContain("path");
    expect(requests(`POST /planned_holes/${FIRST.id}/export`)).toHaveLength(0);
  });

  it("a file that is there: a person is asked, the assistant must say overwrite", async () => {
    const p = run("planned.export", { id: FIRST.id, format: "csv", path: "/tmp/занято" });
    await waitFor(() => dialogStore.get().queue.length > 0);
    const d = dialogStore.get().queue[0];
    expect(d.kind).toBe("confirm");
    if (d.kind === "confirm") d.resolve(true);
    closeDialog(d.id);
    expect((await p).ok).toBe(true);
    const sent = requests(`POST /planned_holes/${FIRST.id}/export`).map((s) => s.body.overwrite);
    expect(sent).toEqual([undefined, true]);
    const asst = await run("planned.export", { id: FIRST.id, format: "csv", path: "/tmp/занято" }, "claude");
    expect(asst.ok).toBe(false);
    expect(asst.result).toContain("overwrite=true");
    const yes = await run("planned.export", { id: FIRST.id, format: "csv", path: "/tmp/занято", overwrite: true }, "claude");
    expect(yes.ok).toBe(true);
  });

  it("works in a project for viewing only (it writes nothing in the project)", async () => {
    app.store.set({ projectReadOnly: true });
    expect((await run("planned.export", { id: FIRST.id, format: "csv", path: "/tmp/out" })).ok).toBe(true);
  });
});

describe("planned.delete and planned.show", () => {
  it("a person is asked; nothing is deleted on «Отмена»; then the hole goes and the list is read again", async () => {
    const p = run("planned.delete", { id: AIM.id });
    expect(await answer(false)).toContain("«ПС-2 (через цель)»");
    expect(await p).toEqual({ ok: true, result: "Отменено" });
    expect(requests(`DELETE /planned_holes/${AIM.id}`)).toHaveLength(0);
    const q = run("planned.delete", { id: AIM.id }, "claude"); // the question of the assistant's call is the registry's, not the handler's
    await answer(true);
    const done = await q;
    expect(done.ok).toBe(true);
    expect(done.result).toContain("удалена");
    expect(requests(`DELETE /planned_holes/${AIM.id}`)).toHaveLength(1);
    await waitFor(() => plannedStore.get().rows.length === 1);
  });

  it("a hole that is not in the list is a refusal", async () => {
    const q = run("planned.delete", { id: 555 }, "claude");
    await answer(true);
    const r = await q;
    expect(r.ok).toBe(false);
    expect(r.result).toContain("не найдена");
  });

  it("show / hide: one hole or all; the view keeps what is switched off", async () => {
    expect((await run("planned.show", { id: FIRST.id, visible: false })).result).toContain("скрыта");
    expect(plannedViewStore.get().hidden).toEqual([FIRST.id]);
    await run("planned.show", { visible: false });
    expect(plannedViewStore.get().hidden.sort()).toEqual([FIRST.id, AIM.id].sort());
    await run("planned.show", { id: AIM.id });
    expect(plannedViewStore.get().hidden).toEqual([FIRST.id]);
    await run("planned.show", {});
    expect(plannedViewStore.get().hidden).toEqual([]);
  });
});
