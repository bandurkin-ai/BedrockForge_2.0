import type { TraceStyle } from "./traceStyle";

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
    <form className="hist-settings downhole-form" onSubmit={(e) => e.preventDefault()}>
      <fieldset>
        <legend>Линия ствола</legend>
        <div className="pair">
          <label>Толщина, px
            <input type="number" min={0.5} max={12} step={0.5} value={style.width} aria-label="Толщина ствола, px"
              onChange={(e) => set({ width: clamp(e.target.value, 0.5, 12, style.width) })} />
          </label>
          <label className="color-pick">Цвет<input type="color" value={style.color} aria-label="Цвет ствола" onChange={(e) => set({ color: e.target.value })} /></label>
        </div>
      </fieldset>

      <fieldset>
        <legend>Риски глубины</legend>
        <label className="check"><input type="checkbox" checked={style.ticks} onChange={(e) => set({ ticks: e.target.checked })} /> показывать риски</label>
        <div className="pair">
          <label>Шаг, м
            <input type="number" min={0.5} max={1000} value={style.tickStep} disabled={!style.ticks} aria-label="Шаг рисок, м"
              onChange={(e) => set({ tickStep: clamp(e.target.value, 0.5, 1000, style.tickStep) })} />
          </label>
          <label>Длина, px
            <input type="number" min={1} max={30} value={style.tickSize} disabled={!style.ticks} aria-label="Длина риски, px"
              onChange={(e) => set({ tickSize: clamp(e.target.value, 1, 30, style.tickSize) })} />
          </label>
        </div>
        <label className="check"><input type="checkbox" checked={style.tickLabels} disabled={!style.ticks} onChange={(e) => set({ tickLabels: e.target.checked })} /> подписывать глубину</label>
        <div className="pair">
          <label>Подпись каждые, м
            <input type="number" min={0.5} max={5000} value={style.labelStep} disabled={!style.ticks || !style.tickLabels} aria-label="Шаг подписей глубины, м"
              onChange={(e) => set({ labelStep: clamp(e.target.value, 0.5, 5000, style.labelStep) })} />
          </label>
          <label>Шрифт, px
            <input type="number" min={7} max={30} value={style.labelSize} disabled={!style.ticks || !style.tickLabels} aria-label="Размер подписи глубины, px"
              onChange={(e) => set({ labelSize: clamp(e.target.value, 7, 30, style.labelSize) })} />
          </label>
        </div>
        <label className="color-pick">Цвет подписей<input type="color" value={style.labelColor} disabled={!style.ticks || !style.tickLabels} aria-label="Цвет подписей глубины" onChange={(e) => set({ labelColor: e.target.value })} /></label>
      </fieldset>

      <fieldset>
        <legend>Пересечения</legend>
        <label className="check"><input type="checkbox" checked={style.intersections} onChange={(e) => set({ intersections: e.target.checked })} /> отмечать точки пересечения</label>
        <div className="pair">
          <label className="check"><input type="checkbox" checked={style.withWireframes} disabled={!style.intersections} onChange={(e) => set({ withWireframes: e.target.checked })} /> с каркасами</label>
          <label className="check"><input type="checkbox" checked={style.withBlocks} disabled={!style.intersections} onChange={(e) => set({ withBlocks: e.target.checked })} /> с блочными моделями</label>
        </div>
        <label className="check" title="Пересечения только с каркасами и моделями, которые видны в окне">
          <input type="checkbox" checked={style.onlyVisible} disabled={!style.intersections} onChange={(e) => set({ onlyVisible: e.target.checked })} /> только с видимыми объектами
        </label>
        <div className="pair">
          <label>Размер значка, px
            <input type="number" min={3} max={40} value={style.markerSize} disabled={!style.intersections} aria-label="Размер значка пересечения, px"
              onChange={(e) => set({ markerSize: clamp(e.target.value, 3, 40, style.markerSize) })} />
          </label>
          <label className="check"><input type="checkbox" checked={style.markerLabels} disabled={!style.intersections} onChange={(e) => set({ markerLabels: e.target.checked })} /> глубина и объект</label>
        </div>
        <label className="check"><input type="checkbox" checked={byObject} disabled={!style.intersections} onChange={(e) => set({ wireframeColor: e.target.checked ? "object" : "#ff4d6d" })} /> каркасы — цветом каркаса</label>
        <div className="pair">
          {!byObject && <label className="color-pick">Каркасы<input type="color" value={style.wireframeColor} disabled={!style.intersections} aria-label="Цвет пересечений с каркасами" onChange={(e) => set({ wireframeColor: e.target.value })} /></label>}
          <label className="color-pick">Блоки (вход ▲, выход ■)<input type="color" value={style.blockColor} disabled={!style.intersections} aria-label="Цвет пересечений с блочными моделями" onChange={(e) => set({ blockColor: e.target.value })} /></label>
        </div>
        {style.intersections && notes?.map((n) => <p key={n} className="muted small">{n}</p>)}
      </fieldset>
      <p className="muted small">Применяется сразу и запоминается для набора.</p>
    </form>
  );
}
