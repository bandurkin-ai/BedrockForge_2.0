import { Fragment, useEffect, useState } from "react";
import type { AuditRun, Dataset, IntervalsData, Summary } from "../api";
import { HoleCard } from "../HoleCard";
import type { TableKind } from "../route";
import { ComponentPanel, type VizSettings } from "../viz/ComponentPanel";
import { makeScale } from "../viz/colorScale";
import { loadIntervals } from "../viz/useVizData";

const fmt = (value: number | null | undefined, digits = 0) =>
  value === null || value === undefined ? "—" : value.toLocaleString("ru-RU", { maximumFractionDigits: digits });

/** Component and colour range shared by every drillhole set in the 3D view. */
function ColorSection({ datasets, activeSet, refreshKey, viz, onViz }: {
  datasets: Dataset[]; activeSet: number | null; refreshKey: number; viz: VizSettings; onViz: (next: VizSettings) => void;
}) {
  const sets = datasets.filter((d) => d.kind === "drillholes");
  const components = [...new Map(sets.flatMap((d) => d.components ?? []).map((c) => [c.name, c])).values()];
  const reference = sets.find((d) => d.id === activeSet) ?? sets[0];
  const component = components.some((c) => c.name === viz.component) ? viz.component : reference?.components?.[0]?.name ?? components[0]?.name ?? null;
  const [intervals, setIntervals] = useState<IntervalsData | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!reference || !component || !reference.components?.some((c) => c.name === component)) {
      setIntervals(null);
      return;
    }
    loadIntervals(reference.id, component, refreshKey).then((i) => !cancelled && setIntervals(i)).catch(() => !cancelled && setIntervals(null));
    return () => {
      cancelled = true;
    };
  }, [reference?.id, component, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const stats = intervals?.stats ?? null;
  return (
    <>
      <h2 className="dock-title">Раскраска по содержаниям</h2>
      <ComponentPanel
        components={components}
        settings={viz}
        component={component}
        scale={makeScale(viz, stats ?? undefined)}
        stats={stats}
        unit={components.find((c) => c.name === component)?.unit ?? null}
        onChange={onViz}
      />
      {sets.length > 1 && reference && <p className="muted small">Авто-диапазон — по набору «{reference.name}».</p>}
    </>
  );
}

/** Right dock: the selected object (hole, wireframe) or the active drillhole set. */
export function PropertiesPanel({
  datasets, activeSet, summary, audit, selectedHole, selectedWireframe, refreshKey, viz, onViz, onOpenTable, onClearHole,
}: {
  datasets: Dataset[];
  activeSet: number | null;
  summary: Summary | null;
  audit: AuditRun | null;
  selectedHole: string | null;
  selectedWireframe: number | null;
  refreshKey: number;
  viz: VizSettings;
  onViz: (next: VizSettings) => void;
  onOpenTable: (kind: TableKind) => void;
  onClearHole: () => void;
}) {
  const color = <ColorSection datasets={datasets} activeSet={activeSet} refreshKey={refreshKey} viz={viz} onViz={onViz} />;
  const set = datasets.find((d) => d.id === activeSet);
  const wireframe = datasets.flatMap((d) => (d.wireframes ?? []).map((w) => ({ ...w, file: d }))).find((w) => w.id === selectedWireframe);

  if (wireframe) {
    const { stats, extent } = wireframe;
    return (
      <div className="props">
        <h2 className="dock-title">Каркас</h2>
        <div className="props-head"><span className="swatch-static" style={{ background: wireframe.color }} /><strong>{wireframe.name}</strong></div>
        <dl className="props-list">
          <dt>Файл</dt><dd>{wireframe.file.name}</dd>
          <dt>Формат</dt><dd>{wireframe.file.meta.format ?? "—"}</dd>
          <dt>Вершины</dt><dd>{fmt(wireframe.point_count)}</dd>
          <dt>Треугольники</dt><dd>{fmt(wireframe.triangle_count)}</dd>
          <dt>Площадь</dt><dd>{fmt(stats.area)} м²</dd>
          <dt>Объём</dt><dd>{stats.volume !== null ? `${fmt(stats.volume)} м³` : "— (не замкнут)"}</dd>
          <dt>Замкнутость</dt><dd className={stats.closed ? "" : "sev warning"}>{stats.closed ? "замкнут" : `открытых рёбер: ${fmt(stats.open_edges)}`}</dd>
          {stats.degenerate > 0 && <><dt>Вырожденные</dt><dd className="sev warning">{fmt(stats.degenerate)}</dd></>}
          <dt>X</dt><dd>{fmt(extent.min[0])} – {fmt(extent.max[0])}</dd>
          <dt>Y</dt><dd>{fmt(extent.min[1])} – {fmt(extent.max[1])}</dd>
          <dt>Z</dt><dd>{fmt(extent.min[2])} – {fmt(extent.max[2])}</dd>
        </dl>
        {stats.attributes && Object.keys(stats.attributes).length > 0 && (
          <>
            <h2 className="dock-title">Атрибуты</h2>
            <dl className="props-list">
              {Object.entries(stats.attributes).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>)}
            </dl>
          </>
        )}
      </div>
    );
  }

  if (selectedHole && activeSet !== null) {
    return (
      <div className="props">
        <h2 className="dock-title">Скважина{set ? ` · ${set.name}` : ""}</h2>
        <HoleCard setId={activeSet} holeId={selectedHole} refreshKey={refreshKey} onOpenTable={onOpenTable} onClose={onClearHole} />
        {color}
      </div>
    );
  }

  return (
    <div className="props">
      {set ? (
        <>
          <h2 className="dock-title">Набор скважин</h2>
          <dl className="props-list">
            <dt>Название</dt><dd>{set.name}</dd>
            <dt>Устья</dt><dd>{fmt(set.counts?.collar)}</dd>
            <dt>Замеры</dt><dd>{fmt(set.counts?.survey)}</dd>
            <dt>Пробы</dt><dd>{fmt(set.counts?.assay)}</dd>
            <dt>Компоненты</dt><dd>{set.components?.length ? set.components.map((c) => `${c.name} (${c.unit})`).join(", ") : "—"}</dd>
          </dl>
          <h2 className="dock-title">Аудит</h2>
          {audit ? (
            <dl className="props-list">
              <dt>Критично</dt><dd className="sev error">{audit.by_severity.error}</dd>
              <dt>Предупреждения</dt><dd className="sev warning">{audit.by_severity.warning}</dd>
              <dt>Информация</dt><dd className="sev info">{audit.by_severity.info}</dd>
              <dt>Запуск</dt><dd>{new Date(audit.created_at).toLocaleString("ru-RU")}{audit.stale && " · устарел"}</dd>
            </dl>
          ) : (
            <p className="muted small">Аудит ещё не запускался.</p>
          )}
          {summary && summary.imports.length > 0 && (
            <>
              <h2 className="dock-title">История импорта</h2>
              <ul className="props-imports">
                {summary.imports.map((j) => (
                  <li key={j.id}>
                    <span>{j.filename}</span>
                    <span className="muted small">{j.kind} · {j.row_count.toLocaleString("ru-RU")} строк · {new Date(j.created_at).toLocaleDateString("ru-RU")}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      ) : (
        <p className="muted small">Выберите набор скважин, скважину или каркас в дереве проекта.</p>
      )}
      {color}
    </div>
  );
}
