/**
 * The field «Фильтр» of a series of a block model (ТЗ 7.30, 7.35 Д-02; ADR 0023 решение 21): the filters of the library
 * (`filtersStore.rows`, the list is read once when the store is empty) or «без фильтра»; a filter the library has lost
 * stays in the list as «Фильтр № N (не найден)» until another is chosen — the core says it in the series. Blocks with
 * f > 0 take part; a rule of a wireframe or a surface works too.
 */
import { useEffect, useSyncExternalStore } from "react";
import { loadFilters } from "../blocks/filters/api";
import { filtersByName, filtersStore, setRows } from "../blocks/filters/state";

export function FilterPick({ value, onChange }: { value: number | null; onChange: (id: number | null) => void }) {
  const get = () => filtersStore.get();
  const { rows, loaded } = useSyncExternalStore(filtersStore.subscribe, get, get);
  useEffect(() => {
    if (!loaded) loadFilters().then(setRows, () => undefined);
  }, [loaded]);
  const list = filtersByName(rows);
  const known = value === null || list.some((f) => f.id === value);
  return (
    <label>
      Фильтр
      <select
        value={value === null ? "" : String(value)}
        aria-label="Фильтр блоков"
        aria-invalid={!known && loaded ? true : undefined}
        title="Фильтр из библиотеки: в расчёт идут блоки, прошедшие его (f > 0), вместе с отбором по полю"
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
      >
        <option value="">без фильтра</option>
        {!known && <option value={String(value)}>{loaded ? `Фильтр № ${value} (не найден)` : `Фильтр № ${value}`}</option>}
        {list.map((f) => (
          <option key={f.id} value={String(f.id)} title={f.title}>
            {f.name}
          </option>
        ))}
      </select>
    </label>
  );
}
