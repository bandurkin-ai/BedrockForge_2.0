/** «Данные по скважинам» (ТЗ 7.17): the window (holes.data_window, uiOnly) and its text for Claude (chart.striplog). */
import type { App } from "../../shell/app";
import { openWindow } from "../../shell/windows";
import { tablesStore } from "../../modules/tables/state";
import { logCsv } from "./csv";
import { loadLog, loadTables, MAX_HOLES } from "./data";

const viewport = (): [number, number] => (typeof window === "undefined" ? [1280, 800] : [window.innerWidth, window.innerHeight]);

export function bindStripLog(app: App): void {
  const activeSet = () => {
    const t = tablesStore.get();
    const set = t.sets.find((x) => x.id === t.setId);
    if (!set) throw new Error("Выберите набор скважин");
    return set;
  };
  app.registry.bind("holes.data_window", () => {
    const set = activeSet();
    const t = tablesStore.get();
    const tableHint = t.table && t.table !== "collar" && t.table !== "survey" ? t.table : null;
    openWindow("striplog", `Данные по скважинам · ${set.name}`, "normal", { setId: set.id, setName: set.name, tableHint, holeHint: app.store.get().holeId }, ...viewport());
    return `Открыто окно «Данные по скважинам» набора «${set.name}»`;
  });
  app.registry.bind("chart.striplog", async (a) => {
    const set = activeSet();
    const holes = String(a.holes ?? app.store.get().holeId ?? "")
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
    if (!holes.length) throw new Error("Укажите скважины или выберите скважину");
    if (holes.length > MAX_HOLES) throw new Error(`Не больше ${MAX_HOLES} скважин рядом`);
    const tables = await loadTables(set.id);
    const name = String(a.table ?? "").trim().toLowerCase();
    const table = name ? tables.find((x) => x.name.toLowerCase() === name) : (tables.find((x) => x.name === "Пробы") ?? tables[0]);
    if (!table) throw new Error(name ? `Нет таблицы «${a.table}» в наборе` : "В наборе нет таблиц опробования");
    const asked = String(a.fields ?? "").split(",").map((x) => x.trim()).filter(Boolean);
    const fields = asked.length ? asked : table.columns.filter((c) => c.kind === "number").slice(0, 8).map((c) => c.name);
    const missing = fields.filter((f) => !table.columns.some((c) => c.name === f));
    if (missing.length) throw new Error(`Нет полей: ${missing.join(", ")}`);
    const data = await loadLog(set.id, holes, [{ table: table.key, fields }]);
    const text = logCsv(data, fields.map((f) => ({ table: table.key, field: f, title: f, event: table.kind === "event" })));
    return text.length > 20_000 ? `${text.slice(0, 20_000)}\n… (обрезано)` : text;
  });
}
