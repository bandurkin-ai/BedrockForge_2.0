import { useMemo, useRef, useState } from "react";
import type { IntervalTableInfo } from "../../api";
import type { Track } from "../../charts/stripLog";
import { viewKind } from "./context";
import { StyleSwatch } from "./StyleGallery";
import { MenuItem, Popover } from "./ui";

const KIND_WORD = { bars: "столбцы", steps: "ступени", line: "линия", points: "точки", tube: "трубка", ribbon: "лента", text: "текст", event: "значки" } as const;

/** The layers of a display: order by dragging (from the hole outwards), eye, sample, field, kind, side, menu. */
export function LayerList({ tracks, tubes, tables, selected, onSelect, onOrder, onPatch, onAdd, onMenu, onArrange }: {
  tracks: Track[];
  tubes: Set<string>;
  tables: IntervalTableInfo[];
  selected: string[];
  onSelect: (ids: string[]) => void;
  onOrder: (ids: string[]) => void;
  onPatch: (id: string, p: Partial<Track>) => void;
  /** fields chosen in «+ Добавить»: table key and field name */
  onAdd: (fields: { table: string; field: string }[]) => void;
  onMenu: (id: string, action: "duplicate" | "saveStyle" | "applyStyle" | "applyTo" | "remove") => void;
  onArrange: () => void;
}) {
  const dragged = useRef<string | null>(null);
  const last = useRef<string | null>(null);
  const click = (id: string, e: React.MouseEvent | React.KeyboardEvent) => {
    if (e.shiftKey && last.current) {
      const a = tracks.findIndex((t) => t.id === last.current);
      const b = tracks.findIndex((t) => t.id === id);
      onSelect(tracks.slice(Math.min(a, b), Math.max(a, b) + 1).map((t) => t.id));
    } else if (e.ctrlKey || e.metaKey) {
      onSelect(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
    } else {
      onSelect([id]);
    }
    last.current = id;
  };
  return (
    <div className="dh-layers">
      <div className="dh-layers-head">
        <strong className="grow">Слои</strong>
        <button type="button" className="small-btn" title="Расставить слои без наложений: трубка на стволе, остальные по сторонам" disabled={tracks.length < 2} onClick={onArrange}>Разложить</button>
        <AddFields tables={tables} onAdd={onAdd} />
      </div>
      <div className="dh-layer-list" role="listbox" aria-label="Слои отображения" aria-multiselectable>
        {tracks.map((t) => {
          const table = tables.find((x) => x.key === t.table);
          const unit = table?.columns.find((c) => c.name === t.field)?.unit;
          const kind = viewKind(t, tubes.has(t.id));
          const side = kind === "tube" || kind === "event" ? "на стволе" : typeof t.split === "number" ? "разделение" : t.side === "left" ? "слева" : t.side === "both" ? "в обе" : "справа";
          const on = selected.includes(t.id);
          return (
            <div
              key={t.id} className={`dh-layer${on ? " selected" : ""}${t.visible === false ? " off" : ""}`} role="option" aria-selected={on} tabIndex={0}
              draggable onDragStart={() => (dragged.current = t.id)} onDragOver={(e) => e.preventDefault()}
              onDrop={() => {
                const from = dragged.current;
                dragged.current = null;
                if (!from || from === t.id) return;
                const ids = tracks.map((x) => x.id).filter((x) => x !== from);
                ids.splice(ids.indexOf(t.id), 0, from);
                onOrder(ids);
              }}
              onClick={(e) => click(t.id, e)} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && e.target === e.currentTarget && click(t.id, e)}
            >
              <span className="dh-handle" title="Перетащите: порядок слоёв от ствола наружу" aria-hidden>⠿</span>
              <button type="button" className="icon dh-eye" aria-pressed={t.visible !== false} title={t.visible === false ? "Слой скрыт: показать" : "Скрыть слой, не удаляя"} aria-label={`${t.visible === false ? "Показать" : "Скрыть"} слой ${t.field}`}
                onClick={(e) => { e.stopPropagation(); onPatch(t.id, { visible: t.visible === false ? undefined : false }); }}>{t.visible === false ? "◌" : "👁"}</button>
              <StyleSwatch style={{ level: "layer", body: t as unknown as Record<string, unknown> }} />
              <span className="dh-layer-field grow" title={`${table?.name ?? "таблица удалена"} · ${t.field}`}>{t.field}{unit ? `, ${unit}` : ""}{t.style?.linked ? " 🔗" : ""}</span>
              <span className="muted small">{KIND_WORD[kind]}</span>
              <span className="muted small dh-layer-side">{side}</span>
              <span onClick={(e) => e.stopPropagation()}>
                <Popover label={`Слой ${t.field}: действия`} title="Дублировать, стили, удалить" className="icon" button="⋯" align="right">
                  {(close) => (
                    <div className="dh-pop-form">
                      {(["duplicate", "saveStyle", "applyStyle", "applyTo"] as const).map((a) => (
                        <MenuItem key={a} onClick={() => { close(); onMenu(t.id, a); }}>
                          {{ duplicate: "Дублировать", saveStyle: "Сохранить как стиль…", applyStyle: "Применить стиль…", applyTo: "Применить к другим полям…" }[a]}
                        </MenuItem>
                      ))}
                      <hr />
                      <MenuItem danger onClick={() => { close(); onMenu(t.id, "remove"); }}>Удалить слой</MenuItem>
                    </div>
                  )}
                </Popover>
              </span>
            </div>
          );
        })}
        {!tracks.length && <span className="muted small dh-empty">Нет слоёв: добавьте поле кнопкой «+ Добавить».</span>}
      </div>
    </div>
  );
}

/** «+ Добавить»: fields of every table of the set, grouped, with a search; several at once. */
function AddFields({ tables, onAdd }: { tables: IntervalTableInfo[]; onAdd: (f: { table: string; field: string }[]) => void }) {
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const groups = useMemo(() => {
    const q = query.trim().toUpperCase();
    return tables.filter((t) => t.columns.length).map((t) => ({ t, cols: t.columns.filter((c) => !q || c.name.toUpperCase().includes(q) || t.name.toUpperCase().includes(q)) })).filter((g) => g.cols.length);
  }, [tables, query]);
  const key = (table: string, field: string) => `${table}\u0000${field}`;
  return (
    <Popover label="Добавить слой" title="Добавить поля как слои отображения" className="small-btn primary" button="+ Добавить ▾" align="right">
      {(close) => (
        <div className="dh-pop-form dh-add">
          <input value={query} placeholder="поиск поля" aria-label="Поиск поля" autoFocus onChange={(e) => setQuery(e.target.value)} />
          <div className="dh-add-list">
            {groups.map(({ t, cols }) => (
              <div key={t.key}>
                <span className="muted small">{t.name}{t.kind === "event" ? " (события)" : ""}</span>
                {cols.map((c) => {
                  const k = key(t.key, c.name);
                  return (
                    <label key={k} className="check">
                      <input type="checkbox" checked={picked.includes(k)} onChange={(e) => setPicked(e.target.checked ? [...picked, k] : picked.filter((x) => x !== k))} />
                      {c.name}{c.unit ? `, ${c.unit}` : ""}{c.kind === "text" ? <span className="muted small"> текст</span> : null}
                    </label>
                  );
                })}
              </div>
            ))}
            {!groups.length && <span className="muted small">Нет полей.</span>}
          </div>
          <button type="button" className="primary" disabled={!picked.length} onClick={() => {
            onAdd(picked.map((k) => {
              const [table, field] = k.split("\u0000");
              return { table, field };
            }));
            setPicked([]);
            close();
          }}>Добавить{picked.length > 1 ? ` (${picked.length})` : ""}</button>
        </div>
      )}
    </Popover>
  );
}
