/**
 * Commands of the section (ТЗ 7.14, ADR 0007 §4): draw by two clicks (S), set by parameters, the window
 * «от / до», clipping (C), profiles ([ ]) exactly one window apart, through a hole, the automatic section
 * across the collars, the mini-plan, reset. The line and the clipping are kept in the project view key
 * `section` (updateView + patchFields); the scene follows at once. A free section (S, `section.set`, through a
 * hole, automatic, profiles, reset) leaves the active series of sections (ADR 0016 §4; the series stays in the
 * project); clipping and the window of the line keep it. The commands of series — `seriesBind.ts`.
 */
import type { App } from "../../shell/app";
import type { CommandContext } from "../../shell/commands";
import { VizError } from "../../viz/error";
import { holeIndex } from "../../viz/holes/manifest";
import { addPoint, DRAW_OFF, startDraw } from "../../viz/section/draw";
import { collarsXY, defaultSection, DEFAULT_WINDOW, lineThroughHole, nextProfile, type SectionLine } from "../../viz/section/math";
import { projectStore } from "../projects/state";
import { updateView, viewStore } from "../scene/bind";
import { currentScene, requireScene } from "../scene/sceneHost";
import { patchFields } from "../scene/viewSync";
import { tablesStore } from "../tables/state";
import { bindSeries } from "./seriesBind";
import { applySectionKey, bindSeriesState, seriesStatus } from "./seriesState";
import { cancelSectionDraw, describeLine, freeLine, sectionOf, sectionStore } from "./state";

const needProject = (ctx: CommandContext) => (ctx.hasProject ? null : "Откройте или создайте проект");
const needLine = (ctx: CommandContext) => needProject(ctx) ?? (ctx.hasSection ? null : "Сначала проведите линию разреза");

const current = () => sectionOf(viewStore.get());
/** The free line, also while a series is active. */
const free = () => freeLine(viewStore.get());

const num = (v: unknown, what: string): number | undefined => {
  if (v === undefined || v === null) return undefined;
  if (typeof v !== "number" || !Number.isFinite(v)) throw new Error(`${what}: нужно число`);
  return v;
};

/** Keeps the section in the project and shows it now; `keepSeries` — the active series stays (clipping, window). */
function save(line: SectionLine | null, clip: boolean, keepSeries = false): string {
  const series = keepSeries ? (current().series ?? null) : null;
  const on = (line !== null || series !== null) && clip;
  updateView("section", patchFields({ line, clip: on, series }));
  applySectionKey(currentScene());
  if (series) return `${seriesStatus() ?? "Разрез серии"}, отсечение ${on ? "вкл" : "выкл"}`;
  return line ? describeLine(line, on) : "Разрез сброшен";
}

/** `]` / `[` without a series: the free line exactly one window on (ADR 0007 §4). */
export function stepProfile(direction: 1 | -1): string {
  const line = free();
  if (!line) throw new Error("Линия разреза не задана: сначала section.set");
  return save(nextProfile(line, direction), current().clip);
}

/** The line through a hole (by `hole_id` or the selected one): through the collar along its azimuth in plan. */
export function holeSection(app: App, a: Record<string, unknown>): { line: SectionLine; name: string } {
  const scene = requireScene();
  const name = typeof a.hole_id === "string" && a.hole_id ? a.hole_id : app.store.get().holeId;
  if (!name) throw new Error("Выберите скважину (щелчок по устью) или укажите hole_id");
  const prefer = typeof a.set_id === "number" ? [a.set_id] : [tablesStore.get().setId, ...scene.debug().sets];
  for (const id of prefer) {
    const layer = id === null ? null : scene.holes(id);
    const i = layer ? holeIndex(layer.manifest, name) : -1;
    if (!layer || i < 0) continue;
    const m = layer.manifest;
    return { line: lineThroughHole({ x: m.collar[i * 3], y: m.collar[i * 3 + 1] }, m.planAz[i], free()), name };
  }
  throw new Error(`Скважина «${name}» не найдена в 3D-окне`);
}

/** A new line (drawn, through a hole, automatic): clip to it and look at its plane, the line from edge to edge. */
function open(line: SectionLine): string {
  const text = save(line, true);
  const scene = currentScene();
  if (scene) {
    try {
      scene.view("section");
    } catch (e) {
      if (!(e instanceof VizError)) throw e;
    }
  }
  return text;
}


/** A click while drawing — in the view or on the mini-plan (project coordinates). */
export function sectionPoint(app: App, p: { x: number; y: number }): void {
  const r = addPoint(sectionStore.get().draw, p, free());
  sectionStore.set({ draw: r.state });
  currentScene()?.setDrawing(r.state);
  if (r.line) app.store.set({ statusNote: open(r.line) });
}

export function bindSection(app: App): void {
  const r = app.registry;
  bindSeriesState(app);
  bindSeries(app);
  const sync = () => {
    const k = current();
    const has = k.line !== null || k.series !== undefined;
    if (app.store.get().hasSection !== has) app.store.set({ hasSection: has });
  };
  viewStore.subscribe(sync);
  projectStore.subscribe(() => {
    if (!projectStore.get().current && sectionStore.get().draw.stage !== "off") sectionStore.set({ draw: DRAW_OFF });
  });

  r.bind(
    "section.draw",
    () => {
      requireScene();
      if (sectionStore.get().draw.stage !== "off") {
        cancelSectionDraw();
        return "Рисование разреза отменено";
      }
      const draw = startDraw();
      sectionStore.set({ draw });
      currentScene()?.setDrawing(draw);
      return "Укажите начало и конец линии разреза";
    },
    needProject,
  );

  r.bind(
    "section.set",
    (a) => {
      const base = { line: free(), clip: current().clip };
      const az = num(a.az, "Азимут")!;
      const length = num(a.length, "Длина");
      if (length !== undefined && !(length > 0)) throw new Error("Длина линии разреза должна быть больше нуля (или не задана — без ограничения)");
      const towards = num(a.towards, "Окно к наблюдателю") ?? base.line?.towards ?? DEFAULT_WINDOW;
      const away = num(a.away, "Окно от наблюдателя") ?? base.line?.away ?? DEFAULT_WINDOW;
      if (towards < 0 || away < 0) throw new Error("Окно разреза не может быть отрицательным");
      const line: SectionLine = { x0: num(a.x0, "X начала")!, y0: num(a.y0, "Y начала")!, az: ((az % 360) + 360) % 360, length: length ?? null, towards, away };
      return save(line, base.clip);
    },
    needProject,
  );

  r.bind(
    "section.window",
    (a) => {
      const line = free();
      const { clip } = current();
      if (!line) throw new Error("Линия разреза не задана: сначала section.set");
      const towards = num(a.towards, "От") ?? line.towards;
      const away = num(a.away, "До") ?? line.away;
      if (towards < 0 || away < 0) throw new Error("Окно разреза не может быть отрицательным");
      return save({ ...line, towards, away }, typeof a.clip === "boolean" ? a.clip : clip, true);
    },
    needLine,
  );

  r.bind(
    "section.clip",
    (a) => {
      const { line, clip, series } = current();
      if (!line && !series) throw new Error("Линия разреза не задана: сначала section.set");
      return save(free(), typeof a.on === "boolean" ? a.on : !clip, true);
    },
    needLine,
  );

  r.bind(
    "section.step",
    (a) => {
      return stepProfile(Number(a.direction) < 0 ? -1 : 1);
    },
    needLine,
  );

  r.bind(
    "section.clear",
    () => {
      cancelSectionDraw();
      return save(null, false);
    },
    needProject,
  );

  r.bind("section.through_hole", (a) => open(holeSection(app, a).line), needProject);

  r.bind(
    "section.auto",
    () => {
      const scene = requireScene();
      const points: [number, number][] = [];
      for (const id of scene.debug().sets) {
        const layer = scene.holes(id);
        if (layer?.visible) points.push(...collarsXY(layer.manifest.collar));
      }
      if (!points.length) throw new Error("Нет устьев для авто-разреза: покажите набор скважин");
      const base = free();
      return open(defaultSection(points, base?.towards, base?.away));
    },
    needProject,
  );

  r.bind("section.plan", (a) => {
    const w = app.store.get().workspace;
    const on = typeof a.open === "boolean" ? a.open : !w.sectionMiniPlan;
    app.store.set((s) => ({ workspace: { ...s.workspace, sectionMiniPlan: on } }));
    return on ? "Мини-план показан" : "Мини-план скрыт";
  });
}
