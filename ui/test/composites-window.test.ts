// The window «Композиты» (ТЗ 8.2.3; ADR 0018 §6, §10–11; Э12 12.26): the method as a tab, the preview on one hole
// follows an edit within 300 ms and drops an overtaken answer, samples and composites side by side on one scale,
// the summary «до/после», 409 `table_exists` → «Заменить?» in our own window. The core is a fetch stub.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPreviewer, preview, PREVIEW_DELAY_MS, startTool, toPreview, type PreviewAnswer } from "../src/modules/composites/api";
import { CompositePreview, CompositeWindow, metalNote } from "../src/modules/composites/CompositeWindow";
import { askReplace, SummaryTable } from "../src/modules/composites/ToolWindow";
import { composite, defaultName, metalChange, summaryRows } from "../src/modules/composites/tools/composite";
import { defaultValues } from "../src/modules/composites/params";
import { setCoreTarget } from "../src/shell/api";
import { closeDialog, dialogStore } from "../src/shell/dialogs";

const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

// Т8.2-1 on hole С-1
const ANSWER: PreviewAnswer = toPreview({
  hole: "С-1",
  samples: { from: [0, 1, 3, 4], to: [1, 3, 4, 6], values: { AU: [1, 2, null, 4] } },
  result: { from: [0, 2, 4], to: [2, 4, 6], values: { AU: [1.5, 2, 4], FLAG: [null, null, null] } },
  summary: {
    before: { count: 4, length: 6, fields: { AU: { mean: 2.6, metal: 13 } } },
    after: { count: 3, length: 6, fields: { AU: { mean: 2.5, metal: 15 } } },
  },
  set_summary: null,
  notes: ["Скважина С-1: остаток 0,4 м присоединён"],
});

const sent: { path: string; body: Record<string, unknown> }[] = [];
let pending: ((r: Response) => void)[] = [];
let answer409 = 0;

beforeEach(() => {
  sent.length = 0;
  pending = [];
  answer409 = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v1", "");
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      sent.push({ path, body });
      if (path.endsWith("/preview")) return new Promise<Response>((res) => pending.push(res));
      if (path === "/composites/run") {
        if (answer409-- > 0) return Promise.resolve(json({ code: "table_exists", message: "Таблица «Композиты 2 м» уже есть.", hint: "Замените её или дайте другое имя.", name: "Композиты 2 м" }, 409));
        return Promise.resolve(json({ source: "core", id: 7 }));
      }
      return Promise.resolve(json({ code: "not_found", message: "Нет", hint: "" }, 404));
    }),
  );
  setCoreTarget({ baseUrl: "http://127.0.0.1:1", token: "t" });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  setCoreTarget(null);
  for (const d of dialogStore.get().queue) closeDialog(d.id);
});

describe("preview", () => {
  it("goes to the core ≤ 300 ms after an edit and keeps only the newest answer", async () => {
    vi.useFakeTimers();
    expect(PREVIEW_DELAY_MS).toBeLessThanOrEqual(300);
    const applied: PreviewAnswer[] = [];
    const p = createPreviewer<PreviewAnswer>((a) => applied.push(a), () => undefined, preview);
    p.request("length", { set_id: 3, table: "5", length: 2 }, 11);
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS);
    expect(sent.map((r) => r.path)).toEqual(["/composites/length/preview"]);
    expect(sent[0].body).toEqual({ params: { set_id: 3, table: "5", length: 2 }, hole_id: 11 });
    // an edit while the first request is in flight
    p.request("length", { set_id: 3, table: "5", length: 4 }, 11);
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS);
    expect(sent).toHaveLength(2);
    pending[1](json({ ...ANSWER, hole: "новый" }));
    pending[0](json({ ...ANSWER, hole: "старый" })); // overtaken: comes later, dropped
    await vi.advanceTimersByTimeAsync(0);
    await vi.waitFor(() => expect(applied).toHaveLength(1));
    await vi.advanceTimersByTimeAsync(10);
    expect(applied.map((a) => a.hole)).toEqual(["новый"]);
    // the same request again is not sent; after stop nothing is applied
    p.request("length", { set_id: 3, table: "5", length: 4 }, 11);
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS);
    expect(sent).toHaveLength(2);
    p.request("bench", { set_id: 3, table: "5", bench: 10 }, 11);
    p.stop();
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS);
    expect(sent).toHaveLength(2);
  });

  it("an answer without some parts is still drawable", () => {
    const a = toPreview({ hole: "С-2" });
    expect(a.samples).toEqual({ from: [], to: [], values: {} });
    expect(a.summary).toBeNull();
    expect(renderToStaticMarkup(createElement(CompositePreview, { answer: a, field: "AU" }))).toContain("нет интервалов");
  });
});

describe("«Заменить?»", () => {
  it("409 table_exists asks in our own window; «Заменить» repeats with replace", async () => {
    answer409 = 1;
    const run = startTool("/run", { set_id: 3, table: "5", method: "length" }, askReplace);
    await vi.waitFor(() => expect(dialogStore.get().queue).toHaveLength(1));
    const d = dialogStore.get().queue[0];
    expect(d.kind).toBe("confirm");
    if (d.kind !== "confirm") return;
    expect(d.options.title).toBe("Заменить?");
    expect(d.options.message).toContain("«Композиты 2 м»");
    expect(d.options.danger).toBe(true);
    d.resolve(true);
    closeDialog(d.id);
    expect(await run).toEqual({ source: "core", id: 7 });
    expect(sent.map((r) => r.body.replace)).toEqual([undefined, true]);
  });

  it("«Отмена» starts nothing", async () => {
    answer409 = 1;
    const run = startTool("/run", { set_id: 3, table: "5", method: "length" }, askReplace);
    await vi.waitFor(() => expect(dialogStore.get().queue).toHaveLength(1));
    const d = dialogStore.get().queue[0];
    if (d.kind === "confirm") d.resolve(false);
    closeDialog(d.id);
    expect(await run).toBeNull();
    expect(sent).toHaveLength(1);
  });
});

describe("window", () => {
  it("the method is a tab; each tab shows its own parameters", () => {
    const html = renderToStaticMarkup(createElement(CompositeWindow, { winId: "w", args: { set_id: 3, method: "bench", bench: 5 } }));
    expect(html.match(/role="tab"/g)).toHaveLength(3);
    for (const t of ["По длине", "По доменам", "По уступам"]) expect(html).toContain(`>${t}</button>`);
    expect(html).toMatch(/aria-selected="true"[^>]*data-testid="composites-tab-bench"/);
    expect(html).toContain("Высота уступа H");
    expect(html).toContain('value="5"');
    expect(html).not.toContain("Длина L");
    expect(html).toContain('placeholder="Композиты по уступам 5 м"');
    expect(html).toMatch(/data-testid="composites-apply"[^>]*disabled/);
    const byLength = renderToStaticMarkup(createElement(CompositeWindow, { winId: "w", args: {} }));
    expect(byLength).toContain("Длина L");
    expect(byLength).toContain("Правило остатка");
    expect(byLength).not.toContain("Высота уступа H");
  });

  it("samples and composites side by side on one scale, the same colours", () => {
    const html = renderToStaticMarkup(createElement(CompositePreview, { answer: ANSWER, field: "AU" }));
    expect(html).toContain('data-testid="composites-samples"');
    expect(html).toContain('data-testid="composites-result"');
    expect(html.indexOf("composites-samples")).toBeLessThan(html.indexOf("composites-result"));
    expect(html).toContain("<title>0–2 м: 1,5</title>");
    expect(html.match(/data-missing="yes"/g)).toHaveLength(1); // 3–4 м of the samples, not zero
    // the same value has the same colour in both columns (2,0 of the samples and of the composites)
    const fills = [...html.matchAll(/<title>(\d–\d) м: 2<\/title>/g)].map((m) => m[1]);
    expect(fills).toEqual(["1–3", "2–4"]);
    const colour = (span: string) => new RegExp(`fill="(#[0-9a-f]{6})"[^>]*><title>${span} м: 2</title>`).exec(html)?.[1];
    expect(colour("1–3")).toBe(colour("2–4"));
  });

  it("the summary «до/после»: count, length, mean and metal by field", () => {
    expect(summaryRows(ANSWER.summary)).toEqual([
      { label: "Число интервалов", before: "4", after: "3" },
      { label: "Общая длина, м", before: "6", after: "6" },
      { label: "AU: среднее", before: "2,6", after: "2,5" },
      { label: "AU: металл Σ l·g", before: "13", after: "15" },
    ]);
    const html = renderToStaticMarkup(createElement(SummaryTable, { summary: ANSWER.summary, title: "Скважина С-1" }));
    expect(html).toContain("<caption>Скважина С-1</caption>");
    expect(html.match(/<tr>/g)).toHaveLength(5);
    expect(summaryRows(null)).toEqual([]);
    expect(summaryRows({ before: { count: 1, length: 2, fields: { AU: { mean: null, metal: null } } }, after: { count: 0, length: 0, fields: {} } })[2]).toEqual({ label: "AU: среднее", before: "—", after: "—" });
    expect(metalChange(ANSWER.summary, "AU")).toBeCloseTo(15.3846, 3);
    expect(metalNote(ANSWER, "AU")).toBe("Металл AU: +15,38 % к пробам скважины");
    expect(metalNote(ANSWER, null)).toBeNull();
  });

  it("the name of the new table follows the method and its size", () => {
    const v = defaultValues(composite.params);
    expect(defaultName(v)).toBe("Композиты 2 м");
    expect(defaultName({ ...v, length: "2,5" })).toBe("Композиты 2,5 м");
    expect(defaultName({ ...v, method: "domain" })).toBe("Композиты по доменам");
    expect(defaultName({ ...v, method: "bench" })).toBe("Композиты по уступам 10 м");
  });
});
