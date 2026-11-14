import { lazy, Suspense } from "react";
import type { Hole, Summary } from "../api";
import { ScaleStrip, type VizSettings } from "./ComponentPanel";
import { makeScale } from "./colorScale";
import type { SectionLine } from "./geometry";
import { LogView } from "./LogView";
import { PlanView } from "./PlanView";
import { SectionView } from "./SectionView";
import { useHoleLog, useIntervals, useTrajectories } from "./useVizData";

// three.js is large: load it only when the 3D tab is opened.
const View3D = lazy(() => import("./View3D").then((m) => ({ default: m.View3D })));

export type VizKind = "plan" | "section" | "log" | "view3d";

/** One of the four visual views; loads the data they share (trajectories, intervals of one component). */
export function VizTab({
  kind,
  projectId,
  refreshKey,
  summary,
  holes,
  selectedHole,
  settings,
  section,
  onSelectHole,
  onSection,
  onOpenSection,
}: {
  kind: VizKind;
  projectId: number;
  refreshKey: number;
  summary: Summary | null;
  holes: Hole[];
  selectedHole: string | null;
  settings: VizSettings;
  section: SectionLine | null;
  onSelectHole: (holeId: string | null) => void;
  onSection: (section: SectionLine | null) => void;
  onOpenSection: () => void;
}) {
  const components = summary?.components ?? [];
  // A component that no longer exists (after re-import) falls back to the first one.
  const component = components.some((c) => c.name === settings.component) ? settings.component : components[0]?.name ?? null;

  const needsPaths = kind !== "log";
  const trajectories = useTrajectories(projectId, refreshKey);
  const intervals = useIntervals(projectId, kind === "plan" ? null : component, refreshKey);
  const log = useHoleLog(projectId, kind === "log" ? selectedHole : null, refreshKey);
  const scale = makeScale(settings, intervals.data?.stats);
  const unit = components.find((c) => c.name === component)?.unit ?? null;

  const loading = (needsPaths && trajectories.loading) || (kind !== "plan" && intervals.loading);
  const error = (needsPaths && trajectories.error) || (kind !== "plan" ? intervals.error : null) || null;

  return (
    <main className="viz-page">
      {kind !== "plan" && <ScaleStrip component={component} unit={unit} scale={scale} log={settings.log} />}
      {kind === "plan" && (
        <PlanView
          projectId={projectId}
          refreshKey={refreshKey}
          holes={holes}
          selectedHole={selectedHole}
          section={section}
          onSelectHole={onSelectHole}
          onSection={onSection}
          onOpenSection={onOpenSection}
        />
      )}
      {kind === "section" && (
        <SectionView
          trajectories={trajectories.data}
          intervals={intervals.data}
          scale={scale}
          unit={unit}
          component={component}
          section={section}
          selectedHole={selectedHole}
          loading={loading}
          error={error}
          onSection={onSection}
          onSelectHole={onSelectHole}
        />
      )}
      {kind === "log" && (
        <LogView
          holeId={selectedHole}
          log={log.data}
          loading={log.loading}
          error={log.error}
          component={component}
          unit={unit}
          scale={scale}
        />
      )}
      {kind === "view3d" && (
        <Suspense fallback={<div className="canvas-note static">Загрузка 3D…</div>}>
        <View3D
          trajectories={trajectories.data}
          intervals={intervals.data}
          scale={scale}
          holes={holes}
          selectedHole={selectedHole}
          loading={loading}
          error={error}
          onSelectHole={onSelectHole}
        />
        </Suspense>
      )}
    </main>
  );
}
