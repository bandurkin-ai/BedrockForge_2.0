// Sample processing (ТЗ 8.2, Д-26; ADR 0018 §10–11; Э12 12.25): the 13 commands of the registry, the nine buttons of
// «Анализ → Обработка проб» found by the palette, a command without its required arguments opens its window, with
// them starts the job; a value out of the ranges of 8.2.2 never reaches the core; 409 `table_exists` — the assistant
// takes the free name; delete from the interface asks our own question. The core is a fetch stub.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { bindComposites, hasRequired, jobBody } from "../src/modules/composites/bind";
import { commands, PREP_BUTTONS, PREP_GROUP_TITLE } from "../src/modules/composites/commands";
import { createPreviewer, nextName, resolveTable } from "../src/modules/composites/api";
import { checkParam, checkParams, defaultValues, parseNumber, rangeText, sourceBody, toolBody, NO_SOURCE } from "../src/modules/composites/params";
import { composite } from "../src/modules/composites/tools/composite";
import { conditions } from "../src/modules/composites/tools/conditions";
import { TOOLS } from "../src/modules/composites/tools";
import { renderCompositesWindow } from "../src/modules/composites/ToolWindow";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { searchPalette } from "../src/shell/paletteSearch";
import { flattenRibbon, type RibbonTab } from "../src/shell/ribbonConfig";
import { windowStore } from "../src/shell/windows";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const TABLES = {
  set: { id: 3, name: "Набор", holes: 2 },
  tables: [
    { table: "collar", kind: "collar", name: "Устья", rows: 2, rev: 1 },
    { table: "5", kind: "assay", name: "Пробы", rows: 10, rev: 4 },
    { table: "6", kind: "interval", name: "Литология", rows: 4, rev: 1 },
  ],
};
const COLUMNS = {
  rows: [],
  columns: [
    { key: "hole", name: "Скважина", kind: "text", role: "fixed" },
    { key: "from", name: "От", kind: "number", role: "fixed" },
    { key: "to", name: "До", kind: "number", role: "fixed" },
    { key: "c1", name: "AU", kind: "number", unit: "г/т", role: "component" },
    { key: "c2", name: "CU", kind: "number", unit: "%", role: "component" },
    { key: "c3", name: "LITH", kind: "text", role: "value" },
  ],
};

let app: App;
const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];
let exists = 0; // how many times `POST /composites/run` answers 409
let lostOnce = 0; // how many times `POST /composites/results/9/rebuild` answers 409 `dependents_lost`
const LOST = [{ id: 11, op: "topcut", title: "Урезка", name: "AU 99 %" }, { kind: "column", name: "MY_NOTE" }, { id: 12 }, { name: "" }];

beforeEach(() => {
  sent.length = 0;
  exists = 0;
  lostOnce = 0;
  windowStore.set({ windows: [] });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      const path = u.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ method, path, body });
      if (path === "/drillholes/sets/3/tables") return json(TABLES);
      if (path.startsWith("/drillholes/sets/3/tables/")) return json(COLUMNS);
      if (path === "/jobs") return json({ jobs: [] });
      if (path === "/composites/run" && method === "POST") {
        if (exists-- > 0) return json({ code: "table_exists", message: "Таблица уже есть.", hint: "Замените или дайте другое имя.", name: "Композиты 2 м", suggested: "Композиты 2 м (2)" }, 409);
        return json({ source: "core", id: 41 });
      }
      if (path === "/composites/results/9/rebuild" && lostOnce-- > 0) return json({ code: "dependents_lost", message: "При перестроении пропадут результаты.", hint: "Подтвердите перестроение.", lost: LOST }, 409);
      if (path.startsWith("/composites/") && method === "POST") return json({ source: "core", id: 42 });
      if (path.startsWith("/composites/results/") && method === "DELETE") return json({ ok: true });
      return json({ code: "not_found", message: "Нет", hint: "" }, 404);
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
  app = createApp({ persist: false });
  bindComposites(app);
  app.store.set({ projectName: "Проект", projectReadOnly: false, setName: "Набор" }); // an active set (ADR 0018 decision 25)
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCoreTarget(null);
  for (const d of dialogStore.get().queue) closeDialog(d.id);
});

const posts = (prefix: string) => sent.filter((r) => r.method !== "GET" && r.path.startsWith(prefix));

describe("registry", () => {
  it("has the 13 commands of ADR 0018 §10 with their flags", () => {
    const ids = commands.map((c) => c.id);
    expect(ids).toEqual([
      "composite.run",
      "composite.code",
      "composite.merge",
      "composite.coords",
      "composite.topcut",
      "composite.equivalent",
      "composite.declus",
      "composite.ore_intervals",
      "composite.conditions_analysis",
      "composite.ore_intervals_edit",
      "composite.ore_protocol",
      "composite.rebuild",
      "composite.delete",
    ]);
    const confirm = commands.filter((c) => c.confirm).map((c) => c.id);
    expect(confirm).toEqual(["composite.topcut", "composite.equivalent", "composite.declus", "composite.rebuild", "composite.delete"]);
    expect(commands.filter((c) => c.uiOnly).map((c) => c.id)).toEqual(["composite.ore_protocol"]);
    for (const c of commands) {
      expect(app.registry.get(c.id)?.run, c.id).toBeTypeOf("function");
      expect(c.title).toMatch(/[А-Яа-яЁё]/);
      expect(c.description).toMatch(/[А-Яа-яЁё]/);
    }
  });

  it("every tool has a command and every button a bound command; the palette finds them", () => {
    expect(TOOLS.map((t) => t.command).sort()).toEqual(PREP_BUTTONS.map((b) => b.command).sort());
    expect(PREP_BUTTONS).toHaveLength(9);
    const tab: RibbonTab = { id: "analysis", title: "Анализ", groups: [{ title: PREP_GROUP_TITLE, items: PREP_BUTTONS.map((b) => ({ ...b })) }] };
    const items = flattenRibbon([tab]);
    for (const b of PREP_BUTTONS) {
      expect(app.registry.reason(b.command), b.command).toBeNull();
      const rows = searchPalette(b.label.toLowerCase(), items, []);
      expect(rows.some((r) => r.kind === "command" && r.item.command === b.command), b.label).toBe(true);
    }
    expect(searchPalette("обработка проб", items, [])).toHaveLength(9);
  });

  it("is unavailable without a project and in a read-only project", () => {
    app.store.set({ projectName: null });
    expect(app.registry.reason("composite.run")).toBe("Откройте или создайте проект");
    app.store.set({ projectName: "Проект", projectReadOnly: true });
    expect(app.registry.reason("composite.topcut")).toBe("Проект открыт только для чтения");
  });
});

describe("window or job", () => {
  it("without the required arguments opens the window with what is given", async () => {
    const r = await app.registry.execute("composite.run", { set_id: 3, length: 4 }, "claude");
    expect(r.ok).toBe(true);
    expect(r.result).toContain("Открыто окно «Композиты»");
    const w = windowStore.get().windows.find((x) => x.kind === "composites-composite");
    expect(w?.props).toEqual({ args: { set_id: 3, length: 4 } });
    expect(posts("/composites")).toHaveLength(0);
    expect(hasRequired(composite, { set_id: 3, table: "Пробы", method: "length" })).toBe(true);
    expect(hasRequired(composite, { set_id: 3, table: " ", method: "length" })).toBe(false);
  });

  it("with them starts the job: the table by name, every numeric field", async () => {
    const r = await app.registry.execute("composite.run", { set_id: 3, table: "Пробы", method: "length", length: 2 }, "claude");
    expect(r.ok, r.result).toBe(true);
    expect(r.result).toContain("задание №41");
    const [post] = posts("/composites");
    expect(post.path).toBe("/composites/run");
    expect(post.body).toMatchObject({ set_id: 3, table: "5", fields: ["AU", "CU"], method: "length", length: 2, remainder: "attach", f_min: 0.5, p_min: 0.5, origin: "domain" });
    expect(post.body).not.toHaveProperty("bench");
  });

  it("a value out of the range of 8.2.2 never reaches the core", async () => {
    for (const args of [{ length: 500 }, { length: 0.05 }, { p_min: 1.5 }, { method: "bench", bench: 60 }]) {
      const r = await app.registry.execute("composite.run", { set_id: 3, table: "Пробы", method: "length", ...args }, "claude");
      expect(r.ok, JSON.stringify(args)).toBe(false);
      expect(r.result).toMatch(/допустимо/);
    }
    const r = await app.registry.execute("composite.topcut", { set_id: 3, table: "Пробы", field: "AU", percentile: 80 }, "user");
    expect(r.result).toContain("«Перцентиль P»: допустимо 90–99,99 %");
    expect(sent).toHaveLength(0); // checked before any request
  });

  it("409 table_exists: the assistant takes the free name", async () => {
    exists = 1;
    const r = await app.registry.execute("composite.run", { set_id: 3, table: "Пробы", method: "length" }, "claude");
    expect(r.ok, r.result).toBe(true);
    const runs = posts("/composites/run");
    expect(runs).toHaveLength(2);
    expect(runs[1].body.name).toBe("Композиты 2 м (2)");
    expect(runs[1].body).not.toHaveProperty("replace");
  });

  it("an unknown table is refused with a hint", async () => {
    const r = await app.registry.execute("composite.coords", { set_id: 3, table: "Нет такой" }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Таблица «Нет такой» не найдена");
    expect(posts("/composites")).toHaveLength(0);
  });

  it("delete from the interface asks our own question (danger); «Отмена» deletes nothing", async () => {
    const run = app.registry.execute("composite.delete", { result_id: 9 }, "user");
    await vi.waitFor(() => expect(dialogStore.get().queue).toHaveLength(1));
    const d = dialogStore.get().queue[0];
    expect(d.kind).toBe("confirm");
    expect(d.kind === "confirm" && d.options.danger).toBe(true);
    if (d.kind === "confirm") d.resolve(false);
    closeDialog(d.id);
    expect((await run).result).toBe("Отменено");
    expect(sent.filter((r) => r.method === "DELETE")).toHaveLength(0);
  });

  it("rebuild: 409 dependents_lost lists the losses in our own window; yes — again with drop_lost, no — nothing", async () => {
    for (const yes of [true, false]) {
      sent.length = 0;
      lostOnce = 1;
      const run = app.registry.execute("composite.rebuild", { result_id: 9 }, "user");
      await vi.waitFor(() => expect(dialogStore.get().queue).toHaveLength(1));
      const d = dialogStore.get().queue[0];
      if (d.kind !== "confirm") throw new Error("not a confirm");
      expect(d.options).toMatchObject({ title: "Перестроить результат", okLabel: "Перестроить", danger: true });
      // an item without op/title (a column of the user, ч. 2 № 3) — by its name; without a name — by its id or dropped
      expect(d.options.message).toContain("• Урезка «AU 99 %»\n• колонка «MY_NOTE»\n• «результат №12»\n\n");
      d.resolve(yes);
      closeDialog(d.id);
      const r = await run;
      const p = posts("/composites/results/9/rebuild");
      if (yes) {
        expect(r.result).toBe("Результат №9: перестроение — задание №42");
        expect(p.map((x) => x.body)).toEqual([{}, { drop_lost: true }]);
      } else {
        expect(r.result).toBe("Отменено");
        expect(p.map((x) => x.body)).toEqual([{}]);
      }
    }
  });

  it("rebuild, protocol and the other jobs go to their routes", async () => {
    await app.registry.execute("composite.rebuild", { result_id: 9 }, "user");
    await app.registry.execute("composite.ore_protocol", { result_id: 9, format: "csv", holes: "С-1, С-2" }, "user");
    await app.registry.execute("composite.merge", { set_id: 3, tables: "Пробы, Литология" }, "user");
    await app.registry.execute("composite.ore_intervals", { set_id: 3, table: "Пробы", field: "AU", cutoff: 1, m_min: 2, m_waste_max: 3 }, "claude");
    const p = posts("/composites");
    expect(p.map((x) => x.path)).toEqual(["/composites/results/9/rebuild", "/composites/results/9/protocol", "/composites/merge", "/composites/ore"]);
    expect(p[1].body).toEqual({ format: "csv", holes: ["С-1", "С-2"] });
    expect(p[2].body).toEqual({ set_id: 3, tables: ["5", "6"] });
    expect(p[3].body).toMatchObject({ set_id: 3, table: "5", field: "AU", cutoff: 1, m_min: 2, m_waste_max: 3, mode: "strict_excl", missing: "break" });
    expect(p[3].body).not.toHaveProperty("missing_max");
  });
});

describe("parameters", () => {
  it("reads Russian numbers and checks the ranges of 8.2.2", () => {
    expect(parseNumber("0,5")).toBe(0.5);
    expect(parseNumber(" 2 ")).toBe(2);
    expect(parseNumber("2 м")).toBeNull();
    expect(rangeText({ min: 0.1, max: 100, unit: "м" })).toBe("0,1–100 м");
    const values = { ...defaultValues(composite.params), length: "150", p_min: "0,25" };
    const { body, errors } = checkParams(composite.params, values);
    expect(errors.length).toBe("«Длина L»: допустимо 0,1–100 м");
    expect(body).not.toHaveProperty("length");
    expect(body.p_min).toBe(0.25);
    const r = toolBody(composite, { ...NO_SOURCE, setId: 3, table: "5", fields: ["AU"] }, values);
    expect(r.body).toBeNull();
    expect(r.reason).toBe("«Длина L»: допустимо 0,1–100 м");
    expect(checkParam({ key: "n", label: "N", kind: "integer", min: 1 }, "1,5")).toEqual({ ok: false, error: "«N»: нужно целое число" });
  });

  it("asks for the source before anything else; by domains needs a domain", () => {
    expect(sourceBody(composite.source, NO_SOURCE)).toBe("Выберите набор скважин");
    expect(sourceBody(composite.source, { ...NO_SOURCE, setId: 3, table: "5" })).toBe("Отметьте поля");
    const v = { ...defaultValues(composite.params), method: "domain" };
    expect(toolBody(composite, { ...NO_SOURCE, setId: 3, table: "5", fields: ["AU"] }, v).reason).toMatch(/выберите домен/);
    const ok = toolBody(composite, { ...NO_SOURCE, setId: 3, table: "5", fields: ["AU"], domain: { kind: "field", table: "6", field: "LITH" } }, v);
    expect(ok.body).toMatchObject({ method: "domain", domain: { table: "6", field: "LITH" } });
  });

  it("conditions: at most 200 variants", async () => {
    const big = { ...defaultValues(conditions.params), cutoffs: "1; 2; 3; 4; 5; 6", m_min: "1; 2; 3; 4; 5; 6", m_waste_max: "1; 2; 3; 4; 5; 6" };
    expect(toolBody(conditions, { ...NO_SOURCE, setId: 3, table: "5", fields: ["AU"] }, big).reason).toBe("Вариантов 216: не больше 200");
    await expect(jobBody(conditions, { set_id: 3, table: "Пробы", field: "AU", cutoffs: "0,5; 1; x", m_min: "1", m_waste_max: "3" })).rejects.toThrow(/числа через точку с запятой/);
  });

  it("names and tables", () => {
    expect(nextName("Композиты 2 м")).toBe("Композиты 2 м (2)");
    expect(nextName("Композиты 2 м (2)")).toBe("Композиты 2 м (3)");
    expect(resolveTable(TABLES.tables, "пробы")).toBe("5");
    expect(resolveTable(TABLES.tables, "6")).toBe("6");
  });

  it("the preview sends the newest request after a pause and drops an overtaken answer", async () => {
    vi.useFakeTimers();
    try {
      const applied: string[] = [];
      const resolvers: ((v: string) => void)[] = [];
      const p = createPreviewer<string>((a) => applied.push(a), () => undefined, () => new Promise<string>((res) => resolvers.push(res)), 150);
      p.request("length", { length: 2 }, 1);
      p.request("length", { length: 3 }, 1); // within the pause: the first is never sent
      await vi.advanceTimersByTimeAsync(150);
      expect(resolvers).toHaveLength(1);
      p.request("length", { length: 4 }, 1);
      await vi.advanceTimersByTimeAsync(150);
      expect(resolvers).toHaveLength(2);
      resolvers[1]("L4");
      resolvers[0]("L3"); // late answer of an older request
      await vi.advanceTimersByTimeAsync(0);
      expect(applied).toEqual(["L4"]);
      p.stop();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("windows", () => {
  it("every tool has its window: source, parameters with ranges, «Применить» waits for the source", () => {
    for (const t of TOOLS) {
      const node = renderCompositesWindow({ id: "w1", kind: `composites-${t.id}`, title: t.title, x: 0, y: 0, w: 640, h: 560, z: 1, maximized: false, props: { args: { set_id: 3 } } });
      const html = renderToStaticMarkup(node as never);
      expect(html, t.id).toContain("Источник");
      expect(html, t.id).toContain("Применить");
      expect(html, t.id).toMatch(/data-testid="composites-apply"[^>]*disabled/);
    }
    const html = renderToStaticMarkup(renderCompositesWindow({ id: "w2", kind: "composites-topcut", title: "Урезка", x: 0, y: 0, w: 1, h: 1, z: 1, maximized: false, props: { args: { percentile: 99.5 } } }) as never);
    expect(html).toContain('value="99,5"');
    expect(html).toContain("диапазон 90–99,99 %");
    expect(renderCompositesWindow({ id: "w3", kind: "omf-export", title: "", x: 0, y: 0, w: 1, h: 1, z: 1, maximized: false })).toBeNull();
  });
});

describe("rules", () => {
  it("no window.confirm / prompt / alert / open in the module; texts in Russian", () => {
    const root = join(__dirname, "../src/modules/composites");
    const files: string[] = [];
    const walk = (d: string) => readdirSync(d).forEach((n) => (statSync(join(d, n)).isDirectory() ? walk(join(d, n)) : files.push(join(d, n))));
    walk(root);
    expect(files.length).toBeGreaterThan(10);
    for (const f of files) {
      const text = readFileSync(f, "utf8");
      expect(text, f).not.toMatch(/\b(window\.)?(confirm|prompt|alert)\s*\(/);
      expect(text, f).not.toMatch(/window\.open\s*\(/);
      expect(text.split("\n").length, f).toBeLessThanOrEqual(600);
    }
    for (const t of TOOLS) for (const p of t.params) expect(p.label, `${t.id}.${p.key}`).toMatch(/[А-Яа-яЁё]/);
  });
});
