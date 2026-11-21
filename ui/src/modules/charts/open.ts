/**
 * The commands of a saved chart (ТЗ 7.28; ADR 0019 §9): `chart.open` — its window with the stored settings by the
 * current data (the assistant gets the text of `POST /stats/chart/{id}`: name, id, n, mean, median, CV),
 * `chart.rename` — a name by hand (it no longer follows the settings; a taken one — 409 `name_taken`),
 * `chart.delete` — with a confirmation (the data of the samples stay). The tree «Графики» runs the same commands.
 */
import type { App } from "../../shell/app";
import { CoreError } from "../../shell/api";
import { askConfirm } from "../../shell/dialogs";
import { needProject, needWritable, noted, refusal } from "../../shell/objectRun";
import { tablesStore } from "../tables/state";
import { computeSaved, deleteChart, getChart, updateChart, type ChartRow } from "./api";
import { CHART_WINDOWS, openChartWindow, savedName, type ChartWindowSpec } from "./bind";
import { dropChart, findChart, upsertChart } from "./state";
import { isChartKind, normalizeSettings, type ChartKind } from "./settings";

const notFound = (id: unknown) => refusal("chart_not_found", `График №${String(id)} не найден.`, "Номера графиков — в дереве набора › «Графики».");

/** The chart of the arguments: the store, else the core (404 — not found). */
export async function chartOf(args: Record<string, unknown>): Promise<ChartRow> {
  const id = args.chart_id;
  if (typeof id !== "number" || !Number.isInteger(id)) throw notFound(id);
  const known = findChart(id);
  if (known) return known;
  try {
    const row = await getChart(id);
    upsertChart(row);
    return row;
  } catch (e) {
    if (e instanceof CoreError && e.status === 404) throw notFound(id);
    throw e;
  }
}

/** A chart of a set of Э13 with a window (not a display along holes, not a swath plot). */
function setChart(row: ChartRow): { kind: ChartKind; spec: ChartWindowSpec; setId: number } {
  const spec = isChartKind(row.kind) ? (CHART_WINDOWS[row.kind] as ChartWindowSpec | undefined) : undefined;
  if (!isChartKind(row.kind) || row.set_id === null || !spec) throw refusal("bad_chart", `«${row.name}» — не график набора, который открывает это окно.`, row.kind === "striplog" ? "Отображение по стволу открывается из «Стволов»." : "Swath plot открывается командой stats.swath.");
  return { kind: row.kind, spec, setId: row.set_id };
}

export async function openSavedChart(args: Record<string, unknown>, source: "user" | "claude"): Promise<string> {
  const row = await chartOf(args);
  const { kind, spec, setId } = setChart(row);
  const setName = tablesStore.get().sets.find((s) => s.id === setId)?.name ?? `Набор ${setId}`;
  const settings = normalizeSettings(kind, row.settings, setId);
  openChartWindow(kind, settings, setName, { chart: row });
  if (source !== "claude") return `Открыт график «${row.name}»`;
  const answer = await computeSaved(row.id);
  const name = savedName(answer.chart.name, { ...row, ...answer.chart });
  return spec.text ? spec.text(answer, name, settings as never) : `Открыт график ${name}`;
}

/** The name given by hand; a version changed meanwhile (a save of the window) is taken and tried once more. */
export async function renameChart(args: Record<string, unknown>): Promise<string> {
  const name = typeof args.name === "string" ? args.name.trim() : "";
  if (!name) throw refusal("bad_param", "Не задано новое имя графика.", "Укажите name.");
  let row = await chartOf(args);
  for (let attempt = 0; ; attempt++) {
    try {
      const out = await updateChart(row.id, { name, expected_version: row.version });
      upsertChart(out);
      return `График переименован: «${out.name}»`;
    } catch (e) {
      const current = e instanceof CoreError && e.code === "version_conflict" ? (e.body?.chart as ChartRow | undefined) : undefined;
      if (!current || attempt) throw e;
      row = current;
    }
  }
}

export async function removeChart(args: Record<string, unknown>, source: "user" | "claude"): Promise<string> {
  const row = await chartOf(args);
  if (source === "user" && !(await askConfirm({ title: "Удалить график", message: `График «${row.name}» удаляется из набора. Данные проб не меняются.`, okLabel: "Удалить", danger: true }))) return "Отменено";
  await deleteChart(row.id);
  dropChart(row.id);
  return `График «${row.name}» удалён`;
}

export function bindSavedCharts(app: App): void {
  app.registry.bind("chart.open", noted(app, (a, source) => openSavedChart(a, source)), needProject);
  app.registry.bind("chart.rename", noted(app, (a) => renameChart(a)), needWritable(app));
  app.registry.bind("chart.delete", noted(app, (a, source) => removeChart(a, source)), needWritable(app));
}
