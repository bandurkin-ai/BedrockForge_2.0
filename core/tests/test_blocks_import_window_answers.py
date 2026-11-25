"""The answers of the core the wizard of the block model is read against (подшаг 17.38; ТЗ 7.31, 8.7.2; ADR 0023
decisions 5–7; e17-api.md §5): the composition of block files through the real route `POST /import/objects/inspect`
(`files[].blocks` — the roles of the columns, the grid, the check of three centres, the codes of «пусто», the units,
the warnings, `error`), the same with the answers of the wizard (`options[<путь>].blocks`), the refusals (blocks off the
grid, a wrong angle, no centres), a table of points that is not a model, and the job `import.objects` followed to its
end: the entry of `files[]` (`rows`, `parents`, `sub`, `checks`), the journal and `GET /blocks/{id}` of the model made.

The file `tests/data/blocks/import_window_answers.json` is read by vitest (`ui/test/blocks-import-*.test.ts`).
Regenerate: `BF_REGEN_BLOCKS_IMPORT=1 uv run pytest -q tests/test_blocks_import_window_answers.py`. What changes from
run to run (the numbers of the jobs, the times, the folder of the files) is not stored; the floats are kept to 9
digits."""

from __future__ import annotations

import json
import math
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any

import pytest

from tests.formats.dm_builder import F, build
from tests.test_archive import wait_job
from tests.test_blocks_infer import ORIGIN, PARENT
from tests.test_datamine_blocks import octree_dm
from tests.test_import_blocks import ROT, regular, sub_csv, text, turned_files, write_dat_model

API = "/api/v1"
BL = API + "/blocks"
DATA = Path(__file__).parent / "data" / "blocks" / "import_window_answers.json"
REAL = Path(__file__).resolve().parents[2] / "tests" / "data" / "real" / "Для BF.zip"
REGEN = os.environ.get("BF_REGEN_BLOCKS_IMPORT") == "1"
SKIPPED = ("created_at", "updated_at", "at", "seconds", "stages")  # and `size` that is a number of bytes


class Box:
    """The files of the test and the way to say them: a path is stored as «/данные/<имя>»."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> str:
        return str(self.folder / name)


@pytest.fixture()
def ic(pclient, tmp_path: Path):  # type: ignore[no-untyped-def]
    assert pclient.post(f"{API}/projects", json={"name": "Импорт блоков"}).status_code == 200
    pclient.box = Box(tmp_path / "data")
    pclient.tmp = tmp_path
    return pclient


def answer(r: Any) -> dict[str, Any]:
    return {"status": r.status_code, "body": r.json()}


def inspect(c: Any, names: list[str], blocks: dict[str, dict[str, Any]] | None = None,
            top: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:  # fmt: skip
    """`POST /import/objects/inspect`: the body the wizard sends (`options` by the path of the file: `blocks` and the
    options of the file itself — `name`, `encoding` — in `top`)."""
    options = {c.box.path(n): {"blocks": b, **(top or {}).get(n, {})} for n, b in (blocks or {}).items()}
    body = {"files": [c.box.path(n) for n in names], "options": options, "section": True}
    return {"request": body, "answer": answer(c.post(f"{API}/import/objects/inspect", json=body))}


def start(c: Any, names: list[str], options: dict[str, Any] | None = None) -> dict[str, Any]:
    """`POST /import/objects`, the job followed to its end: the result and the journal."""
    body = {"files": [c.box.path(n) for n in names],
            "options": {c.box.path(n): o for n, o in (options or {}).items()}}  # fmt: skip
    r = c.post(f"{API}/import/objects", json=body)
    assert r.status_code == 200, r.text
    started = r.json()
    job = wait_job(c, started["source"], started["id"], timeout=120)
    log = c.get(f"{API}/jobs/{started['source']}/{started['id']}/log").json()
    return {
        "request": body,
        "job": {k: job.get(k) for k in ("kind", "title", "queue", "status", "progress", "message", "error", "result")},
        "log": log,
    }


def files_of(c: Any) -> dict[str, Path]:
    """Every file of the test, written once."""
    m = regular()
    box = c.box
    out = {n: Path(box.path(n)) for n in (
        "model.dat", "model.csv", "micromine.csv", "sub.dm", "sub.fbm", "noroles.csv", "off.csv", "dups.csv",
        "points.csv", "tri.dm", "over.csv", "turned.csv", "turned.dat",
    )}  # fmt: skip
    write_dat_model(out["model.dat"], m)
    out["model.csv"].write_text(text(m, decimal="."), encoding="utf-8")
    out["micromine.csv"].write_text(text(m, types=True), encoding="utf-8")
    octree_dm(out["sub.dm"])
    octree_dm(out["sub.fbm"], layout="fbm")
    out["noroles.csv"].write_text("A,B,C,D\n1,2,3,4\n2,3,4,5\n", encoding="utf-8")
    out["off.csv"].write_text(text(m, decimal="."), encoding="utf-8")
    lines = text(m, decimal=".").splitlines()
    out["dups.csv"].write_text("\n".join([*lines, lines[3], lines[5].replace(lines[5].split(";")[6], "999.0")]) + "\n",
                               encoding="utf-8")  # fmt: skip
    out["points.csv"].write_text("X;Y;Z;AU\n1;2;3;0,5\n4;5;6;0,7\n7;8;9;0,9\n", encoding="utf-8")
    out["tri.dm"].write_bytes(build([F("TRIANGLE"), F("PID1"), F("PID2"), F("PID3")], [[1, 1, 2, 3]]))
    _, _, _, _, _ = turned_files(_Holder(box.folder), ROT)  # turned-30.0-project.csv / .dat
    shutil.copy(box.folder / f"turned-{ROT}-project.csv", out["turned.csv"])
    shutil.copy(box.folder / f"turned-{ROT}-project.dat", out["turned.dat"])
    sub_csv(out["over.csv"], [(0, 0, 1, (2, 0, 0), (4, 4, 4))])  # a block over the middle of two halves
    return out


class _Holder:
    """`turned_files` asks for the folder as `p.tmp`."""

    def __init__(self, folder: Path) -> None:
        self.tmp = folder


def masked(x: Any, root: str) -> Any:
    """The floats to 9 significant digits, the folder of the files as «/данные», no times and no sizes."""
    if isinstance(x, dict):
        return {
            masked(k, root): masked(v, root)
            for k, v in x.items()
            if k not in SKIPPED and not (k == "size" and isinstance(v, int | float))
        }
    if isinstance(x, list):
        return [masked(v, root) for v in x]
    if isinstance(x, float):
        return float(f"{x:.9g}") if math.isfinite(x) else x
    if isinstance(x, str):
        return x.replace(root, "/данные")
    return x


def real_files(c: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if not REAL.is_file():
        return out
    with zipfile.ZipFile(REAL) as archive:
        for info in archive.infolist():
            suffix = Path(info.filename).suffix.lower()
            target = Path(c.box.path(f"real{suffix}"))
            with archive.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            out[suffix] = target.name
    return out


def collect(c: Any) -> dict[str, Any]:
    files_of(c)
    grid = {"corner": list(ORIGIN), "rotation": ROT, "size": list(PARENT)}
    hint = {"corner": list(ORIGIN), "size": list(PARENT)}
    seen = {
        "dat": inspect(c, ["model.dat"]),
        "dat_asked": inspect(
            c,
            ["model.dat"],
            {
                "model.dat": {
                    "empty": [-99],
                    "fields": {"AU": {"unit": "%", "role": "grade"}, "ROCK": {"role": "class"}},
                }
            },
            {"model.dat": {"name": "Моя модель"}},
        ),
        "dat_skipped": inspect(c, ["model.dat"], {"model.dat": {"fields": {"ROCK": {"skip": True}}}}),
        "csv": inspect(c, ["model.csv"]),
        "csv_micromine": inspect(c, ["micromine.csv"]),
        "sub_dm": inspect(c, ["sub.dm"]),
        "sub_fbm": inspect(c, ["sub.fbm"]),
        "turned_without_angle": inspect(c, ["turned.csv"]),
        "turned_csv": inspect(c, ["turned.csv"], {"turned.csv": {"grid": grid}}),
        "turned_dat": inspect(c, ["turned.dat"], {"turned.dat": {"grid": grid}}),
        "turned_wrong_angle": inspect(c, ["turned.csv"], {"turned.csv": {"grid": {**grid, "rotation": 35.0}}}),
        "not_detected": inspect(c, ["noroles.csv"]),  # no centre and no size by the names: a table of something
        "no_roles": inspect(c, ["noroles.csv"], {"noroles.csv": {}}),  # the user says: a block model
        "roles_chosen": inspect(c, ["noroles.csv"], {"noroles.csv": {"roles": {"x": "A", "y": "B", "z": "C"}}}),
        "off_grid": inspect(
            c,
            ["off.csv"],
            {
                "off.csv": {
                    "grid": {
                        "corner": [regular().corner[0] + 25.0, regular().corner[1], regular().corner[2]],
                        "size": list(regular().sz),
                    }
                }
            },
        ),
        "over_without_hint": inspect(c, ["over.csv"]),
        "over": inspect(c, ["over.csv"], {"over.csv": {"grid": hint}}),
        "points": inspect(c, ["points.csv"]),
        "triangles_dm": inspect(c, ["tri.dm"]),
        "all": inspect(c, ["model.dat", "sub.dm", "points.csv", "tri.dm", "noroles.csv"]),
        "bad_option": inspect(c, ["model.dat"], {"model.dat": {"centres": "world"}}),
    }
    real = real_files(c)
    for suffix, name in real.items():
        seen[f"real{suffix}"] = inspect(c, [name])
    jobs = {
        "dat": start(
            c,
            ["model.dat"],
            {
                "model.dat": {
                    "blocks": {
                        "empty": [-99],
                        "fields": {"AU": {"unit": "г/т", "role": "grade"}, "ROCK": {"role": "code"}},
                    },
                    "name": "Моя модель",
                }
            },
        ),
        "sub_dm": start(c, ["sub.dm"], {"sub.dm": {"blocks": {"fields": {"AU": {"unit": "г/т"}}}}}),
        "turned": start(c, ["turned.csv"], {"turned.csv": {"blocks": {"grid": grid}}}),
        "dups": start(c, ["dups.csv"]),
        "over": start(c, ["over.csv"], {"over.csv": {"blocks": {"grid": hint}}}),
        "failed_and_points": start(c, ["noroles.csv", "off.csv", "points.csv"]),
    }
    models = {k: answer(c.get(f"{BL}/{v['job']['result']['files'][0]['object_id']}"))
              for k, v in jobs.items() if k in ("dat", "sub_dm", "turned", "dups", "over")}  # fmt: skip
    return {
        "rules": "ответы настоящего ядра через маршруты /import/objects/inspect, /import/objects и GET /blocks/{id} "
        "(test_blocks_import_window_answers.py); номера заданий и времена не хранятся",  # fmt: skip
        "grid": grid,
        "inspect": seen,
        "jobs": jobs,
        "models": models,
        "list": answer(c.get(BL)),
    }


def test_the_answers_the_wizard_of_blocks_is_read_against(ic) -> None:  # type: ignore[no-untyped-def]
    got = json.loads(json.dumps(masked(collect(ic), str(ic.box.folder))))
    seen = got["inspect"]
    for key in ("dat", "csv", "csv_micromine", "sub_dm", "sub_fbm", "turned_csv", "turned_dat"):
        entry = seen[key]["answer"]["body"]["files"][0]
        assert entry["kind"] == "block_model" and entry["blocks"]["error"] is None, key
    assert seen["no_roles"]["answer"]["body"]["files"][0]["blocks"]["error"]["code"] == "blocks_columns"
    assert seen["turned_wrong_angle"]["answer"]["body"]["files"][0]["blocks"]["error"] is not None
    assert seen["points"]["answer"]["body"]["files"][0]["kind"] == "points"
    assert seen["bad_option"]["answer"]["status"] == 422
    for key, job in got["jobs"].items():
        assert job["job"]["status"] == "done", (key, job["job"])
    if REGEN:
        DATA.write_text(json.dumps(got, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    saved = json.loads(DATA.read_text(encoding="utf-8"))
    assert got == saved, "the answers of the core changed: regenerate with BF_REGEN_BLOCKS_IMPORT=1"
