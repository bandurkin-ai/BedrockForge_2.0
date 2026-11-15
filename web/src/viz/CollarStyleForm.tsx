import { MARKER_TITLES, type MarkerShape } from "../charts/stripLog";
import type { CollarStyle } from "./collarStyle";

const FONTS: [CollarStyle["fontFamily"], string][] = [["sans-serif", "Без засечек"], ["serif", "С засечками"], ["monospace", "Моноширинный"]];

/** Marker and label of the collars of a set: applied at once and remembered in the workspace. */
export function CollarStyleForm({ style, onChange }: { style: CollarStyle; onChange: (next: CollarStyle) => void }) {
  const set = (patch: Partial<CollarStyle>) => onChange({ ...style, ...patch });
  const byAudit = style.color === "audit";
  return (
    <form className="hist-settings downhole-form" onSubmit={(e) => e.preventDefault()}>
      <fieldset>
        <legend>Значок устья</legend>
        <div className="pair">
          <label>Значок
            <select value={style.shape} aria-label="Значок устья" onChange={(e) => set({ shape: e.target.value as MarkerShape })}>
              {(Object.keys(MARKER_TITLES) as MarkerShape[]).map((m) => <option key={m} value={m}>{MARKER_TITLES[m]}</option>)}
            </select>
          </label>
          <label>Размер, px
            <input type="number" min={4} max={40} value={style.size} aria-label="Размер значка устья, px" onChange={(e) => set({ size: Math.max(4, Math.min(40, Number(e.target.value) || 10)) })} />
          </label>
        </div>
        <label className="check"><input type="checkbox" checked={byAudit} onChange={(e) => set({ color: e.target.checked ? "audit" : "#3fa56b" })} /> цвет по результатам аудита</label>
        {!byAudit && <label className="color-pick">Цвет<input type="color" value={style.color} aria-label="Цвет значка устья" onChange={(e) => set({ color: e.target.value })} /></label>}
      </fieldset>
      <fieldset>
        <legend>Подпись (номер скважины)</legend>
        <label className="check"><input type="checkbox" checked={style.label} onChange={(e) => set({ label: e.target.checked })} /> показывать подписи</label>
        <div className="pair">
          <label>Шрифт
            <select value={style.fontFamily} disabled={!style.label} aria-label="Шрифт подписи" onChange={(e) => set({ fontFamily: e.target.value as CollarStyle["fontFamily"] })}>
              {FONTS.map(([f, title]) => <option key={f} value={f}>{title}</option>)}
            </select>
          </label>
          <label>Размер, px
            <input type="number" min={8} max={36} value={style.fontSize} disabled={!style.label} aria-label="Размер шрифта подписи, px" onChange={(e) => set({ fontSize: Math.max(8, Math.min(36, Number(e.target.value) || 12)) })} />
          </label>
        </div>
        <div className="pair">
          <label className="check"><input type="checkbox" checked={style.bold} disabled={!style.label} onChange={(e) => set({ bold: e.target.checked })} /> жирный</label>
          <label className="color-pick">Цвет<input type="color" value={style.labelColor} disabled={!style.label} aria-label="Цвет подписи" onChange={(e) => set({ labelColor: e.target.value })} /></label>
        </div>
      </fieldset>
      <p className="muted small">Применяется сразу и запоминается для набора.</p>
    </form>
  );
}
