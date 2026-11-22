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

`domain_values` — через `;`. Ответ:

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
422 `bad_param {param: "directions"}`. Ответ 200 `{source, id}`; 404; 422 `bad_param`, `no_values`,
`too_few_values`, `no_spread`, `no_lengths` (медиана длин не определена — задайте лаг).

Стадии хода: «Чтение таблицы», «Траектории», «Нормальные оценки», «Пары: оси», «Пары: вдоль скважин», «Запись».
Отмена — между полосами ядра. Тот же ключ — расчёт не повторяется (итог читается из кеша).

Итог (`job.result`) — **форма расчёта**:

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
- Ответ ≤ 256 КБ: массивы целиком (≤ 72 направления × 100 лагов). Полные суммы — в файле кеша по `key`.

## `POST /variography/preview` — синхронно

Тело — как `experimental`. Выборка ≤ 20 000 точек (`seed`), ≤ 1 с. Ответ — форма расчёта с `key: null` и
`sampled {n, of, seed}` (если точек больше 20 000), `downhole` — по той же выборке. Ошибки — как у `experimental`.

## `POST /variography/map` — задание `variography.map`

```json
{"set_id": 3, "table": "12", "field": "AU", "domain": null, "weights": null, "transform": "none",
 "plane": {"ang1": 0, "ang2": 0, "ang3": 0}, "what": ["fan", "map"],
 "lag": 10, "nlag": 15, "lag_tol": 5, "dip_tol": 22.5, "band_v": 20, "min_pairs": 30, "seed": 12345,
 "fan": {"n": 12, "tol": 7.5, "q": 1.0},
 "map": {"nu": 20, "nv": 20, "cell": 10, "thickness": 20, "min_pairs": 5}}
```

`plane` — оси плоскости a1, a2, нормаль a3 (по умолчанию горизонтальная). `what` — `fan` (с розой) и / или `map`
(по умолчанию оба). Стадии: «Чтение таблицы», «Траектории», «Нормальные оценки», «Пары: веер», «Пары: карта»,
«Запись». Итог:

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

Ответ: `{model, residual, n, starts: 8, warnings: [..]}` — `model` той же формы (структуры по возрастанию a1),
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

`{key, model}` — `key` расчёта с `transform: "nscore"` (иначе 422 `bad_param {param: "key"}`), `model` в единицах Y.
Ответ: `{model (единицы Z), share, warning: null | "…", residual, curves: [{axis, h [], gamma_z [], gamma_fit []}]}`;
`share` — Σφ_n² / Var(Z), < 0,95 — `warning`.

## `POST /variography/curve` — значения модели, синхронно

`{model, directions?: [{az, plunge}], h?: [..], h_max?, n?: 100}` — без `directions` — три оси модели (первой
структуры); без `h` — `n` расстояний от 0 до `h_max` (по умолчанию 1,5 × наибольшей дальности). Ответ:
`{directions: [{axis?, az, plunge, h [], gamma [], cov []}]}` (cov = C0 + Σc − γ; γ(0) = 0).

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
- `POST /variography/models/{id}/rebuild` → `{source, id}` задания «Перестроить» (16.15; точки на текущих данных с
  прежними параметрами; параметры модели не меняются, невязка пересчитывается); источник удалён — 409
  `source_lost`.
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

## Тренды — `objects(kind = "trend")`

- `GET /trends` → `{trends: [{id, name, kind, ang1, ang2, ang3, ratios, size, centre, color, origin, used}]}`.
- `POST /trends` `{name?, kind: "global", ang1, ang2, ang3, ratios?: [1, r2, r3], size?, centre?, color?,
  model_id?, structure?}` → `{id, name}`. С `model_id` — копия углов и отношений структуры (по умолчанию — с
  наибольшей a1), `origin {type: "model", model_id, structure}`; без — `origin {type: "manual"}` (или
  `"ellipsoid"`, если задан `origin`). `ratios` — [1, a2 / a1, a3 / a1], каждое > 0, первое — 1; `kind: "structural"` — 422 `not_ready`.
- `PUT /trends/{id}` — те же поля; `DELETE /trends/{id}` → `{deleted: id}`, есть ссылки — 409 `in_use {usage}`.
- `GET /trends/{id}/usage` → `{usage: [{id, kind, name, role: "trend"}]}`. Матрица A = diag(1, a1/a2, a1/a3)·R —
  только ядро (`trend.matrix`), в ответах — `matrix [[..],[..],[..]]` у `GET /trends`.

## Вкладка «Статистика» (`/api/v1/stats/tab/*`, 16.17; формы — перенос BF `routers/stats.py`)

Тело — точки вкладки (как прочие `/stats/tab/*`, ≤ 20 000 интервалов; больше — 422 `too_many_samples`).

- `POST /stats/tab/variogram` `{…, lag, nlag, lag_tol, directions: "omni" | "four", min_pairs}` →
  `{directions: [{az, h [], gamma [], n []}], sigma2, model?}`; 4 направления — 0°, 45°, 90°, 135°, допуск 22,5°.
- `POST /stats/tab/variogram-model` `{nugget, structures: [{it, cc, hmaj, hmin, azi}] (≤ 4), h_max, n}` →
  `{directions: [{az, h [], gamma []}]}` — и без данных.
- `POST /stats/tab/map` `{…, cell, n, min_pairs}` → `{u [], v [], gamma [][], n [][]}`; N < порога — `null`.
- `POST /stats/tab/local` `{…, grid {nx ≤ 100, ny ≤ 100}, k, power, radius, realizations 5–300, seed, threshold?}`
  → `{grid, mean [][], sd [][], p10 [][], p90 [][], prob [][] | null}`; пусто — `null`.

## Команды (ADR §13) → маршруты

`variography.calc` → `experimental` (`what: axes`) / `map` (`fan`, `map`); `variography.fit` → `fit`;
`variography.save` → `POST` / `PUT models`; `variography.delete` → `DELETE models/{id}`; `variography.export` /
`import` → `export` / `import` (`path`); `trend.save` → `POST` / `PUT trends`; `trend.delete` → `DELETE trends/{id}`.
`variography.open`, `.models`, `.ellipsoid`, `.show`, `trend.show` — только интерфейс.
