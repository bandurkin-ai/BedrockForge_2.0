/**
 * The string editor in the application (ADR 0015 §5; ТЗ 7.22): one session at a time. Open — the exact geometry
 * of the layer and its version (the base of the save), the layer hidden in the scene while its working copy is
 * edited; save — `PUT …/geometry` with the body of the session (only changed and new lines carry vertices), then
 * the layer read again (`saved(reloaded)`: the new lines take their stable numbers); a 409 — the own window «Слой
 * сохранён после начала правки» with «Сохранить как новый слой», «Перечитать слой», «Отмена»; finish — the
 * question about unsaved changes in an own window. No React.
 */
import { bridge } from "../../bridge";
import { asLoaded, readLineGeometry, saveBody, type LoadedLayer } from "../../edit/io";
import { emptyLayer, MAX_EDIT_VERTICES } from "../../edit/model";
import { EditSession } from "../../edit/session";
import type { App } from "../../shell/app";
import { refusal } from "../../shell/objectRun";
import { projectStore } from "../projects/state";
import { viewStore } from "../scene/bind";
import { lineLayers, loadSceneObjects, sceneObjects, type LineLayerInfo } from "../scene/objects";
import { currentScene, onScene } from "../scene/sceneHost";
import { sectionOf } from "../section/state";
import { tablesStore } from "../tables/state";
import { askChoice, CANCEL } from "./choice";
import { conflictOf, getGeometry, newLayer, putGeometry, type Conflict } from "./editApi";
import { editStore } from "./editState";
import { attachEditorKeys } from "./editKeys";
import { attachSession, viewBox } from "./editScene";
import { SnapFeeder } from "./snapSources";

declare global {
  interface Window {
    __bfEditor?: unknown;
  }
}

let live: { session: EditSession; stop: () => void } | null = null;

export const currentSession = (): EditSession | null => editStore.get().session;

/** The open session, or the refusal of a command that needs one. */
export function requireSession(): EditSession {
  const s = editStore.get().session;
  if (!s) throw refusal("not_editing", "Редактор стрингов не открыт.", "Откройте слой на правку: меню слоя «Редактировать» или «Моделирование › Новый слой», затем повторите.");
  return s;
}

const NUM = new Intl.NumberFormat("ru-RU");

/** «19.11.2026, 14:05» from the time of the core (ISO). */
export function formatAt(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

async function layerInfo(id: number): Promise<LineLayerInfo> {
  if (!sceneObjects.get().loaded) await loadSceneObjects();
  const info = lineLayers().find((l) => l.id === id);
  if (!info) throw refusal("line_layer_not_found", `Слой линий №${id} не найден.`, "Обновите дерево проекта и выберите слой.");
  return info;
}

/** The level of new points in plan: the middle of the heights of the layer, else of the view. */
function startLevel(info: LineLayerInfo): number {
  const e = info.extent;
  if (e && typeof e[2] === "number" && typeof e[5] === "number" && Number.isFinite(e[2]) && Number.isFinite(e[5])) return Math.round((e[2] + e[5]) / 2);
  const sc = currentScene();
  const o = sc?.origin.value;
  if (!sc || !o) return 0;
  return Math.round(sc.camera().target[2] / (sc.debug().vexag || 1) + o.z);
}

function applySection(s: EditSession): void {
  const { line, clip } = sectionOf(viewStore.get());
  s.section = line;
  s.clip = clip;
  s.viewChanged();
  s.touch();
}

function e2e(): boolean {
  const b = bridge();
  if (b?.flags) return b.flags().e2e;
  return typeof location !== "undefined" && new URLSearchParams(location.search).get("e2e") === "1";
}

/** A session becomes the open one: the scene, the snap sources, the section and the visibility follow it. */
function start(app: App, s: EditSession, prev: EditSession | null = null): void {
  live?.stop();
  if (prev) {
    s.snap = prev.snap;
    s.zLevel = prev.zLevel;
    s.tool = prev.tool;
  }
  applySection(s);
  editStore.set((st) => ({ session: s, generation: st.generation + 1, saving: false, snapNotes: [], focused: true }));
  app.store.set({ editing: true });
  const hidden = () => new Set(app.store.get().view.hidden);
  const feeder = new SnapFeeder(s, {
    lines: () => lineLayers().filter((l) => !hidden().has(`ln:${l.id}`)),
    sets: () => tablesStore.get().sets.filter((st) => !hidden().has(`set:${st.id}`)).map((st) => st.id),
    wireframes: () => sceneObjects.get().wireframes.filter((w) => !hidden().has(`wf:${w.id}`)),
    section: () => sectionOf(viewStore.get()),
    viewBox: () => {
      const sc = currentScene();
      return sc ? viewBox(sc) : null;
    },
  });
  const offs: (() => void)[] = [attachEditorKeys(app)];
  let detach: (() => void) | null = null;
  offs.push(
    onScene((scene) => {
      detach?.();
      detach = scene ? attachSession(s, scene, () => feeder.refresh(400)) : null;
      feeder.refresh(0);
    }),
  );
  let section = JSON.stringify(sectionOf(viewStore.get()));
  offs.push(
    viewStore.subscribe(() => {
      const next = JSON.stringify(sectionOf(viewStore.get()));
      if (next === section) return;
      section = next;
      applySection(s);
      feeder.refresh();
    }),
  );
  let hiddenKeys = app.store.get().view.hidden;
  offs.push(
    app.store.subscribe(() => {
      const h = app.store.get().view.hidden;
      if (h === hiddenKeys) return;
      hiddenKeys = h;
      feeder.refresh();
    }),
  );
  offs.push(sceneObjects.subscribe(() => feeder.refresh()));
  offs.push(tablesStore.subscribe(() => feeder.refresh()));
  // another project: the session goes with the scene of the old one
  const uid = projectStore.get().current?.uid ?? null;
  offs.push(projectStore.subscribe(() => void ((projectStore.get().current?.uid ?? null) !== uid && end(app))));
  live = {
    session: s,
    stop: () => {
      detach?.();
      feeder.dispose();
      // no hover and no probe of the core left behind by the session that goes
      s.leave();
      offs.forEach((fn) => fn());
    },
  };
  if (e2e() && typeof window !== "undefined") {
    window.__bfEditor = {
      state: () => {
        const cur = editStore.get().session;
        if (!cur) return null;
        return { layerId: cur.layerId, version: cur.baseVersion, dirty: cur.dirty, tool: cur.tool, lines: cur.layer.lines.length, selection: cur.selection, message: cur.message, hover: cur.hover };
      },
      /** the vertices of a line of the working copy by its place (−1 — the last), project coordinates */
      points: (i: number) => editStore.get().session?.layer.lines.at(i)?.points ?? null,
      /** the open session itself: the timings of app/e2e/perfEditor.spec.ts (snap index, queries) */
      session: () => editStore.get().session,
    };
  }
}

/** Out of the editor without a question: the layer shows again (read anew if it was saved). */
function end(app: App): void {
  live?.stop();
  live = null;
  editStore.set({ session: null, saving: false, snapNotes: [] });
  app.store.set({ editing: false });
}

/** `lines.edit`: the layer on edit (the open one is finished first, with the question about its changes). */
export async function openEditor(app: App, layerId: number): Promise<string> {
  const info = await layerInfo(layerId);
  if (info.cloud) throw refusal("not_editable", `«${info.name}» — облако точек: в редакторе стрингов не правится.`, "Облако меняют фильтр земли и классы точек; линии — в обычном слое.");
  if (info.point_count > MAX_EDIT_VERTICES) throw refusal("layer_too_big", `В слое «${info.name}» ${NUM.format(info.point_count)} вершин — больше 1 млн.`, "Разделите слой на части при импорте или проредите его.");
  const cur = editStore.get().session;
  if (cur?.layerId === layerId) return `Слой «${info.name}» уже на правке`;
  if (cur && !(await finishEditor(app))) return `Правка слоя «${cur.layerName}» продолжается`;
  const loaded = readLineGeometry(await getGeometry(layerId));
  const s = new EditSession(loaded, info.name, info.color ?? "");
  s.zLevel = startLevel(info);
  start(app, s);
  return `Правка слоя «${info.name}»: версия ${loaded.version}, линий ${NUM.format(loaded.layer.lines.length)}. Ctrl+S — сохранить, «Завершить» — выйти из правки`;
}

/** The layer read anew in its current version; the changes not saved go. */
export async function reloadEditor(app: App): Promise<string> {
  const s = requireSession();
  const loaded = readLineGeometry(await getGeometry(s.layerId));
  start(app, new EditSession(loaded, s.layerName, s.color), s);
  return `Слой «${s.layerName}» перечитан: версия ${loaded.version}`;
}

/** Pauses before the second and the third reading of the layer after a save (tests set them to zero). */
export const rereadTiming = { pauses: [300, 1000] };

const pause = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * After a save (or a move, or a save as a new layer) of `sent`: the layer read back and `sent` rebased on it,
 * the edits made meanwhile carried over (`EditSession.saved`). The reading is tried three times; when all fail
 * the session keeps `unsynced` and the next save reads first. True — read.
 */
export async function syncSaved(s: EditSession, pending: NonNullable<EditSession["unsynced"]>): Promise<boolean> {
  s.unsynced = pending;
  for (let i = 0; i <= rereadTiming.pauses.length; i++) {
    if (i) await pause(rereadTiming.pauses[i - 1]);
    let loaded: LoadedLayer;
    try {
      loaded = readLineGeometry(await getGeometry(pending.layerId));
    } catch {
      continue;
    }
    if (s.unsynced !== pending) return true; // read meanwhile by another call
    const switched = pending.layerId !== s.layerId;
    s.unsynced = null;
    s.saved(loaded, pending.moved, pending.sent);
    if (pending.name) s.layerName = pending.name;
    if (switched && editStore.get().session === s) editStore.set((st) => ({ session: s, generation: st.generation + 1 }));
    return true;
  }
  s.say("Слой сохранён, но не перечитан: перед следующим сохранением он будет перечитан");
  return false;
}

const notRead = (s: EditSession) =>
  refusal("layer_not_reread", `Слой «${s.layerName}» сохранён, но после сохранения его не удалось перечитать — новые правки не записаны.`, "Проверьте, что ядро работает (вкладка «Задания» или перезапуск ядра), и сохраните ещё раз (Ctrl+S): слой будет перечитан, правки сохранятся поверх.");

/** The layer of an earlier save read first (a refusal when it still cannot be read): no body over an old base. */
export async function ensureSynced(s: EditSession): Promise<void> {
  if (s.unsynced && !(await syncSaved(s, s.unsynced))) throw notRead(s);
}

/** `lines.save`: a new version of the layer. */
export async function saveEditor(app: App, note = ""): Promise<string> {
  const s = requireSession();
  if (s.drawing) s.finishDrawing();
  if (!s.dirty && !s.unsynced) return "Изменений нет: сохранять нечего";
  if (editStore.get().saving) return "Сохранение уже идёт";
  editStore.set({ saving: true });
  try {
    await ensureSynced(s);
    if (!s.dirty) return `Слой «${s.layerName}» перечитан: версия ${s.baseVersion}, других изменений нет`;
    // the body is made of this working copy; edits made while it is on its way stay (and stay unsaved)
    const sent = s.layer;
    let version: number;
    try {
      version = (await putGeometry(s.layerId, s.saveBody(note))).version;
    } catch (e) {
      const c = conflictOf(e);
      if (!c) throw e;
      return await onConflict(app, s, c);
    }
    void loadSceneObjects();
    if (!(await syncSaved(s, { layerId: s.layerId, sent, moved: [] }))) {
      return `Сохранено: слой «${s.layerName}», версия ${version}. Слой не удалось перечитать — перед следующим сохранением он будет перечитан автоматически`;
    }
    return `Сохранено: слой «${s.layerName}», версия ${s.baseVersion}${s.dirty ? ". Правки, сделанные во время сохранения, ещё не записаны" : ""}`;
  } finally {
    editStore.set({ saving: false });
  }
}

/** The text of the window of a 409. */
export function conflictText(name: string, c: Conflict): string {
  const who = [c.author, formatAt(c.at)].filter(Boolean).join(", ");
  return `Слой «${name}» сохранён после начала правки (версия ${c.version}${who ? `, ${who}` : ""}). Ничего не записано.`;
}

async function onConflict(app: App, s: EditSession, c: Conflict): Promise<string> {
  const answer = await askChoice("Слой изменён", conflictText(s.layerName, c), [
    { id: "new", label: "Сохранить как новый слой", primary: true, hint: "Правка сохраняется отдельным слоем в том же файле; этот слой не меняется" },
    { id: "reload", label: "Перечитать слой", danger: true, hint: `Слой загружается заново в версии ${c.version}; несохранённые правки пропадут` },
    { id: CANCEL, label: "Отмена", hint: "Остаться в правке: ничего не записано" },
  ]);
  if (answer === "new") return saveAsNew(s);
  if (answer === "reload") return reloadEditor(app);
  throw refusal("layer_changed", conflictText(s.layerName, c), "Сохраните правку как новый слой или перечитайте слой.");
}

/** The working copy into a new layer next to the edited one; the session goes on in the new layer. */
async function saveAsNew(s: EditSession): Promise<string> {
  const old = s.layerName;
  const info = lineLayers().find((l) => l.id === s.layerId);
  const made = await newLayer(`${old} (правка)`, info?.file_id ?? null);
  const sent = s.layer;
  const version = (await putGeometry(made.id, saveBody(sent, asLoaded(made.id, 1, emptyLayer())))).version;
  void loadSceneObjects();
  if (!(await syncSaved(s, { layerId: made.id, sent, moved: [], name: made.name }))) {
    return `Сохранено как новый слой «${made.name}» (версия ${version}); слой «${old}» не изменён. Новый слой не удалось перечитать — перед следующим сохранением он будет перечитан, правка продолжится в нём`;
  }
  return `Сохранено как новый слой «${made.name}» (версия ${s.baseVersion}); слой «${old}» не изменён`;
}

/** `lines.finish`: out of the editor; unsaved changes — the question in an own window. False — stayed. */
export async function finishEditor(app: App): Promise<boolean> {
  const s = editStore.get().session;
  if (!s) return true;
  if (s.drawing) s.finishDrawing();
  if (s.dirty) {
    const sum = s.summary();
    const answer = await askChoice("Завершить правку", `В слое «${s.layerName}» есть несохранённые изменения: изменено линий ${sum.touched}, новых ${sum.created}, удалено ${sum.removed}. Сохранить их?`, [
      { id: "save", label: "Сохранить", primary: true, hint: "Новая версия слоя, затем выход из правки" },
      { id: "discard", label: "Не сохранять", danger: true, hint: "Выйти из правки: изменения пропадут" },
      { id: CANCEL, label: "Отмена", hint: "Остаться в правке" },
    ]);
    if (answer === CANCEL) return false;
    if (answer === "save") {
      await saveEditor(app);
      if (editStore.get().session?.dirty) return false;
    }
  }
  end(app);
  return true;
}
