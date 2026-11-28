"""The restrictor of a block model: the share of every block that passes a filter (подшаг 17.8; ТЗ 7.31, 7.35 Д-02,
8.7.5; ADR 0023 §8, decisions 11–12; contract — `docs/progress/e17-api.md` §3 «blocks/restrict.py»).

    r = prepare(model, rules, conn=conn, store=store)     # the rules of `filters/schema.py` (None — all pass)
    for chunk, f in r.iter(model, extra=["AU"]):          # f float64 [chunk.n] ∈ [0; 1]; chunks of 2**20 rows
        ...                                               # a block passes where f > 0 (№ 65); the report weighs by f
    f = evaluate(model, rules, conn=conn, store=store)    # the whole column (8 bytes a row)
    test_rules(project, model_id, rules) -> {"passed", "total", "weight", "warnings"}   # «Проверить на модели»

The share f ∈ [0; 1] of a block (ADR 0023 §8):
  * `expr`, `cond` — 0 or 1 (an expression of 8.7.4 over the fields of the model through `calc.compile`; it must be
    a condition: a number is 422 `filter_rules`). The calculator is two-valued: a comparison with a gap is false, so
    `AU > 1` does not pass a block without AU and `НЕ (AU > 1)` does;
  * `and` — the product of the shares, `or` — the maximum, `not` — 1 − f (the inverse restrictor is 1 − f);
  * `surface` — the height of the surface in the plan at the centre of the block (`geo/dz_kernels.py`): «по центру» —
    1 where the centre is on the needed side (a centre exactly on the surface is «above»), «по доле» — the share of the
    height of the block below the surface, clip((z_surface − z_bottom)/height; 0; 1), «above» is 1 minus it. No
    surface under the block (out of its plan, a hole) — 0, with a warning;
  * `wireframe` — the kernels of 8.7.3 are joined by 17.20 (`LEAVES["wireframe"]`); until then the share is 0 with a
    warning, as for a leaf without an id or one whose wireframe the project has lost (a filter of a `.bfggis` that did
    not carry it): the leaf is «недоступен» and nothing passes it.

  * `slab` (Э20, ADR 0027 decision 9) — a layer along x, y, z (axes of the project) or u, v (axes of the swath frame
    of 8.8.3, `frame` of the leaf): «centre» — 1 where from <= c < to (c — the centre of the block along the axis),
    else 0; «share» (z only) — the part of the height of the block inside [from; to], overlap / height (the model is
    turned about Z only, ADR 0023: the height is along Z). Adjacent layers sum to 1 for every block.

Whole subtrees without a wireframe, a surface or a slab are compiled to one expression (the same product and maximum,
one pass). A field the model lacks — 422 `filter_field_missing {field, model}`. Memory: a chunk of `CHUNK` rows;
`cancel` (a function that raises) is called between the chunks. A filter on two models is the direct expression
(Д-02): the fields are looked up by name in each model alone.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.calc import ExprError, compile
from bf_core.calc.runtime import ExprDataError
from bf_core.db import dialect
from bf_core.storage.blobs import BlobStore

from . import list_layout as ll
from .filters import schema as fs
from .model import ANY, CHUNK, Chunk, Model

Weights = Callable[[Chunk], np.ndarray]
Cancel = Callable[[], None]


# --- errors -----------------------------------------------------------------------------------------------------------


def field_missing(name: str, model: str) -> ApiError:
    return ApiError(
        422,
        "filter_field_missing",
        f"В модели «{model}» нет поля «{name}», которое нужно фильтру.",
        "Выберите другую модель или другой фильтр, либо добавьте поле в модель.",
        {"field": name, "model": model},
    )


def _not_condition(path: str) -> ApiError:
    return fs.bad(path, "Выражение фильтра должно быть условием, а не числом.",
                  "Допишите сравнение, например AU > 1, или условие IF(...).")  # fmt: skip


# --- the context of a build -------------------------------------------------------------------------------------------


@dataclass
class Build:
    """What a builder of a leaf sees: the model, the connection and the store for reading its objects, the list of
    warnings the result keeps (`Restrictor.warnings`)."""

    model: Model
    conn: sa.Connection
    store: BlobStore
    warnings: list[str] = field(default_factory=list)
    fields: dict[str, None] = field(default_factory=dict)  # the fields the shares read, in the order of first use

    def warn(self, text: str) -> None:
        if text not in self.warnings:
            self.warnings.append(text)

    def schema(self) -> dict[str, Any]:
        return {
            str(f["name"]): (list(f.get("labels") or []) if f.get("kind") == "category" else "num")
            for f in self.model.fields
        }

    def name_of(self, object_id: int) -> str:
        from bf_core.wireframes import store as wf

        try:
            return str(wf.wireframe_info(self.conn, object_id)["name"])
        except ApiError:
            return f"№ {object_id}"


Builder = Callable[[Build, Mapping[str, Any], str], Weights]


def _zero(chunk: Chunk) -> np.ndarray:
    return np.zeros(chunk.n, dtype=np.float64)


def _unavailable(b: Build, kind: str, body: Mapping[str, Any]) -> Weights:
    what = "каркаса" if kind == "wireframe" else "поверхности"
    b.warn(f"Правило {what} недоступно: в проекте нет объекта № {body.get('id')}. Блоки его не проходят.")
    return _zero


def _wireframe(b: Build, body: Mapping[str, Any], path: str) -> Weights:
    """The leaf «каркас»: the point of extension of 17.20 (`LEAVES["wireframe"] = …`; the builder returns a function
    chunk → float64 [n] share, «по центру» — parity of the ray, «по доле» — the kernels of 8.7.3 with `m`)."""
    if body.get("id") is None:
        return _unavailable(b, "wireframe", body)
    b.warn(f"Правило каркаса «{b.name_of(int(body['id']))}» в этой сборке ещё не считается: доля 0.")
    return _zero


def _surface(b: Build, body: Mapping[str, Any], path: str) -> Weights:
    """The leaf «поверхность» (module docstring)."""
    from bf_core.geo import dz_kernels
    from bf_core.wireframes import store as wf

    oid = body.get("id")
    if oid is None:
        return _unavailable(b, "surface", body)
    try:
        xyz, tri = wf.read_wireframe(b.conn, b.store, int(oid))
    except ApiError as e:
        if e.code != "wireframe_not_found":
            raise
        return _unavailable(b, "surface", body)
    name = b.name_of(int(oid))
    dz_kernels.warm()
    index = dz_kernels.surface_index(xyz, tri)
    del xyz, tri
    if not index.triangles:
        b.warn(f"У поверхности «{name}» нет треугольников в плане: доля 0.")
        return _zero
    above, frac, grid = body["side"] == "above", body["by"] == "frac", b.model.grid
    if frac and b.model.listed:  # a list of blocks: «по центру», the share is 0 or 1 (ADR 0030 decision 11)
        frac = False
        b.warn(f"Поверхность «{name}»: у модели-списка блоков доля считается по центру блока (0 или 1).")

    def run(chunk: Chunk) -> np.ndarray:
        local = ll.local_centres(grid, chunk)
        centre = grid.to_project(local)
        dz = index.dz(centre)  # z of the centre − z of the surface; NaN — no surface there
        known = ~np.isnan(dz)
        if not known.all():
            b.warn(f"Часть блоков вне плана поверхности «{name}» или над её отверстием: для них доля 0.")
        out = np.zeros(chunk.n, dtype=np.float64)
        if frac:
            box = ll.boxes(grid, chunk)
            height = box[:, 5] - box[:, 2]
            # the turn is about Z: z_surface − z_bottom = (z_centre − dz) − (z_centre − height/2) = height/2 − dz
            below = np.clip((height * 0.5 - dz) / height, 0.0, 1.0)
            out[known] = (1.0 - below if above else below)[known]
        else:
            out[known] = (dz[known] >= 0.0) if above else (dz[known] < 0.0)
        return out

    return run


def _slab(b: Build, body: Mapping[str, Any], path: str) -> Weights:
    """The leaf «слой» (module docstring)."""
    lo, hi, axis, share = float(body["from"]), float(body["to"]), str(body["axis"]), body["by"] == "share"
    grid = b.model.grid
    direction: np.ndarray | None = None
    if axis in ("u", "v"):
        from bf_core.stats import swath_frame

        fr = body.get("frame") or {}
        frame = swath_frame.frame(fr.get("azimuth", 0.0), fr.get("dip", 0.0), fr.get("roll", 0.0))
        direction = frame.u if axis == "u" else frame.v

    def run(chunk: Chunk) -> np.ndarray:
        centre = grid.to_project(ll.local_centres(grid, chunk))
        if share:
            box = ll.boxes(grid, chunk)
            height = box[:, 5] - box[:, 2]
            # the turn is about Z: the block spans centre_z ± height/2 in the project
            top = np.minimum(centre[:, 2] + height * 0.5, hi)
            bottom = np.maximum(centre[:, 2] - height * 0.5, lo)
            out = np.zeros(chunk.n, dtype=np.float64)
            np.divide(np.maximum(top - bottom, 0.0), height, out=out, where=height > 0.0)
            return np.clip(out, 0.0, 1.0)
        if direction is not None:
            from bf_core.stats import swath_frame

            c = swath_frame.project(centre, direction)
        else:
            c = centre[:, "xyz".index(axis)]
        return ((c >= lo) & (c < hi)).astype(np.float64)

    return run


LEAVES: dict[str, Builder] = {"wireframe": _wireframe, "surface": _surface, "slab": _slab}


# --- the tree ---------------------------------------------------------------------------------------------------------


def _program(b: Build, source: Any, path: str, text: str | None = None) -> Weights:
    """A condition of 8.7.4 as a function chunk → 0.0 / 1.0."""
    try:
        prog = compile(source, b.schema()) if text is None else compile(source, b.schema(), text=text)
    except ExprError as e:
        raise fs.expr_error(e, path) from e
    if prog.out_kind != "bool":
        raise _not_condition(path)
    for name in prog.fields_used:
        b.fields.setdefault(name, None)

    def run(chunk: Chunk) -> np.ndarray:
        try:
            got = prog.eval_chunk(chunk.cols, chunk.n)
        except ExprDataError as e:
            raise ApiError(422, ExprDataError.code, str(e), e.hint, {"param": path}) from e
        return got.astype(np.float64)

    return run


def _node(b: Build, node: Mapping[str, Any], path: str) -> Weights:
    if "expr" in node:
        return _program(b, node["expr"], f"{path}.expr")
    pure = fs._as_expr(node)
    if pure is not None:  # a condition or a group of them: one expression, one pass
        return _program(b, pure, path, text=pure.to_text())
    for kind, build in LEAVES.items():
        if kind in node:
            return build(b, node[kind], f"{path}.{kind}")
    kids = [_node(b, item, f"{path}.items.{i}") for i, item in enumerate(node["items"])]
    op = node["op"]
    if op == "not":
        only = kids[0]
        return lambda chunk: 1.0 - only(chunk)  # type: ignore[no-any-return]
    combine = np.multiply if op == "and" else np.maximum

    def run(chunk: Chunk) -> np.ndarray:
        out = kids[0](chunk)
        for kid in kids[1:]:
            out = combine(out, kid(chunk))
        return out  # type: ignore[no-any-return]

    return run


def _check_fields(model: Model, rules: Mapping[str, Any]) -> None:
    have = {str(f["name"]).casefold() for f in model.fields}
    for name in fs.fields_of(rules):
        if name.casefold() not in have:
            raise field_missing(name, model.name)


# --- the restrictor ---------------------------------------------------------------------------------------------------


@dataclass
class Restrictor:
    """The shares of the blocks of one model for one tree. `fields` — what must be read for it (stored names);
    `warnings` — the Russian lines for the user (they grow while the chunks are counted)."""

    fields: list[str]
    warnings: list[str]
    _fn: Weights | None

    def weights(self, chunk: Chunk) -> np.ndarray:
        """float64 [chunk.n] ∈ [0; 1], never NaN. `chunk` must hold `fields`."""
        if self._fn is None:
            return np.ones(chunk.n, dtype=np.float64)
        return self._fn(chunk)

    def mask(self, chunk: Chunk) -> np.ndarray:
        """bool [chunk.n]: the block passes (f > 0, № 65)."""
        return self.weights(chunk) > 0.0

    def iter(
        self, model: Model, *, extra: Sequence[str] = (), chunk: int = CHUNK, cancel: Cancel | None = None
    ) -> Iterator[tuple[Chunk, np.ndarray]]:
        """(chunk, f) over the whole model; the chunk holds `fields` and `extra`."""
        names = list(dict.fromkeys([*self.fields, *extra]))
        for ch in model.iter(names, chunk):
            if cancel is not None:
                cancel()
            yield ch, self.weights(ch)


def prepare(model: Model, rules: Mapping[str, Any] | None, *, conn: sa.Connection, store: BlobStore) -> Restrictor:
    """The restrictor of `rules` (the tree of a filter or a quick restrictor; None — everything passes) on `model`.
    422 `filter_field_missing {field, model}`, `filter_rules`, `expr_syntax`/`expr_field`/`expr_type`."""
    from .fill import leaf  # noqa: F401  (registers `LEAVES["wireframe"]`; here, not at the top: no import cycle)

    if rules is None:
        return Restrictor([], [], None)
    tree = fs.normalize_rules(rules)
    _check_fields(model, tree)
    b = Build(model, conn, store)
    fn = _node(b, tree, "rules")
    return Restrictor(list(b.fields), b.warnings, fn)


def combine(*nodes: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The tree for a filter and a quick restrictor together: AND (the product of the shares); None where nothing is
    asked."""
    given = [dict(n) for n in nodes if n is not None]
    if not given:
        return None
    return given[0] if len(given) == 1 else {"op": "and", "items": given}


def evaluate(
    model: Model,
    node: Mapping[str, Any] | None,
    *,
    conn: sa.Connection,
    store: BlobStore,
    cancel: Cancel | None = None,
) -> np.ndarray:
    """float64 [rows] ∈ [0; 1]: the share of every row, in the order of the file (None — all 1.0)."""
    r = prepare(model, node, conn=conn, store=store)
    out = np.empty(model.rows, dtype=np.float64)
    for ch, f in r.iter(model, cancel=cancel):
        out[ch.start : ch.start + ch.n] = f
    return out


def test_rules(project: Any, model_id: int, rules: Mapping[str, Any]) -> dict[str, Any]:
    """«Проверить на модели»: `{passed, total, weight, warnings}` — blocks with f > 0, all blocks, Σ f (the weight of
    the report, № 65), the warnings of the leaves."""
    store = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        model = Model.open(conn, store, model_id, ANY)
        r = prepare(model, rules, conn=conn, store=store)
    passed, weight = 0, 0.0
    for _, f in r.iter(model):
        passed += int(np.count_nonzero(f > 0.0))
        weight += float(f.sum())
    return {"passed": passed, "total": model.rows, "weight": weight, "warnings": list(r.warnings)}


# pytest must not take the function for a test of this module when it is imported by name
test_rules.__test__ = False  # type: ignore[attr-defined]
