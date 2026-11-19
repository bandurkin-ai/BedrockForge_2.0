/**
 * The main 3D window (ТЗ 7.13): the panel above it and the scene of the open project. The scene is a lazy
 * chunk (`viz/Scene`, three.js) loaded when a project opens; without a project the brand placeholder stays.
 * Without WebGL2 — the diagnostic window with «Программная отрисовка (медленно)»; a lost context shows
 * «Графика перезапускается…» until the browser gives it back, then the scene is filled again from the core.
 * With BF_E2E=1 the state of the scene is readable as `window.__bfScene` (camera, frame ready, draw calls).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { bridge } from "../../bridge";
import { useApp } from "../../shell/AppContext";
import type { App } from "../../shell/app";
import { useStore } from "../../shell/useStore";
import type { HoleHit, LayerHit, Scene } from "../../viz/Scene";
import { VizError } from "../../viz/error";
import { drawHint } from "../../viz/section/draw";
import { emptyOverview } from "../../viz/section/miniPlan";
import { onCoreEvent, onJobFinished } from "../jobs/state";
import { projectStore } from "../projects/state";
import { SectionMap } from "../section/SectionMap";
import { sectionPoint } from "../section/bind";
import { sectionOf, sectionStore } from "../section/state";
import { tablesStore } from "../tables/state";
import { viewStore } from "./bind";
import { HolesSource } from "./loaders";
import type { ObjectSources } from "./objectSources";
import { sceneObjects } from "./objects";
import { currentScene, NO_OBJECTS, sceneUi, setCurrentScene } from "./sceneHost";
import { displaysUi } from "../downhole/displaysUi";
import { Legend } from "../downhole/Legend";
import { Toolbar } from "./Toolbar";
import { FpsOverlay } from "./FpsOverlay";
import { EditorHud } from "../lines/EditToolbar";
import { AssembleHud } from "../model/AssembleBar";
import { TrianglesHud } from "../model/TrianglesBar";
import logoMark from "../../assets/logo-mark.png";
import shell from "../../shell/Shell.module.css";
import css from "./Scene.module.css";

declare global {
  interface Window {
    __bfScene?: unknown;
  }
}

function flags(): { e2e: boolean; softwareGl: boolean } {
  const b = bridge();
  if (b?.flags) return b.flags();
  return { e2e: typeof location !== "undefined" && new URLSearchParams(location.search).get("e2e") === "1", softwareGl: false };
}

export function SceneView() {
  const uid = useStore(projectStore, (p) => p.current?.uid ?? null);
  if (!uid) {
    return (
      <div className={shell.viewportBrand}>
        <img src={logoMark} alt="" className={shell.viewportMark} />
        <strong>
          Bedrock<span className={shell.forge}>Forge</span>
        </strong>
        Откройте или создайте проект — здесь появится 3D-окно
      </div>
    );
  }
  return <SceneMount key={uid} />;
}

/** Everything that ties the scene to the app: view settings, styles, sets, events, picking. */
function connect(app: App, scene: Scene, source: HolesSource): () => void {
  const off: (() => void)[] = [];
  let view = app.store.get().view;
  scene.setView(view);
  const applySection = () => {
    const s = sectionOf(viewStore.get());
    scene.setSection(s.line, s.clip);
  };
  applySection();
  off.push(
    app.store.subscribe(() => {
      const v = app.store.get().view;
      if (v === view) return;
      const hiddenChanged = v.hidden !== view.hidden;
      view = v;
      scene.setView(v);
      if (hiddenChanged) source.sync();
    }),
  );
  let keys = viewStore.get();
  off.push(
    viewStore.subscribe(() => {
      const k = viewStore.get();
      if (k === keys) return;
      const styles = k.collar_styles !== keys.collar_styles || k.trace_styles !== keys.trace_styles;
      const section = k.section !== keys.section;
      keys = k;
      if (styles) source.restyle();
      if (section) applySection();
    }),
  );
  let sets = tablesStore.get().sets;
  off.push(
    tablesStore.subscribe(() => {
      const s = tablesStore.get().sets;
      if (s === sets) return;
      sets = s;
      source.sync();
      source.reload();
    }),
  );
  off.push(
    onCoreEvent((event, data) => {
      if (event === "objects.changed") source.reload();
      if (event !== "drillholes.changed") return;
      try {
        source.reload((JSON.parse(data) as { set_id: number }).set_id);
      } catch {
        source.reload();
      }
    }),
  );
  off.push(
    onJobFinished((job) => {
      if (job.status === "done" && (job.kind === "import.drillholes" || job.kind === "audit.run")) source.reload();
    }),
  );
  off.push(scene.onRestored(() => source.restore()));
  // the selection made before the scene came up
  const hole = tablesStore.get().hole;
  const setId = tablesStore.get().setId;
  if (hole && setId !== null) scene.select({ setId, name: hole.name });
  source.sync();
  return () => off.forEach((fn) => fn());
}

function SceneMount() {
  const app = useApp();
  const host = useRef<HTMLDivElement>(null);
  const ui = useStore(sceneUi, (s) => s);
  const dh = useStore(displaysUi, (s) => s);
  const draw = useStore(sectionStore, (s) => s.draw);
  const keys = useStore(viewStore, (k) => k);
  const mapOpen = useStore(app.store, (s) => s.workspace.sectionMiniPlan);
  const hasSets = useStore(tablesStore, (s) => s.sets.length > 0);
  const hasObjects = useStore(sceneObjects, (o) => o.files.length > 0 || o.rasters.length > 0 || o.wireframes.length > 0);
  const [ready, setReady] = useState(false);
  const { line, clip } = sectionOf(keys);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    let disposed = false;
    let scene: Scene | null = null;
    let source: HolesSource | null = null;
    let disconnect: (() => void) | null = null;
    sceneUi.set({ lost: false, degraded: false, loading: 0, error: null, noWebgl: null, sets: 0, ...NO_OBJECTS });
    let displays: { idle: boolean; dispose(): void } | null = null;
    let objects: ObjectSources | null = null;
    // the scene, the layer of displays along holes, the lines, rasters and surfaces: lazy chunks with three.js
    void Promise.all([import("../../viz/Scene"), import("../downhole/sceneDisplays"), import("./objectSources")])
      .then(([mod, dh, obj]) => {
        if (disposed) return;
        const probe = mod.probeWebGL2();
        if (!probe.ok) {
          sceneUi.set({ noWebgl: probe });
          return;
        }
        const events = {
          onPick: (hit: HoleHit | null) => {
            if (hit) void app.run("hole.select", { hole_id: hit.name, set_id: hit.setId });
            else if (app.store.get().holeId) void app.run("hole.select", { hole_id: null });
          },
          onPickLayer: (hit: LayerHit | null) => objects?.pick(hit),
          onGroundClick: (p: { x: number; y: number }) => sectionPoint(app, p),
          onContextLost: () => sceneUi.set({ lost: true }),
          onContextRestored: () => sceneUi.set({ lost: false, degraded: scene ? scene.renderer.budgetFactor() < 1 : false }),
        };
        try {
          scene = new mod.Scene(el, events);
        } catch (e) {
          sceneUi.set({ noWebgl: { ok: false, renderer: probe.renderer, reason: e instanceof VizError ? e.full : String(e) } });
          return;
        }
        source = new HolesSource(scene, { sets: () => tablesStore.get().sets, hidden: () => app.store.get().view.hidden, keys: () => viewStore.get() });
        sceneUi.set({ software: scene.gpu.gpuClass === "software" || flags().softwareGl });
        setCurrentScene(scene);
        disconnect = connect(app, scene, source);
        const dhs = new dh.DisplaysSource(scene, app);
        displays = dhs;
        const objs = obj.connectObjects(app, scene, source);
        objects = objs;
        setReady(true);
        if (flags().e2e) {
          const s = scene;
          const src = source;
          const ready = () => s.idle && src.idle && dhs.idle && objs.idle && !s.renderer.lost;
          window.__bfScene = {
            ready,
            // `lines`, `rasters`, `wireframes`: what the layers of Э7 show (layers, parts, levels, backdrops, draped
            // DTMs); `picked` — the click
            state: () => ({ ...s.debug(), ready: ready(), loading: sceneUi.get().loading, displays: displaysUi.get(), ...objs.state() }),
            pixels: () => s.pixels(),
            /** the colour of the frame at a client point, drawn now: [r, g, b, a] as on the screen; null — outside */
            pixel: (x: number, y: number) => {
              s.drawNow();
              const gl = s.canvas.getContext("webgl2");
              const r = s.canvas.getBoundingClientRect();
              const px = Math.floor(((x - r.left) / r.width) * s.canvas.width);
              const py = s.canvas.height - 1 - Math.floor(((y - r.top) / r.height) * s.canvas.height);
              if (!gl || px < 0 || py < 0 || px >= s.canvas.width || py >= s.canvas.height) return null;
              const out = new Uint8Array(4);
              gl.readPixels(px, py, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, out);
              return Array.from(out);
            },
            /** the surface of a wireframe over a plan point as drawn now: level, height, image coordinates */
            probe: (id: number, x: number, y: number) => objs.probe(id, x, y),
            /** every part of the surfaces at this level of detail (null — by the size on the screen) */
            lod: (level: number | null) => objs.forceLevel(level),
            collars: (limit?: number) => s.collarsOnScreen(limit),
            /** a project point on the screen (client px), null — out of the view */
            screen: (x: number, y: number, z: number) => s.screenOf([x, y, z]),
            /** perf3d: `frames` frames turning a full circle, each drawn now and waited for */
            bench: (frames: number) => {
              const ms: number[] = [];
              let calls = 0;
              let triangles = 0;
              let points = 0;
              for (let i = 0; i < frames; i++) {
                s.orbit((2 * Math.PI) / frames);
                ms.push(s.drawNow());
                const st = s.debug().stats;
                calls = Math.max(calls, st.calls);
                triangles = Math.max(triangles, st.triangles);
                points = Math.max(points, st.points);
              }
              ms.sort((a, b) => a - b);
              return { frames, ms_per_frame: ms.reduce((a, b) => a + b, 0) / frames, ms_median: ms[frames >> 1], calls, triangles, points, gpu_bytes: s.gpuBytes(), gpu: s.gpu };
            },
            /** a command of the registry, as the ribbon or Claude runs it */
            run: (id: string, args?: Record<string, unknown>) => app.registry.execute(id, args ?? {}),
          };
        }
      })
      .catch((e: unknown) => sceneUi.set({ error: `3D-окно не загрузилось: ${e instanceof Error ? e.message : String(e)}` }));
    return () => {
      disposed = true;
      disconnect?.();
      objects?.dispose();
      displays?.dispose();
      source?.dispose();
      if (currentScene() === scene) setCurrentScene(null);
      scene?.dispose();
      if (window.__bfScene) delete window.__bfScene;
    };
  }, [app]);

  const showMap = ready && mapOpen && (line !== null || draw.stage !== "off");
  const overview = useMemo(() => (showMap ? currentScene()?.planOverview() ?? emptyOverview() : emptyOverview()), [showMap, ui.content, ui.sets]);
  const hint = drawHint(draw);

  return (
    <div className={css.scene}>
      <Toolbar />
      <div className={css.area}>
        <div ref={host} className={css.gl} data-testid="scene-host" />
        {ui.lost && (
          <div className={css.banner} role="status" data-testid="scene-lost">
            Графика перезапускается…
          </div>
        )}
        {ui.degraded && !ui.lost && <div className={css.corner}>Графика перезапускалась трижды за минуту: нагрузка снижена</div>}
        {hint && <div className={css.hint}>{hint}</div>}
        {ready && <Legend />}
        {ready && <EditorHud />}
        {ready && <AssembleHud />}
        {ready && <TrianglesHud />}
        {ready && <FpsOverlay />}
        {(ui.loading > 0 || dh.loading > 0 || ui.linesLoading > 0 || ui.rastersLoading > 0 || ui.wireframesLoading > 0) && !ui.lost && <div className={css.corner}>Загрузка…</div>}
        {dh.simplified > 0 && ui.loading === 0 && dh.loading === 0 && !ui.lost && (
          <div className={`${css.corner} ${css.second}`} title="Части далеко от камеры показаны объединёнными интервалами до 4 или 32 м">
            Отображения упрощены: {Math.round(dh.simplified * 100)} % частей
          </div>
        )}
        {dh.over && <div className={`${css.corner} ${css.third}`}>Не хватает видеопамяти: часть отображений не показана</div>}
        {ui.linesSimplified > 0 && ui.linesLoading === 0 && !ui.lost && (
          <div className={`${css.corner} ${css.fourth}`} title="Части далеко от камеры показаны упрощёнными линиями: отклонение до 4 или 32 м, меньше пикселя на экране">
            Линии упрощены: {Math.round(ui.linesSimplified * 100)} % частей
          </div>
        )}
        {(ui.linesOver || ui.rastersOver || ui.wireframesOver) && !dh.over && !ui.lost && <div className={`${css.corner} ${css.third}`}>Не хватает видеопамяти: часть линий, растров и поверхностей показана упрощённо</div>}
        {ui.wireframesSimplified && ui.wireframesLoading === 0 && !ui.lost && (
          <div className={`${css.corner} ${css.fifth}`} title="Треугольников в кадре больше бюджета графики: самые мелкие на экране части поверхностей показаны упрощёнными">
            Поверхности упрощены
          </div>
        )}
        {ui.wireframesStandins > 0 && !ui.lost && (
          <div className={`${css.corner} ${css.sixth}`} data-testid="wireframes-standins" title="Ядро строит упрощённые копии поверхностей (задание «Копии каркаса для показа»); до тех пор части показаны целиком">
            Готовятся копии для показа
          </div>
        )}
        {ui.software && ui.loading === 0 && !ui.lost && <div className={css.corner} title="WebGL без видеокарты (SwiftShader): 3D медленнее">Программная отрисовка</div>}
        {ui.error && <div className={`${css.note} ${css.error}`}>{ui.error}</div>}
        {ready && !hasSets && !hasObjects && !ui.error && <div className={css.note}>Нет объектов для показа. Загрузите скважины: «Главная» → «Скважины» или перетащите файлы в окно.</div>}
        {ui.noWebgl && <NoWebgl reason={ui.noWebgl.reason} renderer={ui.noWebgl.renderer} />}
        {showMap && (
          <SectionMap overview={overview} section={line} draft={draw.first} drawing={draw.stage !== "off"} onPoint={(x, y) => sectionPoint(app, { x, y })} onClose={() => void app.run("section.plan", { open: false })} />
        )}
        {ready && (
          <div className={css.coords}>
            {line && clip ? `Разрез: азимут ${Math.round(line.az)}°, окно от ${line.towards} до ${line.away} м · ` : ""}
            ЛКМ — вращение, колесо — масштаб, ПКМ — сдвиг · щелчок по устью — выбор скважины, по линии, поверхности или растру — выбор объекта
          </div>
        )}
      </div>
    </div>
  );
}

/** «Нет WebGL2» (ADR 0004 §4): the graphics, the reason, advice, and the software fallback. */
function NoWebgl({ reason, renderer }: { reason: string; renderer: string }) {
  const b = bridge();
  return (
    <div className={css.diag} role="dialog" aria-modal="false" aria-label="3D недоступно">
      <div className={css.diagBox}>
        <h4>3D-окно недоступно: нет WebGL2</h4>
        <dl>
          <dt>Видеокарта</dt>
          <dd>{renderer}</dd>
          <dt>Причина</dt>
          <dd>{reason}</dd>
        </dl>
        <p>Обновите драйвер видеокарты (сайт производителя ноутбука или Intel / AMD / NVIDIA) и перезапустите программу. Остальные функции работают.</p>
        <div className={css.diagActions}>
          <button type="button" className={`${css.button} ${css.primary}`} disabled={!b?.relaunchSoftwareGl} onClick={() => b?.relaunchSoftwareGl()} title="Перезапуск программы с отрисовкой без видеокарты (SwiftShader)">
            Программная отрисовка (медленно)
          </button>
        </div>
      </div>
    </div>
  );
}
