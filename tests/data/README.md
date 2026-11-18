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

## ext/omf и ext/expected

| Файл | Источник | Лицензия | Что это |
|---|---|---|---|
| `omf/v1/test_file.omf` | `bandurkin-ai/omf-python`, `assets/v1` (коммит `7b349ec`) | MIT, © 2021 Global Mining Guidelines Group | OMF v0.9 (файловая v1): 100 точек, линия, 2 каркаса, объём. Образец импорта Э11 |
| `omf/v2/test_file.omf` | то же, `assets/v2` | MIT | Диалект OMF 2.0.0a0 (ZIP, `project.json`). BF его не читает: образец для проверки отказа с понятной ошибкой |
| `omf/minelib/McLaughlin_tutorial.omf` | `bandurkin-ai/Incline-Design`, `docs/manuals/optimization` (коммит `d33c76f`) | MIT, © 2026 Leo Timmins, Lucas Timmins и участники; данные — MineLib | Диалект OMF 2 Incline (`index.json.gz` + Parquet). Блочная модель McLaughlin 140 × 296 × 68 = 2 817 920 блоков 7,62 × 7,62 × 6,096 м, поля au, density, rocktype, hardness. Образец для Э21 и отказа OMF 2 |

Ссылка для McLaughlin: Espinoza D., Goycoolea M., Moreno E., Newman A. (2013). *MineLib: a library of open pit mining problems.* Annals of Operations Research 206(1), 93–114. https://doi.org/10.1007/s10479-012-1258-3

`expected/*.json` — число и состав элементов, прочитанные без библиотеки `omf` (`python tools/gen/ref_omf.py tests/data/ext/omf tests/data/ext/expected`). Тест целостности — `core/tests/test_reference_fixtures.py`.
