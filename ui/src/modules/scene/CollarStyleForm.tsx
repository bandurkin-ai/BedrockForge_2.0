import { MARKER_TITLES, type MarkerShape } from "../../charts/striplog/model";
import type { CollarStyle } from "../../viz/holes/collarStyle";
import { ColorField } from "../../shell/PropsParts";
import css from "./StyleForms.module.css";

const FONTS: [CollarStyle["fontFamily"], string][] = [["sans-serif", "Без засечек"], ["serif", "С засечками"], ["monospace", "Моноширинный"]];

/** Marker and label of the collars of a set: applied at once and remembered for the set in the project view settings (ТЗ 7.15). */
export function CollarStyleForm({ style, onChange }: { style: CollarStyle; onChange: (next: CollarStyle) => void }) {
  const set = (patch: Partial<CollarStyle>) => onChange({ ...style, ...patch });
  const byAudit = style.color === "audit";
  return (
    <form className={css.form} onSubmit={(e) => e.preventDefault()}>
      <fieldset>
        <legend>Значок устья</legend>
        <div className={css.pair}>
          <label>Значок
            <select value={style.shape} aria-label="Значок устья" onChange={(e) => set({ shape: e.target.value as MarkerShape })}>
              {(Object.keys(MARKER_TITLES) as MarkerShape[]).map((m) => <option key={m} value={m}>{MARKER_TITLES[m]}</option>)}
            </select>
          </label>
          <label>Размер, px
            <input type="number" min={4} max={40} value={style.size} aria-label="Размер значка устья, px" onChange={(e) => set({ size: Math.max(4, Math.min(40, Number(e.target.value) || 10)) })} />
          </label>
        </div>
        <label className={css.check}><input type="checkbox" checked={byAudit} onChange={(e) => set({ color: e.target.checked ? "audit" : "#3fa56b" })} /> цвет по результатам аудита</label>
        {!byAudit && <label className={css.check}>Цвет<ColorField value={style.color} label="Цвет значка устья" onCommit={(color) => set({ color })} /></label>}
      </fieldset>
      <fieldset>
        <legend>Подпись (номер скважины)</legend>
        <label className={css.check}><input type="checkbox" checked={style.label} onChange={(e) => set({ label: e.target.checked })} /> показывать подписи</label>
        <div className={css.pair}>
          <label>Шрифт
            <select value={style.fontFamily} disabled={!style.label} aria-label="Шрифт подписи" onChange={(e) => set({ fontFamily: e.target.value as CollarStyle["fontFamily"] })}>
              {FONTS.map(([f, title]) => <option key={f} value={f}>{title}</option>)}
            </select>
          </label>
          <label>Размер, px
            <input type="number" min={8} max={36} value={style.fontSize} disabled={!style.label} aria-label="Размер шрифта подписи, px" onChange={(e) => set({ fontSize: Math.max(8, Math.min(36, Number(e.target.value) || 12)) })} />
          </label>
        </div>
        <div className={css.pair}>
          <label className={css.check}><input type="checkbox" checked={style.bold} disabled={!style.label} onChange={(e) => set({ bold: e.target.checked })} /> жирный</label>
          <label className={css.check}>Цвет<ColorField value={style.labelColor} disabled={!style.label} label="Цвет подписи" onCommit={(labelColor) => set({ labelColor })} /></label>
        </div>
      </fieldset>
      <p className={css.muted}>Применяется сразу и запоминается для набора.</p>
    </form>
  );
}
