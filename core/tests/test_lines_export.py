"""Export of line and point layers (ADR 0011 §3 `export.lines`): «импорт → экспорт → импорт» gives the same
float64 bit for bit in every format, and the same bytes when the format is the one of the source file (the two
real STR files of the owner included); what a format cannot hold is a warning or a Russian error; a cancelled
job leaves no file; the job runs in a worker and writes the journal and `export_log`."""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import export_log
from bf_core.formats.lineset import LineSet, from_lines, from_points
from bf_core.formats.registry import read_objects
from bf_core.formats.str_layers import gather
from bf_core.jobs.context import JobCancelled, JobContext, JobError
from bf_core.jobs.registry import load_handlers
from bf_core.lines import export as le
from bf_core.lines import store as ls_store
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.test_import_job import API, wait_job

pytestmark = pytest.mark.windows

REAL = Path(__file__).resolve().parents[2] / "tests" / "data" / "real"
REAL_FILES = ("Без имени.STR", "Восточный Цители-Сопели.STR")
E0, N0 = 466570.58600000001, 4584699.697000001
AXIS = "0,           0.000,           0.000,           0.000,           0.000,           0.000,           0.000"
SURPAC = "\r\n".join(
    [
        "pit2026,17-Nov-26,pit design,ssi_styles:styles.ssi",
        AXIS,
        "1, 4585061.988, 466570.123, 100.000, борт, 1",
        "1, 4585061.988, 466580.123, 100.000,",
        "1, 4585071.988, 466580.123, 100.500,",
        "1, 4585071.988, 466570.123, 101.000,",
        "1, 4585061.988, 466570.123, 100.000,",
        "0, 0.000, 0.000, 0.000,",
        "2, 4585000.000, 466000.000, -35.750, разлом",
        "2, 4585001.250, 466002.500, -36.000, разлом",
        "0, 0.000, 0.000, 0.000,",
        "1, 4585100.000, 466100.000, 0.000,",
        "0, 0.000, 0.000, 0.000,",
        "0, 0.000, 0.000, 0.000, END",
        "",
    ]
)


class EveryTick(JobContext):
    """The context of a job that sends every tick (the real one sends five a second), so a test can ask for a
    cancel at a given progress. The cancel itself works as in a worker: the next tick raises."""

    def tick(self, progress: float | None = None, message: str | None = None) -> None:
        if self._cancelled.is_set():
            raise JobCancelled()
        self._send(("progress", self.id, progress, message))


class Project:
    """A project of the test core and what `import.objects` (7.12) does with a file: the file object with its
    `source` blob and a layer object for every layer read."""

    def __init__(self, pclient: Any, name: str = "Экспорт") -> None:
        r = pclient.post(f"{API}/projects", json={"name": name})
        assert r.status_code == 200, r.text
        self.client = pclient
        self.project = projects_of(pclient.core).require_current()
        self.store = BlobStore(self.project.folder)
        self.job = 0

    def load(self, path: Path) -> tuple[int, list[int], list[LineSet]]:
        read = read_objects(path)
        with dialect.write_tx(self.project.engine) as conn:
            file_id = ls_store.create_file(conn, self.store, name=path.name, meta={"format": read.format}, source=path)
            ids = [
                ls_store.put_layer(conn, self.store, layer, file_id=file_id, sort_key=float(k))
                for k, layer in enumerate(read.layers)
            ]
        return file_id, ids, read.layers

    def put(self, layers: list[LineSet], name: str = "слои") -> tuple[int, list[int]]:
        with dialect.write_tx(self.project.engine) as conn:
            file_id = ls_store.create_file(conn, self.store, name=name, meta={})
            return file_id, [ls_store.put_layer(conn, self.store, layer, file_id=file_id) for layer in layers]

    def run(self, cancel_at: float | None = None, **params: Any) -> tuple[dict[str, Any], list[tuple[Any, ...]]]:
        """The handler of `export.lines` as a worker runs it; (result, messages sent to the scheduler)."""
        sent: list[tuple[Any, ...]] = []
        stop = threading.Event()

        def send(message: tuple[Any, ...]) -> None:
            sent.append(message)
            if cancel_at is not None and message[0] == "progress" and (message[2] or 0.0) >= cancel_at:
                stop.set()

        self.job += 1
        job = {"id": self.job, "params": params, "project_folder": str(self.project.folder)}
        ctx = EveryTick(job, send, stop)
        try:
            out = load_handlers()["export.lines"].run(ctx)
        finally:
            ctx.close()
        assert out is not None
        return out, sent

    def log_rows(self) -> list[dict[str, Any]]:
        with dialect.read_tx(self.project.engine) as conn:
            return [dict(r) for r in conn.execute(sa.select(export_log).order_by(export_log.c.id)).mappings()]


@pytest.fixture()
def proj(pclient):  # type: ignore[no-untyped-def]
    return Project(pclient)


@pytest.fixture()
def out_dir(tmp_path: Path) -> Path:
    """Where the files go (tmp_path itself also holds the profile and the projects of the test core)."""
    folder = tmp_path / "выгрузка"
    folder.mkdir()
    return folder


def _lines_of(layers: list[LineSet]) -> list[bytes]:
    """Every line of the layers as the bytes of its float64 vertices, sorted: equal lists — the same lines."""
    out = []
    for layer in layers:
        offsets = np.arange(layer.point_count + 1) if layer.offsets is None else layer.offsets
        out += [layer.xyz[a:b].tobytes() for a, b in zip(offsets[:-1], offsets[1:], strict=True)]
    return sorted(out)


def _logs(sent: list[tuple[Any, ...]], level: str | None = None) -> list[str]:
    return [m[3] for m in sent if m[0] == "log" and (level is None or m[2] == level)]


# --- the same bytes back -------------------------------------------------------------------------------


@pytest.mark.parametrize("name", REAL_FILES)
def test_real_micromine_str_comes_back_byte_for_byte(proj: Project, out_dir: Path, name: str) -> None:
    source = REAL / name
    file_id, ids, layers = proj.load(source)
    out, sent = proj.run(path=str(out_dir / "копия"), format="str", file_id=file_id)
    target = Path(out["path"])
    assert target == out_dir / "копия.str" and target.read_bytes() == source.read_bytes()
    assert out["source_layout"] is True and out["warnings"] == [] and not _logs(sent, "warn")
    assert out["layers"] == len(layers) and out["points"] == sum(layer.point_count for layer in layers)
    assert out["lines"] == sum(layer.line_count for layer in layers) and out["size"] == source.stat().st_size
    back = read_objects(target).layers
    assert [b.name for b in back] == [layer.name for layer in layers]
    for b, layer in zip(back, layers, strict=True):  # float64 bit for bit, the lines and their closing
        assert b.xyz.tobytes() == layer.xyz.tobytes() and b.offsets.tolist() == layer.offsets.tolist()
        assert b.closed.tolist() == layer.closed.tolist() and b.seq.tolist() == layer.seq.tolist()
    # the layers one by one, in any order: the records of the file keep their order
    again, _ = proj.run(path=str(out_dir / "по слоям.STR"), format="str", layer_ids=ids[::-1])
    assert Path(again["path"]).name == "по слоям.STR" and Path(again["path"]).read_bytes() == source.read_bytes()
    assert "оформление исходного файла сохранено" in _logs(sent)[-1] and name not in _logs(sent)[-1]
    row = proj.log_rows()[0]
    assert (row["object_id"], row["file_name"], row["format"]) == (file_id, "копия.str", "str")
    assert row["settings"]["source_layout"] is True and row["counts"]["points"] == out["points"]
    assert not list(out_dir.glob("*.part"))


def test_surpac_str_comes_back_byte_for_byte(proj: Project, out_dir: Path) -> None:
    source = out_dir / "pit.str"
    source.write_bytes(SURPAC.encode("cp1251"))
    file_id, ids, layers = proj.load(source)
    assert [layer.name for layer in layers] == ["Стринг 1", "Стринг 2"]
    out, _sent = proj.run(path=str(out_dir / "out.str"), format="surpac", file_id=file_id)
    assert Path(out["path"]).read_bytes() == source.read_bytes() and out["source_layout"] is True
    # one layer of the two: the same records of that string, the same header and END
    one, _ = proj.run(path=str(out_dir / "one.str"), format="surpac", layer_ids=[ids[1]])
    text = Path(one["path"]).read_bytes().decode("cp1251").split("\r\n")
    assert text[:2] == SURPAC.split("\r\n")[:2] and text[2:4] == SURPAC.split("\r\n")[8:10]
    assert (one["layers"], one["lines"], one["points"]) == (1, 1, 2)


# --- the same float64 through every other format -----------------------------------------------------


@pytest.mark.parametrize("fmt", ["surpac", "dxf", "csv", "dat", "str"])
def test_real_lines_keep_every_double_in_every_format(proj: Project, out_dir: Path, fmt: str) -> None:
    file_id, _ids, layers = proj.load(REAL / REAL_FILES[1])
    out, sent = proj.run(path=str(out_dir / f"conv-{fmt}"), format=fmt, file_id=file_id)
    target = Path(out["path"])
    assert target.suffix == le.FORMATS[fmt].suffixes[0] and out["points"] == 37 and out["lines"] == 5
    back = read_objects(target).layers
    if fmt in ("csv", "dat"):  # a row per vertex, in the order of the source records
        assert len(back) == 1 and back[0].points_only
        assert back[0].xyz.tobytes() == gather(layers).xyz.tobytes()
        assert any("Линии записаны точками" in w for w in out["warnings"]) and _logs(sent, "warn") == out["warnings"]
        assert out["source_layout"] is False
    else:
        assert _lines_of(back) == _lines_of(layers)  # the same lines, float64 bit for bit
        assert sum(int(b.closed.sum()) for b in back) == sum(int(layer.closed.sum()) for layer in layers) == 5
        assert out["source_layout"] is (fmt == "str")
    assert not list(out_dir.glob("*.part"))


def _hard_layers() -> list[LineSet]:
    """Doubles that lose bits in a careless text, a line without heights, a closed line that only nearly
    closes, a single vertex, layers whose names differ only by case."""
    rng = np.random.default_rng(4)
    base = np.array([E0, N0, 412.345_678_9])
    walk = base + np.cumsum(rng.normal(0, 3.0, (400, 3)), axis=0)
    awkward = base + np.array([[0.1 + 0.2, 1 / 3, -0.0], [1e-7, 2.5e-10, 5e-324], [1e7 + 1 / 7, -1e-3, 1e3]])
    no_z = base + rng.normal(0, 50, (5, 3))
    no_z[:, 2] = math.nan
    sq = base + np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0.5], [0, 10, 1], [0, 0, 0]], dtype=float)
    near = sq.copy()
    near[-1] += 4e-7  # closed within the tolerance: the last vertex stays as it is
    a = from_lines("Контур", [walk, awkward, sq, near, base[None, :]], attrs={"КОД": ["a", None, "c", "d", "e"]})
    b = from_lines("контур", [no_z, walk[::-1]], color="#ff8800")
    return [a, b]


@pytest.mark.parametrize("fmt", ["str", "surpac", "dxf"])
def test_hard_lines_cycle_bit_for_bit(proj: Project, out_dir: Path, fmt: str) -> None:
    layers = _hard_layers()
    file_id, _ids = proj.put(layers)
    out, _sent = proj.run(path=str(out_dir / "hard"), format=fmt, file_id=file_id)
    assert out["source_layout"] is False and out["lines"] == 7 and out["points"] == sum(x.point_count for x in layers)
    back = read_objects(Path(out["path"])).layers
    got, want = _lines_of(back), _lines_of(layers)
    if fmt == "dxf":  # DXF has no empty number: an empty Z is written as 0 — with a warning, never silently
        assert any("Z" in w for w in out["warnings"])
        want = _lines_of([layers[0], LineSet("z", np.nan_to_num(layers[1].xyz), layers[1].offsets, layers[1].closed)])
    assert got == want
    assert sum(int(b.closed.sum()) for b in back) == 2
    # once more from what was read: the second file is the first, byte for byte
    file2, _ = proj.put(back)
    again, _ = proj.run(path=str(out_dir / "again"), format=fmt, file_id=file2)
    if fmt != "dxf":  # a DXF carries handles and the time of writing in its header
        assert Path(again["path"]).read_bytes() == Path(out["path"]).read_bytes()
    assert _lines_of(read_objects(Path(again["path"])).layers) == want


@pytest.mark.parametrize(("fmt", "options"), [("csv", {}), ("csv", {"delimiter": "\t", "decimal": ","}), ("dat", {})])
def test_points_cycle_bit_for_bit(proj: Project, out_dir: Path, fmt: str, options: dict[str, str]) -> None:
    rng = np.random.default_rng(11)
    n = 5000
    xyz = np.column_stack([rng.uniform(3e5, 7e5, n), rng.uniform(4e6, 8e6, n), rng.uniform(-500, 4000, n)])
    xyz[0] = [0.1 + 0.2, -0.0, 5e-324]
    xyz[1] = [123456789012345.67, -1.7976931348623157e308, 1e-7]
    xyz[3::7, 2] = math.nan  # no height is not 0
    grade = rng.uniform(0, 30, n)
    grade[::5] = math.nan
    source = out_dir / "точки.csv"
    cells = np.column_stack([xyz, grade]).tolist()
    rows = ["X;Y;Z;AU"] + [";".join("" if math.isnan(v) else repr(v) for v in row) for row in cells]
    source.write_text("\n".join(rows) + "\n", encoding="utf-8")
    file_id, _ids, layers = proj.load(source)
    assert layers[0].points_only and np.array_equal(layers[0].xyz, xyz, equal_nan=True)
    out, _sent = proj.run(path=str(out_dir / "out"), format=fmt, file_id=file_id, **options)
    assert (out["layers"], out["lines"], out["points"], out["warnings"]) == (1, 0, n, [])
    back = read_objects(Path(out["path"])).layers[0]
    assert back.points_only and np.array_equal(np.isnan(back.xyz), np.isnan(xyz))
    assert np.array_equal(np.signbit(back.xyz), np.signbit(xyz)) and np.array_equal(back.xyz, xyz, equal_nan=True)
    assert np.array_equal(back.attrs.column("AU").to_numpy(), grade, equal_nan=True)
    assert proj.log_rows()[-1]["settings"]["delimiter"] == options.get("delimiter")


# --- what a format cannot hold; failures -------------------------------------------------------------


def test_points_do_not_go_into_string_files(proj: Project, out_dir: Path) -> None:
    pts = from_points("Пикеты", np.array([[E0, N0, 1.0], [E0 + 1, N0, 2.0]]), attrs={"text": ["ПК1", None]})
    lines = from_lines("Борт", [np.array([[E0, N0, 0.0], [E0 + 5, N0 + 5, 1.0]])])
    file_id, ids = proj.put([pts, lines])
    for fmt in ("str", "surpac"):
        with pytest.raises(JobError) as err:
            proj.run(path=str(out_dir / "p"), format=fmt, layer_ids=[ids[0]])
        assert err.value.code == "no_lines" and "CSV" in err.value.hint and "нет линий" in err.value.message
        out, sent = proj.run(path=str(out_dir / f"mixed-{fmt}"), format=fmt, file_id=file_id)
        assert (out["layers"], out["lines"], out["points"]) == (1, 1, 2)
        assert "«Пикеты»" in out["warnings"][0] and _logs(sent, "warn")[0] == out["warnings"][0]
    dxf, _ = proj.run(path=str(out_dir / "all.dxf"), format="dxf", file_id=file_id)
    back = read_objects(Path(dxf["path"])).layers
    assert sorted((b.name, b.points_only, b.point_count) for b in back) == [("Борт", False, 2), ("Пикеты", True, 2)]
    assert not list(out_dir.glob("p.*")) and not list(out_dir.glob("*.part"))
    assert len(proj.log_rows()) == 3  # the failed jobs wrote nothing


def test_errors_are_russian_and_leave_no_file(proj: Project, out_dir: Path) -> None:
    file_id, ids = proj.put(_hard_layers())
    (out_dir / "папка.dxf").mkdir()
    cases: list[tuple[dict[str, Any], str]] = [
        ({"path": str(out_dir / "папка"), "format": "dxf", "file_id": file_id}, "file_unwritable"),
        ({"path": str(out_dir / "x"), "format": "shp", "file_id": file_id}, "bad_format"),
        ({"path": str(out_dir / "x"), "format": "str", "layer_ids": [999_999]}, "line_layer_not_found"),
        ({"path": str(out_dir / "x"), "format": "csv", "file_id": file_id, "delimiter": "#"}, "unknown_delimiter"),
        ({"path": str(out_dir / "x"), "format": "csv", "file_id": file_id, "encoding": "ascii"}, "unrepresentable"),
        ({"path": str(out_dir / "x"), "format": "csv", "layer_ids": []}, "no_lines"),
    ]
    for params, code in cases:
        with pytest.raises(JobError) as err:
            proj.run(**params)
        e = err.value
        assert e.code == code and e.message and e.hint and any("а" <= ch <= "я" for ch in e.message), params
    assert [p.name for p in out_dir.iterdir()] == ["папка.dxf"] and proj.log_rows() == []


def test_cancel_leaves_no_file(proj: Project, out_dir: Path) -> None:
    rng = np.random.default_rng(1)
    big = from_lines("Много", [np.array([E0, N0, 0.0]) + np.cumsum(rng.normal(0, 1, (400, 3)), axis=0)] * 600)
    file_id, _ids = proj.put([big])
    for fmt in ("str", "surpac", "dxf", "csv", "dat"):
        with pytest.raises(JobCancelled):
            proj.run(cancel_at=0.35, path=str(out_dir / "big"), format=fmt, file_id=file_id)
        assert list(out_dir.iterdir()) == [], fmt
    assert proj.log_rows() == []
    # a cancel that comes when the file is already in place does not undo the export
    late, _ = proj.run(cancel_at=1.0, path=str(out_dir / "поздно"), format="surpac", file_id=file_id)
    assert Path(late["path"]).is_file() and len(proj.log_rows()) == 1
    Path(late["path"]).unlink()
    out, sent = proj.run(path=str(out_dir / "big"), format="str", file_id=file_id)
    seen = [m[2] for m in sent if m[0] == "progress" and m[2] is not None]
    assert out["points"] == 240_000 and seen == sorted(seen) and seen[0] == 0.0 and seen[-1] == 1.0


def test_request_checks_the_parameters(proj: Project, out_dir: Path) -> None:
    file_id, ids = proj.put(_hard_layers())
    path = str(out_dir / "out.str")
    with dialect.read_tx(proj.project.engine) as conn:
        ok = le.request(conn, path=path, format="csv", file_id=file_id, delimiter=";", decimal=None)
        assert ok == {
            "path": path,
            "format": "csv",
            "layer_ids": ids,
            "file_id": file_id,
            "points": 819,
            "delimiter": ";",
        }
        assert le.request(conn, path=path, format="str", layer_ids=[ids[1], ids[1]])["layer_ids"] == [ids[1]]
        assert le._memory(ok) == 512 and le._memory({"points": 10_000_000}) > 4000
        for kw, status, code in (
            ({"path": path, "format": "shp", "file_id": file_id}, 422, "bad_format"),
            ({"path": "out.str", "format": "str", "file_id": file_id}, 422, "path_not_absolute"),
            ({"path": str(out_dir / "нет" / "o.str"), "format": "str", "file_id": file_id}, 404, "folder_not_found"),
            ({"path": path, "format": "str", "file_id": 999_999}, 404, "line_layer_not_found"),
            ({"path": path, "format": "str", "layer_ids": [ids[0], file_id]}, 404, "line_layer_not_found"),
            ({"path": path, "format": "str"}, 404, "line_layer_not_found"),
            ({"path": path, "format": "str", "layer_ids": list(range(1, 2002))}, 422, "too_many_layers"),
        ):
            with pytest.raises(ApiError) as err:
                le.request(conn, **kw)  # type: ignore[arg-type]
            assert (err.value.status, err.value.code) == (status, code) and err.value.hint, kw


# --- the job in a worker -----------------------------------------------------------------------------


def test_job_runs_in_a_worker_with_journal_and_log(proj: Project, out_dir: Path) -> None:
    from bf_core.jobs.service import jobs_of

    source = REAL / REAL_FILES[0]
    file_id, _ids, _layers = proj.load(source)
    c = proj.client
    with dialect.read_tx(proj.project.engine) as conn:
        params = le.request(conn, path=str(out_dir / "из задания.str"), format="str", file_id=file_id)
    key, job_id = jobs_of(c.core).submit("export.lines", params, title="Экспорт линий (STR)")
    job = wait_job(c, key, job_id)
    assert job["status"] == "done", job
    assert Path(job["result"]["path"]).read_bytes() == source.read_bytes() and job["result"]["source_layout"] is True
    log = c.get(f"{API}/jobs/{key}/{job_id}/log").json()
    assert any("Записан файл «из задания.str»" in row["text"] for row in log["lines"]), log
    rows = proj.log_rows()
    assert len(rows) == 1 and rows[0]["job_id"] == job_id and rows[0]["object_id"] == file_id
    bad = dict(params, format="surpac", layer_ids=[], file_id=None)
    key, job_id = jobs_of(c.core).submit("export.lines", bad)
    failed = wait_job(c, key, job_id)
    assert failed["status"] == "failed" and failed["error"]["code"] == "no_lines" and failed["error"]["hint"]
