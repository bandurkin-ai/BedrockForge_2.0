# Э19 и Э19б — контракт неявного моделирования (для дорожек Х, К1, К2, К3, К4, Т, П, И-А, И-Б, Ф; уточняется по коду в 19.30)

Источник истины — ADR 0026 (решения 1–30, «API», «Команды реестра», «Точность и приёмка»), ТЗ 8.9 (8.9.4 —
умолчания и диапазоны), 8.0, 7.35 Д-15, Д-24, Д-25, Д-31…Д-34, Д-39; граф — ADR 0025. Разбор параметров —
`core/bf_core/model/{spec,spec_parts}.py` (19.1, готово). Образцы — `core/tests/data/implicit/{params,
contract_answers}.json`; **`contract_answers.json` — черновик форм** (кроме `defaults`, `bad_param`, `refusals` —
их пишет `spec.py`): настоящие ответы маршрутов пишет 19.16 (`BF_REGEN_IMPLICIT=1`), интерфейс принимается только на
них. Изменение формы — только через оркестратора. Префикс маршрутов — `/api/v1/implicit` (роутер `model/router.py`).

Пути: `M/` = `core/bf_core/model/`, `G/` = `core/bf_core/geo/`, `I/` = `ui/src/modules/implicit/`.

## 1. Общие правила

- `X-BF-Token`, Host/Origin — как у всего ядра. Координаты, значения поля — float64; пусто — `null` в JSON (NaN в
  массивах), **никогда не 0** (8.0.1). Число на входе — число или текст «0,5» / «0.5». Нет ключа / `null` / `""` —
  умолчание. Коды групп — текст (число → `format(x, ".15g")`, как Э16).
- Ошибка — `{code, message, hint, param?, …}` по-русски с подсказкой (§7); `param` — путь в теле: `"h"`,
  `"pou.n_min"`, `"boundary.box.4"`, `"groups.2.codes.0"`, `"groups.1.params.thresholds.3"`, `"anisotropy.ratios.2"`.
  Нет проекта — 409 `no_project`; запись в «только просмотр» — 423 `project_read_only` (POST/PUT/DELETE моделей и
  групп, `build`, `update`, `to_wireframe`, `relations`, `faults`, `vein_system`, `from_model`, `planned_holes`
  кроме `evaluate`, `compare`); `defaults`, `preview`, `check`, `support`, `compare_holes` работают.
- Оптимистичная блокировка: каждый пишущий запрос модели — `expected_version`; нет / не целое ≥ 1 — 422 `bad_param
  {param: "expected_version"}`; другая — 409 `version_conflict {version}` (до постановки задания и при фиксации).
  Модель в состоянии `building` — 409 `model_building` у всех пишущих, кроме отмены задания.
- Задание: ответ `{source: "jobs", id}`; ход — `GET /jobs/{source}/{id}`; синхронные проверки (404, 409, 422) — до
  постановки. Итог несёт `events: [[имя, данные]]`. Запись — последней стадией, целиком или ничего (объект, blobs,
  каркасы-выходы, `lineage.record` — одна транзакция); отмена — между участками и кирпичами, ничего не оставляет.
- Синхронные расчёты (`preview`, `check`, `support`, `compare_holes`, `planned_holes/*/evaluate`) — слот HEAVY
  (`stats.routes_charts.SLOTS`), обгон по `channel` / `seq` (409 `superseded`, как Э17 §1).
- События: `implicit.changed {kind: "implicit_model", id, deleted?}` — после записи и удаления; `objects.changed
  {kind: "wireframe", id}` — выходы; `geostat.changed {kind: "trend", id}` — структурный тренд; `drillholes.changed
  {set_id}` — «Модель на скважины»; Э19б — `implicit.changed {kind: "planned_hole", id}`.
- Пока маршрут не подключён в сборке — 422 `not_ready`. Группы Э19б (`fault`, `vein_system`) и отношения ≠
  `conformable` разбираются `spec.py` уже в Э19; задание Э19 отвечает на них 422 `not_ready` до 19.32–19.36.
- Побитность (Т8.9-8, Т8.9-13, Т8.9-16, ТД39-3): результат не зависит от порядка точек в таблицах, числа потоков
  (1, 2, 4, 8), порядка кирпичей и от того, полное это построение или «Обновить».

## 2. Объект, blobs, кеш, граф (решения 22–26; миграций нет)

```
objects(kind="implicit_model", parent_id=NULL).meta v1 — дерево «Неявные модели»
  = spec.parse_model(тело).resolved(extent).canonical()                         — параметры, §3–§4
  + v 1; algo "rbf-1"; versions {numpy, scipy, numba, skimage, manifold3d}; created_at
  + extent [xmin, ymin, zmin, xmax, ymax, zmax]   охват границы (box или bbox каркаса), float64; origin = центр охвата
  + state "ready" | "building" | "error"; error {code, message, hint, group?} | null
  + outputs [{wireframe_id, kind: "surface" | "body" | "line", group: gid | null, wing: 0 | 1 | null, threshold?,
             name}]   group null + kind body — «Прочее»; line — Э19б (line_layer_id вместо wireframe_id)
  + stats {points, patches, max_residual, cond_max, eta_max, seconds, reused {patches, bricks},
           by_group [{group, wing, points, patches, h_bar, max_residual, cond_max, eta_min, eta_max, support_share}]}
  + inputs [{kind, id, fp, name}] (§2.3); last_run {at, job, mode: "build" | "update", seconds} | null
object_refs(src=model → trend, role="trend")   — удаление тренда — 409 in_use
blobs owner=object(model): implicit_points, implicit_patches, implicit_solution (роли — db/schema.py, оркестратор)
cache\implicit\<model_id>\<sha кирпича>.npy — f8 значения узлов кирпича (производное, 6.5; можно удалить)
```

Выходы — каркасы `commit_wireframe(parent_id = модель, meta.source {op: "implicit", model_id, group, wing?,
threshold?})`, имена «‹модель› · ‹группа›», «… · поверхность», «‹модель› · Прочее»; удаление модели удаляет их.
«В каркас» — копия без родителя, вход графа — модель. Каркасы-выходы при «Обновить» переписываются на месте (тот же
id, версия + 1), лишние удаляются, новые добавляются.

### 2.1. Blobs

| Роль | Формат | Содержимое (dtype) |
|---|---|---|
| `implicit_points` | Parquet zstd, метаданные `bf.implicit` = `{v: 1, groups: [gid…], sources: [имя…]}` | `x, y, z` f8 (проекта); `f` f8; `kind` i1 (индекс `spec.POINT_TYPES`: 0 contact, 1 inside, 2 outside, 3 manual, 4 value); `group` i4; `wing` i1 (−1 — нет, Э19б 0/1); `src` i4 (номер в `sources`; −1 — нет); `from`, `to` f8 (глубины по стволу; NaN у слоёв); `vertex` i8 (номер вершины слоя; −1 у скважин). Порядок: по `group`, внутри — канонический (решение 4) |
| `implicit_patches` | Parquet zstd | `group` i4, `wing` i1, `j` i4 (номер участка в группе), `cx, cy, cz` f8 (центр в пространстве октодерева), `r` f8, `n` i4, `offset` i8 (начало λ в `implicit_solution`), `hash` fixed 32 Б (SHA-256), `eta` f8, `cond` f8 |
| `implicit_solution` | `.npy` f8[Σ(n + 4)] | по строкам `implicit_patches`: λ (n), затем c (4: c0, cx, cy, cz) |

Состав участков (номера точек) не хранится: `octree.build` детерминирован (решение 8) и восстанавливает его по
`implicit_points` (≤ 1 с на 100 тыс.); хеши участков сверяются с `hash`, несовпадение — «устарела» (`algo`).

### 2.2. Кеш «Обновить» (Т8.9-8)

Участок берётся из `implicit_solution` прежнего построения, если совпал его `hash` = SHA-256(центр, R, точки в
каноническом порядке (координаты пространства участка, f), `params_key`), где `params_key` — байты (`algo`, ядро, a,
режим и s / cond_target, A или A_j участка). Кирпич берётся из `cache\…\<sha>.npy`, если совпал SHA-256(ijk кирпича,
h, L, хеши всех участков, покрывающих кирпич, по номерам). Всё остальное считается тем же кодом, поэтому обновление
побитно равно полному построению.

### 2.3. Входы и «устарела» (решение 25)

`("object:implicit_model", id)`; fp = SHA-256(`spec.hash_params()` + sha blobs `implicit_points`,
`implicit_solution` + `algo`). `meta.inputs` (порядок — как здесь):

| Вход | `kind`, `id` | `fp` |
|---|---|---|
| набор скважин (траектории) | `dh_set`, id | ключ траекторий (`variogram.store.trajectories_key`) |
| таблица кодов; таблица проб оболочки | `dh_table`, id | `table_token:rev` |
| композиты оболочки | `dh_result`, id | sha результата |
| слой ручных данных | `object:line_layer`, id | `version` |
| тренд | `object:trend`, id | `version` |
| каркас границы, расширения, обрезки | `wireframe`, id | sha геометрии |
| ЦТМ границы, обрезки | `object:dtm`, id | `version` |

Каркас-выход — вход `object:implicit_model`. Структурный тренд — входы `dh_table` (замеры), `dh_set` или `wireframe`
(опорный). Таблица «Модель на скважины» — `dh_result` со входами `object:implicit_model` или `wireframe` (каждый) и
`dh_set`. Э19б: `object:planned_hole` — источник (fp = `version`). Поставщик — `lineage/adapt_implicit.py` (19.11).
Прямая проверка — `M/store.freshness(conn, meta) -> {stale, stale_reason, source_lost}`; причины: «изменена таблица
«…»», «изменены траектории», «изменён слой «…»», «изменён тренд «…»», «изменён каркас «…»», «изменена ЦТМ «…»»,
«источник удалён». Перестроение графа — `POST /models/{id}/update`.

### 2.4. `.bfggis` (решение 26)

`M/remap.remap_meta(meta, ids: Remap) -> meta` — перенумеровывает `boundary.*_id`, `post.clip_id`, `trend_id`,
`data.set_id`, `data.table`, `groups[].params.{result_id, set_id, table, wireframe_id}`, `manual[].layer_id`,
`outputs[].wireframe_id | line_layer_id`, `inputs[].id`; fp узла — без номеров объектов. Строка вызова в
`archive/importer.py` — оркестратор. Структурный тренд — `geostat/remap.py` (`source.set_id`, `table`,
`wireframe_id`).

## 3. Параметры модели — `spec.parse_model`

```json
{"name": "Литология", "boundary": {"box": [0, 0, -300, 1000, 800, 100], "wireframe_id": null, "dtm_id": 41},
 "h": null, "levels": 2, "kernel": "linear", "kernel_radius": null,
 "smoothing": {"mode": "manual", "s": null}, "trend_id": null, "anisotropy": null,
 "pou": {"n_patch": 1000, "n_min": 100, "delta": 0.25},
 "data": {"set_id": 3, "table": "12", "field": "LITH", "d_max": 30, "step": 5},
 "groups": [{"id": 1, "name": "Дайка", "type": "domain", "codes": ["DY"]},
            {"id": 2, "name": "Песчаник", "type": "domain", "codes": ["SS", "SSc"]}],
 "manual": [{"layer_id": 77, "role": "contact", "group": 2}],
 "post": {"v_min": 0, "simplify": false, "clip": "boundary", "clip_id": null}}
```

| Ключ | Умолчание | Диапазон / правило | `param` ошибки |
|---|---|---|---|
| `name` | «Неявная модель» (store; занято — « (2)») | ≤ 200 | `name`; 409 `name_taken` |
| `boundary` | — | ровно одно из `box` (6 чисел, min < max — иначе 422 `boundary_empty`) и `wireframe_id` (замкнутый — иначе 422 `wireframe_open`); `dtm_id` — граница ∩ тело под ЦТМ (№ 100) | `boundary`, `boundary.box.N`; 404 `wireframe_not_found`, `dtm_not_found` |
| `h` | диагональ охвата / 200, в пределах 0,1–100 м (`resolved`) | 0,1–100 м; > 2²⁰ ячеек по оси — 422 | `h` |
| `levels` (L) | 2 | 0–4, целое | `levels` |
| `kernel` | `linear` | `linear` (−r), `cubic` (r³), `wendland` ((1 − r/a)⁴₊·(4r/a + 1)) | `kernel` |
| `kernel_radius` (a) | null — 2·R участка | > 0 м; только `wendland` (иначе не хранится) | `kernel_radius` |
| `smoothing.mode` | `manual` | `manual` / `auto` (Д-25) | `smoothing.mode` |
| `smoothing.s` | null — 0 для контактов, 0,05 для содержаний (по виду группы, `spec.s_of`) | 0–10; в `auto` — начальное η₀ = s·h̄^k | `smoothing.s` |
| `smoothing.cond_target` | 1e8 (только `auto`) | 1e4–1e12 | `smoothing.cond_target` |
| `trend_id` / `anisotropy` | null / null — A = I | одно из двух; тренд глобальный (A = `trend.matrix`) или структурный (A_j); `anisotropy {ang1 [0; 360], ang2 [−90; 90], ang3 [−180; 180], ratios [1, r2, r3], r > 0}` — та же форма, что у тренда, A = `trend.matrix(anisotropy)` | `trend_id` (404 `trend_not_found`), `anisotropy.*` |
| `pou.n_patch` (N_уч) | 1000 | 300–4000, целое | `pou.n_patch` |
| `pou.n_min` (N_min) | 100 | 20–500, целое, ≤ N_уч | `pou.n_min` |
| `pou.delta` (δ) | 0,25 | 0,1–1 | `pou.delta` |
| `data` | null — только группы без кодов | `{set_id, table (id текстом), field (поле кода), d_max 1–500 м (30), step Δ_т 0,5–50 м (5)}` | `data.*`; 404 `set_not_found`, `table_not_found`, 422 `field_not_found` |
| `groups` | [] | ≤ 50; §4; по возрасту — первая молодая; без `id` — номер max + 1 по порядку | `groups.N.…` |
| `manual` | [] | ≤ 100 `{layer_id, role: contact (умолч.) / inside / outside, group}`; группа должна быть | `manual.N.…`; 404 `layer_not_found` |
| `post.v_min` | 0 — выкл. | ≥ 0 м³ | `post.v_min` |
| `post.simplify` | false | pyfqmr, отклонение ≤ 0,1·h (решение 13) | `post.simplify` |
| `post.clip` / `clip_id` | `boundary` / null | `none`, `boundary`, `wireframe` + id, `dtm` + id | `post.clip_id` |

- Число точек модели > 2 млн — 422 `too_many_points {points}` (№ 101) — store при сохранении (оценка) и задание.
- `canonical()` — что хранится; `hash_params()` — без `name`, с `algo`; ключи `META_ONLY` разбор пропускает:
  `parse_model(meta)` работает. Диапазоны для формы — `spec.ranges_json()`, умолчания — `spec.defaults_json()`.
- Внутренние константы (не параметры пользователя): охват октодерева +10 %, R × 1,25, совпадение точек 1e−9 м,
  узел f = 0 → +1e−9·h, треугольник < 1e−12·h², Ланцош ≤ 30, допуск cond 5 %, η_max = 1e−2·max abs(Φ), зерно 12345
  (`spec_parts.MARGIN`, `PATCH_GROW`, `COINCIDE`, `ZERO_NUDGE`, `MIN_AREA`, `LANCZOS_MAX`, `COND_TOL`, `ETA_MAX_FACTOR`,
  `SEED`). η = `spec.eta(kernel, s, h̄)`: s·h̄ (linear), s·h̄³ (cubic), s (wendland); h̄ — медиана расстояния до
  ближайшей точки по всем точкам группы (крыла) в пространстве A (совпадающие дают 0 и входят).

## 4. Группы — `spec.parse_group`; `{id?, name, type, codes, relation?, faults?, params?}`

| `type` | `codes` | `params` (умолчание) | Данные (8.9.2) |
|---|---|---|---|
| `domain` | коды D (1–1000) или пусто при ручном слое группы | — | контакты по стволу, s(t), ручные |
| `shell` | пусто | `source: composites` (`result_id`) / `table` (`set_id?` — по умолчанию `data.set_id`, `table`); `field`; `transform: linear` / `log` / `nscore`; `mode: numeric` / `indicator`; `thresholds` 1–20 строго ↑; `p` 0,5 (0,3–0,7, только indicator) | значения g (решение 6) |
| `vein` | коды жилы | `bound: hull` / `polygon` (`polygon [[u, v] ≥ 3]`); `offset` 0 (0–10 000 м); `t_min` 0 (0–100 м) | кровля / подошва интервалов (решение 17) |
| `expand` | пусто | `wireframe_id`; `delta` ≠ 0, −1000…1000 м | SDF (решение 18) |
| `fault` (Э19б) | коды разлома или ручной слой | `radius` R_F > 0 м, null — бесконечный | контакты, точки, линии |
| `vein_system` (Э19б) | = коды жил | `veins [{code, name?, bound, polygon, offset, t_min}]`, 2–20, порядок — возраст (первая молодая) | как `vein` |

- `relation` (умолч. `conformable`; `erode`, `onlap`, `intrusion` — Э19б) несёт **молодая** группа: как она
  поступает с группами ниже (старшими). У `fault` отношения и разломов нет. `faults` — id групп вида `fault`.
- В модели: имена и id уникальны, код — в одной группе, коды требуют `data`, у `domain` / `vein` / `fault` — коды
  или ручной слой; `with_group(g)` — заменить по id или добавить последней (старшей).
- Тело группы (решение 15): Э19 — D = {f_D < 0} ∩ граница минус тела групп выше; «Прочее» = граница минус
  объединение. Оболочки (знак как у доменов: внутри f < 0): числовая — одно RBF значений T(g) (T — преобразование),
  поле порога f = T(t) − RBF; индикаторная — RBF значений [g ≥ t] − 0,5 на каждый порог, f = (p − 0,5) − RBF;
  оболочка k = {f_k < 0} ∩ оболочка k − 1; выходы `kind: body` с `threshold`.

## 5. Маршруты (`/api/v1/implicit`)

| Маршрут | Тело / запрос | Ответ |
|---|---|---|
| `GET /defaults` (трактовка 1) | — | `{params: spec.defaults_json(), ranges: spec.ranges_json()}` |
| `GET /models` | — | `{models: [{id, name, version, state, stale, stale_reason, source_lost, groups, outputs, stats {points, patches, seconds} \| null, created_at, updated_at}]}` (по имени) |
| `POST /models` | §3 | `{id, name, version: 1}`; store: ссылки (404), поля, `object_refs` тренда, `extent`, `inputs`; 422, 423 |
| `GET /models/{id}` | — | `{id, name, version, meta, state, error, stale, stale_reason, source_lost, outputs: [{wireframe_id, kind, group, wing, threshold, name, volume, area, triangles}], created_at, updated_at}`; 404 `model_not_found` |
| `PUT /models/{id}` | `{expected_version, …ключи §3}` | `{id, name, version}`; ключи верхнего уровня заменяют хранимые (`groups` — весь список: порядок, удаление) |
| `DELETE /models/{id}` | — | `{deleted, wireframes: [id]}`; окно — `lineage.impact` (askImpact) до запроса |
| `POST /models/{id}/groups` | `{expected_version, group}` | `{id, version, group: canonical}` (новая — последней) |
| `PUT /models/{id}/groups/{gid}` | `{expected_version, group}` | `{id, version, group}`; 404 `group_not_found` |
| `POST /models/{id}/points/preview` | `{group_id \| group, limit? (200 000; 1–2 млн), channel?, seq?}` | `{group, n, truncated, counts {contact, inside, outside, manual, value, merged, dropped_log, no_position}, notes, points {x, y, z, f, kind}}` — столбцы, первые `limit` в каноническом порядке; ≤ 200 мс на 100 тыс. |
| `POST /models/{id}/build` | `{expected_version, threads? (1–64)}` | `{source: "jobs", id}` → `implicit.build` (§6) |
| `POST /models/{id}/update` | то же | `implicit.update`; без прежнего результата — как полное |
| `GET /models/{id}/check` | `?group&page=1&size=500&sort=residual\|source&format=json\|csv` | §8.1; 409 `not_built` |
| `POST /models/{id}/support` | `{wireframe_id?, threshold? (2·D_max), format? json\|f4}` | §8.2 |
| `POST /models/{id}/compare_holes` | `{set_id, table, field, page?, size?, channel?, seq?}` | §8.3 |
| `POST /models/{id}/to_wireframe` | `{wireframe_id, name?}` | `{wireframe_id, name}` (копия выхода без родителя) |
| `POST /drillholes/from_model` (маршрут `drillholes`) | §8.4 | `{source: "jobs", id}` → `drillholes.from_model` |
| `POST/PUT /geostat/trends` (`kind: structural`), `GET /geostat/trends/{id}/discs` | §9 | Д-15 |
| Э19б: `PUT /models/{id}/relations`, `POST /models/{id}/faults`, `PUT /models/{id}/groups/{gid}/faults`, `POST /models/{id}/vein_system`; `/planned_holes…` | §10 | — |

## 6. Задания

| Вид | Очередь | Маршрут | Стадии (`spec.STAGES`) | Модуль |
|---|---|---|---|---|
| `implicit.build` | heavy | `POST /models/{id}/build` | «Данные», «Участки», «Изоповерхности», «Тела», «Проверки» | `M/job.py` |
| `implicit.update` | heavy | `POST /models/{id}/update` | те же (+ число взятых из кеша в `message`) | `M/job.py` (`M/update.py`) |
| `drillholes.from_model` | heavy | `POST /drillholes/from_model` | «Траектории», «Пересечения», «Запись» | `drillholes/from_model.py` |

Итог `implicit.build` / `.update`: `{model_id, version, state, outputs: [{wireframe_id, kind, group, wing,
threshold, name, volume, area, triangles}], stats (§2), reused {patches, bricks}, journal: [строки], events}`.
Провал проверок 8.9.3 п. 7 у группы — `state: "error"`, `error {code: "iso_check", message «Поверхность группы
«…» не прошла проверки: …», hint, group}`; каркасы не пишутся, прежние остаются. `memory_mb(params)` — точки ×
(N_уч + 4)² × 8 Б × потоки + кирпичи. Строки оркестратора: `jobs/registry.py` — `bf_core.model.job`,
`bf_core.drillholes.from_model`; `ops/warm.py` — шаг `implicit` (`M/warm.warm`).

## 7. Ошибки (решение 30; тексты — `spec_parts.REFUSALS`, `spec.refuse(code, …)`)

| Код | Статус | Когда; машинные поля |
|---|---|---|
| `bad_param` | 422 | разбор §3–§5; `param` |
| `rbf_singular` | 422 | вырожденный участок; `{group, wing, patch, sources: [«A-1 12,0–14,0 м», …] (≤ 20), points}` |
| `too_many_points` | 422 | > 2 млн точек; `{points}` |
| `wireframe_open` | 422 | граница / расширение / обрезка не замкнуты; `{param, wireframe_id}` |
| `trend_not_found` | 404 | `{param: "trend_id"}` |
| `trend_structural` | 422 | структурный тренд в оценке 8.8 (№ 99) — `geostat/estimate` |
| `no_contacts` | 422 | у группы (крыла) нет смены знака данных; `{group, wing?}` |
| `boundary_empty` | 422 | охват min ≥ max, граница ∩ ЦТМ пуста; `{param}` |
| `model_not_found` / `group_not_found` | 404 | — |
| `model_building` / `not_built` | 409 | модель строится / ещё не построена |
| `iso_check` | 422 (в `meta.error`) | проверки 8.9.3 п. 7; `{group}` |
| `version_conflict`, `name_taken`, `in_use` | 409 | как Э18 |
| `project_read_only` | 423 | §1 |

## 8. Проверка и скважины (Д-32, Д-33, Д-34 ч. 1)

### 8.1. `GET …/check` (Д-32 п. 1)

`{group, n, max_abs_residual, page, size, total, rows: [{i, group, type, source, from, to, vertex, x, y, z,
f_model, f_data, residual, distance}]}`; `type` — подпись по-русски («контакт», «внутри», «снаружи», «ручная»,
«значение»), `source` — «A-1» / имя слоя; `residual` = f_model − f_data; `distance` — до каркаса группы
(`nearest_triangles`, м; NaN — нет каркаса); сортировка по abs(residual) ↓ (равные — по i). `format=csv` — те же
столбцы, заголовки по-русски, разделитель и кодировка — как у выгрузки таблиц (`drillholes/export`).

### 8.2. `POST …/support` (Д-32 п. 2)

`{threshold, surfaces: [{wireframe_id, group, area, area_beyond, share_beyond, max_distance}], values?}`; `values` —
расстояние вершины до ближайшей точки данных группы (`cKDTree`, м) в порядке вершин каркаса: `format=json` —
массив (≤ 500 тыс. вершин, иначе 422 `bad_param {param: "format"}` с подсказкой f4), `f4` — `application/octet-stream`
float32 LE (только для показа, 8.0.1). Одна поверхность — `wireframe_id`; без него — `values` нет. Доля
`share_beyond` пишется в `stats.by_group[].support_share`.

### 8.3. `POST …/compare_holes` (Д-33)

`{model_codes: [имя группы…, «Прочее», «Вне модели»], data_codes: [код…], length: [[м]], count: [[n]], agree_length,
total_length, agree_share, mismatches {page, size, total, rows: [{hole, hole_id, from, to, data_code, model_code,
length}]}}`; интервал таблицы → код модели `mode_by_length` по `body_stretches` тел модели (приоритет — порядок
групп); совпадение — код данных ∈ `codes` группы; несовпавшие по длине ↓, затем (скважина, от).

### 8.4. `POST /drillholes/from_model` (Д-34 ч. 1)

Тело: `{set_id, name, model_id \| mesh_ids: [id…], codes?: {gid \| wireframe_id: «код»}}` — тела → интервальная
таблица `FROM, TO, CODE` (промежутки — пустой код, не 0; код по умолчанию — имя группы / каркаса); или `{set_id,
name, block_model_id, field, table}` — поле блоков в серединах интервалов `table` (`locate_rows`; вне модели — пусто).
Итог: `{set_id, table_id, name, rows, holes, no_trajectory, report, events}`; 409 `table_exists`, 404, 422, 423.

## 9. Структурный тренд (Д-15 Э19, решение 27)

`POST /geostat/trends {kind: "structural", name, source: {type: "measurements", set_id, table, depth \| from+to,
alpha, dipdir} | {type: "mesh", wireframe_id}, radius R_тр (200; 10–5000 м), ratios [1, r2, r3], size, color}` →
`{id, name, version}`; `GET /geostat/trends/{id}` — + `n_measurements`; `GET /geostat/trends/{id}/discs` → `{radius,
discs: {x, y, z, nx, ny, nz}}` (точки замеров и нормали; у каркаса — центры граней прореженно ≤ 10 000). В оценке
8.8 — 422 `trend_structural` (№ 99). Источник `trend` ориентировки 8.5.2: глобальный — плоскость малой оси,
структурный — нормаль в середине интервала, `TT_SRC = "тренд"`.

## 10. Э19б — стек, разломы, система жил, проектные скважины

- `PUT /models/{id}/relations {expected_version, order: [gid…] (все группы кроме `fault`), relations: {"gid":
  relation}}` → `{id, version}` (`spec.parse_relations`).
- `POST /models/{id}/faults {expected_version, group: {name, type: "fault", codes?, params: {radius?}}}` → `{id,
  version, group}`; ручные слои разлома — `manual` модели (`group` = id разлома).
- `PUT /models/{id}/groups/{gid}/faults {expected_version, fault_ids: [id…]}` → `{id, version}`.
- `POST /models/{id}/vein_system {expected_version, group: {name, type: "vein_system", params: {veins}}}` → `{id,
  version, group}`. Выходы: тело каждой жилы (`group`, `code`), общее тело, линии пересечения — слои стрингов
  (`kind: line`, `line_layer_id`); `stats.vein_pairs [{a, b, line_length, volume_common}]`.
- Крылья: выходы с `wing` 0 (f_F < 0) / 1; `stats.by_group[].offset {mean, max}` — смещение (только чтение).
- `planned_holes`: `objects(kind="planned_hole").meta {v, collar [x, y, z], azimuth [0; 360), dip [−90; 90] (вниз
  — отрицательный, как 7.11), length (0; 10 000] м, change {azimuth, dip} °/100 м (−30…30) | target [x, y, z] |
  null, method "minimum_curvature", set_id?}`. `GET/POST/PUT/DELETE /planned_holes[/{id}]` → `{id, name, version,
  meta, trajectory {depth: [], x: [], y: [], z: []} (шаг 1 м)}`; `POST /planned_holes/{id}/evaluate {model_id |
  mesh_ids}` → `{intervals [{from, to, code}], nearest [{hole, hole_id, distance, depth}] (≤ 5 у цели)}`; `…/compare
  {set_id, hole}` → `{rows [{depth, dx, dy, dz, d}], max}` (шаг 1 м); `…/export {format: csv | dxf}` → файл
  (`collar.csv` + `survey.csv` или DXF-линия).

## 11. Интерфейсы Python (дорожки работают параллельно по этим сигнатурам)

Соглашения: float64; NaN — пусто; numba `cache=True, nogil=True`, без `prange`, `fastmath`; координаты ядер —
локальные (`x − origin`, origin = центр `extent`); BLAS в 1 поток внутри участка (`threadpool_limits(1)`); потоки
над участками / полосами точек / кирпичами; результат побитно один при 1, 2, 4, 8 потоках. `Tick = Callable[[float,
str], None]` (может бросить отмену). Цепочка: **data → octree → solve → evaluate → iso → post → bodies → job**.

```python
# M/spec.py, M/spec_parts.py (19.1, готово) — см. докстроки
parse_model(body) -> ModelSpec   # .resolved(extent) .canonical() .hash_params() .group(gid) .s_of(g) .with_group(g)
                                 # поля: name, boundary, h, levels, kernel, kernel_radius, smoothing, trend_id,
                                 # anisotropy, pou (n_patch, n_min, delta), data (set_id, table, field, d_max, step),
                                 # groups (Group: id, name, type, codes, relation, faults, params), manual, post
parse_group(raw, param) -> Group; parse_build -> BuildSpec(expected_version, threads); parse_preview -> PreviewSpec
parse_check(q) -> CheckSpec; parse_support(body, d_max) -> (wireframe_id, threshold); parse_compare -> CompareSpec
parse_relations(body, spec) -> ModelSpec; eta(kernel, s, h_bar); resolve_h(diag); refuse(code, msg?, hint?, **extra)
POINT_TYPES (i1-коды точек), KERNELS, ETA_POWER, RANGES, REFUSALS, STAGES, ALGO = "rbf-1"

# M/data.py, data_holes.py, data_manual.py (19.7, К3)
@dataclass(frozen=True)
class Points:                       # точки данных модели, по группам, внутри — канонический порядок (решение 4)
    xyz: f8[n, 3]                   # проекта
    f: f8[n]; kind: i1[n]           # значение; индекс spec.POINT_TYPES
    group: i4[n]; wing: i1[n]       # id группы; −1 / 0 / 1 (Э19б)
    src: i4[n]; span: f8[n, 2]; vertex: i8[n]   # источник (номер в sources), от–до по стволу (NaN), вершина слоя (−1)
    sources: list[str]; counts: dict; notes: list[str]
    n -> int; take(rows) -> Points; of_group(gid, wing=-1) -> Points
    to_parquet() -> bytes; from_parquet(data: bytes) -> Points          # blob implicit_points (§2.1)
load(project, spec: ModelSpec, *, groups=None, cancel=None) -> Points   # все группы; 422 too_many_points, no_contacts
canonical_order(xyz, f, kind) -> intp[n]                                # lexsort (x, y, z, f, kind)
merge_coincident(xyz, f, tol=COINCIDE) -> intp[m]                       # оставшиеся; равное f — слито, разное — оба
data_holes.domain_points(lines: iv.Lines, in_d: bool[k] | None (None — нет кода: разрыв), traj: Trajectories,
                         dense: i8[holes], *, d_max, step, group: int) -> Points        # контакты, s(t), ±D_max
data_holes.vein_sides(lines, in_v, traj, dense, *, group) -> (hanging: f8[a, 3], footwall: f8[b, 3])
data_manual.layer_points(xyz: f8[v, 3], offsets: i8[l + 1], closed: bool[l], *, role, d_max, step, group, layer) -> Points
data.grade_points(xyz: f8[n, 3], g: f8[n], *, transform, thresholds) -> (values f8[n] | f8[n, K], notes)

# M/kernels.py (19.2, К1)
KIND = {"linear": 0, "cubic": 1, "wendland": 2}
@njit(inline="always") phi(kind: int, r: float, a: float) -> float
@njit(inline="always") pou_w(r: float) -> float                       # W(r) = (1 − r)⁴₊·(4r + 1)
@njit phi_block(kind, a, z: f8[n, 3], out: f8[n, n]) -> None           # Φ_ij = φ(‖z_i − z_j‖)
# M/system.py (19.2, К1)
@dataclass(frozen=True)
class PatchSolution: lam: f8[n]; c: f8[4]; eta: float; cond: float    # cond₁ (LAPACK gecon)
assemble(kind, a, z: f8[n, 3], radius: float, eta: float) -> f8[n + 4, n + 4]   # [Φ + ηI, P; Pᵀ, 0], P = [1, z/R]
solve_patch(kind, a, z, f: f8[n], radius, eta) -> PatchSolution        # SingularPatch(cond) — rbf_singular выше

# M/octree.py (19.3, К1)
@dataclass(frozen=True)
class Patches:
    centre: f8[m, 3]; radius: f8[m]            # в пространстве октодерева (A — глобальная; исходное — структурная)
    start: i8[m + 1]; members: i8[k]           # CSR: точки участка j — members[start[j]:start[j + 1]] (по возрастанию)
    box: f8[6]                                 # охват октодерева (+10 %)
    m -> int
build(y: f8[n, 3], *, n_patch, n_min, delta, box: f8[6] | None = None) -> Patches   # от порядка и потоков не зависит
patch_hash(centre: f8[3], radius, z: f8[n, 3], f: f8[n], key: bytes) -> bytes        # SHA-256 (§2.2)

# M/solve.py, evaluate.py (19.4, К1); anisotropy.py (19.9); smoothing.py (19.10)
@dataclass(frozen=True)
class Field:                                   # решённое поле группы (крыла)
    kernel: int; a: f8[m]                      # a участка (NaN — не wendland)
    origin: f8[3]; A: f8[3, 3]                 # глобальная A (I — без анизотропии); y = (x − origin)·Aᵀ
    A_local: f8[m, 3, 3] | None                # структурная: z = A_j·(x − origin − c_j); иначе z = y − c_j
    patches: Patches; z: f8[k, 3]              # рабочие координаты точек участков (CSR как members)
    lam: f8[k]; coef: f8[m, 4]; eta: f8[m]; cond: f8[m]; hashes: list[bytes]; h_bar: float
SolutionCache = Mapping[bytes, PatchSolution]  # 19.12 — из implicit_patches / implicit_solution
solve(xyz: f8[n, 3], f: f8[n], *, spec: ModelSpec, group: Group, origin: f8[3], A: f8[3, 3],
      local_axes: Callable[[f8[m, 3]], f8[m, 3, 3]] | None = None, cache: SolutionCache | None = None,
      threads=1, tick=None) -> Field           # 422 rbf_singular {patch, sources}
evaluate(field: Field, xyz: f8[q, 3], *, threads=1, tick=None) -> f8[q]   # PoU; NaN вне всех участков (не 0)
field_fn(field: Field, *, threads=1) -> FieldFn
h_bar(y: f8[n, 3]) -> float                    # медиана расстояния до ближайшей (cKDTree k = 2)
anisotropy.matrix_of(spec, trend_meta | None) -> f8[3, 3]; anisotropy.local(trend_meta, centres_xyz) -> f8[m, 3, 3]
smoothing.auto_eta(kind, a, z, radius, eta0, target) -> (eta, cond2)   # Д-25; детерминирован (PCG64(12345))

# G/iso.py, iso_bricks.py (19.5, К2) — поле любого вида: RBF, SDF расширения, поле крыла
FieldFn = Callable[[f8[q, 3]], f8[q]]          # координаты проекта → f; NaN → +H (8.9.3 п. 6)
@dataclass(frozen=True)
class Grid: lo: f8[3]; h: float; levels: int; n: i8[3]   # n — ячеек шага h, кратно 2^L; H = h·2^L; coarse = n / 2^L
    H -> float; coarse -> i8[3]
grid_of(extent: f8[6], h, levels) -> Grid      # lo = угол охвата, охват покрыт целиком
class BrickCache(Protocol): get(key: bytes) -> f8[N] | None; put(key: bytes, values: f8[N]) -> None
@dataclass(frozen=True)
class Surface: vertices: f8[v, 3]; triangles: i4[t, 3]; bricks: i8[b, 3]; stats: dict   # треугольники канонично
isosurface(field: FieldFn, grid: Grid, *, domain=False, cache: BrickCache | None = None,
           brick_key: Callable[[i8[3]], bytes] | None = None, threads=1, tick=None) -> Surface
# G/iso_post.py (19.6, К2); G/solids.canonical (19.6)
@dataclass(frozen=True)
class PostResult: vertices: f8[v, 3]; triangles: i4[t, 3]; removed_parts: int; simplified: bool; journal: list[str]
postprocess(v, t, *, h, post: Post, clip: tuple[f8[a, 3], i4[b, 3]] | None = None, threads=1) -> PostResult
mesh_failure(v, t, *, h) -> str | None         # причина по-русски (8.9.3 п. 7) или None — прошёл
solids.canonical(v, t) -> (f8[v, 3], i4[t, 3]) # вершины по (x, y, z), треугольник от меньшего номера, сортировка
# M/expand.py (19.6, К2)
expand_field(v: f8[a, 3], t: i4[b, 3], delta: float, *, H: float, threads=1) -> FieldFn   # d − Δ; wireframe_open

# M/bodies.py, shells.py, veins.py (19.13–19.15, К3 / К4)
@dataclass(frozen=True)
class Output: kind: str; group: int | None; wing: int | None; threshold: float | None; name: str
              vertices: f8[v, 3]; triangles: i4[t, 3]; volume: float | None; area: float
@dataclass(frozen=True)
class Solved: group: Group; wing: int | None; field: Field | None; fn: FieldFn; points: Points   # после маски
build_bodies(spec, solved: Sequence[Solved], boundary: tuple[f8, i4], grid: Grid, *, cache=None, threads=1,
             tick=None) -> list[Output]        # маска → решение → тело → вычитание; «Прочее» последним
mask(group: Group, points: Points, younger: Sequence[Solved]) -> bool[n]    # Э19 — все True; Э19б — решение 19
shells.shell_outputs(spec, group, fn_by_threshold, boundary, grid, *, threads) -> list[Output]   # k ⊂ k − 1
veins.vein_body(hanging: f8[a, 3], footwall: f8[b, 3], params: Mapping, h: float) -> tuple[f8, i4] | None

# M/store.py, remap.py (19.11, Х); M/job.py, job_stages.py, update.py (19.12, Х)
KIND = "implicit_model"; create(conn, store, project, spec, author=None) -> {id, name, version: 1}
update(conn, store, project, model_id, raw, expected_version) -> {id, name, version}; delete(conn, store, id)
get(conn, id) -> форма §5; list_rows(conn); freshness(conn, meta); load_fields(conn, store, id) -> dict[(gid, wing), Field]
KIND_BUILD = "implicit.build"; KIND_UPDATE = "implicit.update"; run_build(engine, folder, params, tick, *, update=False)
# Д-32, Д-33 (19.17, 19.19, П): check.table(...), check.support(...), compare_holes.compare(...) — формы §8
# drillholes/from_model.py (19.18): KIND = "drillholes.from_model"; on_holes(traj, dense, bodies: Sequence[coding.Body],
#     *, threads) -> coding.Stretches (body_stretches по стволам целиком); at_midpoints(view, field, lines, traj) -> f8[n]
# geostat/trend_struct.py (19.8, Т): local_axes(trend_meta, xyz: f8[m, 3], *, source) -> f8[m, 3, 3]
#     строки — u1 (простирание), u2 (падение), u3 (нормаль); A_j = diag(1, 1/r2, 1/r3)·R_j
```

## 12. Команды реестра (лента «Моделирование → Неявные модели»; Ctrl+K, ассистент; клавиш нет)

| Команда | Признаки | Маршрут / окно |
|---|---|---|
| `implicit.new {name?, box?, wireframe_id?, h?}` | — | без параметров — мастер (граница, шаг, данные, интерполяция); иначе `POST /models` |
| `implicit.domains {model_id?, codes?}`, `implicit.shells {model_id?, …}`, `implicit.vein {model_id?, codes?}`, `implicit.expand {model_id?, wireframe_id?, delta?}` | — | окно группы; с параметрами — `POST /models/{id}/groups` |
| `implicit.structural {model_id?, set_id?, table?}` | — | замеры → структурный тренд модели (`POST /geostat/trends`, затем `PUT /models/{id} {trend_id}`) |
| `implicit.update {model_id?}` | — | `POST /models/{id}/update` (новая модель — `build`) |
| `implicit.to_wireframe {model_id, wireframe_id}` | — | `POST /models/{id}/to_wireframe` |
| `implicit.delete {model_id}` | confirm | `DELETE /models/{id}` после `askImpact` |
| `implicit.check {model_id}`, `implicit.support {model_id, threshold?}` | — | §8.1, §8.2 |
| `implicit.compare_holes {model_id, set_id, table, field}` | — | §8.3; переход в 7.17 |
| `drillholes.from_model {set_id, model_id \| mesh_ids, name}` | — | §8.4 |
| `trend.save {kind: "structural", …}` | confirm | §9 |
| Э19б: `implicit.relations {model_id, order, relations}`, `implicit.fault {model_id, fault_id?, data?, radius?}`, `implicit.group_faults {group_id, fault_ids}`, `implicit.vein_system {model_id, veins}`, `planned_hole.create {…}`, `planned_hole.evaluate {id, model_id}`, `planned_hole.compare {id, hole_id}` | — | §10 (Главная › Скважины › Проектная скважина) |

Без параметров — окно, с параметрами — выполняет (ADR 0021 п. 9); списки — текст через «;». Значки `implicit`,
`fault`, `vein`, `planned_hole` — `shell/Icon.tsx` (оркестратор).

## 13. Фикстуры

- `core/tests/data/implicit/params.json` — модели `domains`, `shells_auto`, `vein_expand`, `stack_b` (Э19б): `body` →
  `canonical` (`spec.parse_model(body).canonical()`); круговой проход — `test_implicit_spec.py`;
  `BF_REGEN_IMPLICIT=1` переписывает `canonical`.
- `core/tests/data/implicit/contract_answers.json` — **черновик** форм §5–§10 (`{status, body}`); настоящие — 19.16
  (`BF_REGEN_IMPLICIT=1 uv run pytest -q tests/test_implicit_api_answers.py`, синтетика `tools/gen/implicit.py`).
  Ключи `defaults`, `bad_param`, `refusals` уже настоящие (`spec.py`; `test_implicit_spec.py` сверяет).
- `tests/fixtures/implicit/` — складка 100 скважин, слои, сфера-интрузия, жилы (19.7, зерно 12345).

## 14. Трактовки (вне буквы ТЗ и ADR; на согласование оркестратора)

1. `GET /implicit/defaults` — форма мастера (умолчания и диапазоны), как `/estimate/defaults`; в списке ADR «API» нет.
2. Отношение (`relation`) несёт молодая группа и описывает её действие на старшие (Д-24 «у группы — отношение к
   вышележащим» читается со стороны молодой: ТД31-3 «молодой слой выше, отношение «Эрозия»»); у самой старшей
   отношение ни на что не влияет.
3. `kernel_radius` a по умолчанию — 2·R участка (ТЗ умолчания не даёт); `post` (V_min, упрощение, обрезка) — один на
   модель (8.9.3 п. 8 «по выбору»); обрезка по умолчанию — границей.
4. Умолчания вне 8.9.4: `t_min` 0 м, отступ границы жилы 0 м, p — 0,5 (есть в ТЗ), `threads` 1–64, h — в пределах
   диапазона после «диагональ / 200», > 2²⁰ ячеек по оси — отказ; страница 1–5000 строк (500), предпросмотр
   ≤ 2 млн точек (200 тыс.); групп ≤ 50, кодов ≤ 1000, порогов ≤ 20, ручных слоёв ≤ 100, жил ≤ 20.
5. Код — в одной группе модели; новая группа — последней (старшей); группы без `id` нумеруются max + 1 по порядку.
6. Д-33: «код модели» — имя группы; совпадение — код данных входит в `codes` группы; столбцы «Прочее», «Вне модели».
7. Индикаторная оболочка порога t: поле [g ≥ t] − 0,5, поверхность на уровне p − 0,5 (ТЗ 8.9.3).
8. Д-34 ч. 1: промежутки ствола вне тел групп — пустой код (не 0); тело «Прочее» не кодируется.
