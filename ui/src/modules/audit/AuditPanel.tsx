import { useEffect, useState } from "react";
import { useApp } from "../../shell/AppContext";
import { useStore } from "../../shell/useStore";
import { tablesStore } from "../tables/state";
import { auditStore, exampleText, loadAudit, saveSettings, setFilter, SEVERITY_TITLES, TABLE_TITLES, type AuditConfig, type Severity } from "./state";
import css from "./Audit.module.css";

const THRESHOLDS: [keyof AuditConfig, string][] = [
  ["depth_tolerance", "Допуск по глубине, м"],
  ["dogleg_per_30m", "Изгиб, ° на 30 м"],
  ["isolated_factor", "Изоляция устья, × медианы"],
  ["z_jump", "Скачок Z, м"],
  ["assay_outlier_z", "Выброс, робастных σ"],
];

/** Вкладка «Аудит» (ТЗ 7.12): table checked, thresholds, every rule (passed too), findings by hole and rule. */
export function AuditPanel() {
  const app = useApp();
  const s = useStore(auditStore, (x) => x);
  const setId = useStore(tablesStore, (x) => x.setId);
  const [showSettings, setShowSettings] = useState(false);
  useEffect(() => {
    if (setId !== s.setId) void loadAudit(setId);
  }, [setId, s.setId]);
  if (setId === null) return <div className={css.empty}>Нет активного набора скважин: выберите набор в дереве данных.</div>;
  const r = s.result;
  return (
    <div className={css.panel}>
      <div className={css.bar}>
        <button type="button" className={css.primary} onClick={() => void app.run("audit.run")}>
          Запустить аудит
        </button>
        <label>
          Таблица проб{" "}
          <select
            value={s.settings?.table ?? ""}
            aria-label="Проверяемая таблица проб"
            onChange={(e) => void saveSettings({}, e.target.value || null)}
          >
            {!s.tables.length && <option value="">— нет таблиц проб —</option>}
            {s.tables.map((t) => (
              <option key={t.table} value={t.table}>
                {t.name} ({t.rows.toLocaleString("ru-RU")})
              </option>
            ))}
          </select>
        </label>
        <button type="button" className={css.btn} onClick={() => setShowSettings(!showSettings)} aria-expanded={showSettings}>
          Пороги
        </button>
        <button type="button" className={css.btn} disabled={!r} onClick={() => void app.run("audit.export")}>
          Отчёт Excel…
        </button>
        {r ? (
          <span className={r.stale ? css.stale : css.note} data-testid="audit-state">
            {new Date(r.created_at).toLocaleString("ru-RU")} · {r.table_name}
            {r.stale ? " · устарел: данные изменены после аудита" : ""}
          </span>
        ) : (
          <span className={css.note}>Аудит не запускался</span>
        )}
      </div>
      {showSettings && s.settings && <Thresholds config={s.settings.config} table={s.settings.table} />}
      {s.error && <div className={css.error}>{s.error}</div>}
      {r && (
        <div className={css.main}>
          <table className={css.rules} aria-label="Правила аудита">
            <thead>
              <tr>
                <th>Правило</th>
                <th>Строк</th>
                <th>Скв.</th>
              </tr>
            </thead>
            <tbody>
              {r.summary.map((x) => (
                <tr
                  key={x.code}
                  className={`${css[`sev_${x.severity}`]} ${x.rows ? "" : css.passed} ${s.filters.code === x.code ? css.selected : ""}`}
                  title={`${x.code}: ${x.description}`}
                  onClick={() => void setFilter({ code: s.filters.code === x.code ? "" : x.code })}
                >
                  <td>
                    <span className={css.badge}>{SEVERITY_TITLES[x.severity][0]}</span> {x.title}
                  </td>
                  <td>{x.rows ? x.rows.toLocaleString("ru-RU") : "✓"}</td>
                  <td>{x.holes || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className={css.findings}>
            <div className={css.bar}>
              <select value={s.filters.severity} onChange={(e) => void setFilter({ severity: e.target.value as Severity | "" })} aria-label="Важность">
                <option value="">Любая важность</option>
                {(Object.keys(SEVERITY_TITLES) as Severity[]).map((k) => (
                  <option key={k} value={k}>
                    {SEVERITY_TITLES[k]}
                  </option>
                ))}
              </select>
              <select value={s.filters.table} onChange={(e) => void setFilter({ table: e.target.value })} aria-label="Таблица">
                <option value="">Все таблицы</option>
                {Object.entries(TABLE_TITLES).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
              <input placeholder="Скважина" value={s.filters.hole} onChange={(e) => void setFilter({ hole: e.target.value })} aria-label="Скважина" />
              {s.filters.code && (
                <span className={css.chip}>
                  {s.filters.code}
                  <button type="button" aria-label="Снять фильтр по правилу" onClick={() => void setFilter({ code: "" })}>
                    ✕
                  </button>
                </span>
              )}
              <span className={css.note}>находок: {s.total.toLocaleString("ru-RU")}</span>
            </div>
            <ul className={css.list} data-testid="audit-findings">
              {s.findings.map((f, i) => (
                <li key={`${f.code}-${f.hole}-${i}`} className={css[`sev_${f.severity}`]}>
                  <div>
                    <b>{f.hole ?? "—"}</b> · {r.summary.find((x) => x.code === f.code)?.title ?? f.code} · строк: {f.count}
                  </div>
                  {f.examples.slice(0, 20).map((e, k) => (
                    <div key={k} className={css.example}>
                      {exampleText(e)}
                    </div>
                  ))}
                </li>
              ))}
              {!s.findings.length && <li className={css.note}>Находок нет.</li>}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}

function Thresholds({ config, table }: { config: AuditConfig; table: string | null }) {
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries([...THRESHOLDS.map(([k]) => [k, String(config[k]).replace(".", ",")]), ["planned_markers", config.planned_markers.join(", ")]]),
  );
  const [message, setMessage] = useState<string | null>(null);
  const save = async () => {
    const next: Partial<AuditConfig> = {};
    for (const [k] of THRESHOLDS) (next as Record<string, number>)[k] = Number(String(values[k]).replace(",", "."));
    next.planned_markers = values.planned_markers.split(",").map((x) => x.trim()).filter(Boolean);
    setMessage((await saveSettings(next, table)) ?? "Пороги сохранены; они применятся при следующем запуске");
  };
  return (
    <div className={css.thresholds} aria-label="Пороги аудита">
      {THRESHOLDS.map(([k, label]) => (
        <label key={k}>
          {label}
          <input value={values[k]} onChange={(e) => setValues({ ...values, [k]: e.target.value })} aria-label={label} />
        </label>
      ))}
      <label className={css.wide}>
        Признаки плановой скважины в статусе
        <input value={values.planned_markers} onChange={(e) => setValues({ ...values, planned_markers: e.target.value })} aria-label="Признаки плановой скважины" />
      </label>
      <button type="button" className={css.btn} onClick={() => void save()}>
        Сохранить пороги
      </button>
      {message && <span className={css.note}>{message}</span>}
    </div>
  );
}
