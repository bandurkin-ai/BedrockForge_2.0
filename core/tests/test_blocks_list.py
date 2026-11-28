"""A block model as a LIST of blocks — the model that lies in no grid (ADR 0030 decisions 8–14, подшаги 27.4в–г;
замечание заказчика А6): the file the grid refuses (`blocks_unaligned`, with the choice «Импортировать списком
блоков»), the import with `blocks.layout = "list"` (meta v3, the columns `cx … dz`), its checks (duplicates, overlaps
of the boxes), the operations that read boxes (report, filters, calculator, density, categories, export,
regularization),
the operations that refuse it (422 `block_list_unsupported`: «Регуляризуйте модель») and the display (the faces, the
block under a point).

The reference is the blocks the file was built from (a partition of a volume into boxes of random size), not the code
under test: the volume of the model is the volume of the partition."""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import export as bx
from bf_core.blocks import list_layout as ll
from bf_core.blocks import meta as bmeta
from bf_core.blocks import regularize as rg
from bf_core.blocks import report as rp
from bf_core.blocks import store as bm
from bf_core.blocks.model import ANY, Model
from bf_core.blocks.schema import GridV2
from bf_core.db import dialect
from bf_core.db.schema import objects
from tests.test_import_blocks import project as base_project  # noqa: F401
from tests.test_import_blocks import run

ORIGIN = (1000.5, 2000.25, 100.0)
PARENT = 10.0
LABELS = ["руда", "порода", "дайка"]


@pytest.fixture()
def project(base_project):  # type: ignore[no-untyped-def]  # noqa: F811
    base_project.uid = "list-project"
    return base_project


def partition(seed: int = 5, parents: int = 20, rotation: float = 0.0) -> SimpleNamespace:
    """A volume of `parents` × `parents` parents 10 × 10 × 10 cut into boxes: every parent in X into 1–5 equal parts, in
    Y into 1–3, every column into 2–7 pieces of random height. No division is common: the grid refuses it. The centres
    and the sizes are given in the local axes `q`, `centre` — the project axes of a file (turned by `rotation`)."""
    rng = np.random.default_rng(seed)
    q, d = [], []
    for i in range(parents):
        for j in range(parents):
            nx, ny = int(rng.integers(1, 6)), int(rng.integers(1, 4))
            for a in range(nx):
                for b in range(ny):
                    m = int(rng.integers(2, 8))
                    cuts = np.concatenate([[0.0], np.sort(rng.uniform(0.2, PARENT - 0.2, m - 1)), [PARENT]])
                    for z0, z1 in zip(cuts[:-1], cuts[1:], strict=True):
                        x0, y0 = i * PARENT + a * PARENT / nx, j * PARENT + b * PARENT / ny
                        q.append((x0 + PARENT / nx / 2, y0 + PARENT / ny / 2, (z0 + z1) / 2))
                        d.append((PARENT / nx, PARENT / ny, z1 - z0))
    q, d = np.array(q), np.array(d)
    order = rng.permutation(len(q))  # the file is in no order
    q, d = q[order], d[order]
    grid = GridV2.of({"corner": list(ORIGIN), "counts": [1, 1, 1], "size": [1.0, 1.0, 1.0], "rotation": rotation})
    centre = grid.to_project(q)
    au = np.round(rng.lognormal(0.0, 1.0, len(q)), 4)
    au[::11] = np.nan
    dom = rng.integers(0, 3, len(q))
    sg = np.round(rng.uniform(2.4, 3.1, len(q)), 3)
    volume = float(parents * PARENT) ** 2 * PARENT
    return SimpleNamespace(q=q, d=d, centre=centre, au=au, dom=dom, sg=sg, volume=volume, rotation=rotation,
                           n=len(q), parents=parents)  # fmt: skip


def write_csv(path: Path, m: SimpleNamespace, *, extra: list[tuple[Any, ...]] = ()) -> Path:  # type: ignore[assignment]
    lines = ["X,Y,Z,DX,DY,DZ,AU,SG,DOM"]
    for r in range(m.n):
        row = [*m.centre[r], *m.d[r], "" if math.isnan(m.au[r]) else m.au[r], m.sg[r], LABELS[m.dom[r]]]
        lines.append(",".join(repr(float(v)) if not isinstance(v, str) else v for v in row))
    for row in extra:
        lines.append(",".join(repr(float(v)) if not isinstance(v, str) else v for v in row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def opened(p: Any, model_id: int, layout: Any = ANY) -> Model:
    with dialect.read_tx(p.engine) as conn:
        return Model.open(conn, p.store, model_id, layout)


def import_list(p: Any, m: SimpleNamespace, name: str = "Список", **extra: Any) -> Any:
    path = write_csv(p.tmp / f"{name}.csv", m)
    blocks: dict[str, Any] = {"layout": "list", **extra}
    if m.rotation:
        blocks["grid"] = {"rotation": m.rotation, "corner": list(ORIGIN), **blocks.get("grid", {})}
    return run(p, path, blocks, name=name)


def by_centre(centre: np.ndarray) -> np.ndarray:
    return np.lexsort((centre[:, 2], centre[:, 1], centre[:, 0]))


# --- the refusal and the choice --------------------------------------------------------------------------------


def test_the_grid_refuses_the_model_and_offers_the_list(project) -> None:  # type: ignore[no-untyped-def]
    m = partition()
    path = write_csv(project.tmp / "off.csv", m)
    with pytest.raises(ApiError) as exc:
        run(project, path)
    e = exc.value
    assert e.code == "blocks_unaligned"
    ids = [c["id"] for c in e.extra["choices"]]
    assert ids == ["grid", "list", "cancel"]  # the list between «Задать сетку вручную» and «Не импортировать»
    pick = e.extra["choices"][1]
    assert pick["label"] == "Импортировать списком блоков" and "оценка и карьер" in pick["note"]
    from bf_core.importing import object_blockfile as obf

    info = obf.inspect_blockfile(path)
    assert info["error"]["code"] == "blocks_unaligned" and [c["id"] for c in info["error"]["choices"]] == ids
    json.dumps(info["error"])


def test_the_list_is_the_users_choice_and_the_inspection_says_what_it_holds(project) -> None:  # type: ignore[no-untyped-def]
    from bf_core.importing import object_blockfile as obf

    m = partition()
    path = write_csv(project.tmp / "ins.csv", m)
    info = obf.inspect_blockfile(path, {"blocks": {"layout": "list"}})
    assert info["error"] is None and info["layout"] == "list" and info["sub"] is False
    box = info["list"]["extent"]
    assert info["list"]["rows"] == m.n and info["list"]["parents"] >= 1 and len(box) == 6
    assert box[0] == pytest.approx(ORIGIN[0], abs=1e-6) and box[3] == pytest.approx(ORIGIN[0] + 200.0, abs=1e-6)
    assert box[2] == pytest.approx(ORIGIN[2], abs=1e-6) and box[5] == pytest.approx(ORIGIN[2] + 10.0, abs=1e-6)
    assert info["check3"]["ok"] is True
    with pytest.raises(ApiError) as exc:  # a layout that is not one
        obf.BlockOptions.of({"layout": "tree"})
    assert exc.value.code == "bad_param" and exc.value.extra["param"] == "blocks.layout"
    nosize = project.tmp / "nosize.csv"
    nosize.write_text("X,Y,Z,AU\n1,2,3,4\n5,6,7,8\n", encoding="utf-8")
    bad = obf.inspect_blockfile(nosize, {"blocks": {"layout": "list"}})
    assert bad["error"]["code"] == "block_list_sizes" and "размер" in bad["error"]["message"]


# --- the import ----------------------------------------------------------------------------------------------


def test_the_list_imports_every_block_with_its_centre_size_and_fields(project) -> None:  # type: ignore[no-untyped-def]
    m = partition()
    res = import_list(project, m)
    assert res.rows == m.n and res.sub is False and res.parents <= res.rows
    with dialect.read_tx(project.engine) as conn:
        row = conn.execute(objects.select().where(objects.c.id == res.model_id)).first()
        info = bm.block_model_info(conn, res.model_id)
    meta = row.meta
    assert meta["v"] == 3 and meta["layout"] == "list" and meta["sub"] is False and meta["full"] is False
    assert meta["rows"] == m.n and meta["parents"] == res.parents and meta["grid"]["div"] == [1, 1, 1]
    assert info["layout"] == "list" and info["meta_v"] == 3 and info["sub"] is False
    assert bmeta.public_meta(meta, brief=True)["layout"] == "list"
    mod = opened(project, res.model_id)
    assert mod.listed and mod.layout == "list" and mod.rows == m.n
    lc, ld = mod.geo()
    centre = mod.centres()
    sel, got = by_centre(m.centre), by_centre(centre)
    assert np.abs(centre[got] - m.centre[sel]).max() <= 1e-9
    assert np.abs(ld[got] - m.d[sel]).max() == 0.0  # the sizes are the file's, bit for bit
    au = mod.columns(["AU"])["AU"]
    assert np.array_equal(au[got], m.au[sel], equal_nan=True)  # a gap stays a gap, not a zero
    dom = mod.columns(["DOM"])["DOM"]
    names = np.array(mod.labels("DOM"), dtype=object)
    assert np.array_equal(names[dom[got]], np.array(LABELS, dtype=object)[m.dom[sel]])
    assert mod.volume().sum() == pytest.approx(m.volume, rel=1e-12)
    # the order of the file: (idx, cz, cy, cx), the cells of the enclosing grid that hold the centres
    idx = mod.p()
    assert (np.diff(idx.astype(np.int64)) >= 0).all()
    assert np.array_equal(idx, ll.cells_of(mod.grid, lc))
    tie = np.flatnonzero(idx[1:] == idx[:-1])
    assert (lc[tie + 1, 2] > lc[tie, 2]).all() or (lc[tie + 1, 2] == lc[tie, 2]).any()
    assert (ld.max(axis=0) <= np.asarray(mod.grid.regular()) + 1e-12).all()  # the cell is not smaller than any block
    assert mod.subs() is None and mod.sub is False and mod.full is False


def test_a_list_is_opened_only_by_the_operations_that_ask_for_it(project) -> None:  # type: ignore[no-untyped-def]
    res = import_list(project, partition(parents=4))
    with pytest.raises(ApiError) as exc:
        opened(project, res.model_id, ("grid",))
    e = exc.value
    assert e.status == 422 and e.code == "block_list_unsupported" and "Регуляризуйте" in e.hint
    assert "список" in e.message.casefold()
    with dialect.read_tx(project.engine) as conn:
        with pytest.raises(ApiError) as exc2:
            Model.open(conn, project.store, res.model_id, what="Срез")
        assert "«Срез»" in exc2.value.message
    assert opened(project, res.model_id).listed


def test_a_rotated_list_gives_the_centres_of_the_file(project) -> None:  # type: ignore[no-untyped-def]
    m = partition(seed=2, parents=6, rotation=30.0)
    res = import_list(project, m, "Повёрнутый")
    mod = opened(project, res.model_id)
    assert mod.grid.rotation == 30.0
    got, sel = by_centre(mod.centres()), by_centre(m.centre)
    assert np.abs(mod.centres()[got] - m.centre[sel]).max() <= 1e-9
    assert mod.volume().sum() == pytest.approx(m.volume if False else float(6 * PARENT) ** 2 * PARENT, rel=1e-12)


def test_the_centres_in_the_axes_of_the_model_are_read_as_they_are(project) -> None:  # type: ignore[no-untyped-def]
    m = partition(seed=3, parents=3)
    path = write_csv(project.tmp / "own.csv", SimpleNamespace(**{**m.__dict__, "centre": m.q + np.asarray(ORIGIN)}))
    res = run(project, path, {"layout": "list", "centres": "model"}, name="Свои оси")
    mod = opened(project, res.model_id)
    assert np.abs(np.sort(mod.centres(), axis=0) - np.sort(m.q + np.asarray(ORIGIN), axis=0)).max() <= 1e-9


def test_a_big_parent_given_by_the_user_is_the_enclosing_cell(project) -> None:  # type: ignore[no-untyped-def]
    m = partition(parents=4)
    res = import_list(project, m, "Ячейка", grid={"size": [20.0, 20.0, 20.0]})
    mod = opened(project, res.model_id)
    assert mod.grid.regular() == (20.0, 20.0, 20.0) and mod.grid.counts == (2, 2, 1)
    with pytest.raises(ApiError) as exc:
        import_list(project, m, "Мала", grid={"size": [1.0, 1.0, 1.0]})
    assert exc.value.code == "bad_param" and exc.value.extra["param"] == "grid.size"


# --- duplicates, overlaps, gaps -----------------------------------------------------------------------------------


def test_duplicates_are_left_out_and_overlaps_stay_as_findings(project) -> None:  # type: ignore[no-untyped-def]
    m = partition(parents=3)
    again = (*m.centre[7], *m.d[7], 1.0, 2.7, "руда")  # the same centre and size: a repeated row
    inside = (m.centre[20][0] + 0.1, m.centre[20][1], m.centre[20][2], *m.d[20], 1.0, 2.7, "руда")  # a shifted box
    path = write_csv(project.tmp / "bad.csv", m, extra=[again, inside])
    res = run(project, path, {"layout": "list"}, name="Дубли")
    assert res.rows == m.n + 1  # the repeated row is gone, the shifted one stays
    checks = res.checks
    assert checks["duplicates"] == 1 and checks["overlaps"]["method"] == "boxes"
    assert checks["overlaps"]["n"] >= 2  # the shifted box, the one it was shifted from and maybe a neighbour
    kinds = {e["check"] for e in checks["examples"]}
    assert kinds == {"duplicate", "overlap"}
    pairs = [{e["row"], e["other_row"]} for e in checks["examples"] if e["check"] == "overlap"]
    assert {20, m.n + 1} in pairs  # rows of the file, from 0
    dup = next(e for e in checks["examples"] if e["check"] == "duplicate")
    assert dup["row"] == m.n and dup["other_row"] == 7  # the first copy stays
    assert any("Перекрывающиеся блоки" in w for w in res.warnings) and any("Повторяющиеся" in w for w in res.warnings)
    assert checks["volume"] == pytest.approx(m.volume + float(np.prod(m.d[20])), rel=1e-12)


def test_blocks_that_only_touch_are_not_overlaps(project) -> None:  # type: ignore[no-untyped-def]
    m = partition(parents=5, seed=9)
    res = import_list(project, m)
    assert res.checks["overlaps"]["n"] == 0 and res.checks["duplicates"] == 0
    rep = ll.check_cells(project.store, opened(project, res.model_id).shas(), opened(project, res.model_id).meta)
    assert rep.checks["overlaps"] == {"n": 0, "method": "boxes"} and rep.checks["volume"] == pytest.approx(
        float(5 * PARENT) ** 2 * PARENT, rel=1e-12)  # fmt: skip


def test_rows_without_a_centre_or_a_size_are_not_blocks(project) -> None:  # type: ignore[no-untyped-def]
    m = partition(parents=2)
    extra = [(math.nan, 1.0, 2.0, 1.0, 1.0, 1.0, 1.0, 2.7, "руда"), (5.0, 5.0, 5.0, 0.0, 1.0, 1.0, 1.0, 2.7, "руда")]
    res = run(project, write_csv(project.tmp / "gaps.csv", m, extra=extra), {"layout": "list"}, name="Пропуски")
    assert res.rows == m.n and any("без центра или с размером" in w for w in res.warnings)


# --- a cancel ------------------------------------------------------------------------------------------------------


def test_a_cancel_leaves_nothing(project) -> None:  # type: ignore[no-untyped-def]
    from bf_core.jobs.context import JobCancelled

    m = partition(parents=4)
    path = write_csv(project.tmp / "cancel.csv", m)
    seen = []

    def tick(f: float, msg: Any) -> None:
        seen.append(msg)
        if msg == "Запись":
            raise JobCancelled()

    with pytest.raises(JobCancelled):
        run(project, path, {"layout": "list"}, tick=tick)
    with dialect.read_tx(project.engine) as conn:
        assert conn.execute(objects.select().where(objects.c.kind == "block_model")).first() is None
    assert not list(project.store.tmp_dir.glob("*")) if project.store.tmp_dir.exists() else True


# --- the report, the export -----------------------------------------------------------------------------------------


def report_total(p: Any, model_id: int, **body: Any) -> dict[str, Any]:
    out = rp.run_report(p.engine, p.folder, {"model_id": model_id, "grades": ["AU"], "density": 2.5, "cutoffs": [0.0],
                                            "curve": 0, **body})  # fmt: skip
    return out


def test_the_report_counts_the_volume_of_the_boxes(project) -> None:  # type: ignore[no-untyped-def]
    m = partition()
    res = import_list(project, m)
    out = report_total(project, res.model_id)
    total = out["total"] if "total" in out else out["rows"][0]
    ok = ~np.isnan(m.au)
    vol = np.prod(m.d, axis=1)
    assert total["volume"] == pytest.approx(float(vol[ok].sum()), rel=1e-12)
    assert total["tonnes"] == pytest.approx(float(vol[ok].sum()) * 2.5, rel=1e-12)
    # a restrictor: the blocks of one category (the list is filtered by the fields, as any model)
    only = report_total(project, res.model_id, categories={"field": "DOM", "values": ["руда"]})
    one = only["total"] if "total" in only else only["rows"][0]
    pick = ok & (m.dom == 0)
    assert one["volume"] == pytest.approx(float(vol[pick].sum()), rel=1e-12)


def export(p: Any, model_id: int, fmt: str, **body: Any) -> Path:
    folder = p.tmp / "out"
    folder.mkdir(exist_ok=True)
    path = folder / f"m{model_id}.{fmt}"
    bx.run_export(p.engine, p.folder, {"model_id": model_id, "format": fmt, "path": str(path), **body}, chunk=1000)
    return path


def test_dat_csv_and_parquet_of_a_list_come_back(project) -> None:  # type: ignore[no-untyped-def]
    m = partition(seed=7, parents=6)
    res = import_list(project, m)
    mod = opened(project, res.model_id)
    path = export(project, res.model_id, "dat")
    again = run(project, path, {"layout": "list"}, name="Круг DAT")
    back = opened(project, again.model_id)
    assert again.rows == m.n and back.listed
    a, b = by_centre(mod.centres()), by_centre(back.centres())
    assert np.abs(back.centres()[b] - mod.centres()[a]).max() <= 1e-9
    assert np.abs(back.geo()[1][b] - mod.geo()[1][a]).max() <= 1e-12
    assert np.array_equal(back.columns(["AU"])["AU"][b], mod.columns(["AU"])["AU"][a], equal_nan=True)
    assert again.checks["overlaps"]["n"] == 0 and again.checks["duplicates"] == 0
    assert back.volume().sum() == pytest.approx(mod.volume().sum(), rel=1e-12)
    csv = export(project, res.model_id, "csv", delimiter=";", decimal=".")
    text = csv.read_text(encoding="utf-8-sig").splitlines()
    assert len(text) == m.n + 1 and text[0].split(";")[:6] == ["X", "Y", "Z", "DX", "DY", "DZ"]
    mic = str(project.tmp / "out" / "mic.csv")
    done = bx.run_export(project.engine, project.folder,
                         {"model_id": res.model_id, "format": "csv", "header": "micromine", "path": mic})
    assert any("Строка сетки не записана" in w for w in done["warnings"])  # the grid line of a list cannot be told
    import pyarrow.parquet as pq

    table = pq.read_table(export(project, res.model_id, "parquet"))
    assert table.num_rows == m.n and {"X", "Y", "Z", "DX", "DY", "DZ", "AU"} <= set(table.schema.names)
    with pytest.raises(ApiError) as exc:  # OMF is the grid of cells
        with dialect.read_tx(project.engine) as conn:
            bx.request(conn, project.folder, res.model_id,
                       {"format": "omf", "path": str(project.tmp / "out" / "a.omf")})
    assert exc.value.code == "block_list_unsupported"


def test_a_filter_exports_only_the_blocks_that_pass(project) -> None:  # type: ignore[no-untyped-def]
    m = partition(parents=5)
    res = import_list(project, m)
    path = export(project, res.model_id, "csv", delimiter=";", decimal=".",
                  restrict={"expr": "DOM = 'руда'"})
    rows = path.read_text(encoding="utf-8-sig").splitlines()[1:]
    assert len(rows) == int((m.dom == 0).sum())


# --- the calculator, the density, the categories --------------------------------------------------------------------


def test_the_calculator_the_density_and_the_categories_work_on_a_list(project) -> None:  # type: ignore[no-untyped-def]
    from bf_core.blocks import calc_job
    from bf_core.blocks import jobs_fields as jf

    m = partition(parents=4)
    res = import_list(project, m)
    out = calc_job.run_calc(project.engine, project.folder,
                            {"model_id": res.model_id, "expected_version": 1, "field": "AU2", "expr": "AU * 2"})
    mod = opened(project, res.model_id)
    assert out["field"] == "AU2" and mod.version == 2 and mod.listed
    lc = mod.columns(["AU", "AU2"])
    assert np.array_equal(lc["AU2"], lc["AU"] * 2, equal_nan=True)
    dens = jf.run_density(project.engine, project.folder, {
        "model_id": res.model_id, "expected_version": 2, "method": "constant", "value": 2.6, "out": "DENS"})
    assert dens["model_id"] == res.model_id
    assert opened(project, res.model_id).columns(["DENS"])["DENS"].min() == 2.6
    body = {"model_id": res.model_id, "expected_version": 3, "out": "CLS", "majority": 0,
            "rules": [{"label": "богато", "expr": "AU > 1"}, {"label": "бедно", "expr": "AU <= 1"}]}
    cls = jf.run_classify(project.engine, project.folder, body)
    got = opened(project, res.model_id)
    assert cls["model_id"] == res.model_id and got.listed and "CLS" in {f["name"] for f in got.fields}
    with pytest.raises(ApiError) as exc:  # the majority filter needs the parents
        jf.run_classify(project.engine, project.folder, {**body, "expected_version": got.version, "out": "CLS2",
                                                         "majority": 1})
    assert exc.value.code == "block_list_unsupported"


# --- the regularization -----------------------------------------------------------------------------------------------


def test_regularization_keeps_the_volume_the_tonnes_and_the_metal(project) -> None:  # type: ignore[no-untyped-def]
    m = partition(seed=4, parents=8)
    m.au[np.isnan(m.au)] = 0.7  # a gap makes the mean of the rest: the metal is kept where every block has a grade
    res = import_list(project, m)
    mod = opened(project, res.model_id)
    out = rg.regularize(mod, project.store, [10.0, 10.0, 10.0], grades=[{"field": "AU", "weight": "tonnes"}],
                        density="SG", categories=["DOM"], threads=2, chunk=3000)
    try:
        assert out.rows > 0
        b, a = out.totals["before"], out.totals["after"]
        assert b["volume"] == pytest.approx(float(np.prod(m.d, axis=1).sum()), rel=1e-12)
        assert a["volume"] == pytest.approx(b["volume"], rel=1e-9)
        assert a["tonnes"] == pytest.approx(b["tonnes"], rel=1e-9)
        assert a["metal"]["AU"] == pytest.approx(b["metal"]["AU"], rel=1e-9)
        assert b["tonnes"] == pytest.approx(float((np.prod(m.d, axis=1) * m.sg).sum()), rel=1e-12)
        assert b["metal"]["AU"] == pytest.approx(float((np.prod(m.d, axis=1) * m.sg * m.au).sum()), rel=1e-12)
    finally:
        out.staged.discard()
    # the same through the job
    from bf_core.blocks import jobs as bj

    done = bj.run_regularize(project.engine, project.folder, {"model_id": res.model_id, "size": [10.0, 10.0, 10.0],
                                                              "name": "Регуляр", "density": "SG"})
    reg = opened(project, done["model_id"], ("grid",))
    assert reg.layout == "grid" and not reg.listed and reg.grid.size == (10.0, 10.0, 10.0)
    assert reg.volume().sum() == pytest.approx(m.volume, rel=1e-9)


# --- the display: the faces, the block under a point ----------------------------------------------------------------

API = "/api/v1"
VIZ = API + "/viz/blocks"


@pytest.fixture()
def vc(pclient, tmp_path):  # type: ignore[no-untyped-def]
    from fastapi import APIRouter

    from bf_core.blocks import viz_data as vd
    from bf_core.blocks import viz_routes
    from bf_core.projects.service import projects_of
    from bf_core.stats import blocks_cache as bc
    from bf_core.storage.blobs import BlobStore

    if not any(getattr(r, "path", "") == f"{VIZ}/{{bm_id}}/shell" for r in pclient.app.routes):
        viz = APIRouter(prefix="/viz")
        viz.include_router(viz_routes.router)
        pclient.app.include_router(viz, prefix=API)
        blocks = APIRouter(prefix="/blocks")
        blocks.include_router(viz_routes.polygons_router)
        pclient.app.include_router(blocks, prefix=API)
    assert pclient.post(f"{API}/projects", json={"name": "Показ"}).status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.store = BlobStore(pclient.project.folder)
    pclient.tmp = tmp_path
    vd.RESPONSES.clear()
    bc.MODELS.clear()
    return pclient


def put_list(c: Any, m: SimpleNamespace, name: str = "Список") -> int:
    from bf_core.importing import object_blockfile as obf

    path = write_csv(c.tmp / f"{name}.csv", m)
    opts = obf.BlockOptions.of({"layout": "list"})
    done = obf.import_model(obf.open_source(path, opts), opts, engine=c.project.engine, store=c.store,
                            workdir=c.tmp / f"work-{name}", name=name, label=path.name, tick=lambda f, msg: None)
    return done.model_id


def shell_of(c: Any, mid: int, **body: Any) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    from bf_core.viz import binary

    r = c.post(f"{VIZ}/{mid}/shell", json=body)
    assert r.status_code == 200, r.text
    return binary.decode(r.content)


def shared_pairs(c: np.ndarray, d: np.ndarray) -> int:
    """The pairs of blocks that share a whole face (the high face of one is the low face of the other, the same
    extents), by brute force — the reference of the faces that are hidden."""
    lo, hi = c - d / 2, c + d / 2
    pairs = 0
    for a in range(len(c)):
        for ax in range(3):
            u, v = (ax + 1) % 3, (ax + 2) % 3
            m = (np.abs(hi[a, ax] - lo[:, ax]) < 1e-6) & (np.abs(lo[a, u] - lo[:, u]) < 1e-6)
            m &= (np.abs(hi[a, u] - hi[:, u]) < 1e-6) & (np.abs(lo[a, v] - lo[:, v]) < 1e-6)
            m &= np.abs(hi[a, v] - hi[:, v]) < 1e-6
            pairs += int(m.sum())
    return pairs


def test_every_block_has_six_faces_and_a_shell_hides_the_shared_ones(vc) -> None:  # type: ignore[no-untyped-def]
    m = partition(seed=6, parents=3)
    mid = put_list(vc, m)
    head, arr = shell_of(vc, mid, mode="all", colour={"kind": "none"})
    assert head["kind"] == "blocks" and head["faces"] == 6 * m.n and head["simplified"] is False and head["lod"] == 0
    h = arr["h"].astype(np.float64).reshape(-1, 2)
    area = 4.0 * (h[:, 0] * h[:, 1])
    d = m.d
    surface = float((2 * (d[:, 0] * d[:, 1] + d[:, 1] * d[:, 2] + d[:, 0] * d[:, 2])).sum())
    assert area.sum() == pytest.approx(surface, rel=1e-5)
    assert set(np.unique(arr["a"])) == set(range(6))
    assert (np.diff(arr["a"].astype(np.int64)) >= 0).all()  # by the side, the faces of a plane go together
    head, arr = shell_of(vc, mid, mode="shell", colour={"kind": "none"})
    pairs = shared_pairs(m.q, m.d)
    assert pairs > 0  # the blocks of a column meet face to face
    assert head["faces"] == 6 * m.n - 2 * pairs and head["faces_raw"] == head["faces"]
    h = arr["h"].astype(np.float64).reshape(-1, 2)
    assert (4.0 * (h[:, 0] * h[:, 1])).sum() < surface  # what is seen is a part of the surface of all the blocks


def test_the_colour_of_a_face_is_the_class_of_its_block_and_a_filter_drops_blocks(vc) -> None:  # type: ignore[no-untyped-def]
    from bf_core.blocks.shell_merge import NO_CLASS

    m = partition(seed=8, parents=3)
    mid = put_list(vc, m)
    head, arr = shell_of(vc, mid, mode="all", colour={"field": "DOM", "kind": "categories", "hidden": []})
    assert sorted(head["labels"]) == sorted(LABELS) and head["classes"] == 3  # the labels go in the order of the file
    got = np.bincount(arr["k"].astype(np.int64), minlength=3)
    want = 6 * np.bincount(m.dom, minlength=3)
    by_head = {lab: int(got[i]) for i, lab in enumerate(head["labels"])}
    assert by_head == {lab: int(want[i]) for i, lab in enumerate(LABELS)}
    head, arr = shell_of(vc, mid, mode="all", colour={"field": "AU", "kind": "scale"},
                         restrict={"cond": {"field": "SG", "op": ">", "value": 2.8}})
    kept = int((m.sg > 2.8).sum())
    assert head["faces"] == 6 * kept and head["passed"] == kept and head["empty"] == m.n - kept
    nogap = int(np.isnan(m.au[m.sg > 2.8]).sum())  # a block without a value has the class «no value», not 0
    assert int((arr["k"] == NO_CLASS).sum()) == 6 * nogap


def test_above_the_limit_the_blocks_become_cells_of_the_enclosing_grid(vc) -> None:  # type: ignore[no-untyped-def]
    from bf_core.blocks import list_shell as ls

    m = partition(seed=1, parents=6)
    mid = put_list(vc, m)
    with dialect.read_tx(vc.project.engine) as conn:
        mod = Model.open(conn, vc.store, mid, ANY)
    idx, (lc, ld) = mod.p(), mod.geo()
    ok = np.ones(mod.rows, dtype=bool)
    full = ls.view(mod.grid, idx, lc, ld, ok, limit=10**9)
    assert not full.simplified and full.lod == 0
    small = ls.view(mod.grid, idx, lc, ld, ok, limit=600)
    assert small.simplified and small.lod >= 1 and len(small.faces) <= 600 and small.faces_raw == full.faces_raw
    c = small.faces.c  # the coarse shell lies inside the extent of the enclosing grid
    assert (c.min(axis=0) >= -1e-9).all() and (c.max(axis=0) <= np.asarray(mod.grid.lengths()) + 1e-9).all()


def test_the_block_under_a_point_is_the_box_that_holds_it(vc) -> None:  # type: ignore[no-untyped-def]
    m = partition(seed=2, parents=4)
    mid = put_list(vc, m)
    rng = np.random.default_rng(3)
    for r in rng.integers(0, m.n, 8):
        centre, size = m.centre[r], m.d[r]
        got = vc.post(f"{VIZ}/{mid}/at", json={"point": list(centre), "fields": ["AU", "DOM"]}).json()
        assert got["found"] is True and got["sub"] is None and got["passed"] is True
        assert got["centre"] == pytest.approx(list(centre), abs=1e-9) and got["size"] == pytest.approx(list(size))
        assert got["values"]["DOM"] == LABELS[m.dom[r]]
        want = None if math.isnan(m.au[r]) else m.au[r]
        assert got["values"]["AU"] == want  # a gap is null, never 0
        # a point on the high face, with the normal out of the block, is still that block
        top = [centre[0], centre[1], centre[2] + size[2] / 2]
        again = vc.post(f"{VIZ}/{mid}/at", json={"point": top, "normal": [0, 0, 1]}).json()
        assert again["found"] is True and again["row"] == got["row"]
    assert vc.post(f"{VIZ}/{mid}/at", json={"point": [0.0, 0.0, 0.0]}).json() == {"found": False}
    hole = vc.post(f"{VIZ}/{mid}/at", json={"point": [ORIGIN[0] + 5, ORIGIN[1] + 5, ORIGIN[2] + 500]}).json()
    assert hole == {"found": False}
    cut = vc.post(f"{VIZ}/{mid}/at", json={"point": list(m.centre[0]),
                                           "restrict": {"cond": {"field": "SG", "op": ">", "value": 99}}}).json()
    assert cut["found"] is True and cut["passed"] is False and cut["f"] == 0.0


# --- what a list does not do ----------------------------------------------------------------------------------------


def test_the_operations_that_need_the_grid_say_to_regularize(vc) -> None:  # type: ignore[no-untyped-def]
    from bf_core.stats import blocks_cache as bc

    m = partition(seed=2, parents=3)
    mid = put_list(vc, m)

    def refused(r: Any) -> None:
        e = r.json()
        assert (r.status_code, e["code"]) == (422, "block_list_unsupported"), e
        assert "Регуляризуйте" in e["hint"] and "список" in e["message"].casefold()

    slice_body = {"plane": {"origin": list(m.centre[0]), "normal": [0, 0, 1]}}
    refused(vc.post(f"{VIZ}/{mid}/slice", json=slice_body))
    refused(vc.post(f"{API}/blocks/{mid}/slice/polygons", json=slice_body))
    with dialect.read_tx(vc.project.engine) as conn:
        for what in ("Оценка", "Ресурсы", "Карьер", "Заполнение", "Кодирование", "Сравнение"):
            with pytest.raises(ApiError) as exc:
                Model.open(conn, vc.store, mid, what=what)
            assert exc.value.code == "block_list_unsupported" and f"«{what}»" in exc.value.message
        with pytest.raises(ApiError) as exc2:  # the statistics read the grid of parents
            bc.model_of(conn, mid)
    assert exc2.value.code == "block_list_unsupported"
