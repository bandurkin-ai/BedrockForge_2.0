"""Errors, notices and fixed texts of the Claude assistant (подшаг 23.1; ТЗ 7.32; ADR 0029; e23-api.md §12, §14).

    refuse(code, message=None, hint=None, **extra) -> ApiError      REFUSALS[code]; `extra` - machine fields only
    bad_param(param, message=None, hint=None) -> ApiError           422 `bad_param {param}`
    model_unavailable(model), pending_mismatch(expected)            the codes whose text or fields depend on the call
    error_fields(code, message=None, hint=None) -> (message, hint)  texts of an `error` event / `Item(kind="error")`
    notice_message(code) -> str                                     text of a `notice` event / `Item(kind="notice")`
    Refuse(code, **extra)             raised by a validator of `schemas.py`; `parse_body` turns it into ApiError

Every user-facing text is Russian with a hint. A secret (key, token, code) is never part of a text or of `extra`.
"""

from __future__ import annotations

from typing import Any

from bf_core.api.errors import ApiError

# code -> (HTTP status, message, hint). The status of a code that only travels in an `error` event (§10) is the one
# the same failure would have as the answer of a plain route.
REFUSALS: dict[str, tuple[int, str, str]] = {
    # --- §14: the routes ---
    "shell_only": (
        403,
        "Маршрут доступен только оболочке приложения.",
        "Секреты передаёт главный процесс приложения; из окна этот маршрут не вызывается.",
    ),
    "no_project": (409, "Проект не открыт.", "Откройте проект: диалог сохраняется в проекте."),
    "assistant_dialog_not_found": (
        404,
        "Диалог не найден.",
        "Возможно, он удалён или относится к другому проекту. Откройте историю диалогов.",
    ),
    "assistant_login_gone": (
        404,
        "Сеанс входа истёк или не найден.",
        "Нажмите «Войти аккаунтом» ещё раз и введите новый код.",
    ),
    "assistant_not_connected": (
        409,
        "Claude не подключён.",
        "Откройте «Claude: подключение» и введите ключ API или войдите аккаунтом.",
    ),
    "assistant_busy": (
        409,
        "Ассистент отвечает на предыдущий вопрос.",
        "Дождитесь конца ответа или нажмите «Стоп».",
    ),
    "assistant_no_pending": (
        409,
        "Диалог не ждёт результатов команд.",
        "Откройте диалог заново: возможно, он уже продолжен.",
    ),
    "assistant_pending_mismatch": (
        409,
        "Результаты не совпадают с ожидаемыми командами.",
        "Передайте результат каждой команды, которую просил выполнить ассистент, и только их.",
    ),
    "bad_param": (422, "Неверное значение параметра.", "Проверьте значение и повторите."),
    "assistant_bad_key_format": (
        422,
        "Ключ API имеет неверный формат.",
        "Ключ начинается с «sk-ant-» и не содержит пробелов; скопируйте его целиком из Anthropic Console.",
    ),
    "assistant_bad_key": (
        422,
        "Ключ или вход Claude отклонён.",
        "Проверьте ключ в окне «Claude: подключение» или войдите заново.",
    ),
    "assistant_bad_tools": (
        422,
        "Список инструментов не принят.",
        "Обновите приложение; если ошибка повторяется — сообщите разработчику.",
    ),
    "assistant_login_code": (
        422,
        "Код входа не принят.",
        "Скопируйте код со страницы Anthropic целиком или начните вход заново.",
    ),
    "assistant_dialog_full": (422, "Диалог слишком длинный.", "Начните новый диалог."),
    "assistant_no_ant": (
        501,
        "Вход аккаунтом недоступен в этой сборке.",
        "Введите ключ API в окне «Claude: подключение» или задайте переменную ANTHROPIC_API_KEY.",
    ),
    "assistant_login_failed": (
        502,
        "Не удалось начать вход аккаунтом.",
        "Повторите попытку или введите ключ API в окне «Claude: подключение».",
    ),
    "assistant_offline": (
        503,
        "Ассистент недоступен без интернета.",
        "Проверьте подключение к интернету или прокси; история диалогов доступна.",
    ),
    # --- §12: the failures of the API of Anthropic (mostly events of the stream) ---
    "assistant_model_unavailable": (
        422,
        "Модель недоступна для вашей организации.",
        "Выберите другую модель в окне «Claude: подключение».",
    ),
    "assistant_rate_limited": (429, "Превышен лимит запросов Claude.", "Подождите минуту и повторите."),
    "assistant_overloaded": (503, "Сервис Claude перегружен.", "Повторите позже или выберите другую модель."),
    "assistant_api_error": (
        502,
        "Claude API отклонил запрос.",
        "Сообщите разработчику: текст ошибки в журнале ядра.",
    ),
    "assistant_interrupted": (502, "Связь прервалась.", "Нажмите «Повторить»; частичный ответ сохранён."),
    "internal": (500, "Внутренняя ошибка ассистента.", "Скопируйте журнал ядра и отправьте разработчику."),
}

#: The codes of an `error` event of the stream (§10) — the same table, the subset the stream may carry.
STREAM_ERRORS: tuple[str, ...] = (
    "assistant_offline",
    "assistant_bad_key",
    "assistant_model_unavailable",
    "assistant_rate_limited",
    "assistant_overloaded",
    "assistant_api_error",
    "assistant_interrupted",
    "assistant_dialog_full",
    "internal",
)

#: The codes of a `notice` event (§10) -> the text of the notice.
NOTICES: dict[str, str] = {
    "effort_lowered": "Выбранная глубина не поддерживается этой моделью: использована ближайшая допустимая.",
    "max_tokens": "Ответ обрезан: попросите продолжить.",
    "refusal": "Claude отказался отвечать на этот запрос. Переформулируйте вопрос.",
    "context_trimmed": "Диалог длинный: старые результаты инструментов сокращены, чтобы он поместился в контекст.",
    "temporary": "Диалог не сохранится: проект не открыт.",
    "max_rounds": "Достигнут предел шагов за один вопрос: напишите «продолжай», если нужно больше.",
}

# The fixed texts of the loop (§9, §11): the model sees them as the text of a `tool_result`.
TEXT_DECLINED = "Пользователь отклонил действие"
TEXT_STOPPED = "Пользователь остановил ход"
TEXT_SKIPPED = "Не выполнено: пользователь продолжил диалог"
TEXT_NO_SET = "Нет активного набора скважин: укажите set_id"
TEXT_FROM_ASSISTANT = "Нельзя вызвать из ассистента"
TEXT_NOT_SECURE = "Хранилище ключей ОС недоступно: задайте ANTHROPIC_API_KEY"
TEXT_SUBSCRIPTION = (
    "Подписка Claude (Pro, Max, Team) панелью не используется: нужен ключ API или вход аккаунтом Anthropic Console."
)
NOTE_TEMPORARY = "Диалог не сохранится: проект не открыт"

#: The Russian name of a parameter and the hint of `bad_param` for it (the first key of the failed body).
PARAMS: dict[str, tuple[str, str]] = {
    "body": ("тело запроса", "Передайте JSON-объект с полями, описанными в контракте."),
    "text": ("текст вопроса", "Вопрос — от 1 до 20 000 знаков."),
    "title": ("название диалога", "Название — от 1 до 200 знаков."),
    "code": ("код входа", "Код — до 4096 знаков без переводов строки."),
    "key": ("ключ API", "Введите ключ API целиком."),
    "model": ("модель", "Выберите модель из списка в окне «Claude: подключение»."),
    "effort": ("глубина", "Допустимо: low, medium, high, xhigh, max."),
    "context": ("контекст экрана", "Контекст — до 4 КБ, строки до 200 знаков, окон не больше 20."),
    "results": ("результаты команд", "Передайте результат каждой команды один раз."),
    "pending_id": ("номер ожидания", "Возьмите его из события pending."),
    "dialog_id": ("номер диалога", "Возьмите его из списка диалогов."),
    "secret": ("секрет подключения", "Передайте поля, описанные в контракте для этого вида подключения."),
    "kind": ("вид подключения", "Допустимо: api_key, account."),
    "revision": ("номер ревизии", "Возьмите его из ответа на GET /connection/secret."),
    "secure_storage": ("признак защищённого хранилища", "Передайте true или false."),
    "offset": ("смещение", "Смещение — целое число от 0."),
    "limit": ("размер страницы", "Размер страницы — целое число от 1 до 200."),
    "after": ("номер события", "Номер события — целое число от 0."),
}


class Refuse(ValueError):
    """Raised by a validator of `schemas.py` to give a specific code instead of `bad_param`; `parse_body` turns it
    into `refuse(code, message, hint, **extra)`. Never carries the checked value."""

    def __init__(self, code: str, message: str | None = None, hint: str | None = None, **extra: Any) -> None:
        super().__init__(code)
        self.code, self.message, self.hint, self.extra = code, message, hint, extra


def refuse(code: str, message: str | None = None, hint: str | None = None, **extra: Any) -> ApiError:
    """An ApiError of REFUSALS[code]; `message` / `hint` replace the stock text, `extra` - machine fields (`param`,
    `detail`, `expected`), never user text and never a secret."""
    status, msg, tip = REFUSALS[code]
    return ApiError(status, code, message or msg, hint or tip, extra or None)


def bad_param(param: str, message: str | None = None, hint: str | None = None) -> ApiError:
    """422 `bad_param {param}`: «Неверное значение: <имя параметра>.» with the hint of PARAMS."""
    label, tip = PARAMS.get(param, (param, REFUSALS["bad_param"][2]))
    return refuse("bad_param", message or f"Неверное значение параметра «{label}».", hint or tip, param=param)


def model_unavailable(model: str) -> ApiError:
    """404 of the API for a model -> 422 `assistant_model_unavailable` (§12)."""
    return refuse(
        "assistant_model_unavailable",
        f"Модель «{model}» недоступна для вашей организации.",
        model=model,
    )


def pending_mismatch(expected: list[str]) -> ApiError:
    """409 `assistant_pending_mismatch {expected}`: `expected` - the `tool_use_id` of each call to answer."""
    return refuse("assistant_pending_mismatch", expected=list(expected))


def error_fields(code: str, message: str | None = None, hint: str | None = None) -> tuple[str, str]:
    """(message, hint) of an `error` event / `Item(kind="error")`; `message` / `hint` replace the stock text."""
    _, msg, tip = REFUSALS[code]
    return message or msg, hint or tip


def notice_message(code: str) -> str:
    return NOTICES[code]
