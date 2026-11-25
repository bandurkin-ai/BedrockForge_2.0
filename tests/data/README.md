# Данные для тестов

| Папка | Что | Правила |
|---|---|---|
| `real/` | Файлы владельца (скважины, блочные модели, каркасы) | Только чтение; описание — `real/blockmodels-dm-fbm-dat.md` |
| `ref/` | Эталонные векторы, вычисленные сторонними программами | Только чтение; пересоздаются скриптами `tools/gen/ref_*.py`; ADR 0012, ТЗ 7.35 Д-21 |
| `ext/` | Образцы файлов сторонних проектов (MIT) | Только чтение; не менять байты (хеши — в `expected/`) |

## ref/geostatspy

`geostatspy_reference.json` и `manifest.json` — векторы GeostatsPy (MIT, форк `bandurkin-ai/GeostatsPy`, коммит и версии — в `manifest.json`).
Данные: 80 точек (40 равномерных, 40 в кластере 200 × 200 м), зерно `20261118`. Содержимое: `nscore`/`backtr`, `gamv` (4 варианта), `vmodel`, `varmap`, `declus`, `kb2d` (SK/OK, точечный и блочный 4 × 4, поиск 12 проб).

Особенности (подробно — ADR 0012): блочный `kb2d` GeostatsPy сломан — блочные значения посчитаны замкнутой формой numpy; сетка `est[iy][ix]` растёт с Y; в лаге 0 `gamv` есть самопара.

Воссоздать (GeostatsPy в поставку BF не входит, окружение временное):

```bash
uv venv refenv --python 3.12
uv pip install --python refenv/bin/python numpy scipy pandas numba statsmodels tqdm
git clone https://github.com/bandurkin-ai/GeostatsPy /tmp/GeostatsPy
GEOSTATSPY=/tmp/GeostatsPy refenv/bin/python tools/gen/ref_geostatspy.py tests/data/ref/geostatspy
```

Скрипт останавливается, если замкнутая форма расходится с GeostatsPy более чем на 1e−9.

### GeostatsPy 0.0.79 (Э18, Т8.8-4…6, ADR 0024)

`kb2d_079.json`, `kb3d_079.json`, `manifest_079.json` (sha256 файлов, версии, проверки, известные дефекты) — GeostatsPy **0.0.79** с PyPI (MIT), зерно 42. `kb2d`: 300 проб в квадрате 1000 м, сетка 50 × 50 по 20 м, две анизотропные структуры, `ndmax` 16, радиус 400 м; OK и SK, точечный (из 0.0.79) и блочный 4 × 4 (из `core/tests/reference/kb2d_fixed.py` — копия `kb2d` с двумя исправлениями, только для тестов, лицензия MIT рядом). `kb3d`: 250 проб, сетка 16 × 16 × 8, точечный OK и SK, структур 1 и 2 (в том числе с падением); для двух структур в `setrot3D_array` 0.0.79 перенесено одно выделение матрицы (иначе первая структура вырождается в константу) — подробности в `known_defects`. Массивы `est[iy][ix]` и `est[iz][iy][ix]` растут с Y, X, Z.

```bash
export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS
uv venv /tmp/ref079 --python 3.12
uv pip install --python /tmp/ref079/bin/python geostatspy==0.0.79 numpy scipy pandas numba statsmodels tqdm matplotlib
pip download geostatspy==0.0.79 --no-deps -d /tmp/ref079-wheel
GEOSTATSPY_WHEEL=/tmp/ref079-wheel/geostatspy-0.0.79-py3-none-any.whl /tmp/ref079/bin/python tools/gen/ref_geostatspy.py --079 tests/data/ref/geostatspy
```

Повторный запуск даёт те же sha256. Разделы 0.0.46 (`geostatspy_reference.json`, `manifest.json`) не меняются. Тест целостности — `core/tests/test_ref_geostatspy_079.py`.

## Эталоны вариографии (`core/tests/data/variography/`)

Эталоны Э16 лежат рядом с тестами ядра и общие для ядра и интерфейса (ADR 0022; интерфейс сверяет формы с настоящими ответами ядра).

| Файл | Что | Откуда |
|---|---|---|
| `frames.json` | Оси a1–a3 по ang1–ang3 (GSLIB setrot), Т8.6-3 | `test_geostat_rotation.py`; тот же файл читают `ellipsoid-*.test.ts` |
| `model_curves.json` | Значения моделей по осям (сферическая, экспоненциальная, гауссова, до 3 структур), Т8.6-4 | `test_vario_model.py`; тот же файл читает `variography-model.test.ts` |
| `vmodel_geostatspy.json`, `gamv_geostatspy.json` | `vmodel`, `gamv` GeostatsPy, Д-21 | Из `ref/geostatspy` (набор — 80 точек, зерно `20261118`); GeostatsPy не меняется |
| `window_answers.json` | Настоящие ответы ядра для окна «Вариография»: `defaults`, `preview`, итог задания | `test_vario_window_answers.py` |

Пересоздать `window_answers.json` (после изменения формы ответа, затем сверить `variography-window.test.ts`):

```bash
cd core && BF_REGEN_VARIO_WINDOW=1 uv run pytest -q tests/test_vario_window_answers.py
```

Правило `trunc` для карты вариограммы (`varmap` GeostatsPy, подшаг 16.4): GeostatsPy `varmapv` берёт ячейку как `int(Δ / ячейка)` — отбрасывает дробную часть (центральная ячейка вдвое шире), ядро BF округляет (`round`). Для сверки `varmap(..., rule="trunc")` (только в тестах): строки эталона перевёрнуты по Y (строка 0 — наибольший y), ячейка с `npairs ≤ minnp` пуста (`min_pairs = minnp + 1`). Допуск: N точно, γ 1e−9 (`test_vario_fan_map.py`).

| Файл | Что | Откуда |
|---|---|---|
| `varmap_geostatspy.json` | Шесть случаев `varmapv` (решётка со сдвигами k + ½ ячейки, повторные точки) | `core/tests/data/variography/`; одноразовый генератор — в `test_vario_fan_map.py` |
| `core/tests/data/stats/tab_vario_answers.json` | Настоящие ответы ядра для вкладки «Статистика» (вариограмма, карта, `local`) | `core/tests` (API вкладки); пересоздать — `BF_REGEN_STATS_TAB_VARIO=1`, затем сверить `stats-vario.test.ts` |

## Блочные модели и фильтры (`core/tests/data/blocks/`)

Фикстуры Э17 лежат рядом с тестами ядра (не в `tests/data/`: сюда — только реальные данные) и общие для ядра и интерфейса: интерфейс сверяет формы с настоящими ответами ядра (ADR 0023, урок Э14–Э15). Файл — запись ответов, которые пишет тест ядра из настоящих ядер и маршрутов; vitest читает его. Руками не править: после изменения формы ответа перегенерировать и сверить vitest. Времена и размеры файла в фикстуры не пишутся.

| Файл | Что | Тест ядра (`core/tests/`) · флаг | Читает vitest (`ui/test/`) |
|---|---|---|---|
| `grids.json`, `contract_answers.json` | Сетки v2 (поворот, `div`, переменный шаг) и формы ответов контракта | `test_blocks_schema_v2.py` · `BF_REGEN_BLOCKS_CONTRACT` | `blocks-window-ui` (`contract_answers`) |
| `expr_texts.json` | 99 текстов выражений 8.7.4: каноническая запись, позиция ошибки | вручную; `test_calc_parser.py` проверяет разбор | `filters-constructor` |
| `calc_eval_cases.json` | Случаи вычисления 8.7.4 (пропуски, типы, категории) | вручную; `test_calc_eval.py` | — |
| `viz_answers.json` | 13 ответов маршрутов показа: оболочка, «все блоки», LOD, срез, блок в точке, 409 и 422 | `test_blocks_viz.py` · `BF_REGEN_BLOCKS_VIZ` | `blocks-display`, `blocks-show-commands`, `blocks-slice-layer`, `blocks-slice-scene`, `blocks-window`, `blocks-window-ui` |
| `layer_fixture.json` | BFA1 `blocks`, собранный настоящими `lod.view`, `scale_class`, `scale_bins`, `break_class`; лучи выбора, посчитанные простым циклом | `test_blocks_layer_fixture.py` · `BF_REGEN_BLOCKS_LAYER` | `blocks-layer`, `blocks-display`, `blocks-window`, `blocks-window-ui` |
| `window_answers.json` | Окно «Блочная модель · …»: модель, повёрнутая на 30°, с субблоками; роли, проверки, свойства полей; сохранение быстрого ограничителя в фильтр | `test_blocks_window_answers.py` · `BF_REGEN_BLOCKS_WINDOW` | `blocks-window*` |
| `filters_window_answers.json` | Окно «Фильтры»: список, `POST /filters/test`, «Где используется», отказы | `test_filters_window_answers.py` · `BF_REGEN_FILTERS_WINDOW` | `filters-window`, `filters-constructor`, `blocks-show-commands` |
| `swath_filter_answers.json` | Swath по блочной модели с фильтром: запросы и ответы | `test_swath_filter_window_answers.py` · `BF_REGEN_SWATH_FILTER` | `swath-filter` |
| `import_window_answers.json` | Состав блочных файлов (`POST /import/objects/inspect`: роли, сетка, три центра, коды «пусто», отказы) и итог задания `import.objects` для мастера | `test_blocks_import_window_answers.py` · `BF_REGEN_BLOCKS_IMPORT` | `blocks-import-model`, `blocks-import-wizard`, `blocks-export-model` |
| `build_window_answers.json` | Прототип, заполнение, кодирование, регуляризация: предпросмотр, стадии, итоги, отказы | `test_blocks_build_window_answers.py` · `BF_REGEN_BLOCKS_BUILD` | `blocks-build-*` |
| `fields_window_answers.json` | Калькулятор, плотность, категории, схемы, свойства полей | `test_blocks_fields_window_answers.py` · `BF_REGEN_BLOCKS_FIELDS` | `blocks-fields-*` |
| `report_window_answers.json` | «Содержание–тоннаж»: таблица, кривая, группы, CSV | `test_blocks_report_window_answers.py` · `BF_REGEN_BLOCKS_REPORT` | `blocks-report` |
| `export_window_answers.json` | Экспорт: форматы, поля, `files_exist`, отказы | `test_blocks_export_window_answers.py` · `BF_REGEN_BLOCKS_EXPORT` | `blocks-export-model`, `blocks-export-window` |

Пересоздать (из папки `core`; затем `npm test -- <файл vitest>` из корня):

```bash
cd core
BF_REGEN_BLOCKS_CONTRACT=1 uv run pytest -q tests/test_blocks_schema_v2.py
BF_REGEN_BLOCKS_VIZ=1      uv run pytest -q tests/test_blocks_viz.py -k answers_kept
BF_REGEN_BLOCKS_LAYER=1    uv run pytest -q tests/test_blocks_layer_fixture.py
BF_REGEN_BLOCKS_WINDOW=1   uv run pytest -q tests/test_blocks_window_answers.py
BF_REGEN_FILTERS_WINDOW=1  uv run pytest -q tests/test_filters_window_answers.py
BF_REGEN_SWATH_FILTER=1    uv run pytest -q tests/test_swath_filter_window_answers.py
BF_REGEN_BLOCKS_IMPORT=1   uv run pytest -q tests/test_blocks_import_window_answers.py
BF_REGEN_BLOCKS_BUILD=1    uv run pytest -q tests/test_blocks_build_window_answers.py
BF_REGEN_BLOCKS_FIELDS=1   uv run pytest -q tests/test_blocks_fields_window_answers.py
BF_REGEN_BLOCKS_REPORT=1   uv run pytest -q tests/test_blocks_report_window_answers.py
BF_REGEN_BLOCKS_EXPORT=1   uv run pytest -q tests/test_blocks_export_window_answers.py
```

Без флага те же тесты сравнивают сохранённый файл с ответами ядра и падают при расхождении («the answers of the core changed: regenerate with …»). Порядок: ядро → перегенерация → vitest; если форма изменилась, правят и клиент. Фикстуры показа (`viz_answers.json`, `layer_fixture.json`) делят между собой окно модели и слой: перегенерируйте оба и прогоните `blocks-display`, `blocks-layer`, `blocks-window`, `blocks-window-ui`.

Реальные блочные модели (три файла «Для BF» — `.dat`, `.dm`, `.fbm`, одни и те же 169 070 блоков) лежат в `real/` и только читаются: `test_import_blocks.py`, `test_datamine_blocks.py`, `test_blocks_infer.py`; описание — `real/blockmodels-dm-fbm-dat.md`. Повёрнутые и субблочные Datamine и Micromine пока только на синтетике (`dm_builder`, `tools/gen/blockmodel.py`): образцов нет (вопрос № 64).

## ext/omf и ext/expected

| Файл | Источник | Лицензия | Что это |
|---|---|---|---|
| `omf/v1/test_file.omf` | `bandurkin-ai/omf-python`, `assets/v1` (коммит `7b349ec`) | MIT, © 2021 Global Mining Guidelines Group | OMF v0.9 (файловая v1): 100 точек, линия, 2 каркаса, объём. Образец импорта Э11 |
| `omf/v2/test_file.omf` | то же, `assets/v2` | MIT | Диалект OMF 2.0.0a0 (ZIP, `project.json`). BF его не читает: образец для проверки отказа с понятной ошибкой |
| `omf/minelib/McLaughlin_tutorial.omf` | `bandurkin-ai/Incline-Design`, `docs/manuals/optimization` (коммит `d33c76f`) | MIT, © 2026 Leo Timmins, Lucas Timmins и участники; данные — MineLib | Диалект OMF 2 Incline (`index.json.gz` + Parquet). Блочная модель McLaughlin 140 × 296 × 68 = 2 817 920 блоков 7,62 × 7,62 × 6,096 м, поля au, density, rocktype, hardness. Образец для Э21 и отказа OMF 2 |

Ссылка для McLaughlin: Espinoza D., Goycoolea M., Moreno E., Newman A. (2013). *MineLib: a library of open pit mining problems.* Annals of Operations Research 206(1), 93–114. https://doi.org/10.1007/s10479-012-1258-3

`expected/*.json` — число и состав элементов, прочитанные без библиотеки `omf` (`python tools/gen/ref_omf.py tests/data/ext/omf tests/data/ext/expected`). Тест целостности — `core/tests/test_reference_fixtures.py`.
