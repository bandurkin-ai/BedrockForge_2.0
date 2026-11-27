/**
 * The context of the screen a question carries (Э23, 23.15; ТЗ 7.32 «Устройство»; docs/progress/e23-api.md §6): the project, the set and the hole, the panels and the open
 * windows, the view, the section line, the selected object, the state of the audit. The core puts it into the `<workspace>` block of the request.
 *
 * - ≤ 4 KB of JSON for the core (3800 compact bytes here, see `CONTEXT_MAX_BYTES`), every string ≤ 200 characters, `windows` ≤ 20 (a longer context is refused by the core with 422 `bad_param {context}`, so it is cut here: first the windows,
 *   then the panels);
 * - no secrets: the key and the tokens are not in the state of the window; paths of files and the text of the questions are not collected;
 * - a gap stays a gap: no set — `set: null`, a section without a length limit — `section: null` (the contract asks for a number there, and 0 would be a lie), an audit that never ran —
 *   «Аудит не запускался».
 *
 * `buildContext(source)` is pure (tested); `contextSource(app)` reads the stores of the window.
 */
import type { App } from "../../shell/app";
import type { AppState } from "../../shell/store";
import { windowStore } from "../../shell/windows";
import { BOTTOM_TITLES } from "../../shell/workspace";
import { auditStore } from "../audit/state";
import { projectStore } from "../projects/state";
import { viewStore } from "../scene/bind";
import { sectionOf } from "../section/state";
import { tablesStore } from "../tables/state";
import { objectName } from "../wireframes/named";
import type { TurnContext } from "./wireTypes";

/**
 * The contract is 4096 bytes, but the core measures `json.dumps(…, ensure_ascii=False)` WITH spaces after `,` and `:` (≈ 100–150 bytes more than the compact text
 * measured here), so the window keeps to 3800: a context of 4000–4096 compact bytes would be refused by the core at every question (422 `bad_param`).
 */
export const CONTEXT_MAX_BYTES = 3800;
export const STRING_MAX = 200;
export const WINDOWS_MAX = 20;

type ViewMode = TurnContext["view"]["mode"];
const VIEW_MODES: readonly string[] = ["iso", "top", "front", "side", "section"];

export interface AuditBrief {
  error: number;
  warning: number;
  info: number;
  stale: boolean;
}

/** Everything the context is made of; the application fills it from its stores. */
export interface ContextSource {
  state: Pick<AppState, "projectName" | "setName" | "setHoles" | "holeId" | "selected" | "view" | "workspace">;
  projectUid: string | null;
  setId: number | null;
  /** the titles of the open floating windows */
  windows: string[];
  section: { x0: number; y0: number; az: number; length: number | null; towards: number; away: number } | null;
  /** the name of the selected object; null — it cannot be told */
  selectionName: string | null;
  /** null — there is no set, `"none"` — the audit never ran */
  audit: AuditBrief | "none" | null;
  viewMode: ViewMode;
}

const clip = (s: string, max = STRING_MAX): string => (s.length > max ? `${s.slice(0, max - 1)}…` : s);
const finite = (v: number, fallback = 0): number => (Number.isFinite(v) ? v : fallback);

export function panelsOf(w: AppState["workspace"]): string[] {
  return [w.left && "дерево", w.right && (w.rightTab === "assistant" ? "Claude" : "свойства"), w.bottom && `нижняя (${BOTTOM_TITLES[w.bottomTab]})`].filter((p): p is string => typeof p === "string");
}

export function auditText(a: AuditBrief | "none" | null): string | null {
  if (a === null) return null;
  if (a === "none") return "Аудит не запускался";
  return `Аудит: ${a.error} / ${a.warning} / ${a.info}${a.stale ? " (устарел)" : ""}`;
}

const bytes = (c: TurnContext): number => new TextEncoder().encode(JSON.stringify(c)).length;

/** The context of the contract; over the limit — the windows, then the panels are dropped from the end. */
export function buildContext(src: ContextSource): TurnContext {
  const s = src.state;
  const hole = s.holeId !== null && src.setId !== null ? { id: clip(s.holeId), set_id: src.setId } : null;
  const ctx: TurnContext = {
    project: s.projectName !== null ? { uid: clip(src.projectUid ?? ""), name: clip(s.projectName) } : null,
    set: s.setName !== null && src.setId !== null ? { id: src.setId, name: clip(s.setName), holes: Math.max(0, Math.trunc(finite(s.setHoles))) } : null,
    hole,
    panels: panelsOf(s.workspace).map((p) => clip(p)),
    windows: src.windows.slice(0, WINDOWS_MAX).map((w) => clip(w)),
    view: { mode: src.viewMode, perspective: s.view.perspective, vexag: finite(s.view.vexag, 1) },
    section:
      src.section && src.section.length !== null
        ? { x0: finite(src.section.x0), y0: finite(src.section.y0), az: finite(src.section.az), length: finite(src.section.length), towards: finite(src.section.towards), away: finite(src.section.away) }
        : null,
    selection: s.selected ? { kind: clip(s.selected.kind), id: s.selected.id, name: clip(src.selectionName ?? `№${s.selected.id}`) } : null,
    audit: auditText(src.audit),
  };
  while (bytes(ctx) > CONTEXT_MAX_BYTES && ctx.windows.length > 0) ctx.windows.pop();
  while (bytes(ctx) > CONTEXT_MAX_BYTES && ctx.panels.length > 0) ctx.panels.pop();
  return ctx;
}

/** The camera preset is not kept by the scene: the last successful `scene.view` of the log of the commands tells it (by default isometry; a clipped view — section). */
export function viewModeOf(entries: readonly { commandId: string; ok: boolean; args: Record<string, unknown> }[], clipped: boolean): ViewMode {
  const last = entries.find((e) => e.commandId === "scene.view" && e.ok);
  const mode = last && typeof last.args.view === "string" ? last.args.view : null;
  if (mode && VIEW_MODES.includes(mode)) return mode as ViewMode;
  return clipped ? "section" : "iso";
}

/** The state of the window as the source of the context. */
export function contextSource(app: Pick<App, "store" | "registry">): ContextSource {
  const state = app.store.get();
  const setId = tablesStore.get().setId;
  const audit = auditStore.get();
  const line = sectionOf(viewStore.get());
  const free = line.line ?? line.free ?? null;
  return {
    state,
    projectUid: projectStore.get().current?.uid ?? null,
    setId,
    windows: windowStore.get().windows.map((w) => w.title),
    section: free,
    selectionName: state.selected ? objectName(state.selected.kind, state.selected.id) : null,
    audit: state.setName === null || setId === null ? null : audit.setId === setId && audit.result ? { ...audit.result.totals, stale: audit.result.stale } : "none",
    viewMode: viewModeOf(app.registry.entries(), state.view.clip),
  };
}

export const contextOf = (app: Pick<App, "store" | "registry">): TurnContext => buildContext(contextSource(app));
