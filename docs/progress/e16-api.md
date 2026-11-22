# Э16 — контракт API `/api/v1/geostat` (черновик для интерфейса и дорожек; удаляется в 16.30)

Источник истины — ADR 0022 §2–16, ТЗ 8.6.4; разбор тел — `core/bf_core/geostat/variogram/spec.py` (16.11), точки
расчёта — `geostat/variogram/points.py` (16.12), хранение и каркас роутера — 16.13, задания и предпросмотр — 16.14,
карта, подбор, Эрмит, кривая, «Перестроить» — 16.15, тренды — 16.16, вкладка «Статистика» — 16.17. Изменение формы
ниже — только через оркестратора. Префикс всех маршрутов — `/api/v1/geostat` (кроме вкладки: `/api/v1/stats/tab/*`).

## Общие правила

- Заголовок `X-BF-Token`, Host/Origin — как у всего ядра. Числа — float64; пусто (лаг без пар, σ = 0, «> h_max») —
  `null`, никогда не 0. Бесконечность (полосы, толщина слоя карты) — строка `"inf"` (на входе ещё `"∞"`).
- Число на входе — число или текст «0,5» / «0.5»; целые параметры — целое (15 или 15.0). Отсутствует / `null` /
  `""` — значение по умолчанию.
- Ошибка — `{code, message, hint, param?, …}`, тексты по-русски. `param` — ключ тела или путь: `"directions.0.az"`,
  `"domain.values"`, `"fan.n"`, `"map.cell"`, `"model.structures.1.a2"`.
- Нет открытого проекта — 409 `no_project`. Запись в проект «только просмотр» (сохранение, замена, удаление моделей
  и трендов) — 423 `project_read_only`; расчёты, предпросмотр, подбор работают.
- Задание: ответ `{source, id}`; ход и итог — `GET /jobs/{source}/{id}` (как Э12–Э15). Очередь `heavy`. Синхронные
  расчёты (предпросмотр, подбор, Эрмит, кривая, вкладка) — в слоте HEAVY.
- Событие `geostat.changed {kind: "variogram_model" | "trend", id, deleted?}` — после записи и удаления.
- Пока маршрут не подключён в сборке — 422 `not_ready`.

## Данные расчёта — общая часть `experimental`, `preview`, `map`, `defaults`

```json
{"set_id": 3, "table": "12", "field": "AU",
 "domain": {"table": "14", "field": "ZONE", "values": ["окисл", "перех"]},
 "weights": null, "transform": "none"}
```

| Поле | Значение | Ошибки |
|---|---|---|
| `set_id` | число | 404 `set_not_found` `{param: "set_id"}` |
| `table` | интервальная таблица набора (пробы, композиты): id текстом или имя | 404 `table_not_found` `{param: "table"}` |
| `field` | числовое поле таблицы | не выбрано / не число — 422 `bad_param`; 422 `field_not_found` `{param: "field"}` |
| `domain` | `null` — все точки; `{table?, field, values}`: поле (текст или число) той же таблицы (нет `table`) или другой интервальной (по середине пробы); `values` — 1–1000 значений; у числового поля число («2,5» → `"2.5"`) | 422 `bad_param` (`domain.field`, `domain.values`, `domain.values.N`); `{wireframe_ids}` — 422 `bad_param` (каркасы — через «Кодировать каркасами»); 404 `table_not_found` `{param: "domain.table"}` |
| `weights` | `null` / нет — `DECL_W`, если в таблице есть числовое поле с таким именем; `false` или `""` — без весов; имя числового поля | 422 `bad_param` / `field_not_found` `{param: "weights"}` |
| `transform` | `none` (по умолчанию) / `nscore` | 422 `bad_param` |

Веса входят только в таблицу нормальных оценок (в суммы по парам — нет, как GSLIB). Точка с пропуском веса участвует
в парах, в таблицу не входит; её оценка читается из таблицы (`counts.no_weight`).

Точки (`points.py`): середина интервала на траектории (X, Y, Z); пропуск значения, нет положения (нет FROM/TO или
траектории), вне домена — не участвуют и считаются:
`counts {rows, missing, no_position, outside_domain, used, no_weight}`, `rows = missing + no_position +
outside_domain + used`. Опорная точка — центр охвата точек (`origin [x, y, z]`) вычитается до расчёта.
`sigma2` — дисперсия переменной расчёта (делитель n); у `nscore` — ровно 1; `sigma2_z` — дисперсия значений поля.
Ошибки: 0 точек — 422 `no_values`; 1 точка — 422 `too_few_values`; все значения равны — 422 `no_spread`
(в теле всех трёх — `counts`); нет ни одного положительного веса при `nscore` — 422 `bad_param {param: "weights"}`.

## `GET /variography/defaults?set_id&table&field[&domain_table&domain_field&domain_values&weights&transform]`

`domain_values` — через `;` (пустые части отбрасываются); `domain_table` нет — поле той же таблицы; `weights` нет —
DECL_W при наличии, `weights=` (пусто) — без весов, `weights=DECL_W` / имя — это поле (сверено с 16.21: «без
весов» — `weights=`, DECL_W — параметр опущен; `false` текстом — имя поля, 422 `field_not_found`). Синхронно, в
слоте HEAVY. Ответ:

```json
{"set_id": 3, "table": "12", "table_name": "Композиты", "field": "AU", "unit": "г/т",
 "counts": {"rows": 1200, "missing": 4, "no_position": 0, "outside_domain": 0, "used": 1196, "no_weight": 0},
 "sigma2": 1.84, "sigma2_z": 1.84, "median_length": 2.0, "weights": "DECL_W", "has_decl_w": true,
 "extent": {"min": [x, y, z], "max": [x, y, z]},
 "defaults": {"lag": 10, "nlag": 15, "lag_tol": 5, "az_tol": 22.5, "dip_tol": 22.5, "band_h": 30, "band_v": 20,
              "min_pairs": 30, "downhole": true, "dh_lag": 2, "dh_nlag": 15, "dh_lag_tol": 1, "seed": 12345,
              "fan": {"n": 12, "tol": 7.5, "q": 1.0},
              "map": {"nu": 20, "nv": 20, "cell": 10, "thickness": 20, "min_pairs": 5}},
 "ranges": {"lag": {"lo": 0, "hi": 100000, "lo_open": true, "integer": false, "inf": false, "unit": "м"}, "…": {}}}
```

Умолчания (ADR §2): L = 5 × медиана длин интервалов точек, 2 значащие цифры (2 м → 10 м; 1,37 м → 6,9 м); вдоль
скважин — лаг = медиана длин (без округления). Ключи `ranges` — как в таблице ниже.

## Диапазоны (ТЗ 8.6.4; вне диапазона — 422 `bad_param {param}`)

| Ключ | По умолчанию | Диапазон |
|---|---|---|
| `lag` | 5 × медиана (2 зн.) | (0; 100 000] м |
| `nlag` | 15 | 1–100, целое |
| `lag_tol` | L / 2 | [0; L] (проверка после подстановки L) |
| `az_tol`, `dip_tol` | 22,5° | (0°; 90°]; 90° — проверка не делается; оба 90° — всенаправленная |
| `band_h`, `band_v` | 3L, 2L | (0; 100 000] м или `"inf"` |
| `min_pairs` | 30 | 1–1000, целое |
| `downhole` | `true` | логическое |
| `dh_lag`, `dh_nlag`, `dh_lag_tol` | медиана длин, 15, dh_lag / 2 | как `lag`, `nlag`; [0; dh_lag] |
| `seed` | 12345 | 0 – 2⁶³ − 1, целое |
| `ang1` | 0 | [0°; 360°], 360 → 0 |
| `ang2` | 0 | [−90°; 90°] (ang2 = −погружение) |
| `ang3` | 0 | [−180°; 180°], −180 → 180 |
| `directions.N.az`, `.plunge` | 0, 0 | [0°; 360°] (360 → 0), [−90°; 90°] (вниз — положительное) |
| `fan.n` | 12 | 4–72, целое |
| `fan.tol` | 90° / N | (0°; 90°] |
| `fan.q` | 1 | 0,5–1,2 |
| `map.nu`, `map.nv` | 20, 20 (карта 41 × 41) | 1–100 (до 201 × 201), целое |
| `map.cell` | L | (0; 100 000] м |
| `map.thickness` | 2L | (0; 100 000] м или `"inf"` (проекция) |
| `map.min_pairs` | 5 | 1–1000, целое |

## `POST /variography/experimental` — задание `variography.experimental`

```json
{"set_id": 3, "table": "12", "field": "AU", "domain": null, "weights": null, "transform": "none",
 "axes": {"ang1": 30, "ang2": -10, "ang3": 0},
 "lag": 10, "nlag": 15, "lag_tol": 5, "az_tol": 22.5, "dip_tol": 22.5, "band_h": 30, "band_v": 20,
 "min_pairs": 30, "downhole": true, "dh_lag": 2, "dh_nlag": 15, "dh_lag_tol": 1, "seed": 12345}
```

Направления — либо `axes` (три направления — оси a1, a2, a3 матрицы 8.6.3; по умолчанию `{0, 0, 0}`), либо
`directions: [{az, plunge, az_tol?, dip_tol?, band_h?, band_v?}]` (1–72; пропущенный допуск — общий тела). Оба —
422 `bad_param {param: "directions"}`. Ответ 200 `{source, id}`; 404; 422 `bad_param`, `field_not_found`,
`not_ready` (задание не подключено в сборке). То, что видно только по точкам, — `no_values`, `too_few_values`,
`no_spread`, `no_lengths` (медиана длин не определена — задайте лаг), — сбой задания: `job.status: "failed"`,
`job.error {code, message, hint}` (без повтора); предпросмотр с тем же телом отвечает на них синхронно 422 (с
`counts`). Проект «только просмотр» считает (запись кеша — не данные проекта).

Стадии хода (`message` задания): «Чтение таблицы», «Траектории», «Нормальные оценки» (только `nscore`), «Пары: оси»
(у формы `directions` — «Пары: направления»), «Пары: вдоль скважин» (при `downhole`), «Запись». Отмена — между
полосами ядра; запись — последней, целиком или ничего. Потоки — `strips.default_threads()`; итог побитно тот же
при 1 и 4 потоках.

Ключ (`key`) — SHA-256 `{algo: "vario-1", params: канонические параметры после подстановки умолчаний, inputs:
{tables [{id, name_key, token, rev}], trajectories}}`. Тот же ключ — расчёт не повторяется: итог читается из кеша
(`fresh: true`); если в теле заданы оба лага (`lag`, `dh_lag`), ключ известен до чтения таблицы и повтор её не
читает, иначе точки читаются ради медианы длин. Правка таблицы или траекторий — новый ключ.

Итог (`job.result`) — **форма расчёта** и ещё два поля: `fresh` (итог из кеша) и `stages` — секунды стадий
`{read, trajectories, nscore?, pairs, downhole?, write, total}` (у повтора — только прочитанные). Форма:

```json
{"key": "3f…(sha256)", "set_id": 3, "table": "12", "field": "AU", "unit": "г/т", "transform": "none",
 "sigma2": 1.84, "sigma2_z": 1.84, "origin": [x, y, z],
 "counts": {"rows": 1200, "missing": 4, "no_position": 0, "outside_domain": 0, "used": 1196, "no_weight": 0},
 "sampled": null,
 "params": { "…канонические параметры: все числа подставлены, ∞ — \"inf\"…" },
 "directions": [
   {"axis": 1, "az": 30.0, "plunge": 10.0, "az_tol": 22.5, "dip_tol": 22.5, "band_h": 30.0, "band_v": 20.0,
    "lags": [10, 20, "…"], "h": [9.7, null, "…"], "n": [412, 0, "…"],
    "gamma": [0.61, null], "rho": [0.33, null], "pairwise": [0.42, null], "n_pw": [410, 0],
    "few": [false, true]}],
 "downhole": {"lag": 2.0, "lag_tol": 1.0, "lags": [2, 4], "h": [2.0, 4.0], "n": [1180, 1150],
              "gamma": [0.3, 0.5], "rho": [0.8, 0.7], "pairwise": [0.2, 0.3], "n_pw": [1180, 1150],
              "few": [false, false]},
 "warnings": ["Парная относительная: пропущено 12 пар с z_i + z_j ≤ 0."]}
```

- `axis` — 1, 2, 3 у формы `axes`, `null` у `directions`; `az`, `plunge` направления (у осей — из матрицы).
- `lags` — центры k·L, k = 1…nlag; `h` — среднее расстояние пар лага; `gamma` — полувариограмма (у `nscore` —
  переменной Y); `rho` — коррелограмма ρ (график рисует 1 − ρ; σ = 0 — `null`); `pairwise` — парная
  относительная по `n_pw` парам (пары с z_i + z_j ≤ 0 не входят); `few` — N < `min_pairs` (полый знак, в подбор не
  входит). Лаг без пар — `null` во всех величинах, `n` = 0.
- `downhole` — `null` при `downhole: false`; считается по всем точкам (выборка на него не действует).
- `sampled` — `{n, of, seed}`, когда оценка числа проверок пар > 2·10⁹: n′ = ⌊n·√(2·10⁹ / оценка)⌋ точек без
  возврата (`PCG64(seed)`); окно пишет «Расчёт по выборке n′ из n проб».
- `warnings` — одна строка о парной относительной: пропущенные пары осей и вдоль скважин вместе.
- Ответ — массивы целиком: 3 оси × 15 лагов ≈ 8 КБ; предел 72 направления × 100 лагов ≈ 0,75 МБ (больше 256 КБ —
  допускается, IPC заданий и HTTP это несут). Полные суммы — в файле кеша по `key`:
  `cache/variography/<key>.sums.parquet` — строка на [часть, направление, лаг]: `part` (`axis` / `direction` /
  `downhole`), `index`, `k` (1…nlag), `lag` (k·L), `n`, `n_rel`, `s_h`, `s_d2`, `s_t`, `s_hd`, `s_tt`, `s_hh`,
  `s_th`, `s_rel` (суммы `pairs.S_*`), `shift`, `both`; у `nscore` — ещё `<key>.nscore.parquet` (value, score,
  weight: все точки расчёта, вес NaN — нет веса; без поля весов — 1).

## `POST /variography/preview` — синхронно

Тело — как `experimental`. Выборка ≤ 20 000 точек (`seed`, `points.sample`), в слоте HEAVY. Ответ — форма расчёта с
`key: null` и `sampled {n, of, seed}` (если точек больше 20 000; `of` — все точки), `downhole` — по той же выборке;
`sigma2`, `counts` — всех точек. Ничего не пишет. Ошибки — все синхронно: 404, 422 `bad_param`, `no_values`,
`too_few_values`, `no_spread` (с `counts`), `no_lengths`. Замер (Linux, 4 ядра, 200 тыс. точек, снимок таблицы
готов): 0,72–0,83 с.

## `POST /variography/map` — задание `variography.map`

```json
{"set_id": 3, "table": "12", "field": "AU", "domain": null, "weights": null, "transform": "none",
 "plane": {"ang1": 0, "ang2": 0, "ang3": 0}, "what": ["fan", "map"],
 "lag": 10, "nlag": 15, "lag_tol": 5, "dip_tol": 22.5, "band_v": 20, "min_pairs": 30, "seed": 12345,
 "fan": {"n": 12, "tol": 7.5, "q": 1.0},
 "map": {"nu": 20, "nv": 20, "cell": 10, "thickness": 20, "min_pairs": 5}}
```

`plane` — оси плоскости a1, a2, нормаль a3 (по умолчанию горизонтальная). `what` — `fan` (с розой) и / или `map`
(по умолчанию оба). Стадии: «Чтение таблицы», «Траектории», «Нормальные оценки» (только `nscore`), «Пары: веер»,
«Пары: карта» (только заказанные), «Запись»; доля хода — по числу проверок пар. Отмена — между полосами ядер; запись
последней, целиком или ничего. Ключ — как у `experimental` (`op: "map"`; тот же набор параметров расчёта осей — другой
ключ); задан `lag` — ключ известен до чтения таблицы, повтор её не читает. Запись кеша — `<key>.json` (без
`.sums`, `.nscore`); ключ карты в `/fit` и `exp_key` моделей — 422 `bad_param {param: "key" | "exp_key"}`. Итог
(`job.result`) — форма ниже и ещё `set_id`, `table`, `field`, `unit`, `transform` (как у формы расчёта), `fresh`,
`stages {read, trajectories, nscore?, fan?, map?, write, total}`. Замер (Linux, 4 потока, 200 704 композита 2 м на
1024 скважинах, L = 20 м, 15 лагов, веер 12, карта 41 × 41, слой 2L): выборка 99–108 тыс., read 0,4 с, trajectories
0,1–0,6 с, fan 5,9–6,2 с, map 1,6–1,7 с, всего 8,3–8,7 с (горизонтальная и повёрнутая плоскость). Итог:

```json
{"key": "…", "plane": {"ang1": 0, "ang2": 0, "ang3": 0}, "sigma2": 1.84, "sigma2_z": 1.84, "origin": [x, y, z],
 "counts": {"…": 0}, "sampled": null, "params": {"…": 0},
 "fan": {"directions": [{"theta": 0.0, "az": 0.0, "plunge": 0.0, "az_tol": 7.5, "dip_tol": 22.5, "band_h": "inf",
                          "band_v": 20.0, "lags": [], "h": [], "n": [], "gamma": [], "rho": [], "pairwise": [],
                          "n_pw": [], "few": []}]},
 "rose": {"q": 1.0, "level": 1.84, "rays": [{"theta": 0.0, "range": 85.0, "beyond": false},
                                              {"theta": 15.0, "range": null, "beyond": true}]},
 "map": {"nu": 20, "nv": 20, "cell": 10.0, "thickness": 20.0, "min_pairs": 5,
         "u": [-200, "…", 200], "v": [-200, "…", 200],
         "gamma": [[null, 0.4, "…"]], "n": [[0, 7, "…"]]},
 "warnings": []}
```

- `theta` — угол в плоскости от a1 к a2 (0°, 180° / N, …); `az`, `plunge` — то же направление в мировых осях.
- Роза: `range` — первое расстояние, где γ̂ ≥ q·σ² (линейно между точками с N ≥ `min_pairs`); не достигнуто —
  `null` и `beyond: true` («> h_max»).
- Карта: `gamma[j][i]`, `n[j][i]` — строка j по v (снизу вверх, j = 0 — v = −nv·cell), столбец i по u; ячейка с
  N < `map.min_pairs` — `null`. `fan` / `map` — `null`, если не заказаны.

## `POST /variography/fit` — подбор, синхронно

```json
{"key": "…", "type": "semivariogram",
 "model": {"type": "semivariogram", "c0": 0.2, "common_axes": true,
           "structures": [{"type": "spherical", "c": 1.0, "ang1": 30, "ang2": -10, "ang3": 0,
                           "a1": 120, "a2": 80, "a3": 20}]},
 "weights": "n_h", "fix": ["c0", "structures.0.a3"], "fix_nugget": false}
```

`key` — итог `experimental` (оси; `directions` — 422 `bad_param {param: "key"}`: подбор — по осям модели);
нет файла по ключу — 404 `result_not_found`. `type` — величина подбора: `semivariogram` / `correlogram` (1 − ρ) /
`pairwise` / `nscore` (порог S = 1 закреплён). `model` — начальная модель (типы структур задаёт пользователь, углы
закреплены). `weights` — `n_h` (N / h̄, по умолчанию) / `n` / `n_g2` (N / γ², три прохода). `fix` — пути
закреплённых параметров (`c0`, `structures.N.c`, `structures.N.a1…a3`); `fix_nugget` — то же, что `c0` в `fix`.
Начальный C0 — экстраполяция первых трёх лагов вдоль скважин (≥ 0), если `c0` не закреплён.

Ответ: `{model, residual, n, starts: 8, warnings: [..], order, fixed}` — `model` той же формы (структуры по
возрастанию a1; `order` — их места в запросе, `fixed` — пути закреплённых в ответе),
`residual` — взвешенная сумма квадратов, `n` — число точек; предупреждения: «дальность на границе 2·h_max», «точек
меньше, чем неизвестных», «ось без точек». Ошибки: 422 `bad_param {param: "model.…" | "weights" | "fix"}`,
`too_few_points`.

### Модель (`model`) — общая форма `fit`, `back`, `curve`, `models`

| Поле | Значение |
|---|---|
| `type` | `semivariogram` (по умолчанию) / `correlogram` / `pairwise` / `nscore` |
| `c0` | ≥ 0 (0) |
| `structures` | 0–3: `{type: spherical / exponential / gaussian, c ≥ 0, ang1, ang2, ang3, a1, a2, a3 ∈ (0; 100 000] м}` |
| `common_axes` | `true` (по умолчанию): углы всех структур = углам первой (ядро подставляет) |

Порог C0 + Σc > 1,2·σ² допускается (предупреждение окна). Ошибки — 422 `bad_param {param: "model.structures.1.a2"}`.

## `POST /variography/back` — обратное преобразование (Эрмит), синхронно

`{key, model, ltail?, utail?, ltpar?, utpar?}` — `key` расчёта с `transform: "nscore"` (иначе 422 `bad_param
{param: "key"}`; нет записи — 404 `result_not_found`), `model` в единицах Y (`nscore` / `semivariogram` /
`correlogram`; `pairwise` — 422 `{param: "model.type"}`); хвосты — как `backtr` (по умолчанию `linear`). Таблица
нормальных оценок — из `<key>.nscore.parquet` (та же, что у `points.load`).
Ответ: `{model (единицы Z), share, warning: null | "…", residual, curves: [{axis, h [], gamma_z [], gamma_fit []}]}`;
`share` — Σφ_n² / Var(Z), < 0,95 — `warning`.

## `POST /variography/curve` — значения модели, синхронно

`{model, directions?: [{az, plunge}], h?: [..], h_max?, n?: 100}` — без `directions` — три оси модели (первой
структуры); без `h` — `n` расстояний от 0 до `h_max` (по умолчанию 1,5 × наибольшей дальности). Ответ:
`{directions: [{axis, az, plunge, h [], gamma [], cov []}]}` (cov = C0 + Σc − γ; γ(0) = 0). `axis` — 1, 2, 3 у осей
модели, `null` у заданных направлений; оси модели без структур — углов 0. `h` — 1–10 000 чисел ≥ 0; `n` — целое
2–1000; `h` нет — `n` точек от 0 до `h_max` включительно; модель без структур без `h_max` — 422 `bad_param {param:
"h_max"}`. Ошибки — 422 `bad_param {param: "h" | "n" | "h_max" | "directions" | "directions.N.az" | "model.…"}`.

## Модели вариограмм — `objects(kind = "variogram_model")` (16.13: `geostat/variogram/{store, routes_model}.py`)

- `GET /variography/models[?set_id]` → `{models: [{id, name, set_id, table, field, transform, type, color, sill,
  structures: N, stale, stale_reason, source_lost, created_at, updated_at}]}` (по `name`); `sill` = C0 + Σc.
- `POST /variography/models` `{name?, model, exp_key, color?, fit?, back?}` → 200 `{id, name}`; `name` нет / пусто —
  «‹поле› · ‹таблица›», занятое — с « (2)», « (3)»; заданное и занятое (без учёта регистра и лишних пробелов) — 409
  `name_taken {param: "name"}`; `exp_key` не 64 hex — 422 `bad_param {param: "exp_key"}`, нет записи расчёта — 404
  `result_not_found {param: "exp_key"}`; `color` — `#rrggbb` (по умолчанию `#1f6bff`), иначе 422 `bad_param
  {param: "color"}`; `fit` — `{weights: n_h | n | n_g2, residual, n, fixed []}` (ответ `/fit`), `back` — ответ
  `/back` (≤ 256 КБ); 422 `bad_param {param: "model.…" | "fit.…" | "back"}`; 423.
- `GET /variography/models/{id}` → `{id, name, color, meta: {v, source {set_id, table, field, domain, weights},
  transform, type, sigma2, c0, structures, common_axes, calc {params, sampled}, fit, back, inputs, exp_key, sha256,
  algo: "vario-1", versions {numpy, scipy, numba}, created_at, color}, points: форма расчёта, stale, stale_reason,
  source_lost, version, created_at, updated_at}`; `points` — копия формы в файлах модели (роль `vario`; не зависит
  от кеша), `points.set_id`, `points.table` — как в `meta.source`; 404 `model_not_found`.
- `PUT /variography/models/{id}` `{name?, model?, exp_key?, color?, fit?, back?}` → `{id, name}`: только заданные
  поля; `exp_key` — новые точки и источник (`fit`, `back` не сбрасываются, если не заданы; `fit: null` — сброс);
  `version` + 1; 404; 409; 422; 423.
- `DELETE /variography/models/{id}` → `{deleted: id}`; 404; 423.
- `POST /variography/models/{id}/rebuild` → `{source, id}` задания `variography.rebuild` «Перестроить» (16.15;
  точки на текущих данных с прежними параметрами `meta.calc.params`, таблицы — по id, затем по имени; параметры
  модели не меняются; `fit.residual`, `fit.n` пересчитываются по новым точкам с прежним правилом весов (N / γ² — с
  γ самой модели), `back` — по новой таблице нормальных оценок без `curves`, если был); источник удалён — 409
  `source_lost`; 404 `model_not_found`; 423. Стадии — как у `experimental`. Итог: `{model_id, key, fresh, stages,
  fit, back, events: [["geostat.changed", {kind: "variogram_model", id}]]}`; модель — новые `exp_key`, `points`,
  `stale: false`, `version` + 1.
- `GET /variography/models/{id}/export?format=json|gslib[&path=]` → без `path`: `{format, filename, text}`
  (`filename` — имя модели без запрещённых Windows знаков + `.json` / `.par`); с `path` (абсолютный, кириллица
  допустима) — файл записан байтами UTF-8 (GSLIB — ASCII, «\n» и в Windows): `{format, path, bytes}`; путь не
  полный, папки нет, запись не удалась — 422 `bad_param {param: "path"}`; формат — 422 `bad_param {param:
  "format"}`. JSON — поля `meta` без `inputs` с меткой `"kind": "variogram_model"`. Работает и в «только просмотр».
- `POST /variography/models/import` `{format?, text? | path?}` → `{format, model}` (рабочая модель окна, не
  сохраняется; без `format` — по содержимому: «{» — JSON, иначе GSLIB; GSLIB — `type: semivariogram`); ровно одно
  из `text`, `path` (иначе 422 `bad_param {param: "path" | "text"}`); файл > 1 МБ, нет, не читается — 422
  `bad_param {param: "path"}`; битый файл — 422 `bad_file {line?}` с подсказкой.

«Устарел»: `inputs` (таблицы `{id, name_key, token, rev}` и ключ траекторий набора) сравниваются с текущими при
чтении; таблица ищется по id, затем по имени; `stale_reason` — «изменена таблица «…»» (id, token или rev),
«изменены траектории»; набор или таблица удалены (или их нет в архиве `.bfggis`) — `stale: true`, `stale_reason:
"источник удалён"`, `source_lost: true`, «Перестроить» недоступно. Событие `geostat.changed {kind:
"variogram_model", id, deleted?}` — после `POST`, `PUT`, `DELETE`.

Запись расчёта (пишет задание 16.14, читает сохранение — `store.write_result` / `read_result`):
`cache/variography/<key>.json` `{v: 1, result: форма расчёта, source: spec.Data.canonical() без transform, inputs:
Points.inputs, calc: {params, sampled}}`; `cache/variography/<key>.nscore.parquet` — таблица нормальных оценок
(value, score, weight; роль `nscore` модели). `.bfggis`: `geostat/remap.py` перенумеровывает `source.set_id`,
`source.table`, `source.domain.table`, `inputs.tables[].id` (нет в архиве — `null`).

## Тренды — `objects(kind = "trend")` (16.16: `geostat/{trend, trend_store, routes_trend}.py`)

- Тренд — форма: `{id, name, kind: "global", ang1, ang2, ang3, ratios: [1, r2, r3], size, centre: [x, y, z] | null,
  color, origin, used: bool, matrix: [[..],[..],[..]], version, created_at, updated_at}`; углы — нормализованные
  (`rotation.normalize_angles`), `matrix` = A = diag(1, 1/r2, 1/r3)·R (`trend.matrix`, единственный источник A).
- `GET /trends` → `{trends: [форма]}` (по имени); `GET /trends/{id}` → форма; 404 `trend_not_found`.
- `POST /trends` `{name?, kind?: "global", ang1, ang2? = 0, ang3? = 0, ratios? = [1, 1, 1], size? = 100, centre?,
  color? = "#f46a1a", origin?: {type: "manual" | "ellipsoid"}, model_id?, structure?}` → `{id, name}`. Числа — число
  или «0,5». С `model_id` — копия углов и `[1, a2/a1, a3/a1]` структуры `structure` (с 1; по умолчанию — с
  наибольшей a1, из равных первая), `size` = её a1 (если не задан), `origin {type: "model", model_id, structure}`,
  имя по умолчанию «Тренд · ‹модель›»; углы и `ratios` тела при `model_id` не читаются. Без имени — «Тренд», занятое —
  « (2)»; заданное занятое — 409 `name_taken`. `ratios` — три числа, первое ровно 1, остальные > 0 и ≤ 10⁶; `size`
  > 0 и ≤ 10⁷ м. `kind: "structural"` — 422 `not_ready {param: "kind"}`; `structure` без `model_id` — 422.
- `PUT /trends/{id}` — заданные поля заменяют хранимые (прочие остаются, `origin` не меняется без `model_id` /
  `origin`); `{model_id, structure?}` — «Обновить из модели» (копия заново, имя остаётся). Правка модели тренд не
  меняет; модель удалена — тренд остаётся, в ответе `origin.model_id: null`.
- `DELETE /trends/{id}` → `{deleted: id}`; есть ссылки — 409 `in_use {usage}` (текст называет до 5 потребителей).
- `GET /trends/{id}/usage` → `{usage: [{id, kind, name, role}]}` — строки `object_refs(dst_id = тренд)` (потребители
  Э18–Э19 пишут `role: "trend"`; любая роль запрещает удаление), по имени.
- Ошибки `bad_param {param}`: `kind`, `ang1`–`ang3`, `ratios`, `ratios.0`–`.2`, `size`, `centre`, `centre.0`–`.2`,
  `color`, `origin`, `model_id`, `structure`; модель не найдена — 404 `model_not_found`. Запись — 423 в проекте
  «только просмотр»; событие `geostat.changed {kind: "trend", id, deleted?}`.
- Ключ вида `ellipsoids` — `[{object, visible}]` (id модели вариограммы или тренда, `visible` по умолчанию true,
  повтор — первый, ≤ 50 последних); входит в сохранённые виды; удалённые объекты забываются при чтении. `.bfggis`:
  `geostat/remap.py` перенумеровывает `origin.model_id` (нет в архиве — null), ключ `ellipsoids` и ссылки
  `object_refs` — общим механизмом.

## Вкладка «Статистика» (`/api/v1/stats/tab/*`, 16.17; формы — перенос BF `routers/stats.py`)

Маршруты — `stats/routes_tab_vario.py` (ядра 16.10 — `stats/tab_vario.py`, `stats/local.py`). Синхронно, в слоте
HEAVY (2 потока); ничего не хранится. Общие ключи тела — как у прочих `/stats/tab/*`: `set_id`, `table` (имя или
id; null — таблица проб), `field` (null — первый компонент), `holes` (имена; пусто — все). Точки — середины
интервалов со значением и положением, в плане; пропуск значения — не точка (не ноль, `counts.missing`). Значения
тела передаются ядрам как есть: число или текст «0,5»/«0.5»; нет, null или "" — умолчание (у модели — только
числа). Пустой лаг, ячейка, узел — `null`, не 0. Ответы трёх анализов с данными начинаются с `{set_id, table
(имя), field}`; `counts` = `{rows, missing, no_position, used}`.

- `POST /stats/tab/variogram` `{…общие, lag (умолч. круглый шаг охвата/20), nlag 1–200 (10), lag_tol 0…lag
  (lag/2), directions "omni" | "four", min_pairs 1–1000 (30), standardize false, model {nugget | nug, structures
  [{it, cc, hmaj, hmin, azi}] ≤ 4}?}` → `{set_id, table, field, count, sigma2, lag, nlag, lag_tol, min_pairs,
  standardize, lags [k·lag], directions [{az, az_tol, omni, h [] | null, gamma [] | null, n [], few [],
  model_gamma []?}], model {nugget, sill, structures} | null, counts}`; «four» — 0°, 45°, 90°, 135°, допуск 22,5°;
  `few` — пар меньше `min_pairs`; `model_gamma` — модель в центрах лагов (только с `model`); `standardize` делит
  γ на σ².
- `POST /stats/tab/variogram-model` `{nugget | nug, structures, h_max (умолч. 1,5 × наибольшая дальность), n 2–2000
  (101), directions}` → `{nugget, sill, structures, h_max, n, directions [{az, h [], gamma [], cov []}]}` — без
  данных и без открытого набора; нет структур и `h_max` — 422 `bad_param` {param: h_max}.
- `POST /stats/tab/map` `{…общие, cell (как lag), n 1–100 (10), min_pairs 1–1000 (5), standardize}` → `{set_id,
  table, field, count, sigma2, cell, nu, nv, min_pairs, standardize, u [2n+1], v [2n+1], gamma [j][i], n [j][i],
  counts}`; u = ΔX (восток, столбец i), v = ΔY (север, строка j от юга); пар < `min_pairs` — `null`.
- `POST /stats/tab/local` `{…общие, grid {nx, ny} 2–100 (30), k 1–64 (8), power (0; 10] (2), radius (25 % охвата),
  realizations 5–300 (50), seed?, threshold?}` → `{set_id, table, field, count, holes, realizations, k, power,
  radius, seed, threshold, grid {nx, ny, x [nx], y [ny]}, mean [j][i], sd, p10, p90, prob [j][i] | null, empty,
  counts}`; реализация — скважины интервалов с возвратом; узел без данных в радиусе — `null` (`empty` — сколько);
  без `seed` зерно выбирается и возвращается; `prob` — доля реализаций ≥ порога (без порога — null).

Ошибки `{code, message, hint, param?}`: 422 `bad_param` {param — ключ тела; модель вариограммы — `model.<ключ>`,
сетка — `grid.nx`/`grid.ny`}, `field_not_found` / `field_not_number` {field}, `no_values`, `too_few_values`
(< 2 точек; у `local` — < 3 точек или < 3 скважин), `no_spread` (все значения равны; у `local` — одна точка
плана), `too_many_samples` {param: table} — больше 20 000 интервалов (20 001 — отказ; подсказка: композиты или
окно «Вариография»); 404 `set_not_found`, `table_not_found`.

## Команды (ADR §13) → маршруты

`variography.calc` → `experimental` (`what: axes`) / `map` (`fan`, `map`); `variography.fit` → `fit`;
`variography.save` → `POST` / `PUT models`; `variography.delete` → `DELETE models/{id}`; `variography.export` /
`import` → `export` / `import` (`path`); `trend.save` → `POST` / `PUT trends`; `trend.delete` → `DELETE trends/{id}`.
`variography.open`, `.models`, `.ellipsoid`, `.show`, `trend.show` — только интерфейс.
