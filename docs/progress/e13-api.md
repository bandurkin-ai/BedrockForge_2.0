# Э13 — контракт API графиков для интерфейса (временная справка; удаляется в 13.38)

Источник: ADR 0019 п. 9–10; ядро — `core/bf_core/stats/routes_charts.py` (13.15), `routes_more.py` (13.16; форма —
`ui/src/modules/charts/apiMore.ts`). Все пути — от `/api/v1`.
Неопределённое число в ответе — `null` (не NaN и не 0).

## Ошибки

`{code, message, hint, param?}`, тексты по-русски.

| Статус | code | Когда |
|---|---|---|
| 422 | `bad_param` + `param` | неверная настройка: `set_id`, `v`, `weight`, `weight_field`, `negatives`, `bottom_cut`, `top_cut`, `log`, `bins`, `bin_width`, `start`, `end`, `distribution`, `quantiles`, `y_field`, `holes`… |
| 422 | `field_not_found` / `field_not_number` / `field_not_text` + `param` | поля нет в таблице / поле текстовое / поле числовое (`param`: `field`, `y_field`, `weight_field`, `fields`, `x`, `y`, `a`, `b`, `c`, `domain`, `color`) |
| 422 | `no_positive` (+ `param: "log"` у ящиков) | лог-шкала / лог-вероятностный без значений > 0 |
| 422 | `too_many_domains` + `param` | > 300 доменов с пробами (`domain`) / значений текстового поля цвета (`color`) |
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

## Графики 8.3 (13.16): общее

`field` / `x` / `y` / `a` / `b` / `c`: `null` — первый компонент таблицы (иначе первое числовое, кроме `length`),
уже занятые поля пропускаются (`y` после `x` той же таблицы; `a`, `b`, `c` — по порядку). `domain` — текстовое
поле; `domain_table: null` — та же таблица, иначе другая интервальная таблица набора: домен пробы — интервал
[FROM; TO), в который попадает её середина (`source.domain_at`; `domain_outside` — строки без такого интервала).
`color` — числовое или текстовое поле той же таблицы (текстовое — ≤ 300 значений). `holes` — как у гистограммы.
`row` — `dh_rows.id`. Коды домена другой таблицы и Y по ключу пробы хранятся в кеше колонок (по `rev` обеих таблиц).
Счётчики по доменам `DomainCounts` = `counts` отбора + `no_domain` (+ `nonpositive_log` у ящиков и
лог-вероятностного). Группа `role`: `domain`; `none` — строки без домена (`label: null`, последней); `all` — без
поля доменов (одна группа, `label: null`).

## `POST /stats/scatter`

Тело: `v`, `set_id`, `table`, `x`, `y`, `y_set_id` (`null` — тот же набор), `y_table` (`null` — таблица X; иначе
строки сопоставляются по ключу пробы «скважина, FROM, TO»; график двух наборов лежит в наборе X), `color`, `log_x`,
`log_y` (`false`), `weight` (`length`), `weight_field`, `negatives` (`keep`), `exclude_zero`, `regression`
(`ols` / `rma` / `weighted` / `none` — только вид, ядро считает все три), `holes`. Правила `negatives` и
`exclude_zero` — к обоим полям (пара с исключённым значением не рисуется); отсечки к рассеянию не применяются.
```
{ set_id, y_set_id, x_field, y_field, color_field | null, log_x, log_y, weighted,
  counts {rows, missing_x, missing_y, unmatched, bad_weight, negative, zero_excluded, nonpositive_log, used},
  mode: "points" | "density",
  // points (≤ 200 000 пар): x[], y[] (данные), row[], c[] | null, color | null
  //   color: {kind: "number", min, max} | {kind: "text", labels[]} (c — индекс в labels, null — пусто)
  // density (> 200 000): density {nx: 100, ny: 100, x: [lo, hi], y: [lo, hi] (данные), counts[iy·nx + ix]
  //   (от левого нижнего угла; ячейки равны в пространстве осей), max}; c: null, color: null; ключей x/y/row нет
  pearson | null, spearman | null, n, weight_sum, weighted_pearson | null,
  fits {ols {a, b} | null, rma {a, b} | null, weighted {a, b} | null},   // y = a + b·x в пространстве осей
  x_stats {mean, std, min, max}, y_stats {…},  // mean, std — без весов, в пространстве осей (log10 на лог-оси);
                                               // min, max — в данных
  conventions }
```
Счётчики делят строки: `rows = missing_x + unmatched + missing_y + bad_weight + zero_excluded (+ negative при
"exclude") + nonpositive_log + used`; `unmatched` — строки с X без пробы того же ключа в таблице Y.

## `POST /stats/box`

Тело: общие ключи отбора + `field`, `domain`, `domain_table`, `log` (`false`), `holes`.
```
{ set_id, field, domain | null, domain_table | null, domain_outside, log, weighted,
  counts {…counts, no_domain, nonpositive_log},
  groups [{label | null, role, n, weight_sum, mean (взвешенное арифметическое), geometric_mean | null (log),
           q1, median, q3, whisker_low, whisker_high, min, max, nonpositive, outliers[] (показанные),
           outliers_low, outliers_high, outliers_total}],
  outliers_shown, outliers_total, conventions }
```
На лог-шкале квартили и усы — по log10, в данных; `min`, `max` — по значениям > 0.

## `POST /stats/probplot`

Тело: как у ящиков без `log`.
```
{ set_id, field, domain | null, domain_table | null, domain_outside, weighted,
  counts {…counts, no_domain, nonpositive_log},
  series [{label | null, role, points [[value, P %]] (≤ 2000, крайние остаются), count (> 0), nonpositive,
           thinned, r | null, stats {mean, median, cv | null, p90}}],
  conventions }
```
`stats` — всех проб домена после отбора (с весами), не только > 0.

## `POST /stats/domains`

Тело: как у лог-вероятностного.
```
{ set_id, field, domain | null, domain_table | null, domain_outside, weighted,
  counts {…counts, no_domain},
  rows [{label | null, role, n, length, weight_sum, mean, decl_mean | null, median, std, cv | null, min, max, p10,
         p50, p90}],
  total {label: null, role: "all", …},           // «Итого», Σ n = counts.used
  decl_field: "DECL_W" | null, decl_hint | null, notes[], conventions }
```

## `POST /stats/ternary`

Тело: `v`, `set_id`, `table`, `a`, `b`, `c`, `color`, `holes`.
```
{ set_id, fields [a, b, c], color_field | null,
  counts {rows, missing, nonpositive_sum (= negative + zero_sum: не рисуются), negative, zero_sum, used},
  a[], b[], c[] (доли, a + b + c = 1), row[],          // ≤ 200 000; больше — равномерно прорежено по строкам
  total (= counts.used), color_values[] | null (текст — индекс в labels, null — пусто), color | null,
  conventions }
```

## `POST /stats/chart/{chart_id}`

Тело пустое. Расчёт сохранённого графика по его настройкам и текущим данным; набор — тот, в котором лежит
график. Считаются `histogram`, `qq`, `dendrogram`, `scatter`, `box`, `probplot`, `domains`, `ternary`. Ответ —
ответ его вида (как выше) + `chart: {id, kind, name, version}`.

## Выделение проб (13.19; ADR 0019 §8)

Ядро — `stats/selection.py`, `stats/routes_selection.py`; фильтр таблицы — `drillholes/router.py`. Выделение —
строки одной таблицы одной ревизии (`rev`), ≤ 200 000; ядро держит последние 16 (LRU). Чужие id не мешают
(`unknown`). Правка или замена таблицы — каждый запрос с жетоном отвечает 409 `selection_stale {token}` (окно
снимает выделение с сообщением).

`POST /stats/selection` — одно из четырёх тел:
```
{set_id, table, rows: [dh_rows.id]}                       // точки графика (рассеяние, тернарная): row[] ответа
{chart: {kind: "scatter", …настройки рассеяния}, polygon: [[x, y], …]}   // режим плотности: строки выбирает ядро;
{chart: {kind: "scatter", …}, rect: [x0, x1, y0, y1]}     //   координаты в данных, на лог-оси — > 0, проверка в log10
{chart: {kind: "box" | "probplot", …настройки}, range: [lo, hi], groups: [label | null] | null}
        // пробы групп (null — без домена или единственная группа графика без доменов; null / [] — все группы),
        // значение которых после отбора — в [lo; hi] (границы входят; на лог-шкале ящиков и у лог-вероятностного
        // — только > 0)
{set_id, table, filter: {where: str | null, hole: dh_holes.id | null}}   // «наоборот»: строки фильтра «Таблицы»
→ {token, set_id, table_id, table_name, rev, n, unknown, source: "rows" | "scatter" | "box" | "probplot" | "table"}
```
`table` — `name_key` или id (`null` — таблица проб набора); у `chart` таблица — таблица X (`table` настроек),
`holes` настроек действуют. `GET /stats/selection/{token}` → `{…то же, rows: [dh_rows.id по возрастанию]}`
(отметить точки на открытых графиках). `DELETE /stats/selection/{token}` → `{deleted}`.

`GET /stats/selection/{token}/segments?method=minimum_curvature` — BFA1 `selection_segments`: заголовок `{kind,
format: 1, token, set_id, table_id, rev, method, n, segments, no_position (скважина без траектории), origin [x, y,
z] (целые метры, середина габарита), bbox [6] | null}`; массивы `row u4` (dh_rows.id; f8 после 2³²), `hole u4`
(dh_holes.id), `from f8`, `to f8`, `a f4×3`, `b f4×3` — начало и конец интервала на траектории от `origin`;
порядок — скважина по имени, FROM. 200 тыс. строк: выделение фильтром + отрезки 0,14 с, 9,6 МБ.

`GET /drillholes/sets/{id}/tables/{table}?selection=<token>` — страница только строк выделения (вместе с `where`,
`hole`, `sort`); в ответе + `selection {token, n}`.

Отказы: 422 `too_many_rows {param: rows | polygon | rect | range | filter}` (> 200 000), `bad_param {param: rows,
chart, polygon, rect, range, groups, filter, where, hole, table, selection}` (`selection` — жетон другой таблицы),
ошибки настроек графика (`field_not_found {x}` …), `bad_expression`, `bad_method`; 404 `selection_not_found`;
409 `selection_stale {token}`, `table_changed`.

## Вкладка «Статистика» (13.17; ADR 0019 §7): `POST /stats/tab/declus`, `/nscore`, `/backtr`, `/bootstrap`

Ядро — `stats/routes_tab.py` → `stats/tab.py`. Ничего не сохраняется. Точки — интервалы со значением и
положением: середина пробы на траектории, в плане (`source.midpoints`). `rows` — dh_rows.id точек.

Общие ключи тела: `set_id`, `table` (`name_key` или id; `null` — таблица проб), `field` (`null` — первый
компонент), `holes` (имена; пусто — все), `declus` — параметры декластеризации `{method: "cell" | "polygon",
cmin, cmax, ncell, noff, minmax ("min" | "max") | nx, ny, bounds [xmin, xmax, ymin, ymax]}` (имена
`composite.declus`; пустые — по умолчанию: ячейки 2–50 % габарита, 20 размеров, 8 сдвигов, min; полигоны — сетка
по числу скважин, границы — габарит точек). У `/declus` — что считать (`null` — ячейки по умолчанию), у остальных
— веса значений (`null` — без весов).

```
POST /stats/tab/declus      {…общие, weights: false}
→ { set_id, table (имя), field, method, count, naive_mean, declustered_mean, weight_min, weight_max, zero_weights,
    uniform, best_cell_size | null, cell_sizes [0, …] | null, means [plain, …] | null,   // кривая; null — полигоны
    cmin, cmax, ncell, noff, minmax | nx, ny, bounds [4],
    notes [], counts {rows, missing, no_position, used},
    rows [dh_rows.id], weights [] }                         // только при weights: true; Σ weights = count
POST /stats/tab/nscore      {…общие, zmin, zmax}
→ { set_id, table, field, count, values [], scores [] (таблица преобразования по возрастанию), table_rows (её строк;
    < count при весах 0), zmin, zmax, rows [dh_rows.id], sample_scores [] (оценка каждой точки), weighted, counts }
POST /stats/tab/backtr      {…общие, zmin, zmax, scores [1…20 000], ltail, utail ("linear" | "power" | "hyperbolic"
                             …, как Э12), ltpar, utpar}
→ { set_id, table, field, values [число | null] }           // null — у хвоста там нет числа
POST /stats/tab/bootstrap   {…общие, n_boot (1000), seed (null — случайное, возвращается), confidence (0.95)}
→ { set_id, table, field, count, n_boot, seed, confidence, weighted,
    statistics {mean | median | std | cv | p10 | p90: {observed, observed_weighted | null, defined, mean, std,
                ci_low, ci_high, edges [], counts []}},     // null — не определено ни в одной реализации
    counts }
```
Отказы: 422 `too_many_samples {param: "table"}` (> 20 000 интервалов, совет композитировать — Т7.29-2),
`no_values`, `too_few_values`, `no_spread`, `bad_input`, `bad_param {param: set_id, table, holes, declus, method,
cmin, cmax, ncell, noff, minmax, nx, ny, bounds, weights, zmin, zmax, scores, ltail, utail, ltpar, utpar, n_boot,
seed, confidence}`, `field_not_found` / `field_not_number {field}`; 404 `set_not_found`, `table_not_found`.
