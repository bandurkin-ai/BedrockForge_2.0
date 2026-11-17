/** Commands of drillhole sets and the «Таблица» panel: set.create, set.select, table.open (ТЗ 7.10). */
import type { App } from "../../shell/app";
import { askText } from "../../shell/dialogs";
import { api, CoreError } from "../../shell/api";
import { setPanel } from "../../shell/workspace";
import { onCoreEvent, onJobFinished } from "../jobs/state";
import { projectStore } from "../projects/state";
import { loadPage, loadSets, loadTabs, resolveTable, selectSet, openTable, tablesStore } from "./state";

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
    if (cur) void loadSets();
    else tablesStore.set({ sets: [], setId: null, tabs: [], table: null, columns: [], total: 0, pages: {} });
  });
  onJobFinished((job) => {
    if (job.kind === "import.drillholes" && job.status === "done") void loadSets().then(() => loadTabs());
  });
  onCoreEvent((event, data) => {
    if (event === "objects.changed") void loadSets();
    if (event !== "drillholes.changed") return;
    try {
      const d = JSON.parse(data) as { set_id: number; table: string; rev: number };
      const s = tablesStore.get();
      if (d.set_id !== s.setId) return;
      void loadTabs();
      if (d.table === s.table && d.rev !== s.rev) {
        tablesStore.set({ pages: {} });
        void loadPage(0);
      }
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
}
