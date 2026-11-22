// Variography (ТЗ 8.6.4; ADR 0022 §13–14; Э16 16.21): the commands `variography.*` of the registry with their flags,
// a command without its required arguments opens the window «Вариография», with them starts the job under
// `/geostat/variography` with the body of docs/progress/e16-api.md (checked against core spec.py in 16.21); a value
// out of the ranges of 8.6.4 never reaches the core; `fit` and `save` act on the working model of the window; the
// replacement and the delete ask in our own window; the answers keep `null` (never 0). The core is a fetch stub.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defaultsQuery, readCalc, readDefaults } from "../src/modules/variography/api";
import { bindVariography, calcBody, lastWindow, VARIO_WINDOW } from "../src/modules/variography/bind";
import { commands, VARIO_BUTTONS } from "../src/modules/variography/commands";
import { checkValues, dataBody, DEFAULT_DATA, experimentalBody, mapBody, setWork, valuesFromDefaults, variographyStore, workOf } from "../src/modules/variography/state";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { windowStore } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const TABLES = {
  tables: [
    { table: "collar", kind: "collar", name: "Устья", rows: 2, rev: 1 },
    { table: "12", kind: "interval", name: "Композиты", rows: 1200, rev: 2 },
    { table: "14", kind: "interval", name: "Литология", rows: 40, rev: 1 },
  ],
};
// the example of `GET /variography/defaults` of the contract
const DEFAULTS = {
  set_id: 3,
  table: "12",
  table_name: "Композиты",
  field: "AU",
  unit: "г/т",
  counts: { rows: 1200, missing: 4, no_position: 0, outside_domain: 0, used: 1196, no_weight: 0 },
  sigma2: 1.84,
  sigma2_z: 1.84,
  median_length: 2.0,
  weights: "DECL_W",
  has_decl_w: true,
  extent: { min: [0, 0, -100], max: [500, 400, 0] },
  defaults: { lag: 10, nlag: 15, lag_tol: 5, az_tol: 22.5, dip_tol: 22.5, band_h: 30, band_v: 20, min_pairs: 30, downhole: true, dh_lag: 2, dh_nlag: 15, dh_lag_tol: 1, seed: 12345, fan: { n: 12, tol: 7.5, q: 1.0 }, map: { nu: 20, nv: 20, cell: 10, thickness: 20, min_pairs: 5 } },
  ranges: { lag: { lo: 0, hi: 100000, lo_open: true, integer: false, inf: false, unit: "м" } },
};
// the example of `POST /variography/experimental` of the contract
const EXPERIMENTAL = {
  set_id: 3, table: "12", field: "AU", domain: null, weights: null, transform: "none",
  axes: { ang1: 30, ang2: -10, ang3: 0 },
  lag: 10, nlag: 15, lag_tol: 5, az_tol: 22.5, dip_tol: 22.5, band_h: 30, band_v: 20,
  min_pairs: 30, downhole: true, dh_lag: 2, dh_nlag: 15, dh_lag_tol: 1, seed: 12345,
}; // prettier-ignore
const MAP = {
  set_id: 3, table: "12", field: "AU", domain: null, weights: null, transform: "none",
  plane: { ang1: 0, ang2: 0, ang3: 0 }, what: ["fan", "map"],
  lag: 10, nlag: 15, lag_tol: 5, dip_tol: 22.5, band_v: 20, min_pairs: 30, seed: 12345,
  fan: { n: 12, tol: 7.5, q: 1.0 },
  map: { nu: 20, nv: 20, cell: 10, thickness: 20, min_pairs: 5 },
}; // prettier-ignore
const FITTED = { type: "semivariogram", c0: 0.21, common_axes: true, structures: [{ type: "spherical", c: 1.5, ang1: 30, ang2: -10, ang3: 0, a1: 118, a2: 79, a3: 21 }] };

let app: App;
const sent: { method: string; path: string; query: string; body: Record<string, unknown> }[] = [];
let taken = 0;

beforeEach(() => {
  sent.length = 0;
  taken = 0;
  windowStore.set({ windows: [] });
  variographyStore.set({ works: {}, models: [], modelsLoaded: false });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ method, path, query: u.search, body });
      const V = "/geostat/variography";
      if (path === "/drillholes/sets/3/tables") return json(TABLES);
      if (path === "/jobs") return json({ jobs: [] });
      if (path === `${V}/defaults`) return json(DEFAULTS);
      if (path === `${V}/experimental`) return json({ source: "core", id: 61 });
      if (path === `${V}/map`) return json({ source: "core", id: 62 });
      if (path === `${V}/fit`) return json({ model: FITTED, residual: 0.0123, n: 45, starts: 8, warnings: [] });
      if (path === `${V}/models` && method === "POST") {
        if (taken-- > 0) return json({ code: "name_taken", message: "Имя занято.", hint: "", param: "name" }, 409);
        return json({ id: 7, name: body.name });
      }
      if (path === `${V}/models` && method === "GET") return json({ models: [{ id: 7, name: "AU · Композиты", set_id: 3, table: "12", field: "AU", transform: "none", type: "semivariogram", color: null, sill: 1.71, structures: 1, stale: false, stale_reason: null, source_lost: false, created_at: "", updated_at: "" }] });
      if (path === `${V}/models/7` && method === "PUT") return json({ id: 7, name: "AU · Композиты" });
      if (path === `${V}/models/7` && method === "DELETE") return json({ deleted: 7 });
      if (path === `${V}/models/7`) return json({ id: 7, name: "AU", color: null, meta: { v: 1, source: { set_id: 3, table: "12", field: "AU", domain: null, weights: null }, transform: "none", ...FITTED }, points: { key: "abc", directions: [], downhole: null, counts: DEFAULTS.counts }, stale: true, stale_reason: "изменена таблица «Композиты»", source_lost: false });
      if (path === `${V}/models/7/export`) return u.searchParams.get("path") ? json({ format: "gslib", path: u.searchParams.get("path"), bytes: 42 }) : json({ format: "gslib", filename: "AU.txt", text: "1 0.21\n1 1.5 30 -10 0\n118 79 21\n" });
      if (path === `${V}/models/import`) return json({ model: FITTED });
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  app = createApp({ persist: false });
  bindVariography(app);
  app.store.set({ projectName: "Проект", projectReadOnly: false, setName: "Набор" });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  for (const d of dialogStore.get().queue) closeDialog(d.id);
});

const geostat = () => sent.filter((r) => r.path.startsWith("/geostat"));

async function answer(ok: boolean): Promise<string> {
  await vi.waitFor(() => expect(dialogStore.get().queue.length).toBe(1));
  const d = dialogStore.get().queue[0];
  closeDialog(d.id);
  if (d.kind === "confirm") d.resolve(ok);
  return d.kind === "confirm" ? d.options.message : "";
}

describe("registry", () => {
  it("has the ten commands `variography.*` of ADR 0022 §13: confirm — the delete, uiOnly — the models", () => {
    expect(commands.map((c) => c.id)).toEqual(["variography.open", "variography.models", "variography.calc", "variography.fit", "variography.ellipsoid", "variography.show", "variography.save", "variography.delete", "variography.export", "variography.import"]);
    expect(commands.filter((c) => c.confirm).map((c) => c.id)).toEqual(["variography.delete"]);
    expect(commands.filter((c) => c.uiOnly).map((c) => c.id)).toEqual(["variography.models"]);
    for (const c of commands) {
      expect(app.registry.get(c.id), c.id).toBeDefined();
      expect(c.stage).toBeUndefined();
      expect(c.title).toMatch(/[А-Яа-яЁё]/);
      expect(c.description).toMatch(/[А-Яа-яЁё]/);
    }
    expect(commands.filter((c) => app.registry.get(c.id)?.run)).toHaveLength(10);
  });

  it("the buttons of «Геостатистика → Вариография» run registry commands", () => {
    expect(VARIO_BUTTONS.map((b) => b.label)).toEqual(["Вариограммы", "Вдоль скважин", "Веер", "Карта вариограмм", "Нормальные оценки", "Модели"]);
    for (const b of VARIO_BUTTONS) expect(app.registry.get(b.command)).toBeDefined();
  });

  it("is unavailable without a project; saving — in a read-only project", async () => {
    app.store.set({ projectName: null });
    expect((await app.registry.execute("variography.open", {})).ok).toBe(false);
    app.store.set({ projectName: "Проект", projectReadOnly: true });
    expect((await app.registry.execute("variography.calc", {})).ok).toBe(true);
    expect((await app.registry.execute("variography.save", {})).result).toMatch(/только для чтения/);
  });
});

describe("the bodies are the examples of the contract", () => {
  const data = { ...DEFAULT_DATA, setId: 3, table: "12", field: "AU" };
  const values = valuesFromDefaults(readDefaults(DEFAULTS).defaults);

  it("`POST /experimental` and `/map` from the defaults of `GET /defaults`", () => {
    expect(experimentalBody(data, values, { axes: { ang1: 30, ang2: -10, ang3: 0 } }).body).toStrictEqual(EXPERIMENTAL);
    expect(mapBody(data, values, { ang1: 0, ang2: 0, ang3: 0 }).body).toStrictEqual(MAP);
  });

  it("a blank value is left to the core; ∞ is \"inf\"; ang1 360 → 0, ang3 −180 → 180; the domain of the same table has no `table`", () => {
    const d = { ...data, domain: { table: "12", field: "ZONE", values: [" окисл ", "перех", "окисл"] }, weights: { kind: "none" as const }, transform: "nscore" as const };
    const b = experimentalBody(d, { band_h: "∞", lag: "6,9", downhole: "false" }, { axes: { ang1: 360, ang2: 0, ang3: -180 } }).body!;
    expect(b).toStrictEqual({ set_id: 3, table: "12", field: "AU", domain: { field: "ZONE", values: ["окисл", "перех"] }, weights: false, transform: "nscore", axes: { ang1: 0, ang2: 0, ang3: 180 }, lag: 6.9, band_h: "inf", downhole: false });
    expect(dataBody({ ...d, domain: { table: "14", field: "ZONE", values: ["окисл"] } }).body?.domain).toStrictEqual({ table: "14", field: "ZONE", values: ["окисл"] });
  });

  it("out of the ranges of 8.6.4 — no body, the reason in Russian; the tolerance is checked against L", () => {
    expect(checkValues({ nlag: "101" }).errors.nlag).toMatch(/Число лагов: значение вне диапазона от 1 до 100/);
    expect(checkValues({ az_tol: "0" }).errors.az_tol).toMatch(/больше 0/);
    expect(checkValues({ lag: "10", lag_tol: "11" }).errors.lag_tol).toMatch(/больше лага/);
    expect(checkValues({ "fan.n": "3" }).errors["fan.n"]).toMatch(/от 4 до 72/);
    expect(experimentalBody(data, { min_pairs: "2,5" }, { axes: { ang1: 0, ang2: 0, ang3: 0 } }).reason).toMatch(/целое/);
    expect(experimentalBody(data, {}, { axes: { ang1: 0, ang2: -91, ang3: 0 } }).reason).toMatch(/Погружение ang2/);
    expect(dataBody({ ...data, field: null }).reason).toBe("Выберите числовое поле");
  });

  it("`GET /defaults`: the query of the data; the answer read, σ² and the counts kept", () => {
    expect(defaultsQuery({ set_id: 3, table: "12", field: "AU", domain: { table: "14", field: "ZONE", values: ["окисл", "перех"] }, weights: false, transform: "nscore" })).toBe("set_id=3&table=12&field=AU&domain_table=14&domain_field=ZONE&domain_values=%D0%BE%D0%BA%D0%B8%D1%81%D0%BB%3B%D0%BF%D0%B5%D1%80%D0%B5%D1%85&weights=&transform=nscore");
    expect(defaultsQuery({ set_id: 3, table: "12", field: "AU", domain: null, weights: null, transform: "none" })).toBe("set_id=3&table=12&field=AU");
    const d = readDefaults(DEFAULTS);
    expect(d.counts.used).toBe(1196);
    expect(d.ranges.lag).toStrictEqual({ lo: 0, hi: 100000, lo_open: true, integer: false, inf: false, unit: "м" });
  });

  it("the form of the calculation keeps `null` — a lag without pairs is not zero", () => {
    const r = readCalc({ key: "k", directions: [{ axis: 1, az: 30, plunge: 10, band_h: "inf", lags: [10, 20], h: [9.7, null], n: [412, 0], gamma: [0.61, null], rho: [0.33, null], pairwise: [0.42, null], n_pw: [410, 0], few: [false, true] }], downhole: null, sampled: { n: 1000, of: 5000, seed: 12345 } });
    expect(r.directions[0].gamma).toStrictEqual([0.61, null]);
    expect(r.directions[0].h).toStrictEqual([9.7, null]);
    expect(r.directions[0].band_h).toBeNull();
    expect(r.downhole).toBeNull();
    expect(r.sampled).toStrictEqual({ n: 1000, of: 5000, seed: 12345 });
  });
});

describe("handlers", () => {
  it("`variography.calc` without set_id, table, field opens the window prefilled", async () => {
    const r = await app.registry.execute("variography.calc", { field: "AU" }, "claude");
    expect(r.ok).toBe(true);
    expect(r.result).toMatch(/set_id, table, field/);
    const w = windowStore.get().windows.find((x) => x.kind === VARIO_WINDOW)!;
    expect(w.title).toBe("Вариография · AU");
    expect(workOf(w.id)?.data.field).toBe("AU");
    expect(geostat()).toHaveLength(0);
  });

  it("with them — the job `experimental` with the table resolved by name; fan / map — `/map`", async () => {
    const r = await app.registry.execute("variography.calc", { set_id: 3, table: "Композиты", field: "AU", ang1: 30, ang2: -10, lag: 10 }, "claude");
    expect(r.ok, r.result).toBe(true);
    expect(r.result).toMatch(/задание №61/);
    const post = geostat().find((x) => x.method === "POST")!;
    expect(post.path).toBe("/geostat/variography/experimental");
    expect(post.body).toStrictEqual({ set_id: 3, table: "12", field: "AU", domain: null, weights: null, transform: "none", axes: { ang1: 30, ang2: -10, ang3: 0 }, lag: 10, downhole: true });
    expect((await calcBody({ set_id: 3, table: "12", field: "AU", what: "fan", domain: "ZONE", domain_table: "Литология", domain_values: "окисл; перех" })).body).toMatchObject({ what: ["fan"], domain: { table: "14", field: "ZONE", values: ["окисл", "перех"] }, plane: { ang1: 0, ang2: 0, ang3: 0 } });
  });

  it("a value out of range never reaches the core (nor the tables)", async () => {
    const r = await app.registry.execute("variography.calc", { set_id: 3, table: "Композиты", field: "AU", nlag: 500 }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toMatch(/Число лагов/);
    expect(sent).toHaveLength(0);
  });

  it("`fit` refuses before a calculation; then fits the working model of the last window", async () => {
    expect((await app.registry.execute("variography.fit", {}, "claude")).result).toMatch(/Нет открытого окна/);
    await app.registry.execute("variography.open", { set_id: 3, table: "12", field: "AU" });
    const win = lastWindow()!;
    expect((await app.registry.execute("variography.fit", {}, "claude")).result).toMatch(/Сначала рассчитайте/);
    setWork(win, { expKey: "abc", fix: ["structures.0.a3"], result: readCalc({ sigma2: 1.84, params: { lag: 10, nlag: 15 } }) });
    const r = await app.registry.execute("variography.fit", { structures: "spherical", fix_nugget: true }, "claude");
    expect(r.ok, r.result).toBe(true);
    const body = geostat().find((x) => x.path.endsWith("/fit"))!.body;
    expect(body).toMatchObject({ key: "abc", type: "semivariogram", weights: "n_h", fix: ["structures.0.a3", "c0"], fix_nugget: true });
    expect((body.model as { structures: unknown[] }).structures).toHaveLength(1);
    expect(workOf(win)?.model.structures[0].a1).toBe(118);
    expect(workOf(win)?.dirty).toBe(true);
    expect((await app.registry.execute("variography.fit", { structures: "spherical, cubic" }, "claude")).result).toMatch(/заданы неверно/);
  });

  it("`save`: new — POST with the default name (taken — the next one); replacement — asks, then PUT", async () => {
    await app.registry.execute("variography.open", { set_id: 3, table: "12", field: "AU" });
    const win = lastWindow()!;
    setWork(win, { expKey: "abc", defaults: readDefaults(DEFAULTS) });
    taken = 1;
    const r = await app.registry.execute("variography.save", {}, "claude");
    expect(r.result).toBe("Модель вариограммы «AU · Композиты (2)» сохранена (№7)");
    expect(geostat().filter((x) => x.path.endsWith("/models") && x.method === "POST").map((x) => x.body.name)).toEqual(["AU · Композиты", "AU · Композиты (2)"]);
    expect(geostat().find((x) => x.method === "POST")?.body).toMatchObject({ exp_key: "abc", model: { type: "semivariogram", c0: 0, structures: [], common_axes: true } });
    expect(workOf(win)).toMatchObject({ modelId: 7, dirty: false });
    expect(variographyStore.get().models.map((m) => m.id)).toEqual([7]);

    const again = app.registry.execute("variography.save", {}, "user");
    expect(await answer(false)).toMatch(/Заменить сохранённую модель вариограммы №7/);
    expect((await again).result).toBe("Отменено");
    const yes = app.registry.execute("variography.save", { model_id: 7 }, "claude");
    await answer(true);
    expect((await yes).ok).toBe(true);
    expect(geostat().some((x) => x.method === "PUT" && x.path.endsWith("/models/7"))).toBe(true);
  });

  it("`delete`: the user is asked in our own window; the windows of the model keep it as new", async () => {
    setWork("w1", { modelId: 7, expKey: "abc" });
    const run = app.registry.execute("variography.delete", { model_id: 7 }, "user");
    expect(await answer(true)).toMatch(/Удалить модель вариограммы №7/);
    expect((await run).result).toBe("Модель вариограммы №7 удалена");
    expect(workOf("w1")).toMatchObject({ modelId: null, dirty: true });
  });

  it("`open` a stored model: its model, points and «устарел»", async () => {
    const r = await app.registry.execute("variography.open", { model_id: 7 }, "claude");
    expect(r.result).toMatch(/устарела: изменена таблица «Композиты»/);
    const w = workOf(lastWindow()!)!;
    expect(w).toMatchObject({ modelId: 7, expKey: "abc", data: { setId: 3, table: "12", field: "AU" } });
    expect(w.model.structures[0]).toMatchObject({ type: "spherical", a1: 118 });
    expect((await app.registry.execute("variography.open", { model_id: 7 }, "claude")).result).toMatch(/уже открыто/);
  });

  it("`export` without path gives the text, with path writes the file; `import` opens a window with the model", async () => {
    expect((await app.registry.execute("variography.export", { model_id: 7, format: "gslib" }, "claude")).result).toMatch(/^AU\.txt:\n1 0\.21/);
    expect((await app.registry.execute("variography.export", { model_id: 7, path: "C:\\Модели\\AU.txt" }, "claude")).result).toBe("Модель №7 записана: C:\\Модели\\AU.txt (42 байт)");
    const r = await app.registry.execute("variography.import", { path: "C:\\Модели\\AU.json" }, "claude");
    expect(r.ok, r.result).toBe(true);
    expect(geostat().find((x) => x.path.endsWith("/import"))?.body).toStrictEqual({ format: "json", path: "C:\\Модели\\AU.json" });
    expect(workOf(lastWindow()!)).toMatchObject({ modelId: null, dirty: true, model: { c0: 0.21 } });
  });
});
