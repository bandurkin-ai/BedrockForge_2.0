"""GET /api/v1/license — PC code and trial status for the «Лицензия» window (Э1; licence files — Э24)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from bf_core.paths import local_app_dir

from .hwid import current_hwid
from .trial import TrialStore

router = APIRouter()


def trial_store(data_dir: str | None) -> TrialStore:
    hwid = current_hwid().code
    files = [local_app_dir() / "license" / "trial.dat"]
    if data_dir:
        files.insert(0, Path(data_dir) / "license" / "trial.json")
    return TrialStore(files, hwid)


@router.get("/license")
def license_status(request: Request) -> dict[str, object]:
    state = request.app.state.core
    hwid = current_hwid()
    trial = trial_store(state.data_dir).check()
    if trial.clock_back:
        message = "Часы компьютера переведены назад: пробный период приостановлен. Проверьте дату и время Windows."
    elif trial.active:
        message = f"Пробный период: осталось {trial.days_left} дн."
    else:
        message = "Пробный период закончился: доступен режим «только просмотр». Загрузите файл лицензии."
    return {"hwid": hwid.code, "mode": "trial", "trial": trial.as_dict(), "license": None, "message": message}
