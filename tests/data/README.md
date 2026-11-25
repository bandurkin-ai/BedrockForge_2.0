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

Читают: `core/tests/test_estimate_reference.py` (Т8.8-4 — `kb2d` точечный OK и SK, 1e−6 отн., получено 4e−10; Т8.8-5 — блочный 4 × 4 по `kb2d_fixed.py`, 6,7e−10; Т8.8-6 — `kb3d`, 1,7e−15; ТД38-1, ТД38-2), `core/tests/test_ref_geostatspy_079.py` (целостность по `manifest_079.json`). Т8.8-7 (3D-блок, ang3 ≠ 0, 3 структуры, октанты, лимит скважины) сверяется не с GeostatsPy, а с независимой реализацией на NumPy `core/tests/reference/krige3d_numpy.py` (перебор вместо дерева, `numpy.linalg.solve`; отбор совпал точно, EST 5e−13, KV 1,1e−11). Координаты 0.0.79: x на восток, y на север, z вверх, азимут по часовой от севера, положительное падение поднимает большую ось (как `ang2` в BF2); π в `setup_rotmat` 0.0.79 равно 3.14159265, поэтому двумерные ответы отличаются от расчёта с точным π на ≈ 6e−10. Блочный режим 0.0.79 неработоспособен (`kb2d`: неопределённая `real`, строки `if i == j` вне внутреннего цикла C̄_VV; `kb3d`: дискретизация без `nzdis`) — не эталон.

## Оценка содержаний и граф зависимостей (`core/tests/data/estimate/`, `lineage/`, `stats/`)

Фикстуры Э18 лежат рядом с тестами ядра и общие для ядра и интерфейса (ADR 0024, ADR 0025; урок Э14–Э15): интерфейс сверяет формы с настоящими ответами ядра на синтетическом проекте. Файл — запись ответов, которые пишет тест ядра из настоящих маршрутов и заданий (`core/tests/estapi_world.py`: 25 скважин, блочная модель из трёх доменов, модель вариограммы; `estinspect.py` — наборы для инспектора); vitest читает его. Руками не править. Времена, отпечатки, `versions`, `stages`, `token` в файл не пишутся, числа сравниваются до 1e−7 (на другой машине последние цифры могут отличаться); без флага тест сравнивает файл с ответами ядра и падает при расхождении.

| Файл | Что | Тест ядра (`core/tests/`) · флаг | Читает vitest (`ui/test/`) |
|---|---|---|---|
| `estimate/params.json` | 5 строк «Параметров оценки» (`idw`, `ok_point`, `sk`, `ik`, `ok_block_passes`): тело → каноническая форма и колонки | `test_estimate_spec.py` · `BF_REGEN_ESTIMATE` (переписывает `canonical` и `columns`) | `estimation/form` (и `estimation/harness.ts`) |
| `estimate/contract_answers.json` | Настоящие ответы маршрутов Э18: `defaults`, `estimations`, `estimation`, `estimation_post`, `estimation_put_conflict`, `bad_param`, `variogram_scale`, `plan`, `run_submit`, `run_field_exists`, `run_result`, `preview`, `xval_submit`, `xval_result`, `xval`, `kna`, `global`, `qq`, `swath`. Ключи `simulation`, `simulate_result`, `simulate_post_result` — старые образцы контракта Э18б (настоящие ответы моделирования — `simulate/answers.json`, ниже), `inspect` — старый образец; тест их не трогает | `test_estimate_api_answers.py` · `BF_REGEN_ESTIMATE` | `estimation/*` (общая обвязка — `estimation/harness.ts`) |
| `estimate/inspect_answers.json` | Инспектор блока: `answers` (`pass_1`, `pass_3`, `few_samples`, `out_of_domain`, `changed`), `errors` (`no_block`, `no_model`, `no_params`, `no_such_row`), `rows` | `test_estimate_inspect_route.py` (`test_the_answers_the_window_is_read_against`) · `BF_REGEN_INSPECT` | `estimation/inspect`, `inspect-review`, `inspect-scene` |
| `lineage/answers.json` | Граф зависимостей: `status`, `impact`, `origin`, `pin`, `rebuild` (план и запуск) и отказы — по цепочке заглушек (`stub`), по настоящим результатам (`real`: композиты → модель вариограммы) и правилам (`rules`) | `test_lineage_window_answers.py` · `BF_REGEN_LINEAGE` | `lineage/*` (`lineage/harness.ts`) |
| `stats/contact_blocks_answers.json` | `POST /stats/contact` с `blocks`: профиль блоков у контакта для домена из каркаса (`wireframe`) и из поля модели (`domain`) | `test_stats_contact_blocks.py` · `BF_REGEN_CONTACT_BLOCKS` | `contact-blocks` |
| `ref/geostatspy/*_079.json`, `manifest_079.json` | Эталоны GeostatsPy 0.0.79 (раздел выше) | `tools/gen/ref_geostatspy.py --079`; тест — `test_ref_geostatspy_079.py` | — |

Пересоздать (из папки `core`; затем `npm test -- <файл vitest>` из корня):

```bash
cd core
BF_REGEN_ESTIMATE=1        uv run pytest -q tests/test_estimate_spec.py
BF_REGEN_ESTIMATE=1        uv run pytest -q tests/test_estimate_api_answers.py
BF_REGEN_INSPECT=1         uv run pytest -q tests/test_estimate_inspect_route.py -k the_answers_the_window
BF_REGEN_LINEAGE=1         uv run pytest -q tests/test_lineage_window_answers.py
BF_REGEN_CONTACT_BLOCKS=1  uv run pytest -q tests/test_stats_contact_blocks.py
```

Один флаг `BF_REGEN_ESTIMATE` относится к двум тестам: `test_estimate_spec.py` пишет `params.json`, `test_estimate_api_answers.py` — `contract_answers.json` (только ключи этого теста, остальные остаются). Порядок: ядро → перегенерация → vitest (`estimation/*`, `lineage/*`, `contact-blocks`); если форма ответа изменилась, правят и клиент, и `docs/progress/e18-api.md`. После изменения полей оценки, проходов или формы ответа инспектора перегенерируйте `contract_answers.json` и `inspect_answers.json`.

## Моделирование (`core/tests/data/simulate/`)

Фикстура Э18б общая для ядра и интерфейса (ADR 0024 §9, `docs/progress/e18-api.md` §9): интерфейс сверяет формы с настоящими ответами ядра на синтетическом проекте `core/tests/estapi_world.py` (25 скважин, блочная модель из трёх доменов, модель вариограммы в нормальных оценках «AU · Y сим»).

| Файл | Что | Тест ядра (`core/tests/`) · флаг | Читает vitest (`ui/test/simulation/`) |
|---|---|---|---|
| `simulate/answers.json` | Настоящие ответы маршрутов Э18б в форме `{status, body}`: строки (`simulation`, `simulations`, `simulation_post`, `simulation_put_conflict`, `simulation_delete`, `simulation_empty`, `simulation_name_taken`, `sis_row`, `cosgs_row`), запуск (`run_submit`, `run_result`, `run_body_result`, `run_version_conflict`, `sis_run_result`, `sis_cat_run_result`, `cosgs_run_result`), сводка (`summary`, `summary_empty`, `sis_cat_summary`), постобработка (`post_submit`, `post_result`, `post_field_exists`, `post_version_conflict`, `sis_post`, `sis_post_result`, `sis_cat_post`, `sis_cat_post_result`, `sis_cat_post_thresholds`, `cosgs_post`) и отказы (`bad_param`, `bad_param_search`, `variogram_scale`, `sis_no_method`, `cosgs_bad_rho`, `cosgs_not_a_number`). Времена, отпечатки, хеши и номера заданий при сверке отбрасываются | `test_simulation_api.py::test_the_answers_are_what_the_core_answers` · `BF_REGEN_SIMULATE` (переписывает файл целиком) | `harness.ts`, `form`, `wizard`, `summary`, `post`, `tree`, `commands`, `review` |

Пересоздать (из папки `core`; затем `npm test -- simulation` из корня):

```bash
cd core
BF_REGEN_SIMULATE=1  uv run pytest -q tests/test_simulation_api.py
```

Без флага тест сравнивает ответы ядра с файлом: расхождение значения, формы или набора ключей — отказ. Порядок: ядро → перегенерация → vitest (`simulation/*`); если форма ответа изменилась, правят и клиент (`ui/src/modules/simulation/api.ts`), и `docs/progress/e18-api.md` §9. Реализации хранятся в файле Parquet, в `answers.json` их нет: только сводки и итоги заданий.

## Неявное моделирование (`tests/fixtures/implicit/`, `core/tests/data/implicit/`)

Фикстуры Э19 (ADR 0026, `docs/progress/e19-api.md` §13). Исходные данные — синтетика, не реальные файлы: `tests/fixtures/implicit/` лежит в `tests/fixtures/` (не в `tests/data/`, где только реальные и эталонные данные); ответы ядра для интерфейса — рядом с тестами ядра, как у Э17–Э18б.

| Файл | Что | Откуда | Читает |
|---|---|---|---|
| `tests/fixtures/implicit/collars.csv` | `HOLE,X,Y,Z,TD,AZIMUTH,DIP`: 100 прямых скважин на сетке 10 × 10 с шагом 40 м (устья около 300 м, длина ≈ 120–135 м) | `tools/gen/implicit.py`, зерно 12345 | `core/tests/modapi_world.py` (маршруты, задания, Д-32…Д-34), `test_model_{data,bodies,veins,job}.py`, `app/e2e/implicitWorld.ts`, `bench/scenarios/implicit_compile.py` |
| `tests/fixtures/implicit/lithology.csv` | `HOLE,FROM,TO,CODE`: порода каждого интервала; границы — точные контакты (поиск делением пополам по аналитической геологии), две цифры после запятой. Коды ALV (над волнистой поверхностью), SS, SH, GR (снизу), INT (сфера-интрузия R = 55 м в (200, 200, 200)), VN (плита-жила 3 м, нормаль (0,8; 0; 0,6)) | то же | то же |
| `tests/fixtures/implicit/assays.csv` | `HOLE,FROM,TO,AU`: пробы по 5 м; AU по породе (INT и VN богатые), часть значений пропущена (не 0) | то же | оболочки содержаний, E2E |
| `tests/fixtures/implicit/strings.csv` | `LAYER,LINE,CLOSED,X,Y,Z`: ручные данные — контакт SS/SH в трёх сечениях, замкнутое кольцо в интрузии, точки вне интрузии | то же | `data_manual`, E2E |
| `core/tests/data/implicit/params.json` | Модели `domains`, `shells_auto`, `vein_expand`, `stack_b` (Э19б): `body` → каноническая форма (`spec.parse_model(body).canonical()`) | `test_implicit_spec.py` · `BF_REGEN_IMPLICIT` (переписывает `canonical`) | vitest `implicit/props`, `implicit/harness` (`real.ts`) |
| `core/tests/data/implicit/contract_answers.json` | Ответы маршрутов Э19 в форме `{status, body}`: `defaults`, `bad_param`, `refusals` (пишет `test_implicit_spec.py`), модели, группы, предпросмотр точек, `build_*`, `update_*`, `to_wireframe`, `check`, `check_csv`, `support*`, `compare_holes`, `from_model_*`, отказы и 409 (пишет `test_implicit_contract.py`); образцы форм (Э19б, структурный тренд): `trend_*`, `build_iso_error`, `*_b` | `test_implicit_contract.py` · `BF_REGEN_IMPLICIT` (сцена `core/tests/modapi_world.py`: 100 скважин, домены INT и SS; времена, отпечатки, версии библиотек и невязки из сравнения исключены, числа — до 1e−7) | vitest `implicit/*` (`harness.ts`, `checkHarness.ts`, `groupsHarness.ts`) |

Данные для замеров (50 тыс. скважин, 6 кодов) строит `tools/gen/implicit.holes_arrays(holes, seed)` без файлов; сценарии `bench/scenarios/implicit_*.py` — `implicit_common.py`.

Воссоздать CSV (после изменения генератора; тесты Э19 сравнивают числа с файлами, перегенерация меняет их — затем `BF_REGEN_IMPLICIT=1`):

```bash
python tools/gen/implicit.py --out tests/fixtures/implicit
cd core
BF_REGEN_IMPLICIT=1 uv run pytest -q tests/test_implicit_spec.py
BF_REGEN_IMPLICIT=1 uv run pytest -q tests/test_implicit_contract.py
```

Без флага те же тесты сравнивают сохранённые файлы с ответами ядра и падают при расхождении. Порядок: генератор → ядро → перегенерация → vitest (`npm test -- implicit`); если форма ответа изменилась, правят и клиент (`ui/src/modules/implicit/api.ts`), и `docs/progress/e19-api.md`. Ключ `defaults` и `bad_param` пишет `test_implicit_spec.py`, остальное — `test_implicit_contract.py` (его ключи заменяют образцы, чужие ключи файла остаются).

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
