import { useMemo, useState } from "react";
import type { Hole, Summary } from "../api";
import type { TableKind } from "../route";
import { Icon } from "./icons";

const MAX_SHOWN = 300;

const TABLE_ITEMS: { kind: TableKind; label: string }[] = [
  { kind: "collar", label: "Устья" },
  { kind: "survey", label: "Инклинометрия" },
  { kind: "assay", label: "Пробы" },
];

/** Left dock: the project's data tables and the hole navigator. */
export function ProjectTree({
  summary,
  holes,
  selectedHole,
  onOpenTable,
  onSelectHole,
  onImport,
  onDemo,
}: {
  summary: Summary | null;
  holes: Hole[];
  selectedHole: string | null;
  onOpenTable: (kind: TableKind) => void;
  onSelectHole: (holeId: string | null) => void;
  onImport: () => void;
  onDemo: () => void;
}) {
  const [query, setQuery] = useState("");
  const [onlyFlagged, setOnlyFlagged] = useState(false);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return holes.filter((h) => (!q || h.hole_id.toLowerCase().includes(q)) && (!onlyFlagged || h.error + h.warning > 0));
  }, [holes, query, onlyFlagged]);
  const shown = filtered.slice(0, MAX_SHOWN);

  return (
    <div className="tree">
      <section>
        <h2 className="dock-title">Данные</h2>
        <ul className="tree-list">
          {TABLE_ITEMS.map(({ kind, label }) => (
            <li key={kind}>
              <button onClick={() => onOpenTable(kind)} title={`Открыть таблицу «${label}»`}>
                <Icon name="table" size={14} />
                <span className="grow">{label}</span>
                <span className="count">{summary ? summary.counts[kind].toLocaleString("ru-RU") : "…"}</span>
              </button>
            </li>
          ))}
        </ul>
        {summary && summary.components.length > 0 && (
          <p className="muted small tree-note">Компоненты: {summary.components.map((c) => `${c.name} (${c.unit})`).join(", ")}</p>
        )}
      </section>

      <section className="tree-holes">
        <h2 className="dock-title">Скважины · {holes.length.toLocaleString("ru-RU")}</h2>
        {holes.length === 0 ? (
          <div className="empty">
            <p className="muted small">В проекте пока нет скважин.</p>
            <button className="primary" onClick={onImport}>Импортировать данные</button>
            <button onClick={onDemo}>Загрузить демо-проект</button>
          </div>
        ) : (
          <>
            <div className="tree-filter">
              <input
                type="search"
                placeholder="Поиск скважины…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Поиск скважины"
              />
              <button
                className={onlyFlagged ? "icon active" : "icon"}
                onClick={() => setOnlyFlagged((v) => !v)}
                aria-pressed={onlyFlagged}
                title="Только скважины с замечаниями аудита"
              >
                <Icon name="audit" size={16} />
              </button>
            </div>
            <ul className="tree-list hole-list">
              {shown.map((h) => (
                <li key={h.hole_id}>
                  <button
                    className={h.hole_id === selectedHole ? "active" : ""}
                    onClick={() => onSelectHole(h.hole_id === selectedHole ? null : h.hole_id)}
                    title={`${h.hole_id}: замеров ${h.survey_count}, проб ${h.assay_count}`}
                  >
                    <span className="grow hole-name">{h.hole_id}</span>
                    <span className="dots">
                      {h.error > 0 && <span className="dot error" title={`Критично: ${h.error}`}>{h.error}</span>}
                      {h.warning > 0 && <span className="dot warning" title={`Предупреждений: ${h.warning}`}>{h.warning}</span>}
                      {h.info > 0 && <span className="dot info" title={`Информация: ${h.info}`}>{h.info}</span>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {filtered.length > MAX_SHOWN && (
              <p className="muted small tree-note">Показано {MAX_SHOWN} из {filtered.length.toLocaleString("ru-RU")}. Уточните поиск.</p>
            )}
            {filtered.length === 0 && <p className="muted small tree-note">Ничего не найдено.</p>}
          </>
        )}
      </section>
    </div>
  );
}
