import { useMemo, useState } from "react";
import type { Hole, Summary } from "./api";
import { HoleCard } from "./HoleCard";
import type { Route, TableKind } from "./route";

const MAX_SHOWN = 300;

const TABLE_ITEMS: { kind: TableKind; label: string }[] = [
  { kind: "collar", label: "Устья" },
  { kind: "survey", label: "Инклинометрия" },
  { kind: "assay", label: "Пробы" },
];

export function Sidebar({
  open,
  projectId,
  summary,
  holes,
  route,
  selectedHole,
  refreshKey,
  onOpenTable,
  onSelectHole,
}: {
  open: boolean;
  projectId: number;
  summary: Summary | null;
  holes: Hole[];
  route: Route;
  selectedHole: string | null;
  refreshKey: number;
  onOpenTable: (kind: TableKind) => void;
  onSelectHole: (holeId: string | null) => void;
}) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? holes.filter((h) => h.hole_id.toLowerCase().includes(q)) : holes;
  }, [holes, query]);
  const shown = filtered.slice(0, MAX_SHOWN);

  return (
    <aside className={open ? "sidebar open" : "sidebar"} aria-label="Навигация по проекту">
      <section>
        <h2 className="side-title">Данные</h2>
        <ul className="side-nav">
          {TABLE_ITEMS.map(({ kind, label }) => (
            <li key={kind}>
              <button
                className={route.tab === "table" && route.kind === kind ? "active" : ""}
                onClick={() => onOpenTable(kind)}
              >
                <span>{label}</span>
                <span className="count">{summary ? summary.counts[kind].toLocaleString("ru-RU") : "…"}</span>
              </button>
            </li>
          ))}
        </ul>
        {summary && summary.components.length > 0 && (
          <p className="muted small components">
            Компоненты: {summary.components.map((c) => `${c.name} (${c.unit})`).join(", ")}
          </p>
        )}
      </section>

      <section className="holes">
        <h2 className="side-title">Скважины ({holes.length.toLocaleString("ru-RU")})</h2>
        <input
          type="search"
          placeholder="Поиск скважины…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Поиск скважины"
        />
        {holes.length === 0 && <p className="muted small">Нет скважин. Импортируйте устья на вкладке «Импорт».</p>}
        <ul className="hole-list">
          {shown.map((h) => (
            <li key={h.hole_id}>
              <button
                className={h.hole_id === selectedHole ? "active" : ""}
                onClick={() => onSelectHole(h.hole_id === selectedHole ? null : h.hole_id)}
                title={`${h.hole_id}: замеров ${h.survey_count}, проб ${h.assay_count}`}
              >
                <span className="hole-name">{h.hole_id}</span>
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
          <p className="muted small">Показано {MAX_SHOWN} из {filtered.length.toLocaleString("ru-RU")}. Уточните поиск.</p>
        )}
        {holes.length > 0 && filtered.length === 0 && <p className="muted small">Ничего не найдено.</p>}
      </section>

      {selectedHole && (
        <HoleCard
          projectId={projectId}
          holeId={selectedHole}
          refreshKey={refreshKey}
          onOpenTable={onOpenTable}
          onClose={() => onSelectHole(null)}
        />
      )}
    </aside>
  );
}
