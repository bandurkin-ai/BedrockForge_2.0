import { useEffect, useState } from "react";
import { getHole, type HoleDetail } from "./api";
import type { TableKind } from "./route";

const SEVERITY: Record<string, string> = { error: "Критично", warning: "Предупреждение", info: "Информация" };

function fmt(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? "—" : value.toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

export function HoleCard({
  projectId,
  holeId,
  refreshKey,
  onOpenTable,
  onClose,
}: {
  projectId: number;
  holeId: string;
  refreshKey: number;
  onOpenTable: (kind: TableKind) => void;
  onClose: () => void;
}) {
  const [hole, setHole] = useState<HoleDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    getHole(projectId, holeId)
      .then((h) => !cancelled && setHole(h))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [projectId, holeId, refreshKey]);

  return (
    <section className="hole-card" aria-label={`Скважина ${holeId}`}>
      <header>
        <strong>{holeId}</strong>
        <button className="icon" onClick={onClose} title="Снять выбор" aria-label="Снять выбор">✕</button>
      </header>
      {error && <p className="muted">{error}</p>}
      {hole && (
        <>
          <dl>
            <dt>X</dt><dd>{fmt(hole.x, 3)}</dd>
            <dt>Y</dt><dd>{fmt(hole.y, 3)}</dd>
            <dt>Z</dt><dd>{fmt(hole.z, 3)}</dd>
            <dt>Глубина</dt><dd>{fmt(hole.td)} м</dd>
            <dt>Азимут</dt><dd>{fmt(hole.azimuth, 1)}°</dd>
            <dt>Наклон</dt><dd>{fmt(hole.dip, 1)}°</dd>
            <dt>Статус</dt><dd>{hole.status || "—"}</dd>
            {hole.stage && <><dt>Этап</dt><dd>{hole.stage}</dd></>}
          </dl>
          <div className="hole-links">
            <button onClick={() => onOpenTable("collar")}>Устье</button>
            <button onClick={() => onOpenTable("survey")}>Замеры: {hole.survey_count}</button>
            <button onClick={() => onOpenTable("assay")}>Пробы: {hole.assay_count}</button>
          </div>
          {hole.findings.length > 0 ? (
            <ul className="hole-findings">
              {hole.findings.map((f) => (
                <li key={f.code} className={f.severity} title={SEVERITY[f.severity]}>
                  {f.message}{f.count > 1 && ` (${f.count})`}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted small">Замечаний аудита нет.</p>
          )}
        </>
      )}
    </section>
  );
}
