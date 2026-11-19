/**
 * The fields of the window of an operation, one per type of the schema (`opForms.ts`): a wireframe (a list; an
 * open one where a solid is needed is marked «не замкнут»), several wireframes, lines of layers (the layer
 * checked, its line numbers from 1 or every line), one closed line of a layer, layers, sets of drillholes,
 * numbers (with a comma or a point; the unit after the label), three numbers X; Y; Z, a choice and a flag.
 */
import { toggleId } from "../../shell/props";
import { count } from "../../shell/treeActions";
import css from "../../shell/PropsParts.module.css";
import type { LineFileInfo, LineLayerInfo } from "../scene/objects";
import type { WireframeInfo } from "../wireframes/state";
import { openDetail, wireframeChoices, type FieldValue, type LinePick, type LinesPick, type OpField } from "./opForms";

export interface PickData {
  wireframes: readonly WireframeInfo[];
  files: readonly LineFileInfo[];
  sets: readonly { id: number; name: string; holes: number }[];
}

interface Props {
  field: OpField;
  value: FieldValue;
  data: PickData;
  invalid: boolean;
  onChange: (value: FieldValue) => void;
}

const labelOf = (f: OpField): string => (f.unit ? `${f.label}, ${f.unit}` : f.label);
const testId = (f: OpField): string => `op-field-${f.key}`;

/** «Рудное тело (не замкнут: открытых рёбер — 14)» · «Рельеф (ЦТМ)» */
function wireframeLabel(w: WireframeInfo): string {
  const open = openDetail(w);
  return `${w.name}${w.dtm ? " (ЦТМ)" : ""}${open ? ` (не замкнут: ${open})` : ""}`;
}

/** Layers of lines (not points, not clouds); `closed` — only the layers with a closed line. */
function lineLayers(files: readonly LineFileInfo[], closed = false): LineLayerInfo[] {
  return files.flatMap((f) => f.layers).filter((l) => !l.cloud && !l.points_only && l.line_count > 0 && (!closed || l.closed_count > 0));
}

const layerNote = (l: LineLayerInfo): string => `линий: ${count(l.line_count)}${l.closed_count ? `, замкнутых: ${count(l.closed_count)}` : ""}`;

export function FieldInput({ field: f, value, data, invalid, onChange }: Props) {
  const label = labelOf(f);
  const aria = invalid ? { "aria-invalid": true } : {};
  switch (f.type) {
    case "number":
    case "integer":
    case "vector":
      return (
        <label className={css.formRow} data-testid={testId(f)}>
          <span>
            {label}
            {f.required ? " *" : ""}
          </span>
          <input
            className={css.input}
            inputMode={f.type === "vector" ? "text" : f.type === "integer" ? "numeric" : "decimal"}
            value={typeof value === "string" ? value : ""}
            placeholder={f.type === "vector" ? "X; Y; Z" : f.required ? "" : "не задано"}
            aria-label={label}
            {...aria}
            onChange={(e) => onChange(e.target.value)}
          />
        </label>
      );
    case "bool":
      return (
        <label className={css.check} data-testid={testId(f)}>
          <input type="checkbox" checked={value === true} aria-label={label} onChange={(e) => onChange(e.target.checked)} />
          {label}
        </label>
      );
    case "choice":
      return (
        <label className={css.formRow} data-testid={testId(f)}>
          <span>{label}</span>
          <select className={css.input} value={typeof value === "string" ? value : ""} aria-label={label} onChange={(e) => onChange(e.target.value)}>
            {(f.options ?? []).map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      );
    case "wireframe": {
      const list = wireframeChoices(f, data.wireframes);
      return (
        <label className={css.formRow} data-testid={testId(f)}>
          <span>
            {label}
            {f.required ? " *" : ""}
          </span>
          <select className={css.input} value={typeof value === "number" ? value : 0} aria-label={label} {...aria} onChange={(e) => onChange(Number(e.target.value) || null)}>
            <option value={0}>{list.length ? "— выберите —" : "— каркасов нет —"}</option>
            {list.map((w) => (
              <option key={w.id} value={w.id}>
                {wireframeLabel(w)}
              </option>
            ))}
          </select>
        </label>
      );
    }
    case "wireframes": {
      const list = wireframeChoices(f, data.wireframes);
      const ids = Array.isArray(value) ? (value as number[]) : [];
      return (
        <fieldset className={css.group} data-testid={testId(f)} {...aria}>
          <legend>{label}</legend>
          {!list.length && <p className={css.note}>В проекте нет каркасов.</p>}
          <div className={css.layers}>
            {list.map((w) => (
              <label key={w.id} className={css.check}>
                <input type="checkbox" checked={ids.includes(w.id)} aria-label={`Каркас «${w.name}»`} onChange={(e) => onChange(toggleId(ids, w.id, e.target.checked))} />
                {wireframeLabel(w)}
              </label>
            ))}
          </div>
        </fieldset>
      );
    }
    case "lines": {
      const picks = Array.isArray(value) ? (value as LinesPick[]) : [];
      const layers = lineLayers(data.files);
      const set = (layerId: number, on: boolean) => onChange(on ? [...picks.filter((p) => p.layer_id !== layerId), { layer_id: layerId, text: "" }] : picks.filter((p) => p.layer_id !== layerId));
      const text = (layerId: number, t: string) => onChange(picks.map((p) => (p.layer_id === layerId ? { ...p, text: t } : p)));
      return (
        <fieldset className={css.group} data-testid={testId(f)} {...aria}>
          <legend>{label}</legend>
          {!layers.length && <p className={css.note}>Слоёв линий нет: загрузите или нарисуйте контуры («Главная › Загрузка › Линии», «Линии › Новый слой»).</p>}
          <div className={css.layers}>
            {layers.map((l) => {
              const pick = picks.find((p) => p.layer_id === l.id);
              return (
                <div key={l.id} className={css.row}>
                  <label className={css.check}>
                    <input type="checkbox" checked={!!pick} aria-label={`Слой «${l.name}»`} onChange={(e) => set(l.id, e.target.checked)} />
                    {l.name}
                    <span className={css.note}>{layerNote(l)}</span>
                  </label>
                  {pick && <input className={css.input} value={pick.text} placeholder="все линии слоя" aria-label={`Номера линий слоя «${l.name}»`} onChange={(e) => text(l.id, e.target.value)} />}
                </div>
              );
            })}
          </div>
          <p className={css.note}>Номера линий — с 1, через запятую, диапазон через дефис («1, 3–5»); пусто — все линии слоя. Тай-линии — линии слоя с атрибутом ROLE = TIE.</p>
        </fieldset>
      );
    }
    case "line": {
      const pick = (value ?? { layer_id: null, text: "" }) as LinePick;
      const layers = lineLayers(data.files, true);
      const layer = layers.find((l) => l.id === pick.layer_id);
      return (
        <fieldset className={css.group} data-testid={testId(f)} {...aria}>
          <legend>{label}</legend>
          <label className={css.formRow}>
            <span>Слой с замкнутой линией</span>
            <select className={css.input} value={pick.layer_id ?? 0} aria-label={`${label}: слой`} onChange={(e) => onChange({ layer_id: Number(e.target.value) || null, text: "" })}>
              <option value={0}>{f.required ? "— выберите —" : "— не задан —"}</option>
              {layers.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name} (замкнутых линий: {l.closed_count})
                </option>
              ))}
            </select>
          </label>
          {layer && (
            <label className={css.formRow}>
              <span>Номер линии</span>
              <input className={css.input} inputMode="numeric" value={pick.text} placeholder={layer.closed_count > 1 ? `от 1 до ${layer.line_count}` : "единственная замкнутая"} aria-label={`${label}: номер линии`} onChange={(e) => onChange({ ...pick, text: e.target.value })} />
            </label>
          )}
        </fieldset>
      );
    }
    case "layers":
    case "sets": {
      const ids = Array.isArray(value) ? (value as number[]) : [];
      const rows = f.type === "layers" ? data.files.flatMap((fl) => fl.layers).filter((l) => !l.cloud).map((l) => ({ id: l.id, name: l.name, note: l.points_only ? `точек: ${count(l.point_count)}` : layerNote(l) })) : data.sets.map((s) => ({ id: s.id, name: s.name, note: `${count(s.holes)} скв.` }));
      return (
        <fieldset className={css.group} data-testid={testId(f)}>
          <legend>{label}</legend>
          {!rows.length && <p className={css.note}>{f.type === "layers" ? "Слоёв точек и линий нет." : "Наборов скважин нет."}</p>}
          <div className={css.layers}>
            {rows.map((r) => (
              <label key={r.id} className={css.check}>
                <input type="checkbox" checked={ids.includes(r.id)} aria-label={r.name} onChange={(e) => onChange(toggleId(ids, r.id, e.target.checked))} />
                {r.name}
                <span className={css.note}>{r.note}</span>
              </label>
            ))}
          </div>
        </fieldset>
      );
    }
    case "triangles":
      return <p className={css.note}>«{f.label}» — отмечаются в 3D-окне в режиме правки треугольников.</p>;
  }
}
