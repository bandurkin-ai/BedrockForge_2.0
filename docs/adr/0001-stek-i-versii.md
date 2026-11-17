# ADR 0001. Стек и точные версии

**Статус:** Принято, 2026-11-16 (Э0). Версии закреплены; пункты с меткой «спайк» подтверждаются на `windows-latest` (а–г), при провале — правка этого ADR.

## Контекст

- Решения заказчика №3–5 (раздел 2) задают стек: Electron + React/TS/three.js + Python 3.12 + SQLite/Parquet/DuckDB. ТЗ 6.3 даёт ориентиры версий; «более новые — только если спайк Э0 подтвердил совместимость»; 17.1.3 — «не угадывай».
- Версии и лицензии взяты из реестров npm и PyPI 2026-11-16 (`npm view`, `pip index versions`, метаданные PyPI), а не по памяти.
- BF фиксировал только UI (`web/package.json`, `desktop/package.json`); Python в BF ставился без lock-файла (`pip install .` в Docker) — «проверенных в BF» версий Python-пакетов нет, а незакреплённый pandas 3 сломал импорт CSV.

## Решение

### 1. Правила выбора

| № | Правило |
|---|---------|
| П1 | UI-пакеты из BF — версия BF или последний патч того же minor (Vite 5.4.x, vitest 2.1.x, Electron 45.2.x) |
| П2 | Python — последняя стабильная в границах ТЗ; новые мажоры и `.0` не берём (pandas 3, SQLAlchemy 2.1, duckdb 2.0, pillow 13, pyarrow 26) |
| П3 | Окно выдержки 7 дней: только выпуски до **2026-11-09** (`exclude-newer` в uv, `npm install --before=2026-11-09`) — защита от отозванных и скомпрометированных выпусков |
| П4 | Только готовые колёса `cp312-win_amd64` и `manylinux x86_64` (`no-build = true`); сборка из исходников запрещена |
| П5 | Прямые зависимости — точные версии (`==`, без `^`); транзитивные — lock-файлом с хешами |

### 2. Инструменты

| Инструмент | Версия | Примечание |
|------------|--------|-----------|
| Python | **3.12.16** | Сборка python-build-standalone через uv (`core/.python-version`); установщиков python.org для security-only ветки 3.12 под Windows нет → **спайк а** (PyInstaller + такая сборка); запасной вариант — `actions/setup-python` 3.12.10 |
| uv | **0.13.6** (посл. 0.13.8) | В CI — ровно 0.13.6 (`setup-uv`, `version`); `required-version = ">=0.13.6,<0.14"`; образ облака даёт 0.11.32 (знает Python только до 3.12.13) → `uvx --from uv==0.13.6 uv …`; MIT OR Apache-2.0 |
| Node.js / npm | **22.23.4 / 10.9.9** | Версия облачной среды; electron@45 требует Node ≥ 22.12; `.nvmrc`, `engine-strict` |
| PostgreSQL | 16 | Только CI (service) и сервер Э25 |

### 3. npm (точные версии)

| Пакет | Закреплено | Последняя | Лицензия | Где | Обоснование |
|-------|-----------|-----------|----------|-----|-------------|
| electron | **45.2.3** | 45.3.3 (16.11) | MIT (+ Chromium, см. п. 7) | app, dev | BF: 45.2.1; последний патч 45.2 в окне П3; Chromium 156.0.8078.80, Node 24.22.2 |
| electron-builder | **26.18.0** | 26.18.0 (тег v26: 26.18.1) | MIT | app, dev | Версия BF; опция `electronFuses` есть в схеме (проверено) |
| electron-updater | **6.8.11** | 6.8.11 | MIT | app, runtime (вводится на Э24) | Парный выпуск к builder 26.18.0 |
| react, react-dom | **18.3.1** | 19.3.1 | MIT | ui | Ориентир ТЗ и BF |
| typescript | **5.6.3** | 7.0.2 | Apache-2.0 | ui, app | Ориентир; openapi-typescript требует `^5.x` |
| vite / @vitejs/plugin-react | **5.4.21 / 4.3.4** | 8.3.8 / 6.3.0 | MIT | ui, dev | BF: 5.4.11 → последний патч 5.4 |
| vitest | **2.1.9** | 5.1.0 | MIT | ui, app, dev | BF: 2.1.8 → последний патч 2.1 (требует vite ^5) |
| esbuild | **0.28.2** | 0.28.2 | MIT | app, dev | Сборка main и preload в один файл (preload в `sandbox` не может `require`); версии ≤ 0.24.2 подпадают под GHSA-67mh-4wv8-2f99 |
| three / @types/three | **0.170.0** | 0.187.0 | MIT | ui | Ориентир ТЗ и BF |
| three-mesh-bvh | **0.9.18** | 0.9.18 | MIT | ui | В BF не было; ТЗ «0.8+»; peer `three >= 0.159` → **спайк б** |
| @playwright/test | **1.49.1** | 1.65.1 | Apache-2.0 | app, dev | Ориентир ТЗ «1.4x» (последняя 1.4x); запускает Electron 45.2.3 в облаке (проверено). Ключ программного рендера задаём сами — 1.49.1 его не добавляет → **спайк б**; запасная — 1.65.0 (проверена там же) |
| openapi-typescript | **7.13.0** | 7.13.0 | MIT | ui, dev | Типы API из OpenAPI (ТЗ 6.6) |
| @types/react, @types/react-dom | **18.3.31 / 18.3.7** | 19.3.0 | MIT | ui, dev | Последние для React 18 |
| @types/node | **22.20.5** | 26.6.6 | MIT | dev | Версия BF, общая для ui и app; API Node 24 (внутри Electron 45) сверх Node 22 не используем |
| license-checker-rseidelsohn | **4.4.2** | 5.0.1 (нужен Node ≥ 24) | BSD-3-Clause | dev | Поддерживаемая замена `license-checker` (25.0.1, 2019) |
| @cyclonedx/cyclonedx-npm | **6.0.1** | 7.0.1 | Apache-2.0 | dev | SBOM |
| pdf-lib | 1.17.1 (вводится на Э22) | 1.17.1 | MIT | app | Склейка PDF |

Других runtime-зависимостей UI нет (графики, состояние, окна — свой код, как в BF); новая зависимость — только через ADR.

### 4. Python, ядро (runtime, точные версии)

| Пакет | Закреплено | Последняя | Лицензия | Обоснование |
|-------|-----------|-----------|----------|-------------|
| fastapi | **0.162.2** | 0.163.1 (15.11) | MIT | ≥ 0.115; тянет starlette 1.10.0, pydantic ≥ 2.9 |
| uvicorn | **0.54.0** | 0.54.0 | BSD-3-Clause | Без extras (h11): меньше бинарных модулей |
| pydantic | **2.14.3** | 2.14.3 | MIT | pydantic-core 2.50.4 |
| SQLAlchemy | **2.0.57** | 2.1.8 | MIT | ТЗ: линия 2.0; 2.1 — отдельный ADR |
| alembic | **1.20.1** | 1.20.1 | MIT | ≥ 1.13; batch mode для SQLite |
| pg8000 | **1.31.6** | 1.31.6 | BSD-3-Clause | Драйвер PostgreSQL вместо psycopg (LGPL), см. ADR 0003 |
| numpy | **2.3.5** | 2.5.5 (15.11) | BSD-3-Clause (+ п. 7) | Линия 2.3 — текущая на момент выпуска pandas 2.3.3 (09.2025); numba 0.68.1 допускает `< 2.6` |
| scipy | **1.18.1** | 1.18.1 | BSD-3-Clause (+ п. 7) | `numpy >= 2.0, < 2.8` |
| pandas | **2.3.3** | 3.1.1 | BSD-3-Clause | ТЗ: `< 3`; последний выпуск 2.x; ограничение `python-dateutil == 2.9.0.post0` (4.0.0 вышел 01.11) |
| pyarrow | **25.0.1** | 26.0.1 | Apache-2.0 | Патч-выпуск предыдущего мажора |
| duckdb | **1.5.6** | 2.0.0 | MIT | Последняя 1.x; parquet встроен; автозагрузка расширений отключается |
| numba / llvmlite | **0.68.1 / 0.50.0** | те же | BSD-2-Clause / BSD-2 + Apache-2.0 WITH LLVM-exception | ≥ 0.60; `numpy < 2.6` → **спайк а, г** |
| manifold3d | **3.5.4** | 3.5.5 (13.11) | Apache-2.0 | BF: `>=3.2,<4` |
| PythonCDT | **2.0.2** | 2.0.2 | MPL-2.0 (+ BSD-3, LLVM-exception) | Имя модуля — `pythoncdt` |
| mapbox-earcut | **2.1.0** | 2.1.0 | ISC | `triangulate_float64` — как в BF (проверено) |
| pyfqmr | **0.5.0** | 0.5.0 | MIT | LOD; вход — записываемая копия (17.3.6) |
| scikit-image | **0.26.0** | 0.26.0 | BSD-3-Clause | Marching cubes |
| ortools | **9.15.6755** | 9.15.6755 | Apache-2.0 | Эталон и запасной решатель LG; 24 МБ; protobuf 6.33.6 |
| numexpr / threadpoolctl | **2.14.2 / 3.8.0** | те же | MIT / BSD-3-Clause | Калькулятор полей; контроль потоков |
| ezdxf / openpyxl | **1.4.5 / 3.1.5** | те же | MIT | DXF (запись, сверка); XLSX |
| pillow / tifffile | **12.3.0 / 2026.10.15** | 13.0.0 / 2026.11.12 | MIT-CMU / BSD-3-Clause | Растры; `imagecodecs` не включаем (отдельный ADR на этапе растров) |
| zstandard / cryptography | **0.25.0 / 50.0.2** | те же | BSD-3 / Apache-2.0 OR BSD-3 | `.bfggis`, AES-GCM, scrypt, Ed25519 |
| pyproj | **3.8.0** | 3.8.0 | MIT | PROJ 9.8.1; сеть PROJ выключена |
| anthropic | **2.6.0** | 2.9.0 (16.11) | Apache-2.0 | Нужен на Э23; обновить перед Э23 (выходит еженедельно) |
| psutil | **7.2.2** | 7.2.2 | BSD-3-Clause | Оценка памяти заданий (ADR 0002) |

### 5. Python, разработка и сборка (группы `dev` и `build`, в дистрибутив не входят)

pytest 9.2.0, httpx 0.28.1, ruff 0.17.4, mypy 2.4.1, pip-licenses 5.5.5, cyclonedx-bom 8.0.1, pdfminer.six 20260107 (все MIT/BSD/Apache-2.0); **PyInstaller 6.22.3** и pyinstaller-hooks-contrib 2026.9 (см. п. 7); GeostatsPy 0.0.79 — только сверка в тестах; Cython (Apache-2.0) — версия фиксируется на Э24.

### 6. Lock-файлы и обновление

| Файл | Содержимое |
|------|-----------|
| `core/pyproject.toml` + `core/uv.lock` | Один универсальный lock для `win_amd64` и `linux x86_64` (`[tool.uv] environments`), хеши sha256, `exclude-newer`, `no-build`, `constraint-dependencies` |
| Корневой `package.json` (`workspaces: ["ui", "app"]`) + **один** `package-lock.json` | Все npm-пакеты с `integrity`; в `ui/package.json` и `app/package.json` — точные версии |

**npm workspaces — да, один `package-lock.json` в корне** (так же сформулировано в ТЗ 6.3 и S8; набор разрешён одним lock: 651 пакет). Условия: (1) main и preload собирает esbuild, интерфейс — Vite; `node_modules` в asar не кладётся, поэтому подъём зависимостей в корень на упаковку не влияет; (2) пакеты, чей код попадает в сборку (react, three, three-mesh-bvh, electron-updater, pdf-lib), объявляются в `dependencies` — по ним идут проверка лицензий и SBOM; всё остальное — `devDependencies`; (3) пакет `electron` 45 не скачивает двоичный файл при `npm ci` (нет `postinstall`): он загружается при первом запуске или шагом `npx install-electron` — задания Linux CI без Electron его не качают, в `windows.yml` это явный шаг с кешем.

Установка — только `uv sync --locked` и `npm ci`. Обновление — отдельный PR `deps: …`: правка версии → `uv lock` / `npm install --package-lock-only --before=<дата>` → CI Linux + Windows → проверка лицензий → bench. Патчи безопасности Electron 45.x — раз в месяц; мажоры — только ADR.

### 7. Лицензии: что найдено

| Находка | Где | Решение |
|---------|-----|---------|
| LGPL в Chromium: `ffmpeg.dll` (LGPL-2.1+), части Blink/WebKit | Electron | Неустранимо при решении №4; исключение с уведомлением заказчика; `LICENSES.chromium.html` в дистрибутиве |
| GCC runtime (GPL-3.0+ WITH GCC-exception-3.1) в OpenBLAS | колёса numpy, scipy (Windows) | Исключение GCC разрешает распространение; в `THIRD_PARTY_NOTICES` |
| PyInstaller: GPL-2.0+ с исключением для загрузчика | сборка | Допустимо (исключение покрывает собранные программы); в белом списке как инструмент сборки |
| **Nuitka ≥ 4.0 — AGPLv3+** (2.8.10 — последняя Apache-2.0) | Э24 | Не используем; защищённый модуль — Cython (Apache-2.0) |
| psycopg 3 — LGPL-3.0-only; psycopg2 — LGPL | драйвер PG | pg8000 (BSD-3-Clause) |
| Нет в списке ТЗ, но разрешительные: MIT-0 (cffi, scramp), MIT-CMU (pillow), BlueOak-1.0.0 (`sax` в electron-updater), Python-2.0 (`argparse`), 0BSD, LLVM-exception | runtime | Добавить в белый список проверок CI; прочие (CC0, CC-BY, WTFPL) — только dev |
| MPL-2.0: PythonCDT, certifi | runtime | Разрешена ТЗ; без изменений исходников |
| `Nsis7z` (код 7-Zip, LGPL) в установщике | NSIS | Проверить на Э24; замена — `useZip` |

## Причины

- Набор Python разрешён одним `uv lock` для Windows и Linux и установлен в облаке; набор npm — одним lock-файлом workspaces; production-зависимости npm: 0 уязвимостей (`npm audit --omit=dev`).
- numpy 2.3.5 — пересечение допусков numba (`< 2.6`), scipy (`>= 2.0`), tifffile (`>= 2.1`), ortools (`>= 2.0.2`) и линии, современной pandas 2.3.3.

## Последствия

- Vite 5.4 / vitest 2.1 не получают исправлений: `npm audit` показывает уязвимости dev-сервера и `tinypool` (только dev; dev-сервер в CI и сборке не используется). Переход на Vite 7+/vitest 4+ — отдельный ADR после В1.
- Ядро: 69 runtime-пакетов, колёса Windows ≈ 223 МБ (llvmlite 42, scipy 37, pyarrow 28, ortools 24) + Electron ≈ 100 МБ в сжатом виде → оценка дистрибутива 330–370 МБ при лимите 400 МБ (запас мал); размер контролируется в `windows.yml`.
- Белый список лицензий шире списка ТЗ 6.3 — требуется согласие заказчика (отчёт вехи В1).
- Каркас Э0 в репозитории собран до этого ADR на последних версиях (Electron 45.3.3, numpy 2.5.5, duckdb 2.0.0 и др.) — lock-файлы приводятся к таблицам п. 3–4 до спайков на `windows-latest`.

## Альтернативы

| Вариант | Почему нет |
|---------|-----------|
| Всё самое новое (React 19, TS 7, Vite 8, three 0.187, numpy 2.5, pandas 3) | Нарушает правило ТЗ; большой объём непроверенных изменений на старте |
| Отдельные lock-файлы `ui` и `app` без workspaces (как в BF) | Дублирует общие dev-пакеты, требует сверки версий скриптом; изоляция упаковки уже достигается сборкой esbuild |
| numpy 2.5.5 / 2.4.6 | Вышли после pandas 2.3.3 (последней 2.x); выгоды нет, риск есть |
| asyncpg (Apache-2.0) | Только asyncio; ядро синхронное (SQLite) |
| **Предложение заказчику:** после В1 обновить dev-инструменты UI (Vite, vitest) отдельным спайком | Снимет предупреждения `npm audit` |

## Проверка

| Что | Как | Статус |
|-----|-----|--------|
| Колёса `cp312-win_amd64` | `pip download --only-binary=:all: --platform win_amd64` для 22 бинарных пакетов; `uv lock` с `no-build` | Выполнено в облаке |
| Совместимость набора Python | `uv sync --locked` (Python 3.12.16, Linux) + дымовой скрипт: импорты, numba `cache=True` в папке с кириллицей и пробелом, булева операция manifold3d, pythoncdt, earcut, marching cubes → pyfqmr, Parquet zstd → DuckDB без автозагрузки расширений, OR-Tools max-flow, CSV с десятичной запятой, Ed25519 | Выполнено в облаке (Linux) |
| То же на Windows в `onedir` | **Спайк а**: PyInstaller 6.22.3 на `windows-latest`, тот же дымовой скрипт из `bf-core.exe`, размер папки | Э0 |
| Electron 45.2.3 + three 0.170.0 + three-mesh-bvh, 1 млн треугольников, программный рендер; Playwright 1.49.1 | **Спайк б** (в облаке на Linux уже запускается, см. ADR 0004) | Э0 |
| Ядро на 127.0.0.1 с токеном, 401 без токена; запуск из пути с кириллицей | **Спайки в, г** (ADR 0002, 0005) | Э0 |
| Лицензии и SBOM | `pip-licenses`, `license-checker-rseidelsohn` с белым списком в `ci.yml` | Э0 (каркас CI) |

## Итоги спайков Э0 (2026-11-16)

| Спайк | Где | Результат |
|-------|-----|-----------|
| а. PyInstaller 6.22 onedir: numpy, scipy, numba, manifold3d, PythonCDT, pyfqmr, duckdb, pyarrow считают (`bf-core --selftest`) | `windows-latest`, run 43916849569; Linux | ✅ onedir 434 МБ (Windows); portable `.exe` 214 МБ, NSIS 243 МБ (бюджет 400 МБ) |
| б. Electron 45 + three 0.170, 1 млн треугольников, программный WebGL2, кадр — артефакт | Linux (xvfb) и `windows-latest` | ✅ рендер «SwiftShader (Subzero)» с `--enable-unsafe-swiftshader`; ≈ 0,23–0,9 с на кадр; `artifacts/spike-mesh-1m.png` |
| в. Ядро на 127.0.0.1 с токеном отвечает окну `app://`; без токена 401; чужие Host/Origin 403 | Linux, `windows-latest` (собранный `.exe`) | ✅ |
| г. Запуск из пути с кириллицей и пробелами, кеш numba там же | `windows-latest`: `…\Мои проекты\bf core`, приложение — `…\Программы BF\BedrockForge 2` | ✅ кеш numba: первый запуск 1,24 с, повторный 0,19 с |

Находка спайка г: в собранном `bf-core.exe` numba не использует `NUMBA_CACHE_DIR` (у функций относительный
`co_filename`, исходника на диске нет) и пишет кеш в общий `%LOCALAPPDATA%\numba`. Решение —
собственный локатор `core/bf_core/numba_cache.py` (свежесть кеша — по штампу `.exe`, как у numba для
frozen-приложений); проверка — шаг «Спайк а/г» в `windows.yml` (≥ 2 файла кеша в заданной папке).

Спайки а–г пройдены на наборе «последних» версий; закреплённый набор этого ADR проверяется тем же
`windows.yml` в PR Э0 (Python 3.12.16, numpy 2.3.5, Electron 45.2.3 и т. д.).

## Дополнение Э3 (2026-11-17)

| Пакет | Версия | Лицензия | Зачем |
|-------|--------|----------|-------|
| xlrd | 2.0.2 | BSD-3-Clause | Чтение `.xls` (ТЗ 9.12: «`.xls` — да»; 7.7 «отклоняется» — более ранняя редакция, принята 9.12). Только чтение старого формата; `.xlsx` по-прежнему openpyxl |

## Дополнение Э7 (2026-11-17)

| Пакет | Версия | Лицензия | Зачем |
|-------|--------|----------|-------|
| pyshp | 3.1.7 | MIT | Чтение ESRI Shapefile (Д-13, ADR 0009, ADR 0011 § 6): геометрия записей `.shp` — точки, линии, полигоны, MultiPoint с Z и M (`core/bf_core/formats/shp.py`); в тестах — запись эталонных файлов |

- Выбор версии: 3.1.7 выпущена 2026-11-07 — последняя до `exclude-newer` 2026-11-09 (П3); 3.1.8 (2026-11-09 17:22 UTC) и 3.1.9 (2026-11-16) — вне окна. Не `.0` (П2). `requires-python >= 3.9`.
- Состав: чистый Python, колесо `py3-none-any` 74 КБ, один модуль `shapefile.py`, зависимостей нет, вложенных DLL нет (правило ADR 0009 о колёсах с `*.libs` не затрагивается). `uv lock`: 96 пакетов, добавлен только pyshp; `check_licenses_py.py` — «licenses ok: 71 distributions».
- В `bf-core.exe` попадает сам: PyInstaller видит `import shapefile` в `formats/shp.py` (как `xlrd` и `openpyxl` в `formats/excel.py`); правка `bf-core.spec` не нужна.
- Граница использования (замер 7.7, 1 млн вершин, бюджет Д-13 — 10 с): pyshp читает геометрию запись за записью (линии 10 000 × 100 вершин — 1,0–1,1 с; 250 000 полигонов по 4 вершины — 1,9–2,0 с; память ≤ 235 МБ). Файл одиночных точек с записями одной длины и колонки `.dbf` читаются таблицей numpy (`formats/shp.py`, `formats/dbf.py`): через pyshp 1 млн точек с тремя полями — 6,9 с (геометрия 4,3 с + записи 2,6 с), таблицей — 0,7–0,8 с. `.shx` не используется: записи обходятся по `.shp` с проверкой длин до выделения памяти.
