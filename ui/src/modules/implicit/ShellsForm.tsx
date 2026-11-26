/**
 * The window «Оболочки содержаний» (ТЗ 8.9.2, 8.9.4; ADR 0026 решение 16): the values (the composites of a result or the samples of a table, the
 * field), the transform (linear / logarithm / normal scores), the kind (numeric / indicator, p 0,3–0,7) and the thresholds; the shell of the
 * threshold k lies inside the shell of the threshold k − 1 (the line under the thresholds shows the nesting the core will build). «Сохранить»
 * runs `implicit.shells`; the group is added last or the chosen one is changed.
 */
import { useEffect, useState } from "react";
import css from "../../shell/PropsParts.module.css";
import { errorText } from "../../shell/props";
import { loadFields, loadResults, loadSets, loadTables, type FieldRow, type ResultRow, type SetRow, type TableRow } from "../composites/api";
import { DataCheck, FieldIssue, GroupFrame, TextRow } from "./GroupParts";
import { groupRanges } from "./groupBase";
import { shells } from "./groupKinds";
import { useLatest, useSel } from "../estimation/hooks";
import { implicitStore } from "./state";
import { MODE_TITLES, MODES, parseThresholds, shellTitle, SOURCE_TITLES, SOURCES, TRANSFORM_TITLES, TRANSFORMS, type ShellForm, type ShellMode, type ShellSource, type Transform } from "./ShellsState";
import { wizardStore } from "./wizardState";

/** The tables a sample can come from: not the collars and the surveys. */
const SAMPLE_KINDS = new Set(["assay", "interval", "event", "composite"]);

/** «g ≥ 0,5 ⊃ g ≥ 1 ⊃ g ≥ 2,5»: the nesting of the shells by the typed thresholds; null — nothing to show yet. */
export function nestingLine(f: Pick<ShellForm, "thresholds" | "transform">): string | null {
  const th = parseThresholds(f.thresholds);
  if (!th.ok || th.values.length < 2) return null;
  return th.values.map((t) => shellTitle(t, f.transform)).join(" ⊃ ");
}

export function ShellsForm({ winId }: { winId: string }) {
  const s = useSel(shells.store, (x) => x);
  const f = s.form;
  const info = useSel(implicitStore, (x) => (s.modelId === null ? undefined : x.details[s.modelId]));
  const defaults = useSel(wizardStore, (x) => x.defaults.data);
  const ranges = groupRanges(defaults);
  const modelSet = ((info?.meta.data as { set_id?: unknown } | null | undefined)?.set_id ?? null) as number | null;
  const setId = f.setId ?? modelSet;
  const [error, setError] = useState<string | null>(null);
  const fail = (e: unknown) => setError(errorText(e));
  const sets = useLatest<SetRow[]>(loadSets, [], fail);
  const results = useLatest<ResultRow[]>(f.source === "composites" && setId !== null ? () => loadResults(setId) : null, [f.source, setId], fail);
  const tables = useLatest<TableRow[]>(f.source === "table" && setId !== null ? () => loadTables(setId) : null, [f.source, setId], fail);
  const resultRow = results?.find((r) => r.id === f.resultId) ?? null;
  const tableKey = f.source === "composites" ? (resultRow?.table_id !== null && resultRow?.table_id !== undefined ? String(resultRow.table_id) : null) : f.table;
  const tableKnown = f.source === "table" ? !!tables?.some((t) => t.table === f.table) : tableKey !== null;
  const fields = useLatest<FieldRow[]>(setId === null || !tableKey || !tableKnown ? null : () => loadFields(setId, tableKey), [setId, tableKey, tableKnown], fail);
  const numeric = (fields ?? []).filter((x) => x.kind === "number");
  const nesting = nestingLine(f);
  const set = (patch: Partial<ShellForm>) => shells.setForm(patch);

  // a stored shell of the composites knows its result, not its set: the set is found among the results of the sets
  useEffect(() => {
    if (f.source !== "composites" || f.resultId === null || f.setId !== null || !sets) return;
    let alive = true;
    void Promise.all(sets.map((x) => loadResults(x.id).then((rows) => (rows.some((r) => r.id === f.resultId) ? x.id : null)).catch(() => null))).then((found) => {
      const id = found.find((x): x is number => x !== null);
      if (alive && id !== undefined) shells.set({ form: { ...shells.store.get().form, setId: id } });
    });
    return () => {
      alive = false;
    };
  }, [sets, f.source, f.resultId, f.setId]);

  return (
    <GroupFrame rt={shells} winId={winId} intro="Оболочка порога k вложена в оболочку порога k − 1: ядро пересекает их. Новая группа добавляется последней.">
      <fieldset className={css.group} data-testid="grp-name-box">
        <legend>Группа</legend>
        <TextRow rt={shells} label="Название" value={f.name} onChange={(v) => set({ name: v })} id="name" at="name" placeholder="Оболочка N" />
      </fieldset>

      <fieldset className={css.group} data-testid="grp-values">
        <legend>Значения</legend>
        {error && (
          <p className={css.error} role="alert" data-testid="grp-data-error">
            {error}
          </p>
        )}
        <label className={css.formRow}>
          <span>Источник</span>
          <select className={css.input} value={f.source} aria-label="Источник значений" data-testid="grp-source" onChange={(e) => set({ source: e.target.value as ShellSource, resultId: null, table: null, field: null })}>
            {SOURCES.map((x) => (
              <option key={x} value={x}>
                {SOURCE_TITLES[x]}
              </option>
            ))}
          </select>
        </label>
        <FieldIssue rt={shells} at="params.source" id="source" />
        <label className={css.formRow}>
          <span>Набор скважин</span>
          <select className={css.input} value={setId ?? ""} aria-label="Набор скважин" data-testid="grp-set" onChange={(e) => set({ setId: e.target.value ? Number(e.target.value) : null, resultId: null, table: null, field: null })}>
            <option value="">{sets ? "— выберите набор —" : "Чтение…"}</option>
            {(sets ?? []).map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
        </label>
        <FieldIssue rt={shells} at="params.set_id" id="set" />
        {f.source === "composites" ? (
          <>
            <label className={css.formRow}>
              <span>Результат</span>
              <select className={css.input} value={f.resultId ?? ""} aria-label="Результат обработки проб" data-testid="grp-result" disabled={setId === null} onChange={(e) => set({ resultId: e.target.value ? Number(e.target.value) : null, field: null })}>
                <option value="">{setId === null ? "—" : results ? (results.length ? "— выберите результат —" : "в наборе нет результатов обработки") : "Чтение…"}</option>
                {(results ?? []).map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name} ({r.op_title}){r.stale ? " — устарел" : ""}
                  </option>
                ))}
              </select>
            </label>
            <span className={css.note}>Композиты — результат инструмента «Композиты» набора скважин.</span>
            <FieldIssue rt={shells} at="params.result_id" id="result" />
          </>
        ) : (
          <>
            <label className={css.formRow}>
              <span>Таблица проб</span>
              <select className={css.input} value={tableKnown ? f.table! : ""} aria-label="Таблица проб" data-testid="grp-table" disabled={setId === null} onChange={(e) => set({ table: e.target.value || null, field: null })}>
                <option value="">{setId === null ? "—" : tables ? "— выберите таблицу —" : "Чтение…"}</option>
                {(tables ?? []).filter((t) => SAMPLE_KINDS.has(t.kind) || t.table === f.table).map((t) => (
                  <option key={t.table} value={t.table}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
            <FieldIssue rt={shells} at="params.table" id="table" />
          </>
        )}
        <label className={css.formRow}>
          <span>Поле содержания</span>
          <select className={css.input} value={f.field && numeric.some((x) => x.name === f.field) ? f.field : ""} aria-label="Поле содержания" data-testid="grp-field" disabled={!tableKnown} onChange={(e) => set({ field: e.target.value || null })}>
            <option value="">{tableKnown ? (fields ? "— выберите поле —" : "Чтение…") : "—"}</option>
            {numeric.map((x) => (
              <option key={x.key} value={x.name}>
                {x.name}
                {x.unit ? `, ${x.unit}` : ""}
              </option>
            ))}
          </select>
        </label>
        <FieldIssue rt={shells} at="params.field" id="field" />
      </fieldset>

      <fieldset className={css.group} data-testid="grp-thresholds-box">
        <legend>Пороги и вид</legend>
        <label className={css.formRow}>
          <span>Преобразование</span>
          <select className={css.input} value={f.transform} aria-label="Преобразование значений" data-testid="grp-transform" onChange={(e) => set({ transform: e.target.value as Transform })}>
            {TRANSFORMS.map((x) => (
              <option key={x} value={x}>
                {TRANSFORM_TITLES[x]}
              </option>
            ))}
          </select>
        </label>
        <span className={css.note}>Логарифм — для значений больше нуля; нормальные оценки приводят значения к нормальному виду (пороги задаются в исходных единицах).</span>
        <FieldIssue rt={shells} at="params.transform" id="transform" />
        <label className={css.formRow}>
          <span>Вид оболочки</span>
          <select className={css.input} value={f.mode} aria-label="Вид оболочки" data-testid="grp-mode" onChange={(e) => set({ mode: e.target.value as ShellMode })}>
            {MODES.map((x) => (
              <option key={x} value={x}>
                {MODE_TITLES[x]}
              </option>
            ))}
          </select>
        </label>
        <FieldIssue rt={shells} at="params.mode" id="mode" />
        <TextRow rt={shells} label="Пороги" value={f.thresholds} onChange={(v) => set({ thresholds: v })} id="thresholds" at="params.thresholds" placeholder="0,5; 1; 2,5" note="От 1 до 20 порогов по возрастанию, через «;» — в единицах поля." />
        {nesting && (
          <p className={css.note} data-testid="grp-nesting">
            Вложенность: {nesting}
          </p>
        )}
        {f.mode === "indicator" && <TextRow rt={shells} label="Вероятность p" value={f.p} onChange={(v) => set({ p: v })} id="p" at="params.p" placeholder="0,5" range={ranges.p} note="Выше p — оболочка уже; ниже — шире." />}
        <DataCheck rt={shells} />
      </fieldset>
    </GroupFrame>
  );
}
