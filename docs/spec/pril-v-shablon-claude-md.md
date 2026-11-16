## Приложение В. Шаблон CLAUDE.md

Создаётся на Э0; не больше 200 строк; обновляется по ходу работ.

    # BedrockForge 2 — правила для Claude
     
    ## Проект
    Desktop-ГГИС для Windows 11: Electron (app/) + React/TS/three.js (ui/) + Python 3.12
    ядро bf-core (core/) + SQLite/Parquet/DuckDB. Офлайн. Интерфейс только на русском.
    ТЗ: docs/spec/00-index.md (этап → файлы). Решения: docs/adr/. Состояние: PROGRESS.md.
     
    ## Команды
    - Ядро: cd core && uv sync && uv run pytest -q -x --tb=short
    - Ядро, БД на PostgreSQL: BF_TEST_PG=postgresql://... uv run pytest -q -m db
    - Интерфейс: cd ui && npm ci && npm test -- --reporter=dot && npx tsc --noEmit -p .
    - Сборка интерфейса: cd ui && npm run build
    - Замеры: cd core && uv run python -m bench.run --profile ci --check
    - Приложение (dev): npm --prefix app run dev
    - Сборка .exe — только в CI windows.yml
     
    ## Структура
    app/ оболочка · ui/src/{shell,viz,charts,edit,modules/<модуль>} · core/bf_core/<модуль>
    · core/tests · bench/ · tools/ (bf-license, gen) · docs/{spec,adr,USER_GUIDE.md,CHECKLIST.md}
     
    ## Модели (основная сессия — Opus / high; не менять внутри вехи)
    | Субагент | model / effort | Для чего |
    |---|---|---|
    | architect | fable / xhigh | Проект этапа, ADR |
    | kernel-engineer | fable / xhigh (лимит Fable → opus / xhigh) | Вычислительные ядра |
    | reviewer | opus / high | Ревью этапа |
    | test-runner | haiku / low | Тесты, сборки, логи |
    | explorer | haiku / low | Поиск, чтение больших файлов, справочник BF |
    | docs-writer | sonnet / medium | Руководство, CHANGELOG, CHECKLIST |
    Не больше 2 субагентов одновременно. Режим расхода — в PROGRESS.md (таблица 13.4 ТЗ).
     
    ## Чекпойнт (после каждого подшага 30–45 мин)
    тесты → коммит «Э<N>: …» / «wip(Э<N>): …» → PROGRESS.md «Сейчас» и «Следующее
    действие» → push. Незапушенное теряется при пересоздании контейнера.
     
    ## Экономия токенов
    - Читать только нужные файлы spec; ТЗ целиком не перечитывать.
    - Поиск, логи, вывод тестов — через субагентов; короткие отчёты.
    - Целевые тесты во время работы, полный набор — перед слиянием.
    - Переносить код BF (docs/spec/…-приложение-е.md), не писать заново.
    - Не опрашивать CI — ждать событий PR.
     
    ## Обязательные правила (полный список — раздел 17 ТЗ)
    - Тексты для пользователя — по-русски; идентификаторы и комментарии — по-английски.
    - Координаты float64; float32 только для показа от целочисленного начала.
    - Пропуски не превращать в ноль; ошибки — по-русски с подсказкой.
    - Имена файлов не различаются только регистром; файл ≤ ~600 строк.
    - Команда = запись в реестре (лента, Ctrl+K, клавиши, ассистент).
    - Только собственные окна; никаких window.confirm/prompt/alert/open.
    - Зависимости — только MIT/BSD/Apache-2.0/MPL-2.0/ISC; lock-файлы.
    - Готовность этапа — раздел 10.5 ТЗ.
     
    ## Compact instructions
    При сжатии сохрани: веху, этап, ветку, номер PR, «Следующее действие» из PROGRESS.md,
    нерешённые ошибки тестов и CI, принятые в этой сессии решения без ADR. Остальное —
    в PROGRESS.md и docs/adr/.
