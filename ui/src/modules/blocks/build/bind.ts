/**
 * The handlers of `blocks.prototype`, `blocks.fill`, `blocks.code`, `blocks.regularize`, `blocks.rebuild` and the windows
 * of the building of a block model (ТЗ 8.7.3, 8.7.5; ADR 0023 §6, §9, §13). Without arguments a command opens its window
 * (the model chosen in the tree, else the first suitable one); with them — `run.ts` checks the arguments, starts the job
 * and follows it. The window shows the same run (the stages, the result, the refusal in its place of the form). The
 * preview of the prototype follows the form (`requestPreview`: the core is asked, an answer that was overtaken is
 * dropped) and its frame is drawn in the main 3D by a lazy chunk (`frame.ts`). One window of a kind per project; another
 * project — clean forms. A model that is gone leaves the forms.
 */
import { createElement, type ReactNode } from "react";
import type { App } from "../../../shell/app";
import { needWritable, noted, viewport } from "../../../shell/objectRun";
import { focusWindow, openWindow, windowStore, type FloatWin } from "../../../shell/windows";
import { projectStore } from "../../projects/state";
import { onScene } from "../../scene/sceneHost";
import type { BlockModelInfo } from "../api";
import { blockById, blocksStore, loadBlockDetails } from "../state";
import { CodeWindow } from "./CodeWindow";
import { previewPrototype, refusalOf } from "./api";
import { argsOfPrototype, bodyOfPrototype, previewBody } from "./forms";
import { FillWindow } from "./FillWindow";
import { PrototypeWindow } from "./PrototypeWindow";
import { RegularizeWindow } from "./RegularizeWindow";
import { numberText } from "../report/state";
import { runCode, runFill, runPrototype, runRebuild, runRegularize } from "./run";
import { buildStore, form, OP_WINDOWS, prototypes, resetBuild, setForm, setPreview, withBlocks, type OpKind } from "./state";

export const TITLES: Record<OpKind, string> = { prototype: "Прототип модели", fill: "Заполнить каркасами", code: "Кодировать каркасами", regularize: "Регуляризация" };

/** The window of a kind, if open. */
export const windowOfKind = (kind: OpKind): string | null => windowStore.get().windows.find((w) => w.kind === OP_WINDOWS[kind])?.id ?? null;

/** The model of the tree that is selected, if it is one. */
const selectedModel = (app: App): BlockModelInfo | undefined => {
  const sel = app.store.get().selected;
  return sel?.kind === "blocks" ? blockById(sel.id) : undefined;
};

/** The fields of a model with their details when read (labels, statistics), else from the list. */
export const modelFieldsOf = (id: number | null): BlockModelInfo | null => (id === null ? null : (blocksStore.get().details[id] ?? blockById(id) ?? null));

// ---- the models the forms choose ----

export function choosePrototype(id: number | null): void {
  setForm("fill", { prototypeId: id });
}

export function chooseCodeModel(id: number | null): void {
  setForm("code", { modelId: id });
}

/** A model chosen for the regularization: its fields are read, the size of the new block is the double of the old one. */
export function chooseRegModel(id: number | null): void {
  const m = id === null ? undefined : blockById(id);
  const double = m && !m.grid.variable && m.grid.size ? { size: m.grid.size.map((v) => numberText(v * 2)) } : {};
  setForm("regularize", { modelId: id, grades: [], gradesAll: true, categories: [], catMode: "all", densityField: "", ...double });
  if (id !== null) void loadBlockDetails(id);
}

/** The prototype of a window: the one selected in the tree, else the one the form has, else the only one of the project. */
function defaultPrototype(app: App): number | null {
  const sel = selectedModel(app);
  const list = prototypes();
  if (sel && list.some((m) => m.id === sel.id)) return sel.id;
  const have = form("fill").prototypeId;
  if (have !== null && list.some((m) => m.id === have)) return have;
  return list.length === 1 ? list[0].id : null;
}

function defaultModel(app: App, have: number | null): number | null {
  const sel = selectedModel(app);
  const list = withBlocks();
  if (sel && list.some((m) => m.id === sel.id)) return sel.id;
  if (have !== null && list.some((m) => m.id === have)) return have;
  return list.length === 1 ? list[0].id : null;
}

export function openBuildWindow(app: App, kind: OpKind): string {
  const open = windowOfKind(kind);
  if (open) {
    focusWindow(open);
    return open;
  }
  if (kind === "fill") choosePrototype(defaultPrototype(app));
  else if (kind === "code") chooseCodeModel(defaultModel(app, form("code").modelId));
  else if (kind === "regularize") {
    const id = defaultModel(app, form("regularize").modelId);
    if (id !== form("regularize").modelId) chooseRegModel(id);
  }
  return openWindow(OP_WINDOWS[kind], TITLES[kind], "normal", {}, ...viewport());
}

// ---- the preview of the prototype ----

let previewTimer: ReturnType<typeof setTimeout> | null = null;

/** The core is asked for the grid of the form (after a short pause of the typing); an answer that was overtaken is dropped. */
export function requestPreview(delay = 250): void {
  if (previewTimer) clearTimeout(previewTimer);
  previewTimer = null;
  const got = bodyOfPrototype(argsOfPrototype(form("prototype")));
  if (!got.ok) {
    setPreview({ phase: "idle", key: "", data: null, error: null, seq: buildStore.get().preview.seq + 1 });
    return;
  }
  const body = previewBody(got.body);
  const key = JSON.stringify(body);
  const cur = buildStore.get().preview;
  if (key === cur.key && cur.phase !== "idle" && cur.phase !== "error") return;
  const seq = cur.seq + 1;
  setPreview({ phase: "loading", key, seq, data: cur.key === key ? cur.data : null, error: null });
  const uid = projectStore.get().current?.uid ?? null;
  previewTimer = setTimeout(() => {
    previewTimer = null;
    previewPrototype(body)
      .then((data) => {
        if (buildStore.get().preview.seq === seq && (projectStore.get().current?.uid ?? null) === uid) setPreview({ phase: "ok", data, error: null });
      })
      .catch((e: unknown) => {
        if (buildStore.get().preview.seq === seq) setPreview({ phase: "error", data: null, error: refusalOf(e) });
      });
  }, delay);
}

// ---- the registry ----

export function bindBuild(app: App): void {
  const reg = (id: string, run: (a: Record<string, unknown>, source: "user" | "claude") => Promise<string>) => app.registry.bind(id, noted(app, (a, s) => run(a, s)), needWritable(app));
  reg("blocks.prototype", (a, s) => runPrototype(app, a, s));
  reg("blocks.fill", (a, s) => runFill(app, a, s));
  reg("blocks.code", (a, s) => runCode(app, a, s));
  reg("blocks.regularize", (a, s) => runRegularize(app, a, s));
  reg("blocks.rebuild", (a, s) => runRebuild(app, a, s));
  // what was asked of one project does not belong to another
  let uid: string | null = projectStore.get().current?.uid ?? null;
  projectStore.subscribe(() => {
    const cur = projectStore.get().current?.uid ?? null;
    if (cur === uid) return;
    uid = cur;
    resetBuild();
  });
  // a model that is gone leaves the forms (the result of the run stays until the next run)
  blocksStore.subscribe(() => {
    if (!blocksStore.get().loaded) return;
    const gone = (id: number | null) => id !== null && !blockById(id);
    if (gone(form("fill").prototypeId)) setForm("fill", { prototypeId: null });
    if (gone(form("code").modelId)) setForm("code", { modelId: null });
    if (gone(form("regularize").modelId)) setForm("regularize", { modelId: null });
  });
  // a closed window of the prototype leaves no preview: the frame goes from the 3D with it
  windowStore.subscribe(() => {
    if (!windowOfKind("prototype") && buildStore.get().preview.phase !== "idle") setPreview({ phase: "idle", key: "", data: null, error: null, seq: buildStore.get().preview.seq + 1 });
  });
  // the frame of the prototype in the main 3D: a lazy chunk with three.js, attached to every new scene
  let detach: (() => void) | null = null;
  let generation = 0;
  onScene((scene) => {
    const mine = ++generation;
    detach?.();
    detach = null;
    if (!scene) return;
    void import("./frame").then((m) => {
      if (mine === generation) detach = m.attachFrameScene(scene);
    });
  });
}

/** The windows for the shell's window layer (`shell/Shell.tsx`); null — not a window of the building. */
export function renderBuildWindow(w: FloatWin): ReactNode | null {
  switch (w.kind) {
    case OP_WINDOWS.prototype:
      return createElement(PrototypeWindow, { winId: w.id });
    case OP_WINDOWS.fill:
      return createElement(FillWindow, { winId: w.id });
    case OP_WINDOWS.code:
      return createElement(CodeWindow, { winId: w.id });
    case OP_WINDOWS.regularize:
      return createElement(RegularizeWindow, { winId: w.id });
    default:
      return null;
  }
}
