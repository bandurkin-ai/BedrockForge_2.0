# Э20 — контракт подсчёта ресурсов (для дорожек Х, Я, Ф, Д, И-А, И-Б, П)

**Состояние: контракт 20.1 (разбор параметров — `core/bf_core/resources/{spec,spec_parts,spec_bodies}.py`; остальное —
по ADR 0027, уточняется подшагами 20.2–20.10 через оркестратора).** Источник истины — ADR 0027 (решения 1–30, «API»,
«Точность и приёмка», вопросы № 105–112), ТЗ 8.10, 8.0, 7.35 Д-36, Д-37, Д-35 (граф), Д-27 (контроль); граф — ADR 0025.
Образцы — `core/tests/data/resources/{params,contract_answers}.json`. `contract_answers.json` сейчас — **образцы форм**
(ключи `defaults`, `bad_param`, `refusals` — настоящие, пишет `test_resources_spec.py`); настоящие ответы ядра
запишет 20.7 (`BF_REGEN_RESOURCES=1 uv run pytest -q tests/test_resources_contract.py`). Интерфейс принимается только на
настоящих ответах. Изменение формы — только через оркестратора.
Префикс маршрутов — `/api/v1/resources` (роутер `resources/router.py`), сравнение оценок — `POST /api/v1/blocks/{id}/compare`.

Пути: `R/` = `core/bf_core/resources/`, `B/` = `core/bf_core/blocks/`, `U/` = `ui/src/modules/resources/`.

## 1. Общие правила

- `X-BF-Token`, Host/Origin — как у всего ядра. Координаты, суммы, значения — float64; пусто — `null` в JSON
  (NaN в массивах), **никогда не 0** (8.0.1). Число на входе — число или текст «0,5» / «0.5». Нет ключа / `null` / `""` —
  умолчание.
- Ошибка — `{code, message, hint, param?, …}` по-русски с подсказкой (§11). `param` — путь внутри `params`: `"cutoffs.2"`,
  `"cutoffs.step"`, `"elements.1.unit"`, `"groups.1"`, `"bench.h"`, `"balance.off_cutoff"`, `"restrict.items.0.cond.op"`;
  на уровне запроса — `"params"`, `"name"`, `"save"`, `"expected_version"`, `"path"`. Нет проекта — 409 `no_project`; запись
  в «только просмотр» — 423 `project_read_only` (сохранение отчёта, переименование, удаление, шаблоны, экспорт — нет:
  экспорт пишет вне проекта и работает); **предпросмотр, переход (`drill`), сравнение отчётов и оценок — работают**.
- Задание: ответ `{source: "jobs", id}`; ход — `GET /jobs/{source}/{id}`; синхронные проверки (404, 409, 422) — до
  постановки. Итог несёт `events: [[имя, данные]]`. Запись — последней стадией, целиком или ничего (объект, blobs,
  `lineage.record` — одна транзакция); отмена ничего не оставляет.
- Оптимистичная блокировка отчёта и шаблона — `expected_version` (целое ≥ 1, иначе 422 `bad_param`; другая версия — 409
  `version_conflict {version}`); отчёт в состоянии `building` — 409 `report_building` у пишущих запросов.
- События: `resources.changed {kind: "resource_report", id, deleted?}` — после записи, перестроения и удаления;
  `resources.changed {kind: "template", id}` — шаблон; `estimate.compare` пишет поле — `blocks.changed {model_id}`.
- Побитность (8.0.2): результат не зависит от размера порций, порядка блоков внутри порции и числа потоков;
  «Перестроить» (тот же `params`) = расчёт с нуля, те же байты `data`. ALGO = `resources-1` (`spec.ALGO`).

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

Тело запроса: `params` — обязательно; `name` — имя объекта (≤ 200 знаков; нет — «Отчёт по ресурсам», занято — « (2)»);
`save` — по умолчанию **false** (предпросмотр: ничего не пишется); true — объект `resource_report`.

| Ключ | Правило, умолчание, диапазон |
|---|---|
| `model_id` | обязателен, номер блочной модели |
| `elements` | ≤ 20; строка (поле) или `{field, unit?}`; нет — поля `*_CUT`, иначе роль `grade` (как 7.31); пустой список — 422; повтор поля — 422; единица `unit` — текст ≤ 20 знаков, нет — единица поля. Единицы приводятся к канону: `g/t`, `gpt`, `г/т` → `"g/t"`; `ppm`, `%`, `ppb` как есть; другая — как задана (металла нет, предупреждение `unit_unknown`) |
| `cutoff_field` | g_б (число); нет — первый элемент; может не входить в `elements` |
| `cutoffs` | список ≤ 200 (элемент — число или `null` = «без борта»: все блоки группы, и без g_б, как BF `cutoff=None`) или диапазон `{from, to, step}` (step > 0, to ≥ from, ≤ 200 значений, шум суммы убирается: 0,1 + 2·0,1 → 0,3); повторы убираются, порядок — как задан (диапазон — по возрастанию); нет — `[null]`; модуль борта ≤ 10¹²; в каноне — раскрытый список |
| `balance` | `{cutoff: c_б, off_cutoff?: c_з}`; c_з < c_б (иначе 422 `balance.off_cutoff`); строки `balance` (g_б ≥ c_б) и `offbalance` (c_з ≤ g_б < c_б; нет c_з — нет строк) |
| `density` | число > 0, поле или нет: меняющееся поле SG, иначе меняющееся поле с ролью `density` (постоянное пропускается), иначе 422 `bad_param {density}` |
| `filter_id`, `restrict` | ограничитель: фильтр библиотеки И быстрое дерево правил (`filters.normalize_rules`; И = произведение долей, решение 8); «Карьер» и «Ниже рельефа» окна — листья этого дерева |
| `groups` | ≤ 3 ключа: строка = категорийное поле; `{kind: "field", field}`; `{kind: "bench"}` (один). Повтор — 422. Числовое поле — 422 `group_numeric {param: "groups.i"}` (№ 112); нет поля — 404 `block_field_not_found`. Подписи — по ключам, порядок ключей = столбцы `g1…g3`. Пустых сочетаний в выводе нет |
| `bench` | только при ключе `bench`; `h` — высота, м, (0; 1000], по умолчанию 10; `z0` — отметка, нет — кратная H ниже низа диапазона; `z_min`, `z_max` — диапазон (нет — охват модели по Z, `z_min` < `z_max`); берутся уступы сетки `[Z0 + j·H; Z0 + (j + 1)·H]`, касающиеся диапазона, ≤ 1000 (422 `bench.h`); часть блока вне уступов — «(пусто)» (№ 107). Подпись «+250…+260», «-10…0», «+12,5…+22,5» (`spec.bench_label`) |
| `curve` | 0 (без кривой) или 2…500; по умолчанию 100; 1 — 422 |
| `template_id` | код шаблона (`[A-Za-z0-9_.-]`, ≤ 64) или `null` (нет формы); **только показ** — не входит в `params_hash` и вход графа (решение 17) |

`spec.parse_params` проверяет формы и диапазоны; поля — по модели: `spec.resolved(model.fields, (z_lo, z_hi))` подставляет
умолчания, приводит имена к написанию модели, берёт единицу поля, считает уступы и подписи ключей. Лимиты данных:
сочетаний групп ≤ 20 000 (произведение «категорий + 1» по ключам), ячеек ≤ 2·10⁶, иначе 422 `report_too_big`.
`resolved().canonical()` — `meta.params` и вход `params_hash()` (SHA-256 канонического JSON без `template_id`, + ALGO).
`GET /resources/defaults` → `{params, ranges}` (`spec.defaults_json()`, `ranges_json()`) — форма окна.

**Код группы (решение 4)**: смешанное основание, первый ключ — старшая цифра; цифра ключа = код категории + 1 (в блочной модели
−1 — пусто) или номер уступа − j0 + 1; **0 = «(пусто)»**. `spec.radices()` = (len(labels) + 1 по ключам),
`group_count()`, `decode(code)`, `group_labels(code)` (подписи, 0 → «(пусто)»). Группа без ключей — код 0, одна группа.

Единицы и металл (решение 10), `spec.metal_family(unit)`, `spec.metal_k(unit)`: M_e = Σ t·g_e·k_e хранится в базовой единице
семейства — `g` (граммы: г/т, ppm — k = 1; ppb — k = 10⁻³) или `t` (тонны: % — k = 0,01); неизвестная единица — металла нет
(`null`). Показ — единица шаблона (§4); 1 тыс. унц = 31 103,4768 г (`spec.TROY_OUNCE_G`).

## 3. Результат — итог задания `resources.report` (`save: false` — тот же ответ без `report_id`)

```json
{"report_id": 17 | null, "name": "Отчёт по ресурсам", "version": 1, "saved": true, "algo": "resources-1",
 "params": {resolved canonical}, "params_hash": "sha256…",
 "model": {"id": 12, "name": "Рудное тело", "version": 4, "cells_sha": "…", "grid": {…}},
 "elements": [{"field": "AU", "unit": "g/t", "unit_label": "г/т", "metal_family": "g", "metal_base": "г"}],
 "cutoff_field": "AU", "density": {"field": "SG", "value": null},
 "keys": [{"kind": "field", "field": "CLASS", "labels": ["Измеренные", "Выявленные", "Предполагаемые"]}],
 "bench": null | {"h": 10, "z0": -20, "z_min": -12, "z_max": 33, "j0": 0, "n": 6},
 "rows": [Row…], "rows_total": 7, "truncated": false,
 "totals": {Row-итог первого борта},
 "curve": Curve | null, "control": Control, "excluded": {"filtered": 0, "no_volume": 0, "no_density": 0},
 "form": Form | null, "warnings": [{"code", "message", "hint", "param?"}], "files": [], "seconds": {"read": 0.1, "restrict": 0.0, "kernel": 0.2}}
```

**Row** (Parquet `data`: столбцы `g1…g3`, `total`, `kind`, `cutoff`, `cutoff_to`, `blocks`, `volume`, `tonnes`, `<e>:wt`,
`<e>:sum`, `<e>:metal`, `<e>:grade`, `<e>:missing_t`, `<e>:missing_n`):

```json
{"g": ["Измеренные"], "total": false, "kind": "cutoff", "cutoff": 1.0, "cutoff_to": null,
 "blocks": 3, "volume": 3000.0, "tonnes": 7500.0,
 "elements": {"AU": {"wt": 7500.0, "sum": 13750.0, "grade": 1.8333333333333333, "metal": 13750.0,
                     "missing_t": 0.0, "missing_n": 0}}}
```

- `kind`: `cutoff` (g_б ≥ `cutoff`), `all` («без борта», `cutoff` null), `balance` (g_б ≥ c_б, `cutoff` = c_б),
  `offbalance` (c_з ≤ g_б < c_б: `cutoff` = c_з, `cutoff_to` = c_б). `total: true` — итог (отдельные аккумуляторы, не сумма
  групп; `g` — `null` по числу ключей; без ключей `g: []` и итоговые строки — единственные). Порядок: по `kind`/борту запроса
  (сначала `all`, борта как заданы, `balance`, `offbalance`), внутри — итог, затем группы по возрастанию кода. Пустые группы
  не выводятся.
- `blocks` — число блоков с f > 0, объёмом и плотностью в строке; `volume` = Σ V·FRAC·f, `tonnes` = Σ V·FRAC·f·ρ;
  по элементу: `wt` = Σ тоннажа блоков, у которых есть g_e (знаменатель), `sum` = Σ t·g_e, `grade` = sum / wt (пусто, если wt = 0),
  `metal` = sum·k_e в базовой единице (пусто — единица неизвестна), `missing_t`/`missing_n` — тонны и число блоков без g_e
  (не 0, не в среднее и металл).
- `excluded` (каждый блок один раз, в этом порядке): `filtered` (f = 0), `no_volume` (пустой FRAC), `no_density` (плотность пуста
  или ≤ 0; в «Контроль»). Блок без g_б входит в строки `all`, но не в строки борта (в том числе борта ≤ 0, как Э17).
- `rows` в ответе: сохранённый отчёт — первая страница (500) + `rows_total`, остальное — `GET /resources/reports/{id}`;
  предпросмотр — до 20 000 строк (`truncated: true` и предупреждение `rows_truncated` — «сохраните отчёт, чтобы смотреть все»).
- `totals` (для дерева проекта и сравнения): итоговая строка первого борта `{kind, cutoff, blocks, volume, tonnes,
  elements: {e: {grade, metal}}}`.

**Curve** (`sidecar:curve`; решение 11): 100 бортов (2–500) от min до max g_б по статистике поля (как Э17), по итогу:

```json
{"field": "AU", "cutoffs": [0.2, …], "blocks": […], "volume": […], "tonnes": […],
 "elements": {"AU": {"grade": […], "metal": […]}}}
```
Борт c: S(c) = {g_б ≥ c} (корзины + суффиксные суммы Neumaier, ADR «Варианты»); `tonnes` не возрастает с бортом (Т8.10-3).

**Control** (`sidecar:control`; решение 13; ТД27-2):

```json
{"tolerance": 1e-12,
 "sum_groups": {"tonnes": {"sum": 20000.0, "total": 20000.0, "rel": 0.0},
                "volume": {…}, "metal": {"AU": {…}}, "ok": true},
 "no_density": {"blocks": 0, "volume": 0.0},
 "default_density": null | {"blocks": 0, "tonnes": 0.0},
 "no_grade": {"AU": {"blocks": 0, "tonnes": 0.0}},
 "status": {"AU": {"prefix": "AU_OK_", "items": [{"code": 1, "label": "вне домена", "blocks": 0, "volume": 0.0, "share": 0.0}, …]}},
 "wireframes": [{"wireframe_id": 7, "name": "Карьер", "volume_restrictor": 1.0e6, "volume_wireframe": 1.0e6,
                 "diff_pct": 0.0, "warning": false, "outside_extent": false}],
 "warnings": []}
```
`rel` — |Σ групп − итог| / |итог| для T, V, M по борту каждой строки (худший); > 1e−12 — предупреждение `sum_mismatch`.
`default_density` — тоннаж блоков с `DENS_SRC` = 1 (есть поле — иначе `null`); `status` — только для элементов из оценки
(`source.op = estimate`, префикс `<префикс>STATUS`): коды 1, 2, 3 (`вне домена`, `мало проб`, `вырожденная система`), `share` —
доля объёма ограничителя; `wireframes` — по листу `wireframe` внутри ограничителя: Σ V·FRAC·f_листа против
`solids.signed_volume`, `diff_pct` > 1 % — предупреждение `wireframe_volume`, каркас за охватом модели — `outside_extent`
(№ 110).

**Warnings** (`code`): `unit_unknown {param}` (металла нет), `sum_mismatch`, `no_density` (есть блоки без плотности),
`wireframe_volume`, `template_other` (подписи схемы вне шаблона — в «Прочие»), `balance_missing` (форма ГКЗ без `balance`),
`rows_truncated`, `grids_differ`, `cutoff_field_differs`, `grade_units_differ` (сравнение).

## 4. Форма и шаблоны (решения 17–19; `R/templates.py`, `R/forms.py` — 20.4)

**Template** (справочник `settings`, scope `resources`, key `report_templates`; `jorc` и `gkz` всегда есть, переписываются, не
удаляются; шаблон — показ, смена не делает отчёт «устарел»):

```json
{"id": "jorc", "name": "JORC", "builtin": true, "version": 1,
 "key_field": null, "section_field": null,
 "order": ["Измеренные", "Выявленные", "Предполагаемые"],
 "subtotals": [{"label": "Изм. + Выяв.", "members": ["Измеренные", "Выявленные"], "after": "Выявленные"}],
 "total_label": "Итого", "other_label": "Прочие",
 "balance": "cutoff", "columns": ["tonnes", "grade", "metal"],
 "units": {"tonnes": "kt", "volume": "m3", "metal": {"g": "koz", "t": "t"}},
 "decimals": {"tonnes": 1, "volume": 0, "grade": 2, "metal": 1}}
```

`key_field` — поле-ключ строк (null — первый ключ с ролью `class`, иначе первый); `section_field` — внешний ключ (домен), по одному
разделу на подпись (null — без разделов); `order` — порядок подписей; `subtotals` — подытоги после `after`; подписи схемы
вне `order` — строка `other_label` + предупреждение `template_other`; строка шаблона без блоков — T = 0, ḡ пусто (не 0).
`balance`: `"cutoff"` — один блок столбцов (борт запроса `?cutoff=`, нет — первый), `"split"` — «Балансовые» /
«Забалансовые» (строки `balance`, `offbalance`; нет `params.balance` — предупреждение `balance_missing`).
Единицы: `tonnes` — `t` / `kt` / `Mt`; `volume` — `m3` / `km3`; `metal` — по семейству элемента: `g` → `g` / `kg` / `t` / `koz`
(тыс. унц), `t` → `t` / `kt`. По умолчанию (№ 106): JORC — тыс. т, тыс. унц; ГКЗ — тыс. т, кг; % → т. ГКЗ (№ 105): порядок `A`, `B`,
`C1`, `C2`, подытог «A + B + C1» после `C1`, `balance: "split"`, строка «Итого».

**Form** (ответ `form` и `GET /resources/reports/{id}?template=&cutoff=`; считает только ядро — один источник чисел для
таблицы окна и XLSX, Т8.10-5):

```json
{"template_id": "jorc", "cutoff": 1.0,
 "columns": [{"key": "tonnes", "label": "Руда, тыс. т", "group": null},
             {"key": "grade:AU", "label": "Au, г/т", "group": null},
             {"key": "metal:AU", "label": "Au, тыс. унц", "group": null}],
 "sections": [{"label": null, "rows": [
   {"kind": "row", "label": "Измеренные", "cells": {"tonnes": {"value": 7.5, "text": "7,5"},
                                                     "grade:AU": {"value": 1.8333333333333333, "text": "1,83"},
                                                     "metal:AU": {"value": 0.4420…, "text": "0,4"}}}]}],
 "warnings": []}
```
`kind` строки: `row`, `subtotal`, `total`, `other`. Ячейка `{value, text}`: `value` — число в единице шаблона **без округления**
(`null` — нет значения), `text` — показ: знаки шаблона, округление «от нуля» по десятичной записи (как показывает Excel), десятичная запятая,
неразрывный пробел между тысячами, пусто — «—». Интерфейс показывает `text` как есть; XLSX пишет `value` с форматом тех же
знаков. Подытог: T, Σ t·g, M — суммы строк, ḡ = Σ t·g / Σ wt (не среднее средних). `group` у столбца — «Балансовые» / «Забалансовые».

## 5. Маршруты (`/api/v1/resources`)

| Маршрут | Тело / запрос → ответ | Ошибки |
|---|---|---|
| `GET /defaults` | `{params, ranges}` (§2) | — |
| `POST /reports` | `{params, name?, save}` → задание `resources.report` `{source, id}` | 422 `bad_param`; 404 `block_model_not_found`, `block_field_not_found`, `filter_not_found`; 422 `filter_field_missing`, `group_numeric`; 423 (только `save: true`) |
| `GET /reports` | `{reports: [{id, name, state, version, model: {id, name}, totals, graph: {stale, stale_reason, source_lost, pinned}, files, created_at}]}` | — |
| `GET /reports/{id}?template=&cutoff=&page=&size=` | §3 без `form` по умолчанию + `rows` страницы (`size` ≤ 5000, 500), `rows_total`, `form` (если `template`/`template_id`), `stale`, `stale_reason` | 404 `resource_report_not_found`, `template_not_found` |
| `PUT /reports/{id}` | `{name, expected_version}` → `{id, version}` | 409 `version_conflict`, `report_building`; 423 |
| `DELETE /reports/{id}` | → `{deleted: true}` (подтверждение окном, `askImpact`) | 404; 409 `report_building`; 423 |
| `POST /reports/{id}/export` | `{format: "xlsx"\|"csv", path, template?, cutoff?, overwrite?}` → задание `resources.export` | 409 `files_exist {files}`; 422 `bad_param {path}`; 404 |
| `POST /reports/{id}/drill` | `{group, cutoff, kind?}` → §8 | 404; 422 `bad_param` |
| `POST /reports/{id}/drill/rows` | `{group, cutoff, kind?, page, size, csv?}` → §8 | то же |
| `POST /compare` | `{report_a, report_b}` → §9 (синхронно) | 404 `resource_report_not_found` |
| `POST /blocks/{id}/compare` | `{field_a, field_b, domain?, out?}` → задание `estimate.compare` | 404 `block_field_not_found`; 423 |
| `GET /templates` | `{templates: [{id, name, builtin, version}]}` | — |
| `GET /templates/{id}` | `{template}` (§4) | 404 `template_not_found` |
| `PUT /templates/{id}` | `{template, expected_version}` → `{id, version}`; `jorc`/`gkz` переписываются | 409 `version_conflict`; 422; 423 |

В теле `drill` и `export` номер отчёта — в пути (`report_id` в теле — для команды реестра и `spec.parse_drill`).

## 6. Задания (6.7; явная регистрация в `jobs/registry.py` — оркестратор)

| Задание | Очередь | Стадии | Итог |
|---|---|---|---|
| `resources.report` | quick, 640 МБ | «Чтение» (модель, ограничитель), «Расчёт», «Запись» (только `save`) | §3; `events: [["resources.changed", {kind, id}]]` |
| `resources.export` | quick | «Расчёт формы», «Запись» (`‹имя›.part` → переименование) | `{path, format, bytes, rows}` |
| `estimate.compare` | quick | «Чтение», «Расчёт», «Запись» | §9.2 |

«Перестроить» (`lineage.rebuild`, `R/store.py` maker): те же `params`, перезапись на месте, `version + 1`.

## 7. Объект, blobs, граф (решения 14–16, 21; новых ролей и миграций нет)

```
objects(kind="resource_report", parent_id=NULL).meta v1 — раздел «Отчёты» → «Отчёты по ресурсам», ≤ 1000
  v 1; algo "resources-1"; params (resolved canonical); params_hash; model {id, name, cells_sha, grid};
  fields {имя: fp}; inputs (ADR 0025); totals; state "ready"|"building"|"error"; error {code, message, hint} | null;
  files [{format, path, sha256, at}]; versions {numpy, numba, pyarrow}; created_at
blobs owner=object: data (Parquet zstd, строки §3), sidecar:curve (Parquet), sidecar:control (JSON), sidecar:xlsx
```
Узел графа `object:resource_report` (`lineage/adapt_resources.py`): fp = sha `data` + ALGO; входы — `object:block_model`, `bm_field`
каждого читаемого поля (элементы, g_б, плотность, ключи, FRAC, `STATUS`, `DENS_SRC`), `object:filter`, `wireframe`/ЦТМ листов.
Шаблон входом не является.

## 8. Переход к блокам (Д-36; `resources.drill`)

`POST /resources/reports/{id}/drill` `{group: ["Измеренные", "(пусто)"] | null, cutoff: 1.0 | null, kind?}` (`spec.parse_drill`: `kind`
по борту — `null` → `all`, число → `cutoff`; `balance`/`offbalance` — борта отчёта; `group: null` или `[]` — итог):

```json
{"rules": {"op": "and", "items": [{ограничитель отчёта}, {"cond": {"field": "CLASS", "op": "=", "value": "Измеренные"}},
                                  {"slab": {"axis": "z", "from": 0, "to": 10, "by": "share"}}, {"expr": "AU >= 1"}]},
 "cell": {"blocks": 3, "volume": 3000.0, "tonnes": 7500.0}, "stale": false, "stale_reason": null, "model_id": 12}
```
«(пусто)» — `{"cond": {"field", "op": "empty"}}`; уступ — лист `slab` (расширение Э17, 20.8); борт балансовых — `expr` с
интервалом. Синхронно, без расчёта. `rules` → `blocks.filter {model_id, restrict: rules}` (3D). `…/drill/rows` →

```json
{"cell": {…}, "page": 1, "size": 1000, "total": 3,
 "rows": [{"i": 0, "j": 0, "k": 0, "x": 5.0, "y": 5.0, "z": 5.0, "dx": 10, "dy": 10, "dz": 10, "f": 1.0, "volume": 1000.0,
           "tonnes": 2500.0, "keys": {"CLASS": "Измеренные"}, "elements": {"AU": 1.0}}],
 "csv": null}
```
`size` ≤ 1000; `csv: true` — в `csv` текст страницы (`;`, десятичная запятая, CRLF). Σ `volume`, `tonnes` набора = `cell` (ТД36-1, 1e−12).

## 9. Сравнение (Д-37)

**9.1. Два отчёта** — `POST /resources/compare {report_a, report_b}` (A = B допустимо: Δ = 0, ТД37-1):

```json
{"a": {"id": 17, "name": "…", "model": {"id": 12, "name": "…", "cells_sha": "…"}, "inputs": […], "stale": false, "created_at": "…"},
 "b": {…}, "grids_differ": false, "warnings": [],
 "keys": [{"kind": "field", "field": "CLASS"}], "elements": ["AU"],
 "rows": [{"g": ["Измеренные"], "total": false, "kind": "cutoff", "cutoff": 1.0, "only_in": null,
           "a": {Row без g}, "b": {Row без g},
           "delta": {"blocks": 0, "volume": 0.0, "tonnes": 0.0, "elements": {"AU": {"grade": 0.0, "metal": 0.0}}}}],
 "curves": {"a": Curve | null, "b": Curve | null}}
```
Строки совмещаются по (ключи, `kind`, борт); строка только в одном — `only_in: "a"|"b"`, `delta: null` (пропуск, не 0).
Разные модели или `cells_sha` — `grids_differ: true`; разные g_б или единицы — предупреждения `cutoff_field_differs`,
`grade_units_differ`.

**9.2. Две оценки** — `POST /blocks/{id}/compare` → задание `estimate.compare`:

```json
{"model_id": 12, "field": "AU_OK_EST_D", "a": "AU_OK_EST", "b": "AU_ID_EST", "field_written": true,
 "stats": {"n": 1000, "mean": 0.01, "std": 0.2, "min": -1.1, "max": 0.9, "percentiles": {"1": -0.7, "5": -0.4, "25": -0.1, "50": 0.0, "75": 0.1, "95": 0.4, "99": 0.6}},
 "histogram": {"edges": […], "counts": […]},
 "domains": [{"domain": "DY", "blocks": 400, "volume": 4.0e5, "mean_a": 1.1, "mean_b": 1.0, "mean_delta": 0.1, "status_changed": 3}],
 "status": {"a": "AU_OK_STATUS", "b": "AU_ID_STATUS", "changed": 3} | null, "warnings": []}
```
Δ = A − B, NaN, если пропуск в любом (не 0); `domains` — по полю `domain` (категорийное; нет — по роли `code`/`class`, иначе
одна строка «Все»); средние A, B, Δ — взвешены объёмом (№ 108), гистограмма — по числу блоков; `status_changed` — блоков со сменой
`<префикс>STATUS`, если оба поля из оценки (иначе `null`). Поле Δ — `source {op: "compare", a, b}`, вход графа — оба поля.

## 10. Экспорт (решение 20; `R/xlsx.py`, `R/csv_out.py`, `R/export_job.py`)

XLSX (`composites/ore_xlsx.Book`, фирменные стили): «Отчёт» (форма), «Таблица» (все строки `data`), «Кривая», «Контроль»,
«Параметры» (модель, `cells_sha`, поля и их fp, плотность, ограничители — `title_of`, борта, дата, версия программы, ALGO).
CSV — `;`, десятичная запятая, CRLF, пропуск — пустая ячейка; строки таблицы, не форма. `‹имя›.part` → переименование; 409
`files_exist {files}`; `overwrite: true` — замена. PDF — Э22.

## 11. Ошибки (решение 30; тексты — `spec_parts.REFUSALS`, `spec.refuse(code, …)`)

| Код | Статус | Когда; машинные поля |
|---|---|---|
| `bad_param` | 422 | разбор §2, §5–§10; `param` |
| `report_too_big` | 422 | сочетаний групп > 20 000 или ячеек > 2·10⁶ |
| `filter_field_missing` | 422 | в ограничителе поле, которого нет в модели (например `PIT` до Э21); `{field}` |
| `group_numeric` | 422 | числовое поле среди ключей группы; `{param: "groups.i"}` |
| `resource_report_not_found`, `block_field_not_found`, `filter_not_found`, `template_not_found` | 404 | `block_field_not_found` несёт `param` |
| `report_building`, `version_conflict {version}`, `files_exist {files}` | 409 | — |
| `project_read_only` | 423 | §1 |

## 12. Интерфейсы Python (по коду 20.1; `kernels`, `compute`, `forms`… — дополняют своими сигнатурами 20.2, 20.4)

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
```
`fields` — `Model.fields` (словари `name`, `kind` `"number"|"category"`, `unit`, `role`, `labels`, `stats`); `z_range` — охват центров блоков ± половина
блока по Z. Модуль не тянет numpy (`test_no_numpy_at_import`).

## 13. Команды реестра (лента «Блочные модели → Ресурсы»; Ctrl+K, ассистент; клавиш нет)

`resources.report` (без `model_id` — окно), `resources.curve`, `resources.templates` (uiOnly), `resources.export {report_id, format, path}`,
`resources.delete` (confirm, `askImpact`), `resources.drill {report_id, group, cutoff}`, `resources.compare {report_a, report_b}`;
«Геостатистика → Проверка → Сравнить оценки» — `estimate.compare {model_id, field_a, field_b}`.

## 14. Фикстуры

- `core/tests/data/resources/params.json` — случаи `minimal`, `jorc_class`, `gkz_balance`, `bench_domain`, `restricted`: `body` →
  `canonical` (`spec.parse_params(body).canonical()`), `resolved` (на модели `fields`, `z_range` из того же файла); круговой проход —
  `test_resources_spec.py`; `BF_REGEN_RESOURCES=1` переписывает `canonical` и `resolved`.
- `core/tests/data/resources/contract_answers.json` — `{status, body}`: **образцы** (по контракту, числа — Т8.10-1: 8 блоков 10×10×10 м,
  ρ = 2,5, Au 0,2…5,0 г/т, борт 1,0: 12 500 т, 2,5 г/т, 31,25 кг) — `report_post`, `report_result`, `reports_list`, `report_get`,
  `report_put`, `report_delete`, `export_post`, `export_result`, `drill`, `drill_rows`, `compare_reports`, `compare_post`, `compare_result`,
  `templates`, `template_get`, `template_gkz`, `template_put`, `report_not_found`, `template_not_found`, `field_not_found`,
  `filter_not_found`, `report_building`, `version_conflict`, `files_exist`, `filter_field_missing`, `report_too_big`, `read_only`; **настоящие** (пишет `spec.py`) —
  `defaults`, `bad_param`, `group_numeric`, `refusals`. Все настоящие ответы 20.7 запишет в тот же файл
  (`BF_REGEN_RESOURCES=1 uv run pytest -q tests/test_resources_contract.py`, как `BF_REGEN_IMPLICIT=1` в Э19) — до этого интерфейс идёт на образцах
  только для сборки форм, а приёмка (20.14 и далее) — на настоящих.

## 15. Трактовки (вне буквы ТЗ и ADR 0027 — приняты в 20.1)

1. Умолчание `cutoffs` — `[null]` («без борта»), не `[0]` как в Э17: ТЗ 8.10 даёт «без борта» только как c = null (BF `cutoff=None`); борт 0 исключает
   блоки без g_б.
2. `balance` — отдельный ключ `{cutoff, off_cutoff?}`; в ADR есть только строки `balance`/`offbalance`. Нет c_з — строк `offbalance` нет.
3. `bench.h` по умолчанию 10 м (ТЗ называет «уступы H = 10 м» в замере, умолчания не даёт); `z_min`/`z_max` ограничивают набор уступов сетки
   (уступы, касающиеся диапазона; целые уступы), а не режут блоки внутри уступа.
4. Единица элемента канонизируется (`g/t`, `gpt`, `г/т` → `g/t`), чтобы хеш не зависел от написания; неизвестная единица не ошибка (решение 10).
5. Группа задаётся строкой (поле) или `{kind: "field" | "bench"}`; `bench` без ключа `bench` в `groups` отбрасывается.
6. Размер результата: предпросмотр — до 20 000 строк в ответе (`truncated`); сохранённый — страницы `GET …/reports/{id}`.
7. `GET /resources/defaults`, `parse_query` (`?template=&cutoff=&page=&size=`) и `kind` ячейки `drill` — вне списка ADR «API», по образцу Э19
   (`GET /implicit/defaults`) и ТЗ Д-36 («группа, борт»).
8. Удаление своего шаблона в ADR не предусмотрено (только `PUT`) — не добавлено; вопрос — оркестратору при 20.4.
9. Текст пустой ячейки формы — «—»; округление текста «от нуля» по десятичной записи числа (как показывает Excel) — уточняет 20.4.
