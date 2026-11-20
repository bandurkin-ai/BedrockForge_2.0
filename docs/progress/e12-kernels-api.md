# Э12 — интерфейсы ядер для заданий 12.21 (временная справка оркестратора; удалить в 12.36)

## Урезка — `composites/topcut.py` (12.8, 9b70bce)
- `TopcutParams.of(body)`: `value`, иначе `thresholds` («КОД=значение; …», dict или `[{domain, value}]`), иначе `percentile` 90–99,99 (по умолчанию 99).
- `topcut(values, lengths, p, codes=, names=, field=, tick=)` → `.cut`, `.flag` (1 / 0 / NaN), `.thresholds {домен: T | None}`, `.report`; имена колонок — `column_names(field)`.
- Отчёт: `field, columns, mode, percentile, domains[], total, summary{before, after}, notes[]`; домен — `domain, threshold, n, n_cut, cut_pct, length, mean_before/after, cv_before/after, max_before/after, loss_pct`.
- `topcut_analysis(values, lengths, codes=, names=, field=, percentiles=None, decile_limit=40, percentile_limit=10, tick=)` → по домену `curves{p, threshold, mean, cv, loss_pct, n_cut}` (P 90,0…99,9), `deciles[10]`, `top[10]`, `top_decile_pct`, `top_percentile_pct`, `flag_decile`, `flag_percentile`, `quantiles{p, z, value}`.

## Эквивалент — `composites/equivalent.py` (12.8)
- `EquivalentParams.of(body, units={поле: единица})` → `.fields` (основной первым), `.name`, `.factors`; `equivalent([g1, g2, …], p, tick=)` → `.values`, `.report` (`factors[{field, factor, k, unit, price_unit, price, recovery, text}]`, `n, n_missing, n_zeroed`).
- Единица содержания: из запроса, иначе из единицы поля, иначе `unit_unknown`. Цена по умолчанию: % и кг/т — $/т, прочее — $/унц; `base_recovery` 0,9. `nan_zero`: проба без единого содержания — NaN, не 0.

## Декластеризация — `stats/declus3d.py`, `stats/declustering.py` (12.9, c0c9855)
- `DeclusParams.of(body)` → `declus_weights(x, y, z, v, p, threads=, tick=)` → `.weights` (NaN без координат или значения), `.report` (`n, n_missing, naive_mean, mean, uniform, best_size, sizes, means, min/max_weight, n_zero, notes`); x, y, z — середины интервалов.
- Ошибка — `stats.weighted.StatsError` (`code, message, hint, param, as_dict()`); `jobs.as_api` должен её знать (одна строка).
- Прогрев: `stats.weighted.warm()`, `stats.declus3d.warm()` — из `composites/warm.py`.

## Окно «Рудные интервалы (ГКЗ)» (12.30, 7972bae) — контракт для 12.22–12.23
- Задание: `POST /composites/ore` `{set_id, table, field, cutoff, m_min, m_waste_max, mc_min?, c_min?, mode, missing, missing_max?, thickness:{source:"length"|"field", field?}, domain?:{table,field}|{wireframe_ids}, contacts_values?:int[], name?}` → `{source, id}`; 409 `table_exists` → повтор с `replace: true`. Пустые `mc_min`/`c_min` не шлются (ядро: Сб·Мрт и Сб).
- Результат задания: `{set_id, table_id, name, chart_id?}`; окно открывает таблицу и включает отображение в 3D; без `chart_id` ищет отображение «Рудные интервалы» среди отображений набора.
- Предпросмотр `POST /composites/ore/preview {params, hole_id}` и `GET /composites/results/{id}/protocol?hole=<имя>` — одна форма: `{hole, unit, samples{from,to,values{<поле>, ORE_CLS (имена классов), ORE_ID}}, result{from,to,values{CLASS (имена), NESTED, M, C, MC, BASIS, ORE_ID, N_SAMPLES, WASTE_LEN, FLAG, LABEL}}, protocol{events:[OreEvents.records() без hole]}, summary, set_summary|null, notes[]}`; `row0`/`row1` событий — индексы проб скважины с 0; `summary` — `ore_set._summary` как есть; у GET `summary` может не быть; 409 `stale` — текстом.
- Экспорт: `POST /composites/results/{id}/protocol {format:"xlsx"|"csv", path, holes?, overwrite?}` → `{source, id}`; 409 `files_exist {files}` → «Заменить?» и `overwrite: true`.
- 12.31: `tools/conditions.ts::finish` должен звать `oreFinish` из `tools/ore.ts`.

## Окна «Анализ кондиций» и ручная правка (12.31, 07bb9fa) — контракт для 12.23
- Анализ: `POST /composites/conditions {set_id, table, field, cutoffs[], m_min[], m_waste_max[]` (без повторов, по возрастанию)`, mode, missing, missing_max?, thickness{source, field?}, domain?, contacts_values?, exclude?{field, values: string[]}}` → `{source, id}`. `exclude.values` — строки как набраны; ядро сравнивает как текст, у числового поля — по значению. > 200 вариантов окно не запускает.
- Варианты окно ищет в результате задания (`variants` или `rows`), иначе в `dh_results.report.variants|rows` последнего результата op `conditions` с теми же `params.table`, `params.field`. Строка: `{cutoff, m_min, m_waste_max, count, m_sum, m_mean?, c_mean?, mc_sum}`, `unit?` рядом; пропуск — null.
- Правка: `POST /composites/results/{id}/edit {hole, action, from, to}`: `split` — FROM интервала и глубина разреза; `merge` — FROM верхнего и TO нижнего; `move` — нынешняя граница и новая глубина (сверить с `ore_edit.py`). Ответ `{rows?: {from, to, values{CLASS, NESTED, MANUAL…}}, violations: [string | {code, message, hint?, from?, to?}], report{edits[]}}`.
- Правка читает скважину через `GET …/protocol?hole=` и закрыта у устаревшего результата → после правки результат остаётся свежим; `MANUAL` — в `result.values` протокола.

## Ядра протокола, кондиций, правки (12.15–12.17: 04be900/3180913, 5654a38, 30d7bfd) — для 12.23
- Протокол: `ore_protocol.hole_protocol(lines, params, hole=0, *, unit="", edits=None)` → `HoleProtocol.as_dict()` = `{hole, unit, algo, mode, rows[{sample|None, from, to, l, g, line, state, cls, ore_id, note}], columns[{key, title, hint, lanes}], cells[{col, lane, row0, row1, text, tone, from, to, m, c, mc, ore, decision, event, manual}], events[…, n, mark, text; row0/row1 — от первой пробы скважины], intervals[строки + manual], stats, edits, violations}`; `.grid()` → `{head, body, tones, merges}` — готовый лист «Скв. …». Длинная форма: `long_form(lines, params, *, holes=None, edits={индекс скважины: [правки]}, tick=None, chunk=100_000)` → блоки; `.rows(names)` под `LONG_HEAD` (16 колонок), `.records()`. 5 млн проб — 5,5 млн записей, 3,2 с ядро / 14 с с `rows()`, ≤ 0,4 ГБ.
- Кондиции: `ore_conditions.conditions_analysis(lines, cutoffs, m_min, m_waste_max, *, mode, missing, missing_max, contacts_values, mc_min=None, c_min=None, exclude=маска, threads, tick)` → `Conditions(.variants, …, .as_dict())`; строка `{cutoff, m_min, m_waste_max, mc_min, c_min, count, m_sum, m_mean, c_mean, mc_sum, step_pct, c_trim, step_flag, mark}`; `exclusion(column, values)` → маска; `MAX_VARIANTS`. 12 вариантов × 5 млн — 18,3 с / 1 поток, 8,9 с / 4.
- Правка: `ore_edit.edit_hole(lines, params, edits, hole=0)` → `HoleEdit(.intervals, .manual, .made, .violations[{code, message, hint, from, to}], .edits, .rows(), .columns(unit), .events())`; `edits` — ВСЕ правки скважины по порядку (сохранённые + новая), ядро применяет заново; отказ — `OreError("ore_edit_refused")`, ничего не сохранять. Нарушения: `not_k`, `layer`, `trim`, `join`. Протокол результата с правками — передавать `edits` в `hole_protocol` / `long_form`.
