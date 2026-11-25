# Э18 и Э18б — контракт оценки содержаний и моделирования (для дорожек Х, Р, К1, К2, П, Б, С, Ф, И-А, И-Б, И-В; удаляется в 18.28 / 18.36)

Источник истины — ADR 0024 (решения 1–22, §2 схема, §10 команды, §11 API, §12 интерфейсы), ТЗ 8.8 (8.8.2 —
умолчания и диапазоны), 8.0, 7.35 Д-21, Д-23, Д-27, Д-28, Д-29, Д-30, Д-35, Д-38; граф зависимостей — ADR 0025
(в ветке-источнике — `0023-graf-zavisimostey-aktualnost.md`). Разбор параметров — `core/bf_core/geostat/estimate/
{spec,spec_parts}.py` (18.1, готово). Образцы — `core/tests/data/estimate/{params.json, contract_answers.json}`.
Изменение формы — только через оркестратора. Префикс маршрутов — `/api/v1/geostat`.

Пути: `E/` = `core/bf_core/geostat/estimate/`, `S/` = `core/bf_core/geostat/simulation/`, `M/` =
`ui/src/modules/estimation/`.

## 1. Общие правила

- `X-BF-Token`, Host/Origin — как у всего ядра. Координаты, значения — float64; пусто — `null` в JSON (NaN в
  массивах), **никогда не 0** (8.0.1). Число на входе — число или текст «0,5» / «0.5»; целые — целое (15 или
  15.0). Нет ключа / `null` / `""` — умолчание.
- Ошибка — `{code, message, hint, param?, …}` по-русски с подсказкой; `param` — путь в теле: `"disc.2"`,
  `"samples.soft.dist"`, `"method.ik.thresholds.1"`, `"variograms.0.model_id"`, `"search.passes.1.hy.q"`,
  `"outputs.fields.3"`, `"target.rules.items.0.cond.op"`. Нет открытого проекта — 409 `no_project`. Запись в проект
  «только просмотр» — 423 `project_read_only` (сохранение, удаление, `run`, `xval`, `simulate.*`); `defaults`,
  `preview`, `inspect`, `kna`, `global`, `qq` работают.
- Оптимистичная блокировка: `PUT` строки, плана, моделирования — `expected_version` объекта; `run`, `post` —
  `expected_version` блочной модели. Нет или не целое ≥ 1 — 422 `bad_param {param: "expected_version"}`; другая —
  409 `version_conflict {version}` (до постановки задания и ещё раз при фиксации — записанное другим не
  затирается). Окна на 409 — «Перечитать» / «Сохранить как новые».
- Задание: ответ `{source, id}`; ход и итог — `GET /jobs/{source}/{id}` (`status`, `progress`, `message` — стадия,
  `result`, `error {code, message, hint, param?}`). Синхронные проверки тела (404, 422, 409) — до постановки.
  Итог несёт `events: [[имя, данные]]`. Запись — последней стадией, целиком или ничего; отмена — между порциями.
- Синхронные расчёты (`preview`, `inspect`, `qq`, `defaults`) — в слоте HEAVY (`stats.routes_charts.SLOTS`);
  обгон по `channel` (строка 1–64) / `seq` (целое ≥ 0), отменённый — 409 `superseded` (как Э17 §1).
- События: `geostat.changed {kind: "estimation" | "estimation_plan" | "simulation", id, deleted?}` — после записи и
  удаления; `objects.changed {kind: "block_model", id}` — после записи полей (`estimate.run`, `simulate.post`).
- Пока маршрут не подключён в сборке — 422 `not_ready`.
- Побитность (Т8.8-9, -12, -13): результат не зависит от порядка проб в таблице, числа потоков (1, 2, 4, 8) и
  разбиения на порции; опорная точка `origin` хранится в строке и одна для оценки, перекрёстной проверки,
  предпросмотра и инспектора.

## 2. Объекты и поля (миграции нет; роли blobs `xval`, `real` — `db/schema.py`, оркестратор)

```
objects(kind="estimation", parent_id=NULL).meta v1 — «Параметры оценки» (дерево «Геостатистика → Параметры оценки»)
  = spec.parse_estimation(тело).canonical()                                     — параметры, §3
  + origin [x, y, z]     центр охвата проб строки (D ∪ S), float64; задаётся при первом сохранении (18.11) и
                         при «Перестроить»; xval / preview / inspect берут её отсюда
  + inputs [{kind, id, fp, name}]   входы в форме графа ADR 0025 (§2.3)
  + algo "est-1"; versions {numpy, scipy, numba}; created_at
  + last_run {at, job, version (блочной модели после записи), counts, merged, class_means} | null
objects(kind="estimation_plan").meta v1 {v: 1, name, model_id, items: [estimation_id …]} — порядок = порядок строк
object_refs(src=estimation → trend, role="trend"), (→ filter, role="filter")   — запрет удаления тренда/фильтра (409 in_use)
blobs owner=object(estimation) role="xval"   — Parquet таблицы перекрёстной проверки (последний расчёт)
Э18б: objects(kind="simulation").meta v1 — §9; blobs owner=object(simulation) role="real" — реализации
```

`counts` строки: `{targets, by_status [n0, n1, n2, n3], by_pass [n_1…n_P], singular [по проходам], samples {rows,
missing, no_position, outside_domain, used, merged, topcut, soft, no_weight}}`; `rows = missing + no_position +
outside_domain + used` (как Э16), `merged` — сколько строк усреднено в совпадающие пробы, `soft` — пробы S.

### 2.1. Поля блочной модели (вопрос № 81, Д-27, Д-29)

Имя — `<префикс><ключ>`; один файл `fld:` на задание (`FieldsWriter`, Э17 §3). В памяти ядер — float64, NaN — пусто.

| Ключ | dtype | Смысл | Методы |
|---|---|---|---|
| `STATUS` | i1 | Д-27: 0 «оценён», 1 «вне домена» (вне доменов плана или вне ограничителя), 2 «мало проб» (ни в одном проходе не выполнены n_min, октанты, скважины), 3 «вырожденная система» (во всех проходах, где хватало проб, главный элемент < 1e−12·C(0)). Пишется **всегда** | все |
| `EST` | f8, role `grade`, unit пробы | оценка; при STATUS ≠ 0 — NaN | nn, idw, sk, ok |
| `KV` | f4 | σ²_SK / σ²_OK | sk, ok |
| `NS`, `NH` | i2 | число проб, скважин | все |
| `NHY` | i2 | Д-29: затронуто правилом ураганных проб (0 при выключенном) | все |
| `AD`, `DMIN` | f4, unit «м» | среднее и наименьшее евклидово расстояние до проб | все |
| `PASS` | i1 | проход, давший оценку (1…5) | все |
| `SLOPE`, `KE` | f4 | наклон регрессии (SK — 1), эффективность | sk, ok |
| `LAGR` | f4 | μ | ok |
| `SNW` | f4 | сумма отрицательных весов | sk, ok |
| `NOCT` | i1 | заполненные октанты | все |
| `P1…PK` | f4 | F_k после поправки порядка, ∈ [0; 1] | ik |
| `ETYPE` | f8, role `grade` | Σ (F_k − F_(k−1))·m_k | ik |

- STATUS ≠ 0 — все поля строки, кроме `STATUS`, пусты (NaN / null), не 0. Строки модели вне всех строк плана
  префикса — STATUS 1. Словарь кодов — `spec_parts.STATUS_LABELS`; в поле — `description` «0 — оценён; 1 — вне
  домена; 2 — мало проб; 3 — вырожденная система» (трактовка: числовое i1, а не `cat`, — `STATUS = 0` и
  `IF(STATUS = 0, EST, -1)` ТД27-3 калькулятора 8.7.4 работают над числом; категория сравнивается по тексту
  подписи).
- Если проход вырожден, а следующий оценил — STATUS 0, PASS = номер прохода, причина — в журнал задания.
- `source` поля: `{op: "estimate", params: {estimation_ids: [...]}, inputs: [sha256 файлов], algo: "est-1",
  versions, at}`.

### 2.2. Слияние и частичный повтор (решение 10, вопрос № 77)

Строки плана с одним префиксом пишут одни поля; поля и метод у них должны совпадать, домены — различаться (иначе
422 `bad_param {param: "items.N"}` «Строки с одним префиксом …»). Повторный запуск части строк переписывает только
строки модели их доменов; значения остальных доменов переносятся из текущих полей (новый файл `fld:`, старые
значения копируются порциями). STATUS 1 перезаписывается только у строк, не принадлежащих ни одному домену строк
префикса, известных плану.

### 2.3. Входы и «устарел» (8.0.2; Д-35, ADR 0025)

Строка — расчётный узел графа `("object:estimation", id)`; поля, которые она пишет, — узлы `("bm_field",
"<model_id>:<имя>")` с входом `object:estimation`. `meta.inputs` (порядок — как здесь; `fp` — отпечаток по
ADR 0025 реш. 3):

| Вход | `kind`, `id` | `fp` |
|---|---|---|
| таблица проб; таблица домена проб (если другая) | `dh_table`, id таблицы | `table_token:rev` |
| набор скважин (траектории) | `dh_set`, id набора | ключ траекторий (`variogram.store.trajectories_key`) |
| модель вариограммы (каждая из `variogram`, `variograms`) | `object:variogram_model`, id | её `meta.sha256` (= `sha` снимка) |
| тренд (`trend_id`) | `object:trend`, id | `version` |
| фильтр-ограничитель (`filter_id`) | `object:filter`, id | `version` |
| геометрия блочной модели | `object:block_model`, id | `cells:<sha cells>` |
| поле домена модели и поля ограничителя | `bm_field`, `"<model_id>:<имя>"` | sha файла поля |

- Прямая проверка (до готовности графа) — `E/store.freshness(conn, meta) -> {stale, stale_reason, source_lost}`:
  входы сравниваются с текущими; причины по-русски: «изменена таблица «…»», «изменены траектории», «изменена
  модель вариограммы «…»», «изменён тренд «…»», «изменён фильтр «…»», «изменена блочная модель», «изменено поле
  «…» модели»; удалён — `source_lost: true`, «источник удалён» (снимок модели вариограммы остаётся — строка
  считается, но «устарела»). Транзитивная часть — граф ADR 0025 (провайдер `object:estimation` регистрирует
  оркестратор в `lineage/providers.py`; `job.py` после записи вызывает `lineage.record(conn, dst, inputs)` в той
  же транзакции, если модуль есть). «Перестроить» — `POST /estimate/run {estimation_ids: [id]}`.
- Э18б: `object:simulation` — те же входы (+ `bm_field` вторичной переменной ко-SGS); поля постобработки —
  `bm_field` со входом `object:simulation`.

## 3. Параметры оценки (тело строки) — `spec.parse_estimation`

```json
{"name": "AU · OK · окисл", "model_id": 7,
 "target": {"domain_field": "ZONE", "label": "окисл", "filter_id": null, "rules": null},
 "sub": "parent", "disc": [4, 4, 2],
 "samples": {"set_id": 3, "table": "12", "field": "AU", "unit": "г/т",
             "domain": {"table": "14", "field": "ZONE", "values": ["окисл"]},
             "soft": {"domains": ["перех"], "dist": 5}, "topcut": {"field": "AU_CUT"} , "weights": null},
 "method": {"kind": "ok", "power": 2, "mean": {"mode": "declus", "value": null},
            "ik": {"thresholds": [0.5, 1, 2.5], "models": "per", "class_means": "declus"}},
 "variogram": {"model_id": 21, "snapshot": {…}, "sha": "…", "scale": "z", "share": null},
 "variograms": null, "trend_id": null,
 "search": {"from": "model", "ang1": null, "ang2": null, "ang3": null, "r1": null, "r2": null, "r3": null,
            "passes": [{"factor": 1, "n_min": 4, "n_max": 24, "n_hole": 0, "n_oct": 0, "k_oct": 0,
                        "n_holes_min": 0, "hy": {"t": 20, "q": 0.5, "mode": "drop"}}]},
 "outputs": {"prefix": "AU_OK_", "fields": ["STATUS", "EST", "KV", "NS", "NH", "AD", "PASS", "SLOPE", "KE"]}}
```

| Ключ | Умолчание | Диапазон / правило | `param` ошибки |
|---|---|---|---|
| `name` | «‹поле› · ‹МЕТОД› · ‹домен›» (store; занято — « (2)») | ≤ 200 | `name`; 409 `name_taken` |
| `model_id` | — | блочная модель; переменный шаг — 422 `variable_grid` | `model_id`; 404 `block_model_not_found` |
| `target.domain_field`, `.label` | null — все блоки | поле модели (категория или число); `label` — его значение; одно без другого — 422 | `target.label`, `target.domain_field`; 422 `domain_unmapped` (метки нет в модели) |
| `target.filter_id` / `.rules` | null — без ограничителя | одно из двух; `rules` — дерево Э17 §13 (`normalize_rules`); блок проходит при f > 0 (№ 65 Э17) | `target.rules…` (`filter_rules`); 404 `filter_not_found` |
| `sub` | `parent` | `parent` (оценка в центре родителя с дискретизацией родителя, все (P, D) — её) / `own` (своя дискретизация субблока) | `sub` |
| `disc` | [4, 4, 2]; [1, 1, 1] — точечный | 1–10 по оси, целые | `disc`, `disc.N` |
| `samples.set_id`, `.table`, `.field` | — | набор; интервальная таблица (id текстом или имя → store хранит id текстом); числовое поле | `samples.…`; 404 `set_not_found`, `table_not_found`, 422 `field_not_found` |
| `samples.domain` | null — все пробы | `{table?, field, values 1–1000}`; значения — текст (число → `format(x, ".15g")`) | `samples.domain.values.N` |
| `samples.soft` | null — жёсткая | `{domains: [S…], dist d_м ≥ 0 м}`; d_м > 0 без доменов, S без домена проб, S ∩ D — 422 | `samples.soft`, `.domains`, `.dist` |
| `samples.topcut` | null | `{field}` (поле `*_CUT`) или `{value}` (min(z, T)); оба — 422 | `samples.topcut…` |
| `samples.weights` | null — `DECL_W`, если есть; `false` / `""` — без весов | имя числового поля | `samples.weights` |
| `method.kind` | `ok` | `nn`, `idw`, `sk`, `ok`, `ik` | `method.kind` |
| `method.power` | 2 | 0,5–5 (IDW) | `method.power` |
| `method.mean` | `{mode: "declus"}` | SK: `declus` (декл. среднее домена) / `value` + число | `method.mean.value` |
| `method.ik` | `{models: "per", class_means: "declus"}` | `thresholds` 1–20, строго по возрастанию (обязательны для ik); `per` — `variograms` по одной на порог, `median` — одна `variogram` | `method.ik.thresholds.N` |
| `variogram` | — (обязательна для sk, ok, ik `median`; для nn, idw — только поиск) | `{model_id, snapshot?, sha?, scale?, share?}`; снимок и `sha` заполняет store (копия `meta` модели) | `variogram.model_id`; 404 `model_not_found` |
| `variograms` | — | ik `per`: K штук в порядке порогов, `t` — порог (необязателен, иначе = порогу) | `variograms`, `variograms.N.t` |
| `variogram.scale` (Д-38) | ставит store | `z` — модель в Z (`transform: none`, в т. ч. индикаторная 18.10); `back` — `meta.back.model` модели в Y; `y` — модель в Y — **422 `variogram_scale`** «Модель построена в нормальных оценках» / «Выполните обратное преобразование модели или выберите модель в исходных единицах» (кроме SGS/ко-SGS Э18б). `share` — доля Эрмита `back` | `variogram.model_id` |
| `trend_id` | null | тренд Э16 (`object_refs role "trend"`) | `trend_id`; 404 `trend_not_found` |
| `search.from` | `model` (если есть модель), иначе `trend` (если `trend_id`), иначе `manual` | `model` — углы и дальности структуры с наибольшей a1 (из равных — первой); `trend` — углы тренда, r1 — заданный или `size`, r2, r3 = r1·отношения; `manual` — r1 обязателен, r2, r3 по умолчанию = r1, углы 0 | `search.from`, `trend_id`; 422 `no_structures` |
| `search.ang1…3` | из источника | как вариография (ang1 [0; 360), ang2 [−90; 90], ang3 (−180; 180]) | `search.angN` |
| `search.r1…r3` | дальности модели | > 0 и ≤ 100 000 м | `search.rN` |
| `search.passes` | 3 прохода ×1, ×2, ×3 | 1–5; `factor` (0; 100] (умолч. номер прохода); n_min / n_max 1–128 (4 / 24), n_min ≤ n_max; `n_hole` 0–64; `n_oct` 0–16; `k_oct` 0–8; `n_holes_min` 0–128 (0 — выкл.) | `search.passes.N.…` |
| `search.passes.N.hy` (Д-29) | null — выкл. | `{t, q ∈ (0; 1], mode: drop \| cap (умолч. drop)}`; без `t` — выкл.; `t` без `q` — 422 | `search.passes.N.hy.q`, `.mode` |
| `outputs.prefix` | «‹ПОЛЕ›_‹МЕТОД›_» (не буквы/цифры → «_») | 1–40 знаков: буквы, цифры, «_» | `outputs.prefix` |
| `outputs.fields` | по методу: nn — STATUS EST DMIN PASS; idw — STATUS EST NS AD PASS; sk — STATUS EST KV NS NH AD PASS SLOPE; ok — то же + KE; ik — STATUS NS PASS P ETYPE; + NHY, если в проходе есть `hy` | подмножество §2.1 для метода (`spec_parts.ALLOWED`); STATUS добавляется всегда; порядок канонический | `outputs.fields.N` |

- Порядок отбора (Д-29, № 66): кандидаты в эллипсоиде прохода → правило ураганных проб (проба > t при h_s >
  q·r1·factor — отброшена или урезана до t) → сортировка по (h_s, номер) → n_max, n_скв, n_окт → k_окт,
  n_скв_min, n_min. Записан в `defaults.params.search.order`.
- Мягкая граница (8.8.2, № 79; уточнение Д-28): проба S допускается для блока D, если **евклидово** расстояние
  от пробы до центра блока ≤ d_м. Д-28 даёт источник d_м и пары (A, B) — окно контактного анализа (`estimate.open
  {soft: {a, b, distance}}` → мастер: `samples.domain.values = [a]`, `soft = {domains: [b], dist: distance}`);
  расстояние блока до границы домена (знаковое) нужно профилю блоков `chart.contact` (§8.5), а не отбору проб.
- «Номер пробы» в правилах равенства (NN, отбор) — позиция в каноническом порядке: lexsort (X, Y, Z, значение)
  после усреднения совпадающих (≤ 1e−6 м, один домен) (№ 76).
- `canonical()` — то, что хранится; `hash_params()` — без `name`, с `algo`. Ключи `meta` (`v`, `origin`, `inputs`,
  `algo`, `versions`, `last_run`, `created_at`) разбор пропускает: `parse_estimation(meta)` работает.
- Диапазоны для формы — `spec.ranges_json()` (`{lo, hi, lo_open, integer, unit}`, null — без границы); умолчания —
  `spec.defaults_json()`.

## 4. Строки и планы

- `GET /estimations[?model_id]` → `{estimations: [{id, name, model_id, method, prefix, label, version, stale,
  stale_reason, source_lost, plans: [id], last_run {at, by_status} | null, created_at, updated_at}]}` (по имени).
- `GET /estimations/{id}` → `{id, name, version, meta, columns: spec.columns(), fields_exist: [имена полей,
  которые уже есть в модели], stale, stale_reason, source_lost, plans, created_at, updated_at}`; 404
  `estimation_not_found`.
- `POST /estimations` тело §3 → `{id, name, version: 1}`. Проверки store (18.11): ссылки (404), поля и их типы,
  `label` есть в поле модели и хотя бы одно значение домена есть в таблице — иначе 422 `domain_unmapped
  {param}`; проб домена 0 — 422 `no_samples {counts}`; модель с переменным шагом — 422 `variable_grid`; копия
  модели вариограммы (`snapshot`, `sha`, `scale`, `share`; модель в Y без `back` — 422 `variogram_scale`);
  `from: model | trend` — радиусы и углы; `origin` по пробам; `inputs`; `object_refs` тренда и фильтра. 423.
- `PUT /estimations/{id}` `{expected_version, …ключи §3}` → `{id, name, version}`: ключи верхнего уровня заменяют
  хранимые, итог разбирается заново; снимок модели вариограммы берётся заново (новая версия модели снимает
  «устарел»); `origin` пересчитывается, если сменились пробы (`samples`). 404, 409 `version_conflict`,
  `name_taken`, 422, 423.
- `DELETE /estimations/{id}` → `{deleted: id, plans: [id]}`: строка уходит из планов (их `version` + 1, события),
  blob `xval` — со сборкой мусора; поля модели остаются. 404, 423.
- `GET /estimation-plans[?model_id]` → `{plans: [{id, name, model_id, items: [id], version, created_at,
  updated_at}]}`; `GET /estimation-plans/{id}` → `{…, items: [{id, name, method, prefix, label, stale}]}` (форма
  `contract_answers.plan`); 404 `plan_not_found`.
- `POST /estimation-plans` `{name?, model_id, items: [id] (0–100, без повторов)}` → `{id, name, version}`;
  `PUT /estimation-plans/{id}` `{expected_version, name?, items?}`; `DELETE` → `{deleted}` (строки остаются).
  Проверки: строка есть (404 `estimation_not_found {param: "items.N"}`), той же модели и совместима по префиксу
  (§2.2) — иначе 422 `bad_param {param: "items.N"}`. 409, 423.
- `.bfggis`: `geostat/remap.py` (расширение 18.11) перенумеровывает `model_id`, `samples.set_id`, `samples.table`,
  `samples.domain.table`, `variogram(s).model_id`, `trend_id`, `target.filter_id`, `inputs[].id` (нет в архиве —
  null → «источник удалён»), `items` планов (нет — строка убирается), `last_run.job` не меняется.

## 5. `GET /estimate/defaults?model_id&set_id&table&field[&block_domain&domain_table&domain_field&domain_values&weights&variogram_id&method]`

Синхронно, слот HEAVY. `domain_values` — через `;`; `weights` — как Э16 (нет — DECL_W при наличии, пусто — без
весов). Ответ (`contract_answers.defaults`):
`{model: {id, name, version, rows, parents, sub, regular, rotation, size, domain_fields: [{name, kind, labels |
values}]}, samples: {set_id, table, table_name, field, unit, weights, counts, mean {naive, declus}, domain: {table,
field, values: [{value, n, naive, declus}]} | null}, mapping: {метка блока: [значения домена проб]} (совпадение
имён без учёта регистра; null без доменов), variogram: {id, name, scale, share, warnings: [], snapshot, sha} |
null, search: {from, ang1…r3} | null, params: spec.defaults_json(), ranges: spec.ranges_json()}`.
`variogram.warnings` (мастер, Д-38): «Доля Эрмита ‹share› < 0,95 — обратное преобразование приближённое.»,
«Модель построена по другому полю (‹поле›).», «Модель построена по другому домену.» — предупреждения, не запрет;
модель в Y без `back` — 422 `variogram_scale {param: "variogram_id"}` для method ≠ sgs. Декл. среднее без весов —
наивное (`declus` = `naive`); нет значений — null.

## 6. Запуск — `POST /estimate/run` → задание `estimate.run` (`heavy`)

Тело `spec.parse_run`: `{plan_id | estimation_ids: [id], expected_version (блочной модели), replace: false}`;
ровно одно из `plan_id`, `estimation_ids` (иначе 422 `bad_param {param: "plan_id"}`). До постановки: строки есть,
одной модели, совместимы (§2.2), не «источник удалён» (409 `source_lost {param: "items.N"}`); версия модели
(409 `version_conflict`); выходное поле уже есть и `replace: false` — 409 `field_exists {field}` (окно спрашивает
собственным окном «Заменить поля …?» и шлёт `replace: true`); 423.

Стадии (`message`): «Пробы», «Поиск и кригинг: проход k» (k — номер прохода, который сейчас считается для порции),
«Запись». Порции по 2¹⁶ целей (пары (P, D) подряд идущих строк модели; `sub: own` — строки), K порций потоками
(`composites.strips.default_threads()`), запись по порядку (очередь ограничена), один `FieldsWriter`, фиксация —
`commit_fields(…, source=)` + `meta.last_run` строк + `lineage.record` в одной транзакции. Отмена — между
порциями, ничего не остаётся. Итог (`contract_answers.run_result`): `{model_id, version, plan_id | null, fields:
[имена], rows: [{estimation_id, prefix, label, counts}], merged: [{prefix, estimation_ids}], log: [строки
журнала], events}`; журнал называет число блоков с вырожденной системой по проходам и число усреднённых проб.

## 7. Предпросмотр и инспектор (синхронно, то же ядро — `E/kernel.estimate_block`)

**`POST /estimate/preview`** `{estimation: тело §3 | estimation_id, at: {row} | {ijk} | {xyz}, channel?, seq?}`
(мастер, ещё не сохранённые параметры; `spec.parse_at`): ≤ 100 мс. **`POST /estimate/inspect`** (Д-30, команда
`estimate.inspect`) `{model_id, block: [i, j, k] | xyz: [x, y, z] | row, params_id?, channel?, seq?}`
(`spec.parse_inspect`): ≤ 200 мс; без `params_id` — все строки, чьи поля (`source.op = estimate`) есть в модели и
чей домен содержит блок. `ijk` — родитель (субблоки `own` — первая строка родителя; `row` — точная строка);
`xyz` — строка, содержащая точку; вне модели — 422 `bad_param {param: "at.xyz"}`.

Ответ preview — `{at {row, p, ijk}, centre, size, label, status, pass, values {ключ: число | null}, mu, samples
[кандидат], ellipsoid {centre, ang1–3, r1–3 (× factor прохода), pass}, passes [{pass, ok, n, reason: null | "few" |
"singular"}], origin, counts {candidates, selected}}`. Ответ inspect — `{model_id, block {ijk, p, rows, centre,
size}, label, results: [{estimation_id, name, prefix, method, status, pass, values, stored (значения полей
модели), same (побитно равны), changed (входы или параметры изменились: `freshness` или `same = false`), warning:
null | "Параметры или входы изменились", mu, samples, ellipsoid, passes}]}`.

Кандидат: `{i (номер в каноническом порядке), row_id (dh_rows.id первой строки), hole, from, to, xyz, z_raw, z
(использованное: урезка, усреднение, ураганная), h (h_s), dist (евклидово), octant (0–7 в осях эллипсоида: бит 0 —
x ≥ 0, бит 1 — y ≥ 0, бит 2 — z ≥ 0), lambda (null у невыбранных; IDW — нормированный вес; NN — 1), reason}`.
Список — пробы с h_s ≤ r1·factor **последнего** прохода плюс пробы других доменов там же (только инспектор), не
больше 1000 ближайших (по (h_s, i)); `counts.candidates` — всего. Коды `reason` (фиксированный список,
`E/search.REASONS`, подписи — в `M/`):

| Код | Подпись | Код | Подпись |
|---|---|---|---|
| `selected` | «выбрана» | `other_domain` | «другой домен (жёсткая граница)» |
| `hy_capped` | «урезана до t» (выбрана, z = t) | `soft_far` | «дальше d_м (мягкая граница)» |
| `outside` | «вне эллипсоида» (h_s > r прохода, давшего оценку) | `hy_dropped` | «ураганная, отброшена» |
| `hole_limit` | «лимит скважины» | `merged` | «совпадающая точка (усреднена)» |
| `octant_limit` | «лимит октанта» | `over_n_max` | «за n_max» |

Отрезки к пробам, цвет по весу и эллипсоид — `M/scene.ts` (слой Д-14 `viz/ellipsoid` как есть). «Скопировать
снимок» — JSON ответа как есть. Переход к интервалу — окно 7.17 по `row_id`.

## 8. Проверка

### 8.1. Перекрёстная проверка — `POST /estimate/xval` → `estimate.xval` (`heavy`)

`{estimation_id, by: "hole" | "sample" (умолч. hole)}` → `{source, id}`; то же ядро с маской исключения (по пробе
i — проба i; по скважине — все пробы её скважины), та же `origin`, те же проходы; цель — положение пробы, точечно
(`disc` не применяется — оценка в точке, трактовка). Итог: `{estimation_id, by, n, summary, events:
[["geostat.changed", {kind: "estimation", id}]]}`; таблица — blob `xval` строки (Parquet: i, row_id, hole, from,
to, x, y, z, true, est, error, kv, z_error, pass, ns, status). `GET /estimations/{id}/xval?page=1&size=500` →
`{estimation_id, by, at, stale, total, page, pages, size, rows, summary, histogram {edges, counts}}`; нет
расчёта — 404 `xval_not_found`. error = est − true; z_error = error/√KV (KV ≤ 0 или нет — null); summary — `{n,
mean_error, std_error (делитель n), mae, r (Пирсон), slope (истинное на оценку: cov/var(est)), mean_ratio
(среднее error²/KV по строкам с KV > 0; IDW, NN — null)}`; строки STATUS ≠ 0 — в таблице, вне сводки.

### 8.2. KNA — `POST /estimate/kna` → `estimate.kna` (`quick`)

`{estimation_id | estimation, blocks: [{row} | {ijk} | {xyz}] (1–10) | null — три автоматически, vary?: ["n_max",
"radius", "disc", "size"]}`. Автоблоки — квантили 0,9 / 0,5 / 0,1 числа проб в r1 среди блоков строки (`auto`:
«много», «средне», «мало»). Перебор по одному параметру (№ 80): n_max 4…64 шагом 4; радиусы ×0,5…×2 шагом 0,25;
дискретизация d × d × d, d = 1…5 (трактовка); размер блока ×0,5 / ×1 / ×2 родителя. Показатели — KE, SLOPE, KV,
SNW, `w_far` (вес самой дальней выбранной пробы), LAGR, NS, status. Рекомендация — наименьший n_max с SLOPE ≥
0,95·max по блоку; общая — наибольшая из рекомендаций блоков (трактовка); не применяется автоматически. Форма —
`contract_answers.kna`.

### 8.3. Глобальная статистика — `POST /estimate/global` → `estimate.global` (`quick`)

`{estimation_id}` → `{estimation_id, field, composites {naive, declus: {n, mean, var, cv}}, nn {n, volume, mean,
var, cv} (NN на лету по тем же блокам, без записи, вес — объём), estimate {…} (EST блоков STATUS = 0, вес —
объём), diff_pct (100·(mean_est − mean_declus)/mean_declus), norm (|diff_pct| ≤ 5), block_var {expected (C̄_VV
модели для размера блока), estimate}}`; поля оценки нет в модели — 409 `not_estimated`.

### 8.4. Q-Q и swath

`POST /estimate/qq {estimation_id, quantiles?, weighted?: true}` — синхронно, HEAVY: форма `stats.qq.qq_samples`
(x — композиты домена с весом DECL_W при `weighted`, y — EST блоков STATUS = 0 с весом объёма). Swath — окно Э13
без правок ядра: ряд `{kind: "blocks", model_id, field: "<префикс>EST", filter: {op: "and", items: [{cond:
{field: "<префикс>STATUS", op: "=", value: 0}}, домен]}}` и ряд `{kind: "samples", set_id, table, field, weight:
"DECL_W" | null, within: "blocks"}` (`estimate.swath` открывает окно с ними).

### 8.5. Профиль блоков у контакта (Д-28)

`POST /stats/contact` (Э13) принимает `blocks: {model_id, field (поле оценки), status_field?}`: ответ получает
`blocks: {edges (те же), mean [], n [], volume []}` — среднее поля по блокам STATUS = 0 в корзинах знакового
расстояния центра блока до границы (A < 0 < B). Расстояние: домен из каркаса — `geo/sdf.signed_distance` (BVH,
знак — чётность луча; ядро общее с 8.9.3), домен из поля модели — `scipy.ndimage.distance_transform_edt` по сетке
родителей с размером ячейки (субблоки — по родителю): `E/contact.block_distance`. Команды: `chart.contact` +
`blocks` (кнопка «Задать мягкую границу…» → `estimate.open {soft: {a, b, distance}}`, uiOnly). Подшаг — по плану
оркестратора (в плане 18.x его нет).

## 9. Э18б — моделирование (`objects(kind="simulation")`, ADR 0024 §9, Д-23)

```
meta v1 {v, name, kind: sgs | sis | cosgs, model_id, target, samples, variogram {…, scale: y (sgs, cosgs) | z (sis)},
         variograms (sis per), method: null | {ik: {thresholds | categories, models}} (sis),
         search {from, ang1–3, r1–3, passes: [один проход: n_max проб 24]}, nodes 12, seed 12345, n_real 20,
         secondary: null | {field, rho: число | null (считается), rho_used},
         nscore {ltail, utail, ltpar, utpar} (как `backtr`), origin, inputs, algo "sim-1", versions,
         last_run {at, job, nodes, realizations}}
blob real: Parquet zstd, строки — цели (P, D) родителей регулярной модели по порядку модели:
           p u64, dom i2, r001…rNNN — f4 (SGS, ко-SGS, SIS с порогами) | u1 (SIS-категории, коды подписей)
```

- Разбор — `S/spec.py` (18.30/18.32) на частях `E/spec_parts`: `parse_samples`, `parse_target`, `parse_search`,
  `parse_vario(…, simulation=True)` (модель в Y разрешена; в SIS — индикаторные в Z). Диапазоны: `seed` 0…2⁶³−1
  (12345, 8.0.1), `n_real` 1–100 (20), n_max проб 1–128 (24), `nodes` 0–64 (12), `rho` [−1; 1], категории SIS —
  2–20 подписей поля домена проб. Субблочная модель — узлы-родители, субблоки берут значение родителя; переменный
  шаг — 422 `variable_grid` (№ 83).
- `POST /simulate/run {simulation: тело | simulation_id, expected_version?}` → `simulate.run` (`heavy`): стадии
  «Нормальные оценки», «Реализации k / N», «Запись»; итог `{simulation_id, nodes, realizations, seconds,
  events}`. `GET /simulations[?model_id]`, `GET /simulations/{id}` (форма `contract_answers.simulation`),
  `PUT /simulations/{id} {expected_version, …}`, `DELETE /simulations/{id}` (blob `real` — со сборкой мусора).
- `POST /simulations/{id}/post {expected_version (модели), prefix (умолч. «‹ПОЛЕ›_SGS_»), stats: ["mean", "std",
  "p10", "p50", "p90", "prob"], thresholds: [t…] (для prob), replace}` → `simulate.post` (`quick`): поля MEAN,
  STD (f8), P10, P50, P90 (f8), `PROB_GT_<t>` (f4; t — `format(t, "g")`, «.» → «_») через `stats/local.local_*`;
  409 `field_exists`, `version_conflict`.
- Команды: `simulate.sgs` (+ `secondary`), `simulate.sis`, `simulate.post`; дерево «Геостатистика → Реализации».

## 10. Задания

| Вид | Очередь | Маршрут | Стадии | Модуль |
|---|---|---|---|---|
| `estimate.run` | heavy | `POST /estimate/run` | «Пробы», «Поиск и кригинг: проход k», «Запись» | `E/job.py` |
| `estimate.xval` | heavy | `POST /estimate/xval` | «Пробы», «Перекрёстная проверка», «Запись» | `E/xval.py` |
| `estimate.kna` | quick | `POST /estimate/kna` | «Пробы», «Варианты» | `E/kna.py` |
| `estimate.global` | quick | `POST /estimate/global` | «Чтение», «Ближайший сосед», «Статистика» | `E/checks.py` |
| `simulate.run` | heavy | `POST /simulate/run` | «Нормальные оценки», «Реализации k / N», «Запись» | `S/job.py` |
| `simulate.post` | quick | `POST /simulations/{id}/post` | «Чтение», «Расчёт», «Запись» | `S/post.py` |

Строки оркестратора: `jobs/registry.py` — `bf_core.geostat.estimate.job`, `…estimate.xval`, `…estimate.kna`,
`…estimate.checks`, `bf_core.geostat.simulation.job`, `…simulation.post`; `ops/warm.py` — шаги `estimate`,
`simulate`; `db/schema.py` — роли `xval`, `real`; `archive/importer.py` — вызов remap строк, планов, моделирований.

## 11. Интерфейсы Python

Соглашения ядер: float64; NaN — пусто; numba `cache=True, nogil=True`, без `prange`, `fastmath`, BLAS; координаты
ядер — локальные (`xyz − origin`); результат побитно одинаков при 1, 2, 4, 8 потоках; массивы целей — порции
`CHUNK = 1 << 16`.

```python
# E/spec.py, E/spec_parts.py (18.1, готово) — см. докстроки; главное:
parse_estimation(body) -> EstimationSpec   # .canonical() .hash_params() .columns() .resolved(trend=) .point
                                           # .search_variogram()  поля: name, model_id, target, sub, disc, samples,
                                           # method, variogram, variograms, trend_id, search, outputs
parse_plan(body) -> PlanSpec; parse_run(body) -> RunSpec; parse_at(raw) -> {row}|{ijk}|{xyz}
parse_inspect(body) -> InspectSpec(model_id, at, params_id)
parse_vario(raw, param, t=None, *, simulation=False) -> VarioRef(model_id, snapshot, sha, scale, share, t)
search_from_model(snapshot) -> (angles, radii); search_from_trend(trend_form, r1) -> (angles, radii)
Pass(factor, n_min, n_max, n_hole, n_oct, k_oct, n_holes_min, hy: Hy(t, q, mode) | None)
STATUS_LABELS, DTYPES, ALLOWED, DEFAULT_FIELDS, ALGO = "est-1", SELECTION_ORDER

# E/samples.py (18.5) — пробы строки плана в каноническом порядке
@dataclass(frozen=True)
class RowSamples:
    xyz: f8[n, 3]; local: f8[n, 3]      # проекта; − origin
    z: f8[n]; raw: f8[n]                # использованное (урезка, усреднение) / исходное
    w: f8[n] | None                     # веса декластеризации; NaN — нет веса
    hole: i4[n]; holes: list[str]
    dom: i2[n]                          # 0 — D; 1 — S (мягкая); ≥ 2 — другие домены (только all_domains)
    row_id: i8[n]; span: f8[n, 2]       # dh_rows.id и FROM, TO первой строки
    merged: i2[n]                       # сколько строк усреднено (1 — нет)
    origin: f8[3]; counts: dict; inputs: list[dict]; means: {"naive": float|None, "declus": float|None}
load(project, spec, *, origin=None, all_domains=False, cancel=None) -> RowSamples   # 422 no_samples {counts}
canonical_order(xyz, z) -> intp[n]                    # lexsort (X, Y, Z, значение)
merge_coincident(xyz, z, w, hole, dom, tol=1e-6) -> (keep: intp[m], group: intp[n])
origin_of(xyz) -> f8[3]                               # центр охвата
declus_mean(z, w) -> float | None

# E/cov.py (18.3) — = variogram.model.Model.cov до 1e−15
@dataclass(frozen=True)
class CovModel:
    c0: float; sill: float              # C(0) = C0 + Σ c_s
    kinds: i8[S]; c: f8[S]; mats: f8[S, 3, 3]   # h_r = ‖mats[s]·Δ‖ (= diag(1/a1, 1/a2, 1/a3)·R)
    @classmethod
    def of(cls, snapshot: Mapping | Model) -> CovModel
cov(cm, d: f8[n, 3]) -> f8[n]; cov_star(cm, d) -> f8[n]   # C (Δ = 0 → C(0)), C* (без самородка)
@njit cov_ij(kinds, c, mats, c0, dx, dy, dz) -> float; cov_star_ij(...) -> float
disc_offsets(size: (dx, dy, dz), disc: (nx, ny, nz), rotation_deg: float) -> f8[N, 3]   # центры подъячеек от
                                                       # центра блока, оси проекта (R_z(φ)); N = 1 → [[0, 0, 0]]
cbar_iv(cm, xs: f8[n, 3], centre: f8[3], offsets) -> f8[n]
class CvvCache: def get(self, cm, size, disc, rotation_deg) -> float     # C̄_VV; N = 1 → C(0)

# E/krige.py (18.4)
OUT = ("EST", "KV", "SLOPE", "KE", "LAGR", "SNW")   # индексы выхода out[6]
@njit lu_solve(a: f8[k, k], b: f8[k], tol: float) -> bool            # на месте, выбор главного; False — < tol
@njit krige(kinds, c, mats, c0, sill, xs: f8[n, 3], z: f8[n], centre: f8[3], offsets: f8[N, 3], cvv: float,
            ordinary: bool, mean: float, lam: f8[n], out: f8[6], work: f8[(n + 1)·(n + 2)]) -> int
            # 0 — решено, 3 — вырождена (главный элемент < 1e−12·C(0)); μ — out[LAGR] (SK: 0)
krige_one(cm, xs, z, centre, offsets, cvv, *, ordinary=True, mean=0.0) -> KrigeResult   # обёртка для тестов
    # KrigeResult(lam, mu, est, kv, slope, ke, snw, status)

# E/methods.py (18.7)
@njit nn(z, h, idx) -> float                          # ближайшая по (h_s, номер)
@njit idw(z, h, power, r1) -> (est, weights)          # h < 1e−9·r1 — среднее совпадающих (BF idw)

# E/search.py (18.6)
REASONS = ("selected", "hy_capped", "outside", "hole_limit", "octant_limit", "over_n_max", "other_domain",
           "soft_far", "hy_dropped", "merged")         # u1-коды = индекс
@dataclass(frozen=True)
class Ellipsoid:
    angles: tuple; radii: tuple; S: f8[3, 3]          # diag(1, r1/r2, r1/r3)·R; h_s = ‖S·Δ‖; внутри — h_s ≤ r1
    def h(self, d: f8[n, 3]) -> f8[n]; def octant(self, d) -> i1[n]   # октант в осях эллипсоида
class Searcher:                                        # одно cKDTree на строку (D ∪ S) в шар-пространстве
    def __init__(self, s: RowSamples, ell: Ellipsoid, passes: Sequence[Pass], soft_dist: float) -> None
    def gather(self, centres: f8[m, 3], k: int, *, todo: bool[m] | None = None,
               exclude: tuple[str, np.ndarray] | None = None) -> Neighbours
               # k — номер прохода с 0; exclude ("sample", i8[m]) | ("hole", i4[m]) — xval; k_c = max(4·n_max,
               # 64), повтор ×4 (8.8.2, решение 6)
    def explain(self, centre: f8[3], k: int, *, pool: RowSamples | None = None) -> Candidates   # инспектор
@dataclass
class Neighbours:                                      # по целям порции; −1 — нет
    idx: i8[m, n_max]; h: f8[m, n_max]; z: f8[m, n_max]   # отобранные, по (h_s, номер); z — использованное
    n: i2[m]; nh: i2[m]; nhy: i2[m]; noct: i1[m]; ok: bool[m]   # ok — n_min, k_окт, n_скв_min выполнены
@dataclass
class Candidates:
    idx: i8[k]; h: f8[k]; dist: f8[k]; octant: i1[k]; z: f8[k]; reason: u1[k]   # отсортированы по (h_s, номер)

# E/indicator.py (18.9)
indicators(z, thresholds) -> f8[n, K]                  # [z ≤ t_k]; NaN остаётся NaN
@njit order_relations(F: f8[K]) -> None                # на месте: обрезка [0; 1], среднее поправок вверх и вниз
class_means(z, w, thresholds) -> f8[K + 1]             # декл. средние классов; пустой класс — середина порогов
@njit etype(F: f8[K], means: f8[K + 1]) -> float

# E/kernel.py (18.7) — порция → проходы → метод
@dataclass(frozen=True)
class RowKernel:                                       # скомпилированная строка плана
    spec: EstimationSpec; samples: RowSamples; cov: CovModel | tuple[CovModel, ...] | None
    ell: Ellipsoid; searcher: Searcher; offsets: f8[N, 3]; cvv: float; mean: float; class_means: f8[K + 1] | None
compile_row(spec, samples, *, block_size: (dx, dy, dz), rotation_deg: float, cvv: CvvCache) -> RowKernel
@dataclass
class ChunkResult:
    values: dict[str, f8[m]]                           # ключи outputs (P → P1…PK), float64, NaN — пусто
    status: i1[m]; by_pass: i8[P]; singular: i8[P]
estimate(rk, centres: f8[m, 3], *, sizes: f8[m, 3] | None = None, exclude=None) -> ChunkResult
estimate_block(rk, centre: f8[3], size=None, *, pool: RowSamples | None = None) -> BlockDetail
    # BlockDetail(values, status, pass_, mu, lam: f8[n_sel], candidates: Candidates, ellipsoid, passes)
run(rk, chunks: Iterable[f8[m, 3]], *, threads: int, tick=None) -> Iterator[ChunkResult]   # порядок порций

# E/store.py (18.11)
KIND = "estimation"; PLAN = "estimation_plan"
create(conn, store, project, spec, author=None) -> {id, name, version}; update(conn, …, id, raw,
expected_version) -> {…}; delete(conn, id) -> {deleted, plans}; get(conn, id) -> форма §4; list_rows(conn,
model_id=None); freshness(conn, meta) -> {stale, stale_reason, source_lost}; inputs_of(conn, spec, samples) -> list
plan_create / plan_update / plan_delete / plan_get / plan_list; check_plan(conn, items) -> [EstimationSpec]
estimation_meta(meta, objects_of, tables_of) -> dict    # в geostat/remap.py (.bfggis)

# E/job.py (18.12)
KIND = "estimate.run"; run_estimate(engine, folder, params, tick) -> dict   # форма итога §6

# E/xval.py (18.14), E/kna.py (18.15), E/checks.py (18.16), E/contact.py (Д-28)
run_xval(engine, folder, params, tick) -> dict; summary(true, est, kv) -> dict; read_page(conn, store, id, page,
size) -> dict
analyse(project, spec, blocks, vary, *, tick=None) -> dict; auto_blocks(rk, model) -> list[dict];
recommend(rows) -> int
global_stats(project, estimation_id, *, tick=None) -> dict; qq(project, estimation_id, quantiles, weighted) -> dict
block_distance(model, *, field: str, a: str, b: str) -> f8[rows]           # EDT по сетке родителей, A < 0 < B
# geo/sdf.py: signed_distance(vertices f8[v, 3], triangles i4[t, 3], points f8[n, 3]) -> f8[n]  (внутри < 0)

# E/routes.py, E/routes_check.py (18.13): routers, подключает geostat/router.py (оркестратор); E/warm.py (18.17)
```

**Э18б (`S/`):**

```python
# S/path.py (18.30)
generator(seed: int, n_real: int, r: int) -> np.random.Generator   # PCG64(SeedSequence(seed).spawn(n_real)[r])
random_path(n: int, rng) -> intp[n]
# S/nodes.py (18.30)
spiral_template(steps: (dx, dy, dz), ell: Ellipsoid, cm: CovModel, n_max: int) -> i4[k, 3]   # смещения сетки по
                                                                     # убыванию C, равные — по (|di|, …) (GSLIB)
@njit nearest_nodes(grid_index, simulated: bool[...], template, n_nodes) -> i8[n_nodes]
# S/sgs.py (18.31)
simulate_sgs(rk: SimKernel, r: int, out: f4[n_nodes]) -> None          # одна реализация, последовательно
compile_sim(spec_sim, samples, model) -> SimKernel                      # nscore с весами, SK μ = 0
# S/sis.py (18.33), S/cosgs.py (18.34)
simulate_sis(rk, r, out) -> None; simulate_cosgs(rk, r, secondary_y: f8[n_nodes], rho: float, out) -> None
# S/post.py (18.32)
post(realizations: f4[n, N], stats, thresholds) -> dict[str, f8[n]]    # stats/local.local_* (1e−12)
# S/store.py, S/job.py, S/routes.py (18.32): KIND = "simulation"; "simulate.run", "simulate.post"
```

## 12. Команды → маршруты (вкладка «Геостатистика»; лента и дерево — оркестратор)

| Команда | Признаки | Маршрут / окно |
|---|---|---|
| `estimate.open {method?, soft?: {a, b, distance}}` | uiOnly | мастер (шаги: модель, ограничитель, поля → пробы → метод и вариограмма → поиск и проходы → выходные поля → задание); `defaults`, `preview` |
| `estimate.plan` | uiOnly | окно «План оценки»: `/estimation-plans`, `/estimations` |
| `estimate.ellipsoid` | — | слой эллипсоида поиска строки (`preview` → `ellipsoid`) |
| `estimate.run {plan_id \| estimation_ids}` | confirm (при замене полей) | `POST /estimate/run` |
| `estimate.delete {id}` | confirm | `DELETE /estimations/{id}` |
| `estimate.inspect {model_id, block \| xyz, params_id?}` | — | `POST /estimate/inspect`; окно «Блок · i, j, k» |
| `estimate.xval {estimation_id, by?}` | — | `POST /estimate/xval` → `GET /estimations/{id}/xval` |
| `estimate.kna`, `estimate.global`, `estimate.qq`, `estimate.swath` | — | §8.2–8.4 |
| `chart.contact` + `blocks` | — | `POST /stats/contact` (§8.5) |
| `simulate.sgs {…, secondary?}`, `simulate.sis {…}`, `simulate.post {id, …}` | —, —, confirm | §9 |

Без параметров команда открывает окно; «только просмотр» — 423 у пишущих.

## 13. Фикстуры

- `core/tests/data/estimate/params.json` — 5 строк (`idw`, `ok_point`, `sk`, `ik`, `ok_block_passes`): `body` →
  `canonical` (`spec.parse_estimation(body).canonical()`) и `columns`; круговой проход — `test_estimate_spec.py`;
  `BF_REGEN_ESTIMATE=1` переписывает `canonical` и `columns`.
- `core/tests/data/estimate/contract_answers.json` — образцы ответов §4–9 (`defaults`, `estimations`, `estimation`,
  `estimation_post`, `estimation_put_conflict`, `bad_param`, `variogram_scale`, `plan`, `run_submit`,
  `run_field_exists`, `run_result`, `preview`, `inspect`, `xval_submit`, `xval`, `kna`, `global`, `simulation`,
  `simulate_result`, `simulate_post_result`), `{status, body}`. Числа `preview`/`inspect` — Э-ОК-1 (Т8.8-1).
  **Образцы, не настоящие ответы:** интерфейс И-А/И-Б/И-В строится на них до 18.13; принимается только на
  настоящих (`BF_REGEN_ESTIMATE=1` в 18.13, 18.14–18.16, 18.32 заменяет файл ответами маршрутов).

## 14. Трактовки (вне буквы ТЗ; на согласование оркестратора)

1. STATUS — числовое i1 со словарём в `description`, не `cat` (сравнение `STATUS = 0` в калькуляторе 8.7.4).
2. `NHY` по умолчанию — только если в проходе включено правило Д-29; STATUS — всегда, вне выбора пользователя.
3. Диапазоны, которых нет в таблице 8.8.2: `factor` (0; 100], `n_holes_min` 0–128, пороги IK 1–20, префикс 1–40
   знаков `\w`, план ≤ 100 строк; Э18б — `n_real` 1–100, `nodes` 0–64.
4. `from: manual` — r2, r3 по умолчанию = r1, углы 0; `from: trend` — r1 = `size` тренда; модель для радиусов
   при IK «на порог» — модель среднего порога.
5. Пороги `hy` — по радиусу прохода (q·r1·factor).
6. Перекрёстная проверка — точечно в положении пробы; KNA — дискретизация d × d × d; общая рекомендация KNA —
   наибольшая по блокам.
7. Инспектор показывает до 1000 кандидатов (h_s ≤ радиуса последнего прохода).
8. `DELETE` строки убирает её из планов (а не 409).
9. IK: пустой класс — m_k = середина его порогов (крайние — порог); ETYPE по 8.8.1.
