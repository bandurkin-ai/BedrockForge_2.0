"""Storage of wireframes and DTM settings (ADR 0011 §1): vertices come back bit for bit in their order, the
triangles are the same triangles in spatial order with their part table; measures against the analytic
answer; staging leaves no temp files; names, colour, attributes of the user, the settings of a DTM, the drape
reference to a plan raster (a vertical one is refused), «вернуть в каркасы», deleting."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, object_refs, objects, rasters, wireframes
from bf_core.geo import meshes as gm
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import store as ws
from tests.test_import_job import API
from tests.test_viz_mesh import cube, grid, hills

pytestmark = pytest.mark.windows


@pytest.fixture()
def project(pclient):  # type: ignore[no-untyped-def]
    r = pclient.post(f"{API}/projects", json={"name": "Каркасы"})
    assert r.status_code == 200, r.text
    return projects_of(pclient.core).require_current()


def add_raster(conn: sa.Connection, name: str, *, vertical: bool = False, state: str = "ready") -> int:
    """A raster row as the import writes it (the rows of `rasters` are read directly: ADR 0011 §1)."""
    now = dialect.utcnow()
    res = conn.execute(
        sa.insert(objects).values(
            kind="raster",
            name=name,
            name_key=dialect.name_key(name),
            sort_key=0.0,
            state=state,
            version=1,
            meta={},
            created_at=now,
            updated_at=now,
        )  # fmt: skip
    )
    raster_id = int(res.inserted_primary_key[0])  # type: ignore[index]
    georef = {"affine": [0.5, 0.0, 512_000.25, 0.0, -0.5, 6_235_000.75], "source": "world", "crs": None}
    conn.execute(
        sa.insert(rasters).values(
            object_id=raster_id, width=40, height=30, georef=georef, vertical=vertical, opacity=1.0, version=1
        )
    )
    return raster_id


def err(fn: Any, *args: Any, **kw: Any) -> ApiError:
    with pytest.raises(ApiError) as e:
        fn(*args, **kw)
    assert e.value.message and e.value.hint
    return e.value


def tmp_files(project: Any) -> list[Any]:
    tmp = BlobStore(project.folder).tmp_dir
    return list(tmp.iterdir()) if tmp.exists() else []


# --- geometry --------------------------------------------------------------------------------------------


def test_geometry_comes_back_bit_for_bit(project) -> None:  # type: ignore[no-untyped-def]
    v, t = grid(130, 110, hills)  # 28 600 shuffled triangles on UTM coordinates
    store = BlobStore(project.folder)
    with dialect.write_tx(project.engine) as conn:
        wf = ws.put_wireframe(conn, store, v, t, name="Рельеф", color="#8C9A6B", attrs={"Участок": "Север"})
    assert tmp_files(project) == []
    with dialect.read_tx(project.engine) as conn:
        info = ws.wireframe_info(conn, wf)
        xyz, tri = ws.read_wireframe(conn, store, wf)
        row = conn.execute(sa.select(wireframes).where(wireframes.c.object_id == wf)).mappings().one()
        roles = {r.role: r.sha256 for r in conn.execute(sa.select(blob_owners).where(blob_owners.c.owner_id == wf))}
        shas = ws.blob_shas(conn, wf)
    assert xyz.dtype == np.float64 and tri.dtype == np.int32
    assert xyz.tobytes() == v.tobytes()  # the vertices, in their order, bit for bit
    assert sorted(map(tuple, tri.tolist())) == sorted(map(tuple, t.tolist()))  # the same triangles
    assert set(roles) == {"xyz", "tri"} and shas == roles
    assert info["point_count"] == len(v) and info["triangle_count"] == len(t) and info["version"] == 1
    assert info["extent"] == [*v.min(axis=0).tolist(), *v.max(axis=0).tolist()]  # exact float64
    assert all(float(c).is_integer() for c in info["origin"])
    assert np.abs(np.array(info["origin"]) - (v.min(axis=0) + v.max(axis=0)) / 2).max() <= 0.5
    assert info["color"] == "#8c9a6b" and info["dtm"] is None
    assert info["attrs"] == [{"name": "Участок", "value": "Север"}]
    # the part table: consecutive runs of the stored triangles with their exact boxes
    render = row["render"]
    assert render["part_triangles"] == gm.PART_TRIANGLES and len(render["parts"]) == 1
    first, count, *box = render["parts"][0]
    assert (first, count) == (0, len(t)) and box == info["extent"]

    # stored along the Z-order curve: cut finer, the parts are compact pieces of the plan
    def cover(triangles: np.ndarray) -> float:
        fine = gm.parts_table(xyz, triangles, max_triangles=1800)
        return sum((p.bbox[3] - p.bbox[0]) * (p.bbox[4] - p.bbox[1]) for p in fine) / (130 * 110)

    assert cover(tri) < 4.0 < 12.0 < cover(t)  # 3.3 as stored; the shuffled input spans the surface every time
    # measures: the same numbers as the kernel gives for the input
    s = info["stats"]
    assert s["area"] == pytest.approx(gm.mesh_stats(v, t)["area"], rel=1e-12)
    assert s["plan_area"] == pytest.approx(130 * 110, rel=1e-12) and not s["closed"] and s["volume"] is None
    assert s["open_edges"] == 2 * (130 + 110)


def test_closed_solid_has_its_volume(project) -> None:  # type: ignore[no-untyped-def]
    v, t = cube(8, 25.0)
    with dialect.write_tx(project.engine) as conn:
        wf = ws.put_wireframe(conn, BlobStore(project.folder), v, t, name="Куб")
        s = ws.wireframe_info(conn, wf)["stats"]
    assert s["closed"] and s["volume"] == pytest.approx(25.0**3, rel=1e-12) and s["orientation"] == "outward"
    assert s["area"] == pytest.approx(6 * 25.0**2, rel=1e-12)


def test_bad_meshes_are_refused_and_staging_leaves_nothing(project) -> None:  # type: ignore[no-untyped-def]
    v, t = grid(6, 6, hills, seed=None)
    store = BlobStore(project.folder)
    bad = v.copy()
    bad[5, 2] = np.nan  # an empty height is not a zero: a wireframe needs every coordinate
    for xyz, tri, code in [(bad, t, "bad_coordinates"), (v, t + 10_000, "bad_mesh"), (v, t[:0], "empty_mesh")]:
        e = err(ws.stage_wireframe, store, xyz, tri)
        assert e.status == 422 and e.code == code
    assert tmp_files(project) == []
    staged = ws.stage_wireframe(store, v, t)
    assert len(tmp_files(project)) == 2 and set(staged.shas) == {"xyz", "tri"}
    staged.discard()
    assert tmp_files(project) == []

    class Stop(Exception):
        pass

    seen: list[tuple[float, str]] = []

    def cancel(value: float, message: str) -> None:
        seen.append((value, message))
        if value >= 0.75:
            raise Stop

    with pytest.raises(Stop):  # a cancel from the progress callback: nothing is left behind
        ws.stage_wireframe(store, v, t, progress=cancel)
    assert tmp_files(project) == [] and seen[0][0] == 0.0 and all(m for _v, m in seen)
    assert [a for a, _m in seen] == sorted(a for a, _m in seen)
    with dialect.write_tx(project.engine) as conn:  # a failed commit drops the temp files too
        with pytest.raises(ApiError):
            ws.put_wireframe(conn, store, v, t, name="Плохой цвет", color="red")
        assert conn.execute(sa.select(sa.func.count()).select_from(wireframes)).scalar_one() == 0
        conn.rollback()
    assert tmp_files(project) == []


# --- properties ------------------------------------------------------------------------------------------


def test_names_colour_and_versions(project) -> None:  # type: ignore[no-untyped-def]
    v, t = cube(2, 5.0)
    store = BlobStore(project.folder)
    with dialect.write_tx(project.engine) as conn:
        a = ws.put_wireframe(conn, store, v, t, name="Рудное тело")
        assert ws.free_name(conn, "Рудное  тело") == "Рудное тело (2)"
        assert ws.free_name(conn, "рудное тело", exclude_id=a) == "рудное тело"
        b = ws.put_wireframe(conn, store, v, t, name=ws.free_name(conn, "Рудное тело"))
        assert ws.free_name(conn, "Рудное тело") == "Рудное тело (3)" and ws.free_name(conn, "  ") == "Каркас"
        assert [w["name"] for w in ws.list_wireframes(conn)] == ["Рудное тело", "Рудное тело (2)"]
        out = ws.update_wireframe(conn, b, 1, {"name": "  Зона  окисления ", "color": "#AABBCC"})
        assert out["name"] == "Зона окисления" and out["color"] == "#aabbcc" and out["version"] == 2
        assert ws.update_wireframe(conn, b, 2, {"color": None})["color"] is None
        stale = err(ws.update_wireframe, conn, b, 1, {"name": "Другое"})
        assert stale.status == 409 and stale.code == "version_conflict"
        assert stale.extra["wireframe"]["version"] == 3 and stale.extra["wireframe"]["name"] == "Зона окисления"
        assert err(ws.update_wireframe, conn, b, 3, {"name": "РУДНОЕ ТЕЛО"}).code == "name_taken"
        assert err(ws.update_wireframe, conn, b, 3, {"name": "   "}).code == "name_empty"
        assert err(ws.update_wireframe, conn, b, 3, {"name": "я" * 201}).code == "name_too_long"
        assert err(ws.update_wireframe, conn, b, 3, {"color": "#12345"}).code == "bad_color"
        assert err(ws.wireframe_info, conn, 999_999).status == 404
        assert ws.wireframe_info(conn, b)["version"] == 3  # refused changes change nothing
        # an object that is still being built does not exist for the interface
        c = ws.put_wireframe(conn, store, v, t, name="Строится", state="building")
        assert err(ws.wireframe_info, conn, c).code == "wireframe_not_found" and len(ws.list_wireframes(conn)) == 2


def test_attributes_of_the_user(project) -> None:  # type: ignore[no-untyped-def]
    v, t = cube(2, 5.0)
    with dialect.write_tx(project.engine) as conn:
        wf = ws.put_wireframe(conn, BlobStore(project.folder), v, t, name="Тело")
        out = ws.edit_attrs(
            conn, wf, 1, values={"Участок": " Север ", "Год": 2026, "Утверждён": True, "Плотность": 2.7}
        )
        assert out["version"] == 2 and out["attrs"] == [
            {"name": "Участок", "value": "Север"},
            {"name": "Год", "value": 2026},
            {"name": "Утверждён", "value": True},
            {"name": "Плотность", "value": 2.7},
        ]
        # rename, delete, change and add in one request; names are compared without case; the order stays
        out = ws.edit_attrs(
            conn, wf, 2, rename={"год": "Год отчёта"}, remove=["УТВЕРЖДЁН"],
            values={"участок": "Юг", "Комментарий": "", "Плотность": None},
        )  # fmt: skip
        assert out["attrs"] == [
            {"name": "Участок", "value": "Юг"},
            {"name": "Год отчёта", "value": 2026},
            {"name": "Плотность", "value": None},  # empty stays empty, never 0
            {"name": "Комментарий", "value": None},
        ]
        for kw, code in [
            ({"rename": {"Нет такого": "Х"}}, "attr_not_found"),
            ({"remove": ["Нет такого"]}, "attr_not_found"),
            ({"rename": {"Участок": "плотность"}}, "attr_exists"),
            ({"rename": {"Участок": "  "}}, "attr_name_empty"),
            ({"values": {"": 1}}, "attr_name_empty"),
            ({"values": {"я" * 101: 1}}, "attr_name_too_long"),
            ({"values": {"Т": "я" * 2001}}, "attr_too_long"),
            ({"values": {"Т": float("nan")}}, "not_a_number"),
            ({"values": {"Т": [1, 2]}}, "bad_attr_value"),
            ({"values": {f"А{i}": i for i in range(ws.MAX_ATTRS)}}, "too_many_attrs"),
        ]:
            assert err(ws.edit_attrs, conn, wf, 3, **kw).code == code, kw
        assert err(ws.edit_attrs, conn, wf, 2, values={"Т": 1}).status == 409
        assert ws.edit_attrs(conn, wf, 3, rename={"участок": "УЧАСТОК"})["attrs"][0]["name"] == "УЧАСТОК"
        assert err(ws.put_wireframe, conn, BlobStore(project.folder), v, t, name="Д", attrs=[
            {"name": "А", "value": 1}, {"name": "а", "value": 2}]).code == "attr_exists"  # fmt: skip
        conn.rollback()
    assert tmp_files(project) == []


# --- DTM -------------------------------------------------------------------------------------------------


def test_dtm_settings_drape_and_back_to_wireframes(project) -> None:  # type: ignore[no-untyped-def]
    v, t = grid(20, 20, hills)
    with dialect.write_tx(project.engine) as conn:
        wf = ws.put_wireframe(conn, BlobStore(project.folder), v, t, name="Рельеф")
        plan = add_raster(conn, "Ортофото")
        other = add_raster(conn, "Карта")
        scan = add_raster(conn, "Разрез 12", vertical=True)
        building = add_raster(conn, "Загружается", state="building")
        # settings without `on` move the wireframe to the DTMs, with the defaults for what is not given
        out = ws.set_dtm(conn, wf, 1, {"opacity": 0.5})
        assert out["version"] == 2
        assert out["dtm"] == {"raster_id": None, "clip": True, "tint": "#ffffff", "opacity": 0.5, "shade": True}
        out = ws.set_dtm(conn, wf, 2, {"raster_id": plan, "tint": "#FFEEDD", "clip": False, "shade": False})
        assert out["dtm"] == {"raster_id": plan, "clip": False, "tint": "#ffeedd", "opacity": 0.5, "shade": False}
        stored = conn.execute(sa.select(wireframes.c.dtm).where(wireframes.c.object_id == wf)).scalar_one()
        assert stored == {"clip": False, "tint": "#ffeedd", "opacity": 0.5, "shade": False}  # no raster id inside
        refs = conn.execute(sa.select(object_refs)).mappings().all()
        assert [(r["src_id"], r["dst_id"], r["role"]) for r in refs] == [(wf, plan, "drape")]
        assert ws.dtm_users(conn, plan) == [{"id": wf, "name": "Рельеф"}] and ws.dtm_users(conn, other) == []
        # refused: a vertical raster, a raster that is not there (or not ready), bad numbers — nothing changes
        e = err(ws.set_dtm, conn, wf, 3, {"raster_id": scan})
        assert e.status == 422 and e.code == "drape_vertical_raster" and "Разрез 12" in e.message
        assert err(ws.set_dtm, conn, wf, 3, {"raster_id": 999_999}).status == 404
        assert err(ws.set_dtm, conn, wf, 3, {"raster_id": building}).code == "raster_not_found"
        assert err(ws.set_dtm, conn, wf, 3, {"raster_id": wf}).code == "raster_not_found"  # not a raster
        for bad in ({"opacity": 1.5}, {"opacity": -0.1}, {"opacity": float("nan")}):
            assert err(ws.set_dtm, conn, wf, 3, bad).code == "bad_opacity"
        assert err(ws.set_dtm, conn, wf, 3, {"tint": "white"}).code == "bad_color"
        assert err(ws.set_dtm, conn, wf, 2, {"clip": True}).status == 409
        # (the failed calls above ran in this transaction: the drape must still be the plan raster)
        assert ws.wireframe_info(conn, wf)["dtm"]["raster_id"] == plan
        # another raster replaces the first; 0 takes it off, the other settings stay
        assert ws.set_dtm(conn, wf, 3, {"raster_id": other})["dtm"]["raster_id"] == other
        assert conn.execute(sa.select(sa.func.count()).select_from(object_refs)).scalar_one() == 1
        out = ws.set_dtm(conn, wf, 4, {"raster_id": 0})
        assert out["dtm"] == {"raster_id": None, "clip": False, "tint": "#ffeedd", "opacity": 0.5, "shade": False}
        ws.set_dtm(conn, wf, 5, {"raster_id": plan, "on": True})
        # «Вернуть в каркасы»: the settings and the raster go, whatever else is in the request
        out = ws.set_dtm(conn, wf, 6, {"on": False, "raster_id": other, "opacity": 0.2})
        assert out["dtm"] is None and out["version"] == 7
        assert conn.execute(sa.select(sa.func.count()).select_from(object_refs)).scalar_one() == 0
        # back in the DTMs: the defaults again, no raster
        out = ws.set_dtm(conn, wf, 7, {"on": True})
        assert out["dtm"] == {"raster_id": None, **ws.DTM_DEFAULTS}


def test_vertical_drape_is_refused_without_side_effects(project) -> None:  # type: ignore[no-untyped-def]
    v, t = grid(8, 8, hills)
    with dialect.write_tx(project.engine) as conn:
        wf = ws.put_wireframe(conn, BlobStore(project.folder), v, t, name="Рельеф", dtm={"opacity": 0.8})
        plan = add_raster(conn, "Ортофото")
        scan = add_raster(conn, "Скан", vertical=True)
        ws.set_dtm(conn, wf, 1, {"raster_id": plan})
    with pytest.raises(ApiError), dialect.write_tx(project.engine) as conn:
        ws.set_dtm(conn, wf, 2, {"raster_id": scan, "opacity": 0.1})
    with dialect.read_tx(project.engine) as conn:
        info = ws.wireframe_info(conn, wf)
    assert info["version"] == 2 and info["dtm"]["raster_id"] == plan and info["dtm"]["opacity"] == 0.8


def test_created_as_a_dtm_with_a_raster_and_deleted(project) -> None:  # type: ignore[no-untyped-def]
    v, t = grid(10, 10, hills)
    store = BlobStore(project.folder)
    with dialect.write_tx(project.engine) as conn:
        plan = add_raster(conn, "Ортофото")
        wf = ws.put_wireframe(conn, store, v, t, name="ЦТМ", dtm=True, raster_id=plan, meta={"built": {"points": 121}})
        twin = ws.put_wireframe(conn, store, v, t, name="Копия")  # the same geometry: the same blobs
        info = ws.wireframe_info(conn, wf)
        assert info["dtm"] == {"raster_id": plan, **ws.DTM_DEFAULTS} and info["built"] == {"points": 121}
        # an object that refers to the wireframe keeps it
        now = dialect.utcnow()
        chart = conn.execute(
            sa.insert(objects).values(
                kind="chart",
                name="График",
                name_key="график",
                sort_key=0.0,
                state="ready",
                version=1,
                meta={},
                created_at=now,
                updated_at=now,
            )  # fmt: skip
        ).inserted_primary_key[0]
        conn.execute(sa.insert(object_refs).values(src_id=chart, dst_id=wf, role="source"))
        e = err(ws.delete_wireframe, conn, wf)
        assert e.status == 422 and e.code == "wireframe_in_use" and "График" in e.message
        assert e.extra["objects"] == [{"id": chart, "name": "График", "kind": "chart"}]
        conn.execute(sa.delete(object_refs).where(object_refs.c.src_id == chart))
        gone = ws.delete_wireframe(conn, wf)
        assert gone["id"] == wf and gone["shared"] and set(gone["shas"]) == {"xyz", "tri"}
        assert conn.execute(sa.select(sa.func.count()).select_from(object_refs)).scalar_one() == 0  # the drape too
        assert ws.dtm_users(conn, plan) == []
        assert conn.execute(sa.select(blob_owners.c.owner_id).distinct()).scalars().all() == [twin]
        assert not ws.delete_wireframe(conn, twin)["shared"]
        assert conn.execute(sa.select(sa.func.count()).select_from(wireframes)).scalar_one() == 0
        assert conn.execute(sa.select(sa.func.count()).select_from(blob_owners)).scalar_one() == 0
        assert err(ws.delete_wireframe, conn, wf).status == 404
