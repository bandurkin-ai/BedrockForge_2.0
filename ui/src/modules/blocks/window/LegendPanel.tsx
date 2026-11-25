/**
 * The tab «Легенда» of the window «Блочная модель · …» (ТЗ 6.8, 8.7.6; ADR 0023 §11): what the colours of the faces mean,
 * read from the last answer of the core (`header`: the resolved range of the scale, the bins, the labels) and the colours of
 * the style (`legendItems` with `legendStyleOf` — the very texture the layer draws): a scale is a strip with its ends, the
 * breaks are classes with their ranges, the categories are their labels (a hidden one is struck out), the faces with no
 * value have their own grey, never the colour of zero. Without an answer yet there is nothing to show — it says so.
 */
import css from "../../../shell/PropsParts.module.css";
import { num } from "../../../shell/props";
import { legendItems, NO_VALUE_COLOR, type LegendItem } from "../../../viz/blocks/legend";
import { hiddenCodes, legendStyleOf, valueText } from "../display";
import mod from "./Window.module.css";
import { useInfo, useModel, useStyle } from "./hooks";
import { classText, rangeText } from "./style";

const rgb = (c: LegendItem["color"]): string => `rgb(${c[0]},${c[1]},${c[2]})`;

/** «от 0,5 до 1» of a bin; the ends of the first and the last class are open. */
export function binText(i: Pick<LegendItem, "from" | "to">): string {
  if (i.from === null && i.to === null) return "—";
  return classText({ from: i.from, to: i.to });
}

/** The strip of a scale as a CSS gradient: `stops` samples of the 256 buckets. */
export function stripOf(items: readonly LegendItem[], stops = 24): string {
  if (!items.length) return "none";
  const at = (t: number) => items[Math.min(items.length - 1, Math.floor(t * items.length))];
  const list = Array.from({ length: stops }, (_, i) => `${rgb(at((i + 0.5) / stops).color)} ${((i / (stops - 1)) * 100).toFixed(1)}%`);
  return `linear-gradient(to right, ${list.join(", ")})`;
}

export function LegendPanel({ id }: { id: number }) {
  const style = useStyle(id);
  const { model } = useModel(id);
  const info = useInfo(id);
  const h = info?.header;
  const field = model?.fields.find((f) => f.name === style.colour.field);
  if (!h || info?.status === "error") {
    return (
      <div className={mod.panel} data-testid="legend-panel" data-state="none">
        <p className={css.note}>{info?.status === "error" ? "Показ не удался — легенды нет. Причина — на вкладке «Показ»." : style.visible ? "Легенда появится, когда ядро ответит на запрос показа…" : "Легенда появится после «Показать в 3D»: она строится по ответу ядра."}</p>
      </div>
    );
  }
  const kind = h.colour.kind;
  const items = legendItems(h, info?.maxClass ?? -1, legendStyleOf(style.colour));
  const hidden = new Set(hiddenCodes(style.colour));
  return (
    <div className={mod.panel} data-testid="legend-panel" data-state={kind}>
      <fieldset className={css.group}>
        <legend>{h.field ? `Поле ${h.field}${field?.unit ? `, ${field.unit}` : ""}` : "Без раскраски"}</legend>
        {kind === "none" && <p className={css.note}>Раскраски нет: блоки показаны одним цветом. Выберите поле на вкладке «Показ».</p>}
        {kind === "scale" && (
          <div data-testid="legend-scale">
            <div className={mod.strip} style={{ background: stripOf(items), height: 16, width: "100%" }} data-testid="legend-strip" />
            <div className={mod.ends}>
              <span data-testid="legend-from">{valueText(h.colour.from ?? null)}</span>
              <span data-testid="legend-to">{valueText(h.colour.to ?? null)}</span>
            </div>
            <p className={css.note} data-testid="legend-range">
              {rangeText(info) ?? "Границы шкалы не названы."}
            </p>
          </div>
        )}
        {kind === "classes" && (
          <ul className={mod.rows} aria-label="Градации" data-testid="legend-classes">
            {items.map((i) => (
              <li key={i.k} data-testid={`legend-item-${i.k}`}>
                <span className={mod.swatch} style={{ background: rgb(i.color) }} />
                <span>{binText(i)}</span>
              </li>
            ))}
          </ul>
        )}
        {kind === "categories" && (
          <ul className={mod.rows} aria-label="Категории" data-testid="legend-categories">
            {items.map((i) => (
              <li key={i.k} data-testid={`legend-item-${i.k}`} data-hidden={hidden.has(i.k) || undefined}>
                <span className={mod.swatch} style={{ background: rgb(i.color), opacity: hidden.has(i.k) ? 0.35 : 1 }} />
                <span style={hidden.has(i.k) ? { textDecoration: "line-through" } : undefined}>{i.label ?? `код ${i.k}`}</span>
                {hidden.has(i.k) && <span className={css.note}>скрыта</span>}
              </li>
            ))}
          </ul>
        )}
        {kind !== "none" && (
          <div className={mod.nodata} data-testid="legend-nodata">
            <span className={mod.swatch} style={{ background: NO_VALUE_COLOR }} />
            <span>нет значения</span>
          </div>
        )}
      </fieldset>
      <p className={css.note} data-testid="legend-facts">
        Прошло {num(h.passed, 0)} из {num(h.rows, 0)} блоков; граней на экране {num(h.faces, 0)}
        {h.simplified ? ` (упрощено, LOD ${h.lod})` : ""}.
      </p>
    </div>
  );
}
