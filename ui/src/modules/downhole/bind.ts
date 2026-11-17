/**
 * Displays along holes and the style library (ТЗ 7.16, ADR 0007 §3, §8): the library of the open project,
 * the commands of the forms (downhole.open, holes.show_data) and of the style files (styles.export, styles.import).
 * The 3D layer of the displays (6.15) reads `libraryStore`, `viewStore.displays` and `draftStore`.
 */
import type { App } from "../../shell/app";
import type { CommandContext } from "../../shell/commands";
import { closeWindow, windowStore } from "../../shell/windows";
import type { DisplayForm } from "../../charts/striplog/model";
import { projectStore } from "../projects/state";
import { tablesStore } from "../tables/state";
import { exportStyles, importStyles, libraryStore, loadLibrary, readStyleFile } from "./library";
import { openDisplay, openForm, openImport } from "./open";
import { errText } from "./styleActions";
import { formTables } from "./tables";

const FORMS: DisplayForm[] = ["grade", "lith", "event"];
const needProject = (ctx: CommandContext) => (ctx.hasProject ? null : "Откройте проект");

function setOf(a: Record<string, unknown>): { id: number; name: string } {
  const t = tablesStore.get();
  const id = typeof a.set_id === "number" ? a.set_id : (t.setId ?? (t.sets.length === 1 ? t.sets[0].id : null));
  const set = t.sets.find((x) => x.id === id);
  if (!set) throw new Error(typeof a.set_id === "number" ? `Нет набора скважин ${a.set_id}` : "Выберите набор скважин");
  return set;
}

const formArg = (v: unknown): DisplayForm | null => (FORMS.includes(v as DisplayForm) ? (v as DisplayForm) : null);

export function bindDownhole(app: App): void {
  const { registry, store } = app;

  // The library of the open project; the forms of another project close.
  let uid: string | null = null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    for (const w of windowStore.get().windows) if (w.kind === "downhole" || w.kind === "styles-import") closeWindow(w.id);
    libraryStore.set({ styles: [], charts: [], loaded: false });
    if (cur) void loadLibrary().catch((e: unknown) => store.set({ statusNote: `Библиотека стилей не прочитана: ${errText(e)}` }));
  });

  registry.bind("downhole.open", (a) => {
    if (typeof a.chart_id === "number") {
      openDisplay(a.chart_id);
      return `Открыто отображение ${a.chart_id}`;
    }
    const set = setOf(a);
    const form = formArg(a.form) ?? "grade";
    openForm(set.id, form);
    return `Открыта форма «${form === "grade" ? "Содержания" : form === "lith" ? "Литология" : "События"}» набора «${set.name}»`;
  }, needProject);

  registry.bind("holes.show_data", async (a) => {
    const set = setOf(a);
    const field = typeof a.field === "string" && a.field.trim() ? a.field.trim() : null;
    const tableName = typeof a.table === "string" && a.table.trim() ? a.table.trim() : null;
    let form = formArg(a.form);
    if (!field) {
      openForm(set.id, form ?? "grade");
      return `Открыта форма отображения набора «${set.name}»: выберите поля; в дерево отображение попадёт по «Сохранить»`;
    }
    const tables = await formTables(set.id);
    const q = tableName?.toLowerCase();
    const pool = q ? tables.filter((t) => t.name.toLowerCase() === q || t.key === q) : tables;
    if (q && !pool.length) throw new Error(`Нет таблицы «${tableName}» в наборе «${set.name}». Таблицы: ${tables.map((t) => t.name).join(", ") || "нет"}`);
    const table = pool.find((t) => t.columns.some((c) => c.name.toUpperCase() === field.toUpperCase()));
    const column = table?.columns.find((c) => c.name.toUpperCase() === field.toUpperCase());
    if (!table || !column) throw new Error(`Нет поля «${field}»${tableName ? ` в таблице «${tableName}»` : " в таблицах набора"}`);
    form ??= table.kind === "event" ? "event" : column.kind === "number" ? "grade" : "lith";
    openForm(set.id, form, { table: table.key, field: column.name });
    return `Черновик отображения «${column.name}» (${table.name}) показан на скважинах набора «${set.name}»; в дерево попадёт по «Сохранить» в форме`;
  }, needProject);

  registry.bind("styles.export", async () => {
    if (!libraryStore.get().styles.length) throw new Error("В библиотеке проекта нет стилей: сохраните стиль в форме отображения");
    const path = await exportStyles(null);
    return path ? `Стили сохранены: ${path}` : "Сохранение отменено";
  }, needProject);

  registry.bind("styles.import", async () => {
    const f = await readStyleFile();
    if (!f) return "Импорт отменён";
    if (!f.conflicts.length) {
      const r = await importStyles(f.file, {});
      return `Импорт из «${f.name}»: добавлено ${r.created}`;
    }
    openImport(f);
    return `Импорт из «${f.name}»: совпадают названия (${f.conflicts.length}) — выберите, что сделать с каждым`;
  }, needProject);
}
