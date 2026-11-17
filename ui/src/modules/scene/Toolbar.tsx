/**
 * The panel above the 3D window (ТЗ 7.13; BF SceneView toolbar): views, «Вписать», sections, clipping,
 * profiles, the plane of the section, the mini-plan, reset; «от / до» and the azimuth of the line; the
 * vertical exaggeration, the opacity of wireframes, perspective and the grid; «Данные по скважинам».
 * Every button is a command of the registry (ribbon, Ctrl+K, keys and Claude run the same).
 */
import { useEffect, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { Icon, type IconName } from "../../shell/Icon";
import { useStore } from "../../shell/useStore";
import { VEXAG_STEPS } from "../../shell/workspace";
import { DEFAULT_WINDOW } from "../../viz/section/math";
import { sectionOf, sectionStore } from "../section/state";
import { viewStore } from "./bind";
import css from "./Scene.module.css";

function ToolButton({ icon, label, hint, keys, active, disabled, off, onClick }: {
  icon: IconName;
  label: string;
  hint?: string;
  keys?: string;
  active?: boolean;
  disabled?: boolean;
  /** why the button is off */
  off?: string;
  onClick: () => void;
}) {
  const title = `${label}${hint ? ` — ${hint}` : ""}${keys ? ` (${keys})` : ""}${disabled && off ? `. ${off}` : ""}`;
  return (
    <button type="button" className={css.tool} title={title} aria-label={label} aria-pressed={active === undefined ? undefined : active} disabled={disabled} onClick={onClick}>
      <Icon name={icon} size={24} />
    </button>
  );
}

/** A number field written on Enter or when it loses focus (one command, not one per key). */
function NumberField({ label, title, value, step, onCommit }: { label: string; title: string; value: number; step: number; onCommit: (v: number) => void }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(Math.round(value * 10) / 10)), [value]);
  const commit = () => {
    const v = Number(text.replace(",", "."));
    if (Number.isFinite(v) && v !== value) onCommit(v);
    else setText(String(Math.round(value * 10) / 10));
  };
  return (
    <label className={css.field} title={title}>
      {label}
      <input type="number" step={step} value={text} aria-label={title} onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} />
    </label>
  );
}

export function Toolbar() {
  const app = useApp();
  const view = useStore(app.store, (s) => s.view);
  const holeId = useStore(app.store, (s) => s.holeId);
  const hasSet = useStore(app.store, (s) => s.setName !== null);
  const mapOpen = useStore(app.store, (s) => s.workspace.sectionMiniPlan);
  const keys = useStore(viewStore, (k) => k);
  const drawing = useStore(sectionStore, (s) => s.draw.stage !== "off");
  const { line } = sectionOf(keys);
  const [opacity, setOpacity] = useState(view.wireframeOpacity);
  useEffect(() => setOpacity(view.wireframeOpacity), [view.wireframeOpacity]);
  const run = (id: string, args: Record<string, unknown> = {}) => void app.run(id, args);
  const commitOpacity = () => opacity !== view.wireframeOpacity && run("scene.style", { wireframe_opacity: opacity });

  return (
    <div className={css.toolbar} role="toolbar" aria-label="Панель 3D-окна">
      <div className={css.group} role="group" aria-label="Повернуть вид">
        <ToolButton icon="cubeTop" label="В план" hint="Вид сверху (Y вверх, X вправо) на то, что сейчас на экране" keys="T" onClick={() => run("scene.view", { view: "top" })} />
        <ToolButton icon="cubeLeft" label="Сбоку Z/X" hint="Вид сбоку на север: Z вверх, X вправо" keys="X" onClick={() => run("scene.view", { view: "front" })} />
        <ToolButton icon="cubeRight" label="Сбоку Z/Y" hint="Вид сбоку на запад: Z вверх, Y вправо" keys="Y" onClick={() => run("scene.view", { view: "side" })} />
        <ToolButton icon="maximize" label="Вписать" hint="Показать всё видимое, не поворачивая вид" keys="F" onClick={() => run("scene.fit")} />
      </div>
      <span className={css.sep} />
      <div className={css.group} role="group" aria-label="Разрез">
        <ToolButton icon="vwSection" label="Разрез" hint="Линия разреза: два щелчка — начало и конец, в текущем виде или на плане" keys="S" active={drawing} onClick={() => run("section.draw")} />
        <ToolButton icon="vwHole" label="Разрез через скважину" hint="Через устье по азимуту скважины: вся скважина в плоскости разреза" disabled={!holeId} off="Выберите скважину (щелчок по устью)" onClick={() => run("section.through_hole")} />
        {!line && <ToolButton icon="vwAuto" label="Авто-разрез" hint="Поперёк простирания устьев, через их центр" onClick={() => run("section.auto")} />}
        {line && (
          <>
            <ToolButton icon="vwClip" label="Отсечение" hint="Отсекать всё за пределами окна разреза" keys="C" active={view.clip} onClick={() => run("section.clip")} />
            <ToolButton icon="vwPrev" label="Предыдущий профиль" hint="Сдвиг на ширину окна к наблюдателю" keys="[" onClick={() => run("section.step", { direction: -1 })} />
            <ToolButton icon="vwNext" label="Следующий профиль" hint="Сдвиг на ширину окна от наблюдателя" keys="]" onClick={() => run("section.step", { direction: 1 })} />
            <ToolButton icon="vwToPlane" label="В плоскость разреза" hint="Z вверх, линия слева направо" onClick={() => run("scene.view", { view: "section" })} />
            <ToolButton icon="vwMap" label="План" hint="Мини-план: данные, линия разреза и окно" active={mapOpen} onClick={() => run("section.plan")} />
            <ToolButton icon="vwReset" label="Сбросить разрез" hint="Убрать линию разреза и отсечение" onClick={() => run("section.clear")} />
          </>
        )}
      </div>
      {line && (
        <>
          <NumberField label="От" title="Окно разреза к наблюдателю, м" value={line.towards ?? DEFAULT_WINDOW} step={5} onCommit={(v) => run("section.window", { towards: Math.max(0, v) })} />
          <NumberField label="До" title="Окно разреза от наблюдателя, м" value={line.away ?? DEFAULT_WINDOW} step={5} onCommit={(v) => run("section.window", { away: Math.max(0, v) })} />
          <NumberField label="Аз." title="Азимут линии разреза, градусы" value={line.az} step={5} onCommit={(v) => run("section.set", { x0: line.x0, y0: line.y0, az: v, ...(line.length !== null ? { length: line.length } : {}) })} />
        </>
      )}
      <span className={css.sep} />
      <div className={css.group} role="group" aria-label="Вертикальное преувеличение" title="Вертикальное преувеличение">
        <Icon name="vwVexag" size={16} />
        {VEXAG_STEPS.map((v) => (
          <button key={v} type="button" className={`${css.tool} ${css.step}`} aria-pressed={view.vexag === v} aria-label={`Преувеличение ×${v}`} title={`Вертикальное преувеличение ×${v}`} onClick={() => view.vexag !== v && run("scene.style", { vexag: v })}>
            ×{v}
          </button>
        ))}
      </div>
      <label className={css.field} title="Непрозрачность каркасов">
        <Icon name="vwOpacity" size={16} />
        <input type="range" min={0.1} max={1} step={0.05} value={opacity} aria-label="Непрозрачность каркасов" onChange={(e) => setOpacity(Number(e.target.value))} onPointerUp={commitOpacity} onKeyUp={commitOpacity} onBlur={commitOpacity} />
      </label>
      <div className={css.group} role="group" aria-label="Отображение">
        <ToolButton icon="vwPerspective" label="Перспектива" hint="Включено — перспектива; выключено — ортогональная проекция, как на планах и разрезах" active={view.perspective} onClick={() => run("scene.style", { perspective: !view.perspective })} />
        <ToolButton icon="vwGrid" label="Сетка" hint="Координатная сетка с подписями" active={view.grid} onClick={() => run("scene.style", { grid: !view.grid })} />
      </div>
      <span className={css.sep} />
      <ToolButton icon="striplog" label="Данные по скважинам" hint="Колонки данных вдоль выбранных скважин" disabled={!hasSet} off="Нет активного набора скважин" onClick={() => run("holes.data_window")} />
    </div>
  );
}
