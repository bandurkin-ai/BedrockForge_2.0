import { useEffect, useState } from "react";
import {
  auditExportUrl,
  getAudit,
  getFindings,
  runAudit,
  type AuditConfig,
  type AuditFinding,
  type AuditRun,
  type FindingsPage,
} from "./api";

const SEVERITY: Record<string, string> = { error: "Критично", warning: "Предупреждение", info: "Информация" };
const TABLES: Record<string, string> = { collar: "Устья", survey: "Инклинометрия", assay: "Пробы" };
const PAGE = 50;

const DEFAULT_CONFIG: AuditConfig = {
  depth_tolerance: 0.01,
  dogleg_per_30m: 10,
  isolated_factor: 10,
  z_jump: 50,
  assay_outlier_z: 8,
};

const CONFIG_LABELS: Record<keyof AuditConfig, string> = {
  depth_tolerance: "Допуск по глубине, м",
  dogleg_per_30m: "Порог изгиба, °/30 м",
  isolated_factor: "Изоляция устья (во сколько раз дальше типичного)",
  z_jump: "Скачок отметки Z, м",
  assay_outlier_z: "Выброс содержания (робастных σ)",
};

function formatExample(example: Record<string, unknown>): string {
  return Object.entries(example)
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => `${k}=${typeof v === "number" ? Number(v.toPrecision(6)) : String(v)}`)
    .join(", ");
}

function Badge({ severity }: { severity: string }) {
  return <span className={`badge ${severity}`}>{SEVERITY[severity]}</span>;
}

export function AuditPage({
  projectId,
  holeId,
  onAudited,
  onClearHole,
}: {
  projectId: number;
  holeId: string | null;
  onAudited: () => void;
  onClearHole: () => void;
}) {
  const [run, setRun] = useState<AuditRun | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [config, setConfig] = useState<AuditConfig>(DEFAULT_CONFIG);
  const [filters, setFilters] = useState({ severity: "", table: "", code: "", hole_id: "" });
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<FindingsPage | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getAudit(projectId)
      .then((r) => {
        setRun(r);
        if (r) setConfig({ ...DEFAULT_CONFIG, ...(r.config as Partial<AuditConfig>) });
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoaded(true));
  }, [projectId]);

  useEffect(() => setOffset(0), [holeId]);

  const runId = run?.run_id;
  useEffect(() => {
    if (runId === undefined) {
      setPage(null);
      return;
    }
    getFindings(projectId, { ...filters, hole: holeId ?? undefined }, PAGE, offset)
      .then(setPage)
      .catch((e: Error) => setError(e.message));
  }, [projectId, runId, filters, offset, holeId]);

  async function onRun() {
    setBusy(true);
    setError(null);
    try {
      setRun(await runAudit(projectId, config));
      setOffset(0);
      onAudited();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function updateFilter(patch: Partial<typeof filters>) {
    setFilters({ ...filters, ...patch });
    setOffset(0);
  }

  const maxRows = Math.max(1, ...(run?.rules.map((r) => r.rows) ?? [1]));
  const passed = run?.rules.filter((r) => r.findings === 0).length ?? 0;
  const total = page?.total ?? 0;

  return (
    <main className="page">
      <h1>Аудит данных</h1>
      {error && <div className="alert error">{error}</div>}

      <div className="actions">
        <button className="primary" onClick={onRun} disabled={busy}>
          {busy ? "Проверка…" : run ? "Запустить заново" : "Запустить аудит"}
        </button>
        {run && <a className="button" href={auditExportUrl(projectId)}>Скачать отчёт (Excel)</a>}
        {run && <span className="muted">Последний запуск: {new Date(run.created_at).toLocaleString("ru-RU")}</span>}
      </div>
      {run?.stale && <div className="alert warn">После этого аудита были импортированы новые данные. Запустите аудит заново.</div>}

      <details className="settings">
        <summary>Параметры проверок</summary>
        <div className="settings-grid">
          {(Object.keys(CONFIG_LABELS) as (keyof AuditConfig)[]).map((key) => (
            <label key={key} className="field">
              {CONFIG_LABELS[key]}
              <input
                type="number"
                step="any"
                min={0}
                value={config[key]}
                onChange={(e) => setConfig({ ...config, [key]: Number(e.target.value) })}
              />
            </label>
          ))}
        </div>
      </details>

      {loaded && !run && <p className="muted">Аудит ещё не запускался. Сначала импортируйте данные, затем нажмите «Запустить аудит».</p>}

      {run && (
        <>
          <section className="cards">
            {(["error", "warning", "info"] as const).map((s) => (
              <div key={s} className={`card sev-${s}`}>
                <div className="muted">{SEVERITY[s]}</div>
                <div className="big">{run.by_severity[s]}</div>
                <div className="muted small">находок (скважина × правило)</div>
              </div>
            ))}
            <div className="card">
              <div className="muted">Правил без замечаний</div>
              <div className="big">{passed} из {run.rules.length}</div>
            </div>
          </section>

          <h2>Результаты по правилам</h2>
          <div className="table-wrap">
            <table className="rules">
              <thead>
                <tr><th>Уровень</th><th>Таблица</th><th>Правило</th><th>Скважин</th><th>Строк</th><th></th></tr>
              </thead>
              <tbody>
                {run.rules.map((r) => (
                  <tr
                    key={r.code}
                    className={r.findings === 0 ? "passed" : "clickable"}
                    title={r.description}
                    onClick={() => r.findings > 0 && updateFilter({ code: filters.code === r.code ? "" : r.code })}
                  >
                    <td>{r.findings === 0 ? <span className="ok-mark">✓ пройдено</span> : <Badge severity={r.severity} />}</td>
                    <td>{TABLES[r.table]}</td>
                    <td>{r.title}{filters.code === r.code && " ◄"}</td>
                    <td>{r.findings}</td>
                    <td>{r.rows}</td>
                    <td className="barcell">
                      {r.rows > 0 && <div className={`bar ${r.severity}`} style={{ width: `${Math.max(3, (r.rows / maxRows) * 100)}%` }} />}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted small">Нажмите на строку правила, чтобы отфильтровать находки.</p>

          <h2>Находки</h2>
          <div className="filters">
            <select value={filters.severity} onChange={(e) => updateFilter({ severity: e.target.value })}>
              <option value="">Все уровни</option>
              {Object.entries(SEVERITY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <select value={filters.table} onChange={(e) => updateFilter({ table: e.target.value })}>
              <option value="">Все таблицы</option>
              {Object.entries(TABLES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <input
              type="search"
              placeholder="Скважина…"
              value={filters.hole_id}
              onChange={(e) => updateFilter({ hole_id: e.target.value })}
            />
            {filters.code && <button onClick={() => updateFilter({ code: "" })}>Сбросить правило ✕</button>}
            {holeId && (
              <span className="filter-chip">
                Скважина: <strong>{holeId}</strong>
                <button className="icon" onClick={onClearHole} aria-label="Показать все скважины" title="Показать все скважины">✕</button>
              </span>
            )}
          </div>

          {page && page.total === 0 && <p className="muted">Нет находок по выбранным фильтрам.</p>}
          {page && page.total > 0 && (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Уровень</th><th>Таблица</th><th>Скважина</th><th>Строк</th><th>Правило</th><th>Примеры</th></tr>
                  </thead>
                  <tbody>
                    {page.items.map((f: AuditFinding) => {
                      const examples = f.details.examples ?? [];
                      const text = examples.slice(0, 3).map(formatExample).join("  |  ");
                      return (
                        <tr key={f.id}>
                          <td><Badge severity={f.severity} /></td>
                          <td>{TABLES[f.table]}</td>
                          <td>{f.hole_id}</td>
                          <td>{f.count}</td>
                          <td className="wrap">{f.message}</td>
                          <td className="wrap" title={examples.map(formatExample).join("\n")}>
                            {text}{examples.length > 3 && "  …"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="pager">
                <button onClick={() => setOffset(Math.max(0, offset - PAGE))} disabled={offset === 0}>← Назад</button>
                <span>{offset + 1}–{Math.min(offset + PAGE, total)} из {total}</span>
                <button onClick={() => setOffset(offset + PAGE)} disabled={offset + PAGE >= total}>Вперёд →</button>
              </p>
            </>
          )}
        </>
      )}
    </main>
  );
}
