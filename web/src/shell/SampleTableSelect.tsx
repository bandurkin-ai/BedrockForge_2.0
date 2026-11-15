import { useEffect, useState } from "react";
import { getSampleTables, type Dataset, type SampleTables } from "../api";

/** Has the set any sample table (built-in assays or an assay / interval table with rows)? */
export const hasSamples = (d: Dataset | undefined) =>
  !!d && ((d.counts?.assay ?? 0) > 0 || (d.interval_tables ?? []).some((t) => t.kind !== "event" && t.row_count > 0));

const cache = new Map<string, Promise<SampleTables>>();

/** The sample tables of a set (built-in «Пробы» and assay tables of their own), cached per set and data version. */
export function loadSampleTables(setId: number, refreshKey = 0): Promise<SampleTables> {
  const key = `${setId}:${refreshKey}`;
  let p = cache.get(key);
  if (!p) {
    p = getSampleTables(setId).catch((e) => {
      cache.delete(key);
      throw e;
    });
    cache.set(key, p);
  }
  return p;
}

export function useSampleTables(setId: number | null, refreshKey = 0): SampleTables | null {
  const [tables, setTables] = useState<SampleTables | null>(null);
  useEffect(() => {
    if (setId === null) {
      setTables(null);
      return;
    }
    let cancelled = false;
    loadSampleTables(setId, refreshKey).then((t) => !cancelled && setTables(t)).catch(() => !cancelled && setTables(null));
    return () => {
      cancelled = true;
    };
  }, [setId, refreshKey]);
  return tables;
}

/** The table a chart or analysis reads: its own choice when the set still has it, else the set's default. */
export function effectiveTable(tables: SampleTables | null, chosen: string | null | undefined): string | null {
  if (!tables) return chosen ?? null;
  return chosen && tables.tables.some((t) => t.key === chosen) ? chosen : tables.default;
}

/** «Таблица проб»: which sample table audit and statistics work on. */
export function SampleTableSelect({ tables, value, onChange, label = "Таблица проб" }: {
  tables: SampleTables | null;
  value: string | null;
  onChange: (key: string) => void;
  label?: string;
}) {
  if (!tables) return null;
  if (!tables.tables.length) return <p className="muted small">В наборе нет таблиц опробования с числами.</p>;
  return (
    <label>{label}
      <select value={effectiveTable(tables, value) ?? ""} onChange={(e) => onChange(e.target.value)} aria-label={label}>
        {tables.tables.map((t) => <option key={t.key} value={t.key}>{t.name} · {t.row_count.toLocaleString("ru-RU")}</option>)}
      </select>
    </label>
  );
}
