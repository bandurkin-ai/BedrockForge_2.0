/**
 * The window «Тернарная» (ТЗ 8.3; ADR 0019 §4, §11): three numeric fields of a table normalised to their sum, the
 * colour by a numeric or text field; rows with a missing field or a sum ≤ 0 are counted, not drawn. CSV of the
 * shares. An edit computes again 300 ms later.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { errorText } from "../../shell/props";
import type { StatsFields } from "../../modules/charts/api";
import type { TernaryResult } from "../../modules/charts/apiMore";
import type { ChartWindowProps } from "../../modules/charts/bind";
import { useSavedChart } from "../../modules/charts/saved";
import { DEFAULT_TERNARY_VIEW, type TernarySettings } from "../../modules/charts/settings";
import { ChartWindow } from "../ChartWindow";
import { ChartToolbar } from "../core/ChartToolbar";
import { FieldSelect, keepField, TableSelect, useStatsFields } from "../core/Pickers";
import { inlineRasters } from "../core/PointsCanvas";
import css from "../core/Chart.module.css";
import { TernaryChart } from "./TernaryChart";
import { ternaryCalculator, ternaryCsv, ternaryNotes, ternaryRequest } from "./model";

/** The first three components (else numeric fields but the length) for A, B, C. */
export function defaultTriple(f: Pick<StatsFields, "numeric">): [string | null, string | null, string | null] {
  const comps = f.numeric.filter((v) => v.kind === "component").map((v) => v.name);
  const rest = f.numeric.filter((v) => v.kind === "attribute").map((v) => v.name);
  const all = [...comps, ...rest];
  return [all[0] ?? null, all[1] ?? null, all[2] ?? null];
}

export function TernaryWindow(props: ChartWindowProps<"ternary">) {
  const { setId, setName } = props;
  const [s, setS] = useState<TernarySettings>(props.settings);
  const [result, setResult] = useState<TernaryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const set = (patch: Partial<TernarySettings>) => setS((prev) => ({ ...prev, ...patch }));
  const saved = useSavedChart("ternary", props, s, setS);

  const f = useStatsFields(setId, s.table, true);
  useEffect(() => {
    const x = f.fields;
    if (!x) return;
    const [a, b, c] = defaultTriple(x);
    setS((prev) => ({ ...prev, table: prev.table ?? x.table, a: keepField(x, prev.a) ?? a, b: keepField(x, prev.b) ?? b, c: keepField(x, prev.c) ?? c }));
  }, [f.fields]);
  const calc = useMemo(() => ternaryCalculator((r) => (setResult(r), setError(null)), (e) => setError(errorText(e)), setBusy), []);
  useEffect(() => () => calc.stop(), [calc]);
  const request = useMemo(() => ternaryRequest(s), [s]);
  useEffect(() => {
    if (f.fields && request) calc.request(request);
  }, [calc, f.fields, request]);

  const numeric = f.fields?.numeric.filter((v) => v.kind !== "length") ?? [];
  const colorFields = [...numeric.map((v) => v.name), ...(f.fields?.text.map((v) => v.name) ?? [])];
  const settingsForm = (
    <>
      <fieldset>
        <legend>Данные</legend>
        <TableSelect value={s.table} tables={f.fields?.tables ?? []} onChange={(table) => set({ table })} />
        <FieldSelect title="Вершина A" value={s.a} fields={numeric} onChange={(a) => set({ a })} />
        <FieldSelect title="Вершина B" value={s.b} fields={numeric} onChange={(b) => set({ b })} />
        <FieldSelect title="Вершина C" value={s.c} fields={numeric} onChange={(c) => set({ c })} />
        <label>
          Цвет по полю
          <select value={s.color ?? ""} aria-label="Цвет по полю" onChange={(e) => set({ color: e.target.value || null })}>
            <option value="">нет</option>
            {colorFields.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label className={css.check}>
          <input type="color" value={s.view.color} aria-label="Цвет точек" onChange={(e) => setS((p) => ({ ...p, view: { ...p.view, color: e.target.value } }))} /> Цвет точек
        </label>
      </fieldset>
    </>
  );
  const fileName = `Тернарная ${setName} ${[s.a, s.b, s.c].filter(Boolean).join(" ")}`.trim();
  const toolbar = <ChartToolbar svgRef={svgRef} fileName={fileName} disabled={!result?.a.length} prepare={inlineRasters} csvLabel="CSV долей" csv={() => (result ? ternaryCsv(result) : null)} onReset={() => setS((p) => ({ ...p, view: { ...DEFAULT_TERNARY_VIEW } }))} />;
  const empty = !f.fields ? "Расчёт…" : numeric.length < 3 ? "Нужно три числовых поля." : !request ? "Выберите три поля." : result && !result.a.length ? "Нет проб с тремя значениями и суммой > 0." : "Расчёт…";
  return (
    <ChartWindow
      settings={settingsForm}
      toolbar={toolbar}
      busy={busy}
      error={error ?? f.error}
      empty={empty}
      note={saved.note}
      chart={result && result.a.length > 0 && <TernaryChart ref={svgRef} result={result} color={s.view.color} />}
      footer={result && <p className={css.muted}>{ternaryNotes(result).join(" · ")}</p>}
    />
  );
}
