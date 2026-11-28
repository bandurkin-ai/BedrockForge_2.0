import type { TraceStyle } from "../../viz/holes/traceStyle";
import { ColorField } from "../../shell/PropsParts";
import css from "./StyleForms.module.css";

const clamp = (v: string, lo: number, hi: number, fallback: number) => {
  const n = Number(v.replace(",", "."));
  return Number.isFinite(n) && v.trim() !== "" ? Math.max(lo, Math.min(hi, n)) : fallback;
};

/**
 * Hole traces of a set in 3D (the gear of «Стволы»): line width and colour, depth ticks with written
 * depths, marks where the holes cross wireframes and enter or leave block models. Applied at once.
 */
export function TraceStyleForm({ style, onChange, notes }: { style: TraceStyle; onChange: (next: TraceStyle) => void; notes?: string[] }) {
  const set = (patch: Partial<TraceStyle>) => onChange({ ...style, ...patch });
  const byObject = style.wireframeColor === "object";
  return (
    <form className={css.form} onSubmit={(e) => e.preventDefault()}>
      <fieldset>
        <legend>Линия ствола</legend>
        <div className={css.pair}>
          <label>Толщина, px
            <input type="number" min={0.5} max={12} step={0.5} value={style.width} aria-label="Толщина ствола, px"
              onChange={(e) => set({ width: clamp(e.target.value, 0.5, 12, style.width) })} />
          </label>
          <label className={css.check}>Цвет<ColorField value={style.color} label="Цвет ствола" onCommit={(color) => set({ color })} /></label>
        </div>
      </fieldset>

      <fieldset>
        <legend>Глубина скважины</legend>
        <label className={css.check} title="Риска в конце скважины и её глубина в метрах">
          <input type="checkbox" checked={style.endDepth} onChange={(e) => set({ endDepth: e.target.checked })} /> показать глубину
        </label>
      </fieldset>

      <fieldset>
        <legend>Риски глубины</legend>
        <label className={css.check}><input type="checkbox" checked={style.ticks} onChange={(e) => set({ ticks: e.target.checked })} /> показывать риски</label>
        <div className={css.pair}>
          <label>Шаг, м
            <input type="number" min={0.5} max={1000} value={style.tickStep} disabled={!style.ticks} aria-label="Шаг рисок, м"
              onChange={(e) => set({ tickStep: clamp(e.target.value, 0.5, 1000, style.tickStep) })} />
          </label>
          <label>Длина, px
            <input type="number" min={1} max={30} value={style.tickSize} disabled={!style.ticks} aria-label="Длина риски, px"
              onChange={(e) => set({ tickSize: clamp(e.target.value, 1, 30, style.tickSize) })} />
          </label>
        </div>
        <label className={css.check}><input type="checkbox" checked={style.tickLabels} disabled={!style.ticks} onChange={(e) => set({ tickLabels: e.target.checked })} /> подписывать глубину</label>
        <div className={css.pair}>
          <label>Подпись каждые, м
            <input type="number" min={0.5} max={5000} value={style.labelStep} disabled={!style.ticks || !style.tickLabels} aria-label="Шаг подписей глубины, м"
              onChange={(e) => set({ labelStep: clamp(e.target.value, 0.5, 5000, style.labelStep) })} />
          </label>
          <label>Шрифт, px
            <input type="number" min={7} max={30} value={style.labelSize} disabled={!(style.ticks && style.tickLabels) && !style.endDepth} aria-label="Размер подписи глубины, px"
              onChange={(e) => set({ labelSize: clamp(e.target.value, 7, 30, style.labelSize) })} />
          </label>
        </div>
        <label className={css.check}>Цвет подписей<ColorField value={style.labelColor} disabled={!(style.ticks && style.tickLabels) && !style.endDepth} label="Цвет подписей глубины" onCommit={(labelColor) => set({ labelColor })} /></label>
      </fieldset>

      <fieldset>
        <legend>Пересечения</legend>
        <label className={css.check}><input type="checkbox" checked={style.intersections} onChange={(e) => set({ intersections: e.target.checked })} /> отмечать точки пересечения</label>
        <div className={css.pair}>
          <label className={css.check}><input type="checkbox" checked={style.withWireframes} disabled={!style.intersections} onChange={(e) => set({ withWireframes: e.target.checked })} /> с каркасами</label>
          <label className={css.check}><input type="checkbox" checked={style.withBlocks} disabled={!style.intersections} onChange={(e) => set({ withBlocks: e.target.checked })} /> с блочными моделями</label>
        </div>
        <label className={css.check} title="Пересечения с цифровыми моделями рельефа из раздела «ЦТМ». Рельеф, построенный через устья, пересекает каждую скважину на глубине 0 м">
          <input type="checkbox" checked={style.withDtm} disabled={!style.intersections} onChange={(e) => set({ withDtm: e.target.checked })} /> с ЦТМ
        </label>
        <label className={css.check} title="Пересечения только с каркасами и моделями, которые видны в окне">
          <input type="checkbox" checked={style.onlyVisible} disabled={!style.intersections} onChange={(e) => set({ onlyVisible: e.target.checked })} /> только с видимыми объектами
        </label>
        <div className={css.pair}>
          <label>Размер значка, px
            <input type="number" min={3} max={40} value={style.markerSize} disabled={!style.intersections} aria-label="Размер значка пересечения, px"
              onChange={(e) => set({ markerSize: clamp(e.target.value, 3, 40, style.markerSize) })} />
          </label>
          <label className={css.check}><input type="checkbox" checked={style.markerLabels} disabled={!style.intersections} onChange={(e) => set({ markerLabels: e.target.checked })} /> глубина и объект</label>
        </div>
        <label className={css.check}><input type="checkbox" checked={byObject} disabled={!style.intersections} onChange={(e) => set({ wireframeColor: e.target.checked ? "object" : "#ff4d6d" })} /> каркасы — цветом каркаса</label>
        <div className={css.pair}>
          {!byObject && <label className={css.check}>Каркасы<ColorField value={style.wireframeColor} disabled={!style.intersections} label="Цвет пересечений с каркасами" onCommit={(wireframeColor) => set({ wireframeColor })} /></label>}
          <label className={css.check}>Блоки (вход ▲, выход ■)<ColorField value={style.blockColor} disabled={!style.intersections} label="Цвет пересечений с блочными моделями" onCommit={(blockColor) => set({ blockColor })} /></label>
        </div>
        {style.intersections && notes?.map((n) => <p key={n} className={css.muted}>{n}</p>)}
      </fieldset>
      <p className={css.muted}>Применяется сразу и запоминается для набора.</p>
    </form>
  );
}
