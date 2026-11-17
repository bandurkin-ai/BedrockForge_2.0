/**
 * The frame counter and the smoke scenario (ADR 0004 §5, ТЗ 5.3): `diag.fps` (Ctrl+Shift+F) shows the counter;
 * with `smoke` the view turns about its centre for 10 s and the result — frames per second (mean, minimum),
 * ms per frame, triangles and draw calls in a frame, video memory, the class and string of the graphics, the
 * share «упрощено» — goes to the log of the counter, ready for «Скопировать» (the customer's check on the
 * acceptance PC). No three.js: the scene is reached by type.
 */
import { Store } from "../../shell/store";
import type { Scene } from "../../viz/Scene";
import { bytesText } from "../../viz/core/stats";
import { displaysUi } from "../downhole/displaysUi";

export const SMOKE_MS = 10_000;
/** a full turn in 10 s */
const TURN_RAD_PER_MS = (2 * Math.PI) / SMOKE_MS;

export const fpsStore = new Store<{ open: boolean; running: boolean; log: string[] }>({ open: false, running: false, log: [] });

const n1 = (v: number) => (Math.round(v * 10) / 10).toLocaleString("ru-RU");

/** The smoke scenario: turns the view for `ms`, gives the summary line. */
export function runSmoke(scene: Scene, ms = SMOKE_MS): Promise<string> {
  return new Promise((resolve) => {
    scene.fps.reset();
    let start: number | null = null;
    let last: number | null = null;
    let calls = 0;
    let triangles = 0;
    fpsStore.set({ running: true });
    scene.animate((now) => {
      start ??= now;
      if (last !== null) scene.orbit((now - last) * TURN_RAD_PER_MS);
      last = now;
      const s = scene.debug().stats;
      calls = Math.max(calls, s.calls);
      triangles = Math.max(triangles, s.triangles);
      if (now - start < ms) return true;
      const f = scene.fps.summary(now);
      const gpu = scene.gpu;
      const simplified = Math.round(displaysUi.get().simplified * 100);
      const text = [
        `${new Date().toLocaleString("ru-RU")} · вращение ${Math.round(ms / 1000)} с`,
        `кадры/с: среднее ${n1(f.mean)}, минимум ${n1(f.min)}; мс на кадр ${n1(f.msPerFrame)} (${f.frames} кадров)`,
        `в кадре: треугольников ${triangles.toLocaleString("ru-RU")}, вызовов отрисовки ${calls}; видеопамять ≈ ${bytesText(scene.gpuBytes())}`,
        `графика: ${gpu.gpuClass === "software" ? "программная" : gpu.gpuClass === "discrete" ? "дискретная" : "встроенная"} — ${gpu.renderer}; упрощено: ${simplified} %`,
      ].join("\n");
      fpsStore.set((x) => ({ running: false, log: [text, ...x.log].slice(0, 20) }));
      resolve(text);
      return false;
    });
  });
}
