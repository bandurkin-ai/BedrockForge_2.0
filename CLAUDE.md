# BedrockForge 2 — правила для Claude

## Проект
Desktop-ГГИС для Windows 11: Electron (app/) + React/TS/three.js (ui/) + Python 3.12
ядро bf-core (core/) + SQLite/Parquet/DuckDB. Офлайн. Интерфейс только на русском.
ТЗ: docs/spec/00-index.md (этап → файлы). Решения: docs/adr/. Состояние: PROGRESS.md.
Справочник BF (только чтение, через explorer): ветка `claude/laughing-bohr-gpy965`
(`git worktree add /home/user/bf-ref origin/claude/laughing-bohr-gpy965 --detach`).

## Команды
- Ядро: `cd core && uv sync && uv run pytest -q -x --tb=short`
- Ядро, линт: `uv run ruff check . && uv run mypy bf_core`
- Ядро, БД на PostgreSQL: `BF_TEST_PG=postgresql+pg8000://… uv run pytest -q -m db` (драйвер pg8000 — BSD, ADR 0003)
- Ядро, самопроверка зависимостей: `uv run python -m bf_core --selftest`
- Интерфейс и оболочка (npm workspaces ui, app): `npm ci && npm test -- --reporter=dot && npm run typecheck`
- Сборка интерфейса и оболочки: `npm run build`
- E2E (Linux, программный WebGL): `cd app && xvfb-run -a npx playwright test`
- Замеры: `uv run --project core python -m bench.run --profile ci --check`
- Сборка bf-core: `cd core && uv sync --group build && uv run pyinstaller bf-core.spec`
- Сборка .exe — только в CI windows.yml (сборка + дымовой E2E, артефакт .exe не выгружается; выпуск — release.yml)
- ТЗ → spec: `pandoc docs/TZ/BF2-TZ.docx -t gfm --wrap=none -o /tmp/tz.md && python3 tools/spec/split_tz.py /tmp/tz.md docs/spec`
- В облаке uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS` (прокси с собственным CA); uv 0.13.6 (`pip install --user uv==0.13.6`)
- Версии: только по ADR 0001 (точные `==`, `exclude-newer`, `npm install --before=<дата>`); новая зависимость — через ADR

## Структура
app/ оболочка (src/main, src/preload, static/ заставка, e2e/) · ui/src/{shell,viz,charts,edit,modules/<модуль>}
· core/bf_core/<модуль> (api — FastAPI; роутер `<модуль>/router.py` регистрируется в `api/modules.py`)
· core/tests · bench/ (run.py, budgets.json, scenarios/) · tools/ (gen/, ci/, spec/, bf-license — Э24)
· docs/{spec,adr,USER_GUIDE.md,CHECKLIST.md} · tests/data/real/ (файлы real_data, только чтение)
Модуль печати называется `printing` (не `print` — не затеняет встроенную функцию).

## Модели (основная сессия — Opus / high; не менять внутри вехи)
| Субагент | model / effort | Для чего |
|---|---|---|
| architect | fable / xhigh | Проект этапа, ADR |
| kernel-engineer | fable / xhigh (лимит Fable → opus / xhigh) | Вычислительные ядра |
| reviewer | opus / high (безопасность Э24–Э25 — fable / high) | Ревью этапа |
| test-runner | haiku / low | Тесты, сборки, логи |
| explorer | haiku / low | Поиск, чтение больших файлов, справочник BF |
| docs-writer | sonnet / medium | Руководство, CHANGELOG, CHECKLIST |
Не больше 4 субагентов одновременно (указание заказчика 2026-11-18, подписка Ultra). Режим расхода — в PROGRESS.md (таблица 13.4 ТЗ).

## Чекпойнт (после каждого подшага 30–45 мин)
тесты → коммит «Э<N>: …» / «wip(Э<N>): …» → PROGRESS.md «Сейчас» и «Следующее
действие» → push. Незапушенное теряется при пересоздании контейнера.

## Git
- Ветка сессии `claude/…` → PR этапа в `main` (заголовок «Э<N>. <Название>», описание — Приложение Ж).
- Сливать после зелёного CI (`ci` и `windows`), merge commit; затем `git fetch origin main && git merge origin/main`.
- CI ждать событиями PR (подписка), не опрашивать.
- Экономия минут Actions: `ci` — только PR и push в main; `windows` — только PR не в черновике
  (или вручную, workflow_dispatch). PR держать черновиком, пока этап не готов; правки только docs/*.md CI не запускают.

## Экономия токенов
- Читать только нужные файлы spec; ТЗ целиком не перечитывать.
- Поиск, логи, вывод тестов — через субагентов; короткие отчёты.
- Целевые тесты во время работы, полный набор — перед слиянием.
- Переносить код BF (docs/spec/pril-e-…md), не писать заново.
- Решение принято и записано в ADR — не обсуждать заново.

## Обязательные правила (полный список — раздел 17 ТЗ)
- Фирменный стиль во всём интерфейсе и **всех выпусках**: docs/brand/README.md (палитра, Montserrat/Inter, знак, иконка), ADR 0006.
- Тексты для пользователя — по-русски; идентификаторы и комментарии — по-английски.
- Координаты float64; float32 только для показа от целочисленного начала (ui/src/viz/origin.ts).
- Пропуски не превращать в ноль; ошибки — `{code, message, hint}` по-русски с подсказкой.
- Имена файлов не различаются только регистром; файл ≤ ~600 строк.
- Команда = запись в реестре (лента, Ctrl+K, клавиши, ассистент).
- Только собственные окна; никаких window.confirm/prompt/alert/open.
- Зависимости — только MIT/BSD/Apache-2.0/MPL-2.0/ISC/PSF (шрифты — OFL-1.1, ADR 0006); lock-файлы (core/uv.lock, package-lock.json);
  проверка `tools/ci/check_licenses_py.py`, `tools/ci/check_licenses_npm.mjs`.
- Ядро слушает только 127.0.0.1, токен `X-BF-Token`, проверка Host/Origin (core/bf_core/api/security.py).
- numba: `cache=True`; кеш — `NUMBA_CACHE_DIR` (bf_core/paths.py; в .exe — bf_core/numba_cache.py).
- multiprocessing только `spawn`; обработчики заданий регистрируются явным импортом.
- Готовность этапа — раздел 10.5 ТЗ.

## Compact instructions
При сжатии сохрани: веху, этап, ветку, номер PR, «Следующее действие» из PROGRESS.md,
нерешённые ошибки тестов и CI, принятые в этой сессии решения без ADR. Остальное —
в PROGRESS.md и docs/adr/.
