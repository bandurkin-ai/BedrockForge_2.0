# Э21 — контракт оптимизации карьера (подшаг 21.1, контракт первым)

**Состояние: контракт реализован ядром (21.1–21.15); ответы в `contract_answers.json` — настоящие, записаны ядром.** Источник истины — ADR 0028
(решения 1–12, 19–30, «API», «Точность и приёмка», вопросы № 114–124), ТЗ 8.11, 8.0, 7.35 Д-21, Д-35 (граф); граф — ADR 0025. Код: параметры —
`core/bf_core/mine/spec*.py`, образцы — `core/tests/data/pit/params.json`, тесты — `core/tests/test_pit_spec.py`; маршруты — `routes.py`, `routes_extra.py`,
задания — `job.py`, `dtm_job.py`, хранение — `store*.py`. Ответы заданий и маршрутов — `core/tests/data/pit/contract_answers.json` (все ключи настоящие:
`BF_REGEN_PIT=1 uv run pytest -q tests/test_pit_contract.py` пишет их, `…/test_pit_spec.py` — `defaults`, `validate_new`, `validate_ready`, `bad_param`,
`pit_not_ready`, `refusals`; `surfaces_result` и `solid_result` — настоящие ответы заданий с 21.21б-2). Изменение формы — только через оркестратора.

Отклонения настоящих ответов от образцов 21.1 (интерфейс читает настоящие):
- `template.levels` = K (число уровней шаблона равно K), углы смещений и `error` округлены до 0,01°;
- `defaults.model` дополнен `k_auto` (K для угла 45° по шагу сетки), `terrains` (ЦТМ проекта: `[{id, name}]`), `grid` (сетка модели: `counts`, `size`, …);
- таблица отчёта (`report.table`) расширена столбцами по элементам (`‹элемент›:grade`, `:metal`, `:rec_metal`, накопленные `cum_…`);
- `surfaces_result.folder` — путь папки текстом («Карьер/‹сценарий›», не `folder_id`), у каждой оболочки `replaced`, в итоге `skipped`, `warnings`, `stages`;
  `solid_result` — `folder`, `replaced`, `warnings`, `stages`;
- новый отказ 409 `pit_stale` (§13): рельеф сценария изменён после расчёта — каркас и поверхности отказываются, «Перезапустите расчёт сценария»;
- новое поведение удаления (§15, п. 10) и импорта `.bfggis` (§8): удаление снимает `source.scenario_id` у полей PIT/ORE и ЦТМ/каркасов сценария;
  импорт перенумеровывает `meta.source.scenario_id` ЦТМ и каркасов, так что повторное «Поверхности» заменяет их на месте;
- 422 `pit_too_large` проверяется рано (§13): маршрут `/run` и стадия «Чтение»; `expected_version` заданий поверхностей и каркаса проверяется (409 `version_conflict`).
Префикс маршрутов — `/api/v1/pit` (роутер `mine/router.py`: `routes.py`; регистрация `"mine"` в `api/modules.py` — оркестратор).

Пути: `M/` = `core/bf_core/mine/`, `U/` = `ui/src/modules/pit/`. Единицы денег — «$» (валюта проекта не различается).

## 1. Общие правила

- `X-BF-Token`, Host/Origin — как у всего ядра. Координаты, суммы, значения — float64; пусто — `null` в JSON (NaN в массивах),
  **никогда не 0** (8.0.1). Число на входе — число или текст «0,5» / «0.5». Нет ключа / `null` / `""` — умолчание.
- Ошибка — `{code, message, hint, param?, …}` по-русски с подсказкой (§13). `param` — путь внутри `params`: `"rf.step"`,
  `"elements.1.price"`, `"rocks.types.0.rec.AU"`, `"slopes.table.2.angle"`, `"calendar.rate"`; на уровне запроса — `"params"`, `"name"`,
  `"expected_version"`, `"shells"`, `"shell"`, `"a"`, `"b"`. Нет проекта — 409 `no_project`.
- **Только просмотр** (проект открыт в другом окне): 423 `project_read_only` — у `POST /scenarios`, `PUT`, `DELETE`, `…/run`,
  `…/surfaces`, `…/solid` (пишут объекты, поля, каркасы). Работают: `defaults`, `validate`, `GET`
  сценариев, отчёт, шаблон, `compare`. Как в Э20: проект, открытый только для чтения, не подключён к планировщику, поэтому
  `check` (задание, хотя ничего не пишет) в нём тоже 423 (допущение, §15).
- Задание: ответ `{source, id}` (`source` — `"project"` или `"jobs"`); ход — `GET /jobs/{source}/{id}`; синхронные проверки (404, 409,
  422) — до постановки. Итог несёт `events: [[имя, данные]]`. Запись — последней стадией, целиком или ничего (объект, blobs, поля
  модели, `lineage.record` — одна транзакция); отмена ничего не оставляет.
- Оптимистичная блокировка сценария — `expected_version` (целое ≥ 1, иначе 422 `bad_param {param: "expected_version"}`; другая версия —
  409 `version_conflict {version}`). Сценарий в состоянии `running` — 409 `pit_running` у пишущих запросов.
- События: `pit.changed {kind: "pit_scenario", id, deleted?}` — после записи, запуска, переименования, удаления;
  `objects.changed {kind: "block_model", id}` — после записи полей PIT и ORE; `objects.changed {kind: "wireframe", id}` — каркасы,
  созданные `pit.surfaces` и `pit.solid`.
- Побитность (8.0.2, Т8.11-9): результат не зависит от размера порций, числа потоков (1–8) и от решателя (наименьшее оптимальное
  замыкание единственно); «Перестроить» (тот же `params`) = расчёт с нуля, те же байты `data`. ALGO = `pit-1` (`spec.ALGO`, решение 34).

## 2. Параметры сценария — `spec.parse_params`, `POST /pit/scenarios {name?, params?}`

Каноническая форма (`parse_params(x).canonical()`; пустой `{}` — новый сценарий — настоящая, `contract_answers.json` → `defaults`):

```json
{"model_id": 12 | null,
 "elements": [{"field": "AU", "unit": "g/t" | null, "price": 2000.0 | null, "sales": 5.0, "royalty": 0.0}],
 "density": "SG" | 2.7 | null, "default_density": 2.6 | null,
 "rocks": {"by": null | {"kind": "field", "field": "ROCK"} | {"kind": "rules", "rules": [{"name": "Богатая", "rules": {дерево filters}}]},
           "types": [{"name": "Окисленная", "c_p": 12.0 | null, "c_mr": 0.0, "rec": {"AU": 0.7}, "waste_only": false, "air": false}]},
 "mining": {"c_m0": 2.5 | null, "z_ref": null | 30.0, "h_gl": 10.0, "c_gl": 0.0},
 "rf": [0.3, 0.35, …, 1.5],
 "slopes": {"table": [{"az": 0.0, "angle": 45.0}], "domain_field": null | "DOM",
            "domains": [{"label": "A", "table": [{"az": 0.0, "angle": 40.0}]}], "tolerance": 2.5},
 "k": null | 8,
 "terrain_id": null | 5, "filter_id": null | 3, "restrict": null | {правила 8.7.5},
 "calendar": {"q_ore": null | 1000000.0, "q_mass": null | 2000000.0, "rate": 0.08},
 "prefix": "", "solver": "auto"}
```

| Ключ | Правило, умолчание, диапазон |
|---|---|
| `model_id` | номер блочной модели; **может быть `null`** (новый сценарий; `validate` называет его первым) |
| `elements` | ≤ 20; строка (поле) или `{field, unit?, price?, sales?, royalty?}`; повтор поля — 422. `unit` — `g/t` (`г/т`, `gpt`), `ppm`, `ppb`, `%` (другая — 422 `bad_param {elements.i.unit}`); нет — единица поля модели. `price` P_e (≥ 0, не задана — `null`, «не готов»), `sales` S_e (≥ 0, 0), `royalty` roy_e ∈ [0; 1) (0). Единица цены и сбыта — за единицу металла: «$/унц» для г/т, ppm, ppb, «$/т» для % (`defaults.choices.price_labels`) |
| `density` | число > 0, поле или `null`: меняющееся поле SG, иначе меняющееся поле с ролью `density` (как Э20), иначе `default_density` (одно число на все блоки), иначе 422 `bad_param {density}` при `resolved` |
| `default_density` | число > 0 или `null`; плотность блоков, у которых поле пусто (№ 118); обязательна, если такие блоки есть (`validate`: `missing_density_blocks`), иначе 422 `pit_density_missing {blocks}` при запуске |
| `rocks.by` | `null` — один тип «Все породы» на все блоки; `{kind: "field", field}` — категорийное поле, каждая подпись и «(пусто)» — тип; `{kind: "rules", rules: [{name, rules}]}` — ≤ 50 правил по порядку, первое с f > 0 даёт тип, иначе «Прочие» (имя «Прочие» правилу нельзя) |
| `rocks.types` | ≤ 100, по имени без учёта регистра, в каноне — по имени; `c_p` (≥ 0, затраты на переработку, $/т; `null` — не задан), `c_mr` (c_mр, доплата за добычу руды, ≥ 0, 0), `rec` — `{элемент: 0…1}` (rec_e, извлечение; ключ — поле из `elements`, иначе 422), `waste_only` («только пустая»: R = 0), `air` («воздух»: вне графа и не пустая); обе отметки вместе — 422 `bad_param {rocks.types.i.air}`. Тип с `air` или `waste_only` не требует `c_p` и `rec` |
| `mining` | `c_m0` ≥ 0 ($/т, `null` — не задан), `z_ref` (нет — верх сетки; `resolved` берёт верхнюю границу центров ± полблока), `h_gl` > 0 (10 м), `c_gl` ≥ 0 (0) |
| `rf` | список (1…200 чисел 0,01…5) или `{from, to, step}` (`step` > 0, ≤ 200 значений, шум суммы убирается: 0,3 + 3·0,05 → 0,45); в каноне — список по возрастанию без повторов; нет — 0,30…1,50 шаг 0,05 (25 оболочек). Больше 200 — 422 `bad_param {rf.step}` (диапазон) / `{rf}` (список) |
| `slopes.table` | 1…16 строк `{az, angle}`; `az` ∈ [0; 360) от севера по часовой (у единственной строки можно без `az` — постоянный угол), `angle` ∈ [10; 80]°; повтор азимута — 422; в каноне — по азимуту; нет — 45° |
| `slopes.domain_field`, `.domains` | категорийное поле и ≤ 16 доменов `{label, table}` (в каноне — по подписи); домены без поля — 422; блок без своей таблицы пользуется `slopes.table`; дуги берутся из таблицы домена **нижнего** блока (решение 10) |
| `slopes.tolerance` | 0,5…10°, 2,5° |
| `k` | `null` — авто (наименьшее ≥ 8 и ≤ 20, решение 11) или целое 1…20 |
| `terrain_id` | каркас-ЦТМ рельефа или `null` (без рельефа: предупреждение `no_terrain`; f_b = доля ограничителя). Лист `surface {id, side: "below", by: "frac"}` (решение 5) |
| `filter_id`, `restrict` | ограничитель: фильтр библиотеки И быстрое дерево правил (`filters.normalize_rules`); f_b — произведение долей |
| `calendar` | Q_р `q_ore` и Q_гм `q_mass` (> 0, т/год; `null` — не задано / без ограничения для Q_гм), r `rate` ∈ [0; 0,5] **долей** в год (8 % — 0,08; окно показывает проценты); **не входит в `params_hash`** (решение 19) |
| `prefix` | `\w{0,40}`: поля `<prefix>PIT`, `<prefix>ORE` (№ 120); по умолчанию пусто |
| `solver` | `auto` (HPF), `hpf`, `ortools`; **не входит в `params_hash`** (решение 16); `POST …/run {solver}` перекрывает |

`params_hash` — SHA-256 канонического JSON (ключи по алфавиту, без пробелов, `ensure_ascii=False`) от `hash_params()` =
`canonical()` без `calendar` и `solver` + `algo`. Порядок ключей тела и запись `rf` списком / диапазоном на хеш не влияют.

`PitSpec.resolved(model.fields, (z_lo, z_hi))` подставляет умолчания и сверяет с моделью: имена полей — в написании модели (в том
числе ключи `rec` и подписи типов пород и доменов), единица элемента — из поля, плотность, `mining.z_ref` = `z_hi`; 404
`block_field_not_found {param}`, 422 `bad_param {param}` (поле не числовое / не категорийное, нет единицы, нет плотности).
`resolved().canonical()` — `meta.params` и вход `params_hash()`.

`GET /pit/defaults?model_id=` → `{params, choices, limits, ranges, model?}`: `params` — умолчания нового сценария; `choices` —
`solvers`, `rock_by`, `grade_units`, `grade_labels`, `price_labels`, `rf_default`; `limits` — `elements` 20, `rock_types` 100,
`rock_rules` 50, `sectors` 16, `domains` 16, `shells` 200, `scenarios` 200, `name` 200, `prefix` 40; `ranges` — `rf`, `k`,
`slopes.angle`, `slopes.az`, `slopes.tolerance`, `calendar.rate`, `calendar.q`, `mining.h_gl`, `recovery`, `royalty`, `money`, `density`
(`{lo, hi, lo_open, hi_open, integer, unit}`). С `model_id` добавляется `model` (`spec.model_defaults`): `elements` — поля `*_CUT`,
иначе роль `grade`, с единицей поля, если она одна из четырёх (иначе `null`); `density` — поле по умолчанию; `categories` —
категорийные поля с подписями (породы, домены); `z_ref`; `k_auto` — K авто по шагу сетки; `terrains` — ЦТМ проекта
`[{id, name}]` (кандидаты рельефа); `grid {corner, counts, size, rotation, div}`.

## 3. Готовность — `POST /pit/validate {params}` (синхронно) и `spec.validate`

```json
{"ready": false,
 "missing": [{"param": "elements.0.price", "message": "Не задана цена элемента AU."}],
 "warnings": [{"code": "no_terrain", "param": "terrain_id", "message": "…", "hint": "…"}]}
```

`ready` истинно, когда `missing` пуст; это единственный источник кнопки «Запустить» (команда `pit.run` недоступна, подсказка —
`missing[0].message`) и 422 `pit_not_ready {missing, param}` (`spec.require_ready`, сообщение «Сценарий не готов к расчёту: <первое>»).
Порядок `missing` постоянный: модель → элементы (на элемент: поле, единица, цена) → плотность (`density`; `default_density`, если
есть блоки без плотности) → поле пород и доменов → **типы пород** (на каждый встреченный тип: `rocks.types.<имя>` — тип не задан, затем
`.c_p`, `.rec.<элемент>`; «только пустая» и «воздух» их не требуют) → `mining.c_m0`. Путь типа — **по имени**, а не по индексу
(`rocks.types.Окисленная.rec.CU`): встреченный тип может отсутствовать в `params`. Встреченные типы: подписи поля пород, имена
правил, один «Все породы»; задание запуска подставляет по данным (`context.rock_types`, в том числе «(пусто)» и «Прочие»).
Предупреждения (не мешают запуску): `no_terrain`, `no_calendar` (NPV не будет рассчитан), `price_not_above_sales`, `type_unused`,
`domain_unknown`. Ошибка формы параметров — 422 `bad_param` (не `ready: false`).

## 4. Проверка шаблона — `POST /pit/check {params}` → задание `pit.check` (quick; стадии «Шаблон», «Контроль угла»)

Нужны модель и её сетка: `model_id` обязателен (422 `bad_param {model_id}`); цены и породы не нужны. Модель с переменным шагом —
422 `variable_grid`. Итог (`contract_answers.json` → `check_result`, образец; настоящий — 21.11):

```json
{"model": {"id": 12, "name": "…", "version": 3, "cells_sha": "…", "grid": {"corner": [..], "counts": [..], "size": [10.0, 10.0, 10.0], "order": "F", "rotation": 0.0, "div": [1, 1, 1]}},
 "k": 8, "k_auto": true, "tolerance": 2.5, "levels": 8,
 "domains": [{"label": null, "table": [{"az": 0.0, "angle": 45.0}], "offsets": [[1, 0, 1], …], "n_offsets": 17,
              "error": {"max": 1.44, "mean": 0.58}, "warning": false,
              "phi_eff": {"az": [0, 1, …, 359], "angle": [44.6, …]}}],
 "warnings": [{"code": "angle_error", "param": "slopes", "message": "Ошибка угла 2,05° больше допуска 2,0°.", "hint": "Увеличьте K."}],
 "seconds": 0.02}
```

`offsets` — смещения (Δi, Δj, m) шаблона (m = 1…K уровней вверх, Δ в осях сетки; порядок канонический (m, Δj, Δi)); `domains[i].label`
— `null` для общей таблицы, иначе подпись домена; `phi_eff.angle[a]` — φ_эфф при азимуте `a`° от севера по часовой в **осях проекта**
(поворот сетки вокруг Z уже учтён); `error.max` / `.mean` — max и среднее abs(φ_эфф − φ) по азимутам домена, градусы. Предупреждение
`angle_error` — `error.max` > допуска (не отказ). Тот же объект — `sidecar:template` сценария (`GET /scenarios/{id}/template` → `{template}`
с `domains` и `k`, без `model`).

## 5. Сценарий — `/pit/scenarios`

| Маршрут | Тело / запрос → ответ | Ошибки |
|---|---|---|
| `POST /scenarios {name?, params?}` | → `{id, version: 1, name}`; нет `params` — умолчания (§2); нет `name` — «Сценарий карьера», занятое — суффикс « (2)» | 422 `bad_param`, `too_many_scenarios`; 404 `block_model_not_found`; 423 |
| `GET /scenarios` | `{scenarios: [{id, name, version, state, has_result, params_changed, model: {id, name}, shells, value, graph: {stale, stale_reason, source_lost, pinned}, created_at, updated_at, job_id}]}` | — |
| `GET /scenarios/{id}` | `{id, name, version, state, error, has_result, params_changed, params, params_hash, meta, graph, created_at, updated_at, job_id}` (`meta` — §6; `params` — канон, `resolved`, если есть результат) | 404 `pit_scenario_not_found` |
| `PUT /scenarios/{id} {name?, params?, calendar?, expected_version}` | → `{id, version, name, params_changed}`; новые `params` у сценария с результатом ставят `params_changed: true` (нужен запуск); `calendar` один не меняет `params_hash` и не делает результат устаревшим | 409 `version_conflict`, `pit_running`, `name_taken {param: "name"}`; 422 `bad_param`; 404; 423 |
| `DELETE /scenarios/{id}` | → `{id, deleted: true}` (подтверждение окном, `askImpact`: поля PIT/ORE и ЦТМ остаются, связи в графе — «источник утрачен») | 404; 409 `pit_running`; 423 |
| `POST /scenarios/{id}/run {solver?}` | → задание `pit.run` (heavy) `{source, id}` | 404; 409 `pit_running`, `pit_fields_taken`; 422 `pit_not_ready`, `variable_grid`, `pit_too_large`, `pit_density_missing`, `pit_empty`, `filter_field_missing`; 404 `block_model_not_found`, `block_field_not_found`, `wireframe_not_found`, `filter_not_found`; 423 |
| `GET /scenarios/{id}/template` | `{template}` (§4) | 404; 409 `pit_no_result` |
| `GET /scenarios/{id}/report?calendar=` | §7 | 404; 409 `pit_no_result` |
| `POST /scenarios/{id}/surfaces {shells?}` | §9 | 404; 409 `pit_no_result`, `pit_running`; 422 `bad_param {shells.i}`; 423 |
| `POST /scenarios/{id}/solid {shell, name?}` | §10 | 404; 409 `pit_no_result`; 422 `bad_param {shell}`, `pit_solid_failed`; 423 |
| `POST /compare {a, b}` | §11 (синхронно) | 404 `pit_scenario_not_found`; 409 `pit_no_result`; 422 `bad_param {a, b}` |

`state`: `ready` (есть результат или новый без результата), `running` (от первой до последней транзакции), `error` (`error {code, message,
hint}`). `has_result` — записан ли результат; `params_changed` — параметры правились после последнего запуска (текущий `params_hash` ≠ `meta.params_hash`).
«Перестроить» и «устарел»: граф Д-35 (§12), `lineage.rebuild` запускает `pit.run` с теми же `params` (параметрами результата, `rebuild: true`);
параметры, изменённые после запуска и ещё не запущенные (`meta.draft`), при этом остаются: `params_changed` — по-прежнему `true`. Их снимает «Запустить».

## 6. Запуск — итог задания `pit.run` и `meta` сценария

Стадии: «Чтение», «Экономика», «Шаблон», «Граф», «RF_max», «Оболочки k/N», «Отчёт», «Запись» (последняя пишет объект, blobs, поля PIT и
ORE, `lineage.record` в одной транзакции; 409 `model_changed`, если модель изменилась). Итог (`contract_answers.json` → `run_result`):

```json
{"scenario_id": 17, "name": "Сценарий карьера", "version": 2, "algo": "pit-1", "params_hash": "sha256…", "solver": "hpf",
 "params": {resolved canonical},
 "model": {"id": 12, "name": "…", "version": 3, "cells_sha": "…", "grid": {…}},
 "template": {"k": 8, "k_auto": true, "domains": [{"label": null, "n_offsets": 17, "error": {"max": 1.44, "mean": 0.58}}], "warnings": []},
 "q": 0.01, "objective_error": 12.5,
 "nodes": {"model": 1000000, "air": 20000, "graph": 640000, "cones": 410000},
 "shells": [{"k": 1, "rf": 0.3, "blocks": 1200, "ore_t": 1.5e6, "waste_t": 2.0e6, "value": 3.4e6}],
 "totals": {"ore_t": …, "waste_t": …, "strip": …, "cf": …, "elements": {"AU": {"grade": 1.1, "metal": 1.2e5, "rec_metal": 1.0e5}}},
 "outputs": {"fields": ["PIT", "ORE"], "wireframes": []},
 "control": {…}, "warnings": [{"code": "…", "message": "…", "hint": "…", "param": "…"}],
 "seconds": {"read": 1.0, "econ": 0.4, "template": 0.0, "graph": 1.5, "rf_max": 12.0, "shells": 90.0, "report": 3.0, "write": 1.0, "total": 110.0},
 "events": [["pit.changed", {"kind": "pit_scenario", "id": 17}], ["objects.changed", {"kind": "block_model", "id": 12}]]}
```

`shells[k]`: `k` — номер оболочки 1…N (по возрастанию RF; значение поля PIT), `blocks` — родителей в C_k, `ore_t`, `waste_t` — накопленные при
RF = 1 (руда — ORE), `value` — Σ v_b замыкания C_k при RF_k, $; `totals` — по C_N. `q`, `objective_error` — решение 8 (ошибка цели
≤ N·q/2). `nodes.cones` — узлов после конусов (решение 13). `control` (`sidecar:control`): `air` (число и объём «воздуха»), `empty_cells`
(ячейки разреженной модели — «(пусто)», число и объём), `no_grade {элемент: {blocks, tonnes}}` (NaN → не 0, № 117), `no_density`,
`default_density {blocks, tonnes} | null`, `sub_blocks` (строк в родителях), `closed: true`, `nested: true` (проверка Т8.11-5).

`meta` сценария (решение 20): `v`, `algo`, `params`, `params_hash`, `model {id, name, cells_sha, grid}`, `fields {имя: fp}`, `inputs` (ADR 0025),
`template`, `q`, `objective_error`, `shells`, `totals`, `outputs {fields, wireframes}`, `control`, `solver`, `seconds`, `versions`, `state`, `job_id`.
`data` (Parquet): приращения по оболочкам; `sidecar:benches`, `sidecar:zgrid`, `sidecar:template`, `sidecar:control` — решение 21.

Поля модели: `<prefix>PIT` (i2: номер наименьшей оболочки, 0 — вне карьера и «воздух»), `<prefix>ORE` (i1: 1 — руда при RF = 1, 0 — пустая,
пусто — «воздух» и нет тоннажа), `source {op: "pit", scenario_id}`; занятое чужое имя — 409 `pit_fields_taken` с подсказкой «задайте префикс». Ярлык «Карьер» Э20
(`PIT <= n`) работает без правок.

## 7. Отчёт pit-by-pit — `GET /pit/scenarios/{id}/report?calendar=`

`calendar` — JSON-текст `{"q_ore": 1e6, "q_mass": null, "rate": 0.1}` (или нет — календарь сценария); пересчёт NPV синхронный из `data`, ничего не пишет.
Ответ (`contract_answers.json` → `report_get`):

```json
{"scenario_id": 17, "name": "…", "version": 2, "state": "ready", "algo": "pit-1", "params_hash": "sha256…", "base_rf": 1.0,
 "model": {…}, "calendar": {"q_ore": 1000000.0, "q_mass": null, "rate": 0.1}, "npv_ready": true,
 "elements": [{"field": "AU", "unit": "g/t", "unit_label": "г/т", "metal_unit": "унц", "price_label": "$/унц"}],
 "shells": [Shell…], "totals": {Shell-итог по C_N},
 "series": {"k": [1, …], "rf": […], "ore_t": […], "waste_t": […], "cf": […], "npv_best": […], "npv_worst": […]},
 "table": {"columns": [{"key": "k", "title": "№", "unit": "", "decimals": 0}, …],
           "rows": [{"k": 1, "cells": {"rf": {"value": 0.3, "text": "0,30"}, …}}]},
 "warnings": [{"code": "no_calendar", "message": "…", "hint": "…"}], "seconds": 0.01}
```

**Shell** (приращение ΔC_k = C_k \ C_(k−1) при базовой цене RF = 1, решение 18):
`{k, rf, blocks, ore_t, waste_t, strip, elements: {AU: {grade, metal, rec_metal}}, revenue, cost_mine, cost_process, cost_sales, royalty, cf,
 cum: {blocks, ore_t, waste_t, strip, revenue, cost_mine, cost_process, cost_sales, royalty, cf, elements}, best: {npv}, worst: {npv}}`.
`strip` — W/O = waste_t / ore_t (`null` при O = 0, не 0); `grade` — средняя по тоннажу блоков со значением (`null` без таких), `metal` — металл в руде
(`унц` для г/т, ppm, ppb; `т` для %), `rec_metal` — извлекаемый; `revenue` — выручка при RF = 1 (до сбыта и роялти), `cost_sales` — Σ S_e·металл,
`royalty` — Σ roy_e·(выручка_e − сбыт_e), `cost_mine` — Σ C_m всех блоков приращения, `cost_process` — Σ C_p **только руды**,
`cf = revenue − cost_sales − royalty − cost_mine − cost_process` (тождество проверяется тестом). Σ приращений = `cum` последней оболочки = `totals`.
`best.npv` — лучший случай для n = k (ΔC_1 … ΔC_k по порядку), `worst.npv` — худший (C_k по уровням модели сверху вниз, уступ = уровень, № 122);
оба `null`, если `q_ore` не задан (`npv_ready: false`, предупреждение `no_calendar`). NPV — формула ТЗ дословно (τ_i, t_i; r = 0 — предел CF_i;
τ_i = 0 — CF_i·(1 + r)^(−t_i)); Q_гм пусто — без ограничения. `table` — таблица pit-by-pit **готовым текстом ядра** (знаки, десятичная запятая,
неразрывные пробелы; интерфейс показывает `text` как есть, график берёт `series`).

## 8. Хранение (решения 20–24; подшаг 21.10) — что видит интерфейс

Вид объекта `pit_scenario` (родитель `NULL`, ≤ 200 в проекте): новый (без результата) → правка `params` (`version + 1`) → запись результата на месте
(атомарно). Дерево: «Карьер → Сценарии» (параметры, хеш модели, K, ошибка угла, `LineageBadge`; дети — ЦТМ и каркасы). `.bfggis` туда-обратно
(`mine/remap.py`: модель, фильтр, каркасы рельефа и ограничителя, ЦТМ-выходы; неизвестный id — DEAD_ID; номер сценария в `source.scenario_id` полей PIT/ORE
(вместе с id в их `source.params`) и в `meta.source.scenario_id` ЦТМ и каркасов `pit_surface` / `pit_solid` — иначе повторное «Поверхности» делало бы копии).

## 9. Поверхности — `POST /pit/scenarios/{id}/surfaces {shells?}` → задание `pit.surfaces` (quick)

`shells` — номера оболочек 1…N (нет — все, № 124; числа ≤ N проверяет маршрут: 422 `bad_param {shells.i}`). Из `sidecar:zgrid` строятся
каркасы с `wireframes.dtm` (узлы — центры столбцов в осях проекта, 2 треугольника на клетку; z столбца — низ нижнего вынутого родителя, иначе
рельеф в центре столбца, без рельефа — верх сетки; вне плана рельефа — узел не строится), имя «‹сценарий› — RF 0,85», папка сценария; вход в графе — сценарий.
Итог: `{scenario_id, folder, wireframes: [{shell, rf, wireframe_id, name, nodes, triangles, replaced}], skipped, warnings, events, stages}`; уже построенная оболочка
(ЦТМ, найденная по графу и `meta.source` {op, scenario_id, shell}) — заменяется на месте. Читаются только столбцы z выбранных оболочек. 409 `pit_stale`
(рельеф изменён после расчёта), 409 `version_conflict` (версия сценария не та, что видел маршрут), 422 `pit_too_large` (до чтения z).

## 10. Каркас карьера — `POST /pit/scenarios/{id}/solid {shell, name?}` → задание `pit.solid` (quick)

Тело между рельефом (верх) и z-сеткой оболочки (низ) на одной сетке узлов, общий шов там, где они совпадают; `manifold3d` `check_status`; не замкнут —
422 `pit_solid_failed`. Итог: `{scenario_id, shell, rf, wireframe_id, name, folder, volume, volume_blocks, volume_diff_pct, parts, closed, method, replaced, warnings, events, stages}` —
верх тела — текущий рельеф, поэтому рельеф, изменённый после расчёта, — 409 `pit_stale` (сверка отпечатка рельефа с `meta.inputs`);
`volume` каркаса, `volume_blocks` — Σ V вынутых родителей (сверка по сетке центров: расхождение показывается, не отказ), `parts` — число частей (≥ 1),
`method` — `between` или `difference` (запасной путь — разность `under_solid`).

## 11. Сравнение — `POST /pit/compare {a, b}` (синхронно; A = B допустимо)

Ответ (`contract_answers.json` → `compare_result`):

```json
{"a": {"id": 17, "name": "…", "version": 2, "model": {"id": 12, "name": "…", "cells_sha": "…"}, "solver": "hpf", "params_hash": "…"},
 "b": {…}, "same_grid": true,
 "params_diff": [{"param": "elements.0.price", "a": 2000.0, "b": 2200.0}],
 "shells": [{"rf": 0.85, "status": "both", "k_a": 12, "k_b": 12,
             "a": {"blocks": …, "ore_t": …, "waste_t": …, "cf": …, "npv_best": …, "npv_worst": …},
             "b": {…}, "diff": {"blocks": …, "ore_t": …, "waste_t": …, "cf": …, "npv_best": …, "npv_worst": …}}],
 "blocks": {"only_a": {"blocks": 120, "tonnes": 3.1e5}, "only_b": {"blocks": 0, "tonnes": 0.0}} | null,
 "warnings": [{"code": "grids_differ", "message": "…", "hint": "…"}]}
```

Оболочки совпадают по RF (после `clean`). RF только в одном сценарии: `status` — `only_a` / `only_b`, у второй стороны и `diff` — `null` (пропуск, **не 0**).
`params_diff` — различающиеся ключи `params` (без `calendar`, `solver`). `blocks` — блоки конечного карьера только в A / только в B (число и т) — только при одной
модели и одном `cells_sha`, иначе `null` и предупреждение `grids_differ`; т = объём строки (с FRAC) × плотность сценария — без доли ограничителя и рельефа;
плотность 0, отрицательная или пустая — плотность по умолчанию, как в расчёте, без неё — строка в `no_tonnes`. Другие предупреждения: `stale` (сценарий «устарел»), `calendar_differs`.

## 12. Граф (Д-35, решение 23; `lineage/adapt_pit.py`)

Узел `object:pit_scenario`: fp = SHA-256 `data` + ALGO (без результата — «нет результата»); входы — `object:block_model`, `bm_field` каждого читаемого поля
(содержания, плотность, поле пород и доменов, FRAC), `wireframe` рельефа и листов ограничителя, `object:filter`. Поля PIT и ORE и ЦТМ оболочек —
`graph.record(…, [object:pit_scenario])`. Правка поля содержаний → сценарий и PIT «устарел» с цепочкой; «Перестроить» — `rebuild.register("object:pit_scenario")`
(тот же `params`); «Закрепить» — как ADR 0025.

## 13. Ошибки (`{code, message, hint, …}`; `spec.REFUSALS`; решение 30 + §15)

| Код | HTTP | Когда |
|---|---|---|
| `bad_param {param}` | 422 | форма и диапазон параметров (§2); `expected_version`, `shells.i`, `shell`, `a`, `b` |
| `pit_not_ready {missing, param}` | 422 | запуск без готовых цен, пород, затрат (§3); сообщение — первое `missing` |
| `variable_grid` | 422 | переменный шаг сетки (`tensors`): «Регуляризуйте модель» |
| `pit_too_large` | 422 | узлов > 10 млн или охват не помещается в индекс int32 / 1 ГБ; сетка > 2²⁹ ячеек, план > 2²⁶ колонок, z-сетки N оболочек > 2²⁸ значений (8 Б — 2 ГБ): проверяется рано — в `/run`, стадии «Чтение», перед чтением z (`mine/limits.py`) |
| `pit_density_missing {blocks}` | 422 | блоки без плотности при пустой `default_density` |
| `pit_empty` | 422 | нет блоков с v > 0 при RF_max |
| `filter_field_missing` | 422 | в ограничителе поле, которого нет в модели |
| `pit_solid_failed` | 422 | каркас не замкнут |
| `too_many_scenarios` | 422 | в проекте уже 200 сценариев (трактовка, §15) |
| `pit_internal` | 500 | нарушена вложенность или замкнутость оболочки (Т8.11-5), с журналом; результат не пишется |
| `pit_scenario_not_found`, `block_model_not_found`, `block_field_not_found {param}`, `wireframe_not_found`, `filter_not_found` | 404 | объект не найден |
| `pit_running`, `pit_no_result`, `version_conflict {version}`, `name_taken {param}`, `pit_fields_taken`, `model_changed`, `pit_stale`, `pit_scenario_broken` | 409 | состояние (`pit_no_result`, `filter_not_found`, `pit_scenario_broken` — трактовка, §15); `pit_stale` — рельеф изменён после расчёта (поверхности, каркас): «Перезапустите расчёт сценария»; `pit_scenario_broken` — в проекте нет файла результата сценария (или в нём нет нужной оболочки): «Запустите расчёт сценария заново» (`store.broken`) |
| `not_implemented` | 501 | вид задания не зарегистрирован в этой сборке (`routes_extra.registered`: `pit.surfaces`, `pit.solid`); в собранном ядре не встречается — страховка от сборки без регистрации `bf_core.mine.dtm_job` |
| `out_of_memory` (ошибка задания, не ответ маршрута) | — | `MemoryError` в любом задании карьера: «Не хватило памяти на расчёт карьера», подсказка — закрыть программы или укрупнить модель регуляризацией |
| `project_read_only` | 423 | проект открыт только для чтения (§1) |

## 14. Интерфейсы Python (`M/spec*.py`; другие дорожки читают только их)

```python
# spec.py (+ spec_parts.py, spec_parse.py, spec_bodies.py); без numpy (test_no_numpy_at_import)
parse_params(raw) -> PitSpec                       # формы и диапазоны; неполный сценарий допустим; ApiError 422 bad_param {param}
PitSpec.resolved(fields, z_range) -> PitSpec       # умолчания и сверка с моделью (fields — Model.fields; z_range — (z_lo, z_hi) центров ± полблока)
PitSpec.canonical() / .hash_params() / .params_hash()      # meta.params; без calendar и solver + algo
PitSpec: model_id: int|None, elements: tuple[Element], density: str|float|None, default_density: float|None, rocks: Rocks,
         mining: Mining, rf: tuple[float, ...] (по возрастанию), slopes: Slopes, k: int|None, terrain_id, filter_id, restrict: dict|None,
         calendar: Calendar, prefix: str, solver: str ("auto"|"hpf"|"ortools"), resolved_: bool
Element(field, unit: "g/t"|"ppm"|"ppb"|"%"|None, price: float|None, sales: float, royalty: float)  .k -> k_e   # решение 3
Rocks(by: RockBy|None, types: tuple[RockType]).type_of(name)                    # по имени без учёта регистра
RockBy(kind: "field"|"rules", field: str|None, rules: tuple[RockRule(name, rules: dict)])
RockType(name, c_p: float|None, c_mr: float, rec: dict[str, float], waste_only: bool, air: bool)    # rec — по Element.field
Slopes(table: tuple[Sector(az, angle)], domain_field: str|None, domains: tuple[Domain(label, table)], tolerance)
Mining(c_m0: float|None, z_ref: float|None, h_gl, c_gl);  Calendar(q_ore, q_mass, rate)
validate(params, context=None) -> {ready, missing, warnings}        # context: fields, rock_types, missing_density_blocks
require_ready(params, context=None) -> PitSpec                      # 422 pit_not_ready {missing, param}
grade_k(unit) -> float|None   # g/t, ppm: 1/31,1034768 унц·т⁻¹·(г/т)⁻¹; ppb: 1e-3 от него; %: 0,01 т/т
grade_label(unit), price_label(unit), refuse(code, message=None, hint=None, **extra) -> ApiError, canonical_json(obj) -> str
defaults_json() -> {params, choices, limits};  ranges_json();  model_defaults(fields, z_range) -> {elements, density, categories, z_ref}
parse_check(body) -> PitSpec;  parse_scenario_create(body) -> ScenarioCreate(name, params);  parse_scenario_update(body) -> ScenarioUpdate(name, params, calendar, expected_version)
parse_run(body) -> Run(solver);  parse_report_query(q) -> ReportQuery(calendar);  parse_surfaces(body) -> Surfaces(shells);  parse_solid(body) -> Solid(shell, name);  parse_compare(body) -> Compare(a, b)
ALGO = "pit-1", VERSION, KIND = "pit_scenario", REFUSALS, EMPTY_LABEL = "(пусто)", OTHER_LABEL = "Прочие", ALL_LABEL = "Все породы"
```

## 15. Трактовки (вне буквы ТЗ и ADR 0028)

1. `model_id` и «не заданное» (`price`, `c_p`, `c_m0`, `rec`) — `null` в каноне: форма нового сценария хранится и разбирается без отказа; полноту называет `validate`, а не `parse_params`.
2. Умолчание углов — один сектор 45° (ТЗ задаёт только допуск и диапазон 10…80°); таблица домена с одной строкой без `az` — постоянный угол.
3. `calendar` и `solver` не входят в `params_hash` (календарь — показ, решение 19; решатель не влияет на наименьшее замыкание, решение 16); `prefix`, `tolerance`, `terrain_id` — входят.
4. Ключ `rec` — по полю элемента (не по порядку); при `rocks.by = null` встреченный тип один — «Все породы»; единственный тип списка с другим именем засчитывается как он.
5. Плотность без поля и без найденного по умолчанию, но с `default_density` — одно число на все блоки (иначе `resolved` отказывает 422 `bad_param {density}`).
6. Единица содержания вне g/t, ppm, ppb, % — 422 `bad_param {elements.i.unit}` (в Э20 неизвестная единица допускалась — здесь k_e нужен).
7. Коды сверх списка решения 30: `pit_no_result` (409: отчёт, шаблон, поверхности, каркас, сравнение без результата), `too_many_scenarios` (422, ≤ 200 по решению 20), `filter_not_found` (404, как в Э20), `pit_internal` (500, решение 17).
8. `GET …/report?calendar=` — JSON-текст в строке запроса (ADR называет только «calendar»); `shells` в поверхностях — номера 1…N (значения поля PIT), не RF.
9. Задание `pit.check` в проекте «только просмотр» — 423 (как предпросмотр Э20: нет планировщика); `validate` и `compare` работают.
10. Удаление сценария не удаляет поля PIT/ORE и ЦТМ (как Э20 — отчёт не удаляет поля); связи в графе остаются «источник удалён». С 21.21б-2 они становятся «осиротевшими»:
    у полей PIT/ORE и у ЦТМ/каркасов (`meta.source`) снимается `scenario_id` (номер освобождённой строки на SQLite берёт следующий сценарий — он не получает чужое),
    рёбра графа, где сценарий — источник, теряют номер (`src_id` 0, имя остаётся). Новый сценарий с тем же именем полей — 409 `pit_fields_taken` с подсказкой про префикс.
11. `rate` — доля в год (0,08), не проценты; окно показывает проценты и переводит.
12. Таблица pit-by-pit (`table`) — текст ядра по образцу формы Э20 (`{value, text}`); столбцы и знаки — 21.8, форма ячейки — этот контракт.
13. (21.21б-1, формы ответов не меняет.) Мосты долин рельефа: «воздух» внутри охвата реальных узлов, через который проходит транзитивная дуга шаблона, становится узлом с v′ = 0
    (`PitGraph.real`, `graph.py` BRIDGES; общая таблица углов). Мост не блок: в `PIT`, `ORE`, отчёте, числе блоков и поверхностях его нет. Плотность 0 в блоке — пропуск
    (как пустое поле: `default_density` или 422 `pit_density_missing`). Пустые ячейки разреженной модели обходятся только ниже рельефа (граница уровня по столбцу,
    `econ_empty.py`), их число входит в предел `pit_too_large` до чтения порций. Значения MineLib `.upit` разбираются как десятичные строки (точное масштабирование).
14. (21.21б-2.) Задание любого вида, исчерпавшее память (`MemoryError`), завершается ошибкой `out_of_memory` (`limits.OUT_OF_MEMORY`), а не обрывом процесса; `pit_scenario_broken` (409) — файла
    результата нет в проекте (`store.broken`).
15. Сверка с кодом (21.22): маршруты `routes.py` / `routes_extra.py` — `GET /defaults`, `POST /validate`, `POST /check`, `GET|POST /scenarios`, `GET|PUT|DELETE /scenarios/{id}`,
    `POST /scenarios/{id}/run`, `GET /scenarios/{id}/template`, `GET /scenarios/{id}/report`, `POST /scenarios/{id}/surfaces`, `POST /scenarios/{id}/solid`, `POST /compare`; коды §13 =
    `spec_parts.REFUSALS` + `bad_param`, `variable_grid`, `pit_too_large`, `pit_density_missing`, `pit_empty`, `pit_solid_failed`, `pit_internal`, `pit_scenario_broken`, `not_implemented`,
    `out_of_memory`; в ADR 0028 решение 30 `pit.check` в «только просмотр» — «работает», в коде — 423 (п. 9).

## 16. Команды реестра (решение 29; лента «Карьер», Ctrl+K, ассистент; клавиш нет)

«Сценарий» — `pit.new`, `pit.economics`, `pit.rocks`, `pit.slopes`, `pit.relief`, `pit.calendar`; «Расчёт» — `pit.check`, `pit.run` (недоступна до `ready`,
подсказка — `missing[0].message`); «Результаты» — `pit.report`, `pit.surfaces`, `pit.solid`, `pit.compare`; `pit.rename`, `pit.delete` (confirm, `askImpact`),
`pit.show {scenario_id, shell}` (3D: блоки с фильтром `PIT` ≤ n — показ Э17). Параметры команд плоские, как у Э20: элементы — текст «AU:г/т:2000:5:0,03; CU:%:9000:300»,
RF — список или `rf_from/rf_to/rf_step`, ограничитель — дерево в JSON.

## 17. Фикстуры и тесты

- `core/tests/data/pit/params.json` — `fixture` (поля модели, `z_range`), `cases` (`minimal`, `t8117`, `rocks_field`, `rocks_rules`, `sectors_domains`, `rf_range`,
  `rf_list`, `restricted`: `body` → `canonical`, `resolved`), `invalid` (57 тел → ожидаемый `param` 422), `validate` (тело, контекст → первый `missing`);
  `BF_REGEN_PIT=1` переписывает `canonical` и `resolved`.
- `core/tests/data/pit/contract_answers.json` — `{status, body}`, все ключи — настоящие ответы ядра (`_note`): `defaults`, `validate_new`, `validate_ready`, `bad_param`,
  `pit_not_ready`, `refusals` пишет `spec` (`test_pit_spec.py`); остальные — `test_pit_contract.py` (`check_*`, `scenario_*`, `run_*`, `template_get`, `report_get`,
  `compare_result`, `surfaces_result`, `solid_result`, отказы маршрутов).
- pytest: `test_pit_spec.py` (этот подшаг); далее `test_pit_{econ,template,flow,hpf,shells,report,fields,surfaces,solid,store,job,compare,contract}.py`.
