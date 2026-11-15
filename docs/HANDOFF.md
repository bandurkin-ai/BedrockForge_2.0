# BedrockForge — передача контекста (ветка `base-functional`)

Состояние на 2026-11-14 (после этапа 7: хранилище, загрузка частями, фоновые задания). Документ для старта новой сессии: что сделано, как устроено, где что лежит, что дальше.

## 0. Документы

| Документ | Что внутри | Когда читать |
|---|---|---|
| `docs/ROADMAP.md` | Редакция 2: точность, производительность (каркасы 200+ МБ, блочные модели до 2 ГБ), этапы 7–29, вехи M0–M7, бюджеты и замеры | Перед выбором следующего этапа |
| `docs/FORMATS.md` | Побайтное описание STR Micromine, TRIDB, DXF polyface, world/TAB/GRF-привязки по образцам пользователя | Перед чтением или записью любого формата |
| `docs/HANDOFF.md` (этот файл) | Состояние кода, запуск, грабли, список дел | Первым при старте сессии |

## 1. Договорённости

| Тема | Решение |
|---|---|
| Ветка | Работаем только в `base-functional` (бывшая `ccr-2cd541a4-54l2t6`) |
| Язык ответов | Русский, кратко, таблицы и буллиты |
| Платформа | Десктоп-веб, смартфон не нужен |
| Тема | Тёмная по умолчанию, переключатель есть |
| Раскладка | Лента + дерево данных слева + один 3D-вьюпорт + свойства/Claude справа + нижняя панель |
| Claude | В обе стороны: панель в приложении (Claude API) и MCP-сервер |
| Окно | Всегда 3D. Кнопки только поворачивают вид или отсекают данные («как в Micromine») |
| Форматы | Читаем всё заявленное, но реальная работа — скважины и каркасы; блочные модели — заглушка |
| Секреты | Ключи в репозиторий не коммитить. Ключ, введённый в окне «Claude: подключение», хранится в `<DATA_DIR>/assistant/connection.json` (600) и в браузер не отдаётся |

## 2. Запуск

```bash
git pull
docker compose up --build
```

- На Windows: `scripts/BedrockForge.bat` делает то же самое с логом `BedrockForge_log.txt` рядом с файлом (прошлый запуск — `BedrockForge_log_prev.txt`); путь к репозиторию — переменная `REPO` в начале файла. Файл можно держать где угодно.
- Веб: http://localhost:5173, API: http://localhost:8000. Сервис `worker` выполняет фоновые задания; хранилище — том `store` (`DATA_DIR=/data`).
- Лента → «Демо-проект» создаёт учебный проект (48 скважин, каркасы, линии, растры в плане и в разрезе).
- Локальная разработка без Docker: PostgreSQL 16 (`pg_ctlcluster 16 main start`; пользователь `geo`/`geo`, базы `geo` и `geo_test`), `alembic upgrade head` и `uvicorn app.main:app` в `api/` (задания идут в потоке API, `JOBS_IN_API=1`), `API_URL=http://localhost:8000 npx vite` в `web/`.
- Тесты: `cd api && python3 -m pytest` (281 passed / 17 skipped), `cd web && npm test` (81 passed). Не использовать `/root/.local/bin/pytest` — без alembic.
- Замеры: `cd api && python -m bench.run --profile ci --check` (1 млн треугольников) или `--profile full` (10 млн, 2 ГБ).

## 3. Стек

| Слой | Технологии |
|---|---|
| API | FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, numpy, pandas, Pillow, ezdxf |
| Хранилище | Файлы по SHA-256 в `DATA_DIR` (`app/storage/`), очередь заданий в PostgreSQL (`app/jobs/`) |
| CI | `.github/workflows/ci.yml`: тесты API и веба, замеры с порогами и сравнением с базовой веткой |
| Claude | anthropic SDK: `beta.messages.create`, adaptive thinking, effort medium, prompt caching, server-side fallback |
| MCP | MCP SDK, streamable HTTP, stateless, защита от DNS rebinding |
| Веб | React 18 + TypeScript + Vite 5, three.js 0.170 |

## 4. Модель данных

```
Project → Dataset (kind: drillholes | lines | wireframes | blocks | rasters)
  drillholes → Collar / Survey / Assay / Component / ImportJob / AuditRun / AuditFinding  (по set_id)
               IntervalTable (имя, колонки [{name, kind number|text, unit}]) → IntervalRow (hole_id, from_m, to_m, values JSONB)
  wireframes → Wireframe
  lines      → LineLayer
  rasters    → Raster (georef JSONB, extent, elevation, opacity, blobs.image)
  blocks     → всё в Dataset: meta (сетка, поля, проверки, report) и blobs: centres <f8, sizes <f4, render <f4 ×6, a0… (поля: <f8 или коды <i4)
Blob (sha256, size)        — файлы хранилища; ссылки — JSONB-колонки `blobs` у dataset, wireframe, line_layer, raster
Upload                     — загрузка частями (DATA_DIR/uploads/<id>.part)
Job                        — фоновое задание: статус, прогресс, журнал, отмена, попытки
```

- Наборы скважин независимые; API скважин — `/api/sets/{id}/…`. У `Dataset` есть `folder` (папка в дереве).
- Миграции: `0003_datasets`, `0004_lines_rasters`, `0005_object_store_jobs` (перенос bytea → файлы и обратно), `0006_wireframe_render`, `0007_charts`, `0008_interval_tables`.
- Геометрия: точные массивы float64 (`blobs.vertices`), копия для показа float32 относительно `origin` в центре объекта (`blobs.render_vertices`); отдаётся сырыми байтами через `/api/blobs/{sha}`, читается в Web Worker (`viz/geometryLoader.ts`). Каждый объект в сцене стоит в своём начале (`mesh.position`), буферы не копируются.
- Новый тип данных с файлами → добавить таблицу в `OWNERS` (`app/storage/gc.py`), иначе очистка удалит его файлы.

## 5. Форматы (`api/app/formats/`, подробно — `docs/FORMATS.md`)

| Данные | Форматы | Файл |
|---|---|---|
| Скважины | Micromine DAT (типы C/N/R…, кодовые страницы), CSV, Excel | `micromine.py`, импорт |
| Каркасы | Micromine TRIDB, DXF (сетки) + статистика: площадь, объём, замкнутость | `wireframes.py` |
| Линии | Micromine STR, Surpac STR, DXF | `lines.py` |
| Растры | GeoTIFF, World (.jgw/.pgw/.tfw), MapInfo TAB, GRF (в т.ч. с Z → вертикальный) | `rasters.py` |
| Блочные модели | Micromine DAT (memmap, столбцы), Micromine CSV, CSV | `blockmodel.py`, `micromine.read_columns`, `bocu1.py` |

Привязка растра: аффинное преобразование по углам пикселей `X=a·col+b·row+c, Y=d·col+e·row+f`, для вертикальных ещё `Z=g·col+h·row+i`.

## 6. Интерфейс (`web/src/`)

- **Реестр команд** `shell/registry.ts` — единый список для ленты, палитры (Ctrl+K), хоткеев, ui-инструментов Claude и MCP-моста (SSE). Флаги `confirm`, `uiOnly`, `silent`.
- **Лента** `shell/ribbonConfig.ts` (не `ribbon.ts` — конфликт регистра с `Ribbon.tsx` ломал Windows).
- **Дерево данных** `shell/DataTree.tsx`: Скважины (наборы), Линии, Каркасы, Блочные модели (заглушка), Растры; папки внутри групп, «В папку…».
- **Задания** `shell/JobsPanel.tsx` + `shell/useJobs.ts`: вкладка «Задания» нижней панели, передача файлов, ход по SSE, журнал, отмена. Загрузка — `uploadResumable` в `api.ts`.
- **Свойства** `shell/PropertiesPanel.tsx`: набор, скважина, каркас, линии, растр (вкл. форму вертикальной привязки).
- **Состояние рабочего места** `shell/workspace.ts`, ключ `bedrockforge.workspace.v3`.
- **3D-сцена** `viz/scene3d.ts` (GeoScene) + `viz/SceneView.tsx` (тулбар), `viz/geometry.ts` (SectionLine).

### Виды и разрез

| Кнопка | Хоткей | Действие |
|---|---|---|
| В план | T | Вид сверху на текущую область (центр и масштаб сохраняются; `GeoScene.toPlan`). |
| Сбоку Z/X | X | Вид на север |
| Сбоку Z/Y | Y | Вид на запад |
| 3D | 3 | Изометрия |
| Вписать | F | Вписать, не поворачивая |
| Разрез | S | Два клика — линия, в текущем виде (без поворота и вписывания) или на мини-плане. Точки берутся на плоскости через центр вида (`GeoScene.pickPoint`: горизонтальной, а для вида сбоку — вертикальной к зрителю); оранжевая «резинка» (`setDraft`). Потом вид встаёт в плоскость и вписывает линию от края до края |
| План (в разрезе) | — | Мини-план `viz/SectionMap.tsx`: скважины, линии, контуры каркасов и растров (`GeoScene.planOverview`, до 20 000 точек), линия разреза, окно, сторона взгляда. Видимость в localStorage `geo.sectionMap` |
| Отсечение | C | Оставить только окно «к себе / от себя» |
| ◀ ▶ | [ ] | Шаг профиля |
| Через скважину | — | Линия через устье по азимуту траектории |
| Авто-разрез | — | По данным |

- `SectionLine {x0, y0, az, length, towards, away}`: зритель справа от линии, линия слева направо; окно по умолчанию 25 м.
- Сечения каркасов считаются в JS (в координатах объекта); отсечение — clipping planes по всей сцене. Точное сечение на сервере — этап 10.5.
- Вертикальный растр при включённом отсечении — подложка (рисуется под скважинами).

## 7. Claude и MCP (`api/app/`)

| Файл | Назначение |
|---|---|
| `assistant/service.py` | Цикл диалога: data-инструменты на сервере, ui-инструменты уходят в браузер. Диалоги — в БД (`AssistantChat`: `messages` — история Messages API с thinking-блоками, `items` — что показывает панель, `pending` — ход, ждущий браузер), расход каждого запроса — `AssistantUsage` (миграция 0011) |
| `assistant/connection.py` | Одно подключение на установку: ключ из окна, вход аккаунтом через `ant auth login --no-browser` (pty; профиль `bedrockforge` в `<DATA_DIR>/assistant/anthropic`, SDK: `Anthropic(profile=...)`), либо окружение; модель/глубина, цены моделей и оценка стоимости |
| `assistant/tools.py` | Инструменты данных (`set_id`, `resolve_set`, `data_list_datasets`) |
| `mcp_server.py` | MCP-инструменты с `set_id` |
| `ui_bridge.py` | SSE-мост к браузеру |
| `config.py` | Настройки ассистента/MCP |

Ключевые команды реестра: `scene.view`, `scene.fit`, `scene.style`, `layer.show`, `set.select`, `hole.select`, `object.select`, `raster.set`, `raster.place_on_section`, `section.set / draw / step / clear / clip / window / through_hole`, `audit.run / export`, `*.upload`, `project.demo / delete`, `dataset.move`, `jobs.list / cancel`, `assistant.ask`.

## 8. Прочее в ветке (из другой сессии)

Параллельно ветку развивает другая сессия — перед работой `git pull`.

- `api/app/stats/`: декластеризация, нормальные метки, вариограммы, карта вариограмм (порт из GeostatsPy), неопределённость (bootstrap, локальная статистика, IDW); эндпоинты в `routers/stats.py`.
- Веб: вкладка статистики `web/src/StatsPage.tsx`, `web/src/stats/` (графики, анализы, умолчания).
- Папка шаблонов форматов проекта.

## 9. Грабли

| Проблема | Решение |
|---|---|
| Белый экран на Windows | Файлы, различающиеся только регистром, — запрещены |
| Dataclass: non-default после default | Новые поля с default — в конец |
| Postgres упал между запусками | `pg_ctlcluster 16 main start` |
| Новый набор после импорта сбрасывался | `pendingSet` + `datasetsKey` |
| Разрез вписывался во весь проект | `fit()` учитывает только объекты в окне отсечения |
| Каркасы ребром не видны в разрезе | Считаем линии сечения |
| Тесты и хранилище | Корень хранилища задаёт фикстура `blob_store` (conftest), задания запускает фикстура `run_jobs` |
| Источник импорта «пропал» после отмены | Исходный файл переносится в хранилище последним шагом задания, после этого отмена невозможна |
| Имя скачанного файла «download» в тестах Playwright | Особенность безэкранного Chromium для blob-ссылок; в обычном браузере имя из атрибута `download` |
| pyfqmr падает на memmap | Ему нужны записываемые массивы: `np.array(..., copy)` в `geo/meshes._simplify_one` |
| Каркасы до этапа 10 | `render` пустой: рисуются одной частью без уровней; «проверить» в свойствах строит всё заново |
| Двойная загрузка из мастера | StrictMode дважды запускает эффект монтирования: передача стартует один раз по флагу в `useRef` |
| Строка поля в DAT: имя в 10 символов | Тип идёт сразу за именем без пробела (`RUN_AU_CUTN 12 8`), короткие имена дополнены пробелами. Разбор `_VARIABLE_RE` допускает нулевой промежуток; раньше такие файлы не читались совсем (и cp1251-файлы тоже) |
| Кодировка `ibm-5347_P100-1998` | Имя кодировки в заголовке — ICU с суффиксом; `codec_of` отбрасывает `_P100-1998`. Раньше кириллица в cp1251-файлах читалась как ASCII |
| BOCU-1 в DAT | Не «ASCII + 0x50», а настоящий BOCU-1 (кириллица — многобайтово). Декодер `formats/bocu1.py` сверен с ICU 74 (`libicuuc`, `ucnv_convert`), векторы — в `tests/test_bocu1.py`. Старый тестовый построитель кодировал A–O без сдвига — исправлен |
| «Неизвестный тип задания import.objects» у всех заданий (Docker) | Главная причина: `python -m app.jobs.worker` исполняет файл как `__main__`, а обработчики регистрируются в другой копии модуля `app.jobs.worker` (она импортируется из `handlers.py`) — у исполнителя список обработчиков пуст. Теперь `__main__` запускает настоящий модуль (`tests/test_worker_process.py` запускает процесс). Тесты и uvicorn с `JOBS_IN_API` этого не показывали. Дополнительно: `_ensure_handlers` под блокировкой, вызывается в основном потоке до запуска исполнителей и повторно в `run_job`; при ошибке импорта задание показывает её. Упавшее задание — «Повторить» (`POST /api/jobs/{id}/retry`, загрузки те же) |
| Пиковая память `--profile full` | Данные готовятся в отдельном процессе (`--prepare`), иначе генерация попадает в замер |

## 10. Что дальше

Этапы 7 и 10 сделаны (вехи M0, M1). Этап 12 начат: импорт, проверки, отчёт по бортам и показ блочных моделей (12.1–12.3, 12.6 частично). Не сделано: Datamine DM, поворот модели, срез произвольной плоскостью на сервере и показ только внешних граней для моделей > 3 млн блоков (12.4–12.5).

Интервальные таблицы и данные по стволу: импорт — тип `interval` в `/api/import/commit` (+ `table_name`), запись — `importing/store.commit_interval_table`; `routers/intervals.py` (`/sets/{id}/interval-tables`, `/interval-tables/{id}` PATCH/DELETE/rows, `/sets/{id}/downhole`; пробы — встроенная таблица с ключом `assay`). Аудит, статистика и 3D по-прежнему работают только с пробами. Веб — `charts/stripLog.ts` (настройки, чистые функции), `StripLogChart.tsx`, `StripLogWindow.tsx`; сохраняются как графики вида `striplog`, в дереве — узел «Данные по скважинам».

**Источник проб для аудита и статистики.** `app/samples/source.py`: `resolve_source(session, set_id, key)` (ключ `assay` или id интервальной таблицы; без ключа — встроенные пробы, если есть, иначе первая таблица с числами) и `source_sql` — подзапрос `src` с колонками как у `assay` (для таблицы её `values` идут как `analytes`). Им пользуются `routers/stats.py` (`table` в телах запросов и в `/stats/fields`, список — `/sets/{id}/stats/tables`) и аудит (`AuditConfig.table`, сохраняется в `AuditRun.config`). Файл проб с названием не «Пробы» импортируется отдельной интервальной таблицей (`routers/imports.py`). Веб: `shell/SampleTableSelect.tsx` (`useSampleTables`, `effectiveTable`, `hasSamples`). Не переведены на выбор таблицы: карточка скважины (счётчик проб), инструмент ассистента с пробами (`assistant/tools.py`), `/sets/{id}/intervals`.

**Данные по стволам в главном окне (3D и разрезы).** Отображение = сохранённый график вида `downhole` (настройки `StripLogSettings`: слои `tracks[]` любых таблиц набора, `form` — только подсказка первого поля, `template` — ссылка на шаблон); включённые — `ws.logs`. В дереве: «Устья» (шестерёнка → `CollarStyleForm`, стиль в `ws.collarStyles`, типы в `viz/collarStyle.ts` — без three.js, чтобы не тянуть его в основной чанк), «Стволы» (ПКМ → форма `viz/downhole/DownholeForm.tsx`, строки отображений под ним). Форма — окно с предпросмотром (`App`: `forms`, `previews`); сохраняет только кнопка (`saveDisplay`). `logLayers` идут в порядке дерева; `SceneView` считает для каждого начало (`stackOf` → `StackStart`), чтобы колонки и трубки нескольких отображений не накладывались. Геометрия — `viz/downholeGeometry.ts` (`buildDownhole`: трубки, колонки с атрибутами `aTan`/`aAmp`, значки событий, подписи); сторона колонки — в шейдере (`GeoScene.sideMaterial`), колонка не уже `PANEL_MIN_PX`. Раскраска по градациям — `Track.colorBy = "classes"`, `classes`. Форма — модули `viz/downhole/`: каркас `DownholeForm.tsx` (история отмены, слои, полосы стиля), `LayerList`, `sections/*` (Данные, Вид, Шкала, Цвет, Фильтр, Подписи), `ClassEditor` + `HistogramPlot` (гистограмма с границами), `CategoryEditor` (виртуальный список), `StyleBar`/`StyleGallery`/`ApplyDialog`/`ImportDialog`, чистые `classify.ts`, `gradients.ts`, `histogram.ts`, `styles.ts` (`resolveTracks` — подставляет связанные стили и раскраски; вызывается в форме и в `App.logLayers`; `unlinkStyle`, сопоставление полей шаблона по имени, формат файла, встроенные стили с отрицательными id). Новые поля `Track` (сторона, `widthM`, `offsetM`, прозрачность, `split`, `over`, `radius`, `filter`, `label`, `gradient`, `colorField`, `hidden`, `catOrder`, `catMarkers`, `style`, `palette`) необязательны: старые отображения открываются как раньше. Геометрия: `placeColumns` раскладывает колонки по сторонам (`StackStart` хранит занятое слева и справа: единицы колонки + метры), фильтр и особые значения (`cleanValue`, `passesFilter`) до построения, точки — `SidePoints`, цвета панелей RGBA (прозрачность). Библиотека стилей — таблица `style` (`level` palette / layer / display, `data_kind`, `body`, `default_for`, `extra`), миграция 0010 (переносит прежние `palette`), `routers/styles.py`: CRUD, `/styles/{id}/usage`, экспорт и импорт `.bfstyle.json` с решениями по конфликтам. Стили проекта загружает `App` (`styles`), форма меняет их через `onStyles`. События — `interval_table.kind = "event"` (миграция 0009; глубина в `from_m = to_m`), импорт — тип `event`. `POST /sets/{id}/downhole`: `all`, `fields`. Старой раскраски по компоненту в сцене нет (слой `intervals`, команда `viz.set` убраны; `ComponentPanel`/`VizSettings` остались только как тип). Окно «Данные по скважинам» (`StripLogWindow`) — просмотр, не сохраняется (вид `striplog` в дереве не показывается). Команды Claude: `holes.show_data` (сохраняет отображения), `holes.data_window` / `chart.striplog` (окно).

Блочные модели, где что лежит: `app/formats/blockmodel.py` (чтение, сетка, проверки), `app/importing/blockmodels.py` (запись столбцов), `app/blocks/report.py` и `app/routers/blockmodels.py` (`/render`, `/values/{key}`, `/blocks/{n}`, `/report`, единицы полей); веб — `shell/BlockModelPanel.tsx`, `charts/GradeTonnageWindow.tsx`, `viz/blockStyle.ts`, блоки в `viz/scene3d.ts` (`addBlockModel`, `setBlockStyle`, `pickBlock` — InstancedMesh), распознавание файла — `shell/sniffBlocks.ts`; команда Claude `blocks.report`.

Этап 10, где что лежит: `app/formats/dxf_stream.py` (потоковый DXF), `wireframes.py` (TRIDB потоком, OBJ, STL), `app/geo/meshes.py` (Z-порядок, части, упрощение), `app/geo/checks.py` (самопересечения на numba, нормали), `app/geo/section.py` (точное сечение), задание `wireframes.derive` в `app/jobs/handlers.py`; в вебе — уровни в `viz/scene3d.ts` (`addWireframe`, `setWireframeLevel`, `setWireframeCut`), загрузка от грубого к точному в `viz/SceneView.tsx`.

- [x] 7.6: мастер импорта `shell/ImportWizard.tsx` (кнопка «Импорт файлов», перетаскивание в окно, кнопки «Каркасы/Линии/Растры»): передача → состав файлов (`app/formats/inspect.py`) → тип, папка, растры без привязки на разрез → задания.
- [ ] Импорт таблиц скважин (`/api/import/*`) пока идёт в запросе — перевести на задания в этапе 8.
- [x] Этап 10: потоковое чтение TRIDB и DXF, OBJ/STL, проверки, части, уровни детализации, точное сечение на сервере.
- [x] Гистограмма проб (правая кнопка в дереве): `app/stats/histogram.py`, эндпоинты `stats/fields` и `stats/histogram` в `routers/stats.py`; веб — `charts/` (окно, график, экспорт), `shell/ContextMenu.tsx`.
- [x] Q-Q график: `app/stats/qq.py`, `POST /api/stats/qq` (общая загрузка значений — `load_field_values` в `routers/stats.py`); веб — `charts/QQWindow.tsx`, `charts/QQChart.tsx`.
- [x] Дендрограмма: `app/stats/dendrogram.py`, `POST /api/sets/{id}/stats/dendrogram`; веб — `charts/DendrogramWindow.tsx`, `charts/DendrogramChart.tsx`.
- [x] Сохранённые графики: таблица `chart` (миграция `0007`), `routers/charts.py`; в дереве — узел «Графики» набора; окна сообщают настройки через `onSettings`, `App.tsx` сохраняет их с задержкой 0,8 с.
- [ ] Остатки этапа 10: Surpac DTM (нужен образец), Datamine (после читателя DM), подгрузка частей по Range в порядке видимости, BVH для запроса по клику (этап 16).
- [ ] Каркас больше 200 МБ и блочная модель 1–2 ГБ от пользователя — без них этапы 10 и 12 идут на синтетике (`api/bench/synthetic.py`).
- [ ] Привязка скана разреза по опорным точкам в UI (этап 11).
- [ ] Проверить на реальных файлах пользователя: `Pit.tridb`, `TST-FAULTS.DXF`, контуры `.STR`, `Восточный_TS.jpg` (+ .jgw/.TAB/.grf), вертикальный GRF.
- [ ] Дать Claude/MCP инструменты статистики (`app.stats`); UI уже есть.
- [x] Кнопка удаления проекта в UI (лента «Главная → Проект»).
- Общий план — `docs/ROADMAP.md`; замеры этапа 7 — раздел 2.4.
- Образцы, полученные для форматов: STR, TRIDB, DXF polyface, растр с JGW/TAB/GRF — разобраны в `docs/FORMATS.md`. Не получены: литология и QAQC (этап 8), DWG и DTM Surpac (этапы 9–10), блочные модели Datamine DM и текст (этап 12).
