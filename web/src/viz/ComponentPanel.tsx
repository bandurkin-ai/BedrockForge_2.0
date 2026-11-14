import type { ValueStats } from "../api";
import { DEFAULT_SCALE, formatValue, type ColorScale, type ScaleSettings } from "./colorScale";

export interface VizSettings extends ScaleSettings {
  /** null = the first component of the project */
  component: string | null;
}

export const DEFAULT_VIZ: VizSettings = { component: null, ...DEFAULT_SCALE };

/** Component choice, colour range and legend shared by the plan, section, log and 3D views. */
export function ComponentPanel({
  components,
  settings,
  component,
  scale,
  stats,
  unit,
  onChange,
}: {
  components: { name: string; unit: string }[];
  settings: VizSettings;
  /** the component actually shown (settings.component or the default) */
  component: string | null;
  scale: ColorScale | null;
  stats: ValueStats | null;
  unit: string | null;
  onChange: (next: VizSettings) => void;
}) {
  if (components.length === 0) {
    return <p className="muted small">Нет компонентов: импортируйте пробы, чтобы раскрасить стволы по содержаниям.</p>;
  }

  const parse = (text: string): number | null => {
    const value = Number(text.replace(",", "."));
    return text.trim() === "" || !Number.isFinite(value) ? null : value;
  };

  return (
    <div className="component-panel">
      <label>
        Компонент{" "}
        <select value={component ?? ""} onChange={(e) => onChange({ ...settings, component: e.target.value })}>
          {components.map((c) => <option key={c.name} value={c.name}>{c.name} ({c.unit})</option>)}
        </select>
      </label>
      <label>
        от{" "}
        <input
          className="range-input"
          inputMode="decimal"
          value={settings.min ?? ""}
          placeholder={scale ? formatValue(scale.min) : "авто"}
          onChange={(e) => onChange({ ...settings, min: parse(e.target.value) })}
          aria-label="Начало шкалы"
        />
      </label>
      <label>
        до{" "}
        <input
          className="range-input"
          inputMode="decimal"
          value={settings.max ?? ""}
          placeholder={scale ? formatValue(scale.max) : "авто"}
          onChange={(e) => onChange({ ...settings, max: parse(e.target.value) })}
          aria-label="Конец шкалы"
        />
      </label>
      <label className="check">
        <input type="checkbox" checked={settings.log} onChange={(e) => onChange({ ...settings, log: e.target.checked })} />
        логарифмическая
      </label>
      {(settings.min !== null || settings.max !== null) && (
        <button onClick={() => onChange({ ...settings, min: null, max: null })} title="Вернуть диапазон 5–95 перцентиль">Авто</button>
      )}
      {scale && (
        <div className="legend" aria-label="Легенда шкалы">
          <div className="legend-bar" style={{ background: scale.gradientCss() }} />
          <div className="legend-ticks">
            {scale.ticks().map((t) => (
              <span key={t.value} style={{ left: `${t.position * 100}%` }}>{formatValue(t.value)}</span>
            ))}
          </div>
          <div className="muted small">
            {unit ?? ""}{stats && stats.count > 0 && <> · значений: {stats.count.toLocaleString("ru-RU")}, макс. {formatValue(stats.max)}</>}
          </div>
        </div>
      )}
    </div>
  );
}
