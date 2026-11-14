"""Benchmarks with budgets (docs/ROADMAP.md, 2.2).

    python -m bench.run --profile ci --check          small sizes, CI gate
    python -m bench.run --profile full --check        target sizes (10 million triangles)
    python -m bench.run --scenario import_tridb --size 2000000

Every scenario runs in its own process, so its peak memory (max RSS) is its own.
`--check` fails (exit 1) when a time or memory budget of bench/budgets.json is exceeded;
with `--baseline FILE` it also reports scenarios more than 20 % slower than that earlier run.
Database scenarios use BENCH_DATABASE_URL or TEST_DATABASE_URL (a migrated database).
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SLOWDOWN = 1.2

PROFILES = {
    "ci": {"mesh_stats": 1_000_000, "store_mesh": 1_000_000, "import_tridb": 1_000_000,
           "serve_blob": 1_000_000, "upload_chunks": 256},
    "full": {"mesh_stats": 10_000_000, "store_mesh": 10_000_000, "import_tridb": 10_000_000,
             "serve_blob": 10_000_000, "upload_chunks": 2048},
}
UNITS = {"upload_chunks": "МБ"}


def _db_url() -> str:
    return os.environ.get("BENCH_DATABASE_URL") or os.environ.get(
        "TEST_DATABASE_URL", "postgresql+psycopg://geo:geo@localhost:5432/geo_test")


def _cache() -> Path:
    d = Path(os.environ.get("BENCH_STORE", tempfile.gettempdir())) / "bench-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _prepare(size: int) -> None:
    """Generates the input data once, in a separate process, so its memory is not measured."""
    import numpy as np

    from bench.synthetic import cube_n_for, subdivided_cube, write_tridb

    d = _cache()
    if (d / f"cube-{size}.tridb").exists():
        return
    v, t = subdivided_cube(cube_n_for(size))
    np.save(d / f"cube-{size}-v.npy", v)
    np.save(d / f"cube-{size}-t.npy", t)
    write_tridb(d / f"cube-{size}.tridb", [("куб", v, t)])


def _cube(size: int):
    import numpy as np

    d = _cache()
    return np.load(d / f"cube-{size}-v.npy"), np.load(d / f"cube-{size}-t.npy")


def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    return Session(create_engine(_db_url()), expire_on_commit=False)


def _project(session, name: str) -> int:
    from sqlalchemy import text

    session.execute(text("DELETE FROM project WHERE name = :n"), {"n": name})
    pid = session.execute(text("INSERT INTO project (name) VALUES (:n) RETURNING id"), {"n": name}).scalar()
    session.commit()
    return pid


def _drop_project(session, pid: int) -> None:
    from datetime import timedelta

    from sqlalchemy import text

    from app.storage import gc

    session.execute(text("DELETE FROM project WHERE id = :i"), {"i": pid})
    session.commit()
    gc.collect(session, grace=timedelta(0))


# ---- scenarios: setup outside the timer, the measured part inside `timed`


def scenario_mesh_stats(size: int, timed) -> dict:
    from app.formats.wireframes import mesh_stats

    v, t = _cube(size)
    with timed():
        stats = mesh_stats(v, t)
    assert stats["closed"] and abs(stats["volume"] - 1e9) < 1e-3, stats
    return {"triangles": len(t)}


def scenario_store_mesh(size: int, timed) -> dict:
    from app.storage import geometry

    v, t = _cube(size)
    with _session() as s:
        with timed():
            geometry.store_mesh(s, v, t)
            s.commit()
    return {"triangles": len(t), "mb": round((v.nbytes * 1.5 + t.nbytes) / 1e6, 1)}


def scenario_import_tridb(size: int, timed) -> dict:
    import shutil

    from app.importing.objects import import_wireframes
    from app.storage import blobs

    path = blobs.tmp_path(".tridb")
    shutil.copyfile(_cache() / f"cube-{size}.tridb", path)
    mb = path.stat().st_size / 1e6
    with _session() as s:
        pid = _project(s, "bench import")
        with timed():
            d = import_wireframes(s, pid, path, "bench.tridb")
            s.commit()
        w = s.execute(__import__("sqlalchemy").text("SELECT stats FROM wireframe WHERE dataset_id = :d"), {"d": d.id}).scalar()
        _drop_project(s, pid)
    assert w["closed"] and abs(w["volume"] - 1e9) < 1e-3, w
    return {"triangles": int(12 * __import__("bench.synthetic", fromlist=["x"]).cube_n_for(size) ** 2), "file_mb": round(mb, 1)}


def scenario_serve_blob(size: int, timed) -> dict:
    from fastapi.testclient import TestClient

    from app.main import app
    from app.storage import geometry

    v, t = _cube(size)
    with _session() as s:
        stored = geometry.store_mesh(s, v, t)
        s.commit()
    client = TestClient(app)
    with timed():
        n = 0
        for role in ("render_vertices", "triangles"):
            with client.stream("GET", f"/api/blobs/{stored.blobs[role]}") as r:
                for chunk in r.iter_bytes():
                    n += len(chunk)
    return {"mb": round(n / 1e6, 1)}


def scenario_upload_chunks(size_mb: int, timed) -> dict:
    import hashlib

    from fastapi.testclient import TestClient

    from app.config import settings
    from app.db import get_session
    from app.main import app

    def _override():
        with _session() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    client = TestClient(app)
    with _session() as s:
        pid = _project(s, "bench upload")
    chunk = os.urandom(settings.upload_chunk_bytes)
    total = size_mb * 1024 * 1024
    uid = client.post(f"/api/projects/{pid}/uploads", json={"filename": "big.bin", "size": total}).json()["id"]
    with timed():
        offset = 0
        while offset < total:
            part = chunk[: min(len(chunk), total - offset)]
            r = client.put(f"/api/uploads/{uid}?offset={offset}", content=part,
                           headers={"X-Chunk-SHA256": hashlib.sha256(part).hexdigest()})
            assert r.status_code == 200, r.text
            offset = r.json()["received"]
    client.delete(f"/api/uploads/{uid}")
    with _session() as s:
        _drop_project(s, pid)
    return {"mb": size_mb}


SCENARIOS = {name[len("scenario_"):]: fn for name, fn in globals().items() if name.startswith("scenario_")}


def _child(name: str, size: int) -> None:
    """Runs one scenario in this process and prints its measurements as JSON."""
    from app.storage import blobs

    store = os.environ.get("BENCH_STORE")
    if store:
        blobs.set_root(Path(store))
    elapsed: dict = {}

    class _Timer:
        def __enter__(self):
            self.t = time.perf_counter()

        def __exit__(self, *exc):
            elapsed["s"] = time.perf_counter() - self.t

    extra = SCENARIOS[name](size, _Timer)
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux: KiB
    print(json.dumps({"scenario": name, "size": size, "seconds": round(elapsed["s"], 3),
                      "peak_mb": round(peak_mb, 1), **extra}))


def run(name: str, size: int, store: Path) -> dict:
    env = {**os.environ, "BENCH_STORE": str(store), "JOBS_IN_API": "0"}
    if name != "upload_chunks":
        prep = subprocess.run([sys.executable, "-m", "bench.run", "--prepare", "--size", str(size)],
                              cwd=HERE.parent, env=env, capture_output=True, text=True)
        if prep.returncode:
            return {"scenario": name, "size": size, "error": prep.stderr[-2000:]}
    out = subprocess.run([sys.executable, "-m", "bench.run", "--child", name, "--size", str(size)],
                         cwd=HERE.parent, env=env, capture_output=True, text=True)
    if out.returncode:
        return {"scenario": name, "size": size, "error": (out.stderr or out.stdout)[-2000:]}
    return json.loads(out.stdout.strip().splitlines()[-1])


def check(results: list[dict], profile: str, baseline: dict | None) -> list[str]:
    budgets = json.loads((HERE / "budgets.json").read_text())[profile]
    if baseline:
        baseline = {k: v for k, v in baseline.items() if isinstance(v, dict) and "seconds" in v}
    problems = []
    for r in results:
        name = r["scenario"]
        if "error" in r:
            problems.append(f"{name}: ошибка\n{r['error']}")
            continue
        b = budgets.get(name, {})
        if "seconds" in b and r["seconds"] > b["seconds"]:
            problems.append(f"{name}: {r['seconds']} с > бюджета {b['seconds']} с")
        if "peak_mb" in b and r["peak_mb"] > b["peak_mb"]:
            problems.append(f"{name}: {r['peak_mb']} МБ памяти > бюджета {b['peak_mb']} МБ")
        old = (baseline or {}).get(name)
        if old and old.get("size") == r["size"] and r["seconds"] > old["seconds"] * SLOWDOWN:
            problems.append(f"{name}: {r['seconds']} с, медленнее прошлого замера ({old['seconds']} с) больше чем на 20 %")
    return problems


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", choices=PROFILES, default="ci")
    p.add_argument("--scenario", choices=SCENARIOS, action="append")
    p.add_argument("--size", type=int)
    p.add_argument("--check", action="store_true")
    p.add_argument("--baseline", type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--repeat", type=int, default=1, help="runs per scenario; the fastest counts (less noise)")
    p.add_argument("--child", choices=SCENARIOS, help=argparse.SUPPRESS)
    p.add_argument("--prepare", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args()
    if args.prepare:
        _prepare(args.size)
        return
    if args.child:
        _child(args.child, args.size)
        return

    names = args.scenario or list(PROFILES[args.profile])
    results = []
    with tempfile.TemporaryDirectory(prefix="bench-store-") as store:
        for name in names:
            size = args.size or PROFILES[args.profile][name]
            runs = [run(name, size, Path(store)) for _ in range(max(args.repeat, 1))]
            ok = [x for x in runs if "error" not in x]
            r = min(ok, key=lambda x: x["seconds"]) if ok else runs[0]
            results.append(r)
            if "error" in r:
                print(f"{name:>14} {size:>11,}  ОШИБКА", file=sys.stderr)
            else:
                unit = UNITS.get(name, "треуг.")
                print(f"{name:>14} {size:>11,} {unit:<6} {r['seconds']:>8.2f} с {r['peak_mb']:>8.0f} МБ")
    if args.out:
        args.out.write_text(json.dumps({r["scenario"]: r for r in results}, ensure_ascii=False, indent=2))
    if args.check:
        baseline = json.loads(args.baseline.read_text()) if args.baseline and args.baseline.exists() else None
        problems = check(results, args.profile, baseline)
        for line in problems:
            print("НЕ ПРОШЛО:", line, file=sys.stderr)
        sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
