# Э14 — фактические формы ответов `/api/v1/qaqc` (контракт для дорожки И; удаляется в 14.27)

Снято с работающего ядра (генератор `tools/gen/qaqc.py`, 400 контрольных записей). Списки в примерах урезаны до
двух элементов. Ошибки везде — `{code, message, hint, param?}` по-русски. Пропуск — `null`, не 0.
Статусы: 0 — норма, 1 — предупреждение, 2 — отказ, −1 — не оценён. Даты рядов — мс с 1970 (UTC, дата как записана),
даты партий и данных — ISO-тексты.

## Настройка и справочник CRM

| Маршрут | Ответ |
|---|---|
| `GET /config` | `{version, types: {"ORIG": "ORIG", …, "STD": "CRM", "STANDARD": "CRM", "BLK": "BLANK"}, dl: [{element, lab \| null, value, unit}], limits: {sigma_warn, sigma_fail, run, k_blank, k_dup, th_group, hard_p, hard_c, hard_f, hard_ump, hard_share, n_boot, seed}, empty_is_orig}`; без записи — `version: 0` и умолчания |
| `PUT /config {types, dl, limits, empty_is_orig, expected_version}` | то же, `version + 1`. Коды TYPE хранятся обрезанными, заглавными. 409 `version_conflict {current}`, 422 `bad_param {param: "limits.run" \| "dl[0].value" \| "types.STD" …}`, 423 |
| `GET /crm` | `{version, rows: [{crm_id, element, mean, sd, unit, source}]}` |
| `PUT /crm {rows, expected_version}` | то же; 422 `bad_crm {row (с 0), param: "rows[1].sd"}` («строка N» в тексте — с 1), `too_many_rows` (> 5000) |
| `POST /crm/read {path, sheet?}` | `{rows: [...], problems: [{row, message}], columns: {crm_id: "Standard", mean: "Mean", …}}` — ничего не пишет; 422 `bad_crm_file {columns}` |

Событие `qaqc.changed {what: "config" | "crm", version}`.

## Данные QAQC

| Маршрут | Ответ |
|---|---|
| `GET /data` | `{items: [data], limit: 50}` по названию |
| `GET /data/{id}` | см. ниже; 404 `qaqc_data_not_found` |
| `PUT /data/{id} {name, expected_version}` | data; 409 `name_taken`, `version_conflict {current}`; 423 |
| `DELETE /data/{id}` | `{id, deleted: true}`; 423 |
| `POST /data/inspect {kind: "file", path, sheet?, encoding?, delimiter?, header_row?}` | `{kind: "file", columns: [{name, kind: "number" \| "text", samples: [≤ 5]}], rows (оценка; точно у DAT), rows_exact, mapping, preview: [[…] ≤ 50], info: {format, encoding, delimiter, header_row, sheet, sheets}, summary, warnings}` |
| `POST /data/inspect {kind: "table", set_id, table}` | то же с `kind: "table"`, `rows` — точно, `info: {set_id, table_id, set_name, table_name, rev}`; колонки — поля таблицы и псевдоколонки `HOLE`, `FROM`, `TO`; единицы элементов — из полей таблицы |
| `POST /data {name?, source, mapping, replace_id?}` | **202** `{job_id, source, id}` задания `qaqc.import`; 422 `no_sample_id`, `no_type`, `no_elements`, `field_not_found {param}`, `too_many_elements`, `too_many_data`; 404 `file_not_found`; 409 `name_taken`; 423 |
| `POST /data/{id}/refresh` | 202 то же — источник и сопоставление данных (`replace_id = id`); 404 `source_missing` — таблицы нет |
| `GET /data/{id}/values?field=&q=&limit=` | `{values: [{value, n}], more, empty}` по значению, `limit` ≤ 1000 (200 по умолчанию); поля — имена колонок записей (`lab`, `batch`, `crm_id`, `type_code`, `hole`, `sample_id`, `seq`, `x:<имя>`, `v:<E>`); 422 `field_not_found` |

Данные (`GET /data/{id}`):

```json
{"id": 1, "name": "Контроль", "version": 1, "rows": 620,
 "elements": [{"name": "AU", "unit": "ppm"}, {"name": "CU", "unit": "%"}],
 "fields": [{"name": "sample_id", "kind": "text"}, {"name": "hole", "kind": "text"}, {"name": "parent_id", "kind": "text"},
            {"name": "seq", "kind": "number"}, {"name": "date", "kind": "date"},
            {"name": "v:AU", "kind": "number", "element": "AU"}, {"name": "x:ZONE", "kind": "text", "title": "ZONE"}],
 "labs": ["EXT", "LAB1"], "batches": 13, "date_from": "2024-02-10", "date_to": "2024-11-30",
 "type_codes": [{"code": "BLANK", "n": 60, "type": "BLANK"}, {"code": null, "n": 3, "type": "ORIG"}, {"code": "XX", "n": 2, "type": null}],
 "source": {"kind": "file", "name": "контроль.csv", "path": "C:\\…\\контроль.csv", "sha256": "4a55…", "header_row": 1},
 "mapping": {"v": 1, "sample_id": "SAMPLE_ID", "hole": "HOLE", "type": "TYPE", "parent_id": "PARENT_ID", "crm_id": "CRM_ID",
             "lab": "LAB", "batch": "BATCH", "date": "DATE", "seq": "SEQ",
             "elements": [{"column": "AU_ppm", "name": "AU", "unit": "ppm", "dl_column": null}], "extra": []},
 "stale": false, "source_missing": false, "sha256": "3d89…",
 "counts": {"rows": 620, "no_parent": 0, "dup_ids": 0, "empty_ids": 0, "bad_dates": 0, "empty_dates": 0, "empty_seq": 0,
            "below_dl": 160, "above_dl": 0, "bad_numbers": 0,
            "elements": {"AU": {"numbers": 618, "missing": 2, "below": 40, "above": 0, "bad": 0}}},
 "notes": [], "author": null, "created_at": "…", "updated_at": "…"}
```

Источник-таблица: `source: {kind: "table", set_id, table_id, set_name, table_name, rev}`; `stale: true` — `rev` таблицы
вырос или таблицы нет (`source_missing: true`). Источник-файл хранит путь — «Обновить» перечитывает тот же файл.

Результат задания `qaqc.import`: `{data_id, name, rows, replaced, counts (как выше, без rows), notes, events:
[["objects.changed", {kind: "qaqc_data", id}]]}`. Событие публикуется и по `PUT`/`DELETE` (`deleted: true`).

## Расчёты: `POST /calc/{section}`

Тело: `{data_id | report_id, element, filter?, group?, kind?, offset?, limit?, max_points?, samples?}`. Раздел — имя
из `calc.SECTIONS`: `summary`, `standards`, `blanks`, `batches` (+ `duplicates`, `external` после 14.5–14.7). С
`group` ряд прореживается до `max_points` (по умолчанию 100 000; все предупреждения и отказы остаются, порядок ряда
сохранён). С `report_id` записи, настройка, справочник и фильтр — из снимка отчёта, `filter` тела не действует.
Ошибки: 404 `unknown_section`, `qaqc_data_not_found`, `qaqc_report_not_found`; 422 `field_not_found {param:
"element"}`, `bad_param {param: "filter.labs" | "limit" | "max_points" …}`.

Общая шапка каждого ответа:

```json
{"element": "AU", "unit": "ppm",
 "counts": {"rows": 620, "filtered": 620, "control": 400, "unmapped": 0, "missing": 1},
 "limits": {"sigma_warn": 2.0, "sigma_fail": 3.0, "run": 9, "k_blank": 5.0, "k_dup": 5.0, "th_group": 11, "hard_p": 10.0,
            "hard_c": 20.0, "hard_f": 30.0, "hard_ump": 10.0, "hard_share": 90.0, "n_boot": 1000, "seed": 12345},
 "notes": []}
```

`standards` (с `group {crm, lab}` — `series` и `thinned`; группа пропала под фильтром — `series: null`):

```json
{"groups": [{"crm": "CRM-01", "lab": "LAB2", "state": "ok", "n": 2, "missing": 0, "mu": 0.182, "sd": 0.00728, "unit": "ppm",
             "mean": 0.19095, "std": 0.01945, "bias": 4.9176, "bias_class": 0, "norm": 1, "warn": 0, "fail": 1,
             "pair_fails": 0, "run_flags": 0, "swaps": 1}],
 "series": {"row": [226, 344], "date": [1713225600000, 1715299200000], "seq": [27.0, 45.0], "batch": ["B000004", "B000006"],
            "x": [0.1715, 0.2104], "z": [-1.442, 3.901], "status": [0, 2], "rule": [0, 4], "ma": [null, null],
            "swap": [null, ["CRM-02"]]},
 "thinned": {"shown": 2, "total": 2}}
```

`state`: `ok`, `no_reference`, `unit_mismatch`; `rule` — биты 1 пара, 2 серия, 4 подмена; `swap[i]` — названия CRM,
на которые похожа проба, или `null`; ряд упорядочен по (DATE, SEQ, строка).

`blanks` (с `group {lab}`):

```json
{"groups": [{"lab": "LAB1", "n": 10, "missing": 0, "dl": 0.01, "limit": 0.05, "mixed": false, "norm": 10, "fail": 0, "not_assessed": 0}],
 "series": {"row": [5, 10], "date": [1707523200000, 1707523200000], "seq": [6.0, 11.0], "batch": ["B000000", "B000000"],
            "x": [0.005, 0.005], "status": [0, 0], "prev": [0.3145, 1.052], "limit": [0.05, 0.05]},
 "carryover": {"n": 9, "pearson": 0.6752},
 "thinned": {"shown": 10, "total": 10}}
```

`dl: null, mixed: true` — у лаборатории разные DL (общей линии порога нет); `limit[i]` — порог пробы.

`batches` (`offset`, `limit` ≤ 5000; `total` — по всему фильтру; по первой дате):

```json
{"total": 13, "failed": 2, "not_assessed": 1, "no_batch": 0, "offset": 0,
 "batches": [{"lab": "LAB1", "batch": "B000000", "date_from": "2024-02-10", "date_to": "2024-02-10", "n": 50, "crm": 8,
              "blanks": 4, "crm_fail": 0, "blank_fail": 0, "assessed": 12, "status": 0}]}
```

`summary`:

```json
{"types": {"ORIG": 220, "CRM": 120, "BLANK": 60, "DUP_F": 60, "DUP_C": 60, "DUP_P": 60, "UMP": 40},
 "unmapped_codes": [{"code": "STD", "n": 14}],
 "standards": {"groups": 50, "n": 120, "missing": 0, "norm": 114, "warn": 4, "fail": 2, "not_assessed": 0, "pair_fails": 0,
               "run_flags": 0, "swaps": 2, "bias_attention": 4, "bias_bad": 1},
 "blanks": {"groups": 3, "n": 60, "missing": 0, "norm": 60, "fail": 0, "not_assessed": 0},
 "batches": {"total": 13, "failed": 2, "not_assessed": 1, "no_batch": 0}}
```

`duplicates` (`kind`: `DUP_F` | `DUP_C` | `DUP_P`) и `external` (`group {lab_main, lab_check}`) — формы ADR 0020 п. 10 в
реализации К (14.5–14.7): дополняются после их влития.

## Записи: `POST /records`

Тело: `{data_id | report_id, rows: [строки файла] (≤ 1000) | batch, lab}` (партия — ≤ 5000 записей, по (SEQ, строка);
сравнение названий без регистра). Записи `rows` — в порядке запроса.

```json
{"records": [{"row": 0, "sample_id": "S0000227", "parent_id": "S0000447", "hole": "DH058", "type_code": "DUP_F", "type": "DUP_F",
              "crm_id": null, "lab": "LAB1", "batch": "B000000", "parent_lab": "LAB1", "date": 1707523200000, "seq": 1.0,
              "parent_row": 39, "prev_row": null,
              "values": {"AU": {"v": 1.079, "q": 0, "p": 0.8945, "b": null}, "CU": {"v": 0.2532, "q": 0, "p": 0.251, "b": null}},
              "extra": {}}],
 "total": 1, "more": false}
```

`q`: 0 — число или пусто, 1 — «<DL» (`v` = DL/2), 2 — «>x»; `dl` в `values` — если DL сопоставлен полем;
`type: null` — код «не распознано».

## Отчёты: `/reports…` (14.13)

| Маршрут | Ответ |
|---|---|
| `GET /reports` | `{items: [report без summary], limit: 200}` по названию, вместе со строящимися (`state: "building"`) |
| `GET /reports/{id}` | report + `summary`; 404 `qaqc_report_not_found` |
| `POST /reports {data_id, name?, elements?, filter?, charts?}` | **202** `{report_id, job_id, source, id}` задания `qaqc.report` (очередь `quick`); `charts` — `all` \| `flagged` (по умолчанию) \| `none`; `elements` — все элементы данных по умолчанию; 422 `field_not_found {param: "elements"}`, `bad_param {param: "charts" \| "filter.…" \| "data_id"}`, `too_many_reports`; 404 `qaqc_data_not_found`; 423 |
| `PUT /reports/{id} {name, expected_version}` | report; 409 `name_taken`, `version_conflict {current}` |
| `DELETE /reports/{id}` | `{id, deleted: true}` |
| `GET /reports/{id}/charts` | `{charts: [{key: "img001", section, element, kind?, view?, group?, title}] (≤ 200), total, omitted}`; 409 `report_building` |
| `PUT /reports/{id}/images/{key}` (тело — байты PNG ≤ 2 МБ) | `{key, images}`; повтор ключа заменяет; 413 `image_too_large`, 415 `not_png`, 422 `too_many_images` (ключ сверх 200), 404 `chart_not_found` |
| `POST /reports/{id}/rebuild` | 202 как у создания: сохранённые фильтр, элементы и состав графиков на текущих данных и настройке; версия + 1, рисунки и файлы сбрасываются; 409 `source_deleted`, `report_building` |

Расчёты и записи по снимку отчёта — `POST /calc/{section}` и `POST /records` с `report_id` вместо `data_id`.
События `objects.changed {kind: "qaqc_report", id, deleted?}`; результат задания `{report_id, version, rebuilt, events}`.

Форма отчёта (числа и хеши в примере — для вида):

```json
{"id": 3, "name": "Отчёт QAQC — Контроль", "version": 1, "state": "ready", "stale": null,
 "data_name": "Контроль", "data_sha256": "3d89…", "inputs_sha256": "a1f0…", "elements": ["AU", "CU", "AG"],
 "filter": {"v": 1, "labs": ["LAB1"]}, "charts": "flagged", "created_at": "…", "updated_at": "…", "built_at": "…",
 "job_id": null, "files": [], "images": 0, "size": 52113, "summary_size": 18044,
 "summary": {"v": 1, "elements": {"AU": {"summary": {…}, "standards": {…без series}, "blanks": {…без series},
                                         "duplicates:DUP_F": {…без points, ranked}, "external": {…}}}}}
```

`stale`: `null`, `data` (у данных с тем же uid другие записи), `config` (изменились настройка или справочник),
`source_deleted` (данные удалены — расчёт по `report_id` работает, «Перестроить» — 409). Ответы разделов в
`summary` — те же, что у `POST /calc/{section}`, без построчных ключей `series`, `samples`, `points`, `ranked`,
`batches` (таблица партий — в XLSX, 14.14); у дубликатов — ключ на тип: `duplicates:DUP_F`, `:DUP_C`, `:DUP_P`.

## Файл отчёта: XLSX (14.14)

| Маршрут | Ответ |
|---|---|
| `POST /reports/{id}/files {kind: "xlsx", limit?}` | **202** `{report_id, job_id, source, id}` задания `qaqc.report_file` (очередь `quick`); результат `{report_id, kind: "xlsx", sha256, size, events}`; после конца — `files: [{kind: "xlsx", size}]` в отчёте. `kind: "pdf"` → 422 `not_available` (подсказка «Появится на этапе Э22…»); 409 `report_building`; 423. `limit` (1000 … 1 048 575) — строк на лист, только для проверок |
| `POST /reports/{id}/save {kind: "xlsx", path}` | `{path, size}` — копия собранного файла по пути из `pickSave` (атомарно, перезаписывает); 409 `file_not_built` («Сформировать XLSX» сначала), 404 `folder_not_found`, 409 `file_write_failed`. Работает и в проекте «только просмотр» |

Листы по порядку: «Сводка», «Стандарты», «Бланки», «Дубликаты», «Внешний контроль», «Партии», «Параметры».
Сверх 1 048 575 строк — «Стандарты (2)», «(3)» с повтором шапки. Рисунки (PNG из `PUT …/images/{key}`) — столбиком
справа от таблиц на листе своего раздела. «Перестроить» сбрасывает файл. Тот же отчёт даёт тот же файл побайтно.
