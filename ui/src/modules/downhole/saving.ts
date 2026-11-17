/**
 * «Сохранить» of the display form and «Применить к…» of a style (ТЗ 7.16, ADR 0007 §3): a saved display is
 * an object «chart» under its set; a new one is switched on in the view at once (view key `displays`).
 * A 409 of the core (the display changed meanwhile, e.g. a deleted style left its copy) is asked about.
 */
import { displayName, withDefaults, type StripLogSettings } from "../../charts/striplog/model";
import { CoreError } from "../../shell/api";
import { askConfirm } from "../../shell/dialogs";
import { updateView } from "../scene/bind";
import { tablesStore } from "../tables/state";
import { showDisplay } from "./displays";
import { uniqueName } from "./formModel";
import { chartsOfSet, saveChart, type ChartRow } from "./library";
import { formTables } from "./tables";

const errText = (e: unknown) => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));
const setName = (setId: number) => tablesStore.get().sets.find((s) => s.id === setId)?.name ?? `набор ${setId}`;

/** Switches a new display on in the view (the project keeps it); a view without a project just skips it. */
function switchOn(chart: number): void {
  try {
    updateView("displays", showDisplay(chart, true));
  } catch {
    /* no project view: nothing to remember */
  }
}

/**
 * Saves the settings of a form: a new display (a free name from its layers), or the changes of a saved one.
 * A saved display keeps its own name unless it still has the name made from its layers.
 */
export async function saveDisplay(setId: number, settings: StripLogSettings, existing: ChartRow | null, tableName?: string): Promise<{ chart: ChartRow; text: string }> {
  const auto = displayName(settings, tableName);
  const others = chartsOfSet(setId).filter((c) => c.id !== existing?.id).map((c) => c.name);
  const wasAuto = existing ? existing.name === displayName(withDefaults(existing.settings), tableName) : true;
  const name = wasAuto ? uniqueName(auto, others) : (existing as ChartRow).name;
  let chart: ChartRow;
  try {
    chart = await saveChart(setId, name, settings, existing ?? undefined);
  } catch (e) {
    if (!(e instanceof CoreError && e.status === 409 && e.code === "version_conflict" && existing)) throw new Error(errText(e));
    const current = e.body?.chart as ChartRow | undefined;
    const ok = await askConfirm({
      title: "Отображение изменено",
      message: `«${existing.name}» изменили, пока форма была открыта (например, удалили связанный стиль — слои получили копию его настроек). Записать настройки формы поверх?`,
      okLabel: "Записать",
      danger: true,
    });
    if (!ok || !current) throw new Error("Отображение изменено в другом месте: закройте форму и откройте его заново из дерева.");
    try {
      chart = await saveChart(setId, name, settings, current);
    } catch (again) {
      throw new Error(errText(again));
    }
  }
  if (!existing) switchOn(chart.id);
  return { chart, text: existing ? `Сохранено: ${setName(setId)} → «${chart.name}»` : `Сохранено в дереве: ${setName(setId)} → «${chart.name}»` };
}

export interface ApplyTarget {
  setId: number;
  settings: StripLogSettings;
}

/** «Применить к…»: new displays, saved and switched on at once. */
export async function createDisplays(targets: ApplyTarget[]): Promise<string> {
  const names: string[] = [];
  const tables = new Map<number, Awaited<ReturnType<typeof formTables>>>();
  for (const t of targets) {
    if (!tables.has(t.setId)) tables.set(t.setId, await formTables(t.setId).catch(() => []));
    const table = tables.get(t.setId)?.find((x) => x.key === t.settings.tracks[0]?.table);
    const name = uniqueName(displayName(t.settings, table?.name), chartsOfSet(t.setId).map((c) => c.name));
    try {
      const c = await saveChart(t.setId, name, t.settings);
      switchOn(c.id);
      names.push(c.name);
    } catch (e) {
      throw new Error(`${names.length ? `Создано ${names.length}, затем ошибка: ` : ""}${errText(e)}`);
    }
  }
  return `Создано отображений: ${names.length} (${names.slice(0, 3).join(", ")}${names.length > 3 ? "…" : ""})`;
}
