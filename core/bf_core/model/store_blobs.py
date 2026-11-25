"""The files of an implicit model (подшаг 19.11; ADR 0026 решение 23; docs/progress/e19-api.md §2.1): the blobs of the
roles `implicit_points` (Parquet zstd), `implicit_patches` (Parquet zstd) and `implicit_solution` (`.npy` float64),
staged outside the write transaction and moved into place inside it, the readers, and `load_fields` — the solved
groups the evaluation reads.

    staged = {ROLE_POINTS: stage_points(store, cols, groups=[1, 2], sources=["A-1 12,0–14,0 м"]), …}
    shas = commit(conn, store, model_id, staged)          # replaces the roles it names; the others stay
    fields = load_fields(conn, store, model_id)           # {(group, wing | None): GroupData}; 409 `not_built`

The columns and their types are `POINT_COLUMNS`, `PATCH_COLUMNS` (float64 everywhere a coordinate or a value is; a
gap in a value is NaN, never 0). The solution lies by the rows of `implicit_patches`: λ (n numbers) and c (4) from
`offset`; `load_fields` checks the three files against each other (a mismatch — 409 `not_built`: «устарела»).
Nothing heavy before BF-READY: numpy and pyarrow are imported in the functions.
"""

from __future__ import annotations

import io
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.db.schema import blob_owners

from . import store_base as sb
from .spec import refuse

if TYPE_CHECKING:
    import numpy as np

    from bf_core.storage.blobs import BlobStore

Staged = tuple[Path, str, int]
PARQUET, NPY = "application/vnd.apache.parquet", "application/octet-stream"
META_KEY = b"bf.implicit"
HASH_SIZE = 32  # SHA-256 of a patch (bytes)
#: column → numpy dtype of `implicit_points` (e19-api §2.1); `kind` — the index in `spec.POINT_TYPES`, `wing` −1 —
#: none (Э19б: 0 / 1), `src` −1 — no source, `from`/`to` — depths along the hole (NaN for a layer of strings),
#: `vertex` −1 — a hole
POINT_COLUMNS: dict[str, str] = {"x": "<f8", "y": "<f8", "z": "<f8", "f": "<f8", "kind": "i1", "group": "<i4",
                                 "wing": "i1", "src": "<i4", "from": "<f8", "to": "<f8", "vertex": "<i8"}  # fmt: skip
#: column → numpy dtype of `implicit_patches` (`hash` — fixed 32 bytes)
PATCH_COLUMNS: dict[str, str] = {"group": "<i4", "wing": "i1", "j": "<i4", "cx": "<f8", "cy": "<f8", "cz": "<f8",
                                 "r": "<f8", "n": "<i4", "offset": "<i8", "hash": "S32", "eta": "<f8",
                                 "cond": "<f8"}  # fmt: skip
#: the optional columns of `implicit_patches` of a model with a structural trend (19.12б, 19.9): A_j of the patch,
#: row-major (`a<i><k>` = A_j[i, k], float64). A model with a global A or none does not have them (the file is as it
#: was); `read_patches` takes the file with them or without; `load_fields` gives `GroupData.matrices` (None — none).
MATRIX_COLUMNS: dict[str, str] = {f"a{i}{k}": "<f8" for i in range(3) for k in range(3)}


def _broken(what: str) -> Any:
    return refuse("not_built", f"Результат модели повреждён: {what}.", "Выполните «Построить» заново.")


def _arrays(cols: Mapping[str, Any], spec: Mapping[str, str], what: str) -> dict[str, Any]:
    """The columns as numpy arrays of the stored types (a missing or a ragged one — ValueError: a bug of the job)."""
    import numpy as np

    out: dict[str, Any] = {}
    n = -1
    for name, dtype in spec.items():
        if name not in cols:
            raise ValueError(f"{what}: no column {name!r}")
        a = np.ascontiguousarray(cols[name], dtype=dtype)
        if a.ndim != 1 or (n >= 0 and len(a) != n):
            raise ValueError(f"{what}: column {name!r} has another length")
        n = len(a)
        out[name] = a
    return out


def _table(arrays: Mapping[str, Any], meta: Mapping[str, Any] | None) -> Any:
    import pyarrow as pa

    cols, names = [], []
    for name, a in arrays.items():
        cols.append(pa.array(a, type=pa.binary(HASH_SIZE)) if a.dtype.kind == "S" else pa.array(a))
        names.append(name)
    table = pa.table(cols, names=names)
    if meta is not None:
        table = table.replace_schema_metadata({META_KEY.decode(): json.dumps(meta, ensure_ascii=False)})
    return table


def _stage_parquet(store: BlobStore, table: Any) -> Staged:
    import pyarrow.parquet as pq

    store.tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = store.tmp_dir / f"implicit-{uuid.uuid4().hex}.parquet"
    try:
        pq.write_table(table, tmp, compression="zstd")
        return store.stage_file(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def stage_points(store: BlobStore, cols: Mapping[str, Any], *, groups: Sequence[int],
                 sources: Sequence[str]) -> Staged:  # fmt: skip
    """`implicit_points`: the points of the data (the order is the canonical one: by `group`, inside — ADR 0026
    решение 4); `groups` — the ids of the groups, `sources` — the names `src` numbers."""
    meta = {"v": 1, "groups": [int(g) for g in groups], "sources": [str(s) for s in sources]}
    return _stage_parquet(store, _table(_arrays(cols, POINT_COLUMNS, "implicit_points"), meta))


def stage_patches(store: BlobStore, cols: Mapping[str, Any]) -> Staged:
    """`implicit_patches`: a row per patch — the centre and the radius in the space of the octree, the number of
    points, the offset of its λ in the solution, the hash (32 bytes), η and the condition number. A structural trend
    adds `MATRIX_COLUMNS` (A_j of every patch): all nine or none."""
    spec = {**PATCH_COLUMNS, **MATRIX_COLUMNS} if any(k in cols for k in MATRIX_COLUMNS) else PATCH_COLUMNS
    return _stage_parquet(store, _table(_arrays(cols, spec, "implicit_patches"), None))


def stage_solution(store: BlobStore, solution: Any) -> Staged:
    """`implicit_solution`: f8[Σ(n + 4)] as `.npy` — λ then c of every row of `implicit_patches`."""
    import numpy as np

    buf = io.BytesIO()
    np.save(buf, np.ascontiguousarray(solution, dtype="<f8").reshape(-1), allow_pickle=False)
    return store.stage_bytes(buf.getvalue())


def discard(staged: Mapping[str, Staged]) -> None:
    """The temp files of a result that was not committed."""
    for tmp, _sha, _size in staged.values():
        tmp.unlink(missing_ok=True)


# --- the owner --------------------------------------------------------------------------------------------------------


def shas_of(conn: sa.Connection, model_id: int) -> dict[str, str]:
    """role → SHA-256 of the files of the model (a role it has none of is not in the answer)."""
    q = sa.select(blob_owners.c.role, blob_owners.c.sha256).where(
        blob_owners.c.owner_kind == sb.OWNER, blob_owners.c.owner_id == model_id,
        blob_owners.c.role.in_(sb.BLOB_ROLES))  # fmt: skip
    return {str(r.role): str(r.sha256) for r in conn.execute(q)}


def drop_roles(conn: sa.Connection, model_id: int, roles: Sequence[str] = sb.BLOB_ROLES) -> None:
    conn.execute(sa.delete(blob_owners).where(
        blob_owners.c.owner_kind == sb.OWNER, blob_owners.c.owner_id == model_id,
        blob_owners.c.role.in_(list(roles))))  # fmt: skip


def commit(conn: sa.Connection, store: BlobStore, model_id: int, staged: Mapping[str, Staged]) -> dict[str, str]:
    """Inside the write transaction: the staged files into place as the roles of the model, replacing the files of
    those roles (the old ones go with the next collection); returns role → sha for the roles given."""
    unknown = sorted(set(staged) - set(sb.BLOB_ROLES))
    if unknown:
        raise ValueError(f"not a role of an implicit model: {unknown}")
    drop_roles(conn, model_id, list(staged))
    return {role: store.commit_staged(conn, files, media_type=NPY if role == sb.ROLE_SOLUTION else PARQUET,
                                      owner_kind=sb.OWNER, owner_id=model_id, role=role)
            for role, files in staged.items()}  # fmt: skip


# --- readers ---------------------------------------------------------------------------------------------------------


def _read_parquet(store: BlobStore, sha: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    import numpy as np
    import pyarrow.parquet as pq

    path = store.path_of(sha)
    if not path.is_file():
        raise _broken("файл не найден")
    table = pq.read_table(path)
    raw = (table.schema.metadata or {}).get(META_KEY)
    cols = {name: table.column(name).to_numpy(zero_copy_only=False) for name in table.column_names}
    for name, a in cols.items():
        if a.dtype == object:  # `hash`: fixed-size binary comes as objects
            cols[name] = np.array([bytes(v) for v in a], dtype=f"S{HASH_SIZE}")
    return cols, None if raw is None else json.loads(raw)


def read_points(store: BlobStore, sha: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the columns of `implicit_points`, `{v, groups, sources}`)."""
    cols, meta = _read_parquet(store, sha)
    if meta is None or set(cols) != set(POINT_COLUMNS):
        raise _broken("точки данных")
    return {k: cols[k].astype(POINT_COLUMNS[k], copy=False) for k in POINT_COLUMNS}, meta


def read_patches(store: BlobStore, sha: str) -> dict[str, Any]:
    cols, _meta = _read_parquet(store, sha)
    wide = set(PATCH_COLUMNS) | set(MATRIX_COLUMNS)
    if set(cols) not in (set(PATCH_COLUMNS), wide):
        raise _broken("участки")
    kinds = {**PATCH_COLUMNS, **MATRIX_COLUMNS}
    return {k: cols[k].astype(kinds[k], copy=False) for k in kinds if k in cols}


def read_solution(store: BlobStore, sha: str) -> Any:
    import numpy as np

    path = store.path_of(sha)
    if not path.is_file():
        raise _broken("файл решения не найден")
    a = np.load(path, allow_pickle=False)
    if a.dtype != np.dtype("<f8") or a.ndim != 1:
        raise _broken("решение")
    return a


@dataclass(frozen=True)
class GroupData:
    """One solved group (or one wing of a fault group): its points, its patches and the solution of each patch."""

    group: int
    wing: int | None
    points: dict[str, np.ndarray]  # the columns of `implicit_points` of the group, in the stored order
    patches: dict[str, np.ndarray]  # the columns of `implicit_patches`, by `j`
    lam: list[np.ndarray]  # λ of each patch (views of the solution)
    c: np.ndarray  # f8[patches, 4]: c0, cx, cy, cz of each patch
    matrices: np.ndarray | None = None  # f8[patches, 3, 3]: A_j of a structural trend by `j` (None — no per-patch A)


def load_fields(conn: sa.Connection, store: BlobStore, model_id: int) -> dict[tuple[int, int | None], GroupData]:
    """The solved groups of a built model: {(group id, wing | None): GroupData}. 409 `not_built` — no result yet or
    the three files do not fit each other."""
    import numpy as np

    sb.stored(conn, model_id)
    shas = shas_of(conn, model_id)
    if set(shas) != set(sb.BLOB_ROLES):
        raise refuse("not_built")
    points, _meta = read_points(store, shas[sb.ROLE_POINTS])
    patches = read_patches(store, shas[sb.ROLE_PATCHES])
    solution = read_solution(store, shas[sb.ROLE_SOLUTION])
    n, off = patches["n"].astype(np.int64), patches["offset"]
    if len(n) and (off.min() < 0 or int((off + n + 4).max()) > len(solution) or int((n + 4).sum()) != len(solution)):
        raise _broken("решение не подходит участкам")
    out: dict[tuple[int, int | None], GroupData] = {}
    keys = sorted({(int(g), int(w)) for g, w in zip(patches["group"], patches["wing"], strict=True)})
    for g, w in keys:
        pm = (points["group"] == g) & (points["wing"] == w)
        qm = (patches["group"] == g) & (patches["wing"] == w)
        rows = np.flatnonzero(qm)
        rows = rows[np.argsort(patches["j"][rows], kind="stable")]
        lam = [solution[int(off[i]) : int(off[i] + n[i])] for i in rows]
        tails = [solution[int(off[i] + n[i]) : int(off[i] + n[i]) + 4] for i in rows]
        c = np.array(tails, dtype="<f8").reshape(-1, 4)
        mats = None
        if all(k in patches for k in MATRIX_COLUMNS):
            mats = np.stack([patches[k][rows] for k in MATRIX_COLUMNS], axis=1).reshape(-1, 3, 3)
        out[(g, None if w < 0 else w)] = GroupData(
            g, None if w < 0 else w, {k: v[pm] for k, v in points.items()},
            {k: v[rows] for k, v in patches.items()}, lam, c, mats)  # fmt: skip
    return out
