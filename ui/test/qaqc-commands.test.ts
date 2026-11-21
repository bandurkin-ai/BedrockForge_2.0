// The commands of QAQC (ADR 0020 §9): the 15 commands `qaqc.*` declared at once with their parameters and
// stubs, confirm on what changes the stored results, the tools of the assistant, the ribbon group «Анализ → QAQC»
// found by Ctrl+K, the reasons («Откройте проект», «Загрузите контрольные пробы…», the stage until bound),
// `qaqc.open` and `qaqc.filter` for the person and for the assistant. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QaqcData, StandardsAnswer } from "../src/modules/qaqc/api";
import { bindQaqc, NEED_DATA, NEED_PROJECT, qaqcReason } from "../src/modules/qaqc/bind";
import { commands, needsConfirm, QAQC_BUTTONS, QAQC_GROUP_TITLE, QAQC_SECTIONS } from "../src/modules/qaqc/commands";
import { QaqcPanel } from "../src/modules/qaqc/QaqcPanel";
import { calcKey, initialQaqc, qaqcStore, reportCounts } from "../src/modules/qaqc/state";
import { AppContext } from "../src/shell/AppContext";
import { setCoreTarget } from "../src/shell/api";
import { createApp, type App } from "../src/shell/app";
import { moduleCommands } from "../src/shell/catalog";
import { commandTools, EMPTY_CONTEXT, unavailableReason, validateArgs } from "../src/shell/commands";
import { searchPalette } from "../src/shell/paletteSearch";
import type { FlatItem } from "../src/shell/ribbonConfig";

const IDS = [
  "qaqc.setup", "qaqc.import", "qaqc.open", "qaqc.filter", "qaqc.config", "qaqc.crm", "qaqc.report",
  "qaqc.report_open", "qaqc.report_save", "qaqc.report_rebuild", "qaqc.report_rename", "qaqc.report_delete",
  "qaqc.data_rename", "qaqc.data_refresh", "qaqc.data_delete",
];
const CONFIRM = ["qaqc.config", "qaqc.crm", "qaqc.report_delete", "qaqc.data_refresh", "qaqc.data_delete"];

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const COUNTS = { rows: 500, filtered: 120, control: 60, unmapped: 2, missing: 1 };
const LIMITS = { sigma_warn: 2, sigma_fail: 3, run: 9, k_blank: 5, k_dup: 5, th_group: 11, hard_p: 10, hard_c: 20, hard_f: 30, hard_ump: 10, hard_share: 90, n_boot: 1000, seed: 12345 };
const DATA: QaqcData = {
  id: 4, name: "Контроль 2024", version: 2, rows: 500, elements: [{ name: "AU", unit: "г/т" }, { name: "AG", unit: "г/т" }], fields: [{ name: "x:PROJECT", kind: "text" }],
  labs: ["ALS", "SGS"], batches: ["B1"], date_from: "2024-01-01", date_to: "2024-06-30", type_codes: [{ code: "STD", type: "CRM", n: 60 }], source: { kind: "file", name: "qc.csv" },
  stale: false, sha256: "ab", counts: {},
};
const group = (crm: string, n: number, fail: number) => ({ crm, lab: "ALS", state: "ok", n, mu: 1, sd: 0.05, unit: "г/т", mean: 1.01, std: 0.05, bias: 1, bias_class: 0, norm: n - fail, warn: 0, fail, pair_fails: 0, run_flags: 0, swaps: 0 });
const STANDARDS: StandardsAnswer = { element: "AU", unit: "г/т", counts: COUNTS, limits: LIMITS, notes: [], groups: [group("OREAS 45", 20, 2), group("OREAS 47", 10, 1)] };

describe("the 15 commands of Э14 (ADR 0020 §9)", () => {
  it("are declared at once, each once in the catalog, stubs with the stage until bound", () => {
    expect(commands.map((c) => c.id)).toEqual(IDS);
    const all = moduleCommands().filter((c) => c.id.startsWith("qaqc."));
    expect(all.map((c) => c.id)).toEqual(IDS);
    for (const c of commands) {
      expect(c.stage, c.id).toBe("Э14");
      expect(unavailableReason(c, { ...EMPTY_CONTEXT, hasProject: true }), c.id).toBe("Функция появится на этапе Э14");
      expect(c.title, c.id).toMatch(/[А-Яа-яЁё]/);
      expect(c.description.length, c.id).toBeGreaterThan(40);
    }
  });

  it("confirm: the setup, the CRM, the replacement and refresh of data, deletions; a new import, report, filter, opening — without", () => {
    expect(commands.filter((c) => c.confirm).map((c) => c.id)).toEqual(CONFIRM);
    expect(needsConfirm("qaqc.import", { path: "a.csv" })).toBe(false);
    expect(needsConfirm("qaqc.import", { path: "a.csv", replace_id: 4 })).toBe(true);
    for (const id of ["qaqc.open", "qaqc.filter", "qaqc.report", "qaqc.report_save"]) expect(needsConfirm(id, {}), id).toBe(false);
    for (const id of CONFIRM) expect(needsConfirm(id, {}), id).toBe(true);
  });

  it("parameters: sections, types of duplicates, formats; required ids; the window «Настройка» only from the interface", () => {
    const by = new Map(commands.map((c) => [c.id, c]));
    const open = by.get("qaqc.open")!;
    expect(open.required).toEqual(["section"]);
    expect(open.params?.section.enum).toEqual([...QAQC_SECTIONS]);
    expect(open.params?.kind.enum).toEqual(["DUP_F", "DUP_C", "DUP_P"]);
    expect(validateArgs(open, { section: "standards", element: "AU" })).toBeNull();
    expect(validateArgs(open, { section: "pdf" })).toMatch(/допустимые значения/);
    expect(validateArgs(open, {})).toBe("Не задан параметр «section»");
    expect(Object.keys(by.get("qaqc.filter")!.params!)).toEqual(["labs", "batches", "date_from", "date_to", "crm", "types", "hole_prefix", "where", "reset"]);
    expect(Object.keys(by.get("qaqc.config")!.params!)).toEqual(["types", "dl", "sigma_warn", "sigma_fail", "run", "k_blank", "k_dup", "th_group", "hard_p", "hard_c", "hard_f", "hard_ump", "hard_share", "n_boot", "seed"]);
    expect(by.get("qaqc.setup")!.params?.tab.enum).toEqual(["data", "types", "crm", "dl", "limits"]);
    expect(by.get("qaqc.report")!.params?.charts.enum).toEqual(["all", "flagged", "none"]);
    expect(by.get("qaqc.report_save")!.params?.kind.enum).toEqual(["xlsx", "pdf"]);
    expect(by.get("qaqc.report_rename")!.required).toEqual(["report_id", "name"]);
    expect(by.get("qaqc.data_delete")!.required).toEqual(["data_id"]);
    expect(commands.filter((c) => c.uiOnly).map((c) => c.id)).toEqual(["qaqc.setup"]);
    const tools = commandTools(commands);
    expect(tools).toHaveLength(14);
    expect(tools.find((t) => t.name === "ui_qaqc_config")?.description).toMatch(/после подтверждения пользователем/);
  });

  it("the ribbon group: «Настройка» and the six sections; Ctrl+K finds each by its label", () => {
    expect(QAQC_BUTTONS.map((b) => [b.command, b.args?.section ?? null])).toEqual([["qaqc.setup", null], ...QAQC_SECTIONS.map((s) => ["qaqc.open", s])]);
    expect(QAQC_BUTTONS.map((b) => b.label)).toEqual(["Настройка", "Стандарты", "Бланки", "Дубликаты", "Внешний контроль", "Партии", "Отчёт"]);
    const items: FlatItem[] = QAQC_BUTTONS.map((b) => ({ ...b, icon: "chart" as const, group: `Анализ › ${QAQC_GROUP_TITLE}` }));
    for (const it of items) expect(searchPalette(it.label.toLowerCase(), items, []).some((r) => r.kind === "command" && r.item.label === it.label), it.label).toBe(true);
    expect(searchPalette("анализ qaqc", items, [])).toHaveLength(7);
    expect(searchPalette("томпсон", items, []).map((r) => (r.kind === "command" ? r.item.label : ""))).toEqual(["Дубликаты"]);
  });
});

describe("the reasons and the handlers of the tab", () => {
  let app: App;
  let calls: { url: string; body: unknown }[];
  let datas: QaqcData[];

  beforeEach(() => {
    calls = [];
    datas = [DATA];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        const url = String(input).replace(/^.*\/api\/v1/, "");
        const body = init?.body ? JSON.parse(String(init.body)) : undefined;
        calls.push({ url, body });
        if (url === "/qaqc/data") return json(datas);
        if (url === "/qaqc/calc/standards") return json(STANDARDS);
        if (url === "/qaqc/calc/summary") return json({ element: "AU", unit: "г/т", counts: COUNTS, limits: LIMITS, notes: [] });
        return json({ code: "not_found", message: "Нет", hint: "" }, 404);
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    qaqcStore.set(initialQaqc());
    app = createApp({ persist: false });
    bindQaqc(app);
  });

  afterEach(() => {
    setCoreTarget(null);
    vi.unstubAllGlobals();
    qaqcStore.set(initialQaqc());
  });

  it("no project — «Откройте проект»; no data — «Загрузите контрольные пробы…»; not read yet — available; others stay stubs", () => {
    expect(NEED_PROJECT).toBe("Откройте проект");
    expect(app.registry.reason("qaqc.open")).toBe("Откройте проект");
    app.store.set({ projectName: "Проект" });
    expect(app.registry.reason("qaqc.open")).toBeNull();
    qaqcStore.set({ datas: [] });
    expect(app.registry.reason("qaqc.open")).toBe(NEED_DATA);
    expect(app.registry.reason("qaqc.filter")).toBe("Загрузите контрольные пробы: «Анализ → QAQC → Настройка»");
    qaqcStore.set({ datas: [DATA] });
    expect(qaqcReason({ ...EMPTY_CONTEXT, hasProject: true })).toBeNull();
    expect(app.registry.reason("qaqc.report")).toBe("Функция появится на этапе Э14");
  });

  it("`qaqc.open` reads the data, opens the bottom tab on the section; a wrong data or element is refused with a hint", async () => {
    app.store.set({ projectName: "Проект" });
    const r = await app.registry.execute("qaqc.open", { section: "batches", element: "ag" });
    expect(r).toEqual({ ok: true, result: "Открыта вкладка «QAQC» · Партии · Контроль 2024 · AG" });
    expect(app.store.get().workspace).toMatchObject({ bottom: true, bottomTab: "qaqc" });
    expect(qaqcStore.get()).toMatchObject({ section: "batches", element: "ag" });
    expect((await app.registry.execute("qaqc.open", { section: "standards", data_id: 9 })).result).toBe("Данные QAQC № 9 не найдены. Данные проекта: Контроль 2024 (№ 4).");
    expect((await app.registry.execute("qaqc.open", { section: "standards", element: "CU" })).result).toBe("Элемента «CU» нет в данных «Контроль 2024». Элементы: AU, AG.");
    await app.registry.execute("qaqc.open", { section: "duplicates", kind: "DUP_P" });
    expect(qaqcStore.get().dupKind).toBe("DUP_P");
    expect(calls.filter((c) => c.url === "/qaqc/data")).toHaveLength(1);
  });

  it("`qaqc.open` for the assistant computes the section: records and the share of failures", async () => {
    app.store.set({ projectName: "Проект" });
    const r = await app.registry.execute("qaqc.open", { section: "standards" }, "claude");
    expect(calls.at(-1)).toEqual({ url: "/qaqc/calc/standards", body: { data_id: 4, element: "AU", filter: { v: 1 } } });
    expect(r.result).toBe(`Стандарты (AU, г/т · записей 120 из 500): групп 2, проб 30, отказов 3 (10,0 %)`);
  });

  it("`qaqc.filter`: the conditions replace their own; a refusal in Russian; the assistant gets «записей N из M»", async () => {
    app.store.set({ projectName: "Проект" });
    qaqcStore.set({ datas: [DATA] });
    expect((await app.registry.execute("qaqc.filter", { labs: "ALS" })).result).toBe("Фильтр QAQC: лаборатории: ALS");
    expect((await app.registry.execute("qaqc.filter", { date_from: "01.02.2024" })).result).toBe("Фильтр QAQC: лаборатории: ALS; даты: с 01.02.2024");
    expect((await app.registry.execute("qaqc.filter", { types: "STD" })).result).toMatch(/^Тип «STD» неизвестен/);
    const r = await app.registry.execute("qaqc.filter", { reset: true, crm: "OREAS 45" }, "claude");
    expect(r.result).toBe("Фильтр QAQC: CRM: OREAS 45 · записей 120 из 500");
    expect(calls.at(-1)).toEqual({ url: "/qaqc/calc/summary", body: { data_id: 4, element: "AU", filter: { v: 1, crm: ["OREAS 45"] } } });
    expect(app.store.get().workspace.bottomTab).toBe("qaqc");
  });

  it("no data in the project: `qaqc.open` says where to load them", async () => {
    app.store.set({ projectName: "Проект" });
    datas = [];
    const r = await app.registry.execute("qaqc.open", { section: "standards" });
    expect(r).toEqual({ ok: false, result: "В проекте нет данных контрольных проб. Загрузите их: «Анализ → QAQC → Настройка → Данные»." });
    expect(app.registry.reason("qaqc.open")).toBe(NEED_DATA);
  });

  it("the frame of the tab: data, element, filters with «записей N из M» on the left; the six subtabs on the right", () => {
    const render = () => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(QaqcPanel)));
    expect(render()).toContain("Откройте проект");
    app.store.set({ projectName: "Проект" });
    qaqcStore.set({ datas: [] });
    expect(render()).toContain("В проекте нет данных контрольных проб");
    qaqcStore.set({ datas: [DATA], section: "blanks" });
    reportCounts(calcKey()!, COUNTS);
    const html = render();
    for (const t of ["Стандарты", "Бланки", "Дубликаты", "Внешний контроль", "Партии", "Отчёт"]) expect(html).toContain(`>${t}</button>`);
    expect(html).toContain('aria-selected="true" class="_subtabOn');
    expect(html).toContain("Контроль 2024 (500)");
    expect(html).toContain("AU, г/т");
    expect(html).toContain("записей 120 из 500");
    expect(html).toContain("Сбросить");
    for (const t of ["Лаборатории", "Партии", "Даты", "Стандарты CRM", "Типы", "Скважины, начало имени", "Любое поле", "DUP_F"]) expect(html).toContain(t);
  });
});
