# Э20 — контракт подсчёта ресурсов (по коду на 20.19)

**Состояние: приведён к коду (подшаг 20.19).** Источник истины — код `core/bf_core/resources/*`, `core/bf_core/blocks/compare*.py`,
`routes_compare.py` и настоящие ответы ядра `core/tests/data/resources/contract_answers.json`; решения — ADR 0027 (1–30, «API»,
«Точность и приёмка», вопросы № 105–112), ТЗ 8.10, 8.0, 7.35 Д-36, Д-37, Д-35 (граф), Д-27 (контроль); граф — ADR 0025. Образцы
параметров — `core/tests/data/resources/params.json`. Настоящие ответы записывает
`BF_REGEN_RESOURCES=1 uv run pytest -q tests/test_resources_contract.py`; **исключения — образцы форм, а не ответы ядра:**
`export_post`, `export_result`, `files_exist` (настоящий ответ выгрузки — в `test_resources_export.py`). Изменение формы — только через оркестратора.
Префикс маршрутов — `/api/v1/resources` (роутер `resources/router.py`: `routes.py` + `routes_extra.py`), сравнение оценок —
`POST /api/v1/blocks/{id}/compare` (`blocks/routes_compare.py`).

Пути: `R/` = `core/bf_core/resources/`, `B/` = `core/bf_core/blocks/`, `U/` = `ui/src/modules/resources/`.

## 1. Общие правила

- `X-BF-Token`, Host/Origin — как у всего ядра. Координаты, суммы, значения — float64; пусто — `null` в JSON
  (NaN в массивах), **никогда не 0** (8.0.1). Число на входе — число или текст «0,5» / «0.5». Нет ключа / `null` / `""` —
  умолчание.
- Ошибка — `{code, message, hint, param?, …}` по-русски с подсказкой (§11). `param` — путь внутри `params`: `"cutoffs.2"`,
  `"cutoffs.step"`, `"elements.1.unit"`, `"groups.1"`, `"bench.h"`, `"balance.off_cutoff"`, `"restrict.items.0.cond.op"`;
  на уровне запроса — `"params"`, `"name"`, `"save"`, `"expected_version"`, `"path"`. Нет проекта — 409 `no_project`.
- **Режим «только просмотр»** (проект открыт в другом окне, замок у него; ревью 20.18б). 423 `project_read_only` — у `POST /reports`
  с `save: true`, `PUT`/`DELETE /reports/{id}`, `PUT`/`DELETE /templates/{id}`, `POST /blocks/{id}/compare` (пишет поле Δ), а также у
  **предпросмотра и экспорта**: такой проект не подключён к планировщику заданий, расчёт — задание (допущение 20.18б: синхронный
  предпросмотр поменял бы ответ `{source, id}`; расхождение с ТЗ «предпросмотр работает в просмотре»). Работают: чтение отчётов
  и шаблонов, `drill`, `drill/rows`, `POST /compare` (отчёты). Экспорт в проект открытом для записи пишет вне проекта и запись о файле
  (`record: true`); при `record: false` в проект не пишется (`recorded: false`).
- Задание: ответ `{source, id}` (`source` — `"project"` или `"jobs"`, как вернул планировщик); ход — `GET /jobs/{source}/{id}`;
  синхронные проверки (404, 409, 422) — до постановки. Итог несёт `events: [[имя, данные]]`. Запись — последней стадией, целиком или
  ничего (объект, blobs, `lineage.record` — одна транзакция); отмена ничего не оставляет.
- Оптимистичная блокировка отчёта — `expected_version` (целое ≥ 1, иначе 422 `bad_param {param: "expected_version"}`; другая версия —
  409 `version_conflict {version}`); шаблона — тоже `expected_version` (у нового шаблона не нужен). Отчёт в состоянии `building` —
  409 `report_building` у пишущих запросов (переименование, удаление, экспорт, переход).
- События: `resources.changed {kind: "resource_report", id, deleted?}` — после записи, перестроения, переименования и удаления;
  `resources.changed {kind: "template", id, deleted?}` — шаблон; `estimate.compare` — `objects.changed {kind: "block_model", id}`
  (в итоге задания; интерфейс обновляет поля модели).
- Побитность (8.0.2): результат не зависит от размера порций, порядка блоков внутри порции и числа потоков;
  «Перестроить» (тот же `params`) = расчёт с нуля, те же байты `data`. ALGO = `resources-1` (`spec.ALGO`); сравнение отчётов —
  `resources-compare-1`, сравнение оценок — `compare-1`.

## 2. Параметры отчёта — `spec.parse_params`, `POST /resources/reports {params, name?, save}`

```json
{"model_id": 12,
 "elements": [{"field": "AU", "unit": "г/т"}, {"field": "CU", "unit": "%"}] | ["AU", "CU"] | null,
 "cutoff_field": "AU" | null,
 "cutoffs": [null, 0.5, 1.0] | {"from": 0.5, "to": 3, "step": 0.5} | null,
 "balance": {"cutoff": 1.0, "off_cutoff": 0.5} | null,
 "density": "SG" | 2.7 | null,
 "filter_id": 5 | null, "restrict": {правила 8.7.5} | null,
 "groups": ["CLASS", {"kind": "field", "field": "DOMAIN"}, {"kind": "bench"}],
 "bench": {"h": 10, "z0": null, "z_min": null, "z_max": null},
 "curve": 100, "template_id": "jorc" | null}
```

Тело запроса: `params` — обязательно; `name` — имя объекта (≤ 200 знаков; нет — «Отчёт по ресурсам»); `save` — по умолчанию **false**
(предпросмотр: ничего не пишется); true — объект `resource_report`. Занятое имя при создании — суффикс « (2)» (при переименовании —
409 `name_taken`).

| Ключ | Правило, умолчание, диапазон |
|---|---|
| `model_id` | обязателен, номер блочной модели |
| `elements` | ≤ 20; строка (поле) или `{field, unit?}`; нет — поля `*_CUT`, иначе роль `grade` (как 7.31); пустой список — 422; повтор поля — 422; единица `unit` — текст ≤ 20 знаков, нет — единица поля. Единицы приводятся к канону: `g/t`, `gpt`, `г/т` → `"g/t"`; `ppm`, `%`, `ppb` как есть; другая — как задана (металла нет, предупреждение `unit_unknown`) |
| `cutoff_field` | g_б (число); нет — первый элемент; может не входить в `elements` |
| `cutoffs` | список ≤ 200 (элемент — число или `null` = «без борта»: все блоки группы, и без g_б, как BF `cutoff=None`) или диапазон `{from, to, step}` (step > 0, to ≥ from, ≤ 200 значений, шум суммы убирается: 0,1 + 2·0,1 → 0,3); повторы убираются, порядок — как задан (диапазон — по возрастанию); нет — `[null]`; модуль борта ≤ 10¹²; в каноне — раскрытый список |
| `balance` | `{cutoff: c_б, off_cutoff?: c_з}`; c_з < c_б (иначе 422 `bad_param {param: "balance.off_cutoff"}`); строки `balance` (g_б ≥ c_б) и `offbalance` (c_з ≤ g_б < c_б; нет c_з — нет строк) |
| `density` | число > 0, поле или нет: меняющееся поле SG, иначе меняющееся поле с ролью `density` (постоянное пропускается), иначе 422 `bad_param {density}` |
| `filter_id`, `restrict` | ограничитель: фильтр библиотеки И быстрое дерево правил (`filters.normalize_rules`; И = произведение долей, решение 8); «Карьер» и «Ниже рельефа» окна — листья этого дерева («Карьер» — `{expr: "PIT <= n"}` или `{wireframe: {id, side: "inside", by}}`; «Ниже рельефа» — `{surface: {id, side: "below", by}}`; `by` — `frac` или `centre`) |
| `groups` | ≤ 3 ключа: строка = категорийное поле; `{kind: "field", field}`; `{kind: "bench"}` (один). Повтор — 422. Числовое поле — 422 `group_numeric {param: "groups.i"}` (№ 112); нет поля — 404 `block_field_not_found`. Подписи — по ключам, порядок ключей = столбцы `g1…g3`. Пустых сочетаний в выводе нет |
| `bench` | только при ключе `bench`; `h` — высота, м, [0,000001; 1000], по умолчанию 10; `z0` — отметка, нет — кратная H ниже низа диапазона; `z_min`, `z_max` — диапазон (нет — охват модели по Z, `z_min` < `z_max`); берутся уступы сетки `[Z0 + j·H; Z0 + (j + 1)·H]`, касающиеся диапазона, ≤ 1000 (422 `bench.h`); часть блока вне уступов — «(пусто)» (№ 107). Подпись «+250…+260», «-10…0», «+12,5…+22,5» (`spec.bench_label`) |
| `curve` | 0 (без кривой) или 2…500; по умолчанию 100; 1 — 422 |
| `template_id` | код шаблона (`[A-Za-z0-9_.-]`, ≤ 64) или `null` (нет формы); **только показ** — не входит в `params_hash` и вход графа (решение 17); неизвестный — 404 `template_not_found` до задания |

`spec.parse_params` проверяет формы и диапазоны; поля — по модели: `spec.resolved(model.fields, (z_lo, z_hi))` подставляет
умолчания, приводит имена к написанию модели, берёт единицу поля, считает уступы и подписи ключей. Лимиты данных:
сочетаний групп ≤ 20 000 (произведение «категорий + 1» по ключам), ячеек ≤ 2·10⁶, иначе 422 `report_too_big`; отчётов в проекте — ≤ 1000
(422 `too_many_reports`, при записи). `resolved().canonical()` — `meta.params` и вход `params_hash()` (SHA-256 канонического JSON без
`template_id`, + ALGO).

`GET /resources/defaults` → `{params, ranges}` (`spec.defaults_json()`, `ranges_json()`): `params` — умолчания окна (`elements`/`cutoff_field`/
`density`/`filter_id`/`restrict` — `null`, `cutoffs` — `[null]`, `groups` — `[]`, `bench {h: 10, z0, z_min, z_max: null}`, `curve` 100,
`template_id` null, `save` false) и **`choices`** (`key_kinds`, `row_kinds`, `export_formats`) и **`limits`** (`elements` 20, `cutoffs` 200,
`group_keys` 3, `benches` 1000, `combinations` 20000, `cells` 2000000, `curve` 500, `page` 1000); `ranges` — `bench.h`, `bench.z`, `cutoff`, `curve`
(`{lo, hi, lo_open, integer, unit}`).

**Код группы (решение 4)**: смешанное основание, первый ключ — старшая цифра; цифра ключа = код категории + 1 (в блочной модели
−1 — пусто) или номер уступа − j0 + 1; **0 = «(пусто)»**. `spec.radices()` = (len(labels) + 1 по ключам),
`group_count()`, `decode(code)`, `group_labels(code)` (подписи, 0 → «(пусто)»). Группа без ключей — код 0, одна группа.

Единицы и металл (решение 10), `spec.metal_family(unit)`, `spec.metal_k(unit)`: M_e = Σ t·g_e·k_e хранится в базовой единице
семейства — `g` (граммы: г/т, ppm — k = 1; ppb — k = 10⁻³) или `t` (тонны: % — k = 0,01); неизвестная единица — металла нет
(`null`). Показ — единица шаблона (§4); 1 тыс. унц = 31 103,4768 г (`spec.TROY_OUNCE_G`, `templates.TROY_OUNCE_G`).

## 3. Результат — итог задания `resources.report` (`save: false` — тот же ответ без `report_id`)

Настоящий ответ предпросмотра (`contract_answers.json` → `preview_result`):

```json
{"report_id": 17 | null, "name": "Отчёт по ресурсам", "version": 1 (предпросмотр — 0), "saved": true, "algo": "resources-1",
 "params": {resolved canonical}, "params_hash": "sha256…",
 "model": {"id": 12, "name": "Рудное тело", "version": 4, "cells_sha": "…", "grid": {"corner": [..], "counts": [..], "size": [..], "order": "F", "rotation": 0.0, "div": [1, 1, 1]}},
 "elements": [{"field": "AU", "unit": "g/t", "unit_label": "г/т", "metal_family": "g", "metal_base": "г"}],
 "cutoff_field": "AU", "density": {"field": "SG", "value": null},
 "keys": [{"kind": "field", "field": "CLASS", "labels": ["Измеренные", "Выявленные", "Предполагаемые"]}],
 "bench": null | {"h": 10, "z0": -20, "z_min": -12, "z_max": 33, "j0": 0, "n": 6},
 "rows": [Row…], "rows_total": 7, "truncated": false,
 "totals": {Row-итог первого борта},
 "curve": Curve | null, "control": Control, "excluded": {"filtered": 0, "no_volume": 0, "no_density": 0},
 "form": Form | null, "warnings": [{"code", "message", "hint?", "param?"}], "files": [],
 "seconds": {"read": 0.03, "kernel": 2.34, "control": 0.0005, "total": 2.37},
 "events": [["resources.changed", {"kind": "resource_report", "id": 17}]]  // только у записанного}
```

**Row** (Parquet `data`: столбцы `g1…g3`, `total`, `kind`, `cutoff`, `cutoff_to`, `blocks`, `volume`, `tonnes`, `<e>:wt`,
`<e>:sum`, `<e>:metal`, `<e>:grade`, `<e>:missing_t`, `<e>:missing_n`):

```json
{"g": ["Измеренные"], "total": false, "kind": "cutoff", "cutoff": 1.0, "cutoff_to": null,
 "blocks": 1, "volume": 1000.0, "tonnes": 2500.0,
 "elements": {"AU": {"wt": 2500.0, "sum": 2500.0, "grade": 1.0, "metal": 2500.0, "missing_t": 0.0, "missing_n": 0}}}
```

- `kind`: `cutoff` (g_б ≥ `cutoff`), `all` («без борта», `cutoff` null), `balance` (g_б ≥ c_б, `cutoff` = c_б),
  `offbalance` (c_з ≤ g_б < c_б: `cutoff` = c_з, `cutoff_to` = c_б). `total: true` — итог (отдельные аккумуляторы, не сумма
  групп; `g` — `[null, …]` по числу ключей; без ключей `g: []` и итоговые строки — единственные). Порядок: по `kind`/борту запроса
  (сначала `all`, борта как заданы, `balance`, `offbalance`), внутри — итог, затем группы по возрастанию кода. Группа выводится, если в ней
  учтён хоть один блок (пустая ячейка прямоугольной таблицы: T = 0, ḡ `null`).
- `blocks` — число блоков с f > 0, объёмом и плотностью в строке; `volume` = Σ V·FRAC·f, `tonnes` = Σ V·FRAC·f·ρ;
  по элементу: `wt` = Σ тоннажа блоков, у которых есть g_e (знаменатель), `sum` = Σ t·g_e, `grade` = sum / wt (пусто, если wt = 0),
  `metal` = sum·k_e в базовой единице (пусто — единица неизвестна или wt = 0), `missing_t`/`missing_n` — тонны и число блоков без g_e
  (не 0, не в среднее и металл).
- `excluded` (каждый блок один раз, в этом порядке): `filtered` (f = 0), `no_volume` (пустой FRAC), `no_density` (плотность пуста
  или ≤ 0; в «Контроль»). Блок без g_б входит в строки `all`, но не в строки борта (в том числе борта ≤ 0, как Э17); блок на двух уступах
  считается в обоих (в итоге — один раз); кусок блока короче 1e−12 высоты отбрасывается.
- `rows` в ответе: сохранённый отчёт — первая страница (500) + `rows_total`, остальное — `GET /resources/reports/{id}?page=&size=`;
  предпросмотр — до 20 000 строк (`truncated: true` и предупреждение `rows_truncated`).
- `totals` (для дерева проекта и сравнения): итоговая строка первого борта `{kind, cutoff, blocks, volume, tonnes,
  elements: {e: {grade, metal}}}`.

**Curve** (`sidecar:curve`; решение 11): 100 бортов (2–500) от min до max g_б по статистике поля (как Э17), по итогу:

```json
{"field": "AU", "cutoffs": [0.2, …], "blocks": […], "volume": […], "tonnes": […],
 "elements": {"AU": {"grade": […], "metal": […]}}}
```
Борт c: S(c) = {g_б ≥ c} (корзины + суффиксные суммы Neumaier, ADR «Варианты»); `tonnes` не возрастает с бортом (Т8.10-3).

**Control** (`sidecar:control`; решение 13; ТД27-2) — настоящая форма:

```json
{"tolerance": 1e-12,
 "sum_groups": {"checked": true,
                "tonnes": {"sum": 12500.0, "total": 12500.0, "rel": 0.0},
                "volume": {…}, "metal": {"AU": {…}}, "ok": true},
 "no_density": {"blocks": 0, "volume": 0.0},
 "default_density": null | {"blocks": 0, "tonnes": 0.0},
 "no_grade": {"AU": {"blocks": 0, "tonnes": 0.0}},
 "status": {"AU": {"prefix": "AU_OK_", "items": [{"code": 1, "label": "вне домена", "blocks": 0, "volume": 0.0, "share": 0.0}, …]}},
 "wireframes": [{"wireframe_id": 7, "name": "Карьер", "volume_restrictor": 1.0e6, "volume_wireframe": 1.0e6,
                 "diff_pct": 0.0, "warning": false, "outside_extent": false}],
 "warnings": []}
```
`checked` — `false`, если сравнивать нечего. `rel` — |Σ групп − итог| / |итог| для T, V, M по борту каждой строки (худший); > 1e−12 —
предупреждение `sum_mismatch`. `default_density` — тоннаж блоков с `DENS_SRC` = 1 (есть поле — иначе `null`); `status` — только для
элементов из оценки (`source.op = estimate`, префикс `<префикс>STATUS`): коды 1, 2, 3 (`вне домена`, `мало проб`, `вырожденная
система`), `share` — доля объёма ограничителя; `wireframes` — по листу `wireframe` внутри ограничителя: Σ V·FRAC·f_листа против
`wireframes.stats.volume`, `diff_pct` > 1 % — предупреждение `wireframe_volume`, каркас за охватом модели — `outside_extent` (№ 110).
Контроль — один лишний проход по модели (только при `DENS_SRC` / `STATUS` / `wireframe`). В `GET /reports/{id}` `excluded` вынесен из
`control` на верхний уровень ответа.

**Warnings** (`code`): `unit_unknown {param}` (металла нет), `sum_mismatch`, `no_density` (есть блоки без плотности),
`wireframe_volume`, `template_other` (подписи схемы вне шаблона — в «Прочие»), `balance_missing` (форма ГКЗ без `balance`),
`rows_truncated`; у сравнения — `grids_differ`, `keys_differ`, `cutoff_field_differs`, `grade_units_differ`, `density_differs`, `stale` (§9).

## 4. Форма и шаблоны (решения 17–19; `R/templates.py`, `R/forms.py`)

**Template** (справочник `settings`, scope `resources`, key `report_templates`; `jorc` и `gkz` всегда есть, переписываются, **не
удаляются** (422); свои — создаются, переписываются, удаляются; шаблон — показ, смена не делает отчёт «устарел»). Ответ
`GET /templates/{id}` → `{template}` (настоящий, `contract_answers.json` → `template_get`):

```json
{"template": {"id": "jorc", "name": "JORC", "version": 1, "builtin": true,
  "labels": ["Измеренные", "Выявленные", "Предполагаемые"],
  "subtotals": [{"label": "Изм. + Выяв.", "members": ["Измеренные", "Выявленные"]}],
  "total": "Итого", "key": "class", "section": null,
  "columns": ["tonnes", "grade", "metal"], "balance": false,
  "units": {"tonnes": "kt", "volume": "km3", "metal": {"g": "koz", "t": "t"}},
  "decimals": {"tonnes": 0, "volume": 0, "grade": 2, "metal": 1}}}
```

- `labels` — подписи строк по порядку (группы ключа `key`; 1–64, без повторов без учёта регистра; «Прочие» зарезервировано);
  `subtotals` — до 16: `{label, members}` (≥ 2 подписей из `labels`), показывается **после последнего члена**; `total` — подпись
  итоговой строки («Итого») или `null` (без итога); `key` — роль ключа, к которому относятся `labels` (по умолчанию `class`;
  `[a-z][a-z0-9_]{0,19}`); `section` — роль внешнего ключа (например `domain`) — по разделу на подпись, `null` — один раздел
  (совпадать с `key` не может); `columns` — метрики по порядку из `tonnes`, `volume`, `grade`, `metal` (без повторов; по умолчанию
  `tonnes, grade, metal`); `balance` — **bool**: `true` — столбцы «Балансовые» / «Забалансовые» (строки `balance`, `offbalance`; нет
  `params.balance` — предупреждение `balance_missing`); `decimals` — знаков 0…6 для `tonnes`, `volume`, `grade`, `metal`.
- `units`: `tonnes` — `t` / `kt` / `mt` («т», «тыс. т», «млн т»); `volume` — `m3` / `km3` («м³», «тыс. м³»); `metal` — по основе
  элемента: `{g: g|kg|t|koz, t: g|kg|t|koz}` (или текст для обеих основ); `g` → граммы (г/т, ppm, ppb), `t` → тонны (%); `koz` — «тыс. унц».
- Умолчания (№ 105, 106): **JORC** — «Измеренные», «Выявленные», подытог «Изм. + Выяв.», «Предполагаемые», «Итого»; тыс. т (`kt`), знаков 0;
  металл `{g: koz, t: t}`, знаков 1. **ГКЗ** — A, B, C1, C2, подытог «A + B + C1», «Итого», `balance: true`; тыс. т, металл `{g: kg, t: t}`,
  знаков 0. Образец формы ГКЗ заказчика — вопрос № 105 (открыт).
- `PUT /templates/{id}` `{template: {name, labels, …}, expected_version?}` → `{id, version}` (новый шаблон — без `expected_version`;
  существующий, и `jorc`/`gkz`, — с текущей версией, иначе 409 `version_conflict {version}`); `DELETE /templates/{id}?expected_version=` →
  `{id, deleted: true}`. 422 `bad_param {param}` — `name`, `labels.i`, `subtotals.i…`, `total`, `columns`, `units…`, `decimals…`, `section`,
  `balance`, `id`.

**Form** (ответ `form` и `GET /resources/reports/{id}?template=&cutoff=`; считает только ядро — один источник чисел для таблицы окна и
XLSX, Т8.10-5). Настоящая форма (`preview_result.form`):

```json
{"template": {"id": "jorc", "name": "JORC", "version": 1},
 "elements": [{"name": "AU", "unit": "г/т", "unit_label": "г/т", "base": "g"}],
 "cutoff": {"kind": "cutoff", "value": 1.0}, "cutoffs": [1.0],
 "columns": [{"key": "tonnes", "part": null, "group": null, "title": "Руда, тыс. т", "metric": "tonnes", "element": null, "unit": "тыс. т", "decimals": 0},
             {"key": "grade:AU", "part": null, "group": null, "title": "AU, г/т", "metric": "grade", "element": "AU", "unit": "г/т", "decimals": 2},
             {"key": "metal:AU", "part": null, "group": null, "title": "Металл AU, тыс. унц", "metric": "metal", "element": "AU", "unit": "тыс. унц", "decimals": 1}],
 "sections": [{"title": null, "rows": [
   {"label": "Измеренные", "type": "group", "cells": {"tonnes": {"value": 2.5, "text": "3"},
                                                      "grade:AU": {"value": 1.0, "text": "1,00"},
                                                      "metal:AU": {"value": 0.08037686642156995, "text": "0,1"}}},
   {"label": "Изм. + Выяв.", "type": "subtotal", "members": ["Измеренные", "Выявленные"], "cells": {…}},
   {"label": "Итого", "type": "total", "cells": {…}}]}],
 "warnings": ["…"]}
```
`cutoff {kind, value}` — борт формы (`value` null — «без борта»; у шаблона с `balance` — по `balance`/`offbalance`, столбцы с `part` и
`group` «Балансовые» / «Забалансовые»); `cutoffs` — борта отчёта. Тип строки (`type`): `group`, `subtotal` (с `members`), `other` («Прочие» —
подписи схемы вне шаблона), `total`; при `section` — несколько разделов (`title` — подпись; последним — раздел «Все разделы» с итогом).
`warnings` формы — тексты (не объекты); те же предупреждения попадают в `warnings` результата с кодом. Ячейка `{value, text}`: `value` —
число в единице шаблона **без округления** (`null` — нет значения), `text` — показ: знаки шаблона, округление «от нуля» по десятичной записи
(как показывает Excel), десятичная запятая, неразрывный пробел между тысячами; пусто — пустая строка (интерфейс показывает «—»).
Интерфейс показывает `text` как есть; XLSX пишет `value` с форматом тех же знаков. Подытог: T, Σ t·g, M — суммы строк (`math.fsum`), ḡ = Σ t·g /
Σ wt (не среднее средних). Строка шаблона без блоков — T = 0, ḡ пусто. Ключи, не входящие в `key`/`section` (уступ под классом), суммируются в строку класса.
Тесты формы: `test_resources_forms.py`, `test_resources_templates.py`.

## 5. Маршруты (`/api/v1/resources`)

| Маршрут | Тело / запрос → ответ | Ошибки |
|---|---|---|
| `GET /defaults` | `{params, ranges}` (§2) | — |
| `POST /reports` | `{params, name?, save}` → задание `resources.report` `{source, id}` | 422 `bad_request`, `bad_param`, `report_too_big`, `group_numeric`, `filter_field_missing`; 404 `block_model_not_found`, `block_field_not_found`, `filter_not_found`, `template_not_found`; 409 `no_project`; 423 (только `save: true`) |
| `GET /reports` | `{reports: [{id, name, version, state, model: {id, name}, totals, graph: {stale, stale_reason, source_lost, pinned}, files, xlsx, created_at, updated_at, job_id, rows, size}]}` | — |
| `GET /reports/{id}?template=&cutoff=&page=&size=` | §3 + `rows` страницы (`size` ≤ 5000, умолч. 500), `rows_total`, `page`, `size`, `form` (если `template` в запросе или `template_id` в параметрах), `stale`, `stale_reason`, `graph`, `state`, `error`, `inputs`, `fields`, `created_at`, `updated_at`, `job_id` | 404 `resource_report_not_found`, `template_not_found` |
| `PUT /reports/{id}` | `{name, expected_version}` → `{id, version, name}` | 409 `version_conflict`, `report_building`, `name_taken {param: "name"}`; 422 `bad_param`; 423 |
| `DELETE /reports/{id}` | → `{id, deleted: true}` (подтверждение окном, `askImpact`) | 404; 409 `report_building`; 423 |
| `POST /reports/{id}/export` | `{format: "xlsx"\|"csv", path, template?, cutoff?, what?, overwrite?}` → задание `resources.export` `{source, id}` (§10) | 404 `resource_report_not_found`, `template_not_found`; 409 `files_exist {files}`, `report_building`, `resource_report_broken`; 422 `bad_param {path\|format\|what\|cutoff}`, `bad_path` |
| `POST /reports/{id}/drill` | `{group, cutoff, kind?}` → §8 | 404 `resource_report_not_found`, `block_model_not_found`; 409 `report_building`; 422 `bad_param` |
| `POST /reports/{id}/drill/rows` | `{group, cutoff, kind?, page?, size?, csv?}` → §8 | то же |
| `POST /compare` | `{report_a, report_b}` → §9.1 (синхронно) | 404 `resource_report_not_found`; 409 `report_building`; 422 `bad_param` |
| `POST /blocks/{id}/compare` | `{field_a, field_b, domain?, out?, expected_version?}` → задание `estimate.compare` `{source, id}` (§9.2) | 404 `block_model_not_found`, `block_field_not_found {param}`; 409 `version_conflict`, `field_exists {field}`; 422 `bad_param`, `not_ready`; 423 |
| `GET /templates` | `{templates: [{id, name, builtin, version}]}` | — |
| `GET /templates/{id}` | `{template}` (§4) | 404 `template_not_found`; 422 `bad_param {id}` |
| `PUT /templates/{id}` | `{template, expected_version?}` → `{id, version}`; `jorc`/`gkz` переписываются | 409 `version_conflict {version}`; 422 `bad_param`; 423 |
| `DELETE /templates/{id}?expected_version=` | → `{id, deleted: true}` | 404; 409 `version_conflict`; 422 (`jorc`, `gkz`); 423 |

В теле `drill` и `export` номер отчёта — в пути (`report_id` в теле — для команды реестра и `spec.parse_drill`; маршрут подставляет его сам).

## 6. Задания (6.7; явная регистрация в `jobs/registry.py`)

| Задание | Очередь | Стадии | Итог |
|---|---|---|---|
| `resources.report` | quick, 640 МБ | «Чтение» (модель, ограничитель), «Расчёт» (проход, контроль), «Запись» (только `save`) | §3; `events: [["resources.changed", {kind, id}]]` |
| `resources.export` | quick | «Расчёт формы», «Запись» (`‹имя›.‹случайное›.part` → переименование) | §10 |
| `estimate.compare` | quick | «Чтение», «Расчёт», «Запись» | §9.2 |

«Перестроить» (`lineage.rebuild`, `R/store.py` maker, `lineage/adapt_resources.py`): те же `params`, перезапись на месте, `version + 1`;
отчёт «идёт построение» (`state: "building"`) от первой до последней транзакции; отмена оставляет прежний отчёт. Модель, изменившаяся за
время расчёта, — 409 `model_changed` (ничего не записывается).

## 7. Объект, blobs, граф (решения 14–16, 21; новых ролей и миграций нет)

```
objects(kind="resource_report", parent_id=NULL).meta v1 — раздел «Отчёты» → «Отчёты по ресурсам», ≤ 1000
  v 1; algo "resources-1"; params (resolved canonical); params_hash; model {id, name, cells_sha, grid};
  fields {имя: fp}; inputs (ADR 0025); totals; state "ready"|"building"|"error"; error {code, message, hint} | null;
  files [{format, path, sha256, at}]; versions {numpy, numba, pyarrow}; job_id; rows; created_at
blobs owner=object: data (Parquet zstd, строки §3), sidecar:curve (Parquet), sidecar:control (JSON), sidecar:xlsx (последняя книга)
```
Узел графа `object:resource_report` (`lineage/adapt_resources.py`): fp = sha `data` + ALGO; входы — `object:block_model`, `bm_field`
каждого читаемого поля (элементы, g_б, плотность, ключи, FRAC, `STATUS`, `DENS_SRC`), `object:filter`, `wireframe`/ЦТМ листов.
Шаблон входом не является. `.bfggis`: `R/remap.py`; неизвестный id модели/фильтра → DEAD_ID. Поле Δ сравнения оценок — `source {op: "compare", a, b}`,
входы графа — оба поля (maker «Перестроить» для поля Δ не сделан — повторите сравнение).

## 8. Переход к блокам (Д-36; `R/drill.py`)

`POST /resources/reports/{id}/drill` `{group: ["Измеренные", "(пусто)"] | null, cutoff: 1.0 | null, kind?}` (`spec.parse_drill`: `kind`
по борту — `null` → `all`, число → `cutoff`; `balance`/`offbalance` — борта отчёта; `group: null` или `[]` — итог; ячейка проверяется по
отчёту — 422 `bad_param {cutoff|kind|group}`). Настоящий ответ (`contract_answers.json` → `drill`):

```json
{"rules": {"op": "and", "items": [{ограничитель отчёта}, {"cond": {"field": "CLASS", "op": "=", "value": "Измеренные"}},
                                  {"slab": {"axis": "z", "from": 0, "to": 10, "by": "share"}}, {"expr": "AU >= 1"}]},
 "cell": {"blocks": 1, "volume": 1000.0, "tonnes": 2500.0}, "stale": false, "stale_reason": null, "model_id": 1, "warnings": []}
```
«(пусто)» — `{"cond": {"field", "op": "empty"}}`; уступ — лист `slab` (расширение Э17, 20.8; необязательный `frame {azimuth, dip, roll}` для
осей u/v, `share` — только по z; уступ «(пусто)» — не «слой по всем уступам»); борт балансовых — `expr` с интервалом; блоки без плотности /
FRAC исключаются условием в `rules` (отчёт их не считает). Синхронно, считает ячейку заново ядром отчёта с ограничителем **этого дерева**
(Σ V·FRAC·f и Σ t·f набора = ячейке, ТД36-1, 1e−12). `stale: true` — отчёт «устарел» в графе или модель изменилась с расчёта (набор блоков
может отличаться — `warnings` и `stale_reason`). Известное расхождение: кусок блока на границе уступа короче 1e−12 высоты отчёт отбрасывает,
`slab` считает. `rules` → `blocks.filter {model_id, restrict: rules}` (3D; временный фильтр — `quick.rule` стиля модели, не в библиотеку).
`…/drill/rows` — тот же ответ + страница блоков:

```json
{"…ответ drill…": "…", "page": 1, "size": 1000, "total": 1,
 "rows": [{"i": 1, "j": 1, "k": 0, "x": 15.0, "y": 15.0, "z": 5.0, "dx": 10.0, "dy": 10.0, "dz": 10.0, "f": 1.0, "volume": 1000.0,
           "tonnes": 2500.0, "keys": {"CLASS": "Измеренные"}, "elements": {"AU": 1.0}}],
 "csv": null}
```
`size` ≤ 1000 (умолчание 1000), `page` ≥ 1; `csv: true` — в `csv` текст страницы (`;`, десятичная запятая, CRLF). Блок → `estimate.inspect` (инспектор Д-30) →
проба → интервал 7.17. Предпросмотр (не записанный отчёт) раскрыть до блоков нельзя; полоса swath → `slab` не сделана.

## 9. Сравнение (Д-37)

**9.1. Два отчёта** — `POST /resources/compare {report_a, report_b}` (A = B допустимо: Δ = 0, ТД37-1; отчёт в `building` — 409). Ответ
(`contract_answers.json` → `compare_reports`):

```json
{"a": {"id": 17, "name": "…", "version": 1, "model": {"id": 12, "name": "…", "version": 4, "grid": {…}, "cells_sha": "…"},
       "inputs": [{"kind": "object:block_model", "id": 12, "fp": "…", "name": "…"}, …], "stale": false, "stale_reason": null, "created_at": "…"},
 "b": {…}, "grids_differ": false, "warnings": [{"code": "density_differs", "message": "…"}],
 "keys": [{"kind": "field", "field": "CLASS"}], "elements": ["AU"],
 "rows": [{"g": ["Измеренные"] | null (итог), "total": false, "kind": "cutoff", "cutoff": 1.0, "cutoff_to": null, "only_in": null,
           "a": {blocks, volume, tonnes, elements}, "b": {…},
           "delta": {"blocks": 0, "volume": 0.0, "tonnes": -1000.0, "elements": {"AU": {"grade": 0.0, "metal": -2500.0}}}}],
 "curves": {"a": Curve | null, "b": Curve | null}}
```
Строки совмещаются по (ключи, `total`, `kind`, борт, верхний край); строка только в одном — `only_in: "a"|"b"`, `delta: null` (пропуск, не 0).
Δ содержания — ḡ_A − ḡ_B; пустое содержание в любом — пустая разность. Разные модели или `cells_sha` — `grids_differ: true` (сравниваются только
отчёты, не блоки). Предупреждения `{code, message}`: `grids_differ`, `keys_differ`, `cutoff_field_differs`, `grade_units_differ`,
`density_differs`, `stale`.

**9.2. Две оценки** — `POST /blocks/{id}/compare` → задание `estimate.compare` (`blocks/compare_job.py`, `blocks/compare.py`). Итог (`compare_result`):

```json
{"model_id": 12, "field": "AU_OK_EST_D", "a": "AU_OK_EST", "b": "AU_ID_EST", "field_written": true, "version": 3, "rows": 1000,
 "stats": {"n": 1000, "mean": 0.01, "std": 0.2, "min": -1.1, "max": 0.9, "percentiles": {"1": -0.7, "5": -0.4, "25": -0.1, "50": 0.0, "75": 0.1, "95": 0.4, "99": 0.6}},
 "histogram": {"edges": [… 51 значение], "counts": [… 50 значений]},
 "domains": [{"domain": "DY", "blocks": 400, "missing": 3, "volume": 4.0e5, "mean_a": 1.1, "mean_b": 1.0, "mean_delta": 0.1, "status_changed": 3}],
 "status": {"a": "AU_OK_STATUS", "b": "AU_ID_STATUS", "changed": 3} | null, "warnings": [],
 "stages": {"read": 0.004, "calc": 0.003, "write": 0.014, "total": 0.021}, "events": [["objects.changed", {"kind": "block_model", "id": 12}]]}
```
Δ = A − B, NaN, если пропуск в любом (не 0); поле Δ — `<A>_<B>_D` (или `out`), перезапись только прежнего `source.op == "compare"` (иначе 409
`field_exists`). `domains` — поле `domain` (категорийное; нет — первое поле роли `code`, затем `class`; иначе одна строка «Все»; блоки без подписи —
«(пусто)»); `blocks` — с Δ, `missing` — с пропуском в A или B; средние A, B, Δ — взвешены объёмом (№ 108), гистограмма — 50 интервалов от min до max
по числу блоков; `status_changed` — блоков со сменой `<префикс>STATUS` (STATUS ищется у полей `source.op == "estimate"` с именем на EST/ETYPE), если
оба поля из оценки, иначе `null`; `stats` — по блокам с Δ, дисперсия генеральная, перцентили по Хейзену.

## 10. Экспорт (решение 20; `R/xlsx.py`, `R/csv_out.py`, `R/export_job.py`, `R/export_cols.py`)

`POST /resources/reports/{id}/export` `{format: "xlsx"|"csv", path, template?, cutoff?, what?, overwrite?}`: `path` — абсолютный, расширение
формата добавляется при отсутствии; не внутрь папки проекта (422 `bad_path`); `template` — форма листа «Отчёт» (нет — сохранённый в отчёте, иначе общая);
`cutoff` — борт формы (нет — «без борта», иначе первый); `what` — только CSV: `table` (по умолчанию: все строки), `form` (форма шаблона), `both`
(таблица в выбранном файле и «‹имя› · форма.csv»). Итог задания `resources.export`:
`{path, files, format, what, bytes, rows, sheets, form_rows, recorded, seconds, events}` (`export_result` в `contract_answers.json` — образец формы).

XLSX (`composites/ore_xlsx.Book`, фирменные стили): «Отчёт» (форма: числа — `value` без округления, формат — знаки шаблона), «Таблица» (все строки
`data`), «Кривая», «Контроль», «Параметры» (модель, `cells_sha`, поля и их fp, плотность, ограничители — `title_of`, борта, дата, версия программы, ALGO).
CSV — `;`, десятичная запятая, CRLF, без BOM, пропуск — пустая ячейка. `‹имя›.‹случайное›.part` → переименование, все файлы одного экспорта или ни
одного; 409 `files_exist {files}` (в запросе и ещё раз в задании); `overwrite: true` — замена. После записи (отмена уже невозможна): XLSX
становится последней книгой отчёта (`sidecar:xlsx`), каждый файл — в `meta.files` (`{format, path, sha256, at}`) одной транзакцией, только если
отчёт не перестроен и не удалён (`recorded: false` — файл остаётся пользователю, отчёт его не перечисляет). Выгрузка работает для отчёта в любом
состоянии графа (устаревший тоже). Предпросмотр (не записанный отчёт) выгрузить нельзя (кнопки неактивны). PDF — Э22.

## 11. Ошибки (решение 30; тексты — `spec_parts.REFUSALS`, `spec.refuse(code, …)`; `{code, message, hint, …}`)

| Код | Статус | Когда; машинные поля |
|---|---|---|
| `bad_param` | 422 | разбор §2, §4–§10; `param` |
| `bad_request` | 422 | тело не объект |
| `report_too_big` | 422 | сочетаний групп > 20 000 или ячеек > 2·10⁶ |
| `too_many_reports` | 422 | в проекте уже 1000 отчётов (при записи) |
| `filter_field_missing` | 422 | в ограничителе поле, которого нет в модели (например `PIT` до Э21); `{field, model}` |
| `group_numeric` | 422 | числовое поле среди ключей группы; `{param: "groups.i"}` |
| `bad_path` | 422 | экспорт внутрь папки проекта |
| `resource_report_not_found`, `block_model_not_found`, `block_field_not_found {param}`, `filter_not_found`, `template_not_found {id}` | 404 | — |
| `report_building` | 409 | пишущий запрос, экспорт, переход, сравнение к отчёту, который строится |
| `version_conflict {version}` | 409 | отчёт, шаблон или модель (сравнение оценок) изменены в другом окне |
| `name_taken {param: "name"}` | 409 | переименование в занятое имя (без учёта регистра) |
| `files_exist {files}` | 409 | экспорт: файл уже есть, `overwrite` не задан |
| `model_changed` | 409 | блочная модель изменилась, пока считался отчёт — ничего не записано |
| `field_exists {field}` | 409 | сравнение оценок: поле с именем Δ уже есть и это не прежнее Δ |
| `resource_report_broken` | 409 | файл отчёта не найден в проекте |
| `project_read_only` | 423 | запись в «только просмотр» (§1) |

## 12. Интерфейсы Python

```python
# resources/spec.py (+ spec_parts.py, spec_bodies.py)
parse_request(body) -> Request(params: ReportSpec, name, save)       parse_params(raw) -> ReportSpec
ReportSpec.resolved(fields, z_range) -> ReportSpec                   .canonical() .hash_params() .params_hash()
ReportSpec.radices() / .group_count() / .decode(code) / .group_labels(code)
ReportSpec: model_id, elements (Element(field, unit)), cutoff_field, cutoffs (float|None, …), balance (Balance(cutoff, off_cutoff)),
            density, filter_id, restrict, keys (Key(kind, field, labels)), bench (Bench(h, z0, z_min, z_max, j0, n)), curve, template_id
Bench.label(j) / .labels();  resolve_bench(bench, z_range);  bench_label(z_from, z_to)
norm_unit(raw, param), metal_family(unit), metal_k(unit), unit_label(unit), default_elements(fields), default_density(fields)
parse_query / parse_rename / parse_drill / parse_drill_rows / parse_export / parse_compare_reports / parse_field_compare
refuse(code, message=None, hint=None, **extra) -> ApiError;  defaults_json(); ranges_json();  ALGO, KIND, REFUSALS, EMPTY_LABEL
# kernels.py, compute.py — ядро прохода (numba, cache=True); result.py — строки, итог, баланс, кривая; control.py — контроль
# templates.py (get/put/delete/templates), forms.py (build, rows_flat, format_number, excel_format), view.py (report_json, stored_spec, form_table, api_rows)
# store.py, store_data.py — объект и Parquet; job.py — resources.report; export_job.py, xlsx.py, csv_out.py, export_cols.py — выгрузка
# drill.py — answer / rows_answer;  compare.py — compare_reports;  warm.py — прогрев ядер (≈ 2,5 с);  remap.py — .bfggis
# blocks/compare.py — plan_of / Tally (Д-37, оценки);  blocks/compare_job.py — estimate.compare;  blocks/routes_compare.py
```
`fields` — `Model.fields` (словари `name`, `kind` `"number"|"category"`, `unit`, `role`, `labels`, `stats`); `z_range` — охват центров блоков ± половина
блока по Z. Модуль `spec` не тянет numpy (`test_no_numpy_at_import`).

## 13. Команды реестра (лента «Настройки проекта → Ресурсы»; Ctrl+K, ассистент; клавиш нет)

`resources.report` (без `model_id` — окно; `save` false — предпросмотр), `resources.curve`, `resources.templates` (uiOnly), `resources.export {report_id,
format, path, template?, cutoff?, what?, overwrite?}`, `resources.open {report_id?, template?, cutoff?}` (нижняя панель «Ресурсы»), `resources.rename`,
`resources.delete` (confirm, `askImpact`), `resources.drill {report_id, group, cutoff, kind?}`, `resources.compare {report_a, report_b}` (без параметров —
окно). «Геостатистика → Проверка → Сравнить оценки» — `estimate.compare {model_id, field_a, field_b, domain?, out?}` (confirm). Параметры команд плоские: элементы —
текст «AU:г/т; CU:%», борта — «без борта; 0,5; 1» или `cutoff_from/to/step`, ограничитель — дерево правил в JSON, группы — «CLASS; уступ».

## 14. Фикстуры и тесты

- `core/tests/data/resources/params.json` — случаи `minimal`, `jorc_class`, `gkz_balance`, `bench_domain`, `restricted`: `body` → `canonical`, `resolved`;
  круговой проход — `test_resources_spec.py`; `BF_REGEN_RESOURCES=1` переписывает.
- `core/tests/data/resources/contract_answers.json` — `{status, body}`; модель Т8.10-1 (8 блоков 10×10×10 м, ρ = 2,5, Au 0,2…5,0 г/т, борт 1,0: 12 500 т,
  2,5 г/т, 31,25 кг). **Настоящие ответы ядра**: `report_post`, `preview_result`, `report_result`, `reports_list`, `report_get`, `report_put`, `report_rebuilt`,
  `report_delete`, `templates`, `template_get`, `template_gkz`, `template_put`, `template_delete`, `report_not_found`, `template_not_found`, `field_not_found`,
  `filter_not_found`, `report_building`, `version_conflict`, `name_taken`, `filter_field_missing`, `report_too_big`, `read_only`, `drill`, `drill_rows`,
  `compare_reports`, `compare_post`, `compare_result`, `estimate_compare`, `defaults`, `bad_param`, `group_numeric`, `refusals`. **Образцы форм**: `export_post`,
  `export_result`, `files_exist`.
- pytest: `test_resources_{spec,kernels,result,control,templates,forms,store,export,api,contract,drill,compare,warm}.py`, `test_lineage_resources.py`,
  `test_filters_slab.py`, `test_estimate_compare.py`; vitest `ui/test/resources/*`, `ui/test/blocks/estimateCompare.test.ts`; E2E `app/e2e/resources.spec.ts`.

## 15. Трактовки (вне буквы ТЗ и ADR 0027)

1. Умолчание `cutoffs` — `[null]` («без борта»), не `[0]` как в Э17: ТЗ 8.10 даёт «без борта» только как c = null (BF `cutoff=None`); борт 0 исключает блоки без g_б.
2. `balance` — отдельный ключ `{cutoff, off_cutoff?}`; в ADR есть только строки `balance`/`offbalance`. Нет c_з — строк `offbalance` нет.
3. `bench.h` по умолчанию 10 м; `z_min`/`z_max` ограничивают набор уступов сетки (уступы, касающиеся диапазона; целые уступы), а не режут блоки внутри уступа.
4. Единица элемента канонизируется (`g/t`, `gpt`, `г/т` → `g/t`), чтобы хеш не зависел от написания; неизвестная единица не ошибка (решение 10).
5. Группа задаётся строкой (поле) или `{kind: "field" | "bench"}`; `bench` без ключа `bench` в `groups` отбрасывается.
6. Размер результата: предпросмотр — до 20 000 строк в ответе (`truncated`); сохранённый — страницы `GET …/reports/{id}`.
7. `GET /resources/defaults`, `parse_query` и `kind` ячейки `drill` — вне списка ADR «API», по образцу Э19 (`GET /implicit/defaults`) и ТЗ Д-36.
8. Кривая — корзины + суффиксные суммы (числа те же, без сортировки 20 млн); итог — отдельные аккумуляторы (контроль «Σ групп − итог» проверяет порядок сумм).
9. Шаблон: `DELETE /templates/{id}` добавлен (своих; `jorc`/`gkz` — 422); подытог — после последнего члена; «Прочие» зарезервировано; `balance` — bool.
10. Отчёт пишется только в «Запись», в одной транзакции; занятое имя при создании — « (2)», при переименовании — 409 `name_taken`; 409 `model_changed`.
11. Д-36: временный фильтр = `quick.rule` стиля модели (`blocks.filter` принимает только `filter_id`), снимается «Сбросить» / закрытием окна / сменой проекта;
    лист `slab` в конструкторе фильтров — только чтение. Д-37: средние по доменам — по объёму (№ 108); гистограмма — по числу блоков; данные T18/T19 промпта нет —
    ТД37-2 на 3 блоках по 50 м³ (№ 109).

## 16. Вне ТЗ / не сделано

- Кривые «тоннаж — содержание» по каждой группе (кривая — по итогу отчёта); потери и разубоживание; неопределённость ресурсов (по реализациям Э18б); PDF (Э22);
  полоса swath → `slab` (Д-36 «то же из полосы swath оценки»: в `modules/swath` нет щелчка по полосе).
- Предпросмотр нельзя выгрузить (XLSX / CSV) и раскрыть до блоков; панель «Ресурсы» не открывает нижний док сама (команда `resources.open`, кнопка в окне).
- Группировка по числовому полю — нет (№ 112; подсказка — калькулятор); поворот и субблоки блочной модели без регуляризации — как в Э17.
- Форма ГКЗ по образцу заказчика — вопрос № 105 (до ответа — умолчание §4); maker «Перестроить» для поля Δ — нет.
