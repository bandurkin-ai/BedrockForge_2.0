# Э23 — контракт ассистента Claude (подшаг 23.0, контракт первым)

**Состояние: контракт предложен (23.0); образцы ответов — `core/tests/data/assistant/contract_answers.json` (23.1 — образцы, 23.11 — настоящие,
`BF_REGEN_ASSISTANT=1 uv run pytest -q tests/test_assistant_contract.py`).** Источник истины — ADR 0029 (решения 1–40, вопросы № 125–139), ТЗ 7.32,
6.9–6.10, 7.2, 7.0.3–7.0.5, 5.6. Изменение формы — только через оркестратора. Пути: `A/` = `core/bf_core/assistant/`, `U/` = `ui/src/modules/assistant/`.
Префикс маршрутов — `/api/v1/assistant` (роутер `A/router.py`: `routes.py`; регистрация `"assistant"` в `api/modules.py` — оркестратор).

## 1. Общие правила

- `X-BF-Token`, Host/Origin — как у всего ядра (S1–S2). Время — ISO 8601 UTC (`"2026-11-26T10:00:00Z"`); деньги — USD, float, 6 знаков;
  токены — целые. Пусто — `null`, **никогда не 0** (стоимость неизвестной модели — `null`).
- Ошибка — `{code, message, hint, …}` по-русски с подсказкой (§14). Нет проекта там, где он нужен, — 409 `no_project`.
- **Только оболочка** (`shell`): запрос **без** заголовка `Origin` (окно всегда шлёт `Origin: app://bedrockforge`) — иначе 403 `shell_only`.
  Тела и ответы этих маршрутов не пишутся в журналы.
- Секрет (ключ, токены) **никогда** не возвращается маршрутами окна и не попадает в события, диалоги, журналы, `prefs.json`, проект.
- Потоковые ответы — `Content-Type: text/event-stream`, `Cache-Control: no-store`; события — `id: <n>` (номер в отрезке, с 1), `event: <имя>`,
  `data: <JSON>`; комментарий `: keepalive` раз в 15 с. Чтение — `fetch` + `parseSse` (как `modules/jobs/state.ts`).
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
| `POST /connection/key` | окно | `{key}` → `status` | формат `^sk-ant-[A-Za-z0-9_-]{20,}$` (иначе 422 `assistant_bad_key_format`); проверка `models.retrieve(model)` **до** сохранения: 401/403 → 422 `assistant_bad_key` (состояние прежнее); нет сети → 503 `assistant_offline` (ключ не принят); успех → секрет в памяти, `kind: "api_key"`, `seal_needed: true` |
| `POST /connection/test` | окно | `{}` → `{ok, latency_ms, model, model_available, effort_supported: [..] | null, organization_id | null, message}` | «Проверить связь»: `models.retrieve(model)`; нет подключения — 409 `assistant_not_connected` |
| `DELETE /connection` | окно | → `status` | «Выйти»: секрет из памяти — прочь; остаётся `ANTHROPIC_API_KEY` окружения, если задан (`kind: "env"`); окно затем зовёт `bf.claudeForget()` |
| `PUT /connection/secret` | **shell** | `{kind: "api_key" | "account", secret: {…}, secure_storage}` → `{ok, kind, mask}` | старт оболочки и перезапуск ядра; `api_key`: `{key}`; `account`: `{access_token, expires_at, refresh_token, client_id, organization_id, workspace_id, base_url?}`; без проверки связи (офлайн-старт) |
| `GET /connection/secret` | **shell** | → `{kind, secret, revision}` или 204 (нечего хранить) | главный процесс шифрует (`safeStorage`) и пишет `assistant/connection.bin`; затем `POST /connection/sealed {revision}` → `seal_needed: false` |

Оболочка (`app/src/main/claude.ts`, preload `window.bf`): `claudeSeal(): Promise<{ok, message?}>`, `claudeForget(): Promise<void>`,
`claudeStorage(): {secure: boolean, backend: string}` (sync). `secure: false` (Linux `basic_text`, нет DPAPI) — `claudeSeal` отвечает
`{ok: false, message: "Хранилище ключей ОС недоступно: задайте ANTHROPIC_API_KEY"}`, кроме `BF_E2E=1`.

### Вход аккаунтом (`ant`)
| Маршрут | Тело → ответ | Правила |
|---|---|---|
| `POST /login/start` | `{}` → `{login_id, url, expires_at}` | нет `ant` — 501 `assistant_no_ant` «Вход аккаунтом недоступен в этой сборке» + «Введите API-ключ»; `ant` не выдал адрес за 30 с — 502 `assistant_login_failed {detail}`; новый запуск закрывает прежний |
| `POST /login/{login_id}/finish` | `{code}` → `status` | код ≤ 4096, без `\r\n\0` (иначе 422 `bad_param {param: "code"}`); неверный код — 422 `assistant_login_code`; сеанс истёк (10 мин) или неизвестен — 404 `assistant_login_gone`; успех — `kind: "account"`, `seal_needed: true` |
| `DELETE /login/{login_id}` | → 204 | отмена (процесс `ant` завершается, временная папка удаляется) |

## 4. Расход — `GET /usage`

```json
{"today": Period, "days30": Period, "all": Period, "prices_version": "2026-11", "since": "…Z" | null}
Period = {"requests": 12, "tokens": {"input": 1200, "output": 800, "cache_read": 90000, "cache_write": 30000},
          "cost_usd": 0.4123 | null,
          "by_model": [{"model": "claude-opus-5-5", "title": "Opus 5.5", "requests": 10, "tokens": {…}, "cost_usd": 0.40 | null}]}
```
«Сегодня» — местные сутки машины; «30 дней» — последние 30 местных суток с сегодняшними. `cost_usd` периода — сумма известных; есть вызовы
неизвестной модели — `unpriced_requests` в периоде. Строка `usage.jsonl`: `{"at", "model", "input", "output", "cache_read", "cache_write",
"cost_usd", "long", "dialog_uid", "project_uid", "prices_version"}`.

## 5. Инструменты

### 5.1. Инструменты окна (из реестра) — тело хода `tools`
`ToolDefinition` = как `ui/src/shell/commands.ts` `commandTools()`:
`{name: "ui_<id с _>", description, input_schema: {type: "object", properties: {k: {type: T | [T, "null"], description?, enum?}}, required: [..], additionalProperties: false}}`,
`T ∈ string, number, integer, boolean`. Проверка ядра (иначе 422 `assistant_bad_tools {detail}`): имя `^ui_[a-z0-9_]{1,60}$` без повторов,
≤ 400 инструментов, описание ≤ 2000 знаков, всё ≤ 256 КБ (JSON). Ядро сортирует по имени; описания > 600 знаков — только при 23.9б.

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

Схемы `data_*` — в `A/data_tools.py` (константа `DATA_TOOLS`), имя и название карточки — в образцах (`contract_answers.json` → `data_tools`).
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
≤ 4 КБ JSON (длиннее — 422 `bad_param {param: "context"}`); строки ≤ 200 знаков, `windows` ≤ 20. Ядро вставляет его блоком `<workspace>…</workspace>`.

## 7. Диалоги
| Маршрут | Ответ | Правила |
|---|---|---|
| `GET /dialogs?offset=0&limit=50` | `{total, items: [DialogHead]}` | новые сверху по `last_at`; без проекта — временные диалоги ядра (`temporary: true`) |
| `GET /dialogs/last` | `DialogHead` или 204 | последний непустой диалог текущего проекта |
| `GET /dialogs/{id}` | `Dialog` | 404 `assistant_dialog_not_found` |
| `PATCH /dialogs/{id}` | `{title}` → `DialogHead` | 1…200 знаков (иначе 422 `bad_param {param: "title"}`); идёт отрезок — 409 `assistant_busy` |
| `DELETE /dialogs/{id}` | 204 | идёт отрезок — 409 `assistant_busy`; подтверждение — окно приложения (`assistant.delete`) |

`id` — строка: `"<objects.id>"` для сохранённых, `"t-<uuid>"` для временных.
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
`assistant_not_connected`, 409 `assistant_busy`, 404, 422. Отказы после начала — событие `error` + `turn.end {state: "error"}`.

## 9. Продолжение — `POST /dialogs/{id}/tool_results` (SSE)
```json
{"pending_id": "p-…", "results": [{"tool_use_id": "toolu_…", "ok": true, "text": "Выбрана скважина DH-012", "title": "Выбрать скважину"}],
 "context": Context, "tools": [ToolDefinition]}
```
`results` — ровно все `ui`-вызовы `pending` (иначе 409 `assistant_pending_mismatch {expected: [..]}`); `text` ≤ 4000 знаков (обрезка с «…»);
`ok: false` → `tool_result.is_error: true`. Отказ подтверждения — `{ok: false, text: "Пользователь отклонил действие"}`. Нет `pending` — 409
`assistant_no_pending`.

`POST /dialogs/{id}/cancel` → 204 (нет отрезка — тоже 204). `GET /dialogs/{id}/stream?after=<n>` — догон и продолжение событий текущего
отрезка с номера > n; отрезка нет — 204.

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
| `notice` | `{item: Item (kind notice)}` | `effort_lowered`, `max_tokens` («Ответ обрезан: попросите продолжить»), `refusal`, `context_trimmed`, `temporary`, `max_rounds` |
| `error` | `{item: Item (kind error)}` | `assistant_offline`, `assistant_bad_key`, `assistant_rate_limited`, `assistant_overloaded`, `assistant_api_error`, `assistant_interrupted`, `assistant_dialog_full`, `internal` |
| `turn.end` | `{state: "idle" | "pending" | "error" | "cancelled", stop_reason | null, dialog: DialogHead}` | последним; диалог уже записан |

```json
Pending = {"id": "p-…", "created_at": "…Z",
           "tools": [{"tool_use_id": "toolu_…", "name": "ui_hole_select", "command": "hole.select",
                      "args": {"hole_id": "DH-012"}, "item_id": "…"}]}
```
`command` — `name` без `ui_` с `_` → `.` по реестру окна (`findByToolName`); ядро передаёт `name`, окно ищет команду само. Результаты `data_*`
того же ответа модели ядро хранит в `meta.pending` (окну не нужны).

## 11. Порядок на окне (`U/pending.ts`)
1. `pending` → карточки `waiting`; по порядку `registry.execute(command, args, "claude")` (подтверждение `confirm` — `ConfirmCard`, ADR 0029 реш. 26).
2. Все выполнены → `POST …/tool_results` с новым `context` и `tools` → новый поток.
3. Диалог открыт с `pending` после перезапуска (`Dialog.pending` есть, отрезка нет) — карточки «Ожидает выполнения в окне», кнопки «Выполнить» /
   «Отклонить» (все: `ok: false, text: "Пользователь отклонил действие"`); без автозапуска.
4. «Стоп» во время выполнения команд — оставшиеся `ok: false, text: "Пользователь остановил ход"`, затем `cancel` не нужен.

## 12. Ошибки API Anthropic → коды
| Причина | Код | `message` / `hint` |
|---|---|---|
| нет связи, DNS, прокси, тайм-аут подключения | 503 / событие `assistant_offline` | «Ассистент недоступен без интернета» / «Проверьте подключение к интернету или прокси; история диалогов доступна» |
| 401, 403 | `assistant_bad_key` | «Ключ или вход Claude отклонён» / «Проверьте ключ в окне «Claude: подключение» или войдите заново» |
| 404 модели | `assistant_model_unavailable` | «Модель … недоступна для вашей организации» / «Выберите другую модель» |
| 429 после повторов | `assistant_rate_limited` | «Превышен лимит запросов Claude» / «Подождите минуту и повторите» |
| 529, 5xx после повторов | `assistant_overloaded` | «Сервис Claude перегружен» / «Повторите позже или выберите другую модель» |
| 400 прочее | `assistant_api_error {detail}` | «Claude API отклонил запрос» / «Сообщите разработчику: текст ошибки в журнале ядра» |
| обрыв потока после начала | `assistant_interrupted` | «Связь прервалась» / «Нажмите «Повторить»» (частичный текст сохранён) |

## 13. Подписи интерфейса
Строка состояния (7.0.5, справа): `none` → «Claude: не подключён»; `login` → «Claude: вход…»; `connected` + `account` → «Claude: аккаунт · Opus 5.5»;
`api_key` → «Claude: ключ · Opus 5.5»; `env` → «Claude: подключён · Opus 5.5»; `online: false` — добавить « · нет связи». Значок шапки: `--ok` —
`connected` и `online ≠ false`; `--warn` — `login` или `online: false`; `--text-faint` — `none`; подсказка — тот же текст.

## 14. Ошибки маршрутов
| HTTP | `code` | Когда |
|---|---|---|
| 403 | `shell_only` | секретный маршрут с `Origin` |
| 404 | `assistant_dialog_not_found`, `assistant_login_gone` | — |
| 409 | `no_project` (только `PATCH`/`DELETE` сохранённого), `assistant_not_connected`, `assistant_busy`, `assistant_no_pending`, `assistant_pending_mismatch` | — |
| 422 | `bad_param {param}`, `assistant_bad_key_format`, `assistant_bad_key`, `assistant_bad_tools {detail}`, `assistant_login_code`, `assistant_dialog_full` | — |
| 501 | `assistant_no_ant` | нет `ant` в сборке |
| 502 | `assistant_login_failed {detail}` | `ant` не выдал адрес / завершился с ошибкой |
| 503 | `assistant_offline` | синхронная проверка ключа или связи без сети |

## 15. Образцы и мок
`contract_answers.json` (ключи): `status_none`, `status_key`, `status_account`, `usage`, `prefs_bad`, `key_bad_format`, `key_bad`, `dialogs_list`,
`dialog_full_sample` (с `pending`), `stream_text` (события «Сводка по проекту» — только текст), `stream_data_tools` (два `data_*` + текст),
`stream_pending` («Покажи скважину DH-012 на разрезе» → `pending` из `ui_hole_select`, `ui_section_through_hole`), `stream_after_results`,
`stream_confirm` (`ui_project_delete`), `stream_offline`, `stream_cancelled`, `data_tools` (схемы), `errors`. Сценарии мока —
`core/tests/data/assistant/scenarios/<имя>.json`: `{"steps": [{"match": {"last_user_text"?: "…", "tool_result_for"?: "ui_hole_select"},
"reply": {"stream": true, "content": [{"type": "text", "text": "…"} | {"type": "tool_use", "name": "…", "input": {…}}], "stop_reason": "…",
"usage": {…}} | {"status": 401 | 429 | 529} | {"drop_after_events": 3}}]}`; `GET /__requests` — принятые запросы (без заголовков авторизации).
