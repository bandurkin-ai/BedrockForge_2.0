/** Commands of drillhole sets and the «Таблица» panel: set.create, set.select, table.open (ТЗ 7.10). */
import type { App } from "../../shell/app";
import { askText } from "../../shell/dialogs";
import { api, CoreError } from "../../shell/api";
import { setPanel } from "../../shell/workspace";
import { onCoreEvent, onJobFinished } from "../jobs/state";
import { projectStore } from "../projects/state";
import { loadEdits, loadSets, loadTabs, reload, resolveTable, runTool, selectSet, openTable, setWhere, tablesStore, undoEdit } from "./state";

const needProject = (ctx: { hasProject: boolean }) => (ctx.hasProject ? null : "Откройте или создайте проект");
const errText = (e: unknown) => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));

function syncStatus(app: App): void {
  const s = tablesStore.get();
  const set = s.sets.find((x) => x.id === s.setId);
  app.store.set({ setName: set?.name ?? null, setHoles: set?.holes ?? 0 });
}

export function bindTables(app: App): void {
  tablesStore.subscribe(() => {
    const s = tablesStore.get();
    const set = s.sets.find((x) => x.id === s.setId);
    const st = app.store.get();
    if (st.setName !== (set?.name ?? null) || st.setHoles !== (set?.holes ?? 0)) syncStatus(app);
  });
  let uid: string | null = null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    if (cur) void loadSets(true);
    else tablesStore.set({ sets: [], setId: null, tabs: [], table: null, columns: [], total: 0, pages: {} });
  });
  onJobFinished((job) => {
    if (job.kind === "import.drillholes" && job.status === "done") void loadSets(true).then(() => Promise.all([loadTabs(), reload()]));
  });
  onCoreEvent((event, data) => {
    if (event === "objects.changed") void loadSets();
    if (event !== "drillholes.changed") return;
    try {
      const d = JSON.parse(data) as { set_id: number; table: string; rev: number };
      const s = tablesStore.get();
      if (d.set_id !== s.setId) return;
      void loadTabs();
      if (d.table === s.table && d.rev !== s.rev) void reload();
    } catch {
      /* a malformed event is ignored */
    }
  });

  app.registry.bind(
    "set.create",
    async (a) => {
      const name = typeof a.name === "string" && a.name.trim() ? a.name : await askText({ title: "Новый набор скважин", label: "Название", initial: "Скважины", okLabel: "Создать" });
      if (!name) return "Отменено";
      try {
        const r = await api.post<{ id: number; name: string }>("/drillholes/sets", { name });
        await loadSets();
        await selectSet(r.id, "collar");
        return `Создан набор «${r.name}»`;
      } catch (e) {
        throw new Error(errText(e));
      }
    },
    needProject,
  );
  app.registry.bind(
    "set.select",
    async (a) => {
      const id = Number(a.set_id);
      if (!tablesStore.get().sets.length) await loadSets();
      const set = tablesStore.get().sets.find((x) => x.id === id);
      if (!set) throw new Error(`Набор №${id} не найден`);
      await selectSet(id);
      return `Активный набор: «${set.name}»`;
    },
    needProject,
  );
  app.registry.bind(
    "table.open",
    async (a) => {
      if (a.set_id !== undefined && Number(a.set_id) !== tablesStore.get().setId) await selectSet(Number(a.set_id));
      const s = tablesStore.get();
      if (s.setId === null) throw new Error("Нет активного набора скважин");
      const table = resolveTable(s.tabs, String(a.kind));
      if (!table) throw new Error(`В наборе нет таблицы «${String(a.kind)}»`);
      await openTable(table);
      app.store.set((st) => ({ workspace: setPanel(st.workspace, "bottom", true, "table") }));
      return `Открыта таблица «${tablesStore.get().tabs.find((t) => t.table === table)?.name ?? table}»`;
    },
    (ctx) => (ctx.hasSet ? null : "Нет активного набора скважин"),
  );

  bindTools(app);
}

const needTable = (ctx: { hasSet: boolean }) => (ctx.hasSet && tablesStore.get().table ? null : "Откройте таблицу набора");

/** The panel tools as registry commands (palette, hotkeys, Claude). */
function bindTools(app: App): void {
  const tool = async (path: string, body: Record<string, unknown>, method: "post" | "patch" | "del" = "post") => {
    const r = await runTool(path, body, method);
    if (!r.ok) throw new Error(r.error);
    return r.result;
  };
  const list = (v: unknown) => String(v ?? "").split(",").map((x) => x.trim()).filter(Boolean);
  app.registry.bind("table.filter", async (a) => {
    await setWhere(String(a.where ?? ""));
    const s = tablesStore.get();
    if (s.error) throw new Error(s.error);
    return `Строк: ${s.total.toLocaleString("ru-RU")}`;
  }, needTable);
  app.registry.bind("table.replace", async (a) => {
    const s = tablesStore.get();
    const r = await tool("replace", { column: a.column, find: a.find, replace: a.replace ?? "", whole: !!a.whole, case: !!a.case, hole: s.hole?.id ?? null, where: s.where || null });
    return `Заменено: ${String(r.replaced)}`;
  }, needTable);
  app.registry.bind("table.computed", async (a) => {
    const r = await tool("columns", { name: a.name, expression: a.expression });
    return `Колонка «${String(r.name)}» добавлена`;
  }, needTable);
  app.registry.bind("table.column_add", async (a) => {
    const r = await tool("columns", { name: a.name, kind: a.kind ?? "number", unit: a.unit ?? null });
    return `Колонка «${String(r.name)}» добавлена`;
  }, needTable);
  app.registry.bind("table.column_rename", async (a) => {
    await tool(`columns/${encodeURIComponent(String(a.column))}`, { name: a.name }, "patch");
    return "Колонка переименована";
  }, needTable);
  app.registry.bind("table.column_delete", async (a) => {
    await tool(`columns/${encodeURIComponent(String(a.column))}`, {}, "del");
    return "Колонка удалена (отмена — в журнале правок)";
  }, needTable);
  app.registry.bind("table.append", async (a) => {
    const r = await tool("append", { source: String(a.source) });
    return `Добавлено строк: ${String(r.rows)}`;
  }, needTable);
  app.registry.bind("table.merge", async (a) => {
    const r = await tool("merge", { source: String(a.source), key: ["hole", ...list(a.key)], columns: list(a.columns) });
    return `Совпало строк: ${String(r.matched)} из ${String(r.rows)}`;
  }, needTable);
  app.registry.bind("table.codes_check", async (a) => {
    const s = tablesStore.get();
    try {
      const r = await api.get<{ bad: { value: string; count: number }[]; rows: number; where: string | null }>(`/drillholes/sets/${s.setId}/tables/${s.table}/codes/check?column=${encodeURIComponent(String(a.column))}`);
      if (!r.rows) return "Все значения есть в справочнике";
      if (r.where) await setWhere(r.where);
      return `Нет в справочнике: ${r.bad.map((b) => `${b.value} (${b.count})`).join(", ")}`;
    } catch (e) {
      throw new Error(errText(e));
    }
  }, needTable);
  app.registry.bind("table.undo", async (a) => {
    const id = a.edit_id !== undefined ? Number(a.edit_id) : (await loadEdits()).find((e) => !e.undone)?.id;
    if (id === undefined) return "Правок для отмены нет";
    const err = await undoEdit(id);
    if (err) throw new Error(err);
    return `Правка №${id} отменена`;
  }, (ctx) => (ctx.hasSet ? null : "Нет активного набора скважин"));
  app.registry.bind("table.export", async () => {
    app.store.set((st) => ({ workspace: setPanel(st.workspace, "bottom", true, "table") }));
    return "Экспорт: «Инструменты ▾» → «Экспорт таблицы…»";
  }, needTable);
}
