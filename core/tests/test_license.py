"""Licence window data (ТЗ 5.7): PC code 2 of 3, trial period of 30 days, clock set back."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from bf_core.api.app import CoreState, create_app
from bf_core.license.hwid import Hwid, current_hwid
from bf_core.license.trial import TrialStore

T0 = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)


def store(tmp: Path, hwid: str = "A-B-C") -> TrialStore:
    return TrialStore([tmp / "appdata" / "trial.json", tmp / "local" / "trial.dat"], hwid, use_registry=False)


def test_hwid_is_stable_and_matches_two_of_three() -> None:
    h = current_hwid()
    assert h.code == current_hwid().code
    assert len(h.code) == 26
    a, b, c = h.parts
    assert h.matches(f"{a}-{b}-FFFFFFFF")
    assert not h.matches(f"{a}-EEEEEEEE-FFFFFFFF")
    assert not Hwid(("1", "2", "3")).matches("garbage")


def test_trial_counts_30_days(tmp_path: Path) -> None:
    s = store(tmp_path)
    assert s.check(T0).days_left == 30
    st = s.check(T0 + timedelta(days=10, hours=1))
    assert (st.days_left, st.active) == (20, True)
    st = s.check(T0 + timedelta(days=31))
    assert (st.days_left, st.active) == (0, False)


def test_earliest_first_run_wins_and_deleting_one_mark_does_not_reset(tmp_path: Path) -> None:
    s = store(tmp_path)
    s.check(T0)
    (tmp_path / "appdata" / "trial.json").unlink()
    assert s.check(T0 + timedelta(days=5)).first_run == T0


def test_clock_set_back_pauses_trial(tmp_path: Path) -> None:
    s = store(tmp_path)
    s.check(T0 + timedelta(days=3))
    st = s.check(T0)
    assert st.clock_back and not st.active


def test_tampered_mark_is_ignored(tmp_path: Path) -> None:
    s = store(tmp_path)
    s.check(T0)
    for f in (tmp_path / "appdata" / "trial.json", tmp_path / "local" / "trial.dat"):
        f.write_text(f.read_text().replace("2026-11-16", "2027-01-01"))
    assert s.check(T0 + timedelta(days=1)).first_run == T0 + timedelta(days=1)


def test_marks_bound_to_pc(tmp_path: Path) -> None:
    store(tmp_path, "PC1").check(T0)
    assert store(tmp_path, "PC2").check(T0 + timedelta(days=9)).days_left == 30


def test_license_endpoint(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BF_LOCAL_DIR", str(tmp_path / "local"))
    c = TestClient(create_app(CoreState(token="t", data_dir=str(tmp_path / "data"))), base_url="http://127.0.0.1:1")
    r = c.get("/api/v1/license", headers={"X-BF-Token": "t"})
    assert r.status_code == 200
    body = r.json()
    assert body["hwid"] == current_hwid().code
    assert body["trial"]["days_left"] == 30
    assert "Пробный период" in body["message"]
