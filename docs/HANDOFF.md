# BedrockForge — передача контекста (ветка `base-functional`)

Состояние на 2026-11-14, коммит `a80cd10`. Документ для старта новой сессии: что сделано, как устроено, где что лежит, что дальше.

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
| Секреты | `ANTHROPIC_API_KEY` только из окружения, в репозиторий не коммитить |

## 2. Запуск

```bash
git pull
docker compose up --build
```

- Веб: http://localhost:5173, API: http://localhost:8000.
- Лента → «Демо-проект» создаёт учебный проект (48 скважин, каркасы, линии, растры в плане и в разрезе).
- Локальная разработка без Docker: PostgreSQL 16 (`pg_ctlcluster 16 main start`), `uvicorn app.main:app` в `api/`, `npm run dev` в `web/`.
- Тесты: `cd api && python3 -m pytest` (149 passed / 5 skipped), `cd web && npm test` (26 passed). Не использовать `/root/.local/bin/pytest` — без alembic.

## 3. Стек

| Слой | Технологии |
|---|---|
| API | FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, numpy, pandas, Pillow, ezdxf |
| Claude | anthropic SDK: `beta.messages.create`, adaptive thinking, effort medium, prompt caching, server-side fallback |
| MCP | MCP SDK, streamable HTTP, stateless, защита от DNS rebinding |
| Веб | React 18 + TypeScript + Vite 5, three.js 0.170 |

## 4. Модель данных

```
Project → Dataset (kind: drillholes | lines | wireframes | blocks | rasters)
  drillholes → Collar / Survey / Assay / Component / ImportJob / AuditRun / AuditFinding  (по set_id)
  wireframes → Wireframe
  lines      → LineLayer
  rasters    → Raster (georef JSONB, extent, elevation, opacity, image BYTEA)
```

- Наборы скважин независимые; API скважин — `/api/sets/{id}/…`.
- Миграции: `0003_datasets` (наборы, перенос скважин в набор «Скважины»), `0004_lines_rasters`.
- Геометрия на фронт: base64 float32 относительно origin; origin сцены — по первому объекту.

## 5. Форматы (`api/app/formats/`, подробно — `docs/FORMATS.md`)

| Данные | Форматы | Файл |
|---|---|---|
| Скважины | Micromine DAT (типы C/N/R…, кодовые страницы), CSV, Excel | `micromine.py`, импорт |
| Каркасы | Micromine TRIDB, DXF (сетки) + статистика: площадь, объём, замкнутость | `wireframes.py` |
| Линии | Micromine STR, Surpac STR, DXF | `lines.py` |
| Растры | GeoTIFF, World (.jgw/.pgw/.tfw), MapInfo TAB, GRF (в т.ч. с Z → вертикальный) | `rasters.py` |

Привязка растра: аффинное преобразование по углам пикселей `X=a·col+b·row+c, Y=d·col+e·row+f`, для вертикальных ещё `Z=g·col+h·row+i`.

## 6. Интерфейс (`web/src/`)

- **Реестр команд** `shell/registry.ts` — единый список для ленты, палитры (Ctrl+K), хоткеев, ui-инструментов Claude и MCP-моста (SSE). Флаги `confirm`, `uiOnly`, `silent`.
- **Лента** `shell/ribbonConfig.ts` (не `ribbon.ts` — конфликт регистра с `Ribbon.tsx` ломал Windows).
- **Дерево данных** `shell/DataTree.tsx`: Скважины (наборы), Линии, Каркасы, Блочные модели (заглушка), Растры.
- **Свойства** `shell/PropertiesPanel.tsx`: набор, скважина, каркас, линии, растр (вкл. форму вертикальной привязки).
- **Состояние рабочего места** `shell/workspace.ts`, ключ `bedrockforge.workspace.v3`.
- **3D-сцена** `viz/scene3d.ts` (GeoScene) + `viz/SceneView.tsx` (тулбар), `viz/geometry.ts` (SectionLine).

### Виды и разрез

| Кнопка | Хоткей | Действие |
|---|---|---|
| Сверху Y/X | T | Вид сверху |
| Сбоку Z/X | X | Вид на север |
| Сбоку Z/Y | Y | Вид на запад |
| 3D | 3 | Изометрия |
| Вписать | F | Вписать, не поворачивая |
| Разрез | S | Два клика — линия; вид встаёт в плоскость, Z вверх |
| Отсечение | C | Оставить только окно «к себе / от себя» |
| ◀ ▶ | [ ] | Шаг профиля |
| Через скважину | — | Линия через устье по азимуту траектории |
| Авто-разрез | — | По данным |

- `SectionLine {x0, y0, az, length, towards, away}`: зритель справа от линии, линия слева направо; окно по умолчанию 25 м.
- Сечения каркасов считаются в JS; отсечение — clipping planes по всей сцене.
- Вертикальный растр при включённом отсечении — подложка (рисуется под скважинами).

## 7. Claude и MCP (`api/app/`)

| Файл | Назначение |
|---|---|
| `assistant/service.py` | Цикл диалога: data-инструменты на сервере, ui-инструменты уходят в браузер |
| `assistant/tools.py` | Инструменты данных (`set_id`, `resolve_set`, `data_list_datasets`) |
| `mcp_server.py` | MCP-инструменты с `set_id` |
| `ui_bridge.py` | SSE-мост к браузеру |
| `config.py` | Настройки ассистента/MCP |

Ключевые команды реестра: `scene.view`, `scene.fit`, `scene.style`, `layer.show`, `set.select`, `hole.select`, `object.select`, `raster.set`, `raster.place_on_section`, `section.set / draw / step / clear / clip / window / through_hole`, `audit.run / export`, `*.upload`, `project.demo`, `assistant.ask`.

## 8. Прочее в ветке (из другой сессии)

- `app.stats`: декластеризация, нормальные метки, вариограммы, карта вариограмм (порт из GeostatsPy) + эндпоинты.
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

## 10. Что дальше

- [ ] Привязка скана разреза по опорным точкам в UI (клик по пикселю + ввод X/Y/Z).
- [ ] Проверить на реальных файлах пользователя: `Pit.tridb`, `TST-FAULTS.DXF`, контуры `.STR`, `Восточный_TS.jpg` (+ .jgw/.TAB/.grf), вертикальный GRF.
- [ ] Кнопка удаления проекта в UI.
- [ ] Блочные модели — нужны образцы файлов.
- [ ] Подключить `app.stats` (вариограммы) к интерфейсу и к Claude.
- Общий план — `docs/ROADMAP.md`.
