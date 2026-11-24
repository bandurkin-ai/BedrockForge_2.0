"""Reading the bodies and writing the result of a fill or a coding (подшаг 17.20; ADR 0023 §6).

    meshes, names, warnings = load_meshes(conn, store, grid, wires)       # closed bodies in the local axes (422)
    lab = Labels.of(codes, outside_code, mode)                              # CODE: the labels and the table of codes
    staged = write_rows(store, grid, p, s, w, {name: values}, fields)       # the file `cells` of a new model (V2)
    bm_id = commit_new(conn, store, staged, name=…, source=…, inputs=…)     # a new model
    out = replace_in_place(conn, store, staged, model_id, version, …)      # «Перестроить»: the same id, version + 1

The labels of CODE are the codes of the wireframes in the order of the request (a code given twice is one label — two
bodies of one ore) and, when the mode wants the blocks outside, the code «снаружи» last. A row of no wanted class has
no code (the category's null, never a code of a body) and FRAC NaN: a gap is not a zero. A new model of a fill and
a coding keeps rows by (P, sz, sy, sx) (`CellsWriterV2` checks the order). «Перестроить» replaces the file `cells`
and the files of the fields of the model by the new ones in one transaction and takes away the fields the operation
did not make (they were computed from the old rows): the names go to the result (`dropped`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import metadata
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import meta as bmeta
from bf_core.blocks import schema as bs
from bf_core.blocks.fields_store import version_conflict
from bf_core.blocks.fill.spec import FillSpec, Wire, bad
from bf_core.blocks.store import CellsWriterV2, StagedCells, commit_block_model, not_found
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, objects
from bf_core.storage.blobs import BlobStore

if TYPE_CHECKING:
    import numpy as np

    from bf_core.blocks.fill.columns import Mesh

OWNER = "object"
CHUNK = 1 << 20
ALGO = "fill-1"  # the algorithm of 8.7.3 as built in 17.17–17.20; a new number — «устарел» by `algo`
CODE_TEXT = "Код тела по каркасам"
FRAC_TEXT = "Доля объёма блока, занятая телом"
Tick = Callable[[float | None, str | None], None]


def versions() -> dict[str, str]:
    out = {}
    for pkg in ("numpy", "numba"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:  # pragma: no cover - the build always carries both
            out[pkg] = "?"
    return out


# --- the bodies ------------------------------------------------------------------------------------------------------


def not_closed(index: int, name: str, stats: Mapping[str, Any]) -> ApiError | None:
    """422 `wireframe_open` from the stored stats of a wireframe (no geometry read): None — the stats do not say."""
    from bf_core.blocks.fill.columns import FIX_HINT

    if "closed" not in stats or stats.get("closed"):
        return None
    opened, nonmanifold = int(stats.get("open_edges") or 0), int(stats.get("nonmanifold_edges") or 0)
    if not opened and not nonmanifold:
        detail = "это поверхность, а не тело"
    elif opened:
        detail = f"открытых рёбер — {opened}"
    else:
        detail = f"рёбер с тремя и более треугольниками — {nonmanifold}"
    return ApiError(422, "wireframe_open", f"Каркас «{name}» не замкнут: {detail}.", FIX_HINT,
                    extra={"param": f"wireframes.{index}.id", "name": name})  # fmt: skip


def wireframe_not_found(index: int) -> ApiError:
    return ApiError(404, "wireframe_not_found", "Каркас не найден.", "Обновите список каркасов.",
                    extra={"param": f"wireframes.{index}.id"})  # fmt: skip


def wire_infos(conn: sa.Connection, wires: Sequence[Wire]) -> list[dict[str, Any]]:
    """`wireframes.wireframe_info` of every body (404 `wireframe_not_found {param}`; 422 `wireframe_open` by the
    stored stats)."""
    from bf_core.wireframes import store as wf

    out = []
    for n, w in enumerate(wires):
        try:
            info = wf.wireframe_info(conn, w.id)
        except ApiError as e:
            if e.status == 404:
                raise wireframe_not_found(n) from None
            raise
        err = not_closed(n, str(info["name"]), info.get("stats") or {})
        if err is not None:
            raise err
        out.append(info)
    return out


def load_meshes(
    conn: sa.Connection, store: BlobStore, grid: bs.GridV2, wires: Sequence[Wire], infos: Sequence[Mapping[str, Any]],
    tick: Callable[[float], None] | None = None,
) -> tuple[list[Mesh], list[str]]:  # fmt: skip
    """The bodies as `columns.Mesh` in the local axes of `grid` (the checks of 8.7.3 step 1: closed and consistent,
    else 422 `wireframe_open` / `wireframe_orientation {param: "wireframes.N.id", name}`) and the warnings: a body
    that does not touch the grid in the plan."""
    from bf_core.blocks.fill.columns import Mesh
    from bf_core.wireframes import store as wf

    grid.regular("Заполнение")
    meshes: list[Mesh] = []
    warnings: list[str] = []
    for n, (w, info) in enumerate(zip(wires, infos, strict=True)):
        xyz, tri = wf.read_wireframe(conn, store, w.id)
        mesh = Mesh.of(grid, grid.to_local(xyz), tri, str(info["name"]), stats=info.get("stats") or None, index=n)
        del xyz, tri
        if mesh.index.ncx == 0:
            warnings.append(f"Каркас «{info['name']}» не пересекает сетку в плане: блоков с его кодом нет.")
        meshes.append(mesh)
        if tick is not None:
            tick((n + 1) / len(wires))
    return meshes, warnings


def names_of(infos: Sequence[Mapping[str, Any]], model: Any = None) -> dict[str, Any]:
    """`source.params.names`: the names of the inputs at the time of the job — the objects may be gone when the model
    is read («удалён каркас «Руда»», `inputs.names_in`)."""
    out: dict[str, Any] = {"wireframes": {str(int(i["id"])): str(i["name"]) for i in infos}}
    if model is not None:
        out["model"] = {str(int(model.id)): str(model.name)}
    return out


def not_prototype() -> ApiError:
    return ApiError(422, "not_prototype", "Эта модель не прототип: заполнять можно только прототип.",
                    "Создайте прототип («Блочные модели» → «Прототип») и заполните его.",
                    extra={"param": "prototype_id"})  # fmt: skip


def source_lost(what: str) -> ApiError:
    return ApiError(409, "source_lost", f"Исходных данных больше нет: {what}.",
                    "Восстановите объект или создайте модель заново.")  # fmt: skip


def check_names(fields: Sequence[Mapping[str, Any]], spec: FillSpec) -> None:
    """409 `field_exists` when the model has a field of the name asked for; 422 `frac_exists` — a second field of the
    role `frac` (a model has one: it sets the volume of the rows)."""
    taken = {str(f["name"]).casefold() for f in fields}
    for param, name in (("code_field", spec.code_field), ("frac_field", spec.frac_field)):
        if name is not None and name.casefold() in taken:
            raise ApiError(409, "field_exists", f"Поле «{name}» в модели уже есть.", "Задайте другое имя поля.",
                           extra={"field": name, "param": param})  # fmt: skip
    if spec.frac_field is not None and any(f.get("role") == "frac" for f in fields):
        raise ApiError(422, "frac_exists", "У модели уже есть поле доли объёма.",
                       "Не записывайте второе поле доли (frac_field — пусто) или удалите прежнее.",
                       extra={"param": "frac_field"})  # fmt: skip


# --- the field CODE --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Labels:
    """The labels of CODE and the table from the codes of the kernels: `lut[c]` for the input number c of a body,
    `lut[nb]` — outside, `lut[nb + 1]` — nothing wanted (−1); all int16, −1 — no label."""

    labels: list[str]
    lut: np.ndarray
    nb: int

    @classmethod
    def of(cls, codes: Sequence[str], outside: str, mode: str) -> Labels:
        import numpy as np

        labels: list[str] = []
        index: dict[str, int] = {}
        for c in codes:
            if c.casefold() not in index:
                index[c.casefold()] = len(labels)
                labels.append(c)
        nb = len(codes)
        lut = np.full(nb + 2, -1, np.int16)
        for b, c in enumerate(codes):
            lut[b] = index[c.casefold()]
        if mode != "inside":
            if outside.casefold() in index:
                raise bad("outside_code", f"Код «{outside}» уже есть у каркаса.",
                          "Задайте другой код для блоков снаружи.")  # fmt: skip
            lut[nb] = len(labels)
            labels.append(outside)
        return cls(labels, lut, nb)

    def apply(self, code: np.ndarray) -> np.ndarray:
        """int16 codes of the kernels → the codes of the labels (−1: no code)."""
        import numpy as np

        return self.lut[np.where(code < 0, self.nb + 1, code)]


def field_source(op: str, params: Mapping[str, Any], inputs: Sequence[str] = ()) -> dict[str, Any]:
    return {"op": op, "params": dict(params), "inputs": list(inputs), "algo": ALGO, "versions": versions(),
            "at": dialect.utcnow().isoformat()}  # fmt: skip


def result_fields(
    code_field: str, frac_field: str | None, labels: Sequence[str], source: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """The specs of the fields a fill / coding makes: CODE (category, role `code`) and FRAC (f8, role `frac`)."""
    out: list[dict[str, Any]] = [{
        "name": code_field, "kind": bs.CATEGORY, "dtype": "cat", "unit": None, "description": CODE_TEXT,
        "role": "code", "labels": list(labels), "source": dict(source),
    }]  # fmt: skip
    if frac_field is not None:
        out.append({"name": frac_field, "kind": bs.NUMBER, "dtype": "f8", "unit": None, "description": FRAC_TEXT,
                    "role": "frac", "source": dict(source)})  # fmt: skip
    return out


class Tally:
    """Rows and volume (Σ V·FRAC, m³) by the labels of CODE, added portion by portion; a row with no code or a gap in
    FRAC (NaN volume) is in no label."""

    def __init__(self, labels: Sequence[str]) -> None:
        import numpy as np

        self.labels = [str(x) for x in labels]
        self.rows = np.zeros(len(self.labels), np.int64)
        self.vol = np.zeros(len(self.labels), np.float64)

    def add(
        self, grid: bs.GridV2, p: np.ndarray, s: np.ndarray | None, w: np.ndarray | None, code: np.ndarray,
        frac: np.ndarray,
    ) -> None:  # fmt: skip
        import numpy as np

        from bf_core.blocks import geometry

        k = len(self.labels)
        for a in range(0, len(p), CHUNK):
            b = min(len(p), a + CHUNK)
            c = code[a:b].astype(np.int64)
            v = geometry.row_volumes(grid, p[a:b], None if s is None else s[a:b], None if w is None else w[a:b],
                                     frac[a:b])  # fmt: skip
            has = c >= 0
            self.rows += np.bincount(c[has], minlength=k)[:k]
            good = has & np.isfinite(v)
            self.vol += np.bincount(c[good], weights=v[good], minlength=k)[:k]

    def result(self) -> tuple[dict[str, int], dict[str, float]]:
        return ({k: int(n) for k, n in zip(self.labels, self.rows, strict=True)},
                {k: float(v) for k, v in zip(self.labels, self.vol, strict=True)})  # fmt: skip


# --- writing ---------------------------------------------------------------------------------------------------------


def write_rows(
    store: BlobStore, grid: bs.GridV2, p: np.ndarray, s: np.ndarray | None, w: np.ndarray | None,
    values: Mapping[str, np.ndarray], fields: Sequence[Mapping[str, Any]], tick: Callable[[float], None] | None = None,
    inherit: Callable[[int, int], Mapping[str, np.ndarray]] | None = None,
) -> StagedCells:  # fmt: skip
    """The file `cells` of a new model (a temp file, committed by `commit_new`): rows by (P, sz, sy, sx), `values`
    by the fields; `inherit(a, b)` — more values of the rows [a, b) (the fields of the parent). The temp file is
    dropped if anything raises (a cancel included)."""
    sub = grid.sub.sub
    n = len(p)
    with CellsWriterV2(store, grid, fields, sub=sub) as out:
        for a in range(0, n, CHUNK):
            b = min(n, a + CHUNK)
            part = {k: v[a:b] for k, v in values.items()}
            if inherit is not None:
                part.update(inherit(a, b))
            out.write(p[a:b], part, s[a:b] if sub and s is not None else None,
                      w[a:b] if sub and w is not None else None)  # fmt: skip
            if tick is not None:
                tick((b / n) if n else 1.0)
        return out.finish()


def commit_new(
    conn: sa.Connection, store: BlobStore, staged: StagedCells, *, name: str, op: str, params: Mapping[str, Any],
    inputs: Mapping[str, Any], warnings: Sequence[str], folder: str | None, job_id: int | None,
) -> int:  # fmt: skip
    """Inside the write transaction: a new model `source {op, params}`, `inputs`, `algo`."""
    return commit_block_model(conn, store, staged, name=name, source={"op": op, "params": dict(params)},
                              warnings=warnings, folder=folder, job_id=job_id, inputs=inputs, algo=ALGO)  # fmt: skip


def replace_in_place(
    conn: sa.Connection, store: BlobStore, staged: StagedCells, model_id: int, expected_version: int, *, op: str,
    params: Mapping[str, Any], inputs: Mapping[str, Any], warnings: Sequence[str],
) -> dict[str, Any]:  # fmt: skip
    """«Перестроить»: the rows and the fields of the model `model_id` are the new file; the id and the name stay,
    the version is + 1; the fields of other makers leave the meta. 409 `version_conflict {version}`; the temp file
    goes on a refusal. → {version, dropped: [names]}."""
    if staged.file is None or staged.v2 is None:
        raise RuntimeError("the cells were discarded")
    try:
        row = conn.execute(
            sa.select(objects.c.version, objects.c.meta)
            .where(objects.c.kind == bs.KIND, objects.c.id == model_id, objects.c.state == "ready")
            .with_for_update()
        ).first()
        if row is None:
            raise not_found()
        if int(row.version) != expected_version:
            raise version_conflict(int(row.version))
        old = bmeta.upgrade(row.meta)
        meta = bmeta.upgrade({
            "v": bs.META_V2, "grid": staged.grid, "rows": staged.cells, **staged.v2, "fields": staged.fields,
            "source": {"op": op, "params": dict(params)}, "warnings": list(warnings), "inputs": dict(inputs),
            "algo": ALGO,
        })  # fmt: skip
        kept = {f["name"].casefold() for f in meta["fields"]}
        dropped = [f["name"] for f in old["fields"] if f["name"].casefold() not in kept]
        conn.execute(
            sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == model_id)
        )
        store.commit_staged(conn, staged.file, media_type=bs.PARQUET_MEDIA, owner_kind=OWNER, owner_id=model_id,
                            role=bs.ROLE)  # fmt: skip
        staged.file = None
        version = int(row.version) + 1
        conn.execute(
            sa.update(objects).where(objects.c.id == model_id)
            .values(meta=meta, extent=bs.GridV2.of(staged.grid).extent(), version=version, updated_at=dialect.utcnow())
        )  # fmt: skip
        return {"version": version, "dropped": dropped}
    except BaseException:
        staged.discard()
        raise
