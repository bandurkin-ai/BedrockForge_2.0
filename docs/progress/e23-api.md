# Э23 — контракт ассистента Claude (подшаг 23.0, контракт первым)

**Состояние: контракт принят и сверен с кодом (подшаг 23.22: `routes.py`, `routes_dialogs.py`, `schemas*.py`, `errors.py`, `tools.py`, `loop*.py`); образцы ответов — `core/tests/data/assistant/contract_answers.json` — ответы НАСТОЯЩЕГО ядра на моке (23.11:
`core/tests/assistant/regen.py`; перезапись — `BF_REGEN_ASSISTANT=1 uv run pytest -q tests/test_assistant_contract.py`).** Источник истины — ADR 0029 (решения 1–40, вопросы № 125–139), ТЗ 7.32,
6.9–6.10, 7.2, 7.0.3–7.0.5, 5.6. Изменение формы — только через оркестратора. Пути: `A/` = `core/bf_core/assistant/`, `U/` = `ui/src/modules/assistant/`.
Префикс маршрутов — `/api/v1/assistant` (роутер `A/router.py` = `routes.py` — состояние, подключение, вход, расход; `routes_dialogs.py` — диалоги и ходы; сервис — `A/service.py`, `state.services["assistant"]`; регистрация `"assistant"` в `api/modules.py` — оркестратор).

## 1. Общие правила

- `X-BF-Token`, Host/Origin — как у всего ядра (S1–S2). Время — ISO 8601 UTC (`"2026-11-26T10:00:00Z"`); деньги — USD, float, 6 знаков;
  токены — целые. Пусто — `null`, **никогда не 0** (стоимость неизвестной модели — `null`).
- Ошибка — `{code, message, hint, …}` по-русски с подсказкой (§14). Нет проекта там, где он нужен, — 409 `no_project`.
- Тела читаются вручную: пустое тело — `{}`, битый JSON — 422 `bad_param {param: "body"}` (текст ошибки тело не повторяет). Общий предел ядра для `/api/v1/assistant/`
  (`api/body_limit.py`, `LIMITED_PREFIXES`): тело ≤ 8 МБ (иначе 413 `request_too_large`), вложенность JSON ≤ 64 (иначе 422 `bad_request`). Поверх него — свои пределы
  тела хода: `tools` ≤ 400 штук и ≤ 512 КБ (§5.1), `text` ≤ 20 000 знаков, `context` ≤ 4 КБ, `results` ≤ 64.
- **Только оболочка** (`shell`): запрос **без** заголовка `Origin` (окно всегда шлёт `Origin: app://bedrockforge`) — иначе 403 `shell_only`.
  Тела и ответы этих маршрутов не пишутся в журналы.
- Секрет (ключ, токены) **никогда** не возвращается маршрутами окна и не попадает в события, диалоги, журналы, `prefs.json`, проект.
- Потоковые ответы — `Content-Type: text/event-stream`, `Cache-Control: no-store`; события — `id: <n>` (номер в отрезке, с 1), `event: <имя>`,
  `data: <JSON>`; комментарий `: keepalive` после 15 с тишины. Чтение — `fetch` + свой разбор `U/stream.ts` (`parseSse` из `modules/jobs/state.ts` не годится: теряет `id:`);
  буфер отрезка — ≤ 2000 событий (старые уходят), догон — `GET …/stream?after=<n>`.
- События общей шины `/events`: `assistant.connection {state, kind, mask, model, online, seal_needed}` (без секрета) — после подключения, выхода,
  обновления токена, смены модели/глубины, изменения связи; `assistant.dialogs {project_uid, id, deleted?}` — после записи/переименования/удаления.

## 2. Состояние — `GET /status`

```json
{"state": "none" | "connected" | "login",
 "kind": null | "api_key" | "env" | "account",
 "mask": null | "sk-ant-…a1B2",
 "organization": null | {"id": "org_…", "name": null | "ООО Рудник"},
 "workspace": null | {"id": "wrkspc_…", "name": null | "Геология"},
 "online": null | true | false,             // итог последнего вызова API: null — не было
 "checked_at": null | "…Z",
 "model": "claude-opus-5-5", "model_source": "prefs" | "env" | "default",
 "effort": "medium", "effort_source": "prefs" | "env" | "default",
 "ant_available": true, "secure_storage": true,   // secure_storage — от оболочки (§3), ядро только передаёт
 "seal_needed": false,
 "subscription_note": "Подписка Claude (Pro, Max, Team) панелью не используется: нужен ключ API или вход аккаунтом Anthropic Console.",
 "models": [{"id": "claude-opus-5-5", "title": "Opus 5.5", "default": true, "note": "…",
             "price": {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.0},
             "long": null | {"threshold": 100000, "input": 0.50, "output": 2.50, "cache_read": 0.05, "cache_write": 0.625}}],
 "efforts": [{"id": "low", "title": "Быстро"}, {"id": "medium", "title": "Обычно", "default": true}, {"id": "high", "title": "Тщательно"},
             {"id": "xhigh", "title": "Очень тщательно"}, {"id": "max", "title": "Максимум"}],
 "prices_version": "2026-11"}
```
Цены (ТЗ 7.32): Opus 5.5 4 / 20 / 0,20; Sonnet 5.5 2 / 10 / 0,10; Haiku 5.5 0,10 / 0,50 / 0,01 (`long` — № 128); Fable 5.2 8 / 40 / 0,20;
`cache_write` = 1,25 × `input`. Подпись строки состояния — из `state`/`kind`/`model`/`online` (§13).

`PUT /prefs {model?, effort?}` → `status`. Неизвестная модель — 422 `bad_param {param: "model"}`, глубина — `{param: "effort"}`.

## 3. Подключение

| Маршрут | Кто | Тело → ответ | Правила |
|---|---|---|---|
| `POST /connection/key` | окно | `{key}` → `status` | формат `^sk-ant-[A-Za-z0-9_-]{20,512}$` (пробелы по краям срезаются; иначе 422 `assistant_bad_key_format`); проверка `models.retrieve(model)` **до** сохранения: 401/403 → 422 `assistant_bad_key` (состояние прежнее); нет сети → 503 `assistant_offline` (ключ не принят); успех → секрет в памяти, `kind: "api_key"`, `seal_needed: true` |
| `POST /connection/test` | окно | `{}` → `{ok, latency_ms, model, model_available, effort_supported: [..] | null, organization_id | null, message}` | «Проверить связь»: `models.retrieve(model)`; нет подключения — 409 `assistant_not_connected`; отказ API — не исключение, а ответ `ok: false` + `message` (текст и подсказка по-русски); 404 модели — `ok: true, model_available: false`; итог пишет `online` (так панель выходит из «нет связи», см. §13) |
| `DELETE /connection` | окно | → `status` | «Выйти»: секрет из памяти — прочь; остаётся `ANTHROPIC_API_KEY` окружения, если задан (`kind: "env"`); окно затем зовёт `bf.claudeForget()` |
| `PUT /connection/secret` | **shell** | `{kind: "api_key" | "account", secret: {…}, secure_storage}` → `{ok, kind, mask}` | старт оболочки и перезапуск ядра; `api_key`: `{key}`; `account`: `{access_token, expires_at, refresh_token, client_id, organization_id, workspace_id, base_url?}`; без проверки связи (офлайн-старт) |
| `GET /connection/secret` | **shell** | → `{kind, secret, revision}` или 204 (нечего хранить) | главный процесс шифрует (`safeStorage`) и пишет `assistant/connection.bin`; затем `POST /connection/sealed {revision}` (**shell**) → `status` (`seal_needed: false`) |

Оболочка (`app/src/main/claude.ts`, preload `window.bf`): `claudeSeal(): Promise<{ok, message?}>`, `claudeForget(): Promise<void>`,
`claudeStorage(): {secure: boolean, backend: string}` (sync). `secure: false` (Linux `basic_text`, нет DPAPI) — `claudeSeal` отвечает
`{ok: false, message: "Хранилище ключей ОС недоступно: задайте ANTHROPIC_API_KEY"}`, кроме `BF_E2E=1`.
`claudeStorage().secure` = «ключ можно сохранить»: настоящее шифрование (Windows — DPAPI) либо тестовое хранилище `e2e_plain` при `BF_E2E=1`; Linux безопасен только при бэкенде не `basic_text`/`unknown`.
Файл — `<данные>/assistant/connection.bin` (`safeStorage.encryptString`); главный процесс опрашивает `GET /status` раз в 30 с (`seal_needed`), после `BF-READY` и каждого перезапуска ядра отдаёт секрет
`PUT /connection/secret`; `claudeForget` удаляет только файл (`DELETE /connection` вызывает окно); двойной `claudeSeal` безвреден. Секрет не пишется в журнал оболочки: только коды ответов.

### Вход аккаунтом (`ant`)
| Маршрут | Тело → ответ | Правила |
|---|---|---|
| `POST /login/start` | `{}` → `{login_id, url, expires_at}` | путь к `ant` — только `BF_ANT_PATH` (поиск в `PATH` нет: там может оказаться Apache Ant); нет `ant` — 501 `assistant_no_ant` «Вход аккаунтом недоступен в этой сборке» + «Введите API-ключ»; `ant` не выдал адрес за 30 с, не запустился или завершился — 502 `assistant_login_failed {detail}`; **один вход за раз**: пока вход запускается или проверяет код — 409 `assistant_login_busy`; вход, который ждёт только код, закрывается новым `start`. Профиль `ant` — временная папка `<local_app_dir>/tmp/ant-<случайное>` (0700), профиль `bedrockforge`, из окружения `ant` убраны `ANTHROPIC_API_KEY/AUTH_TOKEN/PROFILE/BASE_URL`; папка удаляется всегда |
| `POST /login/{login_id}/finish` | `{code}` → `status` | код ≤ 4096, не пустой, без `\r\n\0` (иначе 422 `bad_param {param: "code"}`; вход продолжается); **неверный код — 422 `assistant_login_code` и вход закрыт** (повтор с тем же `login_id` — 404 `assistant_login_gone`: нужен новый `start`); сеанс истёк (10 мин) или неизвестен — 404 `assistant_login_gone`; `ant` не завершил вход вовремя — 502 `assistant_login_failed`; успех — `kind: "account"`, `seal_needed: true` (аккаунт проверяется `models.retrieve` до сохранения: отказ — 422 `assistant_bad_key`) |
| `DELETE /login/{login_id}` | → 204 | отмена (процесс `ant` завершается, временная папка удаляется) |

## 4. Расход — `GET /usage`

```json
{"today": Period, "days30": Period, "all": Period, "prices_version": "2026-11", "since": "…Z" | null}
Period = {"requests": 12, "tokens": {"input": 1200, "output": 800, "cache_read": 90000, "cache_write": 30000},
          "cost_usd": 0.4123 | null, "unpriced_requests": 0,
          "by_model": [{"model": "claude-opus-5-5", "title": "Opus 5.5", "requests": 10, "tokens": {…}, "cost_usd": 0.40 | null,
                        "unpriced_requests": 0}]}
```
«Сегодня» — местные сутки машины; «30 дней» — последние 30 местных суток с сегодняшними. `cost_usd` периода — сумма известных; вызовы
неизвестной модели считает `unpriced_requests` (в периоде и в каждой строке `by_model[]`, 0 — таких нет); `cost_usd` строки модели без цены — `null`, не 0.
Файл `usage.jsonl` > 20 МБ: старые месяцы сворачиваются в `usage-summary.json`, свод «всё время» не меняется. Строка `usage.jsonl`: `{"at", "model", "input", "output", "cache_read", "cache_write",
"cost_usd", "long", "dialog_uid", "project_uid", "prices_version"}`.

## 5. Инструменты

### 5.1. Инструменты окна (из реестра) — тело хода `tools`
`ToolDefinition` = как `ui/src/shell/commands.ts` `commandTools()`:
`{name: "ui_<id с _>", description, input_schema: {type: "object", properties: {k: {type: T | [T, "null"], description?, enum?}}, required: [..], additionalProperties: false}}`,
`T ∈ string, number, integer, boolean`. Проверка ядра (иначе 422 `assistant_bad_tools {detail}`; `detail` называет инструмент и правило, но не его текст): имя `^ui_[a-z0-9_]{1,60}$` без повторов,
≤ 400 инструментов, описание ≤ 2000 знаков, имя свойства 1…64 знака, схема — объект с `additionalProperties: false`, всё ≤ **512 КБ** (JSON; было 256 КБ — 23.9б: реестр 330 команд,
270 инструментов без `uiOnly`, `commandTools` 341 779 байт ≈ 79,5 тыс. токенов). Палитра и окно хранят полный текст; **в API уходят сокращённые**
(`tools.build_api_tools`): описание инструмента ≤ 300 знаков, описание параметра ≤ 120 (обрезка по концу предложения или слова) ≈ 290 КБ; порядок — `data_*` по имени,
затем `ui_*` по имени (стабильный префикс для кеша промпта), `cache_control` — на последнем инструменте. Замер 23.18 (270 инструментов): запрос 312 КБ, из них инструменты 307 КБ.
Имя `ui_*`, которого не было в присланных `tools`, ядро само отвечает «Нет такой команды».

### 5.2. Инструменты чтения (ядро, `data_*`)
| Имя | Название (карточка) | Вход | Выход (JSON-текст ≤ 16 000 знаков) |
|---|---|---|---|
| `data_tree` | Дерево проекта | `{offset?: int}` | `{nodes: [{path, kind, id, name, count?, state?}], total, next_offset}` — папки, наборы, таблицы набора (интервальные — с числом интервалов), каркасы, ЦТМ, линии, растры, блочные модели, графики, отчёты; ≤ 300 узлов |
| `data_summary` | Сводка проекта / набора | `{set_id?: int}` | проект: `{name, counts {sets, holes, assays, interval_rows, wireframes, …}, crs, extent, sets [{id, name, holes}]}`; набор: `{id, name, holes, metres, tables [{name, kind, rows, fields}], components [..], extent, audit {e, w, i, stale} | null}` |
| `data_holes` | Список скважин | `{set_id?, search?, with_findings?: bool, offset?, limit? ≤ 200}` | `{set_id, total, rows: [{hole_id, x, y, z, depth, azimuth, dip, findings}], next_offset}` |
| `data_hole` | Карточка скважины | `{hole_id: string, set_id?}` | `{hole_id, collar {x, y, z}, depth, type, survey {n, first, last}, tables [{table, intervals, from, to}], findings [{rule, severity, message}] ≤ 20}` |
| `data_history` | История объекта | `{kind: string, id: int}` | `{object {kind, id, name, created_at, updated_at}, origin [..] (ADR 0025), edits [{at, action, summary}] ≤ 50, versions [..]}` |
| `data_audit` | Итог аудита | `{set_id?}` | `{set_id, ran_at | null, stale, counts {e, w, i}, rules [{rule, title, severity, holes, count}]}` или «Аудит не запускался» |
| `data_findings` | Находки аудита | `{set_id?, rule?, severity?: "E"|"W"|"I", hole_id?, offset?, limit? ≤ 200}` | `{total, rows: [{rule, severity, hole_id, message, examples}], next_offset}` |
| `data_table` | Таблица набора | `{set_id?, table: string, hole_id?, columns?: string (через запятую), offset?, limit? ≤ 200}` | `{table, total, columns, rows: [[…]], next_offset}` |
| `data_stats` | Статистика компонента | `{set_id?, table: string, field: string, domain?: string}` | `{field, unit, n, missing, min, p10, p50, p90, max, mean, sd, cv}` (пропуски не входят в n и не становятся 0) |

Схемы `data_*` — в `A/data_tools.py` (реестр `DATA_TOOLS`; вызов — `data_tools.run(name, args, project=, set_id=)`, не бросает исключений), имя и название карточки — в образцах (`contract_answers.json` → `data_tools`).
Правила чтения: ответ — компактный JSON ≤ 16 000 знаков; список, который не поместился, режется, добавляются `truncated: true` и (для страничных) `next_offset`; числа — 6 значащих (целая часть не режется:
UTM не округляется); пропуск — `null`, не 0; строки из данных проекта очищаются от управляющих знаков и режутся до 200; время одного инструмента ≤ 5 с («Слишком долго: сузьте запрос»); нет проекта — «Проект не открыт»;
`ApiError` обработчика → `tool_result` с `is_error: true` и `message + hint`. Вызовы `data_*` в журнал команд не пишутся (это не команды реестра): они видны карточками чата. Они только читают текущий проект.
Без `set_id` — активный набор из контекста; нет — ошибка «Нет активного набора скважин: укажите set_id». Ошибка — `tool_result` с
`is_error: true` и текстом; карточка показывает его как «ошибка».

## 6. Контекст экрана — `context` в телах хода
```json
{"project": {"uid": "…", "name": "Демо"} | null,
 "set": {"id": 3, "name": "Демо", "holes": 48} | null,
 "hole": {"id": "DH-012", "set_id": 3} | null,
 "panels": ["дерево", "Claude", "нижняя (Журнал)"], "windows": ["Гистограмма · Демо"],
 "view": {"mode": "iso" | "top" | "front" | "side" | "section", "perspective": true, "vexag": 1.0},
 "section": {"x0": 1000.0, "y0": 2000.0, "az": 45.0, "length": 500.0, "towards": 25.0, "away": 25.0} | null,
 "selection": {"kind": "wireframe", "id": 12, "name": "Рудное тело"} | null,
 "audit": "Аудит: 3 / 12 / 40" | "Аудит не запускался" | null}
```
≤ 4 КБ JSON (длиннее — 422 `bad_param {param: "context"}`); строки ≤ 200 знаков, `windows` ≤ 20. Ядро вставляет его блоком `<workspace>…</workspace>` в начало сообщения пользователя
(не в `system`: кеш системного промпта не ломается; закрыть тег именем объекта нельзя). Окно собирает контекст из своих хранилищ (`U/context.ts`), чтобы он поместился: при превышении
сначала отбрасываются окна, затем панели (в ядре порядок отбрасывания — `windows`, `panels`, `audit`, `selection`, `section`, `view`). Разрез без ограничения длины — `section: null`
(`SectionRef.length` в ядре обязателен: число, 0 было бы ложью); режим вида — по последней успешной `scene.view` журнала команд (по умолчанию `iso`, с отсечением — `section`).
Нет набора — `set: null`; в контекст не попадают пути файлов, ключи и токены. Картинки экрана не отправляются — только этот текстовый снимок.

## 7. Диалоги
| Маршрут | Ответ | Правила |
|---|---|---|
| `GET /dialogs?offset=0&limit=50` | `{total, items: [DialogHead]}` | новые сверху по `last_at`; `offset` ≥ 0, `limit` 1…200 (иначе 422 `bad_param`); без проекта — временные диалоги ядра (`temporary: true`, ≤ 20: 21-й вытесняет старейший; теряются при перезапуске ядра) |
| `GET /dialogs/last` | `DialogHead` или 204 | последний непустой диалог текущего проекта (пустой диалог, созданный при вопросе и не записанный из-за сбоя, через час удаляется) |
| `GET /dialogs/{id}` | `Dialog` | 404 `assistant_dialog_not_found` |
| `PATCH /dialogs/{id}` | `{title}` → `DialogHead` | 1…200 знаков после обрезки пробелов (иначе 422 `bad_param {param: "title"}`); идёт отрезок — 409 `assistant_busy`; проект «только просмотр» — 423 `project_read_only`; событие шины `assistant.dialogs` |
| `DELETE /dialogs/{id}` | 204 | идёт отрезок — 409 `assistant_busy`; проект «только просмотр» — 423; подтверждение спрашивает список истории (окно приложения) до вызова команды `assistant.delete`; части (blobs) удаляются вместе с диалогом; событие `assistant.dialogs {deleted: true}` |

`id` — строка: `"<objects.id>"` (до 12 цифр) для сохранённых, `"t-<uuid4 с дефисами>"` для временных (`DIALOG_ID_RE`). Диалог — не данные проекта: его нет в дереве данных, менеджере данных и графе зависимостей;
в сводке проекта он считается ключом `chats`; в `.bfggis` диалоги входят, учёт расхода — нет.
```json
DialogHead = {"id": "41", "uid": "…", "title": "Сводка по проекту", "created_at": "…Z", "last_at": "…Z", "questions": 3,
              "cost_usd": 0.0812 | null, "tokens": {"input": …, "output": …, "cache_read": …, "cache_write": …}, "models": ["claude-opus-5-5"],
              "state": "idle" | "running" | "pending" | "error", "temporary": false, "project_uid": "…" | null}
Dialog = {"head": DialogHead, "items": [Item], "pending": Pending | null, "note": null | "Диалог не сохранится: проект не открыт"}
Item = {"kind": "user", "id", "at", "text"}
     | {"kind": "assistant", "id", "at", "model", "text" (Markdown), "stopped": false, "interrupted": false}
     | {"kind": "tool", "id", "at", "tool_use_id", "name", "side": "data" | "ui", "title", "args": {…},
        "status": "ok" | "error" | "declined" | "waiting", "summary": "…" (≤ 4000)}
     | {"kind": "notice", "id", "at", "code", "message"}
     | {"kind": "error", "id", "at", "code", "message", "hint"}
```
`title` карточки `ui` — название команды (окно знает его по реестру; ядро хранит то, что прислало окно в `results`), `data` — §5.2.

Хранение (только ядро): `objects(kind="assistant_dialog")`, `meta` — `DialogHead` без `id` + `{parts: [sha256], bytes, pending, prices_version}`;
часть — zstd-JSON `{seq, at, messages: [MessageParam API], items: [Item]}`, владелец `object`, роль `data`; порядок — `meta.parts`. ≤ 400 частей,
≤ 32 МБ — 422 `assistant_dialog_full` «Диалог слишком длинный» + «Начните новый диалог».

## 8. Ход — `POST /turns` (SSE)
```json
{"dialog_id": null | "41", "text": "Покажи скважину DH-012 на разрезе", "context": Context, "tools": [ToolDefinition]}
```
`dialog_id: null` — новый диалог (в проекте или временный). `text` 1…20 000 знаков (иначе 422 `bad_param {param: "text"}`). Неразрешённый
`pending` закрывается результатами «Не выполнено: пользователь продолжил диалог». Синхронные отказы (до потока, JSON): 409
`assistant_not_connected`, 409 `assistant_busy`, 409 `assistant_dialog_broken` (часть диалога не читается), 404 `assistant_dialog_not_found`, 422 (`bad_param`, `assistant_bad_tools`,
`assistant_dialog_full`). Отказы после начала — событие `error` + `turn.end {state: "error"}`. Окно истории для API: оценка «знаки / 3»; > 150 тыс. токенов (с инструментами и промптом) старые
`tool_result` заменяются на «[результат сокращён]», затем — рассуждения прошлых вопросов и старые блоки `<workspace>`; текущий цикл не трогается; `model_context_window_exceeded` — одна
повторная обрезка и один повтор. Диалог в памяти ядра/проекте хранит все сообщения: обрезается только запрос.

## 9. Продолжение — `POST /dialogs/{id}/tool_results` (SSE)
```json
{"pending_id": "p-…", "results": [{"tool_use_id": "toolu_…", "ok": true, "text": "Выбрана скважина DH-012", "title": "Выбрать скважину"}],
 "context": Context, "tools": [ToolDefinition]}
```
`results` — ровно все `ui`-вызовы `pending` (иначе 409 `assistant_pending_mismatch {expected: [..]}`); `text` ≤ 4000 знаков (обрезка с «…»);
`ok: false` → `tool_result.is_error: true`. Отказ подтверждения — `{ok: false, text: "Пользователь отклонил действие"}`. Нет `pending` — 409
`assistant_no_pending`.

`POST /dialogs/{id}/cancel` → 204 (нет отрезка — тоже 204). `GET /dialogs/{id}/stream?after=<n>` — догон и продолжение событий текущего
отрезка с номера > n (`after` — целое ≥ 0, иначе 422 `bad_param {param: "after"}`); отрезка нет (завершённый уходит из реестра) — 204, правду тогда говорит `GET /dialogs/{id}`.
Отрезок идёт в своём потоке до конца, даже если читатель отключился (окно закрыто, сеть окна оборвалась): закрытое соединение останавливает только чтение.

## 10. События потока хода
| Событие | `data` | Когда |
|---|---|---|
| `turn.start` | `{dialog: DialogHead, segment_id, model, effort, user_item?: Item}` | первым; новый диалог — с его `id` |
| `text.delta` | `{item_id, text}` | кусок ответа; элементы `assistant` создаются по первому куску |
| `thinking` | `{state: "start" | "end"}` | «Claude думает…» |
| `tool.start` | `{item: Item (kind tool, side data, status "waiting")}` | вызов `data_*` |
| `tool.end` | `{item_id, status, summary}` | итог `data_*` |
| `pending` | `Pending` | нужны команды окна (последнее перед `turn.end`) |
| `usage` | `{call: {model, input, output, cache_read, cache_write, cost_usd | null, long}, dialog: {cost_usd, tokens, questions}}` | после каждого вызова API |
| `notice` | `{item: Item (kind notice)}` | `effort_lowered` (400 «глубина не поддержана» — шаг вниз по уровням; предел модели запоминается в памяти ядра, `loop.EFFORT_CAP`), `max_tokens` («Ответ обрезан: попросите продолжить»; ≤ 16 000 токенов ответа на вызов), `refusal`, `context_trimmed` (окно истории > 150 тыс. токенов, оценка «знаки / 3»), `temporary`, `max_rounds` («Достигнут предел шагов за один вопрос: напишите «продолжай»…»; ≤ 25 вызовов API на вопрос, счёт переносится через `pending.rounds`) |
| `error` | `{item: Item (kind error)}` | `assistant_offline`, `assistant_bad_key`, `assistant_model_unavailable`, `assistant_rate_limited`, `assistant_overloaded`, `assistant_api_error`, `assistant_interrupted`, `assistant_dialog_full`, `internal` |
| `turn.end` | `{state: "idle" | "pending" | "error" | "cancelled", stop_reason | null, dialog: DialogHead}` | последним; диалог уже записан |

```json
Pending = {"id": "p-…", "created_at": "…Z",
           "tools": [{"tool_use_id": "toolu_…", "name": "ui_hole_select", "command": "hole.select",
                      "args": {"hole_id": "DH-012"}, "item_id": "…"}]}
```
`command` в ответах ядра **всегда `null`** (ядро не знает команд окна; схема — `str | null`, `loop.py`); окно ищет команду по `name` (`findByToolName`: `ui_hole_select` → `hole.select`, `_` может входить
в id команды, `section.through_hole`). Результаты `data_*` того же ответа модели ядро хранит в `meta.pending` (окну не нужны). Карточка `ui`-команды пишется в диалог дважды: `waiting` (с `pending`), затем —
с результатом (`tool_results`); при чтении диалога повторы склеиваются (`loop.read_dialog`).

## 11. Порядок на окне (`U/pending.ts`)
1. `pending` → карточки `waiting`; по порядку `registry.execute(command, args, "claude")` (подтверждение `confirm` — `ConfirmCard`, ADR 0029 реш. 26).
2. Все выполнены → `POST …/tool_results` с новым `context` и `tools` → новый поток.
3. Диалог открыт с `pending` после перезапуска (`Dialog.pending` есть, отрезка нет) — карточки «Ожидает выполнения в окне», кнопки «Выполнить» /
   «Отклонить» (все: `ok: false, text: "Пользователь отклонил действие"`); без автозапуска.
4. «Стоп» во время выполнения команд — оставшиеся `ok: false, text: "Пользователь остановил ход"`, затем `cancel` не нужен.

## 12. Ошибки API Anthropic → коды
Тайм-ауты клиента SDK: подключение 5 с, чтение (между событиями потока) 90 с, запись 30 с; повторов SDK — 2 (429, 5xx, обрыв до первого события). Прокси и сертификаты ОС — через `httpx2`
(`HTTPS_PROXY`) и `truststore`. Секрет вырезается из текстов ошибок (`connection.scrub`).
| Причина | Код | `message` / `hint` |
|---|---|---|
| нет связи, DNS, прокси, тайм-аут подключения | 503 / событие `assistant_offline` | «Ассистент недоступен без интернета.» / «Проверьте подключение к интернету или прокси; история диалогов доступна.» |
| 401, 403 | `assistant_bad_key` | «Ключ или вход Claude отклонён» / «Проверьте ключ в окне «Claude: подключение» или войдите заново» |
| 404 модели | `assistant_model_unavailable` (422) | «Модель «…» недоступна для вашей организации.» / «Выберите другую модель в окне «Claude: подключение».» |
| 429 после повторов | `assistant_rate_limited` (429) | «Превышен лимит запросов Claude.» / «Подождите минуту и повторите.» |
| 529, 5xx после повторов | `assistant_overloaded` (503) | «Сервис Claude перегружен.» / «Повторите позже или выберите другую модель.» |
| 400 прочее | `assistant_api_error {detail}` | «Claude API отклонил запрос» / «Сообщите разработчику: текст ошибки в журнале ядра» |
| обрыв потока после начала | `assistant_interrupted` | «Связь прервалась» / «Нажмите «Повторить»; частичный ответ сохранён.» (текст ядра; **кнопки «Повторить» в панели нет — не реализовано**: вопрос отправляют заново; история API при сбое до первого ответа не пишется) |

## 13. Подписи интерфейса
Строка состояния (7.0.5, справа): `none` → «Claude: не подключён»; `login` → «Claude: вход…»; `connected` + `account` → «Claude: аккаунт · Opus 5.5»;
`api_key` → «Claude: ключ · Opus 5.5»; `env` → «Claude: подключён · Opus 5.5»; `online: false` — добавить « · нет связи». Значок шапки: `--ok` —
`connected` и `online ≠ false`; `--warn` — `login` или `online: false`; `--text-faint` — `none`; подсказка — тот же текст.
`online` — итог **последнего** вызова API (`Connection.note_call`: вызов хода, «Проверить связь», проверка ключа); `false` только если до API не дошли (`APIConnectionError`, не 401/429/5xx).
Пока `online: false`, панель не принимает вопросы («Ассистент недоступен без интернета», `assistant.ask` недоступна с этой причиной), а в `online: true` её возвращает только «Проверить связь»
(`POST /connection/test`) или новое подключение: **самостоятельного опроса связи нет** (выход из «нет связи» — действие пользователя; автоповтор не реализован).

## 14. Ошибки маршрутов
| HTTP | `code` | Когда |
|---|---|---|
| 403 | `shell_only` | секретный маршрут с `Origin` |
| 404 | `assistant_dialog_not_found`, `assistant_login_gone` | — |
| 409 | `no_project` (только `PATCH`/`DELETE` сохранённого), `assistant_not_connected`, `assistant_busy`, `assistant_no_pending`, `assistant_pending_mismatch {expected}`, `assistant_login_busy` (вход уже запускается или проверяет код), `assistant_dialog_broken` (часть диалога не читается: файл повреждён или удалён; диалог можно удалить) | — |
| 413 | `request_too_large` (общий предел ядра: тело > 8 МБ) | — |
| 422 | `bad_param {param}`, `assistant_bad_key_format`, `assistant_bad_key`, `assistant_bad_tools {detail}`, `assistant_login_code`, `assistant_dialog_full` (≤ 400 частей, ≤ 32 МБ), `assistant_model_unavailable` (событие потока; HTTP-статус кода — 422), `bad_request` (вложенность JSON > 64) | — |
| 423 | `project_read_only` (`PATCH`/`DELETE` сохранённого диалога в проекте «только просмотр»; сами вопросы в таком проекте идут во временный диалог, № 134) | — |
| 429, 500, 502, 503 | `assistant_rate_limited`, `internal`, `assistant_api_error`, `assistant_interrupted`, `assistant_overloaded` | коды **потока** (§12): статус из таблицы `errors.REFUSALS`, но в ходе хода они приходят событием `error` (HTTP-ответом хода остаётся 200 после первого события) |
| 501 | `assistant_no_ant` | нет `ant` в сборке (`BF_ANT_PATH` не задан или файла нет; `tools/ci/ant.json` без закреплённых url/sha256 — сборка без `ant`, вопрос № 125) |
| 502 | `assistant_login_failed {detail}` | `ant` не выдал адрес / завершился с ошибкой |
| 503 | `assistant_offline` | синхронная проверка ключа или связи без сети |

## 15. Образцы и мок
`contract_answers.json` (ключи; настоящие ответы ядра, `{status, body}` или список событий потока): `status_none`, `status_key`, `status_account`,
`usage`, `prefs_bad`, `key_bad_format`, `key_bad`, `dialogs_list`, `dialog_full_sample` (с `pending`, элементами всех видов), `dialog_temporary`,
`stream_text` («Сводка по проекту» — только текст), `stream_data_tools` (два `data_*` на демо-проекте + текст), `stream_pending` («Покажи скважину DH-012
на разрезе» → `pending` из `ui_hole_select`, `ui_section_through_hole`), `stream_after_results`, `stream_confirm` (`ui_project_delete`),
`stream_declined` (отказ), `stream_offline`, `stream_cancelled`, `stream_notice` (`max_tokens`), `stream_temporary` (диалог без проекта),
`stream_interrupted` (обрыв), `stream_unauthorized` (401), `data_tools` (все 9 из реестра), `errors`, `requests` (тела запросов, вручную).
Идентификаторы и время в образцах заменены стабильными; `command` в `pending` — `null` (ядро не знает команд окна). Сценарии мока —
`core/tests/data/assistant/scenarios/<имя>.json`: `{"steps": [{"match": {"last_user_text"?: "…", "tool_result_for"?: "ui_hole_select"},
"reply": {"stream": true, "content": [{"type": "text", "text": "…"} | {"type": "tool_use", "name": "…", "input": {…}}], "stop_reason": "…",
"usage": {…}} | {"status": 401 | 429 | 529} | {"drop_after_events": 3}}]}`; `GET /__requests` — принятые запросы (без заголовков авторизации).
