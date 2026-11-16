# PROGRESS — BedrockForge 2
Обновлено: 2026-11-16 20:05 UTC · Сессия: https://claude.ai/code/session_01PV4nyXwrRaJmTepomG4WpS · Ветка: claude/vigilant-meitner-vzjsd3 · Режим: норма

## Сейчас
Веха: В1 «Каркас» · Этап: Э2 «Проекты» · PR: — (откроется черновой после 2.1)
Подшаг: 2.1 слой БД (1 из 9)

## Следующее действие
2.1: db/dialect.py, модели проекта и экземпляра, Alembic (две цепочки: project, instance), тест цикла миграций на SQLite и PG.

## План этапа Э2 (spec: 07-03, 07-04, 07-05, 07-06, 06 6.5/6.7; ADR 0002, 0003)
- [ ] 2.1 Слой БД: dialect.py (типы, PRAGMA, BEGIN IMMEDIATE, claim_job), модели (project_meta, objects, object_refs, blobs, blob_owners, jobs, job_log, edit_log, settings; instance: jobs, job_log, projects), Alembic, проверка «диалект только в dialect.py» — pytest SQLite + PG
- [ ] 2.2 Проекты: папка `<Имя>.bfproj` (манифест, project.sqlite, blobs, cache, backups, logs), создание/открытие/переименование/описание/удаление/поиск/сводка, project.lock и «только чтение», quick_check, автокопии (5), сетевые пути; роутер projects — pytest + httpx
- [ ] 2.3 Хранилище blobs (запись через .tmp + SHA-256, владельцы, сборщик раз в час), отдача массива с ETag/Range — pytest
- [ ] 2.4 Задания: планировщик в ядре, исполнители spawn (heavy N + quick 1), tick/отмена, пульс 5 с, «почему ждёт», 3 попытки, память, SSE; роутер jobs — pytest с настоящими процессами
- [ ] 2.5 `.bfggis`: экспорт/импорт заданиями, AES-256, контрольные суммы, пересчёт id, версии, файлы BF — pytest
- [ ] 2.6 Демо-проект (данные в дистрибутиве) — pytest
- [ ] 2.7 Интерфейс: выбор проекта в заголовке, окно «Проекты», вкладка «Задания», команды project.* и jobs.*, последний проект при запуске — vitest + E2E
- [ ] 2.8 E2E «снятие ядра после правки не теряет правку», экспорт → импорт; selftest миграций в .exe
- [ ] 2.9 Документация, ревью, PR, отчёт вехи В1

## Блокеры и вопросы заказчику
- Нет ветки `main` в репозитории (по умолчанию — `claude/pensive-archimedes-75t56a`). Создаю `main`
  от коммита с ТЗ (63bb4aa). Заказчику: сделать `main` веткой по умолчанию и защитить (ci, windows).

## Допущения (вынести в отчёт вехи)
- Ревью Э1, отложено: (11) удаление всех трёх отметок пробного периода начинает его заново — защита на Э24;
  (13) на Windows SO_EXCLUSIVEADDRUSE + TIME_WAIT → перезапуск ядра на новом порту с перезагрузкой окна — проверяется core.spec в windows.yml;
  (14) os._exit при --watch-stdin: на Э2 соединения SQLite/DuckDB закрывать в lifespan до выхода;
  (19) подтверждение команд Claude — модальным окном, в чате — на Э23.
- Справочник BF — ветка `claude/laughing-bohr-gpy965` этого репозитория (код base-functional);
  файлы real_data взяты из `bandurkin-ai/BedrockForge@real_data` (только чтение).
- `autoContinueAtUsageLimit` в проектном `.claude/settings.json` не действует (ключ уровня
  пользователя), в облаке не подтверждён → страховка — ежечасная задача.
- Модуль `print` назван `printing` (не затеняет встроенную функцию Python).
- npm workspaces (ui, app) с одним `package-lock.json` в корне.
- Кеш numba в .exe: свой локатор (bf_core/numba_cache.py), иначе numba пишет в общий `~/.cache/numba`.

## Вехи
| Веха | Этапы | Состояние | Тег |
|---|---|---|---|
| В1 | Э0–Э2 | В работе (Э0 — PR #1, Э1 — PR #2 слиты) | — |

## Расход
| Этап | Окон 5 ч (оценка / факт) | Сессия | Примечание |
|---|---|---|---|
| Э0 | 1–2 / ≈0,5 | vigilant-meitner | Спайки прошли с первого раза |
| Э1 | 2–3 / ≈2 | vigilant-meitner | Лимит в 18:40 UTC; 4 итерации windows-CI (регистр имён, ctypes, makensis, роутеры в .exe) |
| Э2 | 3–4 / — | vigilant-meitner | |

## Страховка от лимита
Задача: trig_01FzykpMptKv3MV6pcJJV8m7 · каждый час (:16) · создана 2026-11-16 17:16 UTC
