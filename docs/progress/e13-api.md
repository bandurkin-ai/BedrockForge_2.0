# Э13 — контракт API графиков для интерфейса (временная справка; удаляется в 13.38)

Источник: ADR 0019 п. 9–10; ядро — `core/bf_core/stats/routes_charts.py` (13.15). Все пути — от `/api/v1`.
Неопределённое число в ответе — `null` (не NaN и не 0).

## Ошибки

`{code, message, hint, param?}`, тексты по-русски.

| Статус | code | Когда |
|---|---|---|
| 422 | `bad_param` + `param` | неверная настройка: `set_id`, `v`, `weight`, `weight_field`, `negatives`, `bottom_cut`, `top_cut`, `log`, `bins`, `bin_width`, `start`, `end`, `distribution`, `quantiles`, `y_field`, `holes`… |
| 422 | `field_not_found` / `field_not_number` + `param` | поля нет в таблице / поле текстовое (`param`: `field`, `y_field`, `weight_field`) |
| 422 | `no_values` | после отбора не осталось значений (подсказка называет причины) |
| 422 | `too_many_bins` + `param` | больше 1000 бинов (`bins` или `bin_width`) |
| 422 | `too_few_values`, `no_spread` | Q-Q: меньше двух значений (> 0 для логнормального) / нет разброса |
| 422 | `too_many_series` + `param` | настройки не проходят `charts.kinds.check` |
| 422 | `chart_not_computed` + `param: "kind"` | `/stats/chart/{id}` для вида, который так ещё не считается |
| 404 | `set_not_found`, `table_not_found`, `chart_not_found` | |

## Общие ключи тела (= `charts.settings` без `view`)

`v` (можно не передавать; иначе 1), `set_id` (обязателен), `table` (`name_key` или id; `null` — таблица проб
набора), `field` (имя без учёта регистра; `null` — первый компонент таблицы), отбор `weight` (`length` по
умолчанию), `weight_field`, `negatives`, `exclude_zero`, `bottom_cut`, `bottom_mode`, `top_cut`, `top_mode`,
`holes` (имена скважин; пусто — все; неизвестные имена не мешают). `view` ядро не читает. Данные читаются заново
при каждом расчёте: правка пробы меняет ответ (Т7.28-1).

`counts` (отбор): `{rows, missing, bad_weight, negative, zero_excluded, bottom_capped, bottom_excluded, capped,
top_excluded, used}`; `rows` — строки таблицы (после `holes`).

`stats` (Describe): `{count, weight_sum, min, max, mean, median, std, variance, cv, skewness, percentiles {"5",
"10", "25", "50", "75", "90", "95", "97.5", "99"}, log {count, mean_ln, variance_ln, geometric_mean} | null}`.

## `POST /stats/histogram`

Тело: общие ключи + `log`, `bin_width`, `bins`, `start`, `end`.

```
{ field, set_id, log, weighted,
  counts: {…counts, nonpositive_log},
  stats, stats_unweighted: Describe | null,          // null, когда weight = "none"
  bins: { edges[n+1], counts[n], weights[n], percent[n], cumulative_percent[n], width,
          below {count, weight}, above {count, weight}, nonpositive {count, weight} },
  conventions: "…" }
```

## `POST /stats/qq`

Тело: общие ключи + `distribution` (`lognormal` по умолчанию, `normal`, `sample`), `quantiles` (3…999, 99),
`y_set_id` (`null` — тот же набор), `y_table` (`null` — таблица проб набора Y), `y_field`, `y_holes`; `log` —
вид окна, ядро не читает. Сторона Y берёт тот же отбор (поле веса — из таблицы Y).

С распределением:
```
{ field, set_id, y_field: null, y_set_id: null, mode: "theory",
  x: {weighted, counts, stats}, y: null,
  points: [[theory, value, p%]] (≤ 2000), count,
  distribution: {name, mu, sigma, dropped_nonpositive}, ppcc | null,
  marks: [[x, y, p%]] (P10, P50, P90) }
```
Две выборки (`distribution: "sample"`):
```
{ field, set_id, y_field, y_set_id, mode: "sample",
  x: {weighted, counts, stats}, y: {weighted, counts, stats},
  points: [[x, y, p%]] (quantiles штук), ratio: [y / x | null], extremes: {x: [min, max], y: [min, max]},
  ks, marks: [[x, y, p%]] }
```

## `POST /stats/dendrogram`

Тело: `v`, `set_id`, `table`, `fields` (имена без учёта регистра; пусто или `null` — все компоненты таблицы, у таблицы
без компонентов — её числовые поля, кроме `length`; поле дважды — один раз), `method` (`pearson` / `spearman`), `log`
(`true`), `weight` (`length`), `weight_field`, `negatives` (`half_abs`), `exclude_zero`, `missing` (`pairwise` /
`complete`), `linkage` (`average`, `complete`, `single`, `weighted`; Ward — 422 `bad_param`), `distance` (`1-r` /
`1-abs`; `1-|r|` тоже читается), `cut` (`null` — без групп), `holes`. Отсечки к дендрограмме не применяются.

```
{ set_id, fields[k], units[k] (null — неизвестна),
  correlation[k][k] (null — не определено), pairs[k][k], rows, used, nonpositive_log,
  linkage [[a, b, height, size]], order[k], leaves[k], icoord[k-1][4], dcoord[k-1][4], max_distance,
  cut | null, groups[k] | null, link_groups[k-1] (null — выше порога) | null, group_count | null,
  conventions,
  spec {method, log, missing, negatives, exclude_zero, weighted, linkage, distance},
  counts {rows, empty, bad_weight, incomplete, used},
  field_counts [{name, missing, negative, zero_excluded, nonpositive, used}] }
```
Отказы: 422 `bad_param` {`fields` (меньше двух полей), `method`, `log`, `missing`, `negatives`, `linkage`,
`distance`, `cut`, `weight`, `weight_field`}; `field_not_found` / `field_not_number` {`fields`, `weight_field`};
`too_few_values` / `no_spread` {`fields`} — сообщение называет поля.

## `POST /stats/chart/{chart_id}`

Тело пустое. Расчёт сохранённого графика по его настройкам и текущим данным; набор — тот, в котором лежит
график. Ответ — ответ его вида (как выше) + `chart: {id, kind, name, version}`.
