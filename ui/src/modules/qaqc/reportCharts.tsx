/**
 * The charts of a QAQC report (ADR 0020 §7, §11): each entry of `GET /qaqc/reports/{id}/charts` is drawn by the
 * same component as in the tab — the control chart of a standard, the blanks of a laboratory, for each type of
 * duplicates the scatter, the ranked HARD and Thompson–Howard, the scatter of a group of the external control —
 * from `POST /qaqc/calc/{section}` with `report_id` (the snapshot of the report, not the current data). The chart is
 * mounted out of sight with the theme of the app, its canvas layer inlined, and written as PNG twice its size on
 * screen (`PNG_SCALE`, as «PNG» of the tab). A chart that has nothing to draw (the group is empty) gives null.
 */
import { createElement, type ReactElement, type Ref } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { BlankChart } from "../../charts/qaqc/BlankChart";
import { blankModel } from "../../charts/qaqc/blankModel";
import { ControlChart } from "../../charts/qaqc/ControlChart";
import { controlModel, orderTitle } from "../../charts/qaqc/controlModel";
import { PairScatter } from "../../charts/qaqc/PairScatter";
import { pairModel, rankedModel, thModel } from "../../charts/qaqc/pairModel";
import { RankedHardChart } from "../../charts/qaqc/RankedHardChart";
import { ThChart } from "../../charts/qaqc/ThChart";
import { PNG_SCALE } from "../../charts/core/ChartToolbar";
import { inlineRasters } from "../../charts/core/PointsCanvas";
import { pngBytes } from "../../charts/striplog/export";
import { calc, type BlanksAnswer, type CalcBody, type CalcSection, type DuplicatesAnswer, type ExternalAnswer, type ReportChart, type StandardsAnswer } from "./api";
import type { DupKind } from "./commands";
import { int, labTitle } from "./format";

/** The PNG of a report is twice the chart on screen (ТЗ 7.25; the criterion of 14.22). */
export const REPORT_PNG_SCALE = PNG_SCALE;

const SECTIONS: readonly CalcSection[] = ["standards", "blanks", "duplicates", "external"];

/** The body of the calculation a chart needs: by `report_id`, its element, group and type of duplicates. */
export function chartBody(chart: ReportChart, reportId: number): CalcBody {
  const body: CalcBody = { report_id: reportId, element: chart.element };
  if (chart.group) body.group = chart.group;
  if (chart.section === "duplicates" && chart.kind) body.kind = chart.kind as DupKind;
  return body;
}

/** One calculation per section, element, group and type: the three views of duplicates share it. */
export const chartCalcKey = (chart: ReportChart): string => JSON.stringify([chart.section, chart.element, chart.kind ?? null, chart.group ?? null]);

const unitOf = (a: { unit: string | null }) => (a.unit ? `, ${a.unit}` : "");
const same = (a: Record<string, string | null> | undefined, b: Record<string, unknown>) => !!a && Object.entries(a).every(([k, v]) => (v ?? null) === (b[k] ?? null));

/**
 * The element of a chart of the report from the answer of its calculation (the same components as the tab), or
 * null when there is nothing to draw. Pure: rendered to markup in the tests.
 */
export function chartElement(chart: ReportChart, answer: unknown, ref?: Ref<SVGSVGElement>): ReactElement | null {
  const label = chart.title;
  if (chart.section === "standards") {
    const a = answer as StandardsAnswer;
    const group = a.groups?.find((g) => same(chart.group, { crm: g.crm, lab: g.lab }));
    if (!a.series || !group) return null;
    const dated = controlModel(a.series, group, a.limits.sigma_warn, a.limits.sigma_fail, "date");
    const model = dated.n > 0 && dated.noDate === 0 ? dated : controlModel(a.series, group, a.limits.sigma_warn, a.limits.sigma_fail, "order");
    if (!model.n) return null;
    return createElement(ControlChart, { ref, model, series: a.series, label, yTitle: `${a.element}${unitOf(a)}`, batch: null, picked: null, orderTitle: orderTitle(a.thinned) });
  }
  if (chart.section === "blanks") {
    const a = answer as BlanksAnswer;
    const group = a.groups?.find((g) => same(chart.group, { lab: g.lab }));
    if (!a.series || !group) return null;
    const dated = blankModel(a.series, group, a.limits.k_blank, "date");
    const model = dated.n > 0 && dated.noDate === 0 ? dated : blankModel(a.series, group, a.limits.k_blank, "order");
    if (!model.n) return null;
    return createElement(BlankChart, { ref, model, series: a.series, label, yTitle: `${a.element}${unitOf(a)}`, batch: null, picked: null, orderTitle: orderTitle(a.thinned, "бланка") });
  }
  if (chart.section === "duplicates") {
    const a = answer as DuplicatesAnswer;
    if (!a.used) return null;
    const u = unitOf(a);
    if (chart.view === "ranked") {
      const model = rankedModel(a.ranked, a.hard_limit, a.share_limit, a.share_at_limit);
      return model.n ? createElement(RankedHardChart, { ref, model, label, note: `пар ${int(a.used)}` }) : null;
    }
    if (chart.view === "th") return a.th?.state === "ok" ? createElement(ThChart, { ref, model: thModel(a.th), th: a.th, label, unit: a.unit }) : null;
    const model = pairModel(a.points, a.hard_limit, a.stats?.rma);
    return model.n ? createElement(PairScatter, { ref, model, points: a.points, label, xTitle: `Исходная проба${u}`, yTitle: `Дубликат${u}`, limit: a.hard_limit, pearson: a.stats?.pearson ?? null, rma: a.stats?.rma ?? null, picked: null }) : null;
  }
  if (chart.section === "external") {
    const a = answer as ExternalAnswer;
    const group = a.groups?.find((g) => same(chart.group, { lab_main: g.lab_main, lab_check: g.lab_check }));
    if (!a.points || !group) return null;
    // the threshold of the answer, else that of the setup (as the tab: `ExternalTab.externalLimits`)
    const limit = (a as { hard_limit?: number | null }).hard_limit ?? a.limits.hard_ump;
    const model = pairModel(a.points, limit, group.rma);
    const u = unitOf(a);
    return model.n ? createElement(PairScatter, { ref, model, points: a.points, label, xTitle: `${labTitle(group.lab_main)}${u}`, yTitle: `${labTitle(group.lab_check)}${u}`, limit, pearson: group.pearson, rma: group.rma, picked: null }) : null;
  }
  return null;
}

const frame = () => new Promise<void>((resolve) => (typeof requestAnimationFrame === "function" ? requestAnimationFrame(() => resolve()) : setTimeout(resolve, 16)));

/** Waits until the canvas layers of the chart show their pictures (at most ~2 s). */
async function rastersReady(svg: SVGSVGElement): Promise<void> {
  for (let i = 0; i < 120; i++) {
    const layers = svg.querySelectorAll("[data-raster]").length;
    if (layers === 0 || svg.querySelectorAll("image[data-layer]").length >= layers) return;
    await frame();
  }
}

/** Mounts the element out of sight inside the app (its theme and fonts), and writes its PNG × 2. */
export async function elementPng(make: (ref: Ref<SVGSVGElement>) => ReactElement): Promise<Uint8Array> {
  const host = document.createElement("div");
  host.setAttribute("aria-hidden", "true");
  host.style.cssText = "position:fixed;left:-20000px;top:0;width:1200px;pointer-events:none;";
  document.body.appendChild(host);
  const root = createRoot(host);
  try {
    let svg: SVGSVGElement | null = null;
    flushSync(() => root.render(make((el) => (svg = el))));
    await frame();
    if (!svg) throw new Error("График отчёта не построен");
    await rastersReady(svg);
    await inlineRasters(svg);
    return await pngBytes(svg, REPORT_PNG_SCALE);
  } finally {
    root.unmount();
    host.remove();
  }
}

/**
 * The drawer of the charts of one report: a calculation is asked once for the views that share it and forgotten
 * when the last chart of `todo` that needs it is drawn (the answers of every group are not held to the end of a
 * report); null — the chart has nothing to draw (it is counted as left out).
 */
export function reportDrawer(reportId: number, todo: readonly ReportChart[] = [], toPng: typeof elementPng = elementPng): (chart: ReportChart) => Promise<Uint8Array | null> {
  const answers = new Map<string, Promise<unknown>>();
  const left = new Map<string, number>();
  for (const c of todo) left.set(chartCalcKey(c), (left.get(chartCalcKey(c)) ?? 0) + 1);
  const done = (key: string) => {
    const n = (left.get(key) ?? 1) - 1;
    if (n > 0) left.set(key, n);
    else {
      left.delete(key);
      answers.delete(key);
    }
  };
  return async (chart) => {
    if (!SECTIONS.includes(chart.section as CalcSection)) return null;
    const key = chartCalcKey(chart);
    let answer = answers.get(key);
    if (!answer) {
      answer = calc(chart.section as CalcSection, chartBody(chart, reportId));
      answers.set(key, answer);
      answer.catch(() => answers.delete(key));
    }
    try {
      const a = await answer;
      if (!chartElement(chart, a)) return null;
      return await toPng((ref) => chartElement(chart, a, ref)!);
    } finally {
      done(key);
    }
  };
}
