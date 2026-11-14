import type { AuditRun, Summary } from "../api";
import { HoleCard } from "../HoleCard";
import type { TableKind } from "../route";
import { ComponentPanel, type VizSettings } from "../viz/ComponentPanel";
import { makeScale } from "../viz/colorScale";
import { useIntervals } from "../viz/useVizData";

/** Component and colour range shared by the section, hole log and 3D views. */
function ColorSection({
  projectId,
  summary,
  refreshKey,
  viz,
  onViz,
}: {
  projectId: number;
  summary: Summary | null;
  refreshKey: number;
  viz: VizSettings;
  onViz: (next: VizSettings) => void;
}) {
  const components = summary?.components ?? [];
  const component = components.some((c) => c.name === viz.component) ? viz.component : components[0]?.name ?? null;
  const intervals = useIntervals(projectId, component, refreshKey);
  const stats = intervals.data?.stats ?? null;
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
    </>
  );
}

/** Right dock: the selected hole, or the project overview when nothing is selected. */
export function PropertiesPanel({
  projectId,
  projectName,
  summary,
  audit,
  selectedHole,
  refreshKey,
  viz,
  onViz,
  onOpenTable,
  onClearHole,
}: {
  projectId: number;
  projectName: string;
  summary: Summary | null;
  audit: AuditRun | null;
  selectedHole: string | null;
  refreshKey: number;
  viz: VizSettings;
  onViz: (next: VizSettings) => void;
  onOpenTable: (kind: TableKind) => void;
  onClearHole: () => void;
}) {
  const color = <ColorSection projectId={projectId} summary={summary} refreshKey={refreshKey} viz={viz} onViz={onViz} />;
  if (selectedHole) {
    return (
      <div className="props">
        <h2 className="dock-title">Скважина</h2>
        <HoleCard projectId={projectId} holeId={selectedHole} refreshKey={refreshKey} onOpenTable={onOpenTable} onClose={onClearHole} />
        {color}
      </div>
    );
  }
  const count = (kind: string) => (summary ? summary.counts[kind].toLocaleString("ru-RU") : "…");
  return (
    <div className="props">
      <h2 className="dock-title">Проект</h2>
      <dl className="props-list">
        <dt>Название</dt><dd>{projectName}</dd>
        <dt>Устья</dt><dd>{count("collar")}</dd>
        <dt>Замеры</dt><dd>{count("survey")}</dd>
        <dt>Пробы</dt><dd>{count("assay")}</dd>
        <dt>Компоненты</dt><dd>{summary?.components.length ? summary.components.map((c) => c.name).join(", ") : "—"}</dd>
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
      {color}
      <p className="muted small">Выберите скважину в дереве проекта или на виде, чтобы увидеть её свойства.</p>
    </div>
  );
}
