# Геологическая система: скважины и аудит данных

Веб-приложение для геолога и первичного аудита данных ГРР. Работает в Chrome на Windows и macOS.

## Структура

| Путь | Назначение |
|---|---|
| `api/` | Бэкенд на FastAPI: чтение файлов, аудит, работа с PostgreSQL |
| `api/app/formats/` | Форматы файлов. Сейчас: Micromine DAT (нативное чтение) |
| `web/` | Интерфейс на React + TypeScript + Vite |
| `docker-compose.yml` | PostgreSQL + PostGIS, API, веб-интерфейс |

## Импорт (этап 2.1)

Экран импорта: загрузка `.DAT` (Micromine), автоопределение типа таблицы (Collar / Survey / Assay),
назначение колонок, выбор компонентов с единицами, проверка и предпросмотр.

| Эндпоинт | Назначение |
|---|---|
| `GET /api/import/schemas` | Целевые таблицы и поля |
| `POST /api/import/upload` | Загрузка файла, предпросмотр, предложенное назначение колонок |
| `POST /api/import/validate` | Проверка назначения, нормализованный предпросмотр |

Сохранение в PostgreSQL появится на этапе 3. Форматы CSV и Excel — на этапе 2.2.

## Запуск (Docker)

```bash
docker compose up --build
```

- Интерфейс: http://localhost:5173
- API: http://localhost:8000/api/health
- PostgreSQL: localhost:5432 (пользователь и база `geo`)

## Тесты API

```bash
cd api
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

Проверка на реальных файлах проекта (не хранятся в репозитории):

```bash
MICROMINE_SAMPLE_DIR=/путь/к/папке/с/DAT pytest
```

## Данные

Файлы проекта (`*.DAT`, Excel, CSV) в git не коммитятся. См. `.gitignore`.
