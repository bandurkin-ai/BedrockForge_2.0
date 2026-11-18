# BedrockForge 2

**BedrockForge by Mikhail Bandurkin** — горно-геологическая информационная система для Windows 11:
данные бурения, 3D, разрезы, каркасы, статистика, геостатистика, блочные модели, ресурсы,
оптимизация карьера, печать. Работает офлайн, интерфейс на русском.

| Часть | Технология | Папка |
|---|---|---|
| Оболочка | Electron 45 | `app/` |
| Интерфейс | React 18, TypeScript, three.js | `ui/` |
| Ядро `bf-core` | Python 3.12, FastAPI, numba, SQLite/Parquet/DuckDB | `core/` |

Требования (ТЗ): `docs/spec/00-index.md`. Решения: `docs/adr/`. Ход работ: `PROGRESS.md`.

## Скачать
Сборки `.exe` — в выпусках (Releases) на вехах (workflow `release`, ручной запуск); workflow `windows`
собирает и проверяет `.exe` на каждом PR, но не выгружает его (экономия места Actions). Сборка без подписи кода: Windows SmartScreen покажет
«Неизвестный издатель» → «Подробнее» → «Выполнить в любом случае».

## Сборка и тесты
Нужны: [uv](https://docs.astral.sh/uv/), Node.js 22.

```bash
cd core && uv sync && uv run pytest -q
```

```bash
npm ci && npm test && npm run typecheck && npm run build
```

```bash
cd app && npx electron .
```

E2E (Linux, программный WebGL):

```bash
cd app && xvfb-run -a npx playwright test
```

Замеры:

```bash
uv run --project core python -m bench.run --profile ci --check
```

Сборка `bf-core.exe` и установщика выполняется в CI на `windows-latest` (`.github/workflows/windows.yml`).

## Лицензия
Собственность Mikhail Bandurkin. Зависимости — только MIT, BSD, Apache-2.0, MPL-2.0, ISC, PSF.
