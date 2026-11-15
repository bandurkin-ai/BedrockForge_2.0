import { useMemo, useState } from "react";
import type { Hole } from "../../../api";
import type { StripLogSettings } from "../../../charts/stripLog";
import type { LayerCtx } from "../context";
import { NumField, parseNum, Row, Section, showNum } from "../ui";

/** The table and field of the layer, and the holes and depths of the whole display. */
export function DataSection({ ctx, settings, holes, onBind, onSettings, single }: {
  ctx: LayerCtx;
  settings: StripLogSettings;
  holes: Hole[];
  /** another table or field for the layer */
  onBind: (table: string, field?: string) => void;
  onSettings: (p: Partial<StripLogSettings>, key?: string) => void;
  /** one layer is selected (the binding can be changed) */
  single: boolean;
}) {
  const { track, table, column, tables } = ctx;
  const [query, setQuery] = useState("");
  const chosen = useMemo(() => new Set(settings.holes), [settings.holes]);
  const shown = useMemo(() => {
    const q = query.trim().toUpperCase();
    return holes.filter((h) => !q || h.hole_id.toUpperCase().includes(q)).slice(0, 300);
  }, [holes, query]);
  const all = settings.allHoles !== false;
  const depth = settings.from || settings.to ? ` · ${settings.from || "0"}–${settings.to || "забой"} м` : "";
  const summary = `${table?.name ?? "—"} · ${track.field}${column?.unit ? `, ${column.unit}` : ""}${all ? "" : ` · ${settings.holes.length} скв.`}${depth}`;
  return (
    <Section id="data" title="Данные" summary={summary} open>
      {single && (
        <>
          <Row label="Таблица" title="Таблица набора, из которой берётся поле слоя">
            <select value={track.table} aria-label="Таблица" onChange={(e) => onBind(e.target.value)}>
              {!table && <option value={track.table}>(таблица удалена)</option>}
              {tables.filter((t) => t.columns.length > 0).map((t) => <option key={t.key} value={t.key}>{t.name} · {t.row_count.toLocaleString("ru-RU")}</option>)}
            </select>
          </Row>
          <Row label="Поле" title="Поле таблицы. При смене поля вид и стиль слоя сохраняются">
            <select value={track.field} aria-label="Поле" onChange={(e) => onBind(track.table, e.target.value)}>
              {!column && <option value={track.field}>{track.field} (нет в таблице)</option>}
              {(table?.columns ?? []).map((c) => <option key={c.name} value={c.name}>{c.name}{c.unit ? `, ${c.unit}` : ""}{c.kind === "text" ? " (текст)" : ""}</option>)}
            </select>
          </Row>
        </>
      )}
      <Row label="Скважины" title="На каких скважинах набора показывать отображение (все его слои)">
        <select value={all ? "all" : "some"} aria-label="Скважины" onChange={(e) => onSettings({ allHoles: e.target.value === "all" })}>
          <option value="all">все скважины набора</option>
          <option value="some">только выбранные{settings.holes.length ? ` (${settings.holes.length})` : ""}</option>
        </select>
      </Row>
      {!all && (
        <div className="dh-holes">
          <input value={query} placeholder="поиск скважины" aria-label="Поиск скважины" onChange={(e) => setQuery(e.target.value)} />
          <div className="dh-hole-list" role="listbox" aria-label="Выбранные скважины" aria-multiselectable>
            {shown.map((h) => (
              <label key={h.hole_id} className="check">
                <input type="checkbox" checked={chosen.has(h.hole_id)} onChange={(e) => onSettings({ holes: e.target.checked ? [...settings.holes, h.hole_id] : settings.holes.filter((x) => x !== h.hole_id) })} />
                {h.hole_id}
              </label>
            ))}
            {holes.length > shown.length && <span className="muted small">показаны первые {shown.length}: уточните поиск</span>}
          </div>
        </div>
      )}
      <Row label="Глубины, м" title="Интервал глубин по стволу; пусто — вся скважина">
        <NumField value={parseNum(settings.from)} label="Глубина от, м" placeholder="от устья" min={0} onChange={(v) => onSettings({ from: showNum(v).replace(",", ".") }, "from")} />
        <span className="muted">–</span>
        <NumField value={parseNum(settings.to)} label="Глубина до, м" placeholder="до забоя" min={0} onChange={(v) => onSettings({ to: showNum(v).replace(",", ".") }, "to")} />
      </Row>
    </Section>
  );
}
