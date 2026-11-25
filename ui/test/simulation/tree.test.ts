// The section «Моделирование» of the data tree (Э18б, 18.35) on the real list of the core (`simulations`): the rows by name with the kind and the last
// run, the words of the state («устарел» with its reason, «источник удалён»), the tooltip, the menu of a row — the commands of the registry, with
// the reasons — and the section drawn; the lineage of the rows (`object:simulation`).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { kindTitle } from "../../src/modules/lineage/api";
import { lineageStore } from "../../src/modules/lineage/state";
import { lastRunText, rowMenu, rowTitleText, SimulationTree, stateText } from "../../src/modules/simulation/SimulationTreeSection";
import { loadRows, rowsByName, simulationStore } from "../../src/modules/simulation/state";
import { KIND_SHORT, readRow } from "../../src/modules/simulation/api";
import { installSimCore, LIST, newApp, S, SID, SIM_MODEL, teardown, type SimCore } from "./harness";

let app: App;
let core: SimCore;
beforeEach(async () => {
  core = installSimCore();
  app = newApp();
  await loadRows();
});
afterEach(() => {
  lineageStore.set({ statuses: {}, version: 0 });
  teardown();
});

const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, el));
const flat = (s: string): string => s.replace(/[  ]/g, " ");
const ROW = readRow(S.simulations.body.simulations[0]);

describe("the rows", () => {
  it("the reader keeps what the core listed; the list is by name", () => {
    expect(ROW).toMatchObject({ id: SID.sgs, name: S.simulation.body.name, kind: "sgs", model_id: SIM_MODEL.id, label: "окисл", field: "AU", n_real: 5, seed: 99, stale: false, stale_reason: null, source_lost: false, last_run: { nodes: 200, realizations: 5 } });
    expect(rowsByName(simulationStore.get().rows).map((r) => r.name)).toEqual(LIST.simulations.map((r) => r.name).sort((a, b) => a.localeCompare(b, "ru")));
    expect(readRow({ id: 1, kind: "что-то" }).kind).toBe("sgs");
    expect(readRow({ id: 1, last_run: null }).last_run).toBeNull();
  });
  it("the words of the state and of the last run", () => {
    expect(stateText(ROW)).toBe("");
    expect(stateText({ ...ROW, stale: true, stale_reason: "изменена таблица «Композиты»" })).toBe("устарел: изменена таблица «Композиты»");
    expect(stateText({ ...ROW, stale: true, stale_reason: null })).toBe("устарел");
    expect(stateText({ ...ROW, source_lost: true, stale: true })).toBe("источник удалён");
    expect(flat(lastRunText(ROW))).toBe("5 реализаций · 200 узлов");
    expect(lastRunText({ last_run: null })).toBe("не выполнялось");
    const n = (k: number) => flat(lastRunText({ last_run: { at: "", nodes: 1, realizations: k } })).split(" ").slice(0, 2).join(" ");
    expect([1, 2, 4, 5, 11, 12, 20, 21, 22, 100].map(n)).toEqual(["1 реализация", "2 реализации", "4 реализации", "5 реализаций", "11 реализаций", "12 реализаций", "20 реализаций", "21 реализация", "22 реализации", "100 реализаций"]);
  });
  it("the tooltip: the kind, the model and the domain, the field, the realizations and the seed, the last run, the way to open it", () => {
    const t = rowTitleText(ROW);
    expect(t).toContain(`«${ROW.name}»: SGS — последовательное гауссово моделирование`);
    expect(t).toContain(`Модель «${SIM_MODEL.name}», домен «окисл», поле AU; 5 реализаций, зерно 99`);
    expect(flat(t)).toContain("Последний запуск: 5 реализаций · 200 узлов");
    expect(t).toContain("Двойной щелчок — изменить в мастере «Моделирование»");
    expect(rowTitleText({ ...ROW, stale: true, stale_reason: "изменена модель вариограммы" })).toContain("Устарел: изменена модель вариограммы");
  });
  it("every kind has its short name and the lineage knows `object:simulation`", () => {
    expect(KIND_SHORT).toEqual({ sgs: "SGS", sis: "SIS", cosgs: "Ко-SGS" });
    expect(kindTitle("object:simulation")).toBe("моделирование");
  });
});

describe("the menu of a row runs the commands of the registry", () => {
  it("Изменить, Выполнить, Сводка, Постобработка, Удалить — each its command with the number of the row", () => {
    const ran: [string, Record<string, unknown>][] = [];
    const items = rowMenu(ROW, (id) => app.registry.reason(id), (id, args) => void ran.push([id, args]));
    expect(items.map((i) => i.label)).toEqual(["Изменить", "Выполнить", "Сводка реализаций…", "Постобработка…", "Удалить…"]);
    for (const i of items) i.run?.();
    expect(ran).toEqual([["simulate.open", { id: ROW.id }], ["simulate.run", { id: ROW.id }], ["simulate.summary", { id: ROW.id }], ["simulate.post", { id: ROW.id }], ["simulate.delete", { id: ROW.id }]]);
  });
  it("a row whose source is gone does not run; a row without realizations has nothing to post-process", () => {
    const reason = (id: string) => app.registry.reason(id);
    const lost = rowMenu({ ...ROW, source_lost: true }, reason, () => undefined);
    expect(lost[1].reason).toBe("Источник удалён: строку нельзя выполнить");
    const fresh = rowMenu({ ...ROW, last_run: null }, reason, () => undefined);
    expect(fresh[3].reason).toBe("Нет реализаций: сначала выполните моделирование");
    expect(rowMenu(ROW, reason, () => undefined).map((i) => i.reason ?? null)).toEqual([null, null, null, null, null]);
  });
  it("without a project the commands say why", () => {
    const items = rowMenu(ROW, (id) => app.registry.reason(id, { hasProject: false, hasSet: false, hasHole: false, hasObject: false, hasSection: false, editing: false, coreOnline: true }), () => undefined);
    expect(items.every((i) => i.reason === "Откройте или создайте проект")).toBe(true);
  });
});

describe("the section drawn", () => {
  it("a row for each simulation: its name, the kind badge, the aria label with the state, the double click opens the wizard (the command `simulate.open`)", () => {
    const html = flat(render(createElement(SimulationTree)));
    for (const r of LIST.simulations) expect(html).toContain(`data-testid="tree-sim-row-${r.id}"`);
    expect(html).toContain(`aria-label="${S.simulation.body.name}: SGS, 5 реализаций · 200 узлов"`);
    expect(html).toContain(">SGS<");
    expect(html).toContain(">Ко-SGS<");
    expect(html).toContain('role="group"');
  });
  it("the empty section says how to begin; before the first read it says nothing is there", async () => {
    core.list = { simulations: [] };
    await loadRows();
    expect(render(createElement(SimulationTree))).toContain("«+» открывает мастер «Моделирование»");
    simulationStore.set({ rows: [], loaded: false });
    expect(render(createElement(SimulationTree))).toContain("Нет объектов");
  });
  it("a stale row tells it in its aria label", async () => {
    core.list = { simulations: [{ ...S.simulations.body.simulations[0], stale: true, stale_reason: "изменена таблица «Композиты»" }] };
    await loadRows();
    expect(flat(render(createElement(SimulationTree)))).toContain("устарел: изменена таблица «Композиты»");
  });
});
