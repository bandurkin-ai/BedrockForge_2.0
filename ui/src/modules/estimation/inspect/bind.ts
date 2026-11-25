/**
 * The handler of `estimate.inspect` and the window «Блок · i, j, k» (ТЗ 7.35 Д-30; 7.35.4 «Геостатистика › Проверка › Инспектор блока»): the command
 * opens the window and asks the core for the block (`block` — «i; j; k» of the parent, `xyz` — a point, `params_id` — one row of the plan); a click
 * on a block of a model that has an estimation (`pickedBlockStore`, filled by the 3D) asks for that block by its row. The window follows the rows:
 * a run or a change of the rows asks the same block again (the flag «Параметры или входы изменились» comes from the core). The layers of the 3D —
 * a lazy chunk (`scene.ts`). `bindEstimation` (`../bind.ts`) calls `bindInspect(app)` and `renderEstimationWindow` — `renderInspectWindow(w)`.
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../../shell/app";
import { needProject, noted, refusal, viewport } from "../../../shell/objectRun";
import { focusWindow, openWindow, updateWindow, windowStore, type FloatWin } from "../../../shell/windows";
import { blockById, blocksStore, isBlocksEvent } from "../../blocks/state";
import { pickedBlockStore } from "../../blocks/display";
import { onCoreEvent } from "../../jobs/state";
import { onScene } from "../../scene/sceneHost";
import { projectStore } from "../../projects/state";
import { estimationStore, isEstimationEvent } from "../state";
import type { InspectAnswer, InspectRequest, Triple } from "./api";
import { InspectWindow } from "./InspectWindow";
import { inspect, inspectStore, INSPECT_WINDOW, reinspect, resetInspect } from "./state";
import { blockTitle, numText, unsolvedNote } from "./view";

export const INSPECT_TITLE = "Блок · i, j, k";

/** The window of the inspector, if open. */
export const inspectWindow = (): string | null => windowStore.get().windows.find((w) => w.kind === INSPECT_WINDOW)?.id ?? null;

/** «13; 7; 1» or «1040,2; 2066,5; −296» → three numbers (integers when asked); null — not that. */
export function parseTriple(text: unknown, integer: boolean): Triple | null {
  if (Array.isArray(text) && text.length === 3 && text.every((x) => typeof x === "number")) return integer && !text.every(Number.isInteger) ? null : [text[0], text[1], text[2]];
  if (typeof text !== "string") return null;
  const parts = text
    .replace(/[−–]/g, "-")
    .split(/[;\s]+/)
    .filter(Boolean)
    .map((p) => Number(p.replace(",", ".")));
  if (parts.length !== 3 || !parts.every(Number.isFinite)) return null;
  return integer && !parts.every(Number.isInteger) ? null : [parts[0], parts[1], parts[2]];
}

/** The body of the request from the arguments of the command; a refusal in Russian when the block is not given or is given twice. */
export function requestOf(a: Record<string, unknown>): InspectRequest {
  if (typeof a.model_id !== "number") throw refusal("block_model_not_found", "Не задана блочная модель.", "Номер модели — в разделе «Блочные модели» дерева.");
  const given = (k: string) => a[k] !== undefined && a[k] !== null && a[k] !== "";
  const kinds = ["block", "xyz", "row"].filter(given);
  if (kinds.length !== 1) throw refusal("bad_param", "Задайте блок: ячейку «i; j; k» или точку «x; y; z» — что-то одно.", "Например block: «13; 7; 1». В 3D блок задаёт щелчок по нему.");
  const req: InspectRequest = { model_id: a.model_id };
  if (given("block")) {
    const t = parseTriple(a.block, true);
    if (!t || t.some((v) => v < 0)) throw refusal("bad_param", "Ячейка блока — три целых неотрицательных числа i; j; k.", "Индексы с нуля, например 13; 7; 1.");
    req.block = t;
  } else if (given("xyz")) {
    const t = parseTriple(a.xyz, false);
    if (!t) throw refusal("bad_param", "Точка — три числа x; y; z в метрах.", "Например 1040,2; 2066,5; −296,25.");
    req.xyz = t;
  } else {
    if (typeof a.row !== "number" || !Number.isInteger(a.row) || a.row < 0) throw refusal("bad_param", "Номер строки блока — целое число от 0.", "Номер строки даёт щелчок по блоку в 3D.");
    req.row = a.row;
  }
  if (given("params_id")) {
    if (typeof a.params_id !== "number" || !Number.isInteger(a.params_id) || a.params_id < 1) throw refusal("bad_param", "Номер строки «Параметры оценки» — целое число от 1.", "Номера строк — в разделе «Геостатистика → Параметры оценки» дерева.");
    req.params_id = a.params_id;
  }
  return req;
}

/** The answer of the command in words (for the assistant): the block, every row with its status, the value and the probes. */
export function answerText(a: InspectAnswer): string {
  const rows = a.results.map((r) => {
    if (!r.active) return `«${r.name}»: ${r.status_label || "блок вне домена"}`;
    const est = r.values.EST !== undefined ? `, EST ${numText(r.values.EST, 4)}` : "";
    const warn = r.warning ? `; ${r.warning.toLowerCase()}` : "";
    const note = unsolvedNote(r);
    if (note) return `«${r.name}»: EST —, ${note}, кандидатов ${r.counts.candidates}${warn}`;
    return `«${r.name}»: ${r.status_label}${est}, проб в системе ${r.counts.selected} из ${r.counts.candidates}${warn}`;
  });
  return `${blockTitle(a)}, центр ${a.block.centre.map((v) => numText(v, 2)).join("; ")}: ${rows.length ? rows.join(" · ") : "нет строк «Параметры оценки», по которым блок оценён"}`;
}

/** Opens the window (or lights the open one) and asks for the block; the text of the answer. */
export async function openInspector(req: InspectRequest): Promise<string> {
  if (!blockById(req.model_id) && blocksStore.get().loaded) throw refusal("block_model_not_found", `Блочная модель №${req.model_id} не найдена.`, "Номера моделей — в разделе «Блочные модели» дерева.");
  const open = inspectWindow();
  if (open) focusWindow(open);
  else openWindow(INSPECT_WINDOW, INSPECT_TITLE, "normal", {}, ...viewport());
  const a = await inspect(req);
  if (!a) {
    const e = inspectStore.get().error;
    if (e) throw refusal(e.code, e.message, e.hint);
    return "Запрос блока заменён более новым";
  }
  const w = inspectWindow();
  if (w) updateWindow(w, { title: blockTitle(a) });
  return answerText(a);
}

/** A block of this model was clicked and the model has an estimation that was run: the inspector asks for it by its row. */
function onPicked(): void {
  const p = pickedBlockStore.get().picked;
  if (!p || p.status !== "found" || !p.answer || !p.answer.found) return;
  const model = p.model;
  if (!estimationStore.get().rows.some((r) => r.model_id === model && r.last_run !== null)) return;
  void openInspector({ model_id: model, row: p.answer.row }).catch(() => undefined); // a refusal stands in the window
}

let unbind: (() => void)[] = [];

export function bindInspect(app: App): void {
  unbind.forEach((fn) => fn());
  unbind = [];
  app.registry.bind(
    "estimate.inspect",
    noted(app, (a) => openInspector(requestOf(a))),
    needProject,
  );
  let lastPicked: unknown = pickedBlockStore.get().picked;
  let uid: string | null = projectStore.get().current?.uid ?? null;
  let generation = 0;
  let detach: (() => void) | null = null;
  unbind.push(
    pickedBlockStore.subscribe(() => {
      const p = pickedBlockStore.get().picked;
      if (p === lastPicked) return;
      lastPicked = p;
      if (p?.status === "found") onPicked();
    }),
    // what was asked of one project does not belong to another
    projectStore.subscribe(() => {
      const cur = projectStore.get().current?.uid ?? null;
      if (cur === uid) return;
      uid = cur;
      resetInspect();
    }),
    // the rows were run or changed: the same block is asked again while the window is open
    onCoreEvent((event, data) => {
      if (!inspectWindow() || !inspectStore.get().request) return;
      const hit = (event === "geostat.changed" && isEstimationEvent(data)) || (event === "objects.changed" && isBlocksEvent(data));
      if (hit) void reinspect();
    }),
    // a closed window forgets the block: the layers of the 3D go with it
    windowStore.subscribe(() => {
      if (!inspectWindow() && (inspectStore.get().request || inspectStore.get().answer)) resetInspect();
    }),
    // the layers in the main 3D: a lazy chunk with three.js, attached to every new scene
    onScene((scene) => {
      const mine = ++generation;
      detach?.();
      detach = null;
      if (!scene) return;
      void import("./scene").then((m) => {
        if (mine === generation) detach = m.attachInspectScene(scene);
      });
    }),
    () => {
      generation++;
      detach?.();
      detach = null;
    },
  );
}

/** The window for `renderEstimationWindow` (`../bind.ts`); null — not the window of the inspector. */
export function renderInspectWindow(w: FloatWin): ReactNode | null {
  return w.kind === INSPECT_WINDOW ? createElement(InspectWindow, { winId: w.id }) : null;
}
