"""The stages of the jobs `implicit.build` and `implicit.update` (подшаг 19.12; ТЗ 8.9.3; ADR 0026 решения 12–15, 17–18,
22–26; docs/progress/e19-api.md §6): the data, the boundary, the groups in their order, the checks.

    prog = Progress(tick)                              # the stages of `spec.STAGES` by the messages of the pipeline
    pre = prefetch(conn, store, prep)                  # the geometry the model names, read once (a read transaction)
    points = load_points(project, prep.spec, prog)     # «Данные»: `data.load`
    built = build_groups(project, prep, pre, points, solver, ...)    # «Участки», «Изоповерхности», «Тела»
    check_outputs(built, prep.spec)                    # «Проверки»: п. 7 of 8.9.3 for what was made

Every domain group goes through `faults.build_with_faults` at once (`job_stack`, 19.36: the chronology, the stack of
relations and the faults need them together; a model without them is `bodies.build_bodies` bit for bit); the other
groups follow in their order: the shells (`model.shells`, 19.14), the veins (`veins.build_vein`), the systems of veins
(`job_stack.build_system`), the expansions (`expand.expand_surface` and `iso_post.postprocess`). Nothing is written
here: a cancel (`tick` raises) or a failure leaves nothing anywhere (the writing is `job_write.py`: one transaction at
the end). numba, manifold3d and pyfqmr come with the functions that need them, not with this module.
"""

from __future__ import annotations

import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError

from .spec_parts import refuse
from .update import BrickFiles, BrickStats, CachingSolver, Record

if TYPE_CHECKING:
    from bf_core.storage.blobs import BlobStore

    from .bodies import Output
    from .data import Points
    from .spec import Group, ModelSpec
    from .store_check import Prepared

Tick = Callable[[float | None, str | None], None]
S_DATA, S_PATCHES, S_ISO = "Данные", "Участки", "Изоповерхности"
S_BODIES, S_CHECK, S_WRITE = "Тела", "Проверки", "Запись"
#: the first word of a message of the pipeline → the stage it belongs to (the text of a stage starts with its name)
STAGE_OF = {"Данные": S_DATA, "Участки": S_PATCHES, "Изоповерхность": S_ISO, "Изоповерхности": S_ISO, "Тела": S_BODIES,
            "Постобработка": S_BODIES, "Проверка": S_BODIES, "Обрезка": S_BODIES, "Жила": S_BODIES,
            "Расширение": S_BODIES, "Разломы": S_PATCHES, "Система": S_BODIES, "Проверки": S_CHECK,
            "Запись": S_WRITE}  # fmt: skip
AT_DATA, AT_BUILD, AT_CHECK, AT_WRITE = 0.0, 0.04, 0.91, 0.94  # where the stages start in the progress of the job
RESIDUAL_LIMIT = 1.0e30  # a field that is not finite is a defect, not a result


# --- the progress -----------------------------------------------------------------------------------------------------


class Progress:
    """The progress of the job: the windows of the stages, the names of the stages in the messages, the seconds of
    each stage. `window(a, b)` is a tick of a stage that takes the share [a, b] of the whole."""

    def __init__(self, tick: Tick) -> None:
        self.tick = tick
        self.started = time.perf_counter()
        self.at, self.stage = self.started, S_DATA
        self.took: dict[str, float] = {}
        self.top = 0.0

    def __call__(self, progress: float | None = None, message: str | None = None) -> None:
        text = message
        if message is not None:
            head = message.split(":", 1)[0].split()[0] if message.strip() else ""
            stage = STAGE_OF.get(head)
            if stage is not None:
                tail = message.split(":", 1)[1].strip() if ":" in message else message[:1].lower() + message[1:]
                text = message if message.startswith(stage + ":") else f"{stage}: {tail}"
                if stage != self.stage:
                    now = time.perf_counter()
                    self.took[self.stage] = self.took.get(self.stage, 0.0) + now - self.at
                    self.at, self.stage = now, stage
        if progress is not None:  # never back: the sub-ranges of the stages add rounding noise
            self.top = max(self.top, min(max(float(progress), 0.0), 1.0))
        self.tick(self.top if progress is not None else None, text)

    def window(self, a: float, b: float) -> Callable[[float | None, str | None], None]:
        def inner(share: float | None = None, text: str | None = None) -> None:
            self(None if share is None else a + (b - a) * min(max(float(share), 0.0), 1.0), text)

        return inner

    def stages(self) -> dict[str, float]:
        now = time.perf_counter()
        took = {**self.took, self.stage: self.took.get(self.stage, 0.0) + now - self.at}
        return {**{k: round(v, 3) for k, v in took.items()}, "total": round(now - self.started, 3)}


# --- what the model names ---------------------------------------------------------------------------------------------


def not_ready(what: str, group: Group | None = None) -> ApiError:
    extra = {} if group is None else {"group": group.id}
    hint = "Уберите эти группы и отношения или дождитесь следующего выпуска."
    return ApiError(422, "not_ready", f"{what} пока не строится.", hint, extra)


def refuse_unready(spec: ModelSpec) -> None:
    """What of Э19б is refused before anything is read (e19-api §1): the relations, a cycle of the order, a fault that
    is not in the model (`job_stack.check_stack`, 19.36); every group and relation of Э19б is built since 19.36."""
    from .job_stack import check_stack

    check_stack(spec)


def anisotropy_of(conn: sa.Connection, spec: ModelSpec) -> tuple[Any, dict[str, Any] | None]:
    """(A, the `meta` of a structural trend) of the model: A is the global one (the trend or the angles by hand; None —
    the identity and for a structural trend), the meta is that of a structural trend (its axes are per patch, 19.9
    `anisotropy.local`: `Structural`), else None."""
    from bf_core.geostat import trend_store as ts

    from . import anisotropy

    meta = None
    if spec.trend_id is not None:
        _name, meta = ts.meta_of(conn, int(spec.trend_id))
    structural = anisotropy.kind_of(spec, meta) == anisotropy.STRUCTURAL
    return anisotropy.global_matrix(spec, meta), (dict(meta or {}) if structural else None)


def global_matrix(conn: sa.Connection, spec: ModelSpec) -> Any:
    """A of the model (None — the identity and a structural trend: `anisotropy_of`)."""
    return anisotropy_of(conn, spec)[0]


@dataclass
class Prefetch:
    """The geometry the model names, read once: the boundary solid, the clip, the wireframes of the expansions."""

    boundary: tuple[Any, Any]
    clip: tuple[Any, Any] | None
    expand: dict[int, tuple[Any, Any]] = field(default_factory=dict)
    matrix: Any = None
    trend: dict[str, Any] | None = None  # `meta` of the structural trend of the model


class Structural:
    """A_j of the patches of a structural trend for `bodies_parts.fit_group` (`matrices_of`; contract of 19.9): the
    source of the trend is read from the project at the first call; the axes of one set of centres are made once (the
    thresholds of a shell and the groups on one data set share the patches); the lines of the journal — those of
    `anisotropy.local` (a centre that took the nearest measurement) under the label of the group."""

    def __init__(self, project: Any, meta: dict[str, Any], threads: int, journal: list[str]) -> None:
        self.project, self.meta, self.threads, self.journal = project, meta, threads, journal
        self._source: Any = None
        self._memo: dict[bytes, Any] = {}
        self.seconds = 0.0  # the time of the axes (the source and `local`)

    def __call__(self, centres: Any, label: str = "", tick: Callable[[float], None] | None = None) -> Any:
        from bf_core.geostat import trend_struct as ts

        from . import anisotropy

        q = np.ascontiguousarray(centres, dtype=np.float64)
        key = q.tobytes()
        got = self._memo.get(key)
        if got is not None:
            return got
        started = time.perf_counter()
        report: dict[str, Any] = {}
        try:
            if self._source is None:
                self._source = ts.load_source(self.project, self.meta)
            mats = anisotropy.local(self.meta, q, source=self._source, threads=self.threads, tick=tick, report=report)
        except ValueError as err:  # `StatsError` of the trend: {code, message, hint} of the geostatistics
            raise ApiError(409 if getattr(err, "code", "") == "source_gone" else 422,
                           str(getattr(err, "code", "bad_param")), str(getattr(err, "message", None) or err),
                           str(getattr(err, "hint", "") or ""), {"param": "trend_id"}) from None  # fmt: skip
        self.seconds += time.perf_counter() - started
        prefix = f"{label}: " if label else ""
        self.journal.extend(f"{prefix}{line}" for line in report.get("journal", []))
        shown = f" по {report['measurements']} замерам" if report.get("measurements") else ""
        self.journal.append(f"{prefix}структурная анизотропия: свои оси в центрах {len(q)} участков{shown}.")
        self._memo[key] = mats
        return mats


def _under_dtm(base: tuple[Any, Any], dtm: tuple[Any, Any]) -> tuple[Any, Any]:
    """`base` ∩ the body under the DTM (вопрос № 100): the sheet swept down below the lowest point of both."""
    from bf_core.geo import clip_sheet, solids

    bv, bt = solids.mesh_arrays(base, "Граница модели")
    sv, st = solids.mesh_arrays(dtm, "ЦТМ")
    if len(st) == 0:
        raise solids.empty_error("ЦТМ")
    depth = float(sv[:, 2].max() - min(float(sv[:, 2].min()), float(bv[:, 2].min())))
    solid = clip_sheet.sweep_solid((sv, st), (0.0, 0.0, -1.0), depth * 1.1 + 1.0, name="ЦТМ")
    v, t, report = solids.boolean_many([(bv, bt), solid], "intersection", names=["Граница модели", "ЦТМ"])
    if report["empty"] or report["flat"]:
        raise refuse("boundary_empty", "Граница модели пуста: под ЦТМ от неё ничего не осталось.",
                     "Выберите ЦТМ, которая лежит над границей модели.", param="boundary.dtm_id")  # fmt: skip
    return solids.canonical(v, t)


def prefetch(conn: sa.Connection, store: BlobStore, prep: Prepared) -> Prefetch:
    """Inside a read transaction: the boundary solid (a box, a wireframe, either one under the DTM), the geometry of the
    clip and of the expanded wireframes, A of the model."""
    from bf_core.wireframes import store as wf

    from .bodies_parts import box_solid

    spec = prep.spec
    b = spec.boundary
    base = wf.read_wireframe(conn, store, b.wireframe_id) if b.wireframe_id is not None else box_solid(b.box or ())
    if b.dtm_id is not None:
        base = _under_dtm(base, wf.read_wireframe(conn, store, b.dtm_id))
    clip = None
    if spec.post.clip in ("wireframe", "dtm") and spec.post.clip_id is not None:
        clip = wf.read_wireframe(conn, store, spec.post.clip_id)
    expand = {int(g.params["wireframe_id"]): wf.read_wireframe(conn, store, int(g.params["wireframe_id"]))
              for g in spec.groups if g.type == "expand"}  # fmt: skip
    matrix, trend = anisotropy_of(conn, spec)
    return Prefetch(base, clip, expand, matrix, trend)


# --- the data ---------------------------------------------------------------------------------------------------------


def load_points(project: Any, spec: ModelSpec, prog: Progress) -> Points:
    """«Данные»: the points of the groups from the project (`data.load`): 422 `too_many_points`, `no_contacts`."""
    from . import data

    prog(AT_DATA, f"{S_DATA}: чтение скважин и ручных данных")
    return data.load(project, spec, cancel=prog.window(AT_DATA, AT_BUILD))


# --- the groups -------------------------------------------------------------------------------------------------------


@dataclass
class Part:
    """A solved field of a group for the files of the model. `wing` −1 — no fault divides it (the number of a wing
    field: `job_stack`); `rows` — the rows of the data it was solved from (None — every row of its group)."""

    group: int
    wing: int
    record: Record
    stats: dict[str, Any]
    rows: Any = None


@dataclass
class LineOut:
    """The lines where two veins of a system cross (a layer of strings of the outputs): `lines` — [{closed, points}]."""

    group: int
    a: str
    b: str
    lines: list[dict[str, Any]]


@dataclass
class Built:
    outputs: list[Output] = field(default_factory=list)
    journal: list[str] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
    time: dict[str, float] = field(default_factory=dict)
    balance: float | None = None
    boundary_volume: float | None = None
    bricks: BrickStats = field(default_factory=BrickStats)
    used: set[str] = field(default_factory=set)
    lines: list[LineOut] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)  # `stats` of the model besides the groups: stack, faults, pairs


def _finite(x: float) -> float | None:
    return float(x) if np.isfinite(x) else None


def _part_stats(group: Group, record: Record, fit_stats: dict[str, Any] | None) -> dict[str, Any]:
    etas = [float(s.eta) for s in record.solutions]
    conds = [float(s.cond1) for s in record.solutions]
    fs = fit_stats or {}
    return {"group": group.id, "wing": None, "points": int(record.n_points), "patches": len(record.hashes),
            "h_bar": _finite(float(fs.get("h_bar", np.nan))),
            "max_residual": _finite(float(fs.get("max_residual", np.nan))),
            "cond_max": _finite(max(conds)) if conds else None, "eta_min": _finite(min(etas)) if etas else None,
            "eta_max": _finite(max(etas)) if etas else None, "support_share": None}  # fmt: skip


def _measures(v: Any, t: Any) -> tuple[float, float]:
    from bf_core.geo import solids

    origin = solids.local_origin(v)
    local = v - origin
    return abs(solids.signed_volume(local, t)), solids.surface_area(local, t)


def _why(err: Any) -> str:
    return str(err.message).removeprefix("Поверхность не прошла проверку: ").rstrip(".")


def _post_clip(spec: ModelSpec, pre: Prefetch) -> tuple[Any, Any]:
    """(`clip`, `boundary`) of `iso_post.postprocess` for a surface that is not a domain: the cut of 8.9.3 п. 8."""
    kind = spec.post.clip
    if kind == "boundary":
        return pre.boundary, None
    if kind == "wireframe":
        return pre.clip, None
    if kind == "dtm":
        return pre.clip, pre.boundary
    return None, None


def _solid_output(group: Group, v: Any, t: Any, name: str, stats: dict[str, Any]) -> Output:
    from .bodies import Output

    volume, area = _measures(v, t)
    return Output("body", group.id, None, None, name, np.asarray(v, dtype=np.float64), np.asarray(t, dtype=np.int32),
                  volume, area, {**stats, "triangles": int(len(t))})  # fmt: skip


def _expansion(spec: ModelSpec, group: Group, pre: Prefetch, h: float, levels: int, threads: int, prog: Tick,
               built: Built) -> None:  # fmt: skip
    from bf_core.geo import iso_post
    from bf_core.geo.iso_bricks import IsoError

    from . import expand
    from .bodies import _body_failure, _solids_error

    v, t = pre.expand[int(group.params["wireframe_id"])]
    try:
        mesh = expand.expand_surface(v, t, float(group.params["delta"]), h=h, levels=levels, threads=threads,
                                     tick=_sub(prog, 0.0, 0.7))  # fmt: skip
    except (expand.ExpandError, IsoError) as err:
        raise _solids_error(err, group) from None
    except ValueError as err:  # sdf.SdfError: an open or an empty wireframe
        raise _solids_error(err, group) from None
    if mesh.empty:
        built.journal.append(f"Группа «{group.name}»: расширение пусто — тело сжато до нуля.")
        return
    clip, boundary = _post_clip(spec, pre)
    try:
        res = iso_post.postprocess(mesh.vertices, mesh.triangles, h=h, post=spec.post, clip=clip, boundary=boundary,
                                   threads=threads, tick=_sub(prog, 0.7, 1.0))  # fmt: skip
    except IsoError as err:
        if err.code == "iso_check":
            raise _body_failure(group, _why(err)) from None
        raise _solids_error(err, group) from None
    built.journal.extend(f"Группа «{group.name}»: {line}" for line in res.journal)
    if len(res.triangles) == 0:
        built.journal.append(f"Группа «{group.name}»: после постобработки расширение пусто.")
        return
    built.outputs.append(_solid_output(group, res.vertices, res.triangles, group.name,
                                       {"delta": float(group.params["delta"])}))  # fmt: skip
    delta, volume = f"{float(group.params['delta']):g}", f"{built.outputs[-1].volume:.6g}"
    built.journal.append(f"Группа «{group.name}»: расширение на {delta} м, объём {volume} м³.".replace(".", ","))


def _vein(project: Any, spec: ModelSpec, group: Group, pre: Prefetch, h: float, threads: int, prog: Tick,
          built: Built) -> None:  # fmt: skip
    from bf_core.geo import iso_post
    from bf_core.geo.iso_bricks import IsoError

    from . import data, veins
    from .bodies import _body_failure, _solids_error

    hanging, footwall, notes = data.load_vein_sides(project, spec, group, _sub(prog, 0.0, 0.1))
    built.journal.extend(f"Группа «{group.name}»: {n}" for n in notes)
    try:
        body = veins.build_vein(hanging, footwall, group.params, h, threads=threads, tick=_sub(prog, 0.1, 0.8))
    except ApiError as err:
        err.message = err.message if err.message.startswith("Группа") else f"Группа «{group.name}»: {err.message}"
        err.extra.setdefault("group", group.id)
        raise
    if body is None:
        built.journal.append(f"Группа «{group.name}»: жила выклинилась на всём протяжении — тела нет.")
        return
    v, t = body.vertices, body.triangles
    built.journal.extend(f"Группа «{group.name}»: {n}" for n in body.notes)
    if spec.post.clip != "none":
        clip, boundary = _post_clip(spec, pre)
        try:
            res = iso_post.postprocess(v, t, h=h, post=replace(spec.post, v_min=0.0), clip=clip, boundary=boundary,
                                       threads=threads, tick=_sub(prog, 0.8, 1.0))  # fmt: skip
        except IsoError as err:
            if err.code == "iso_check":
                raise _body_failure(group, _why(err)) from None
            raise _solids_error(err, group) from None
        v, t = res.vertices, res.triangles
        built.journal.extend(f"Группа «{group.name}»: {line}" for line in res.journal)
        if len(t) == 0:
            built.journal.append(f"Группа «{group.name}»: после обрезки жилы не осталось.")
            return
    built.outputs.append(_solid_output(group, v, t, group.name, dict(body.stats)))
    volume = f"{built.outputs[-1].volume:.6g}".replace(".", ",")
    built.journal.append(f"Группа «{group.name}»: жила, объём {volume} м³.")


def _shells(
    spec: ModelSpec, group: Group, points: Points, pre: Prefetch, solver: CachingSolver, eta_of: Any,
    bricks_of: Callable[[Record, bytes], BrickFiles], threads: int, prog: Tick, built: Built,
    structural: Structural | None = None,
) -> None:
    """The shells of a group (`shells.build_shells`, 19.14): every threshold has its own store of bricks (the field
    is (level − RBF)·unit: the level and the unit are in the key of its bricks, with the patches)."""
    from . import data as md
    from . import shells, shells_parts

    gid = int(group.id or 0)
    data = points.of_group(gid)
    levels = shells_parts.shell_levels(group, data)
    indicator = group.params["mode"] == "indicator"
    p_level = float(group.params["p"] if group.params.get("p") is not None else 0.5) - 0.5

    def bricks(_group: Group, k: int) -> BrickFiles:
        rec = solver.last()
        assert rec is not None  # `fit_group` solved the field of this threshold (or the first one of a numeric shell)
        if indicator:
            shift, unit = p_level, shells_parts.unit_of(md.indicator(data.f, levels[k]))
        else:
            shift, unit = float(levels[k]), shells_parts.unit_of(data.f)
        return bricks_of(rec, struct.pack("<2d", shift, float(unit)))

    n0 = len(solver.records)
    res = shells.build_shells(spec, group, points, pre.boundary, clip=pre.clip, matrix=pre.matrix,
                              matrices_of=structural, eta_of=eta_of, solver=solver, brick_cache=bricks,
                              threads=threads, tick=prog)  # fmt: skip
    built.outputs.extend(res.outputs)
    built.journal.extend(res.journal)
    seen: set[int] = set()
    for (_g, _k), fit in sorted(res.fits_by_threshold.items()):
        rec = solver.record_of(fit.patches)
        if rec is None or id(rec) in seen or solver.records.index(rec) < n0:
            continue
        seen.add(id(rec))
        rec.group = gid
        built.parts.append(Part(gid, -1, rec, _part_stats(group, rec, fit.stats)))


def _sub(tick: Tick, a: float, b: float) -> Callable[[float | None, str | None], None]:
    def inner(share: float | None = None, text: str | None = None) -> None:
        tick(None if share is None else a + (b - a) * min(max(float(share), 0.0), 1.0), text)

    return inner


def build_groups(
    project: Any, prep: Prepared, pre: Prefetch, points: Points, solver: CachingSolver, *, folder: Path,
    model_id: int, read_bricks: bool, threads: int, prog: Progress,
) -> Built:  # fmt: skip
    """The groups of the model in their order into `Built` (the module text). `read_bricks` — «Обновить»: the files of
    the bricks are read as well as written."""
    from bf_core.geo.iso_bricks import Grid

    from .bodies import _boundary

    spec = prep.spec
    built = Built()
    assert spec.h is not None
    h, levels = float(spec.h), max(int(spec.levels), 1)
    lo, hi, _bv, _bt = _boundary(pre.boundary)  # the same box and grid as the isosurface of `build_bodies`
    grid = Grid.from_box(lo, hi, h, levels)
    eta_of = solver.eta_of if spec.smoothing.mode == "auto" else None
    structural = None if pre.trend is None else Structural(project, pre.trend, threads, built.journal)
    domains = [g for g in spec.groups if g.type == "domain"]
    others = [g for g in spec.groups if g.type in ("shell", "vein", "expand", "vein_system")]
    units = (1 if domains else 0) + len(others)
    span = (AT_CHECK - AT_BUILD) / max(units, 1)
    at = AT_BUILD

    def bricks_of(rec: Record, extra: bytes = b"") -> BrickFiles:
        return BrickFiles(folder, model_id, grid, rec, matrix=pre.matrix, read=read_bricks, used=built.used,
                          stats=built.bricks, extra=extra)  # fmt: skip

    def bricks(_group: Group) -> BrickFiles:
        rec = solver.last()
        assert rec is not None  # `fit_group` solved the field of this group a moment ago
        return bricks_of(rec)

    if domains:
        from .job_stack import build_domains

        build_domains(spec, pre, points, solver, bricks=bricks, eta_of=eta_of, structural=structural, threads=threads,
                      win=prog.window(at, at + span), built=built)  # fmt: skip
        at += span
    elif any(g.type == "fault" for g in spec.groups):
        built.journal.append("Разломы модели не использованы: нет доменов, которые они рассекают.")
    for g in others:
        win = prog.window(at, at + span)
        at += span
        win(0.0, f"{S_BODIES}: группа «{g.name}»")
        if g.type == "shell":
            _shells(spec, g, points, pre, solver, eta_of, bricks_of, threads, win, built, structural)
        elif g.type == "vein":
            _vein(project, spec, g, pre, h, threads, win, built)
        elif g.type == "vein_system":
            from .job_stack import build_system

            build_system(project, spec, g, pre, h, threads, win, built)
        else:
            _expansion(spec, g, pre, h, levels, threads, win, built)
        win(1.0, f"{S_BODIES}: группа «{g.name}» готова")
    return built


# --- the checks -------------------------------------------------------------------------------------------------------


def check_outputs(built: Built, spec: ModelSpec, prog: Progress) -> list[str]:
    """«Проверки» (8.9.3 п. 7) of what was made: every body is closed, manifold and has a positive volume; the
    volumes of the domains and «Прочее» add up to the boundary. A body that fails is 422 `iso_check` with its group.
    Returns the lines of the journal."""
    from bf_core.geo import meshes

    lines: list[str] = []
    bodies = [o for o in built.outputs if o.kind == "body"]
    for i, o in enumerate(bodies):
        prog(AT_CHECK + (AT_WRITE - AT_CHECK) * i / max(len(bodies), 1), f"{S_CHECK}: «{o.name}»")
        open_edges, bad_edges = meshes.edge_summary(o.triangles, len(o.vertices))
        why = None
        if open_edges or bad_edges:
            why = f"не замкнуто или не многообразно (открытых рёбер {open_edges}, неманифольдных {bad_edges})"
        elif not (o.volume is not None and o.volume > 0.0):
            why = "объём не положителен"
        if why is not None:
            raise refuse("iso_check", f"Тело «{o.name}» не прошло проверки: {why}.",
                         "Увеличьте шаг h или сглаживание η, проверьте данные на противоречия и повторите: каркас не "
                         "записан.", group=o.group)  # fmt: skip
    if built.balance is not None:
        lines.append(f"Сумма объёмов доменов и «Прочее» отличается от объёма границы на {built.balance:.3g} (доля).")
    prog(AT_WRITE, f"{S_CHECK}: готово")
    return lines


__all__ = ["Built", "Part", "Prefetch", "Progress", "Structural", "anisotropy_of", "build_groups", "check_outputs",
           "global_matrix", "load_points", "not_ready", "prefetch", "refuse_unready"]  # fmt: skip
