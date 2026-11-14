# Геологическая система: скважины и аудит данных

Веб-приложение для геолога и первичного аудита данных ГРР. Работает в Chrome на Windows и macOS.

## Структура

| Путь | Назначение |
|---|---|
| `api/` | Бэкенд на FastAPI: чтение файлов, аудит, работа с PostgreSQL |
| `api/app/formats/` | Форматы файлов. Сейчас: Micromine DAT (нативное чтение) |
| `web/` | Интерфейс на React + TypeScript + Vite |
| `docker-compose.yml` | PostgreSQL + PostGIS, API, веб-интерфейс |

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
