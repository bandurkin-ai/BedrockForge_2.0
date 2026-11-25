// Э18, 18.27в — the review of the inspector of a block: a probe cut to t (`hy_capped`) is taken like `selected` (it has a weight and a segment),
// a row whose system was not solved (STATUS 2, 3) says so and draws its «selected» probes in Slate, `sub` of the row is shown when it is not `parent`.
// The answers are the real ones of the core (`inspect_answers.json`); the `hy_capped` row is one of them with a probe renamed (the core gives
// `hy_capped` the same weight and the same segment as `selected`: `inspect.segments_of`).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppContext } from "../../src/shell/AppContext";
import type { App } from "../../src/shell/app";
import { windowStore } from "../../src/shell/windows";
import { readInspect, type InspectAnswer } from "../../src/modules/estimation/inspect/api";
import { buildSegments } from "../../src/modules/estimation/inspect/build";
import { InspectWindow } from "../../src/modules/estimation/inspect/InspectWindow";
import { inspectStore, INSPECT_WINDOW, resetInspect } from "../../src/modules/estimation/inspect/state";
import { CLICK_HINT, isSelected, solved, subText, takenSet, unsolvedNote, weightSums } from "../../src/modules/estimation/inspect/view";
import { CHECK_BUTTONS, commands } from "../../src/modules/estimation/commands";
import { bindInspect, inspectedModel } from "../../src/modules/estimation/inspect/bind";
import { estimationStore } from "../../src/modules/estimation/state";
import { blocksStore } from "../../src/modules/blocks/state";
import { installCore, newApp, teardown, type Core } from "./harness";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const F: any = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/estimate/inspect_answers.json"), "utf-8"));
const flat = (s: string): string => s.replace(/[  ]/g, " ");

let app: App;
let core: Core;
beforeEach(() => {
  core = installCore();
  app = newApp();
  resetInspect();
});
afterEach(() => {
  resetInspect();
  windowStore.set({ windows: [] });
  teardown();
});

const render = (a: InspectAnswer): string => {
  inspectStore.set({ answer: a, active: null });
  return flat(renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(InspectWindow, { winId: "w1" }))));
};

/** `pass_3` with its probe number `n` of the taken ones cut to t: the reason and the label change, the weight and the segment stay. */
function withCapped(n = 0): { a: InspectAnswer; i: number } {
  const raw = JSON.parse(JSON.stringify(F.answers.pass_3));
  const r = raw.results[0];
  const seg = r.segments[n];
  const c = r.candidates.find((x: { i: number }) => x.i === seg.i);
  c.reason = "hy_capped";
  c.reason_label = "урезана до 2,5";
  return { a: readInspect(raw), i: seg.i };
}

describe("a probe cut to t (hy_capped) is taken, as the core counts it", () => {
  const { a, i } = withCapped();
  const r = a.results[0];
  it("isSelected: a weight or a segment, not the code of the reason", () => {
    const c = r.candidates.find((x) => x.i === i)!;
    expect(c.reason).toBe("hy_capped");
    expect(isSelected(c)).toBe(true);
    expect(isSelected(c, takenSet(r))).toBe(true);
    const cut = r.candidates.find((x) => x.reason === "over_n_max")!;
    expect(isSelected(cut, takenSet(r))).toBe(false);
    // IK: no weights at all, the segment says that the probe is taken
    expect(isSelected({ i: 5, lambda: null }, new Set([5]))).toBe(true);
    expect(isSelected({ i: 5, lambda: null })).toBe(false);
  });
  it("3D: one segment and one point of the probe — no second Slate point and no thin line over it", () => {
    const b = buildSegments(r, a.block.centre);
    expect(b.taken.ids).toHaveLength(24);
    expect(b.taken.ids).toContain(i);
    expect(b.cut.ids).not.toContain(i);
    expect(b.points.ids.filter((x) => x === i)).toHaveLength(1);
    expect(b.points.ids.indexOf(i)).toBeLessThan(b.points.taken);
    expect(new Set(b.points.ids).size).toBe(b.points.ids.length);
    expect(b.points.ids).toHaveLength(r.candidates.length);
  });
  it("the sum of the weights holds the weight of the probe cut to t", () => {
    const all = r.segments.reduce((s, x) => s + (x.lambda ?? 0), 0);
    expect(weightSums(r.candidates).sum).toBeCloseTo(all, 12);
    expect(weightSums(r.candidates.filter((c) => c.reason === "selected")).sum).not.toBeCloseTo(all, 6);
    const html = render(a);
    expect(html).toMatch(/data-testid="inspect-row-\d+" data-reason="hy_capped" data-selected="true"/);
    expect(html).toContain('data-testid="inspect-sum"');
  });
});

describe("a row whose system was not solved (STATUS 2, 3)", () => {
  const few = readInspect(F.answers.few_samples);
  const r = few.results[0];
  it("its «selected» probes are Slate points, none is dropped, none has a segment", () => {
    expect(r.candidates).toHaveLength(4);
    const b = buildSegments(r, few.block.centre);
    expect(b.taken.ids).toHaveLength(0);
    expect(b.points.ids).toHaveLength(4);
    expect(b.points.taken).toBe(0);
    expect(b.cut.ids).toHaveLength(0);
    expect(b.points.colors.slice(0, 3)).toEqual(new Float32Array(b.points.colors.slice(0, 3))); // a colour, not a gap
    expect(Math.max(...b.reach.slice(3)) - Math.min(...b.reach.slice(0, 3))).toBeGreaterThan(0);
  });
  it("the note and the summary say that the system was not solved, not «0 из 4»", () => {
    expect(solved(r)).toBe(false);
    expect(unsolvedNote(r)).toBe("система не решалась (статус 2 — мало проб)");
    const html = render(few);
    expect(html).toMatch(/data-testid="inspect-system" data-solved="false"/);
    expect(html).toContain("система не решалась (статус 2 — мало проб)");
    expect(html).not.toContain("из 4");
    expect(html).toContain("выбрана, но система не решалась (статус 2 — мало проб)");
  });
  it("a solved row keeps «Проб в системе 9 из 121»", () => {
    const ok = readInspect(F.answers.pass_1);
    expect(unsolvedNote(ok.results[0])).toBeNull();
    const html = render(ok);
    expect(html).toMatch(/data-testid="inspect-system" data-solved="true"/);
    expect(html).toContain("из 121");
  });
});

describe("the sub-blocks of the row", () => {
  it("shown only if not `parent`", () => {
    expect(subText("parent")).toBeNull();
    expect(subText(null)).toBeNull();
    expect(subText("own")).toContain("каждый субблок оценивается сам");
    expect(render(readInspect(F.answers.pass_1))).not.toContain('data-testid="inspect-sub"');
    const raw = JSON.parse(JSON.stringify(F.answers.pass_1));
    raw.results[0].sub = "own";
    const html = render(readInspect(raw));
    expect(html).toContain('data-testid="inspect-sub"');
    expect(html).toContain("Субблоки: каждый субблок оценивается сам");
  });
  it("the reader keeps it", () => {
    expect(readInspect(F.answers.pass_1).results[0].sub).toBe("parent");
  });
});

describe("the button «Инспектор блока» (ТЗ 7.35.4) and the command without a model or a block", () => {
  const ROUTE = "POST /geostat/estimate/inspect";
  const asked = () => core.sent.filter((x) => `${x.method} ${x.path}` === ROUTE);
  const windows = () => windowStore.get().windows.filter((w) => w.kind === INSPECT_WINDOW);
  beforeEach(() => bindInspect(app));
  it("it is on the ribbon in «Проверка», and `model_id` is no longer required", () => {
    expect(CHECK_BUTTONS.find((x) => x.command === "estimate.inspect")).toMatchObject({ label: "Инспектор блока" });
    expect(commands.find((c) => c.id === "estimate.inspect")!.required).toBeUndefined();
  });
  it("without a block: the window with «Щёлкните блок в 3D», the core is not asked; again — the same window", async () => {
    expect(await app.registry.execute("estimate.inspect", {}, "user")).toEqual({ ok: true, result: CLICK_HINT });
    expect(windows()).toHaveLength(1);
    await app.registry.execute("estimate.inspect", {}, "user");
    expect(windows()).toHaveLength(1);
    expect(asked()).toHaveLength(0);
    expect(flat(renderToStaticMarkup(createElement(AppContext.Provider, { value: app }, createElement(InspectWindow, { winId: "w1" }))))).toContain("Щёлкните блок в 3D");
  });
  it("`model_id` is optional: the one chosen in the tree, else the only model with an estimation that was run, else a refusal in words", async () => {
    blocksStore.set((s) => ({ list: [...s.list, { ...s.list[0], id: 7 }] }));
    core.force(ROUTE, { status: 200, body: F.answers.pass_1 });
    const row = estimationStore.get().rows[0];
    estimationStore.set({ rows: [{ ...row, model_id: 7, last_run: { at: "2026-01-01", by_status: [1] } }] });
    expect(inspectedModel(app)).toBe(7);
    expect((await app.registry.execute("estimate.inspect", { block: "13; 7; 1" }, "user")).ok).toBe(true);
    expect(asked().at(-1)!.body).toMatchObject({ model_id: 7, block: [13, 7, 1] });
    app.store.set({ selected: { kind: "blocks", id: 5 } });
    expect(inspectedModel(app)).toBe(5); // the tree wins
    estimationStore.set({ rows: [] });
    app.store.set({ selected: null });
    expect(inspectedModel(app)).toBeNull();
    const r = await app.registry.execute("estimate.inspect", { block: "13; 7; 1" }, "claude");
    expect(r.ok).toBe(false);
    expect(r.result).toContain("Не задана блочная модель");
    expect(r.result).toContain("Выберите блочную модель в дереве");
  });
});
