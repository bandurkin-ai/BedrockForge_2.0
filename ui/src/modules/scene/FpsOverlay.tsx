/**
 * The frame counter over the 3D view (ADR 0004 §5, `diag.fps`, Ctrl+Shift+F): frames per second (now,
 * minimum, mean), ms per frame, triangles and draw calls in a frame, video memory (estimate and budget), the
 * class and string of the graphics, the share «упрощено»; the smoke scenario and its log with «Скопировать».
 */
import { useEffect, useState } from "react";
import { bridge } from "../../bridge";
import { useApp } from "../../shell/AppContext";
import { Icon } from "../../shell/Icon";
import { useStore } from "../../shell/useStore";
import { MEMORY_BUDGET, TRIANGLE_BUDGET } from "../../viz/core/budget";
import { bytesText } from "../../viz/core/stats";
import { displaysUi } from "../downhole/displaysUi";
import { fpsStore } from "./fps";
import { currentScene } from "./sceneHost";
import css from "./Scene.module.css";

const CLASS_TITLES = { integrated: "встроенная", discrete: "дискретная", software: "программная" } as const;
const n1 = (v: number) => (Math.round(v * 10) / 10).toLocaleString("ru-RU");

interface Reading {
  current: number;
  min: number;
  mean: number;
  ms: number;
  triangles: number;
  calls: number;
  bytes: number;
}

export function FpsOverlay() {
  const app = useApp();
  const { open, running, log } = useStore(fpsStore, (s) => s);
  const simplified = useStore(displaysUi, (s) => s.simplified);
  const [r, setR] = useState<Reading | null>(null);
  useEffect(() => {
    if (!open) return;
    const tick = () => {
      const s = currentScene();
      if (!s) return setR(null);
      const f = s.fps.summary(performance.now());
      const st = s.debug().stats;
      setR({ current: f.current, min: f.min, mean: f.mean, ms: f.msPerFrame, triangles: st.triangles, calls: st.calls, bytes: s.gpuBytes() });
    };
    currentScene()?.fps.reset();
    tick();
    const t = setInterval(tick, 500);
    return () => clearInterval(t);
  }, [open]);
  if (!open) return null;
  const s = currentScene();
  const gpu = s?.gpu;
  const factor = s?.renderer.budgetFactor() ?? 1;
  return (
    <div className={css.fps} role="status" aria-label="Счётчик кадров" data-testid="fps-overlay">
      <div className={css.mapHead}>
        <span>Счётчик кадров (Ctrl+Shift+F)</span>
        <button type="button" aria-label="Скрыть счётчик" title="Скрыть" onClick={() => void app.run("diag.fps", { open: false })}>
          <Icon name="close" size={12} />
        </button>
      </div>
      {r && (
        <dl>
          <dt>Кадры/с</dt>
          <dd>
            {n1(r.current)} · мин {n1(r.min)} · сред {n1(r.mean)}
          </dd>
          <dt>Мс на кадр</dt>
          <dd>{n1(r.ms)}</dd>
          <dt>В кадре</dt>
          <dd>
            {r.triangles.toLocaleString("ru-RU")} треуг. · {r.calls} вызовов
          </dd>
          <dt>Видеопамять</dt>
          <dd>
            ≈ {bytesText(r.bytes)} из {gpu ? bytesText(MEMORY_BUDGET[gpu.gpuClass] * factor) : "—"}
          </dd>
          <dt>Бюджет треуг.</dt>
          <dd>{gpu ? (TRIANGLE_BUDGET[gpu.gpuClass] * factor).toLocaleString("ru-RU") : "—"}{factor < 1 ? " (снижен после потерь графики)" : ""}</dd>
          <dt>Графика</dt>
          <dd title={gpu?.renderer}>{gpu ? `${CLASS_TITLES[gpu.gpuClass]} — ${gpu.renderer}` : "—"}</dd>
          <dt>Упрощено</dt>
          <dd>{Math.round(simplified * 100)} % частей</dd>
        </dl>
      )}
      <div className={css.fpsActions}>
        <button type="button" className={css.button} disabled={running} onClick={() => void app.run("diag.fps", { smoke: true })}>
          {running ? "Идёт вращение 10 с…" : "Дымовой сценарий (10 с)"}
        </button>
        <button type="button" className={css.button} disabled={!log.length} onClick={() => bridge()?.copyText(log.join("\n\n"))}>
          Скопировать
        </button>
      </div>
      {log.length > 0 && <pre className={css.fpsLog} data-testid="fps-log">{log.join("\n\n")}</pre>}
    </div>
  );
}
