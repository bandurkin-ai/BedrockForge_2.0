// Swath plot: the filter of the library in a series of blocks (ТЗ 7.30, 7.35 Д-02; ADR 0023 решение 21; Э17 17.10б) on the
// REAL answers of the core (`core/tests/data/blocks/swath_filter_answers.json`, made by
// `core/tests/test_swath_filter_window_answers.py` through `POST /stats/swath`): the request = the contract (`filter_id`
// only when it is set), the refusals of a series (`filter_not_found`, `filter_field_missing`, `blocks_unsupported`) in
// Russian with the hint at the card of the series, `notes` and «отфильтровано» in the text of the series, the form
// «Фильтр» (the library, «без фильтра», a filter that is gone), keeping and restoring `filter_id` in the settings.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { readRow } from "../src/modules/blocks/filters/api";
import { filtersStore, resetFilters, setRows } from "../src/modules/blocks/filters/state";
import type { ChartRow } from "../src/modules/charts/api";
import { sourcesView, swathError, type SeriesAnswer, type SwathResult } from "../src/modules/swath/api";
import { blockSeries, defaultSwath, normalizeSwath, sampleSeries, seriesIssue, seriesNote, swathCsv, swathName, swathProblem, swathRequest, type BlockSeries, type SwathSeries } from "../src/modules/swath/model";
import { createSwathSaver } from "../src/modules/swath/saver";
import { SeriesForm } from "../src/modules/swath/SeriesForm";
import { CoreError } from "../src/shell/api";

interface Case {
  status: number;
  request: { settings: { series: Record<string, unknown>[] } };
  body: Record<string, unknown> & Partial<SwathResult>;
}
const A = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks/swath_filter_answers.json"), "utf-8")) as {
  ids: Record<string, number>;
  filters: { body: { filters: unknown[] } };
  sources: { body: unknown };
  ok: Record<string, Case>;
  refused: Record<string, Case>;
};
const spaces = (t: string) => t.replace(/\s/g, " ");
const sources = sourcesView(A.sources.body);
const LOOK = ["color", "line", "bars"];
const noLook = (o: Record<string, unknown>) => Object.fromEntries(Object.entries(o).filter(([k]) => !LOOK.includes(k)));

beforeEach(() => resetFilters());

describe("the request = the contract of the core", () => {
  it("the series of the real requests come out of the client as the core was asked (the look is not sent, filter_id only when set)", () => {
    for (const [name, c] of Object.entries(A.ok)) {
      const s = normalizeSwath(c.request.settings);
      const sent = (swathRequest(s) as { series: Record<string, unknown>[] }).series;
      const asked = c.request.settings.series.map((x) => {
        const o = noLook(x);
        if (o.filter_id === null) delete o.filter_id;
        return o;
      });
      expect(sent, name).toEqual(asked);
    }
    const filtered = (swathRequest(normalizeSwath(A.ok.filter.request.settings)) as { series: Record<string, unknown>[] }).series;
    expect(filtered[0]).not.toHaveProperty("filter_id");
    expect(filtered[1].filter_id).toBe(A.ids.rich);
  });

  it("a filter with a selection by categories sends both; without a filter nothing new", () => {
    const s = normalizeSwath(A.ok.filter_and_labels.request.settings);
    const x = s.series[0] as BlockSeries;
    expect(x.filter_id).toBe(A.ids.rich);
    expect(x.filter).toEqual({ field: "DOMAIN", labels: ["руда"] });
    expect((swathRequest(s) as { series: Record<string, unknown>[] }).series[0]).toMatchObject({ filter_id: A.ids.rich, filter: { field: "DOMAIN", labels: ["руда"] } });
    const plain = blockSeries([], 7, "AU", null);
    expect(plain.filter_id).toBeNull();
    expect(JSON.stringify(swathRequest(defaultSwath([plain])))).not.toContain("filter_id");
  });

  it("a number only: what is not a whole positive number is «без фильтра» (the core would answer 422 bad_param)", () => {
    for (const raw of ["x", true, 1.5, 0, -3, null, undefined, [4], {}]) {
      const s = normalizeSwath({ series: [{ kind: "blocks", model_id: 3, field: "AU", weight: "volume", filter_id: raw }] });
      expect((s.series[0] as BlockSeries).filter_id, String(raw)).toBeNull();
    }
    expect((normalizeSwath({ series: [{ kind: "blocks", model_id: 3, field: "AU", filter_id: 12 }] }).series[0] as BlockSeries).filter_id).toBe(12);
    // the real refusals of the core name the series and the param
    for (const [k, series] of [["bad_param", 0], ["bad_param_bool", 1]] as const) {
      const r = A.refused[k];
      expect(r.status).toBe(422);
      const e = new CoreError(422, String(r.body.code), String(r.body.message), String(r.body.hint));
      e.body = r.body;
      expect(swathError(e).param).toBe(`series[${series}].filter_id`);
      expect(swathError(e).text).toBe(`${r.body.message} ${r.body.hint}`);
      expect(String(r.body.message)).toContain(`series[${series}].filter_id`);
    }
  });
});

describe("the answers: the error of a series, notes, «отфильтровано»", () => {
  const series = (r: Case) => r.body.series as SeriesAnswer[];

  it("the error of a series is Russian with the hint, the other series are counted", () => {
    const gone = series(A.refused.filter_not_found)[1];
    expect(gone.error?.code).toBe("filter_not_found");
    expect(seriesIssue(gone)).toBe("Фильтр не найден. Обновите список фильтров.");
    expect(seriesIssue(series(A.refused.filter_not_found)[0])).toBeNull();
    expect(series(A.refused.filter_not_found)[0].mean.some((v) => v !== null)).toBe(true);
    const lost = series(A.refused.filter_field_missing)[1];
    expect(lost.error?.code).toBe("filter_field_missing");
    expect(seriesIssue(lost)).toMatch(/^В модели «Блочная модель Au» нет поля «ZZZ», которое нужно фильтру\. Выберите другую модель/);
    // nothing becomes 0: an empty series stays empty
    expect(lost.mean.every((v) => v === null)).toBe(true);
    expect(seriesNote(lost)).toBe(`${lost.name}: ${seriesIssue(lost)}`);
    const inside = series(A.refused.within_turned)[1];
    expect(inside.error?.code).toBe("blocks_unsupported");
    expect(seriesIssue(inside)).toMatch(/пробы внутри блоков ряда 1.* Выберите отбор «в кубоиде»\.$/);
    expect(seriesIssue({ error: { code: "source_deleted", message: "Источник удалён." } })).toBeNull(); // another error: not at the card
    expect(seriesIssue(undefined)).toBeNull();
  });

  it("«отфильтровано N» in the text of the series, 0 not said; a series with the error says the hint", () => {
    const [plain, filtered] = series(A.ok.filter);
    expect(spaces(seriesNote(plain))).toBe(`${plain.name}: блоков в расчёте 29 442 из 32 000, без значения 2 558.`);
    expect(spaces(seriesNote(filtered))).toBe(`${filtered.name}: блоков в расчёте 5 040 из 32 000, отфильтровано 26 960.`);
    const both = series(A.ok.filter_and_labels)[0];
    expect(spaces(seriesNote(both))).toContain("отфильтровано 27 231");
    expect(spaces(seriesNote(series(A.ok.filter_null)[0]))).not.toContain("отфильтровано");
    // the model with sub-blocks: the blocks that were cut count by their pieces in a swath, the filter by rows
    const turned = series(A.ok.turned)[1];
    expect(turned.counts.filtered_out).toBeGreaterThan(0);
    expect(seriesNote(turned)).toContain(`отфильтровано ${turned.counts.filtered_out}`);
  });

  it("notes of the core («Ряд N: …») are strings of the result as they are; the CSV and the title do not change", () => {
    const turned = A.ok.turned.body as unknown as SwathResult;
    expect(turned.notes).toHaveLength(2);
    expect(turned.notes[1]).toBe("Ряд 2: блоки на границе полос делятся между полосами пропорционально объёму.");
    expect(A.ok.filter.body.notes).toEqual([]);
    const csv = swathCsv(A.ok.filter.body as unknown as SwathResult, [0, 1]);
    expect(csv.header).toHaveLength(4 + 3 + 3 + 1);
    expect(csv.rows[0]).toHaveLength(csv.header.length);
    expect(csv.rows[0][4]).toBe((A.ok.filter.body.series as SeriesAnswer[])[0].mean[0]);
    // the answer with a filter has the keys the client reads
    for (const k of ["direction", "frame", "box", "limits", "start", "width", "count", "centres", "series", "notes"]) expect(A.ok.filter.body, k).toHaveProperty(k);
  });

  it("a filter is not a reason to refuse the calculation by the client; a series without a model still is «источник удалён» (the core says it)", () => {
    const s = defaultSwath([{ ...blockSeries([], 7, "AU", null), filter_id: 3 }]);
    expect(swathProblem(s)).toBeNull();
    expect(swathName(s)).toBe(swathName({ ...s, series: [{ ...s.series[0], filter_id: null } as SwathSeries] }));
  });
});

describe("the form of a series: the field «Фильтр»", () => {
  const rows = () => A.filters.body.filters.map(readRow);
  const form = (s: SwathSeries, answer?: SeriesAnswer) => renderToStaticMarkup(createElement(SeriesForm, { s, index: 1, all: [s], sources, answer, onChange: () => undefined }));
  const model: BlockSeries = { ...blockSeries([], A.ids.demo, "AU", null) };

  it("the library in the list by name, «без фильтра» chosen when none", () => {
    setRows(rows());
    const html = form(model);
    expect(html).toContain('aria-label="Фильтр блоков"');
    expect(html).toContain('<option value="" selected="">без фильтра</option>');
    for (const f of rows()) expect(html).toContain(`>${f.name.replace(">", "&gt;")}</option>`);
    expect(html.indexOf(">В каркасе<")).toBeLessThan(html.indexOf(">Выше рельефа<")); // by name
    expect(html).not.toContain("swath-series-issue");
  });

  it("the chosen filter is selected; a filter the library has lost stays as «не найден»; the list loading says nothing wrong", () => {
    setRows(rows());
    expect(form({ ...model, filter_id: A.ids.rich })).toContain(`<option value="${A.ids.rich}" title="AU &gt; 1" selected="">Руда &gt; 1</option>`);
    const lost = form({ ...model, filter_id: 987654 });
    expect(lost).toContain('<option value="987654" selected="">Фильтр № 987654 (не найден)</option>');
    expect(lost).toContain('aria-invalid="true"');
    resetFilters(); // not read yet: no «не найден»
    expect(form({ ...model, filter_id: 987654 })).toContain(">Фильтр № 987654</option>");
    expect(filtersStore.get().loaded).toBe(false);
  });

  it("the error of the series is at its card (role alert), with the hint", () => {
    setRows(rows());
    const answer = (A.refused.filter_field_missing.body.series as SeriesAnswer[])[1];
    const html = form({ ...model, filter_id: A.ids.needs }, answer);
    expect(html).toContain('data-testid="swath-series-issue-1"');
    expect(html).toContain("нет поля «ZZZ», которое нужно фильтру. Выберите другую модель или другой фильтр");
    expect(form(model, (A.ok.filter.body.series as SeriesAnswer[])[1])).not.toContain("swath-series-issue");
  });

  it("samples of a set have no filter field", () => {
    setRows(rows());
    expect(form(sampleSeries([], A.ids.set, null, "AU"))).not.toContain("Фильтр блоков");
  });
});

describe("keeping and restoring", () => {
  const withFilter = () => defaultSwath([{ ...blockSeries([], 7, "AU_CUT", "SG"), filter_id: 11 }, sampleSeries([], 3, "пробы", "AU")]);

  it("filter_id is in the stored settings and comes back from them (JSON round trip, a chart saved by an older build has none)", () => {
    const back = normalizeSwath(JSON.parse(JSON.stringify(withFilter())));
    expect((back.series[0] as BlockSeries).filter_id).toBe(11);
    expect(back).toEqual(withFilter());
    const old = JSON.parse(JSON.stringify(withFilter())) as { series: Record<string, unknown>[] };
    delete old.series[0].filter_id;
    expect((normalizeSwath(old).series[0] as BlockSeries).filter_id).toBeNull();
  });

  it("the choice of a filter is saved: one PUT with filter_id in the settings; the same filter saves nothing", async () => {
    const base = defaultSwath([blockSeries([], 7, "AU_CUT", "SG")]);
    const row: ChartRow = { id: 41, set_id: null, kind: "swath", name: "Swath", auto_name: false, settings: base as unknown as Record<string, unknown>, version: 1 };
    let timers: { fn: () => void; id: number }[] = [];
    const clock = { set: (fn: () => void) => (timers.push({ fn, id: timers.length + 1 }), timers.length), clear: (t: unknown) => void (timers = timers.filter((x) => x.id !== t)) };
    const fire = () => {
      const due = timers;
      timers = [];
      due.forEach((t) => t.fn());
    };
    const put = vi.fn(async (_id: number, body: { settings?: object; expected_version: number }) => ({ ...row, settings: body.settings as Record<string, unknown>, version: body.expected_version + 1 }));
    const saver = createSwathSaver(row, {}, put, 400, clock);
    saver.change(base);
    fire();
    expect(put).not.toHaveBeenCalled();
    saver.change({ ...base, series: [{ ...base.series[0], filter_id: 11 } as SwathSeries] });
    fire();
    await new Promise((r) => setTimeout(r, 0));
    expect(put).toHaveBeenCalledTimes(1);
    const sent = put.mock.calls[0][1].settings as { series: BlockSeries[] };
    expect(sent.series[0].filter_id).toBe(11);
    saver.stop();
  });
});
