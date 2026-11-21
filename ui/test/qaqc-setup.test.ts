// «Настройка QAQC → Типы, CRM, DL, Пороги» (ADR 0020 §3, §9–10; ТЗ 8.4.2; Э14 14.19): the ranges of the thresholds
// (σ 1–5, серия 5–20, k_б 1–50, k_д 0–50, группа 5–51 нечётная), a row of the reference with an error shown, a 409
// → the stored values in the form, the save — `PUT` with `expected_version` of the form, unrecognised codes of TYPE
// marked, `qaqc.changed` of a newer version read again. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { configChanged, configStore, loadConfig, loadCrm, resetConfig, setBase } from "../src/modules/qaqc/setup/configStore";
import { CrmTab } from "../src/modules/qaqc/setup/CrmTab";
import { dataStore, resetData } from "../src/modules/qaqc/setup/dataStore";
import { DlTab } from "../src/modules/qaqc/setup/DlTab";
import { applyConfigArgs, applyCrm, applyCrmArgs, checkCrm, checkDl, checkLimits, codeRows, crmArgs, crmTexts, DEFAULT_LIMITS, DEFAULT_TYPES, limitsText, mergeRead, setCodeType, typeOfCode, typesArg, type LimitTexts } from "../src/modules/qaqc/setup/forms";
import { LimitsTab } from "../src/modules/qaqc/setup/LimitsTab";
import { TypesTab } from "../src/modules/qaqc/setup/TypesTab";
import type { CrmRow, QaqcConfig } from "../src/modules/qaqc/setupApi";
import { setCoreTarget } from "../src/shell/api";
import { AppContext } from "../src/shell/AppContext";
import type { App } from "../src/shell/app";
import { closeDialog, dialogStore } from "../src/shell/dialogs";
import { setupApp } from "./qaqcSetupHarness";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const limits = (patch: Partial<LimitTexts>): LimitTexts => ({ ...limitsText(DEFAULT_LIMITS), ...patch });
const errorsOf = (patch: Partial<LimitTexts>) => checkLimits(limits(patch)).errors;

const CONFIG: QaqcConfig = { version: 3, types: { ...DEFAULT_TYPES, Q: "DUP_P" }, dl: [{ element: "Au", lab: null, value: 0.01, unit: "г/т" }], limits: DEFAULT_LIMITS, empty_is_orig: true };
const CRM: CrmRow[] = [
  { crm_id: "OREAS-45", element: "Au", mean: 1.2, sd: 0.05, unit: "г/т", source: "паспорт" },
  { crm_id: "OREAS-45", element: "Cu", mean: 0.31, sd: 0.01, unit: "%", source: "паспорт" },
];

describe("thresholds", () => {
  it("the defaults are the values of the ТЗ and fit", () => {
    expect(DEFAULT_LIMITS).toEqual({ sigma_warn: 2, sigma_fail: 3, run: 9, k_blank: 5, k_dup: 5, th_group: 11, hard_p: 10, hard_c: 20, hard_f: 30, hard_ump: 10, hard_share: 90, n_boot: 1000, seed: 12345 });
    expect(checkLimits(limitsText(DEFAULT_LIMITS))).toEqual({ values: DEFAULT_LIMITS, errors: {} });
  });

  it("σ 1–5, warn < fail", () => {
    expect(errorsOf({ sigma_warn: "0,5" })).toEqual({ sigma_warn: "Вне диапазона: 1–5 σ" });
    expect(errorsOf({ sigma_fail: "5,5" })).toEqual({ sigma_fail: "Вне диапазона: 1–5 σ" });
    expect(errorsOf({ sigma_warn: "1", sigma_fail: "5" })).toEqual({});
    expect(errorsOf({ sigma_warn: "3", sigma_fail: "3" })).toEqual({ sigma_fail: "Порог отказа должен быть больше порога предупреждения" });
    expect(checkLimits(limits({ sigma_warn: "2,5" })).values?.sigma_warn).toBe(2.5);
  });

  it("серия 5–20 целое, k_б 1–50, k_д 0–50", () => {
    expect(errorsOf({ run: "4" }).run).toBe("Вне диапазона: 5–20 проб, целое");
    expect(errorsOf({ run: "21" }).run).toBe("Вне диапазона: 5–20 проб, целое");
    expect(errorsOf({ run: "9,5" }).run).toBe("Целое число: 5–20 проб, целое");
    expect(errorsOf({ run: "5" })).toEqual({});
    expect(errorsOf({ run: "20" })).toEqual({});
    expect(errorsOf({ k_blank: "0,9" }).k_blank).toBe("Вне диапазона: 1–50 × DL");
    expect(errorsOf({ k_blank: "50" })).toEqual({});
    expect(errorsOf({ k_dup: "0" })).toEqual({});
    expect(errorsOf({ k_dup: "-1" }).k_dup).toBe("Вне диапазона: 0–50 × DL");
    expect(errorsOf({ k_dup: "50,5" }).k_dup).toBe("Вне диапазона: 0–50 × DL");
    expect(errorsOf({ k_dup: "" }).k_dup).toBe("Введите число");
  });

  it("группа Томпсона–Ховарта 5–51 нечётная; HARD 1–100 %, доля 50–100 %", () => {
    for (const ok of ["5", "11", "51"]) expect(errorsOf({ th_group: ok })).toEqual({});
    expect(errorsOf({ th_group: "12" }).th_group).toBe("Нечётное число: 5–51 пар, нечётное");
    expect(errorsOf({ th_group: "3" }).th_group).toBe("Вне диапазона: 5–51 пар, нечётное");
    expect(errorsOf({ th_group: "53" }).th_group).toBe("Вне диапазона: 5–51 пар, нечётное");
    expect(errorsOf({ hard_p: "0" }).hard_p).toBe("Вне диапазона: 1–100 %");
    expect(errorsOf({ hard_share: "49" }).hard_share).toBe("Вне диапазона: 50–100 %");
    expect(errorsOf({ n_boot: "99" }).n_boot).toContain("Вне диапазона: 100–5000");
  });

  it("the arguments of `qaqc.config` are checked the same way", () => {
    expect(applyConfigArgs(CONFIG, { th_group: 12 })).toBe("Группа Томпсона–Ховарта: Нечётное число: 5–51 пар, нечётное");
    expect(applyConfigArgs(CONFIG, { sigma_warn: 3.5 })).toBe("Порог отказа должен быть больше порога предупреждения");
    const next = applyConfigArgs(CONFIG, { k_dup: 0, types: '{"q": null, "DUP-X": "DUP_F"}' });
    expect(typeof next).toBe("object");
    if (typeof next === "object") {
      expect(next.limits.k_dup).toBe(0);
      expect(next.types.Q).toBeUndefined();
      expect(next.types["DUP-X"]).toBe("DUP_F");
      expect(next.dl).toBe(CONFIG.dl);
    }
    expect(applyConfigArgs(CONFIG, { types: '{"X": "DUP"}' })).toContain("Тип «DUP» кода «X» неизвестен");
    expect(applyConfigArgs(CONFIG, { dl: '[{"element": "Au", "value": 0}]' })).toBe("DL, строка 1: DL — число больше 0");
  });
});

describe("types, CRM, DL", () => {
  it("a code of a file is compared without case and outer spaces; unrecognised is not ORIG", () => {
    expect(typeOfCode(" std ", DEFAULT_TYPES, true)).toBe("CRM");
    expect(typeOfCode("blk", DEFAULT_TYPES, true)).toBe("BLANK");
    expect(typeOfCode("dup_p", DEFAULT_TYPES, true)).toBe("DUP_P");
    expect(typeOfCode("XX", DEFAULT_TYPES, true)).toBeNull();
    expect(typeOfCode("", DEFAULT_TYPES, true)).toBe("ORIG");
    expect(typeOfCode(" ", DEFAULT_TYPES, false)).toBeNull();
    const data = [{ type_codes: [{ code: "STD", type: null, n: 300 }, { code: "XX", type: null, n: 7 }] }, { type_codes: [{ code: "std", type: null, n: 20 }, { code: "", type: null, n: 5 }] }];
    const rows = codeRows(data, { STD: "CRM", BLK: "BLANK" }, false);
    expect(rows.map((r) => [r.code, r.n, r.type])).toEqual([
      ["XX", 7, null],
      ["", 5, null],
      ["STD", 320, "CRM"],
      ["BLK", 0, "BLANK"],
    ]);
    const t = setCodeType({ STD: "CRM" }, " std", "BLANK");
    expect(t).toEqual({ std: "BLANK" });
    expect(typesArg({ STD: "CRM", Q: "DUP_P" }, { std: "BLANK" })).toEqual({ std: "BLANK", Q: null });
  });

  it("a row of CRM with μ ≤ 0, σ ≤ 0, no key or a repeated key is an error at its row", () => {
    const rows = crmTexts([...CRM, { crm_id: "oreas-45", element: "au", mean: 1, sd: 1 }, { crm_id: "B", element: "Au", mean: 0, sd: 1 }, { crm_id: "B", element: "Ag", mean: 2, sd: -1 }, { crm_id: "", element: "Au", mean: 1, sd: 1 }]);
    const c = checkCrm(rows);
    expect(c.rows).toBeNull();
    expect([...c.errors]).toEqual([
      [2, "Повтор: oreas-45 × au уже в строке 1"],
      [3, "μ — число больше 0"],
      [4, "σ — число больше 0"],
      [5, "Не задан CRM_ID"],
    ]);
    expect(checkCrm(crmTexts(CRM)).rows).toEqual(CRM);
    expect(applyCrmArgs(CRM, { rows: '[{"crm_id": "B", "element": "Au", "mean": 1, "sd": 0}]' })).toBe("CRM, строка 1: σ — число больше 0");
  });

  it("the reference: rows upserted by key, removed keys out, a file read over the form", () => {
    const b = { crm_id: "B", element: "Au", mean: 2, sd: 0.1, unit: "г/т", source: "" };
    expect(applyCrm(CRM, [{ ...CRM[0], mean: 1.3 }, b], [{ crm_id: "oreas-45", element: "cu" }])).toEqual([{ ...CRM[0], mean: 1.3 }, b]);
    expect(applyCrm(CRM, [], [{ crm_id: "OREAS-45" }])).toEqual([]);
    expect(crmArgs(CRM, [CRM[1]])).toEqual({ rows: [CRM[1]], remove: [{ crm_id: "OREAS-45", element: "Au" }] });
    const merged = mergeRead(crmTexts(CRM), [{ crm_id: "OREAS-45", element: "AU", mean: 1.25, sd: 0.04 }, b]);
    expect(merged.map((r) => [r.element, r.mean])).toEqual([
      ["AU", "1,25"],
      ["Cu", "0,31"],
      ["Au", "2"],
    ]);
  });

  it("DL: element required, value > 0, one row per element × laboratory", () => {
    const c = checkDl([
      { element: "Au", lab: "", value: "0,01", unit: "г/т" },
      { element: "au", lab: " ", value: "0,02", unit: "г/т" },
      { element: "Au", lab: "ALS", value: "0", unit: "" },
      { element: "", lab: "", value: "1", unit: "" },
    ]);
    expect([...c.errors]).toEqual([
      [1, "Повтор: au × любая уже в строке 1"],
      [2, "DL — число больше 0"],
      [3, "Не задан элемент"],
    ]);
    expect(checkDl([{ element: "Au", lab: "ALS", value: "0,005", unit: "г/т" }]).rows).toEqual([{ element: "Au", lab: "ALS", value: 0.005, unit: "г/т" }]);
  });
});

describe("saving", () => {
  let app: App;
  const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];
  let server: QaqcConfig;
  let crm: { version: number; rows: CrmRow[] };
  let conflictNext: boolean;
  let badRow: number | null;

  beforeEach(async () => {
    sent.length = 0;
    server = { ...CONFIG };
    crm = { version: 7, rows: CRM };
    conflictNext = false;
    badRow = null;
    resetConfig();
    resetData();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const path = new URL(url).pathname.replace("/api/v1", "");
        const method = init?.method ?? "GET";
        const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
        sent.push({ method, path, body });
        if (path === "/qaqc/config" && method === "GET") return json(server);
        if (path === "/qaqc/config" && method === "PUT") {
          if (conflictNext || body.expected_version !== server.version) {
            server = { ...server, version: server.version + 2, limits: { ...server.limits, sigma_warn: 2.2 } }; // someone else saved
            return json({ code: "version_conflict", message: "Настройку изменили.", hint: "Обновите." }, 409);
          }
          const { expected_version: _v, ...rest } = body;
          server = { ...(rest as Omit<QaqcConfig, "version">), version: server.version + 1 };
          return json(server);
        }
        if (path === "/qaqc/crm" && method === "GET") return json(crm);
        if (path === "/qaqc/crm" && method === "PUT") {
          if (badRow !== null) return json({ code: "bad_crm", message: `Строка ${badRow + 1}: σ должно быть больше 0.`, hint: "Исправьте строку.", row: badRow }, 422);
          crm = { version: crm.version + 1, rows: body.rows as CrmRow[] };
          return json(crm);
        }
        if (path === "/qaqc/data") return json({ data: [] });
        return json({ code: "not_found", message: "Нет", hint: "" }, 404);
      }),
    );
    setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
    app = setupApp();
    await loadConfig();
    await loadCrm();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setCoreTarget(null);
    for (const d of dialogStore.get().queue) closeDialog(d.id);
  });

  const draw = (el: (p: { onDirty: (on: boolean) => void }) => JSX.Element) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(el, { onDirty: () => undefined })));
  const puts = (path: string) => sent.filter((r) => r.method === "PUT" && r.path === path);

  it("the save of a form is a PUT with the version the form started from", async () => {
    setBase("config", 3);
    const r = await app.registry.execute("qaqc.config", { sigma_warn: 2.5, th_group: 13 }, "user");
    expect(r).toEqual({ ok: true, result: "Настройка QAQC сохранена (версия 4): sigma_warn = 2.5; th_group = 13; отчёты по прежней настройке устарели" });
    const [put] = puts("/qaqc/config");
    expect(put.body).toEqual({ types: CONFIG.types, dl: CONFIG.dl, limits: { ...DEFAULT_LIMITS, sigma_warn: 2.5, th_group: 13 }, empty_is_orig: true, expected_version: 3 });
    expect(configStore.get().config?.version).toBe(4);
    expect(configStore.get().base.config).toBe(4);
  });

  it("a value out of range never reaches the core", async () => {
    const r = await app.registry.execute("qaqc.config", { run: 25 }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Серия по одну сторону μ: Вне диапазона: 5–20 проб, целое");
    expect(puts("/qaqc/config")).toHaveLength(0);
    expect(configStore.get().configError?.param).toBe("run");
    expect(draw(LimitsTab)).toContain("Вне диапазона: 5–20 проб, целое");
  });

  it("409 → the stored values in the form, with the message", async () => {
    setBase("config", 3);
    conflictNext = true;
    const r = await app.registry.execute("qaqc.config", { sigma_warn: 2.5 }, "user");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Настройку QAQC изменили в другом окне");
    expect(configStore.get().config).toMatchObject({ version: 5, limits: { sigma_warn: 2.2 } });
    const html = draw(LimitsTab);
    expect(html).toContain('data-testid="qaqc-setup-conflict"');
    expect(html).toContain('data-testid="qaqc-limit-sigma_warn" value="2,2"');
  });

  it("the assistant saves over the newest version", async () => {
    server = { ...server, version: 6 };
    const r = await app.registry.execute("qaqc.config", { types: '{"KK": "CRM"}' }, "claude");
    expect(r.ok).toBe(true);
    expect(puts("/qaqc/config")[0].body).toMatchObject({ expected_version: 6, types: { ...CONFIG.types, KK: "CRM" } });
  });

  it("a row of CRM refused by the core is shown at its row", async () => {
    badRow = 1;
    const r = await app.registry.execute("qaqc.crm", { rows: JSON.stringify(CRM) }, "user");
    expect(r.ok).toBe(false);
    expect(configStore.get().crmError).toEqual({ key: "OREAS-45\u0000CU", message: "Строка 2: σ должно быть больше 0. Исправьте строку." });
    const html = draw(CrmTab);
    expect(html).toContain('data-testid="qaqc-crm-1" data-error="Строка 2: σ должно быть больше 0. Исправьте строку."');
    expect(html).toContain("Строк с ошибками: 1; первая — строка 2");
  });

  it("the reference is saved with its version; a removed key goes out", async () => {
    setBase("crm", 7);
    const r = await app.registry.execute("qaqc.crm", { rows: JSON.stringify([CRM[0]]), remove: '[{"crm_id": "OREAS-45", "element": "Cu"}]' }, "user");
    expect(r.result).toBe("Справочник CRM сохранён (версия 8): строк 1, стандартов 1, элементов 1; отчёты по прежнему справочнику устарели");
    expect(puts("/qaqc/crm")[0].body).toEqual({ rows: [CRM[0]], expected_version: 7 });
  });

  it("«Типы» marks unrecognised codes with their number of records; «DL» shows its rows", () => {
    dataStore.set({ list: [{ id: 1, name: "К", version: 1, rows: 330, elements: [{ name: "Au", unit: "г/т" }], type_codes: [{ code: "STD", type: "CRM", n: 300 }, { code: "XX", type: null, n: 30 }] }] });
    const html = draw(TypesTab);
    expect(html).toContain("Не распознано кодов: 1, записей: 30");
    expect(html).toMatch(/data-testid="qaqc-type-XX" data-unknown=""/);
    expect(html).not.toMatch(/data-testid="qaqc-type-STD" data-unknown/);
    expect(draw(DlTab)).toContain('value="0,01"');
  });

  it("`qaqc.changed` of a newer version is read again, of the known one — not", async () => {
    const before = sent.length;
    configChanged('{"what": "config", "version": 3}');
    expect(sent.length).toBe(before);
    server = { ...server, version: 9 };
    configChanged('{"what": "config", "version": 9}');
    await vi.waitFor(() => expect(configStore.get().config?.version).toBe(9));
    expect(sent.slice(before).map((r) => `${r.method} ${r.path}`)).toEqual(["GET /qaqc/config"]);
  });
});
