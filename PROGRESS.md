# PROGRESS — BedrockForge 2
Обновлено: 2026-11-16 17:20 UTC · Сессия: https://claude.ai/code/session_01PV4nyXwrRaJmTepomG4WpS · Ветка: claude/vigilant-meitner-vzjsd3 · Режим: норма

## Сейчас
Веха: В1 «Каркас» · Этап: Э0 «Подготовка» · PR: — (будет открыт после ADR)
Подшаг: 0.5 CI и спайки на windows-latest (5 из 8)

## Следующее действие
Дождаться ADR 0001–0005 от architect → сверить версии в core/pyproject.toml и package.json,
вписать итоги спайков в ADR → создать ветку main от 63bb4aa (её нет в репозитории) →
открыть PR «Э0. Подготовка» → дождаться ci + windows.

## План этапа Э0
- [x] 0.1 ТЗ → docs/spec/ (77 файлов) + 00-index.md; tools/spec/split_tz.py
- [x] 0.2 CLAUDE.md, PROGRESS.md, .claude/{agents,hooks,settings.json}
- [ ] 0.3 ADR 0001–0005 (architect, fable/xhigh) — в работе
- [x] 0.4 Спайки локально: а (самопроверка зависимостей), б (1 млн треуг., SwiftShader), в (токен/401), г (кириллица, кеш numba в .exe)
- [ ] 0.5 Спайки на windows-latest (windows.yml) — проверка в PR
- [x] 0.6 Каркас: core/, ui/, app/, bench/, tools/gen/, CI ci.yml, windows.yml, release.yml
- [x] 0.7 Страховка от лимита (автопродолжение в облаке не подтверждено)
- [ ] 0.8 Скрипт облачного окружения; README; PR → зелёный CI → слияние

## Перенесено в Э1 (ревью Э0)
- Перезапуск ядра на прежнем порту (иначе CSP окна теряет ядро), при занятом — новый порт + перезагрузка окна; e2e «убить ядро → fetch 200».
- Job Object `KILL_ON_JOB_CLOSE` в ядре (ctypes) + проверка в windows.yml «после выхода нет bf-core.exe».
- Опрос /health (3 пропуска → перезапуск), окно «Ядро не запускается» с журналом, сообщение «Вычислительное ядро перезапущено» в окне.
- Заставка: кнопки «Закрыть» и «Скопировать журнал» при ошибке; журнал ядра — потоком, не appendFileSync.
- Свой portable (tools/portable.nsi, ADR 0005 п. 3); fuses и два профиля сборки (e2e/release).
- release.yml: тесты и E2E перед выпуском. (Э2: исполнители только `spawn`.)

## Блокеры и вопросы заказчику
- Нет ветки `main` в репозитории (по умолчанию — `claude/pensive-archimedes-75t56a`). Создаю `main`
  от коммита с ТЗ (63bb4aa). Заказчику: сделать `main` веткой по умолчанию и защитить (ci, windows).

## Допущения (вынести в отчёт вехи)
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
| В1 | Э0–Э2 | В работе | — |

## Расход
| Этап | Окон 5 ч (оценка / факт) | Сессия | Примечание |
|---|---|---|---|
| Э0 | 1–2 / — | vigilant-meitner | — |

## Страховка от лимита
Задача: trig_01FzykpMptKv3MV6pcJJV8m7 · каждый час (:16) · создана 2026-11-16 17:16 UTC
