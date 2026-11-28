# План этапа: правила интерфейса в оболочке (`ui/src/shell`)

Основание: аудит `ui/src/shell` по `docs/UI_RULES.md` (2026-11-28, 6 агентов Haiku, 61 находка; выборочная проверка по коду — 2 находки ложные, 2 сомнительные). Код аудитом не менялся.
Статус: **проект плана на согласование**. Номер этапа и место в PROGRESS.md назначает заказчик (после В5 остановка до замечаний по v0.5.0, Э22 и В6 — только по команде).

## 1. Цель и границы

| Что | Решение |
|---|---|
| Цель | Закрыть нарушения UI Rules в оболочке без изменения вида (бренд ADR 0006) и без новых функций |
| Только `ui/src/shell` | `app/`, `core/`, `ui/src/{viz,charts,edit,modules}` не затрагиваются (их аудит — отдельно) |
| Новые зависимости | Не добавляются. Виртуализация — `content-visibility: auto`; тесты — vitest `renderToStaticMarkup` + E2E Playwright (Testing Library и jsdom в репозитории нет) |
| Новые функции | Не делаются. Пункты, которые меняют поведение (клавиатурное перемещение окон, изменение размера разделителей), вынесены в раздел 5 на решение заказчика |
| Файл ≤ ~600 строк, русские тексты, ошибки `{code, message, hint}` | Сохраняются |

## 2. Сквозные решения (в ADR не выносить, записать в PR)

| № | Решение | Обоснование |
|---|---|---|
| Р1 | Общий хук `useFocusTrap` (ловушка Tab, фокус внутрь, возврат на вызвавший элемент) в новом `focus.ts`; подключается в `DialogHost`, `CoreDown`, `Palette` | Находка F6 в трёх местах, одна реализация |
| Р2 | Общий хук `useRovingKeys` (стрелки, Home/End) в новом `roving.ts` для `ContextMenu`, вкладок `SettingsWindow` | Находка A4 в двух местах |
| Р3 | Общий класс кнопки в новом `buttons.module.css` (`.btn` с `:hover`, `:active`, `:focus-visible`), подключение через `composes` | C6 в 12 местах; 8 копий одинакового `.btn` |
| Р4 | Селекторы `useStore` возвращают примитивы или устойчивые ссылки. Объект, созданный в селекторе, даёт бесконечный цикл `useSyncExternalStore` | R4: `Ribbon`, `StatusBar`, `Palette` |
| Р5 | Виртуализация `ObjectRows` и `CommandLog` через `content-visibility: auto` + `contain-intrinsic-size`; полноценная виртуализация только если замер покажет необходимость | Правило проекта: зависимость только через ADR |
| Р6 | `PropsParts` (черновик + откат, `useEffect` → `setDraft`) не менять: паттерн осознанный | Находки R3 отклонены |
| Р7 | `Palette.tsx:80` (`div role="option"`) не менять: штатный combobox | Находка A3 ложная |

## 3. План по файлам

### 3.1 Новые файлы

| Файл | Содержимое | Подшаг |
|---|---|---|
| `focus.ts` | `useFocusTrap(ref, {restore})`, чистая функция `nextFocus(list, active, shift)` | 1 |
| `roving.ts` | `useRovingKeys` (чистая функция `nextIndex(count, current, key)`) | 2 |
| `buttons.module.css` | Общая кнопка с состояниями | 3 |
| `test/focus.test.ts`, `test/roving.test.ts` | Тесты чистых функций | 1, 2 |
| `app/e2e/shellFocus.spec.ts` | E2E: ловушка Tab, Esc, возврат фокуса, стрелки в меню и вкладках | 6 |

### 3.2 Существующие файлы

| Файл | Находки | Правка | Подшаг |
|---|---|---|---|
| `DialogHost.tsx` | F6 | `useFocusTrap`, возврат фокуса | 1 |
| `WindowLayer.tsx` | I1 | Перемещение с клавиатуры — раздел 5 (В1); без правок в этапе | — |
| `CoreDown.tsx` | F6, A6, T3 | Ловушка; `role="status"` + `aria-live="polite"`; неразрывный пробел перед «с» | 1, 5 |
| `Palette.tsx` | F6, R4, R11 | Возврат фокуса; узкие селекторы; сброс `sel` в обработчике ввода | 1, 4 |
| `Palette.module.css` | F2, I3 | Видимая замена `outline: none`; `overscroll-behavior: contain` | 3 |
| `ContextMenu.tsx` | A4 | `useRovingKeys` | 2 |
| `SettingsWindow.tsx` | A4, FM1, T8 | `useRovingKeys` во вкладках; `name` у поиска; текст «сочетание занято» с шагом | 2, 5 |
| `StatusBar.tsx` | A3, R4 | `button` вместо `span onClick`; узкие селекторы | 2, 4 |
| `Ribbon.tsx` | R4 | Узкие селекторы | 4 |
| `TreeRow.tsx` | A4, R14 | Вынос статичного `Icon`; клавиша контекстного меню — раздел 5 (В6) | 4 |
| `DataTree.tsx` | I1, A4 | Раздел 5; проверить `tabIndex={-1}` у «+» | 2 |
| `ObjectRows.tsx` | D5, A4 | `content-visibility`; проверить `tabIndex={-1}` у кнопок строки | 2, 4 |
| `CommandLog.tsx` | D5 | `content-visibility` для 200 строк | 4 |
| `OpLogWindow.tsx` | A4 | `tabIndex={0}` у прокручиваемого `pre` | 2 |
| `LicenseWindow.tsx` | A6, I11, T8 | `aria-live`; `translate="no"` у кода ПК; причина и шаг в тексте | 5 |
| `ExternalTools.tsx` | FM1, FM8 | `name`; placeholder с примером и «…» | 5 |
| `GeneralSettings.tsx` | FM1 | `name` у поля пути и select | 5 |
| `tree.ts` | J1 | Индекс папок `Map` вместо `find` в цикле | 4 |
| `workspace.ts` | J1 | `Set` вместо `includes` в `filter` | 4 |
| `settings.ts` | D8 | Проверка `version` при чтении, функция миграции (пока одна версия), тест | 4 |
| `api.ts` | T8 | Подставлять `hint` по умолчанию вместо пустого | 5 |
| `coreClient.ts` | T8 | Ошибка в формате `{code, message, hint}` | 5 |
| `opLog.ts` | I9 | Время через `Intl.DateTimeFormat("ru-RU")` | 5 |
| `CommandLog.module.css` | A11 | Статус «ошибка» ещё и значком/текстом | 5 |
| `Dialog.module.css`, `Window.module.css`, `DataTree.module.css` | I3, C6 | `overscroll-behavior: contain`; общая кнопка | 3 |
| `CoreDown.module.css`, `SettingsWindow.module.css`, `LicenseWindow.module.css`, `OpLogWindow.module.css` | C6 | Общая кнопка | 3 |
| `Shell.module.css`, `Header.module.css`, `Ribbon.module.css`, `StatusBar.module.css` | C6 | `:hover` у `.dockTab`, `.dockClose`, `.project`, `.search`, `.collapse`, `.link` | 3 |
| `TreeRow.module.css` | AN1 | `@media (prefers-reduced-motion: reduce)` | 3 |
| `OpLogWindow.module.css` | C1 | `overflow-wrap: anywhere` | 3 |

Не затрагиваются (чисто): `Shell.tsx`, `Docks.tsx`, `Header.tsx`, `About.tsx`, `Icon.tsx`, `hotkeys.ts`, `keys.ts`, `shortcuts.ts`, `commands.ts`, `registry.ts`, `store.ts`, `app.ts` и др.

## 4. Подшаги (30–45 мин, чекпойнт после каждого)

| № | Подшаг | Файлы | Модель | Тесты |
|---|---|---|---|---|
| 0 | Решения заказчика по разделу 5, план в PROGRESS.md | — | Sonnet | — |
| 1 | `focus.ts`, ловушка фокуса, возврат фокуса | `focus.ts`, `DialogHost`, `CoreDown`, `Palette` | Sonnet (Opus при неудаче со второй попытки) | `focus.test.ts` |
| 2 | `roving.ts`, клавиатурная доступность | `roving.ts`, `ContextMenu`, `SettingsWindow`, `StatusBar`, `ObjectRows`, `OpLogWindow` | Sonnet | `roving.test.ts`, правка `shell.test.ts` |
| 3 | Стили: общая кнопка, `hover`, `overscroll`, `reduced-motion`, `overflow-wrap`, фокус палитры | все `*.module.css` из таблицы | Haiku по готовому списку | `npm run typecheck`, сборка |
| 4 | Перерисовки и данные | `Ribbon`, `StatusBar`, `Palette`, `TreeRow`, `ObjectRows`, `CommandLog`, `tree.ts`, `workspace.ts`, `settings.ts` | Sonnet | Тест `settings` (миграция), замеры из `bench` без ухудшения |
| 5 | Тексты, формы, локаль | `CoreDown`, `SettingsWindow`, `LicenseWindow`, `ExternalTools`, `GeneralSettings`, `api.ts`, `coreClient.ts`, `opLog.ts`, `CommandLog.module.css` | Sonnet (механические правки — Haiku) | Тесты форматов ошибок и `Intl` |
| 6 | E2E: фокус и клавиатура оболочки | `app/e2e/shellFocus.spec.ts` | Sonnet | `cd app && xvfb-run -a npx playwright test shellFocus` |
| 7 | Ревью (`reviewer`), повторный аудит агентами Haiku по `UI_RULES.md`, документация | `docs/USER_GUIDE.md` (клавиши), `CHECKLIST.md` | Sonnet / Haiku | Повторный аудит: 0 подтверждённых находок из списка |
| 8 | PROGRESS этапа → `main` (последний подшаг этапа) | `PROGRESS.md` | Sonnet | — |

Порядок: 1 → 2 → 3 (параллельно с 4, файлы не пересекаются, кроме `Palette`, `StatusBar`: сначала 1–2) → 5 → 6 → 7 → 8.
Агенты, правящие код, — с `isolation: "worktree"`, до 5 одновременно; слияние `git merge --no-ff` оркестратором. Подшаги 1–2 и 4–5 пересекаются по файлам `Palette.tsx`, `StatusBar.tsx`, `SettingsWindow.tsx`, поэтому идут последовательно.

## 5. Вынести на решение заказчика (не делать без указания)

| № | Вопрос | Причина | Рекомендация |
|---|---|---|---|
| В1 | Перемещение плавающих окон с клавиатуры (`WindowLayer.tsx:65`, I1) | Новое поведение и, по правилам проекта, команда в реестре | Отложить, не входит в этап |
| В2 | Изменение размера панелей разделителем с клавиатуры (`Splitter.tsx`, I1) | Новое поведение: клавиши, шаг, команда в реестре | Принять: стрелки с шагом 16 px, `role="separator"` с `tabIndex=0`, `aria-valuenow` |
| В3 | Перетаскивание разделов дерева (`DataTree.tsx:131`, I1) с клавиатурой | Нужна альтернатива («Переместить выше/ниже» в контекстном меню) | Принять как отдельную команду |
| В4 | `tabIndex={-1}` у кнопок «+» и кнопок строки (`DataTree.tsx:154`, `ObjectRows.tsx:82`) | Возможно намеренно (доступ через клавиши дерева) | Сначала проверка `treeKey`; если действия недоступны с клавиатуры — править |
| В6 | Клавиша контекстного меню строки дерева (`TreeRow.tsx:54`, A4) | Новая привязка клавиш: Menu / Shift+F10 в `treeKey`, по правилам проекта — запись в реестре | Принять вместе с В2 |
| В5 | Полноценная виртуализация списков при больших объёмах (50 000 скважин) | Новая зависимость (ADR 0001) | Сначала `content-visibility`, замер; ADR только при необходимости |

## 6. Риски

| Риск | Мера |
|---|---|
| Ловушка фокуса ломает ввод в 3D-холст или сочетания клавиш реестра | Ловушка только внутри модальных окон; `Esc` и клавиши реестра работают как раньше; E2E |
| Узкие селекторы дают бесконечный цикл `useSyncExternalStore` | Селекторы возвращают примитивы или `store`-ссылки; тест на отсутствие цикла |
| Расхождение вида из-за общей кнопки | Только `composes` и состояния; значения цветов — токены ADR 0006; снимки E2E не должны измениться |
| Расход Actions | Один push пачкой в PR, `windows` один раз на готовом PR; перед push — целевые тесты `ui`, `npm run typecheck`, `npm run build` |
| Регресс `bench` и `shell.test.ts` | Подшаг 4: замер до и после, `bench.run --profile ci --check` |

## 7. Готовность этапа (10.5 ТЗ + этот этап)

- Все находки раздела 3 закрыты или отклонены с записью причины (Р6, Р7, В1–В5).
- Повторный аудит агентами Haiku: 0 подтверждённых находок по закрытым ID.
- `npm test`, `npm run typecheck`, `npm run build`, E2E `shellFocus`, `bench --check` зелёные; CI `ci` и `windows` зелёные.
- `PROGRESS.md` обновлён (подшаги `[x]`, «Сейчас», «Следующее действие», «Допущения» № …).

## 8. Отчёт на согласование (Приложение Ж)

«Выполнено» · «Модели» · «Проверка» (повторный аудит вместо Fable: Fable — один раз в конце вехи) · «Не решено и вынесено на решение» (В1–В6) · «Тесты».
