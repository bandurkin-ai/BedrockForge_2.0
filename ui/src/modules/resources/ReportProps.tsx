/**
 * «Свойства» of a saved report of the resources (ТЗ 8.10; ADR 0027 решения 14, 16, 29; Д-35): the name, what the report was made of and what it says (`reportFacts`), the
 * actions — «Показать в панели», «Сохранить XLSX / CSV», «Перестроить» (`lineage.rebuild`: the same parameters, written over, the version grows), «Переименовать…», «Удалить…» (the
 * question «Влияние изменения» is the command's) — and the graph's block: «Закрепить», «Происхождение», «Зависимые» (`LineageProps`). Every action is a command of the registry; an
 * unavailable one says why. The shell shows it for the selected `resource_report`.
 */
import { useEffect, useState } from "react";
import { useApp } from "../../shell/AppContext";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { LineageBadge } from "../lineage/LineageBadge";
import { LineageProps } from "../lineage/LineageProps";
import { projectStore } from "../projects/state";
import type { ReportResult } from "./answers";
import { loadReport } from "./api";
import { reportFacts } from "./reportFacts";
import { NODE_KIND, reportActions } from "./reportActions";
import { useSel } from "./ReportParts";
import { resourcesStore } from "./state";

export function ReportProps({ id }: { id: number }) {
  const app = useApp();
  const row = useSel(resourcesStore, (s) => s.reports.find((r) => r.id === id));
  const uid = useSel(projectStore, (s) => s.current?.uid ?? null);
  const [detail, setDetail] = useState<{ id: number; version: number; result: ReportResult } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const version = row?.version ?? null;
  useEffect(() => {
    let alive = true;
    loadReport(id, { size: 1 }).then(
      (result) => alive && (setDetail({ id, version: result.version, result }), setError(null)),
      (e: unknown) => alive && setError(errorText(e)),
    );
    return () => {
      alive = false;
    };
  }, [id, version, uid]);
  const have = detail && detail.id === id ? detail.result : null;
  const actions = row ? reportActions(app, row) : [];
  const names = new Map(actions.map((a) => [a.label, a]));
  const btn = (label: string, testId: string, text = label) => {
    const a = names.get(label);
    const why = a?.reason ?? (row ? null : "Отчёта нет в списке: обновите раздел «Отчёты»");
    return (
      <button type="button" className={css.btn} disabled={!a || !!why} title={why ? `${text}\nНедоступно: ${why}` : text} data-testid={testId} onClick={() => a?.run?.()}>
        {label}
      </button>
    );
  };
  return (
    <div className={css.stack} data-testid="props-resource-report">
      <fieldset className={css.group}>
        <legend>Отчёт по ресурсам</legend>
        <div className={css.row} data-testid="props-resource-name">
          <strong>{row?.name ?? have?.name ?? `№${id}`}</strong>
          <LineageBadge node={{ kind: NODE_KIND, id }} />
        </div>
        {row?.state === "building" && <p className={css.note}>Отчёт строится: действия станут доступны после конца задания.</p>}
        {row?.state === "error" && <p className={css.error}>Отчёт не построен: перестройте его.</p>}
        {have ? (
          <dl className={css.facts} data-testid="props-resource-facts">
            {reportFacts(have, row).map((f) => (
              <div key={f.key} data-testid={`props-resource-${f.key}`} style={{ display: "contents" }}>
                <dt>{f.label}</dt>
                <dd style={{ whiteSpace: "pre-line", overflowWrap: "anywhere" }}>{f.value}</dd>
              </div>
            ))}
          </dl>
        ) : error ? (
          <p className={css.error}>{error}</p>
        ) : (
          <p className={css.note}>Чтение отчёта…</p>
        )}
        <div className={css.row}>
          {btn("Показать в панели «Ресурсы»", "props-resource-open")}
          {btn("Сохранить XLSX…", "props-resource-xlsx")}
          {btn("Сохранить CSV таблицы…", "props-resource-csv")}
        </div>
        <div className={css.row}>
          {btn("Перестроить", "props-resource-rebuild", "Перестроить отчёт: те же параметры, результат записывается поверх")}
          {btn("Переименовать…", "props-resource-rename")}
          {btn("Удалить…", "props-resource-delete")}
        </div>
      </fieldset>
      <LineageProps node={{ kind: NODE_KIND, id }} />
    </div>
  );
}
