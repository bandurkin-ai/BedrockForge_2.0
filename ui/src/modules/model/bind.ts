/**
 * Commands of the modelling operations (ТЗ 7.23; ADR 0015 §6 «Команды»): the 12 windows `model.*` — one window
 * «model-operation» built from the schema of the core (`OperationWindow.tsx`; `model.dtm` keeps the window
 * «Построение ЦТМ» of `modules/dtm`), `model.operation {op, params, name, folder}` (confirm: a call of Claude runs
 * after the person allows it) and `model.rebuild {wireframe_id | layer_id}` (confirm). A started job is followed
 * (`run.ts`): «Задания» open, the wireframes made are checked, the report opens when it is done.
 */
import type { App } from "../../shell/app";
import { needWritable, noted, refusal, showJobs, viewport } from "../../shell/objectRun";
import { openWindow } from "../../shell/windows";
import { sceneObjects } from "../scene/objects";
import { DIALOG_COMMANDS, dialogOps, dialogTitle, type Prefill } from "./opForms";
import { afterStart, loadOperations, queueText, refusalOf, startOperation, startRebuild, watchOperations, type RebuildTarget, type Started } from "./run";

/** The props of the window «model-operation». */
export interface OperationWindowProps {
  dialog: string;
  op?: string;
  wireframeId?: number;
  layerId?: number;
}

/** The object the window opens with: named by the command, else the one chosen in the tree or the 3D window. */
export function prefillOf(app: App, a: Record<string, unknown>): Prefill {
  const selected = app.store.get().selected;
  const wireframeId = typeof a.wireframe_id === "number" ? a.wireframe_id : selected?.kind === "wireframe" ? selected.id : null;
  const layerId = typeof a.layer_id === "number" ? a.layer_id : selected?.kind === "lines" ? selected.id : null;
  return { wireframeId, layerId };
}

export async function openOperationWindow(dialog: string, pre: Prefill = {}, op?: string): Promise<string> {
  const list = await loadOperations();
  if (!dialogOps(list, dialog).length) throw refusal("unknown_dialog", `Окна операции «${dialog}» нет в ядре.`, "Обновите программу: версии интерфейса и ядра не совпадают.");
  const props: OperationWindowProps = { dialog, ...(op ? { op } : {}), ...(pre.wireframeId ? { wireframeId: pre.wireframeId } : {}), ...(pre.layerId ? { layerId: pre.layerId } : {}) };
  const title = dialogTitle(list, dialog);
  openWindow("model-operation", title, "form", props as unknown as Record<string, unknown>, ...viewport());
  return title;
}

/** `params` of `model.operation`: a JSON object (an empty text — no parameters). */
export function paramsArg(value: unknown): Record<string, unknown> {
  if (value === undefined || value === null || (typeof value === "string" && !value.trim())) return {};
  let parsed: unknown = value;
  if (typeof value === "string") {
    try {
      parsed = JSON.parse(value);
    } catch {
      parsed = null;
    }
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw refusal("bad_params", "params — не JSON-объект.", 'Передайте параметры объектом JSON, например {"wireframe_id": 12, "iterations": 10}.');
  return parsed as Record<string, unknown>;
}

/** The text of a refusal of the core; an open wireframe — with the way to fix it. */
function refused(e: unknown): Error {
  const { text, fix } = refusalOf(e);
  return new Error(fix ? `${text} Исправить: окно «Исправить» (model.repair, wireframe_id ${fix.wireframe_id}) или model.operation с op repair.` : text);
}

const startedText = (s: Started, what: string): string =>
  `${what} (задание №${s.id}, ${queueText(s.queue)})${s.warning ? `. ${s.warning}` : ""}: ход — на вкладке «Задания», отчёт откроется, когда задание будет готово`;

/** What «Перестроить» rebuilds: named, else the wireframe or the layer chosen. */
function rebuildTarget(app: App, a: Record<string, unknown>): { target: RebuildTarget; name: string } {
  const o = sceneObjects.get();
  if (typeof a.wireframe_id === "number") return { target: { wireframe_id: a.wireframe_id }, name: o.wireframes.find((w) => w.id === a.wireframe_id)?.name ?? `№${a.wireframe_id}` };
  const layer = (id: number) => o.files.flatMap((f) => f.layers).find((l) => l.id === id)?.name ?? `№${id}`;
  if (typeof a.layer_id === "number") return { target: { layer_id: a.layer_id }, name: layer(a.layer_id) };
  const s = app.store.get().selected;
  if (s?.kind === "wireframe") return { target: { wireframe_id: s.id }, name: o.wireframes.find((w) => w.id === s.id)?.name ?? `№${s.id}` };
  if (s?.kind === "lines") return { target: { layer_id: s.id }, name: layer(s.id) };
  throw refusal("no_object", "Не выбран объект для «Перестроить».", "Выберите в дереве каркас, ЦТМ или слой линий, построенные операцией, или укажите wireframe_id либо layer_id.");
}

let watching = false;

export function bindModel(app: App): void {
  const r = app.registry;
  const writable = needWritable(app);
  if (!watching) {
    watching = true;
    watchOperations();
  }

  for (const [id, dialog] of Object.entries(DIALOG_COMMANDS)) {
    if (dialog === "dtm") continue; // «Построение ЦТМ» — modules/dtm/bind.ts
    r.bind(
      id,
      noted(app, async (a) => {
        const op = typeof a.op === "string" ? a.op : undefined;
        const title = await openOperationWindow(dialog, prefillOf(app, a), op);
        return `Открыто окно «${title}»`;
      }),
      writable,
    );
  }

  r.bind(
    "model.operation",
    noted(app, async (a) => {
      const params = paramsArg(a.params);
      let started: Started;
      try {
        started = await startOperation({ op: String(a.op), params, name: typeof a.name === "string" ? a.name : null, folder: typeof a.folder === "string" ? a.folder : null });
      } catch (e) {
        throw refused(e);
      }
      showJobs(app);
      await afterStart(started);
      return startedText(started, `Запущена операция «${started.title}»`);
    }),
    writable,
  );

  r.bind(
    "model.rebuild",
    noted(app, async (a) => {
      const { target, name } = rebuildTarget(app, a);
      let started: Started;
      try {
        started = await startRebuild(target);
      } catch (e) {
        throw refused(e);
      }
      showJobs(app);
      await afterStart(started);
      return startedText(started, `Запущено «Перестроить» для «${name}»: «${started.title}» на текущих исходных`);
    }),
    writable,
  );
}
