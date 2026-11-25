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
  + last_run {at, job, version (блочной модели после записи), counts, merged, class_means, params} | null
    (`params` — SHA-256 хеша параметров без имени, `store_base.params_hash`; причина «изменены параметры»)
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
  домена; 2 — мало проб; 3 — вырожденная система» (вопрос № 95; трактовка: числовое i1, а не `cat`, — `STATUS = 0` и
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
  {soft_a, soft_b, soft_distance}` → мастер: `samples.domain.values = [soft_a]`, `soft = {domains: [soft_b], dist: soft_distance}`);
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

Индикаторная модель (18.10, `transform` = `indicator:<t>`, `.15g`): ответ `GET /variography/defaults` (вариография,
Э16) дополнен `indicator {threshold, p}` — порог t и доля `p` проб с z ≤ t (σ² = p(1 − p)); преобразование
модели хранится строкой `indicator:<t>` (ключ кеша и `meta.transform`).

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
журнала], stages, events}`; журнал называет число блоков с вырожденной системой по проходам и число усреднённых проб.
`stages` — секунды этапов `{samples, search, write, total}`: «Пробы» (чтение и компиляция строк), «Поиск и кригинг»
(`search`), «Запись» (`write`) и всё задание (`total`), округлено до 1 мс; образец `run_result.stages` в
`contract_answers.json` не сверяется (время). `counts.targets` и `counts.by_status` считают строки блочной модели
(блок вне домена строки — STATUS 1), `by_pass` и `singular` — блоки, которые ядро считало (при `sub: parent` —
родителей).

## 7. Предпросмотр и инспектор (синхронно, то же ядро — `E/kernel.estimate_block`)

**`POST /estimate/preview`** `{estimation: тело §3 | estimation_id, at: {row} | {ijk} | {xyz}, channel?, seq?}`
(мастер, ещё не сохранённые параметры; `spec.parse_at`): ≤ 100 мс. **`POST /estimate/inspect`** (Д-30, команда
`estimate.inspect`) `{model_id, block: [i, j, k] | xyz: [x, y, z] | row, params_id?, channel?, seq?}`
(`spec.parse_inspect`): ≤ 200 мс; без `params_id` — все строки, чьи поля (`source.op = estimate`) есть в модели и
чей домен содержит блок. `ijk` — родитель (субблоки `own` — первая строка родителя; `row` — точная строка);
`xyz` — строка, содержащая точку; вне модели — 422 `bad_param {param: "at.xyz"}`.

Ответ preview — `{at {row, p, ijk}, centre, size, label, status, pass, values {ключ: число | null}, mu, samples
[кандидат], ellipsoid {centre, ang1–3, r1–3 (× factor прохода), pass}, passes [{pass, ok, n, reason: null | "few" |
"singular"}], origin, counts {candidates, selected}}`. Ответ inspect (`E/inspect.py`, сверено с кодом 18.Д3) —
`{model_id, model_version, block {row, p, ijk, sub, centre, size, parent {centre, size}, rows}, results: [{estimation_id,
name, version, method, prefix, label, sub, disc, source {set_id, table, field}, block {centre, size, rows}, domain
{field, label, row_label}, active, status, status_label, pass, values, mu, ellipsoid, passes, counts {candidates,
shown, selected}, soft_dist, candidates [кандидат], segments [{i, from, to, lambda, z}] (отрезки в 3D к выбранным
пробам), fields [{key, name, dtype, unit, calc (пересчёт), stored (в поле модели), same, exists}], same (побитно
равны записанным | null), missing_fields, changed (входы или параметры изменились: `freshness` или `same = false`),
warning: null | "Параметры или входы изменились", warning_detail, stale_reason, lineage {state, reason, changed},
snapshot (JSON для «Скопировать снимок»)}], warnings [строки: строка пропущена — источник удалён, блок вне доменов]}`.
«Побитно равно» — пересчёт приведён к типу записанного поля (f4, целые) и сравнён; пустое равно пустому.

Кандидат: `{i (номер в каноническом порядке), row_id (dh_rows.id первой строки), hole, from, to, xyz, z_raw, z
(использованное: урезка, усреднение, ураганная), h (h_s), dist (евклидово), octant (0–7 в осях эллипсоида: бит 0 —
x ≥ 0, бит 1 — y ≥ 0, бит 2 — z ≥ 0), lambda (null у невыбранных; IDW — нормированный вес; NN — 1; IK — null),
reason (код), reason_label (подпись по-русски: у «урезана до t» — со значением t, у усреднённой — «; совпадающие
точки усреднены: N»), merged (строк усреднено в пробу), domain (подпись домена: домен оценки, «мягкая граница» или
другой домен)}`. В инспекторе у STATUS 2 и 3 отрезков и весов нет.
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
(среднее error²/KV по строкам с KV > 0; IDW, NN — null), n_ratio (строк с KV > 0, по которым считан mean_ratio),
n_kv_zero (строк с KV ≤ 0 — z_error null), n_skipped (строк STATUS ≠ 0 — вне сводки), by_status [n0, n1, n2, n3]
(по всей таблице)}`; счётчики `n_ratio`, `n_kv_zero`, `n_skipped` — по строкам STATUS = 0; строки STATUS ≠ 0 — в
таблице, вне сводки. `XvalTable.summary()` (задание) отдаёт сводку с `by_status`; `summary(true, est, kv)` — без него.
`read_page(conn, store, estimation_id, page=1, size=500)` (`E/xval.py`): `rows` — словари столбцов (NaN — null),
`size` 1…5000 (422 `bad_param {param: "size"}`), `page` — 422 `bad_param {param: "page"}`; `histogram` без расчёта —
`{edges: [], counts: []}`; `stale` — параметры или входы строки не те, что у расчёта; `at` — время расчёта.

### 8.2. KNA — `POST /estimate/kna` → `estimate.kna` (`quick`)

`{estimation_id | estimation, blocks: [{row} | {ijk} | {xyz}] (1–10) | null — три автоматически, vary?: ["n_max",
"radius", "disc", "size"]}`. Автоблоки — квантили 0,9 / 0,5 / 0,1 числа проб в r1 среди блоков строки (`auto`:
«много», «средне», «мало»). Перебор по одному параметру (№ 80): n_max 4…64 шагом 4; радиусы ×0,5…×2 шагом 0,25;
дискретизация d × d × d, d = 1…5 (трактовка); размер блока ×0,5 / ×1 / ×2 родителя. Показатели — KE, SLOPE, KV,
SNW, `w_far` (вес самой дальней выбранной пробы), LAGR, NS, status. Рекомендация — наименьший n_max с SLOPE ≥
0,95·max по блоку; общая — наибольшая из рекомендаций блоков (трактовка); не применяется автоматически. Форма —
`contract_answers.kna`. Метод строки — только SK и OK (иначе 422 `kna_method`); `estimation_id` и `estimation` — ровно
одно из двух (иначе 422 `bad_param {param: "estimation_id"}`); ответ — `{source, id}` задания, стадии «Пробы»,
«Варианты».

Ответ задания: `{estimation_id, name, method, vary [перебираемые параметры], threshold (0,95), blocks, recommend,
candidates, warnings}`.
- `blocks[]`: `{at {row, p, ijk}, centre [x, y, z], size [dx, dy, dz], n_r1 (проб в радиусе r1 первого прохода), auto
  ("много" | "средне" | "мало" | null — у блока, заданного пользователем), base (показатели блока при параметрах
  строки), vary {параметр: [{value, KE, SLOPE, KV, SNW, w_far, LAGR, NS, status}]}, recommend {n_max}}`. `value` —
  `n_max` целое, `radius` и `size` множитель (число), `disc` — `[d, d, d]`. Показатели блока со STATUS 1–3 — `null`,
  не 0.
- `recommend {n_max}` — общая рекомендация (наибольшая по блокам); `null`, если ни у одного блока нет STATUS = 0 с
  SLOPE > 0; без `n_max` в `vary` — `{}`.
- `candidates` — число блоков домена строки (при `sub: parent` — родителей) до обрезки списка кандидатов, из которых
  выбраны три блока; `null`, если блоки заданы. В домене нет блоков или ни у одного нет проб в радиусе последнего
  прохода — 422 `kna_no_blocks`.
- `warnings` — например, «Дискретизация блока 1×1×1 (точка): размер блока не меняет оценку» при `vary` с `size` для
  точечной строки.

### 8.3. Глобальная статистика — `POST /estimate/global` → `estimate.global` (`quick`)

`{estimation_id}` → `{source, id}` задания (стадии «Чтение», «Ближайший сосед», «Статистика»); итог — `{estimation_id,
name, field, status_field, model_id, method, sub, limit_pct (5), composites {naive, declus: {n, mean, var, cv},
reference ("declus" | "naive")}, nn {n, volume, mean, var, cv} (NN на лету по тем же блокам, без записи, вес —
объём), estimate {…} (EST блоков STATUS = 0, вес — объём; у IK — ETYPE), diff_pct (100·(mean_est −
mean_ref)/mean_ref), norm (|diff_pct| ≤ limit_pct), diff_nn_pct, norm_nn (то же против NN), block_var {expected, sill,
ratio, disc, size, estimate}, blocks {rows, by_status, used, empty, no_volume}, warnings}`; поля оценки нет в
модели — 409 `not_estimated`, блоков со STATUS = 0 нет — 422 `no_estimated_blocks`.
- `composites.reference` — с чем сравнивается оценка: `declus` (есть веса декластеризации) или `naive` (весов у
  строки нет, в `warnings` — предупреждение); `diff_pct`, `norm` считаются от него. Дисперсия — по совокупности
  (Σw(z − m)²/Σw).
- `block_var.expected` — C̄_VV модели вариограммы для размера блока при дискретизации строки (у точечной строки — для
  4×4×4: опора блока, не точки); `expected`, `sill`, `disc` и `ratio` — `null` у IK и у строки без модели вариограммы
  (NN, IDW); `sill` — C(0) модели; `ratio` = `estimate`/`expected` (кригинг сглаживает: ниже 1); `size` — размер
  блока (переменный шаг сетки — медианный размер, в `warnings` — предупреждение).
- `blocks.rows` — строк в модели; `by_status` [n0, n1, n2, n3]; `used` — блоков со STATUS = 0 в сумме; `empty` —
  STATUS = 0 без значения; `no_volume` — STATUS = 0 без объёма (нет FRAC): `empty` и `no_volume` вне статистики,
  в `warnings` — их число.
- Результат побитно один при 1, 2, 4, 8 потоках; порции читаются по `PORTION` строк, суммы — парные, порции
  объединяются формулой Чана.

### 8.4. Q-Q и swath

`POST /estimate/qq {estimation_id, quantiles?, weighted?: true}` — синхронно (без задания): `{estimation_id, name,
field, weighted, …форма stats.qq.qq_samples (points, ratio, extremes, ks, marks), x {n, mean, var, cv}, y {…}}`
(x — композиты домена с весом DECL_W при `weighted` и наличии весов у строки, y — EST блоков STATUS = 0 с весом
объёма; у IK — ETYPE).

Swath — `POST /estimate/swath {estimation_id, direction?: "x" | "y" | "z" (умолч. z)}` (синхронно) → `{estimation_id,
name, direction, series}`; настройки окна Э13 без правок ядра: ряд `{kind: "blocks", model_id, field:
"<префикс>EST" (IK — ETYPE), weight: "volume", density: null, filter: {field: домен, labels: [метка]} | null,
filter_id}` и ряд `{kind: "samples", set_id, table, field, weight: "field" | "none", weight_field: "DECL_W" | имя |
null, negatives: "keep", within: "blocks", within_series: 0}`. Фильтра по STATUS нет: у блоков со STATUS ≠ 0
`<префикс>EST` пуст, и swath их пропускает. Настройки не сохраняются (`estimate.swath` открывает окно с ними;
сохраняет сам swath).

### 8.5. Профиль блоков у контакта (Д-28)

`POST /stats/contact` (Э13) принимает `blocks: {model_id, field (поле оценки), status_field?, domain_field?}`: ответ получает
`blocks: {edges (те же), mean [], n [], volume []}` — среднее поля по блокам STATUS = 0 в корзинах знакового
расстояния центра блока до границы (A < 0 < B). Расстояние: домен из каркаса — `geo/sdf.signed_distance` (BVH,
знак — чётность луча; ядро общее с 8.9.3), домен из поля модели — `scipy.ndimage.distance_transform_edt` по сетке
родителей с размером ячейки (субблоки — по родителю): `E/contact.block_distance`. Контакт: при каркасе проб (`wf`) — каркас, `domain_field` не нужен и
не используется; иначе — `domain_field` (категория модели; по умолчанию — `domain` окна), метки `a`, `b` — из
настроек контакта. Команды: `chart.contact` +
`blocks` (кнопка «Задать мягкую границу…» → `estimate.open {soft_a, soft_b, soft_distance}`, uiOnly). Подшаг — по плану
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
ядер — локальные (`xyz − origin`); результат побитно одинаков при 1, 2, 4, 8 потоках; порции целей задаёт
вызывающий (`run`): задание `estimate.run` — 2¹⁶ целей (§6), перекрёстная проверка — `xval.CHUNK` = 4096.

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

# E/cov.py (18.3) — = variogram.model.Model.cov до 1e−15; массивы модели: kinds i8[S], pars f8[S, 13], head f8[3] = (C0, C(0), Σc)
@dataclass(frozen=True)
class CovModel:
    kinds: np.ndarray; pars: np.ndarray; head: np.ndarray   # args → (kinds, pars, head); sill → head[1] = C(0); nugget → head[0]; key → байты для кеша
cov_model(model: Model) -> CovModel                          # Model → массивы; C(0) суммируется как Model.sill
@njit inline="always" cov_c(kinds, pars, head, dx, dy, dz) -> float         # C(Δ), с самородком; Δ = 0 → C(0)
@njit inline="always" cov_cstar(kinds, pars, head, dx, dy, dz) -> float     # C* — без самородка
@njit cov_pb(kinds, pars, head, px, py, pz, cx, cy, cz, offs f8[N, 3]) -> float   # C̄_iV точки p и блока (центр c, точки offs — смещения от c); N = 1 — C(Δ)
@njit cov_bb(kinds, pars, head, offs f8[N, 3]) -> float                    # C̄_VV блока: N = 1 — C(0), иначе среднее C* по N² парам
@njit cov_matrix(kinds, pars, head, xyz f8[n, 3], out f8[n, n]) -> None    # out[i, j] = C(x_i − x_j); нижний треугольник и симметрия
@njit cov_rhs(kinds, pars, head, xyz f8[n, 3], centre f8[3], offs f8[N, 3], out f8[n]) -> None   # out[i] = C̄_iV
disc_points(size, disc, phi=0.0) -> f8[N, 3]                # центры подъячеек блока (смещения от центра по осям мира; блок повёрнут на φ вокруг +Z); N = 1 → [[0, 0, 0]]
class BlockCov(cov: CovModel):                              # кеш C̄_VV по (size, disc, φ); потокобезопасен
    points(size, disc, phi=0.0) -> f8[N, 3]                # общий массив — только чтение
    vv(size, disc, phi=0.0) -> float
warm_kernels() -> None

# E/krige.py (18.4)
KR_OK, KR_SINGULAR, KR_NAN, KR_SIZE = 0, 1, 2, 3            # статус krige: решено / вырождена (главный элемент < 1e−12·C(0)) / NaN / n > MAX_SYS
O_EST, O_KV, O_SLOPE, O_KE, O_LAGR, O_SNW, O_PIV = range(7) # индексы out[7]; при статусе ≠ 0 пусты out[:O_PIV]
MAX_SYS = 129                                               # размер системы: n_max = 128 проб + строка OK
@njit krige(kinds, pars, head, xyz, n: int, z, centre, offs, bv: float, mean: float, ok: int,
            a, rhs, sol, out) -> int                        # ok 1 — OK, 0 — SK (mean = m); веса — sol[:n], μ — sol[n]; → KR_*
apply_weights(sol, n, z, mean, ok) -> float                 # z* с весами sol[:n] (тот же порядок сумм; OK — без mean)
class KrigeWork(size=MAX_SYS):                              # буферы на поток; run(cov, xyz, z, centre, offs, bv, mean=0.0, ok=1, n=None) -> int; результат — .out; lam(n); mu(n)
warm_krige() -> None

# E/samples.py (18.5) — пробы строки плана в каноническом порядке
D, S = 0, 1                                                 # коды dom: 0 — D; 1 — S (мягкая); ≥ 2 — другие домены (dom_labels[k − 2])
@dataclass(frozen=True)
class RowSamples:
    xyz: f8[n, 3]; local: f8[n, 3]                          # проекта; local = xyz − origin
    z: f8[n]; raw: f8[n]                                    # использованное (урезка, усреднение) / до урезки
    w: f8[n] | None                                         # веса декластеризации; NaN — нет веса; None — строка без весов
    hole: i4[n]; holes: list[str]                           # holes — имена скважин проб (по порядку)
    dom: i2[n]; dom_labels: list[str]
    row_id: i8[n]; span: f8[n, 2]                           # dh_rows.id и FROM, TO первой строки
    merged: i2[n]                                           # сколько строк усреднено (1 — нет)
    origin: f8[3]; counts: dict; inputs: list[dict]; means: {"naive": f8|None, "declus": f8|None}   # means — по D
    notes: list[str]                                        # строки для журнала задания
    n -> int; in_d -> bool[n]
    take(rows) -> RowSamples                                # подмножество (xval исключает пробу или скважину); origin, means, counts — строки
load(project, spec, *, origin=None, all_domains=False, cancel=None) -> RowSamples   # 422 no_samples {counts}: нет проб D
prepare(xyz, z, raw, w, hole, holes, dom, row_id, span, *, origin=None, dom_labels=None, counts=None, inputs=None, tol=TOL, cancel=None) -> RowSamples
canonical_order(xyz, z, *ties) -> intp[n]                   # lexsort (X, Y, Z, значение), затем ties (№ 76)
merge_coincident(xyz, dom, tol=TOL) -> (keep: intp[m], group: intp[n])   # совпадающие пробы одного домена (≤ tol, цепочки); keep — первая проба группы
origin_of(xyz) -> f8[3]                                     # центр охвата проб (8.0.1)
declus_mean(z, w) -> float | None

# E/methods.py (18.7)
nn(z, h, idx) -> float                                   # ближайшая по (h_s, номер); idx — позиции выборки
idw(z, h, power, r1) -> (est: float, w: f8[n])           # h < 1e−9·r1 — среднее совпадающих (BF idw); idw_into(z, h, power, r1, w) -> float — на месте

# E/search.py (18.6)
REASONS = ("selected", "hy_capped", "outside", "hole_limit", "octant_limit", "over_n_max", "other_domain",
           "soft_far", "hy_dropped", "merged")              # u1-коды = индекс; подписи — в M/
EXPLAIN_MAX = 1000                                          # кандидатов инспектора
@dataclass(frozen=True)
class Ellipsoid:
    angles: tuple[float, float, float]; radii: tuple[float, float, float]
    S: f8[3, 3]                                             # diag(1, r1/r2, r1/r3)·R; h_s = ‖S·Δ‖; внутри — h_s ≤ r1
    of(angles, radii) -> Ellipsoid                          # classmethod
    h(d f8[n, 3]) -> f8[n]; octant(d) -> i1[n]              # октант в осях эллипсоида: бит 0 — x ≥ 0, бит 1 — y ≥ 0, бит 2 — z ≥ 0
    r1 -> float
@dataclass class Neighbours:                                # выборка одного прохода для m блоков; −1 / NaN — нет; строки в порядке (h_s, номер)
    idx: i4[m, n_max]; h: f8[m, n_max]; z: f8[m, n_max]     # номера проб (позиции в RowSamples), h_s, использованное значение
    n: i2[m]; nh: i2[m]; nhy: i2[m]; noct: i1[m]            # проб, скважин, затронуто правилом Д-29, октантов
    ok: bool[m]                                             # n_min, k_окт, n_скв_min выполнены
    ad: f8[m]; dmin: f8[m]                                  # AD, DMIN (евклидовы)
    empty(m, width) -> Neighbours; put(rows, src, from_rows) # classmethod / self[rows] = src[from_rows]
@dataclass class Found:                                     # search: по блокам — выборка первого выполненного прохода
    nb: Neighbours; pass_: i1[m]                            # 1…P; 0 — ни один проход не выполнен («мало проб», строка пуста)
@dataclass class Candidates:                                # инспектор: кандидаты блока по (h_s, номер), не больше EXPLAIN_MAX
    idx: i8[k]; h: f8[k]; dist: f8[k] (евклидово); octant: i1[k]; z: f8[k] (t у урезанной); reason: u1[k] (индекс REASONS)
    total: int; n: int; nh: int; noct: int; nhy: int; ok: bool; radius: float   # total — до обрезки; radius — r1·factor прохода
    names() -> list[str]
class Searcher(s: RowSamples, ell: Ellipsoid, passes: Sequence[Pass], soft_dist=0.0, *, k_c=None):   # одно дерево на строку (D ∪ S)
    n_passes -> int; radius(k) -> float; fp_of(k) -> f8[4]
    gather(centres f8[m, 3], k, *, todo=None, exclude=None) -> Neighbours   # проход k (с 0); exclude ("sample", i8[m]) | ("hole", i4[m]) — xval; k_c = max(4·n_max, 64), повтор ×4
    search(centres, *, start=0, todo=None, exclude=None) -> Found           # первый проход от start, который выполнен
    explain(centre f8[3], k, *, pool=None, limit=EXPLAIN_MAX) -> Candidates # инспектор (Д-30); выбор — как у gather, без исключения xval

# E/indicator.py (18.9)
indicators(z, thresholds) -> f8[n, K]                       # [z ≤ t_k]; NaN остаётся NaN
order_relations(f f8[K]) -> None                            # на месте (njit): обрезка [0; 1], поправка порядка GSLIB
class_means(z, w, thresholds) -> f8[K + 1]                  # декл. средние классов; пустой класс — середина соседних порогов, крайний — его порог
proportions(z, w, thresholds) -> f8[K]                      # декл. доли [z ≤ t_k] (среднее индикаторов; SK, 18.33); w None — простые
etype(f f8[K], means f8[K + 1]) -> float                    # Σ (F_k − F_(k−1))·m_k (njit)
@dataclass class IkSetup:                                   # thresholds f8[K]; covs — K моделей («per») или одна («median»); bv — C̄_VV каждой; offs f8[N, 3]; means f8[K + 1]
    median -> bool; k -> int
    of(thresholds, covs, means, *, size=None, disc=(1, 1, 1), phi=0.0, block_cov=None) -> IkSetup   # size None — точка (C̄_VV = C(0))
@dataclass class IkChunk:                                   # p f8[m, K] (после поправки порядка), etype f8[m], status i1[m] (0 / 2 / 3), raw f8[m, K] (F* до поправки)
ik_chunk(nb: Neighbours, local, centres, setup: IkSetup) -> IkChunk   # IK по выборке одного прохода
@dataclass class IkRun:                                     # p, etype, status, pass_ i1[m] (1…P; 0 — никто), nb — выборка прохода, давшего оценку (NS, NH, NHY, NOCT, AD, DMIN)
ik_passes(searcher, local, centres, setup, *, start=0, exclude=None) -> IkRun   # блок — первым выполненным проходом, без вырожденной системы (Д-27)
warm_indicator() -> None

# E/kernel.py (18.7) — порция → проходы → метод
@dataclass(frozen=True)
class RowKernel:                                            # скомпилированная строка плана
    spec: EstimationSpec; samples: RowSamples
    cov: CovModel | None                                    # SK, OK; None — NN, IDW; IK — модели в extra
    ell: Ellipsoid; searcher: Searcher; blockcov: BlockCov | None
    disc: tuple[int, int, int]; phi: float                  # дискретизация; поворот модели, °
    size: tuple[float, float, float] | None                 # размер блоков, если все одинаковы (sub: parent — родителя)
    mean: float                                             # SK: m
    offsets: f8[N, 3]; cvv: float                           # подъячейки размера блока; C̄_VV (точка — C(0))
    local: f8[n, 3]                                         # пробы в локальных координатах (как в samples.local)
    class_means: f8[K + 1] | None; extra: dict              # IK
compile_row(spec, samples, *, block_size=None, rotation_deg=0.0, blockcov=None, k_c=None) -> RowKernel   # blockcov — общий кеш C̄_VV моделя
@dataclass Portion: centres f8[m, 3]; sizes f8[m, 3] | None = None; active bool[m] | None = None; exclude = None
@dataclass ChunkResult:
    values: dict[str, f8[m]]                                # ключи outputs (+ STATUS); NaN — нет
    status: i1[m]; by_pass: i8[P]; singular: i8[P]         # по проходам: оценено / вырождено
    counts: i8[4]                                           # блоки по статусам
    neg_kv: int = 0                                         # блоки с KV < 0 (записано 0; 18.7)
estimate(rk, centres f8[m, 3], *, sizes=None, active=None, exclude=None, local=False) -> ChunkResult   # центры — координаты проекта; local=True — уже локальные
run(rk, chunks: Iterable[f8[m, 3] | Portion], *, threads=1, tick=None, local=False) -> Iterator[ChunkResult]   # порядок порций; threads порций сразу
# E/kernel_block.py (18.7; kernel.estimate_block — ленивый экспорт)
estimate_block(rk, centre f8[3], size=None, *, pool=None, active=True, local=False) -> BlockDetail   # один блок: как estimate, + кандидаты и веса (preview, inspect)
@dataclass BlockDetail:
    centre: f8[3]; size: tuple | None; status: int; pass_: int   # pass_ 1…P; 0 — не оценён
    values: dict[str, float]; mu: float                     # mu — LAGR (OK); иначе NaN
    sel: i8[n]; lam: f8[n]                                  # выбранные пробы; веса (IDW — нормированы, NN — 1, SK/OK — λ)
    candidates: Candidates; cand_lam: f8[k]                 # прохода, давшего оценку (иначе последнего)
    ellipsoid: dict                                         # {centre, centre_xyz, ang1–3, r1–3 (× factor), pass}
    passes: list[dict]                                      # [{pass, ok, n, reason: None | "few" | "singular"}]
    counts: dict                                            # {candidates, selected}

# E/xval.py (18.14)
CHUNK = 4096                                                # цели в порции (отмена — между порциями)
cross_validate(spec, samples, by="hole", *, threads=None, chunk=None, tick=None, k_c=None) -> XvalTable   # by: hole | sample; цель — положение пробы, точечно
XvalTable: n -> int; error -> f8; z_error -> f8 (KV ≤ 0 → NaN); summary() -> dict (сводка §8.1, с by_status)
summary(true, est, kv=None) -> dict                         # n, mean_error, std_error, mae, r, slope, mean_ratio, n_ratio, n_kv_zero, n_skipped
histogram(error, bins=40) -> {edges, counts}
to_parquet(t: XvalTable, meta: dict) -> bytes               # метаданные — ключ bf.xval
read_page(conn, store, estimation_id, page=1, size=500) -> dict   # GET /estimations/{id}/xval (§8.1); 404 xval_not_found; 422 bad_param {page | size}
run_xval(engine, folder, params, tick=_noop, threads=None) -> dict   # задание estimate.xval: {estimation_id, by?} → {estimation_id, by, n, summary, events}

# E/contact.py (Д-28, 18.Д1)
block_distance(model, *, field, a, b, tick=None) -> f8[rows]   # знаковое расстояние центра блока до контакта доменов a (< 0) и b (> 0), м; NaN — нет контакта; EDT по сетке родителей
blocks_profile(model, *, field, a=None, b=None, domain_field=None, wireframe=None, status_field=None, distance=None, step=None, threads=1, tick=None) -> dict
    # ровно один из: (domain_field, a, b) или wireframe; status_field — поле статуса (0 — оценён); distance, step — D и b, м (None — 20 и 1)
# geo/sdf.py (Д-28)
signed_distance(vertices f8[v, 3], triangles i4[t, 3], points f8[n, 3], *, reach=inf, check=True, threads=1, tick=None) -> f8[n]
    # внутри < 0 < снаружи, м; NaN — нет координат; ±inf — нет треугольника в reach; SdfError wireframe_empty / wireframe_open (при check)
signed_distance_to(target: MeshTarget, points, *, reach, threads, tick) -> f8[n]

# E/store.py (18.11) — строки; store_base.py — KIND, PLAN, ALGO, params_hash(spec) (SHA-256 без имени)
create(conn, store, project, spec, author=None, *, prepared=None) -> {id, name, version: 1}
update(conn, store, project, estimation_id, raw, expected_version, *, prepared=None) -> {id, name, version}
delete(conn, estimation_id) -> {deleted, plans}; get(conn, estimation_id) -> форма §4; list_rows(conn, model_id=None)
# E/store_check.py — freshness(conn, meta) -> {stale, stale_reason, source_lost}; inputs_of(conn, spec, samples=None) -> list; prepare(conn, project, spec, *, old=None, old_origin=None) -> Prepared
# E/store_plan.py — plan_create(conn, spec, author=None); plan_update(conn, plan_id, raw); plan_delete(conn, plan_id) -> {deleted}; plan_get(conn, plan_id); plan_list(conn, model_id=None); check_plan(conn, items, model_id=None, param="items") -> list[EstimationSpec]

# lineage/store.py, graph.py (ADR 0025; 18.Г1–Г2, Г1 — вне ADR, см. §14)
Node(kind, id, sub="")                                      # sub — имя поля bm_field (casefold); key() → (id, sub)
InputRef(node, fp, name)                                    # вход: источник, отпечаток при построении (None — не сравнивать), имя
graph.status(conn, nodes, *, store=None) -> dict[Node, NodeStatus]   # NodeStatus(node, state, name, reason, changed)
graph.impact(conn, node) -> dict                            # «Влияние изменения» (§7)
graph.origin(conn, node, *, store=None) -> list[dict]       # «Происхождение» (§10): входы, их отпечатки и состояния
graph.pin(conn, node, pinned) -> NodeStatus                 # «Закрепить» / «Снять»; 404 lineage_node_not_found
graph.record(conn, dst, inputs) -> list[InputRef]           # входы результата — в транзакции записи; закрепление сохраняется
graph.fingerprint(conn, node) -> str | None                 # None — узла нет

# E/job.py, job_plan.py, job_run.py (18.12) — задание `estimate.run`; сверено с кодом
KIND = "estimate.run"; TITLE = "Оценка содержаний"; queue "heavy"; estimate_memory = memory_mb
run_estimate(engine, folder, params, tick=_noop, *, threads=None, job_id=None, memory_mb=None, chunk=CHUNK_ROWS, portion=None) -> dict   # форма итога §6
memory_mb(params) -> int              # что ждёт планировщик (ADR 0002 §4): пробы строк (`samples` маршрута, умолч. 300 000), куски потоков, чанк модели
limits(plan, threads, budget_mb) -> (threads, блоков в куске)          # бюджет памяти урезает потоки и кусок
job_plan.build(conn, store, run: RunSpec, model_view=None) -> Plan     # 404 / 409 / 422 до первого блока; Plan.rows (RowRun), .groups (Group), .names
job_plan.PORTION = 1 << 16; job_plan.CHUNK_ROWS = 1 << 18             # блоков в куске; строк модели за чтение
job_run.pieces(env, tick, chunk) -> Iterator[Piece]; job_run.ordered(items, fn, threads, tick)   # куски по порядку файла, потоки над кусками
rebuild_row(svc, cap, node, body) -> None; register_rebuild() -> None  # изготовитель узла `object:estimation` для `lineage.rebuild`

# E/kna.py, kna_blocks.py (18.15) — задание `estimate.kna`; сверено с кодом
KIND = "estimate.kna"; queue "quick"; VARY = ("n_max", "radius", "disc", "size"); BLOCKS_MAX = 10; SHARE = 0.95; QUANTILES = ((0.9, "много"), (0.5, "средне"), (0.1, "мало"))
analyse(project, spec, blocks=None, vary=None, *, origin=None, tick=_noop) -> dict    # ответ §8.2 без `estimation_id`, `name`
analyse_blocks(rk, blocks: Sequence[BlockAt], vary=VARY, *, tick=_noop) -> {blocks, recommend}   # строка скомпилирована БЕЗ `block_size`: размер идёт с блоком
auto_blocks(rk, targets: Targets) -> list[BlockAt]                    # «много», «средне», «мало»; 422 `kna_no_blocks`
recommend(rows) -> int | None         # наименьший value с SLOPE ≥ 0,95·max среди STATUS = 0; None — нет или max ≤ 0
variants(rk, name) -> [(value, rk', множитель размера)]; run_block(rk, b, scale=1.0) -> показатели; count_in(rk, centres, radius) -> i8[m]   # проб D в шаре радиуса
run_kna(engine, folder, params, tick=_noop) -> {estimation_id, name, …analyse}   # тело задания
kna_blocks.Blocks(…).targets(limit=TARGET_LIMIT = 200_000) -> Targets(row, p, ijk, centre, size, total); BlockAt.at() -> {row, p, ijk}

# E/checks.py (18.16) — задание `estimate.global`; Q-Q, swath — синхронно; сверено с кодом
KIND = "estimate.global"; queue "quick"; LIMIT_PCT = 5.0; PORTION = 1 << 16; POINT_DISC = (4, 4, 4)
global_check(project, spec, *, origin=None, tick=_noop, threads=None) -> dict        # ответ §8.3 без `estimation_id`, `name`
qq_check(project, spec, *, origin=None, quantiles=None, weighted=True, tick=_noop) -> dict   # §8.4
swath_settings(spec, direction="z") -> {direction, series}                          # §8.4
run_global(engine, folder, params, tick=_noop, threads=None); run_qq(engine, folder, params, tick=_noop)   # тела; Q-Q — не задание
expected_variance(spec, grid_rotation, size) -> (C̄_VV | None, C(0) | None, disc | None)

# E/xval.py (18.14) — см. выше (`run_xval`, `read_page`); задание `estimate.xval`, queue "heavy"

# E/preview.py, defaults.py (18.13) — синхронно, слот HEAVY
preview.preview(project, body) -> dict      # §7; light(conn, spec) — строка без тяжёлых проверок; row_of(conn, id) -> (spec, origin); forget() — кеш скомпилированных строк
defaults.build(project, q) -> dict           # §5; model_part(conn, store, model_id) -> (описание модели, Model)

# E/inspect.py, inspect_spot.py, routes_inspect.py (18.Д3) — `POST /estimate/inspect`, слот HEAVY, обгон по `channel`/`seq`
inspect.run(project, req: InspectSpec, tok=None) -> dict    # §7; WARNING = "Параметры или входы изменились"; REASON_LABELS (код → подпись); CACHE_KEEP = 4 скомпилированных строки на процесс
inspect.forget(); inspect_spot.forget()                     # сброс кешей (смена проекта)
inspect_spot.locate(model, at) -> Spot; domain_of(model, spec, restrictor, spot, fields=()) -> Domain; read_fields(model, names, a, b); read_cells(model, a, b)

# E/warm.py (18.17) — шаг `estimate` в `ops/warm.py` (OTHER, QUICK, HEAVY)
PARTS = {"cov", "krige", "search", "methods", "indicator", "kernel"}; warm() -> {часть: секунды}   # часть, упавшая при прогреве, пропускается; read-only и записываемые массивы — обе сигнатуры
contact.warm(); geo.sdf.warm()      # шаг `stats` в `ops/warm.py`: профиль блоков у контакта (Д-28)

# Маршруты (подключает `geostat/router.py`; префикс `/api/v1/geostat`)
routes.py        — /estimations, /estimations/{id}, /estimation-plans, /estimation-plans/{id}, GET /estimate/defaults, POST /estimate/run, POST /estimate/preview
routes_check.py  — POST /estimate/xval, GET /estimations/{id}/xval, POST /estimate/kna, POST /estimate/global, POST /estimate/qq, POST /estimate/swath
routes_inspect.py — POST /estimate/inspect
routes_common.py — body_of, common_of (channel, seq), sync, plain (NaN, ±inf → null), publish (`geostat.changed`), registered (422 `not_ready` до подключения задания)
# /api/v1/lineage (`lineage/router.py`, ADR 0025): POST /status, GET и POST /impact, GET /origin, POST /pin, POST /rebuild {node | kind+id+sub, with_dependents, drop_lost, dry_run}
# jobs/registry.py: bf_core.lineage.job, …estimate.checks, …estimate.job, …estimate.kna, …estimate.xval
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
| `estimate.open {method?, id?, model_id?, soft_a?, soft_b?, soft_distance?}` (плоские) | uiOnly | мастер (шаги: модель, ограничитель, поля → пробы → метод и вариограмма → поиск и проходы → выходные поля → задание); `defaults`, `preview`; `id` — правка строки; `soft_*` — мягкая граница из контакта (`soft_distance` 0 — жёсткая) |
| `estimate.save {id?, as_new?}` | — | сохраняет форму открытого мастера или окна «Параметры оценки»: `POST /estimations` (новая или `as_new`) / `PUT /estimations/{id}` (версия — из формы; 409 → «Перечитать» / «Сохранить как новую»); `id` — проверка, что форма правит эту строку (18.27в; нет в исходном плане команд — «команда = запись реестра») |
| `estimate.plan_save {id?, model_id?, name?, items?, as_new?}` | — | `POST /estimation-plans` (без `id` или `as_new`) / `PUT /estimation-plans/{id}`; `items` — номера строк через «;» в порядке выполнения (18.27в) |
| `estimate.params {id?}` | uiOnly | окно «Параметры оценки»: строка на одной странице, те же поля, что в мастере; `GET /estimations/{id}`, сохранение — `estimate.save` (18.20) |
| `estimate.plan {plan_id?, model_id?}` | uiOnly | окно «План оценки»: `/estimation-plans`, `/estimations` |
| `estimate.plan_delete {id}` | confirm | `DELETE /estimation-plans/{id}` — план уходит, строки и поля модели остаются (18.22; нет в исходном плане команд — «команда = запись реестра») |
| `estimate.ellipsoid {id?, show?, pass?, block?}` | — | слой эллипсоида поиска: без `id` — эллипсоид мастера (`preview` → `ellipsoid`, радиусы и углы шага 4, ×`factor` прохода); с `id` — строка и блок `block` («i; j; k») или блок, по которому щёлкнули; `show: false` убирает слои |
| `estimate.run {plan_id \| estimation_ids, replace?}` | confirm (при замене полей) | `POST /estimate/run`; без аргументов — окно «План оценки» |
| `estimate.delete {id}` | confirm | `DELETE /estimations/{id}` |
| `estimate.inspect {model_id?, block \| xyz \| row, params_id?}` | — | `POST /estimate/inspect`; окно «Блок · i, j, k»; без `model_id` — выбранная в дереве или единственная модель с выполненной оценкой, без блока — окно ждёт щелчка по блоку в 3D (18.27в) |
| `estimate.xval {estimation_id, by?}` | — | без `by` — окно с прежним результатом строки; с `by` — `POST /estimate/xval` → `GET /estimations/{id}/xval`, итог — сводка |
| `estimate.xval_3d {estimation_id?, show?}` | — | пробы последней перекрёстной проверки в 3D, цвет — ошибка (оценка − истинное); берёт таблицу `GET /estimations/{id}/xval`; `show: false` убирает точки (18.21, долг 18.23) |
| `estimate.kna {estimation_id?, vary?, block?, xyz?}`, `estimate.global {estimation_id?}`, `estimate.qq {estimation_id?}`, `estimate.swath {estimation_id?, direction?}` | — | §8.2–8.4; без строки открывается окно; `vary` — через «;», `block` — «i; j; k», несколько — через «\|» |
| `chart.contact` + `blocks` | — | `POST /stats/contact` (§8.5) |
| `lineage.status {kind?, id?, sub?}`, `lineage.impact {…}`, `lineage.pin {…, pinned?}` | —, —, — | `POST /lineage/status`, `GET /lineage/impact`, `POST /lineage/pin` (ADR 0025 §10; группа «Настройки проекта › Зависимости»; без объекта — выбранный в дереве); `lineage.impact` открывает окно «Влияние изменения», если зависимые есть |
| `lineage.rebuild {kind?, id?, sub?, with_dependents?, drop_lost?, confirmed?}` | confirm | `POST /lineage/rebuild`; окно «Влияние изменения» показывает план (`dry_run`) и само запускает перестроение, передавая `confirmed: true` («план уже показан и подтверждён» — без повторного вопроса о плане); для ассистента план подтверждается `confirm` реестра |
| `simulate.sgs {…, secondary?}`, `simulate.sis {…}`, `simulate.post {id, …}` | —, —, confirm | §9 (Э18б; в коде ещё нет) |

Без параметров команда открывает окно; «только просмотр» — 423 у пишущих.

## 13. Фикстуры

- `core/tests/data/estimate/params.json` — 5 строк (`idw`, `ok_point`, `sk`, `ik`, `ok_block_passes`): `body` →
  `canonical` (`spec.parse_estimation(body).canonical()`) и `columns`; круговой проход — `test_estimate_spec.py`;
  `BF_REGEN_ESTIMATE=1` переписывает `canonical` и `columns`.
- `core/tests/data/estimate/contract_answers.json` — **настоящие ответы** маршрутов Э18 на синтетическом проекте
  `core/tests/estapi_world.py` (25 скважин, блочная модель из трёх доменов, модель вариограммы), `{status, body}`;
  пишет `core/tests/test_estimate_api_answers.py` (`BF_REGEN_ESTIMATE=1`), без флага тест сверяет ответы ядра с
  файлом (числа 1e−7; времена, отпечатки, `versions`, `stages`, `token` не сравниваются). Ключи: `defaults`,
  `estimations`, `estimation`, `estimation_post`, `estimation_put_conflict`, `bad_param`, `variogram_scale`, `plan`,
  `run_submit`, `run_field_exists`, `run_result`, `preview`, `xval_submit`, `xval_result`, `xval`, `kna`, `global`,
  `qq`, `swath`. Ключи `simulation`, `simulate_result`, `simulate_post_result` — образцы контракта Э18б (до 18.32),
  `inspect` — старый образец (настоящие ответы — `inspect_answers.json`). Интерфейс принимается только на настоящих
  ответах (`ui/test/estimation/harness.ts` читает файл).
- `core/tests/data/estimate/inspect_answers.json` — настоящие ответы инспектора блока (`answers`: `pass_1`, `pass_3`,
  `few_samples`, `out_of_domain`, `changed`; `errors`: `no_block`, `no_model`, `no_params`, `no_such_row`; `rows`);
  пишет `core/tests/test_estimate_inspect_route.py` (`BF_REGEN_INSPECT=1`).
- `core/tests/data/lineage/answers.json` (`BF_REGEN_LINEAGE=1`, `test_lineage_window_answers.py`) — ответы
  `/lineage/*` (ADR 0025): цепочка заглушек и настоящие результаты; `core/tests/data/stats/contact_blocks_answers.json`
  (`BF_REGEN_CONTACT_BLOCKS=1`, `test_stats_contact_blocks.py`) — профиль блоков у контакта (§8.5).

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
