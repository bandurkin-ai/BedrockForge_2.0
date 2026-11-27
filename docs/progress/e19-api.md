# Э19 и Э19б — контракт неявного моделирования (для дорожек Х, К1, К2, К3, К4, Т, П, И-А, И-Б, Ф)

**Состояние: Э19 — по коду (подшаг 19.30, сверено с `core/bf_core/model/*`, `geo/iso*.py`, `drillholes/from_model*.py`,
`geostat/trend_struct.py`); Э19б (стек, разломы, система жил, проектные скважины) — по коду (подшаг 19.41, сверено с
`model/{stack,faults,faults_parts,vein_system,job_stack,routes_stack,store_lines}.py`, `drillholes/{planned,planned_eval,
planned_export,planned_store,routes_planned}.py`): §10 и поправки Э19б в §1, §2, §4, §5, §7, §11, §12, §14.**

Источник истины — ADR 0026 (решения 1–30, «API», «Команды реестра», «Точность и приёмка»), ТЗ 8.9 (8.9.4 —
умолчания и диапазоны), 8.0, 7.35 Д-15, Д-24, Д-25, Д-31…Д-34, Д-39; граф — ADR 0025. Разбор параметров —
`core/bf_core/model/{spec,spec_parts}.py` (19.1). Образцы — `core/tests/data/implicit/{params,contract_answers}.json`;
`contract_answers.json` — **настоящие ответы ядра** для маршрутов Э19 и Э19б (пишут `test_implicit_contract.py` и
`test_implicit_spec.py`, `BF_REGEN_IMPLICIT=1`: ключи `relations`, `fault_*`, `group_faults*`, `vein_system_*`,
`build_stack_*`, `model_stack`, `model_veins`, `planned_*`); ключи `trend_*` (структурный тренд) и `build_iso_error` —
образцы форм. Интерфейс принимается только на настоящих ответах. Изменение формы — только через оркестратора.
Префикс маршрутов — `/api/v1/implicit` (роутер `model/router.py`).

Пути: `M/` = `core/bf_core/model/`, `G/` = `core/bf_core/geo/`, `I/` = `ui/src/modules/implicit/`.

## 1. Общие правила

- `X-BF-Token`, Host/Origin — как у всего ядра. Координаты, значения поля — float64; пусто — `null` в JSON (NaN в
  массивах), **никогда не 0** (8.0.1). Число на входе — число или текст «0,5» / «0.5». Нет ключа / `null` / `""` —
  умолчание. Коды групп — текст (число → `format(x, ".15g")`, как Э16).
- Ошибка — `{code, message, hint, param?, …}` по-русски с подсказкой (§7); `param` — путь в теле: `"h"`,
  `"pou.n_min"`, `"boundary.box.4"`, `"groups.2.codes.0"`, `"groups.1.params.thresholds.3"`, `"anisotropy.ratios.2"`.
  Нет проекта — 409 `no_project`; запись в «только просмотр» — 423 `project_read_only` (POST/PUT/DELETE моделей и
  групп, `build`, `update`, `to_wireframe`, `relations`, `faults`, `vein_system`, `from_model`, `planned_holes`
  кроме `evaluate`, `compare`); `defaults`, `preview`, `check`, `check.csv`, `support`, `compare_holes` работают
  (`support` пишет в `meta.stats.by_group[].support_share` без смены версии, в «только просмотр» — не пишет, `recorded:
  false`).
- Оптимистичная блокировка: каждый пишущий запрос модели — `expected_version`; нет / не целое ≥ 1 — 422 `bad_param
  {param: "expected_version"}`; другая — 409 `version_conflict {version}` (до постановки задания и при фиксации).
  Модель в состоянии `building` — 409 `model_building` у всех пишущих, кроме отмены задания.
- Задание: ответ `{source: "jobs", id}`; ход — `GET /jobs/{source}/{id}`; синхронные проверки (404, 409, 422) — до
  постановки. Итог несёт `events: [[имя, данные]]`. Запись — последней стадией, целиком или ничего (объект, blobs,
  каркасы-выходы, `lineage.record` — одна транзакция); отмена — между участками и кирпичами, ничего не оставляет.
- Синхронные расчёты: `points/preview` и `compare_holes` — слот HEAVY, обгон по `channel` / `seq` (409 `superseded`,
  как Э17 §1); `check` и `support` — обычные синхронные запросы без обгона (результат `check` кешируется на 3 модели
  по файлам и каркасам, из которых он сделан); Э19б: `planned_holes/*/evaluate`.
- События: `implicit.changed {kind: "implicit_model", id, deleted?}` — после записи и удаления; `objects.changed
  {kind: "wireframe", id}` — выходы; `geostat.changed {kind: "trend", id}` — структурный тренд; `drillholes.changed
  {set_id}` — «Модель на скважины»; Э19б — `implicit.changed {kind: "planned_hole", id}`.
- Группы Э19б (`fault`, `vein_system`) и отношения ≠ `conformable` разбираются `spec.py` с Э19; строятся с 19.36
  (`job_stack`). Отказ `not_ready` (`job_stages.not_ready`) в коде остался, но не выдаётся: `refuse_unready` теперь —
  `job_stack.check_stack` (синхронно до задания: 422 `bad_relations` — цикл порядка расчёта, 422 `bad_param` —
  несуществующий разлом, больше 8 разломов у групп, число полей крыльев > 127).
- Побитность (Т8.9-8, Т8.9-13, Т8.9-16, ТД39-3): результат не зависит от порядка точек в таблицах, числа потоков
  (1, 2, 4, 8), порядка кирпичей и от того, полное это построение или «Обновить».

## 2. Объект, blobs, кеш, граф (решения 22–26; миграций нет)

```
objects(kind="implicit_model", parent_id=NULL).meta v1 — дерево «Неявные модели»
  = spec.parse_model(тело).resolved(extent).canonical()                         — параметры, §3–§4
  + v 1; algo "rbf-1"; versions {numpy, scipy, numba, skimage, manifold3d}; created_at
  + extent [xmin, ymin, zmin, xmax, ymax, zmax]   охват границы (box или bbox каркаса), float64; origin = центр охвата
  + state "ready" | "building" | "error"; error {code, message, hint, group?} | null
  + outputs [{wireframe_id, kind: "surface" | "body", group: gid | null, wing: код | null, threshold?, name, code?,
             common?, region?}]   group null + kind body — «Прочее» (Э19б: у «Прочее» тоже `wing`); `code` — тело жилы
             системы (код жилы), `common` — общее тело системы, `region` — область крыла разлома (пространство, не порода)
             + линии пересечения жил: {line_layer_id, kind: "line", group, wing: null, name, pair: [a, b]} (вместо wireframe_id)
  + stats {points, patches, max_residual, cond_max, eta_max, seconds, reused {patches, bricks}, balance,
           by_group [{group, wing, points, patches, h_bar, max_residual, cond_max, eta_min, eta_max, support_share,
                      faults: [fid], offset: {mean, max} | null, offset_by_fault: {"fid": {mean, max}} | null}],
           stack {order, relations, dropped, dropped_total}, faults {"fid": {...}}, vein_pairs [...]   — Э19б, §10}
           balance — относительная разность суммы объёмов тел и объёма границы (Т8.9-5); `support_threshold` у группы
           пишет «Карта опоры»; max_residual — наибольший модуль невязки в точках данных, м
  + inputs [{kind, id, fp, name}] (§2.3); last_run {at, job, mode: "build" | "update", seconds, params} | null
object_refs(src=model → trend, role="trend")   — удаление тренда — 409 in_use
blobs owner=object(model): implicit_points, implicit_patches, implicit_solution (роли — db/schema.py, оркестратор)
cache\implicit\<model_id>\<sha кирпича>.npy — f8 значения узлов кирпича (производное, 6.5; можно удалить)
```

Выходы — каркасы `commit_wireframe(parent_id = модель, meta.source {op: "implicit", model_id, group, wing?,
threshold?, code?, common?, region?})`, имена «‹модель› · ‹группа›», «… · поверхность», «‹модель› · Прочее»; Э19б:
крыло — «‹группа›, крыло N», область крыла разлома — «‹разлом›, крыло N (область)», жила системы — «‹модель› ·
‹система› · ‹жила›», общее тело — «… · общее тело», «Прочее, крыло N»; линии пересечения жил — слои стрингов
(`store_lines`: файл линий — потомок модели, слой на пару «‹модель› · пересечение A × B», атрибуты «Пара», «Длина, м»;
каждый запуск пишет файл заново — правка слоя в редакторе стрингов при «Обновить» теряется); удаление модели удаляет их.
«В каркас» — копия без родителя, вход графа — модель. Каркасы-выходы при «Обновить» переписываются на месте (тот же
id, версия + 1), лишние удаляются, новые добавляются.

### 2.1. Blobs

| Роль | Формат | Содержимое (dtype) |
|---|---|---|
| `implicit_points` | Parquet zstd, метаданные `bf.implicit` = `{v: 1, groups: [gid…], sources: [имя…]}` | `x, y, z` f8 (проекта); `f` f8; `kind` i1 (индекс `spec.POINT_TYPES`: 0 contact, 1 inside, 2 outside, 3 manual, 4 value); `group` i4; `wing` i1 (−1 — нет; Э19б: код крыла Σ sign_k·2^k, у одного разлома 0 (f_F < 0) / 1; частичные поля конечного разлома — `(mask + 1)·2^m + код`, m — разломы группы; наибольшее 127: `job_stack.wing_id`, иначе 422 `bad_param {param: "faults"}`); `src` i4 (номер в `sources`; −1 — нет); `from`, `to` f8 (глубины по стволу; NaN у слоёв); `vertex` i8 (номер вершины слоя; −1 у скважин). Порядок: по `group`, внутри — канонический (решение 4) |
| `implicit_patches` | Parquet zstd, без метаданных | `group` i4, `wing` i1, `j` i4 (номер участка в группе), `cx, cy, cz` f8 (центр шара: при глобальной A — в пространстве A·x, при структурном тренде — в координатах проекта), `r` f8 (R после роста до N_min), `n` i4, `offset` i8 (начало λ в `implicit_solution`), `hash` fixed 32 Б (`S32`, SHA-256), `eta` f8 (м, в единицах φ), `cond` f8 (cond₁, 6 значащих цифр). **Дополнительно только при структурном тренде** — девять столбцов `a00, a01, …, a22` f8: матрица A_j участка по строкам (`MATRIX_COLUMNS`); при глобальной A и без анизотропии столбцов нет (файл прежний), `store.load_fields` отдаёт `GroupData.matrices` (None — нет) |
| `implicit_solution` | `.npy` `<f8` одномерный, Σ(n + 4) | по строкам `implicit_patches` (по `offset`): λ (n), затем c (4: c0, cx, cy, cz) — c в нормированных координатах участка (x − центр) / масштаб; масштаб — R (глобальная A) или R·‖A_j‖₂ (структурный тренд), хранится не отдельно, а восстанавливается из строк участков |

Состав участков (номера точек) не хранится: `octree.build_patches` детерминирован (решение 8) и восстанавливает его по
`implicit_points` (≤ 1 с на 100 тыс.); хеши участков сверяются с `hash`, несовпадение — «устарела» (`algo`).

### 2.2. Кеш «Обновить» (Т8.9-8)

Участок берётся из `implicit_solution` прежнего построения (`update.PatchCache.from_blobs`), если совпал его `hash` =
`octree.hash_patches(points, patches, values=f, salt=update.salt_of(spec, η₀, A))`: SHA-256 от метки версии, соли (ядро,
радиус a, режим сглаживания, η₀ и цель cond, байты глобальной A), центра, R, числа точек, точек участка в пространстве
участков (в каноническом порядке) и их значений f; у структурного тренда в хеш входит A_j (`anisotropy.patch_hashes`).
η участка — функция его точек и η₀, поэтому в хеш не входит. Кирпич берётся из `cache\implicit\<id>\<sha>.npy` (f8
n × n × n, значения узлов), если совпал SHA-256(`Grid.token()`, индекс кирпича, хеши всех участков, шар которых
(R·(1 + 1e−6)) задевает его коробку, по номерам; у оболочки — ещё порог). Всё остальное считается тем же кодом, поэтому
обновление побитно равно полному построению; после запуска `update.sweep` удаляет файлы, которых запуск не касался. Полное
построение кирпичи пишет и не читает (`BrickFiles(read=False)`).

### 2.3. Входы и «устарела» (решение 25)

`("object:implicit_model", id)`; fp = SHA-256(`spec.hash_params()` + sha blobs `implicit_points`,
`implicit_solution` + `algo`). `meta.inputs` (порядок — как здесь):

| Вход | `kind`, `id` | `fp` |
|---|---|---|
| набор скважин (траектории): набор таблицы кодов, таблицы проб оболочки, набор композитов оболочки | `dh_set`, id | ключ траекторий (`variogram.store.trajectories_key`) |
| таблица кодов; таблица проб оболочки | `dh_table`, id | `table_token:rev` |
| композиты оболочки | `dh_result`, id | sha результата |
| слой ручных данных | `object:line_layer`, id | `version` |
| тренд | `object:trend`, id | `version` |
| каркас границы, расширения, обрезки | `wireframe`, id | sha геометрии |
| ЦТМ границы, обрезки | `object:dtm`, id | `version` |

Каркас-выход — вход `object:implicit_model`. Структурный тренд — входы `dh_table` (замеры), `dh_set` или `wireframe`
(опорный). Таблица «Модель на скважины» — узел **`dh_table`** (не `dh_result`: `composites/*` не меняются) со входами
`object:implicit_model` или `wireframe` (каждый), `bm_field` + `dh_table` (поле блочной модели) и `dh_set`;
«Перестроить с зависимыми» для неё — 422 `lineage_rebuild_unsupported`. Э19б: `object:planned_hole` — источник (fp = `version`). Поставщик — `lineage/adapt_implicit.py` (19.11).
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
- Внутренние константы (не параметры пользователя): охват октодерева +10 % (`octree.MARGIN`, у каждой стороны, после
  A), **предел точек в шаре участка `octree.BALL_FACTOR = 3`** (лист делится дальше, пока в шаре его участка до роста
  больше 3·N_уч точек; решение 19.4б, §14), совпадение точек 1e−9 м, узел f = 0 → +1e−9·h, треугольник < 1e−12·h²,
  Ланцош ≤ 30, допуск cond 5 %, η_max = 1e−2·max abs(Φ), зерно 12345 (`spec_parts.MARGIN`, `PATCH_GROW`, `COINCIDE`,
  `ZERO_NUDGE`, `MIN_AREA`, `LANCZOS_MAX`, `COND_TOL`, `ETA_MAX_FACTOR`, `SEED`); `cond(A) ≤ 1e6`
  (`anisotropy.COND_MAX`, иначе 422 `bad_anisotropy`). η = `spec.eta(kernel, s, h̄)`: s·h̄ (linear), s·h̄³ (cubic), s (wendland); h̄ — медиана расстояния до
  ближайшей точки по всем точкам группы (крыла) в пространстве A (совпадающие дают 0 и входят).

## 4. Группы — `spec.parse_group`; `{id?, name, type, codes, relation?, faults?, params?}`

| `type` | `codes` | `params` (умолчание) | Данные (8.9.2) |
|---|---|---|---|
| `domain` | коды D (1–1000) или пусто при ручном слое группы | — | контакты по стволу, s(t), ручные |
| `shell` | пусто | `source: composites` (`result_id`) / `table` (`set_id?` — по умолчанию `data.set_id`, `table`); `field`; `transform: linear` / `log` / `nscore`; `mode: numeric` / `indicator`; `thresholds` 1–20 строго ↑; `p` 0,5 (0,3–0,7, только indicator) | значения g (решение 6) |
| `vein` | коды жилы | `bound: hull` / `polygon` (`polygon [[u, v] ≥ 3]`: метры в плоскости жилы от `veins.fit_plane().origin`, оси u, v — главные компоненты контактов); `offset` 0 (0–10 000 м); `t_min` 0 (0–100 м) | кровля / подошва интервалов (решение 17) |
| `expand` | пусто | `wireframe_id`; `delta` ≠ 0, −1000…1000 м | SDF (решение 18) |
| `fault` (Э19б) | коды разлома или ручной слой (роли `contact` / `inside` / `outside`) | `radius` R_F — число > 0 до `LEN_MAX` (100 000 м), null — бесконечный | контакты, точки, линии (как у домена) |
| `vein_system` (Э19б) | = коды жил (по порядку `veins`) | `veins [{code, name?, bound, polygon, offset, t_min}]`, 2–20, порядок — возраст (первая молодая); `name` по умолчанию = код; коды жил различны | как `vein`, по коду |

- `relation` (умолч. `conformable`; `erode`, `onlap`, `intrusion` — Э19б, строятся с 19.32/19.34) несёт **молодая**
  группа: как она поступает со **всеми** группами ниже (старшими) — одно отношение на группу. Ядро принимает только
  эти четыре слова (`spec_parts.RELATIONS`); `erosion` и русские «Согласно / Эрозия / Налегание / Интрузия» понимает
  разбор команды `implicit.relations` (`ui … stackApi.parseRelation`), не маршрут. Отношение есть только у доменов
  (`domain`): у оболочки, жилы, расширения, системы жил и разлома — 422 `bad_param {param: "relations.N"}`.
  `faults` — id групп вида `fault` (только у домена).
- В модели: имена и id уникальны, код — в одной группе, коды требуют `data`, у `domain` / `vein` / `fault` — коды
  или ручной слой; `with_group(g)` — заменить по id или добавить последней (старшей).
- Тело группы (решение 15): Э19 — D = {f_D < 0} ∩ граница минус тела групп выше; «Прочее» = граница минус
  объединение. Оболочки (знак как у доменов: внутри f < 0): числовая — одно RBF значений T(g) (T — преобразование),
  поле порога f = T(t) − RBF; индикаторная — RBF значений [g ≥ t] − 0,5 на каждый порог, f = (p − 0,5) − RBF;
  оболочка k = {f_k < 0} ∩ оболочка k − 1; выходы `kind: body` с `threshold`. Стек (отношения), разломы (крылья),
  интрузия, система жил — §10.

## 5. Маршруты (`/api/v1/implicit`)

| Маршрут | Тело / запрос | Ответ |
|---|---|---|
| `GET /defaults` (трактовка 1) | — | `{params: spec.defaults_json(), ranges: spec.ranges_json()}` |
| `GET /models` | — | `{models: [{id, name, version, state, stale, stale_reason, source_lost, groups, outputs, stats {points, patches, seconds} \| null, created_at, updated_at}]}` (по имени) |
| `POST /models` | §3 | `{id, name, version: 1}`; store: ссылки (404), поля, `object_refs` тренда, `extent`, `inputs`; 422, 423 |
| `GET /models/{id}` | — | `{id, name, version, meta, state, error, stale, stale_reason, source_lost, outputs: [{wireframe_id, kind, group, wing, threshold, name, volume, area, triangles}], created_at, updated_at}`; 404 `model_not_found` |
| `PUT /models/{id}` | `{expected_version, …ключи §3}` | `{id, name, version}`; ключи верхнего уровня заменяют хранимые (`groups` — весь список: порядок, удаление) |
| `GET /models/{id}/outputs` | — | `{outputs: [{wireframe_id, kind, group, wing, threshold, name, volume, area, triangles}]}` |
| `DELETE /models/{id}` | — | `{deleted, wireframes: [id]}`; окно — `lineage.impact` (askImpact) до запроса; выход, на который ссылаются другие объекты, — 409 `in_use {usage: [{id, kind, name, role: "output"}]}`, ничего не удалено |
| `POST /models/{id}/groups` | `{expected_version, group}` | `{id, version, group: canonical}` (новая — последней); заданный `id`, уже занятый, — 422 `bad_param {param: "group.id"}` |
| `PUT /models/{id}/groups/{gid}` | `{expected_version, group}` | `{id, version, group}`; 404 `group_not_found`; группа сохраняет место и номер |
| `DELETE /models/{id}/groups/{gid}` | `?expected_version=` или в теле | `{id, version, deleted: gid}`; слои ручных данных группы и ссылки на неё у разломов других групп уходят вместе с ней |
| `POST /models/{id}/points/preview` (то же — `POST /models/{id}/points`) | `{group_id \| group, limit? (200 000; 1–2 млн), channel?, seq?}` | `{group, n, truncated, counts {contact, inside, outside, manual, value, merged, conflicts, no_trajectory, no_depth}, notes, points {x, y, z, f, kind}}` — столбцы, первые `limit` в каноническом порядке; `group` — ещё не сохранённая группа формы; у `vein` / `expand` точек нет (`n: 0`, примечание); ≤ 200 мс на 100 тыс. (тёплый кеш) |
| `POST /models/{id}/build` | `{expected_version, threads? (1–64)}` | `{source: "jobs", id}` → `implicit.build` (§6) |
| `POST /models/{id}/update` | то же | `implicit.update`; без прежнего результата — как полное |
| `GET /models/{id}/check` (CSV — также `GET …/check.csv`) | `?group&page=1&size=500&sort=residual\|source&format=json\|csv&highlight=` | §8.1; 409 `not_built` |
| `POST /models/{id}/support` | `{wireframe_id?, threshold? (2·D_max), format? json\|f4\|f4mesh}` | §8.2 |
| `POST /models/{id}/compare_holes` | `{set_id, table, field, page?, size?, channel?, seq?}` | §8.3 |
| `POST /models/{id}/to_wireframe` | `{wireframe_id, name?}` | `{wireframe_id, name}`: копия выхода без родителя, имя «‹выход› (копия)» (свободное), вход графа — модель; 404 `wireframe_not_found` |
| `POST /drillholes/from_model` (маршрут `drillholes`) | §8.4 | `{source: "jobs", id}` → `drillholes.from_model` |
| `POST/PUT /geostat/trends` (`kind: structural`), `GET /geostat/trends/{id}/discs` | §9 | Д-15 |
| Э19б: `PUT /models/{id}/relations`, `POST /models/{id}/faults`, `PUT /models/{id}/groups/{gid}/faults`, `POST /models/{id}/vein_system` (`model/routes_stack.py`) | §10 | как правка группы: `{id, version[, group]}` |
| Э19б: `/planned_holes` (отдельный префикс, не `/implicit`; `drillholes/routes_planned.py`) | §10 | — |

## 6. Задания

| Вид | Очередь | Маршрут | Стадии (`spec.STAGES`) | Модуль |
|---|---|---|---|---|
| `implicit.build` | heavy | `POST /models/{id}/build` | «Данные», «Участки», «Изоповерхности», «Тела», «Проверки» | `M/job.py` |
| `implicit.update` | heavy | `POST /models/{id}/update` | те же (+ число взятых из кеша в `message`) | `M/job.py` (`M/update.py`) |
| `drillholes.from_model` | heavy | `POST /drillholes/from_model` | «Траектории», «Пересечения», «Запись» | `drillholes/from_model.py` |

Стадии (`spec.STAGES`), последняя запись — «Запись» (`job_stages.S_WRITE`, одна транзакция, не в `STAGES`). Итог
`implicit.build` / `.update`: `{model_id, version, state, mode: "build" | "update", outputs: [{wireframe_id, kind, group,
wing, threshold, name, volume, area, triangles}], stats (§2), reused {patches, bricks}, journal: [строки], stages
{«Данные», «Участки», «Изоповерхности», «Тела», «Проверки», «Запись», total: секунды}, events}`; `volume` у поверхности —
`null`. Э19б: `outputs` несёт и записи `kind: "line"` (`line_layer_id`, `pair`, `lines`, `length`; формы — §10.2), `events` —
`objects.changed {kind: "line_layer", id}` для слоёв линий; отказы стека, разломов и систем жил до задания — `job_stack.check_stack`.
Провал проверок 8.9.3 п. 7 (замкнутость, многообразие, самопересечения, объём > 0) у тела — задание падает с ошибкой
`iso_check {group}` («Тело «…» не прошло проверки: …»), модель получает `state: "error"` и `meta.error {code, message,
hint, group}`; каркасы не пишутся, прежние остаются. «Обновить» берёт кеш участков и кирпичей, только если прошлое
построение сделано той же программой (`algo` и версии `numpy`, `scipy`, `numba`, `skimage`, `manifold3d` в `meta`
совпали), иначе выполняется как полное. `memory_mb(params)` = 400 + 0,0012·точек + 160·потоки + 120 МБ (потоки — не более
`MAX_THREAD_MB` = 2048 МБ на матрицы участков). Отмена (`JobCancelled` на любой стадии) возвращает прежнее состояние
модели и ничего не пишет. Строки оркестратора: `jobs/registry.py` — `bf_core.model.job`,
`bf_core.drillholes.from_model`; `ops/warm.py` — шаг `implicit` (`M/warm.warm`).

## 7. Ошибки (решение 30; тексты — `spec_parts.REFUSALS`, `spec.refuse(code, …)`)

| Код | Статус | Когда; машинные поля |
|---|---|---|
| `bad_param` | 422 | разбор §3–§5; `param` |
| `rbf_singular` | 422 | вырожденный участок (< 4 точек, точки на плоскости или линии, совпавшие точки при η = 0, cond₁ ≥ 1/ε); `{group, wing: null, patch (0-based), sources: [«A-1 12,0–14,0 м», …] (≤ 20), points (число точек участка)}`; у ядра (`system.RbfSingular.extra`) ещё `reason` (`few_points`, `coplanar`, `coincident`, `ill_conditioned`), `cond1` |
| `rbf_bad_data` / `rbf_bad_points` / `rbf_few_points` / `rbf_no_points` | 422 | пропуск или бесконечность в координатах или значении точки (не заменяется нулём); точки и участки не согласованы; точек меньше N_min / нет точек (`octree`) |
| `bad_anisotropy` | 422 | A не конечна / вырождена / cond(A) > 1e6; тренд и анизотропия вручную вместе; у структурного тренда у центров участков нет осей (`extra.patches` — до 20 номеров, 0-based) |
| `no_bodies` | 422 | у модели нет тел для сверки (Д-33) |
| `iso_too_large` | 422 | охват слишком велик для шага h (ключ кирпича не умещается в 63 бита) или грубая сетка больше 1e8 узлов (`geo/iso.MAX_COARSE_NODES`); `IsoError`, текст с подсказкой увеличить h или L |
| `table_exists` | 409 | «Модель на скважины»: таблица с таким именем в наборе уже есть (ничего не заменяется) |
| `too_many_points` | 422 | > 2 млн точек; `{points}` |
| `wireframe_open` | 422 | граница / расширение / обрезка не замкнуты; `{param, wireframe_id}` |
| `trend_not_found` | 404 | `{param: "trend_id"}` |
| `trend_structural` | 422 | структурный тренд в оценке 8.8 (№ 99) — `geostat/estimate` |
| `no_contacts` | 422 | у группы (крыла) нет смены знака данных; `{group, wing?}`; у жилы — ещё `reason`: `few_contacts` (< 3 различных контактов на боку), `collinear`, `not_planar` |
| `boundary_empty` | 422 | охват min ≥ max, граница ∩ ЦТМ пуста; `{param}` |
| `model_not_found` / `group_not_found` | 404 | — |
| `model_building` / `not_built` | 409 | модель строится / ещё не построена; `check` — также у модели «устарела», если после построения изменены параметры поля (ядро и его радиус, сглаживание, анизотропия, пороги индикаторной оболочки): хеш участка из файлов не сходится с параметрами (`check._verify`) |
| `iso_check` | 422 (ошибка задания и `meta.error`) | проверки 8.9.3 п. 7 тела; `{group}`; `geo/iso_post.IsoError("iso_check")` — поверхность не прошла проверки после постобработки |
| `bad_relations` | 422 | Э19б: отношения образуют цикл порядка расчёта (`stack.order_of`); `{groups: [позиции]}`; проверяется в `PUT …/relations` и до задания (`job_stack.check_stack`) |
| `fault_outside` | 422 (ошибка задания) | Э19б: поверхность разлома не рассекает границу модели, одно из крыльев пусто; `{fault}` |
| `no_contacts` у крыла / после стека | 422 | Э19б: нет смены знака у данных группы после отбрасывания по отношениям стека (`{group, dropped}`), у крыла разлома (`{group, wing, faults}`), у самого разлома (`{group, fault}`); у жилы системы — `{group, …}` с названием жилы в тексте |
| `planned_hole_not_found`, `set_not_found`, `hole_not_found`, `wireframe_not_found` | 404 | Э19б: проектные скважины (§10) |
| `target_at_collar`, `target_beyond_length {need}`, `hole_no_trajectory`, `bad_format`, `wireframe_orientation` | 422 | Э19б: проектные скважины (§10) |
| `files_exist {files}`, `folder_not_found`, `path_not_absolute` | 409 / 404 / 422 | Э19б: выгрузка проектной скважины (§10) |
| `version_conflict`, `name_taken`, `in_use` | 409 | как Э18 |
| `project_read_only` | 423 | §1 |

## 8. Проверка и скважины (Д-32, Д-33, Д-34 ч. 1)

### 8.1. `GET …/check` (Д-32 п. 1)

`{model {id, name, version, state, stale, stale_reason, source_lost}, warnings, group, n, total, max_abs_residual,
highlight, over, page, size, sort, groups: [{group, name, type, wing, threshold, points, method: "field" | "identity",
max_abs_residual, wireframes: [id]}], rows: [{i, group, group_name, wing, threshold, type, source, from, to, vertex, x,
y, z, f_model, f_data, residual, distance, over}]}`. `i` — номер точки (строка `implicit_points`, с 0); `n` — число
точек в выборке (одно поле группы), `total` — число строк (индикаторная оболочка — строка на точку и порог);
`type` — подпись по-русски («контакт», «внутри», «снаружи», «ручная», «значение»), `source` — «A-1» / имя слоя;
`residual` = f_model − f_data; `distance` — до тела группы (`geo.nearest.nearest_triangles`: поверхность и грани
стыков с младшими группами и границей; м; `null` — у группы нет каркаса); `highlight` — порог подсветки, м (параметр
`highlight`, по умолчанию 0,1·h); `over` — число точек выше порога, у строки — признак. Сортировка: `residual` — по
abs(residual) ↓ (без невязки — последние; равные — по i), `source` — по имени источника, глубине, i. Поле берётся из
файлов построения (`PatchField` по сохранённым участкам; при Σ n² участков > 4·10⁸ — по тождеству `f_i − Σ W·η·λ / Σ W`,
`method: "identity"`); «устарела» — не ошибка, а `warnings`. У жил и расширений строк нет. `format=csv` (или
`GET …/check.csv`) — все строки выборки, разделитель «;», десятичная запятая, UTF-8 с BOM, заголовки по-русски («№»,
«Группа», «Порог», «Тип», «Источник», «От, м», «До, м», «Вершина», «X», «Y», «Z», «f модели», «f данных», «Невязка»,
«Расстояние до каркаса, м», «Выше порога»), пропуск — пустая ячейка; имя файла «Проверка модели № ‹id›.csv».

### 8.2. `POST …/support` (Д-32 п. 2)

`{model, warnings, threshold, d_max, recorded, surfaces: [{wireframe_id, name, group, wing, kind, vertices, area,
area_beyond, share_beyond, max_distance}], values?, values_n?}`; поверхности — выходы `surface` (у модели без них — тела
групп); `values` — расстояние вершины до ближайшей точки данных группы (`cKDTree`, м, `null` — у группы нет точек) в
порядке вершин каркаса `wireframe_id` (любой выход): `format=json` — массив (≤ 500 тыс. вершин, иначе 422 `bad_param
{param: "format"}` с подсказкой f4), `f4` — `application/octet-stream` float32 LE (только для показа, 8.0.1; остальной
ответ — JSON в заголовке `X-BF-Support`), **`f4mesh`** — тело ответа: вершины как float32-смещения от целого начала
`mesh.origin` (центр охвата, округлён до метра; x, y, z подряд), затем расстояния (float32, NaN — нет данных), затем
треугольники (uint32 по три), всё little-endian; описание `{origin, vertices, triangles}` — `mesh` в `X-BF-Support`
(показ любого каркаса, в т. ч. из нескольких частей, без сопоставления вершин). Доля `share_beyond` (площадь, которая
приходится на вершины за порогом: треть площади каждого треугольника на каждую вершину) пишется в
`stats.by_group[].support_share` вместе с `support_threshold`, версия не меняется.

### 8.3. `POST …/compare_holes` (Д-33)

`{model {id, name, stale_reason}, table {id, name, field}, model_codes: [имя группы (оболочка — «Группа ≥ порог»)…,
«Прочее», «Вне модели»], other_col, outside_col, data_codes: [код…] (+ «Нет кода» последней строкой, `no_code_row`),
length: [[м]], count: [[n]], agree: [[bool]], agree_length, total_length, coded_length, agree_share (null — нет
интервалов с кодом), no_code {count, length}, no_trajectory {holes, intervals, length}, intervals, holes, notes,
mismatches {page, size, total, rows: [{hole, hole_id, from, to, depth, data_code, model_code, length}]}}`; интервал
таблицы → код модели `composites.intervals.mode_by_length` по `body_stretches` тел модели на всём стволе (приоритет —
порядок групп; «Вне модели» участвует в голосовании своей длиной); совпадение — код данных ∈ `codes` группы или равен
имени столбца (без учёта регистра), «Прочее» и «Вне модели» не совпадают никогда; пустой код — не несовпадение и не в
базе `agree_share` (`coded_length`); `depth` — середина интервала (по ней окно 7.17 идёт к скважине); `no_trajectory` —
скважины таблицы без траектории (интервалы не сравниваются); несовпавшие по длине ↓, затем (скважина, от); страница
1–5000 строк (500).

### 8.4. `POST /drillholes/from_model` (Д-34 ч. 1)

Тело: `{set_id, name, model_id \| mesh_ids: [id…], codes?: {gid \| wireframe_id: «код»}}` — тела → интервальная
таблица `FROM, TO, CODE` (промежутки — пустой код, не 0; код по умолчанию — имя группы / каркаса); или `{set_id,
name, block_model_id, field, table}` — поле блоков в серединах интервалов `table` (`locate_rows`; вне модели — пусто).
Итог: `{set_id, table_id, name, rows, holes, no_trajectory, report {mode: "bodies" | "field", model | block_model,
bodies [{name, triangles, holes, inside, length, code}], codes [{code, rows, length}], holes, no_trajectory, stretches,
length_inside, length, rows, journal, seconds}, journal, events}`; `no_trajectory` — **число** скважин без траектории
или нулевой длины (в сверке Д-33 — объект `{holes, intervals, length}`); строки покрывают [0, длина] каждой скважины
(первое FROM = 0, последнее TO = длина пути побитно), промежутки — `CODE` пустой (не 0), два касающихся отрезка одного
кода (крылья) — одна строка; пересечения ствола с треугольниками — точные (`geo/intersect.py`, 1e−8 м), тела,
перекрывающиеся друг с другом, — приоритет первого; режим поля — значение числового поля блочной модели в середине
интервала (`blocks.geometry.locate_rows`; вне модели — пусто); таблица новая: занятое имя — 409 `table_exists`, ничего не
заменяется; запись — таблица в стороне, затем подмена и `lineage.record(dh_table, …)` в одной транзакции (отмена не
оставляет следов); 404, 422 (`wireframe_open`, `wireframe_orientation`), 423.

## 9. Структурный тренд (Д-15 Э19, решение 27; `geostat/{trend_struct,trend,trend_store,routes_trend}.py`)

`POST /geostat/trends {kind: "structural", name?, source: {type: "measurements", set_id, table, alpha, dipdir, depth?} |
{type: "mesh", wireframe_id, r_n?}, radius? (R_тр, 10–5000 м, 200), ratios? ([1, r2, r3]), size? (диаметр диска в 3D,
м), color?}` → `{id, name}`; имя по умолчанию «Структурный тренд»; `PUT /geostat/trends/{id}` `{expected_version, …}` →
`{id, name, version}`; `GET /geostat/trends[/{id}]` — у структурного нет углов, `matrix: null`, есть `source`,
`source_info {set_name, table_name, wireframe_name, lost}`, `source_lost`, `radius`, `ratios`, `size`, `color`; у
одного — `n_measurements` (null — каркас или источник удалён). Замеры: таблица набора, положение — поле `depth` по стволу
или середина FROM–TO, α (0–90°) и Aп (0–360°) — поля плоскости (ADR 0021 §2); замеры без положения (скважина без
траектории, нет глубины), с α и Aп вне диапазона или пустые отбрасываются и считаются в журнале (не 0). `r_n` (0–200 м,
0 — нормаль ближайшего треугольника) осредняет нормаль каркаса по площади треугольников в радиусе.

`GET /geostat/trends/{id}/discs` → `{radius, n, total, thinned, discs: {x, y, z, nx, ny, nz}}`: диск в каждом замере
(верхняя единичная нормаль; не более 50 000, прореживание равномерное) или в центрах граней каркаса (не более 10 000);
координаты проекта, float64; глобальный тренд — 422 `trend_not_structural`, источник удалён — 409 `source_gone`.

`trend_struct.local_axes(t, xyz, *, source, threads, tick, report)` → строки R_j: u1 — по простиранию, u2 — по падению
(`n × u1`), u3 = n — верхняя нормаль; нормаль в точке — главный собственный вектор Σ w·n·nᵀ замеров в R_тр, w = 1 /
max(d, 1е−6 м); нет замеров в R_тр — ближайший замер (в `report`, журнал); NaN-строка — у точки нет координат.
A_j = diag(1, 1/r2, 1/r3)·R_j (`anisotropy.local`: те же байты, что у `trend.matrix`).

В оценке 8.8 структурный тренд — 422 `trend_structural` (№ 99). Источник `trend` ориентировки 8.5.2 (`intercepts/orient.py`):
глобальный — плоскость малой оси (ось с наименьшим отношением, при равенстве — третья; = источник `global` с теми же
углами, 1e−12), структурный — нормаль в середине интервала по стволу, интервал без положения — пусто; `TT_SRC = "тренд"`.

## 10. Э19б — стек, разломы, система жил, проектные скважины (по коду, 19.41)

### 10.1. Маршруты `/implicit` (`model/routes_stack.py`)

Каждый — правка модели, как в `routes_groups.py` (`_edit`): вся модель проверяется заново, версия + 1, построенная
модель «устарела» до «Обновить»; 404 `model_not_found`, `group_not_found`; 409 `model_building`, `version_conflict
{version}`; 422 `bad_param {param}`; 423 `project_read_only`.

| Маршрут | Тело | Ответ; отказы |
|---|---|---|
| `PUT /models/{id}/relations` | `{expected_version, order: [gid…], relations: {"gid": relation}}` — `order`: все группы кроме `fault`, каждая один раз, первая — самая молодая (нет ключа — прежний порядок); `relations`: `conformable`, `erode`, `onlap`, `intrusion` (не названная группа сохраняет отношение); разломы остаются после остальных (`spec.parse_relations`) | `{id, version}`; 422 `bad_param {order}` (не те группы), `{relations.N}` (нет группы, неизвестное слово, отношение у не-домена), 422 `bad_relations` (цикл порядка расчёта, `job_stack.check_stack`) |
| `POST /models/{id}/faults` | `{expected_version, group: {name, type?: "fault", codes?, params: {radius?}}, manual?: [{layer_id, role}]}`; слои `manual` получают номер новой группы, роли `contact` / `inside` / `outside` | `{id, version, group}` (группа — последней); 422 `bad_param {group.type}` (не `fault`), `{group.id}` (занят), `{manual}`, `{groups.N.codes}` (ни кодов, ни слоёв) |
| `PUT /models/{id}/groups/{gid}/faults` | `{expected_version, fault_ids: [id…]}` (≤ 64; пустой список — не рассекается) | `{id, version}`; 422 `bad_param {fault_ids}` (группа не домен), `{fault_ids.N}` (не разлом) |
| `POST /models/{id}/vein_system` | `{expected_version, group: {name, type?: "vein_system", params: {veins: [{code, name?, bound, polygon, offset, t_min}]}}}` — 2–20 жил, первая — самая молодая | `{id, version, group}`; 422 `bad_param {group.params.veins}` (меньше двух), `{…veins.N.code}` (повтор кода) |

`group` в ответах — каноническая форма §4; новая группа — последней. Системе жил отношение не задаётся (в стек не входит),
разлому — тоже (в порядке расчёта он первый).

### 10.2. Построение — `build` / `update` (`job_stack.py`, `stack.py`, `faults.py`, `vein_system.py`)

```python
stack.build_stack(spec, points, boundary, *, relations=None, clip=None, matrix=None, matrices_of=None, eta_of=None,
                  solver=None, brick_cache=None, threads=1, tick=None) -> BodiesResult        # + stats["stack"]
faults.build_with_faults(<те же аргументы>) -> FaultsResult  # BodiesResult + faults {fid: Fit}, fields {gid: FaultedField}, wing_fits
vein_system.build_vein_system(veins: Sequence[VeinInput(code, name, hanging, footwall, params)], h, *, threads=1,
                  tick=None, dense_max=None) -> VeinSystemResult(parts, common, pairs, lines, stats, notes)
job_stack.build_domains(...)   # домены и разломы модели через build_with_faults → выходы, поля, части (Part)
job_stack.build_system(...)    # группа vein_system: тела жил, общее тело, линии пар, vein_pairs
```

Если разломов у доменов нет и все отношения «Согласно», `build_with_faults` делегирует `build_stack`, а тот —
`bodies.build_bodies`: результат побитно прежний (файлы Э19 те же). Побитность при 1, 2, 4, 8 потоках и «Обновить» =
полному построению (Т8.9-13, ТД31-4, ТД39-3).

**Отношения** (молодая группа → все старшие; маска — по ЗНАЧЕНИЮ поля другой группы в точке данных (`Fit.field`, RBF без
границы), не по каркасу; допуск «на поверхности» = max(1e−9·h̄, наибольшая невязка данных той группы)):

| `relation` | Слово | Данные | Тело |
|---|---|---|---|
| `conformable` | «Согласно» | не отбрасываются | молодая побеждает в перекрытии (хронология 8.9.2) |
| `erode` | «Эрозия» | у каждой старшей отбрасываются точки с f_молодой < допуск (внутри тела молодой и на её поверхности) | старшее − молодое; кровля старой срезана |
| `onlap` | «Налегание» | молодая отбрасывает свои точки с f_старшей < −допуск (ниже поверхности старшей) | молодое ∩ {f_старшей ≥ 0}; побеждает старшая |
| `intrusion` | «Интрузия» (Д-31) | у каждой старшей отбрасываются точки строго внутри тела (f_I < −допуск; контакт на поверхности I остаётся) | тело I вычитается из старших; само режется более молодыми по их отношению |

Порядок расчёта — топологический по «победителям» пар (молодая i, старшая j): победитель i для `conformable` / `erode` /
`intrusion`, j для `onlap`; среди свободных — наименьшая позиция (самая молодая). Группа маскируется уже решёнными;
цикл — 422 `bad_relations`. Нет контактов после маски — 422 `no_contacts {group, dropped}`. Журнал: «Стек отношений:
порядок расчёта — …», по группе: «Группа «…»: ‹отношение› группы «…» — отброшено точек N из M (…)».

**Разломы.** Группа `fault` — отдельное RBF f_F (данные как у домена: контакты, ±D, ручные слои); крылья — тело {f_F < 0}
(крыло 0) и остаток границы (крыло 1) (`WingTiling`: не пересекаются). Домен с непустым `faults` рассечён: данные делятся по
знаку f_F (|f_F| ≤ 1e−9·h или поле не определено — на оба крыла), у каждого крыла своё поле (тот же PoU), тело крыла =
{g_k < 0} ∩ область крыла − тела групп перед ним. Несколько разломов — области = комбинации знаков (порядок разломов — как
в списке модели, код крыла Σ sign_k·2^k); разлом рассекает только те домены, у которых он в `faults`; разломов у групп не
больше 8. Конечный разлом (`radius` R_F): g_k = w·f_k + (1 − w)·f_0, w = W(r/R_F) = (1 − t)⁴₊(4t + 1), r — расстояние до
ближайшего контакта разлома; за R_F поля крыльев = f_0 побитно (Т8.9-12). Смещение крыльев — результат (вдоль нормали
контакта), не параметр. Отказы: 422 `no_contacts {group, wing, faults}` (у крыла нет смены знака), `{group, fault}` (у
разлома нет контактов), `fault_outside {fault}`.

**Система жил.** Каждая жила целиком строится `veins.build_vein`; выклинившаяся целиком — без тела (журнал). Тело жилы j =
сырое R_j минус сырые тела всех более молодых жил; общее тело = объединение сырых (Σ объёмов вырезанных = объём общего);
пара (молодая a, старшая b), сырые тела которых встречаются, даёт строку `{a, b, line_length, volume_common}` (объём
R_a ∩ R_b ДО выреза) и линии `crossing.intersection_lines` (по паре; линии разных пар не сливаются); не встречаются —
строки нет. Общая зона отдельного кода не имеет (вопрос № 94): она у более молодой жилы. Обрезка модели (`post.clip`)
режет каждое тело системы.

**Выходы** (имена и формы записей — §2):

| Выход | `kind` | `group` | `wing` | Прочее |
|---|---|---|---|---|
| лист разлома внутри границы | `surface` | разлом | null | «‹модель› · ‹разлом› · поверхность» |
| область крыла разлома | `body` | разлом | 0 / 1 | `region: true` — пространство, не порода (в сумму объёмов пород не входит); «…, крыло N (область)» |
| домен без разломов | `surface`, `body` | домен | null | как в Э19 |
| крыло рассечённого домена | `surface`, `body` | домен | код крыла | «‹группа›, крыло N» |
| «Прочее» | `body` | null | код области (при разломах), иначе null | «Прочее, крыло N» |
| жила системы | `body` | система | null | `code` — код жилы; «‹модель› · ‹система› · ‹жила›» |
| общее тело системы | `body` | система | null | `common: true`; «… · общее тело» |
| линия пересечения пары | `line` | система | null | `{line_layer_id, pair: [a, b], lines, length}` вместо `wireframe_id`; «‹модель› · пересечение A × B» |

`stats` (в `meta` и в итоге задания; ключи словарей — текст): `stack {order: [gid в порядке расчёта], relations: {"gid":
relation}, dropped: [{group, by, relation, points, of, undefined}], dropped_total}`; `faults {"fid": {radius | null,
points, wing_volumes: [v0, v1], balance, max_residual}}`; `by_group[]` — по группе и крылу (`wing` код | null): `faults
[fid]`, `offset {mean, max} | null`, `offset_by_fault {"fid": {mean, max}} | null`; `vein_pairs [{group, a, b,
line_length, volume_common}]` (a — молодая, b — старшая); `balance` — |Σ объёмов доменов и «Прочее» − объём границы| /
объём границы ≤ 1e−9 (области крыльев не входят; баланс двух крыльев разлома — `faults[fid].balance`). «Модель на
скважины» и прогноз проектной скважины берут тела доменов (оба крыла — под именем группы) и жилы (под их кодами), без
областей крыльев и общего тела (`from_model_spec._model_bodies`).

### 10.3. Проектные скважины (`drillholes/{planned,planned_store,planned_eval,planned_export,planned_remap}.py`)

`objects(kind="planned_hole", parent_id=NULL)`, имена уникальны; без файлов (траектория считается из `meta`); не строка
набора скважин, на статистику и оценку не влияет (вопрос № 103). Узел графа `object:planned_hole` — источник (fp =
`version`). `meta` v1: `{v, collar [x, y, z], azimuth [0; 360), dip [−90; 90] (вниз — отрицательный, как 7.11), length
(0; 10 000] м, change {azimuth, dip} (−30…30 °/100 м) | null, target [x, y, z] | null, method, set_id | null}`; `method` —
`minimum_curvature` (умолч.), `tangent`, `balanced_tangent`, `average_angle`. Режим один из трёх: прямая; `change`
(азимут и угол на глубине d: `azimuth + change.azimuth·d/100`, угол ограничен ±90; станции через 10 м и забой); `target`
(прямая от устья через точку: `azimuth` и `dip` ядро считает и записывает, заданные заменяются; `length` нет — до цели;
короче расстояния — 422 `target_beyond_length {need}`; длиннее — скважина идёт дальше цели; цель в устье — 422
`target_at_collar`; `PUT` с новой целью без `length` кончает скважину в новой цели). Оба режима сразу — 422 `bad_param
{param: "change"}`. Траектория — `desurvey` 7.11; в ответах — каждый 1 м и забой.

| Маршрут (префикс `/api/v1/planned_holes`) | Тело | Ответ |
|---|---|---|
| `GET ?trajectory=` | — | `{planned_holes: [{id, name, version, meta[, trajectory]}]}` |
| `POST` | `{name?, collar, azimuth, dip, length, change \| target, method?, set_id?}` (название по умолчанию «Проектная скважина», свободное) | `{id, name, version: 1, meta, trajectory {depth, x, y, z}, target_depth}`; 409 `name_taken` |
| `GET /{id}`, `PUT /{id}`, `DELETE /{id}` | `PUT` — `{expected_version, name?, ключи тела POST}` | то же, версия + 1; `{deleted}`; 404 `planned_hole_not_found`; 409 `version_conflict {version}` |
| `POST /{id}/evaluate` | `{model_id \| mesh_ids, codes?, set_id?, point?}` — нужны модель или каркасы и/или набор (иначе 422 `bad_param {model_id}`) | `{id, intervals: [{from, to, code}] \| null, codes: [{code, length}] \| null, nearest: [{hole, hole_id, distance, depth}] \| null (≤ 5), target {point, depth}, set_id?, length, inside, journal (последние три — с телами)}`; промежуток вне тел — `code: null`; `point` — точка для ближайших скважин (по умолчанию цель, без цели — забой); `distance` — до самой скважины (по отрезкам), `depth` — по ней |
| `POST /{id}/compare` | `{set_id?, hole \| hole_id}` (набор по умолчанию — сохранённый со скважиной) | `{id, set_id, rows: [{depth, dx, dy, dz, d}], max, max_depth, length {planned, actual, compared}, hole, hole_id}` — каждый 1 м до меньшей длины; Δ = факт − план; одинаковые пути — 0; 404 `set_not_found`, `hole_not_found`; 422 `hole_no_trajectory` |
| `POST /{id}/export` | `{format: "csv" \| "dxf", path, overwrite?, delimiter?, decimal?, encoding?}` | `{format, files, points, size}`; `csv` — `path` папка: `collar.csv` (`HOLEID, EAST, NORTH, RL, DEPTH, AZIMUTH, DIP`) и `survey.csv` (`HOLEID, DEPTH, AZIMUTH, DIP`), «;» и «,», набор скважин принимает их импортом; `dxf` — файл (`.dxf` добавляется), одна полилиния по пути каждые 1 м; файлы уже есть — 409 `files_exist {files}` без `overwrite`; 404 `folder_not_found`, 422 `bad_format`, `path_not_absolute` |

Запись публикует `implicit.changed {kind: "planned_hole", id, deleted?}`; в проекте «только просмотр» запись — 423,
`evaluate`, `compare`, `export` работают. Другие отказы `evaluate`: 404 `model_not_found`, `wireframe_not_found`; 409
`model_building`; 422 `no_bodies`, `wireframe_open`, `wireframe_orientation`. `.bfggis`: `meta.set_id` перенумеровывается
(`planned_remap`; набора нет в архиве — `DEAD_ID`, запрос с ним — 404 `set_not_found`).

## 11. Интерфейсы Python (по коду, 19.30; Э19б — сигнатуры `stack`, `faults`, `vein_system`, `job_stack` в §10.2)

Соглашения: float64; NaN — пусто (не 0); numba `cache=True, nogil=True`, без `prange`, `fastmath`, импорт numba — внутри
функций (обработчики заданий лёгкие); координаты — проекта, сдвиг к началу делают сами ядра и показ; BLAS в 1 поток внутри
участка (`system.single_blas_thread()` один раз вокруг всего пула — `threadpool_limits(1)` из потоков пула не действует,
threadpoolctl 3.8); потоки — над участками / полосами точек / кирпичами; результат побитно один при 1, 2, 4, 8 потоках.
`Tick = Callable[[float | None, str | None], None]` (может бросить `JobCancelled`; в `octree`, `solve`, `evaluate`,
`smoothing` — `Callable[[], None]`). Цепочка: **data → octree → solve → evaluate → iso → iso_post → bodies / shells /
veins / expand → job**.

```python
# M/spec.py, M/spec_parts.py (19.1)
parse_model(body) -> ModelSpec   # .resolved(extent) .canonical() .hash_params() .group(gid) .s_of(g) .with_group(g)
                                 # поля: name, boundary, h, levels, kernel, kernel_radius, smoothing, trend_id,
                                 # anisotropy, pou (n_patch, n_min, delta), data (set_id, table, field, d_max, step),
                                 # groups (Group: id, name, type, codes, relation, faults, params), manual, post
parse_group(raw, param) -> Group; parse_build -> BuildSpec(expected_version, threads); parse_preview -> PreviewSpec
parse_check(q) -> CheckSpec; parse_support(body, d_max) -> (wireframe_id, threshold); parse_compare -> CompareSpec
parse_relations(body, spec) -> ModelSpec; eta(kernel, s, h_bar); resolve_h(diag); refuse(code, msg?, hint?, **extra)
POINT_TYPES (i1-коды точек), KERNELS, ETA_POWER, RANGES, REFUSALS, STAGES, ALGO = "rbf-1"

# M/data.py, data_holes.py, data_manual.py (19.7)
@dataclass(frozen=True, eq=False)
class Points:                       # точки данных, по группам; внутри группы — канонический порядок (решение 4)
    xyz: f8[n, 3]; f: f8[n]; kind: i1[n]; group: i4[n]; wing: i1[n]; src: i4[n]; span: f8[n, 2]; vertex: i8[n]
    sources: list[str]; counts: dict; notes: list[str]; levels: dict[int, list[float]]   # levels — уровни порогов оболочки T(t)
    n -> int; empty(); take(rows) -> Points; of_group(gid, wing=-1) -> Points; to_parquet() -> bytes; from_parquet(bytes)
load(project, spec: ModelSpec, *, groups=None, cancel: Tick = None) -> Points      # 422 too_many_points, no_contacts
load_vein_sides(project, spec, group, cancel=None) -> (hanging f8[a, 3], footwall f8[b, 3], journal)
canonical_order(xyz, f, kind, *tie) -> intp[n]                    # lexsort (x, y, z, f, kind), затем источник, span, вершина
merge_coincident(xyz, f, tol=COINCIDE, *, segment=None, ...) -> intp[m]   # совпавшие с равным f — одна, с разным — обе
assemble(parts, *, merge=True, group_names=None, ...) -> Points; check_size(n); check_contacts(points, gid, *, wing=-1)
data_holes.domain_points(lines, in_d | None, traj, dense, *, d_max, step, group, names=None, threads=1, tick=None,
                         limit=POINTS_MAX) -> Points                  # контакты, s(t), ±D_max; разрыв кода — без точек
data_holes.read_code_table(project, sel, tick=None) -> CodeTable; domain_points_of(table, codes, *, d_max, step, group, ...)
data_holes.vein_sides(lines, in_v, traj, dense, *, group=None) -> (hanging, footwall); vein_sides_of(table, codes, *, tick)
data_holes.shell_points(project, spec, group, gid, k, tick=None) -> Points      # значения g поля содержаний
data_manual.layer_points(xyz, offsets, closed=None, *, role, d_max, step, group, layer=0, name=None) -> Points
data_manual.grade_points(xyz, g, *, transform="linear", thresholds=None) -> GradeValues      # (values, notes)

# M/kernels.py, system.py (19.2)
kernels.phi(r, kind, a=None); gram(x, kind, a=None, eta=0.0, out=None); cross(x, y, kind, a=None)   # numba; kernel_code(kind)
system.assemble(xn, kind, radius=None, eta=0.0) -> f8[n + 4, n + 4]    # [Φ + ηI, P; Pᵀ, 0], P = (1, xn), выровнено на 64 байта
@dataclass(frozen=True)
class PatchSolution: points; lam: f8[n]; poly: f8[4]; centre: f8[3]; scale: float; kind: str; radius: float | None
                     eta: float; cond1: float          # .rcond, .gram_apply(t), .evaluate(t) — проверка, не вычислитель поля
system.solve_patch(xyz, values, kind="linear", radius=None, eta=0.0, *, centre=None, scale=None, cond_max=1/ε) -> PatchSolution
    # внутри: нормировка (x − c)/R, φ(R·r) = R^s·φ(r); 422 RbfSingular (reason few_points / coplanar / coincident /
    # ill_conditioned) и rbf_bad_data; cond₁ — LAPACK dgecon, 6 значащих цифр

# M/octree.py (19.3, 19.4б)
@dataclass(frozen=True, eq=False)
class PatchSet:                     # CSR; массивы только для чтения
    centers: f8[m, 3]; radii: f8[m]; leaf_min, leaf_max: f8[m, 3]; leaf_counts: i8[m]; indptr: i8[m + 1]; indices: i8[k]
    hashes: tuple[str, ...]; extent_min, extent_max: f8[3]; matrix: f8[3, 3] | None; n_patch, n_min, delta
    n_patches -> int; sizes -> i8[m]; patch(j) -> i8   # номера точек участка, по возрастанию
octree.build_patches(points, *, n_patch=1000, n_min=100, delta=0.25, extent=None, matrix=None, margin=0.10,
                     max_depth=20, ball_factor=3.0, tick=None) -> PatchSet
    # матрица A применяется один раз внутри (`matrix=`); R = (1 + δ)·половина диагонали листа и растёт до расстояния до
    # N_min-й точки (а не шагами ×1,25); лист делится, пока в шаре его участка > ball_factor·n_patch точек (19.4б);
    # пустые листья — тоже участки (покрытие охвата); нумерация — обход листьев в порядке Мортона
octree.hash_patches(points, patches, *, values=None, salt=b"", tick=None) -> list[str]   # hex SHA-256; без values = ps.hashes
octree.transform_points(points, matrix); transform_extent(lo, hi, matrix)

# M/solve.py, evaluate.py (19.4)
solve.solve_patches(points, values, patches, *, kind="linear", radius=None, eta=0.0, threads=1, tick=None, cond_max=1/ε,
                    matrices=None, sources=None, max_memory=2 << 30) -> list[PatchSolution]
    # eta, radius — число или список по участкам; matrices — A_j (None — участок как есть); масштаб участка с A_j =
    # R_j·‖A_j‖₂; участки с большим n — первыми; бюджет памяти 2·(n + 4)² float64 на участок в работе;
    # 422 rbf_singular {patch, centre, n_points, reason, points, sources}, rbf_bad_data
evaluate.PatchField(patches, solutions, *, matrices=None, threads=1)   # __call__(points, *, threads=None, tick=None) -> f8[q]
    # PoU: f = Σ W_j·f_j / Σ W_j, W(r) = (1 − r)⁴₊·(4r + 1); NaN там, где ни один шар не достаёт (не 0)
evaluate.evaluate(patches, solutions, points, *, matrices=None, threads=1, tick=None); partition_weight(r); BAND = 4096

# M/anisotropy.py (19.9); M/smoothing.py (19.10)
anisotropy.kind_of(spec, trend) -> "none" | "global" | "structural"; matrix_of(spec, trend) -> f8[3, 3]
anisotropy.global_matrix(spec, trend) -> f8[3, 3] | None            # то, что принимает build_patches(matrix=); None при A = I
anisotropy.local(trend, centres, *, source, threads=1, tick=None, report=None) -> f8[m, 3, 3]   # A_j в центрах участков
anisotropy.patch_hashes(hashes, matrices); stretch_of(A) = ‖A‖₂; check_matrix(a); ellipsoid_of(a, size) -> Ellipsoid
smoothing.eta_for_patches(points, values, patches, spec, *, group, s=None, matrices=None, h_bar=None, threads=1, tick=None,
                          max_memory=...) -> f8[m]                   # η каждого участка, м; «Вручную» — s·h̄^k, «Авто» — Д-25
smoothing.smooth_patches(...) -> PatchEta(eta, cond, exact); refine_patch(points, patches, spec, j, **kw) -> (η, cond₂)
smoothing.nearest_median(points, matrix=None) -> h̄; kernel_radii(kernel, kernel_radius, patches) -> list | None

# G/iso.py, iso_bricks.py, iso_post.py, solids.py (19.5, 19.6)
FieldFn = Callable[[f8[q, 3]], f8[q]]          # координаты проекта → f; нечисловое → +H (8.9.3 п. 6)
iso_bricks.Grid.from_box(lo, hi, h, levels) -> Grid     # Grid(lo, h, levels, blocks); .step = 2^L, .n, .token(), .key_dims
class BrickCache(Protocol): get(index: tuple[int, int, int]) -> f8[n, n, n] | None; put(index, values)
iso.isosurface(field, lo, hi, h, *, levels=2, near=None, dilate=1, threads=1, tick=None, cache=None, chunk_bricks=None,
               brick_order=None) -> IsoMesh        # IsoMesh(vertices f8[v, 3], triangles i4[t, 3], grid, stats: IsoStats)
iso.field_threads(threads) -> int                  # 1 внутри потока кирпича (без вложенного параллелизма)
iso_post.postprocess(v, t, *, h, post, clip=None, boundary=None, threads=1, tick=None) -> PostResult
    # PostResult(vertices, triangles, removed_parts, simplified, journal); V_min → pyfqmr (≤ 0,1·h) → обрезка (boundary /
    # wireframe / dtm ∩ boundary) → проверки п. 7; IsoError("iso_check"), solids.SolidError
iso_post.mesh_failure(v, t, *, h, tick=None, intersections=True) -> str | None      # причина по-русски или None
solids.canonical(v, t) -> (f8[v, 3], i4[t, 3]); solids.boolean(...), boolean_many(...)   # manifold3d; побитно при потоках
# M/expand.py
expand.expand_field(v, t, delta, *, H, threads=1) -> FieldFn; expand_box(v, delta, H) -> (lo, hi)
expand.expand_surface(v, t, delta, *, h, levels=2, threads=1, tick=None) -> IsoMesh; ExpandError(code, message, hint)

# M/bodies.py, shells.py, veins.py (19.13–19.15)
@dataclass(frozen=True)
class Output: kind: "surface" | "body"; group: int | None; wing: int | None; threshold: float | None; name: str
              vertices: f8[v, 3]; triangles: i4[t, 3]; volume: float | None; area: float; stats: dict; journal: list
@dataclass
class BodiesResult: outputs: list[Output]; fits: dict[int, Fit]; journal: list[str]; stats: dict
bodies.build_bodies(spec, points, boundary: (v, t), *, clip=None, matrix=None, matrices_of=None, eta_of=None, solver=None,
                    brick_cache=None, threads=1, tick=None) -> BodiesResult    # маска → решение → тело → вычитание; «Прочее» последним
bodies.mask(group, points, younger) -> bool[n]     # Э19 — все True; Э19б — решение 19
bodies_parts.fit_group(spec, group, pts, *, extent, matrix=None, matrices_of=None, eta_of=None, solver=None, threads=1,
                       tick=None, span=(0, 1), need_contacts=True) -> Fit        # Fit(group, patches, solutions, field, eta, h_bar, stats)
bodies_parts.box_solid(box) -> (v, t); h_bar(y); Tiling / Tile — тела D_i = {f_i < 0} ∩ граница минус тела старших
shells.build_shells(spec, group, points, boundary, *, clip=None, matrix=None, matrices_of=None, eta_of=None, solver=None,
                    brick_cache=None, threads=1, tick=None, surfaces=False) -> ShellsResult   # вложенность k ⊂ k − 1
veins.fit_plane(hanging, footwall) -> Plane(origin, u, v, w)       # полигон границы — в (u, v) от origin
veins.build_vein(hanging, footwall, params, h, *, threads=1, tick=None, dense_max=DENSE_MAX) -> VeinBody | None
veins.vein_body(hanging, footwall, params, h, **kw) -> (f8[v, 3], i4[t, 3]) | None; veins.boundary_of(params, uv_all)

# M/store.py (+ store_base, store_blobs, store_check), remap.py, update.py, job*.py (19.11, 19.12)
store.KIND = "implicit_model"; create(conn, store, project, spec, author=None, *, prepared=None, points=None) -> {id, name, version: 1}
store.update(conn, store, project, model_id, raw, expected_version, *, prepared=None, points=None) -> {id, name, version}
store.delete(conn, store, model_id) -> {deleted, wireframes, removed}; get(conn, id) -> форма §5; list_rows(conn)
store.begin(conn, model_id, expected_version, job_id=None); set_state(conn, id, state, *, error=None, job_id=None)
store.record_run(...); commit_output(...); replace_output(...)   # «Обновить» переписывает выход на месте (версия + 1)
store.prepare(conn, spec, *, points=None) -> Prepared; inputs_of(conn, spec); freshness(conn, meta) -> {stale, stale_reason, source_lost}
store.load_fields(conn, store, model_id) -> {(gid, wing | None): GroupData(points, patches, lam, c, matrices)}   # 409 not_built
remap.remap_meta(meta, ids) -> meta
update.PatchCache.from_blobs(conn, store, model_id); CachingSolver(spec, cache, matrix=, threads=); BrickFiles(folder, model_id,
    grid, record, matrix=, read=, used=); salt_of(spec, η₀, A); sweep(folder, model_id, used)
job.KIND_BUILD = "implicit.build"; KIND_UPDATE = "implicit.update"
job.run_build(engine, folder, params, tick, *, update=False, threads=None, job_id=None, memory_mb=None) -> dict   # тело задания
job.memory_mb(params) -> int; build_job / update_job — обработчики (`jobs/registry.py`)

# Д-32, Д-33 (19.17, 19.19)
check.load(project, model_id) -> Loaded; check.page(loaded, cs, highlight=None) -> dict (§8.1); csv_bytes(loaded, cs, highlight)
check_support.parse(body) -> Request; run(project, model_id, req) -> Support; mesh_body(values, mesh) -> (bytes, head)
compare_holes.parse(body) -> Request; run(project, model_id, req) -> dict (§8.3)    # синхронно, слот HEAVY, channel / seq
# drillholes/from_model.py (19.18): KIND = "drillholes.from_model"; on_holes(traj, dense, bodies, ids=None, threads=..., tick=...)
#     -> coding.Stretches; whole_samples(traj, dense, ids); fill_rows(stretches, holes, to, code_of); at_midpoints(view, field,
#     lines, traj, dense, threads=..., tick=...) -> f8[n]; run(engine, folder, params, *, job_id, tick); memory_mb(params)
# geostat/trend_struct.py (19.8): local_axes(trend_meta, xyz: f8[m, 3], *, source, threads=1, tick=None, report=None) -> f8[m, 3, 3]
#     строки — u1 (простирание), u2 (падение), u3 (нормаль); load_source(project, t, tick=None); discs(project, t); count_measurements
```

## 12. Команды реестра (лента «Моделирование → Неявные модели»; Ctrl+K, ассистент; клавиш нет)

| Команда | Признаки | Маршрут / окно |
|---|---|---|
| `implicit.new {name?, box?, wireframe_id?, h?}` | — | без параметров — мастер (граница и шаг, домены литологии, интерполяция, итог); с `box` или `wireframe_id` — `POST /models` (пустая модель, ничего не строится); `box` — текст «x_мин; y_мин; z_мин; x_макс; y_макс; z_макс» |
| `implicit.save {as_new?, build?}` | — | сохраняет форму мастера (`POST /models` / `PUT /models/{id}`); `build` — затем построить или обновить |
| `implicit.domains {model_id?, codes?, name?}`, `implicit.shells {model_id?, group_id?, name?, source?, result_id?, set_id?, table?, field?, transform?, mode?, thresholds?, p?}`, `implicit.vein {model_id?, group_id?, name?, codes?, bound?, offset?, t_min?}`, `implicit.expand {model_id?, group_id?, name?, wireframe_id?, delta?}` | — | окно группы; с параметрами — `POST /models/{id}/groups` (с `group_id` — `PUT`); списки — текст через «;» |
| `implicit.structural {model_id?, set_id?, table?, name?, source?, alpha?, dipdir?, depth?, wireframe_id?, r_n?, radius?}` | — | замеры или каркас → структурный тренд модели (`POST /geostat/trends`, затем `PUT /models/{id} {trend_id}`); без полного источника — окно «Новый тренд» (структурный) |
| `implicit.show {model_id?, group_id?, what, visible?}` | — | `what`: `points` — точки данных группы в 3D (`POST …/points/preview`, ≤ 50 тыс.), `ellipsoid` — эллипсоид анизотропии модели |
| `implicit.update {model_id?}` | — | `POST /models/{id}/update` (новая модель — `build`) |
| `implicit.to_wireframe {model_id, wireframe_id?, name?}` | — | `POST /models/{id}/to_wireframe` (окно «В каркас»); без `wireframe_id` — окно |
| `implicit.delete {model_id}` | confirm | `DELETE /models/{id}` после `askImpact` |
| `implicit.check {model_id}`, `implicit.support {model_id, threshold?, wireframe_id?}` | — | §8.1, §8.2; окно «Проверка по скважинам» (вкладки «Отклонения», «Карта опоры») |
| `implicit.compare_holes {model_id, set_id, table, field}` | — | §8.3; переход в 7.17 («К скважине») |
| `drillholes.from_model {set_id, model_id \| mesh_ids, name}` | — | §8.4 |
| `trend.save {kind: "structural", …}`, `trend.show {trend_id, visible}` | confirm, — | §9; у структурного `trend.show` — диски в 3D |
| Э19б: `implicit.relations {model_id?, order?, relations?}` | — | без `order` и `relations` — окно «Стек отношений»; с ними — `PUT …/relations` (порядок — домены через «;» от самого молодого, номер или название; отношения «группа=отношение» через «;»: `conformable`, `erode` (`erosion`), `onlap`, `intrusion` или русские слова) |
| Э19б: `implicit.fault {model_id?, group_id?, name?, codes?, layers?, radius?, domains?}`, `implicit.group_faults {model_id?, group_id, fault_ids}`, `implicit.vein_system {model_id?, group_id?, name?, veins?, bound?, offset?, t_min?}` | — | без данных — окно (`Разлом`, `Система жил`); с данными — `POST …/faults` (+ `PUT …/faults` домена), `PUT …/groups/{gid}/faults`, `POST …/vein_system`; `layers` — «номер:роль» через «;»; `radius: null` — бесконечный разлом; `veins` — коды по возрасту через «;» |
| Э19б: `planned.new {name?, x?, y?, z?, azimuth?, dip?, length?, change_azimuth?, change_dip?, target_x?, target_y?, target_z?, set_id?, method?}`, `planned.open {id?}`, `planned.save {as_new?}`, `planned.evaluate {id?, model_id?, mesh_ids?, set_id?}`, `planned.compare {id?, set_id?, hole?}`, `planned.export {id?, format, path?, overwrite?}`, `planned.delete {id}` (confirm), `planned.show {id?, visible?}`, `planned.pick {what}` (только окно) | —; confirm у `planned.delete` | §10.3 (лента «Главная › Скважины»: «Проектная скважина», «Прогноз», «План / факт», «Экспорт проектной»); без параметров — окно «Проектная скважина» |

Без параметров — окно, с параметрами — выполняет (ADR 0021 п. 9); списки — текст через «;». Значки `implicit`,
`fault`, `vein`, `planned_hole` — `shell/Icon.tsx` (оркестратор). Идентификаторы проектных скважин — `planned.*`
(в 7.35 Д-34 названы `planned_hole.create / evaluate / compare`; решение 19.39, §14 п. 26).

## 13. Фикстуры

- `core/tests/data/implicit/params.json` — модели `domains`, `shells_auto`, `vein_expand`, `stack_b` (Э19б): `body` →
  `canonical` (`spec.parse_model(body).canonical()`); круговой проход — `test_implicit_spec.py`;
  `BF_REGEN_IMPLICIT=1` переписывает `canonical`.
- `core/tests/data/implicit/contract_answers.json` — ответы маршрутов Э19 `{status, body}`: настоящие — ключи
  `test_implicit_contract.py` (`BF_REGEN_IMPLICIT=1 uv run pytest -q tests/test_implicit_contract.py`; сцена
  `core/tests/modapi_world.py`: 100 скважин, домены INT и SS), `defaults`, `bad_param`, `refusals` — `test_implicit_spec.py`;
  образцы форм: `trend_structural_post`, `trend_discs`, `build_iso_error`; ключи Э19б — настоящие ответы: `relations*`,
  `fault_*`, `group_faults*`, `build_stack_*`, `model_stack`, `update_stack_result`, `vein_system_*`, `model_veins`,
  `outputs_veins`, `delete_veins` (19.36; `stack_b` в `params.json`), `planned_*` (19.37, 19.39). Без флага
  тест проверяет, что ядро отвечает то же (времена, отпечатки, версии библиотек, невязки и числа, округляемые по-разному
  на машинах, исключены; числа сравниваются до 1e−7).
- `tests/fixtures/implicit/` — `collars.csv`, `lithology.csv`, `assays.csv`, `strings.csv` (`tools/gen/implicit.py`, зерно
  12345): складка 100 прямых скважин, слои ALV, SS, SH, GR, сфера-интрузия INT, плита VN; ручные стринги.

## 14. Трактовки (вне буквы ТЗ и ADR: п. 1–8 — контракт 19.1, п. 9–19 — приняты при реализации Э19, п. 20–29 — при реализации Э19б)

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
9. (Принято при реализации Э19.) Предел точек в шаре участка `octree.BALL_FACTOR = 3`: лист делится дальше, пока в шаре его
   участка (до роста для N_min) больше 3·N_уч точек (19.4б; на скважинных данных без предела участки доходили до 18,6 тыс.
   точек — 509 с и 5,5 ГБ; с пределом 100 тыс. скважинных точек — ≈ 50 с и 0,6 ГБ). Результат в смысле интерполяции тот же,
   участков больше; уточняет 8.9.2 (верхней границы числа точек в шаре там нет), в параметры и интерфейс не выводится.
10. «R × 1,25 до выполнения N_min» — радиус растёт до расстояния до N_min-й ближайшей точки (с запасом 1e−9), а не шагами
    ×1,25; пустые листья тоже дают участки (покрытие охвата), NaN — только за охватом.
11. «+10 % охвата» — по 10 % размера охвата с каждой стороны (`margin = 0,1`), после A.
12. Масштаб нормированных координат участка с локальной A_j — R_j·‖A_j‖₂ (радиус шара после A_j): при сильной анизотропии
    cond₁ в десятки раз меньше, чем при R_j; без анизотропии — R_j. Поле участка от масштаба не зависит.
13. cond(A) ≤ 1e6 (`anisotropy.COND_MAX`): отношения осей за пределами 1 : 10⁶ — ошибка набора, 422 `bad_anisotropy`.
14. «Авто» (Д-25): цель — cond₂ блока Φ_⊥ + ηI на дополнении к P (не cond₁ системы); двухступенчатая проверка (грубая
    оценка по n и наименьшему расстоянию, затем точный спектр — Ланцош или `eigvalsh` для ≤ 96 точек); накладные ≤ 10 % — на
    невырожденных данных с линейным ядром (5,2 %); на вырожденных участках и кубическом ядре до 100–300 % (измеряется каждый
    подозрительный участок); участок, который пропустил грубый отсев и оказался вырожденным, — `refine_patch`.
15. Каркасы-выходы неизменяемы в `wireframes/store`; «Обновить» сохраняет id выходов и заменяет геометрию на месте (версия
    + 1); «В каркас» — копия без родителя.
16. Полигон границы жилы в (u, v) в интерфейсе не рисуется и не вводится (в ответах нет данных плоскости): выпуклая оболочка
    + отступ; ранее сохранённый полигон показывается и сохраняется; редактор полигона — решение заказчика, вне ТЗ.
17. У `lineage/graph.reindex` нет `object:trend`: у проектов, сохранённых до Э19, нет обратного индекса структурных
    трендов (на новых проектах и `.bfggis` не проявляется).
18. `hash` участка зависит от значений f, соли (ядро, a, режим, η₀, цель cond, A) и A_j; η в хеш не входит (функция тех же
    аргументов). «Обновить» использует кеш только если версия программы и библиотек прежняя, иначе строит полностью.
19. Показ в «Свойствах» и «Проверке»: пропуск — «—», не 0; невязка и расстояния — в метрах, в окне — 4 значащих цифры.
20. (Э19б.) Маска данных стека — по ЗНАЧЕНИЮ поля другой группы в точке данных (`Fit.field`, RBF без границы), не по каркасу:
    точка за границей или под третьим телом всё равно по свою сторону поверхности. Допуск «на поверхности» —
    max(1e−9·h̄, наибольшая невязка данных группы, чья это поверхность): «Эрозия» отбрасывает и точки на поверхности
    молодой (контакт среза — срез, а не поверхность старой); «Интрузия» и «Налегание» контакт на поверхности оставляют
    (это контакт старого слоя с интрузией, молодого — со старой поверхностью).
21. У группы одно отношение ко всем более старым (не к выбранной); «Налегание» меняет порядок расчёта (победитель —
    старшая), остальные — молодая первой; порядок — топологический, цикл — 422 `bad_relations` (при одном отношении на
    группу не возникает; страховка маршрута и задания). Оболочки, жилы, расширения и системы жил в стек не входят.
22. Крылья разлома: код Σ sign_k·2^k (0 — f_F < 0); точка данных на разломе (|f_F| ≤ 1e−9·h) — на оба крыла; разломов у групп
    модели не больше 8, номер поля крыла ≤ 127 (int8 в blobs); тела `{f_F < 0}` и остаток — области (`region`), а не породы:
    в `balance` пород они не входят (у каждого разлома свой баланс двух областей); лист разлома — выход `surface`.
23. Смещение крыльев не задаётся: оно измеряется после построения (среднее и наибольшее вдоль нормали контакта, по группе и
    по каждому разлому) и показывается только для чтения («Свойства», окно «Разлом»).
24. Система жил: вырез — сырыми телами только более молодых жил; общее тело — объединение сырых; общая зона отдельного
    кода не имеет (№ 94); `volume_common` — до выреза; пары, чьи тела не встречаются, в таблицу не входят; линии — слой
    стрингов на пару (файл линий — потомок модели, пишется заново при каждом запуске); жила, выклинившаяся целиком или
    целиком вырезанная молодыми, тела не имеет (журнал, не ошибка).
25. «Интрузия» — отношение домена, а не тип группы; тело интрузии режется более молодыми группами по их отношению
    («Согласно» и «Эрозия» — они побеждают).
26. Идентификаторы команд проектных скважин — `planned.new / open / save / evaluate / compare / export / delete / show /
    pick` (в 7.35 Д-34 и ADR 0026 — `planned_hole.create / evaluate / compare`); лента «Главная › Скважины», раздел дерева
    «Проектные скважины» (скважина не входит в раздел «Скважины»).
27. Проектная скважина: «через цель» — прямая от устья через точку (азимут и угол решает ядро и пишет в `meta`); дуга
    `change` считается по станциям через 10 м; прогноз, ближайшие скважины и «План / факт» читают СОХРАНЁННУЮ скважину (окно
    просит сохранить); «ближайшие» — до 5, расстояние до самой скважины (по отрезкам), а не до станции; «План / факт» —
    каждый 1 м до меньшей из длин, Δ = факт − план; `collar.csv` и `survey.csv` — тот формат, который принимает импорт
    набора скважин (разделитель «;», десятичная запятая).
28. Прогноз проектной скважины и «Модель на скважины» берут от модели с разломами тела доменов (оба крыла — под именем
    группы) и жилы (под их кодами); области крыльев и общее тело системы не кодируются.
29. «Обновить» модели со стеком и разломом: участки кешируются (как в Э19), кирпичи групп с разломом (у крыльев своё поле)
    и лист разлома — нет; доля времени полного построения: ci 0,61–0,65, full 0,81; цель ТЗ ≤ 25 % не достигнута
    (вопрос № 104); результат побитно равен полному построению.
