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
