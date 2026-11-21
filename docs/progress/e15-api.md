# Э15 — контракт API `/api/v1/intercepts` (черновик для интерфейса; удаляется в 15.22)

Источник истины — ADR 0021 §4–12, ТЗ 8.5.4; разбор тел — `core/bf_core/intercepts/spec.py` (15.9), маршруты —
`intercepts/router.py` (15.10: `tt`, `run`, результаты, удаление, перестроение; задания — 15.11–15.12; предпросмотры
— 15.11–15.12; `orientation/plane` — 15.13). Изменение формы ниже — только через оркестратора.

## Общие правила

- Заголовок `X-BF-Token`, Host/Origin — как у всего ядра (`api/security.py`). Числа — float64, пропуск — `null`
  (никогда не 0).
- Ошибка — `{code, message, hint, param?, …}`; `message`, `hint` — по-русски. `param` — ключ тела или путь внутри
  ориентировки: `"orientation.0.alpha"`, `"orientation.1.attitudes.3.code"`, `"orientation.0.wireframe_ids"`.
- Запись в проект «только просмотр» (`POST /tt`, `/run`, `/results/{id}/rebuild`, `DELETE /results/{id}`) — 423
  `project_read_only`. Нет открытого проекта — 409 `no_project`.
- Задание: ответ `{source, id}` (как Э12); ход и итог — `GET /jobs/{source}/{id}`.
- Каркасы в заданиях (15.13): читаются по одному (каждый уходит из кеша после чтения); сообщения хода — «Пересечения
  стволов: каркас N из M», «Ближайшие треугольники: каркас N из M», «Осреднение нормалей: каркас N из M». Ключи
  геометрии каркасов — во `inputs.meshes` записи и в хеше: каркас заменён (то же имя, другая геометрия) — запись
  «устарела» («изменён каркас «…»»), «Перестроить» берёт нынешний. d_max > 50 м на больших каркасах — в `journal`
  задания и в `notes` предпросмотров оценка «поиск ближайшей точки может занять до N с» (при оценке от 10 с);
  диапазон ТЗ не сужается. Керн — таблица «события» или интервальная (глубина — FROM / середина).

## Ориентировка (`orientation`) — общая часть `tt` и `run`

Список из 1–3 источников по приоритету (интервал берёт нормаль первого, который её дал); один вид — не дважды.

```json
{"source": "global", "alpha": 30, "dipdir": 120}
{"source": "domain", "field": "DOMAIN", "table": "12", "attitudes": [{"code": "QZ", "alpha": 30, "dipdir": 120}]}
{"source": "domain", "wireframe_ids": [5, 6], "attitudes": [{"code": "Тело 1", "alpha": 30, "dipdir": 120}]}
{"source": "mesh", "wireframe_ids": [5, 6], "d_max": 50, "r_n": 0}
{"source": "core", "table": "14", "alpha": "ALPHA_K", "beta": "BETA_K", "line": "boh", "d_core": 10}
```

| Поле | Значение | Ошибки |
|---|---|---|
| `alpha`, `dipdir` | α 0–90°, Aп 0–360° (360 → 0); число или текст «45,5» | 422 `bad_param` |
| `domain.field`, `domain.table?` | поле той же таблицы (нет `table`) или другой интервальной (id текстом или имя) | 422 `bad_param` (не выбрано), 422 `field_not_found`, 404 `table_not_found` |
| `domain.wireframe_ids` | замкнутые каркасы-домены (код — имя каркаса), как Э12 | 422 `wireframe_not_found`, `wireframe_open` |
| `domain.attitudes` | 1–1000 строк `{code, alpha, dipdir}`; код числового поля — число («2,5» → `"2.5"`), текст — без пробелов по краям; код не повторяется | пусто — 422 `no_orientation`; 422 `bad_param` |
| `mesh.wireframe_ids` | 1–16 каркасов или поверхностей (замкнутые и незамкнутые), порядок — приоритет | пусто — 422 `no_orientation`; 422 `wireframe_not_found`, `bad_param` |
| `mesh.d_max` | 0–1000 м (50); 0 — только пересечения | 422 `bad_param` |
| `mesh.r_n` | 0–200 м (0) | 422 `bad_param` |
| `core.table` | таблица набора любого вида («события», интервальная, пробы) | пусто — 422 `no_orientation`; 404 `table_not_found` |
| `core.alpha`, `core.beta` | числовые поля α_к, β_к | не выбрано — 422 `no_orientation`; не число — 422 `bad_param`; 422 `field_not_found` |
| `core.line` | `boh` (по умолчанию) / `toh` | 422 `bad_param` |
| `core.d_core` | 0–1000 м (10) | 422 `bad_param` |

Простая форма команды (`intercept.tt` / `intercept.run` из `Ctrl+K` и ассистента; ADR §9) — если `orientation` нет:
`source: global | domain | mesh | core` и плоские поля `alpha, dipdir` · `domain, domain_table?, domain_wireframes?,
attitudes` («код=α/Aп; …» или список) · `wireframe_ids` (список или «5,6»), `d_max?, r_n?` · `core_table, core_alpha,
core_beta, core_line?, d_core?`; `fallback_alpha` / `fallback_dipdir` — вторым источником «глобальная». Ошибки —
с путём `orientation.N.…`; неизвестный `source` — `param: "source"`.

## `POST /intercepts/tt` — «Истинная мощность»

```json
{"set_id": 3, "table": "12", "orientation": [ … ], "eps": 0.1, "step": 1, "angles": true}
```

| Поле | Значение |
|---|---|
| `set_id` | число; нет — 404 `set_not_found` |
| `table` | интервальная таблица набора (пробы, композиты, «Рудные интервалы»…): id текстом или имя; 404 `table_not_found` `{param: "table"}` |
| `orientation` | 1–3 источника; пусто — 422 `no_orientation` `{param: "orientation"}` |
| `eps` | ε_в 0,01–0,5 (0,1) |
| `step` | часть ствола 0,1–10 м (1) |
| `angles` | `true` (по умолчанию) — ещё ALPHA, DIPDIR, HOLE_THETA, HOLE_AZ; не логическое — 422 `bad_param` |

Ответы: 200 `{source, id}` задания `intercept.tt` (heavy); 422 `bad_param` / `no_orientation` `{param}`; 404; 409
`column_exists` `{param: "table", name}` — в таблице есть поле TT, TT_FLAG, TT_SRC (или углы) не от прежней
«Истинной мощности» этой таблицы; 423; пока задание не подключено в сборке (до 15.11) — 422 `not_ready`.
Повтор на той же таблице перезаписывает свои поля и свою запись.

Пишет в таблицу источника одной транзакцией: `TT` (м), `TT_FLAG` (сумма битов: 1 — почти параллельна, 2 — нет
ориентировки, 4 — керн не пересчитан, 8 — нет траектории), `TT_SRC` (текст: «глобальная», «домен», «каркас:
пересечение», «каркас: ближайшая», «керн: замер», «керн: ближайший», «нет»), при `angles` — `ALPHA`, `DIPDIR`,
`HOLE_THETA`, `HOLE_AZ` (°). Пусто — `null`.

Итог задания (`job.result`): `{set_id, result_id, table_id, name, columns: [..], rows, fresh, summary, journal,
stages, events}`; `summary` = `report` записи; `stages` — секунды стадий `{read, trajectories, hash, kernel, write}`
(при `fresh: true` — без `kernel`, `write`; `summary` тогда нет). Повтор на той же таблице — та же запись (её `id`).

`report` записи `tt` (15.11): `counts {src: {TT_SRC: строк}, flags: {"1", "2", "4", "8": строк}}`, `rows`, `length`
(Σ LENGTH строк в расчёте, м), `tt` (Σ TT, м), `with_tt` (строк с TT), `excluded` (вне расчёта: нет глубины, TO ≤
FROM), `skipped` (без FROM или TO — не прочитаны, поля пусты), `capped` (интервалов с увеличенной частью ствола),
`absent` (виды источников, которых не было), `sources` (по источнику цепочки `{source, rows, …}`),
`journal`, `versions {numpy, numba}`, `seconds`, `edits` (`[]`), `written`. NaN — `null`. Таблица-результат с полями
TT (композиты, «Рудные интервалы») при перестроении теряет их: «Истинная мощность» — в `lost` 409 `dependents_lost`.

## `POST /intercepts/run` — «Рудные пересечения»

```json
{"set_id": 3, "table": "12", "field": "AU", "cutoff": 1, "include_cutoff": 1.5, "l_min": 2, "min_by": "length",
 "waste_max": 2, "missing": "waste", "density": null, "fields": ["CU"], "orientation": [ … ], "eps": 0.1, "step": 1,
 "name": "Рудные пересечения", "replace": false}
```

| Поле | Значение |
|---|---|
| `table`, `set_id` | как `tt` |
| `field` | числовое поле содержания; не выбрано / не число — 422 `bad_param`; 422 `field_not_found` |
| `cutoff` | борт c — конечное число больше нуля (ТЗ 8.5.4, как ядро правила); нет, 0 или меньше — 422 `bad_param` |
| `include_cutoff` | c2 «в том числе», `> cutoff`; `null` / пусто — нет |
| `l_min` | 0–100 м (2): L_min при `min_by = length`, m_min при `min_by = tt` |
| `min_by` | `length` (по умолчанию) / `tt` |
| `waste_max` | L_п 0–50 м (2) |
| `missing` | `waste` (по умолчанию: пустая порода с g = 0, длина — в MISS_LEN) / `break` (разрыв) |
| `density` | числовое поле плотности; нет — без плотности |
| `fields` | поля содержаний сверх `field` (список или «CU, AG»); нет — все числовые, кроме плотности, служебных и полей «Истинной мощности» (TT, TT_FLAG, TT_SRC, ALPHA, DIPDIR, HOLE_THETA, HOLE_AZ); `[]` — только `field` |
| `orientation` | 0–3 источника; пусто — TT пусто, TT_SRC «нет» (`min_by = tt` тогда проверяет длину, бит 2) |
| `eps`, `step` | как `tt` |
| `name` | имя новой таблицы (≤ 200 знаков; «Рудные пересечения»); имя источника — 422 `name_is_source` |
| `replace` | `true` — заменить одноимённую таблицу (после «Заменить?») |

Ответы: 200 `{source, id}` задания `intercept.run` (heavy); 422 / 404 как выше; 409 `table_exists`
`{name, suggested, lost}` — окно спрашивает «Заменить?» (`lost` — операции над той таблицей, которые не
повторить) и повторяет с `replace: true` или с `name: suggested`; 423.

Таблица (вид `interval`): HOLE, FROM, TO, LENGTH, TT, TT_FLAG, TT_SRC, ALPHA, DIPDIR, HOLE_THETA, HOLE_AZ, содержания
(`field` первым, затем `fields`), LEN_G, TT_G, WASTE_LEN, N_INT, X, Y, Z, INCL, LABEL, ID, KIND («основное»,
«в том числе»), MISS_LEN. Отображение по стволу «Рудные пересечения» — сохранённая колонка (striplog) набора с
`settings.origin = {op: "intercepts", table: name_key}`.

Итог задания: `{set_id, result_id, table_id, name, chart_id, rows, columns, replaced, fresh, summary, journal,
stages, events}`; `columns` — имена таблицы от HOLE; `summary` = `report` записи; `stages` — секунды `{read,
trajectories, hash, kernel, write}` (при `fresh: true` — без `kernel`, `write`, без `summary`).

`report` записи `intercepts` (15.12): `counts {src, flags}` (по всем строкам таблицы), `rows`, `intercepts`,
`included`, `length` (Σ LENGTH основных, м), `tt` (Σ TT основных; `null` — ни у одного нет), `len_g`, `mean` (=
`len_g / length`; `null` — нет пересечений), `waste_len`, `miss_len`, `excluded` (проб вне правила: нет глубины,
TO ≤ FROM, перекрытия), `skipped` (без FROM или TO — не прочитаны), `unit` (поля содержания), `field`, `journal`,
`versions`, `seconds`, `edits` (`[]`), `written` (`[]`), `table_columns`. NaN — `null`.

## Предпросмотры (синхронно, одна скважина; 15.11–15.12)

`POST /intercepts/tt/preview`, `POST /intercepts/run/preview` — тело `{params, hole_id}`: `params` — тело `tt` /
`run` (без `replace`, `name` не учитывается), `hole_id` — `dh_holes.id`. Ошибки разбора — как у задания; нет
скважины — 422 / 404 `hole_not_found`. Ничего не пишется.

`tt/preview` →
```json
{"hole": "СКВ-1",
 "rows": {"from": [..], "to": [..], "length": [..], "TT": [..], "TT_REL": [..], "TT_FLAG": [..], "TT_SRC": [..],
          "ALPHA": [..], "DIPDIR": [..], "HOLE_THETA": [..], "HOLE_AZ": [..]},
 "counts": {"src": {"глобальная": 10, "нет": 2}, "flags": {"1": 0, "2": 2, "4": 0, "8": 0}},
 "mesh_skipped": false, "set_summary": null, "notes": ["…"]}
```
`TT_REL` = TT / LENGTH (окраска столбца; LENGTH ≤ 0 или TT пусто — `null`); углы — при любом `angles`; строки — в
порядке скважины по FROM; `counts` — по скважине; `mesh_skipped` — каркасов в списке больше 2 млн треугольников, ориентировка
по каркасу не считалась, цепочка перешла к следующему источнику (в `notes` — строка об этом); `set_summary` — `counts` по всей таблице при ≤ 200 000 строк, иначе `null`.

`run/preview` →
```json
{"hole": "СКВ-1", "unit": "г/т",
 "samples": {"from": [..], "to": [..], "values": {"AU": [..]}, "ore": [true, false, ..]},
 "candidates": [{"from": 1, "to": 6, "length": 5, "tt": 4.1, "grade": 1.16, "kind": "основное",
                 "accepted": true, "reason": null}],
 "result": {"from": [..], "to": [..], "values": {"ID": [..], "KIND": [..], "LENGTH": [..], "TT": [..],
            "AU": [..], "LEN_G": [..], "TT_G": [..], "WASTE_LEN": [..], "MISS_LEN": [..], "N_INT": [..],
            "INCL": [..], "TT_FLAG": [..], "TT_SRC": [..], "LABEL": [..]}},
 "summary": {"intercepts": 1, "included": 1, "length": 5, "len_g": 5.8},
 "set_summary": null, "notes": ["…"]}
```
`reason` отказанного кандидата: «короче L_min», «ИМ меньше m_min»; `set_summary` — `summary` по набору при ≤ 200 000
проб, иначе `null`. Ядро (15.12): `samples.values` — поле содержания, прочие поля таблицы и плотность; кандидаты —
все кандидаты правила, «в том числе» сразу после своего основного; `tt` кандидата — при `min_by = tt` та, по которой
принимали, иначе — ориентировки на его [FROM; TO] (нет — `null`); `result.values` — все колонки таблицы после HOLE,
FROM, TO (шире примера); `summary` и `set_summary` — в форме `report` записи без `journal` и версий (шире примера);
`mesh_skipped` — как у `tt/preview`.

## `POST /intercepts/orientation/plane` (15.13)

`{wireframe_ids: [5, 6]}` → `{alpha, dipdir, spread, triangles, area}`: средняя плоскость (главный собственный вектор
Σ S·n·nᵀ, n_z ≥ 0), `spread` — средневзвешенный по площади угол между нормалями треугольников и нормалью плоскости,
° (0 — плоская поверхность; у закрытой пластины — доля боковых граней × 90°), `area` — м². Сделано в 15.13; проект
«только просмотр» — можно (ничего не пишет). 422
`wireframe_not_found`, `bad_param` (не больше 16 каркасов; не больше 2 млн треугольников в сумме — `MESH_TRIANGLES`
предпросмотра, 15.20), `no_orientation` (нет треугольников с площадью).

## `GET /intercepts/codes?set_id=&table=&column=` (15.20)

«Заполнить кодами поля» таблицы доменов → `{codes: ["Д01", "Д02", …], total: 1500, truncated: true}`: все различные
непустые значения поля по строкам таблицы, записанные так, как ориентировка записывает код домена (`orient.py`):
текст — без пробелов по краям, число — `.15g` (−0 = 0); отсортированы (числа — по значению, тексты — как строки),
не больше 1000 (`CODES_MAX`); `total` — всех различных кодов, `truncated` — `total > 1000` (окно пишет «показаны
1 000 из N»). `table` — id готовой таблицы набора (число ASCII), `column` — ключ поля (`c5`) или его имя. Только
чтение; проект «только просмотр» — можно. 404 `set_not_found`, `table_not_found`, `column_not_found`. Заменяет
`GET …/tables/{table}/stats` → `top` (там только 10 самых частых).

## Результаты (15.10)

- `GET /intercepts/results?set_id=` → `{results: [..]}` — строки операций `tt` и `intercepts` в форме
  `GET /composites/results` (общий список, который показывает и их): `{id, set_id, table_id, table, op, op_title,
  makes_table, name, params, inputs, sha256, algo, report, written, job_id, created_at, stale, reason}`.
- `DELETE /intercepts/results/{id}` → как `DELETE /composites/results/{id}`: `{deleted, set_id, deleted_tables,
  changed, deleted_charts}`; `tt` — поля скрываются (запись журнала `column`, «Отменить» вернёт), `intercepts` —
  таблица и её отображение. Не своя операция или нет записи — 404 `result_not_found`; 409 `result_busy`.
- `POST /intercepts/results/{id}/rebuild` `{drop_lost?}` → `{source, id}`: то же задание с параметрами записи на
  текущих таблицах (найденных по имени) и каркасах (по имени и геометрии); `intercepts` — с `replace: true` и
  именем нынешней таблицы. 404 `result_not_found` (и для чужих операций), 409 `source_gone`, `result_busy`,
  `dependents_lost {lost}` (повтор с `drop_lost: true`), `column_exists`; 422 параметров (разбор заново); 423;
  до 15.11–15.12 — 422 `not_ready`. Тот же хеш и `algo` — расчёт не повторяется, «устарел» снимается.

Запись `dh_results`: `op` `tt` (`table_id` — таблица с полями) / `intercepts` (`table_id` — новая таблица);
`name` «Истинная мощность · ‹Таблица›» / имя таблицы; `params` — канонический `spec.body` (таблицы — id текстом,
поля — сохранёнными именами, все числа заполнены, `orientation` — списком); `inputs` — `results.make_inputs`
(таблицы: источник, затем таблицы ориентировки по порядку; `set`; `meshes` — каркасы ориентировки по порядку);
`algo` `tt-1` / `icpt-1`; `report.written` (`tt`: `[{table: name_key, columns: [TT, …]}]`), `report.table_columns`
(`intercepts`), сводка: строк по TT_SRC и по битам, Σ LENGTH, Σ TT, пересечений, Σ LEN_G, среднее, WASTE_LEN,
MISS_LEN, исключённые строки, версии numpy и numba, `seconds`.

События: `drillholes.changed {set_id, table, rev}`, `composites.changed {set_id}`, `objects.changed {kind: "chart",
set_id, id, deleted?}` (отображение «Рудные пересечения»).
