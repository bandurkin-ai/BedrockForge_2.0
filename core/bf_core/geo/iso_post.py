"""The surface after the isosurface: the checks and the optional steps of 8.9.3 п. 7–8 (ADR 0026 решение 13; вопросы
№ 100, № 102).

    postprocess(v, t, *, h, post, clip=None, boundary=None, threads=1, tick=None) -> PostResult
    mesh_failure(v, t, *, h) -> str | None      why the surface is no closed solid (Russian), None — it passed
    PostResult(vertices float64 V×3, triangles int32 T×3, removed_parts, simplified, journal)
    IsoError(code, message, hint)               `iso_check`: the checks failed — the wireframe is not written

The steps, in this order, each only when asked (`post`: `v_min`, `simplify`, `clip`; a `Post` of `model.spec_parts`):
  1. parts below V_min: every connected part with |volume| < `v_min` m³ is removed — an island and a cavity alike
     (a cavity is a part turned inwards: its signed volume is negative);
  2. pyfqmr (`simplify`): a first target of a quarter of the triangles; the deviation of the vertices of the surface
     as it was from the simplified one (`geo.deviation.max_deviation`) must be at most 0.1·h and the result must stay
     a closed oriented manifold; else a gentler target (at most three repeats), then the surface as it was — and a
     line of the journal says so;
  3. the cut (`clip`): "boundary" / "wireframe" — `clip` is a closed solid, the result is the intersection
     (`solids.boolean`); "dtm" — `clip` is an open sheet, the body under it is `clip_sheet.sweep_solid` downwards and
     the result is the intersection with it (and with `boundary`, the solid of the extent, when it is given: «охват ∩
     тело под ЦТМ», вопрос № 100). The result of a boolean is `solids.canonical` — the same bytes whatever the
     threads of manifold3d;
  4. the checks of п. 7 on the final surface (`mesh_failure`): closed (every edge in two triangles), the normals
     consistent, no self-intersections, the signed volume above 0. A failure is `IsoError("iso_check")`. Before a cut
     the surface is checked whole (a boolean takes sound solids); after it the crossings are not looked for again: the
     result of manifold3d is a manifold clear of them, and the float64 test of `geo.checks` reads a sliver the cut
     leaves on a DTM (a triangle 8 m × 0.04 m, no crossing in exact arithmetic) as one.

An empty surface (nothing left after a step) is a result, not an error: no triangles, a line of the journal. A
step that is not asked for does not touch the arrays: they are compacted (the vertices no triangle uses are
dropped, the order of the others is kept) and nothing else changes.

`tick(fraction, message)` is called in the calling thread between the steps and in the long ones, and may raise to
cancel. `threads` goes to the deviation. pyfqmr, manifold3d and numba are imported by the first step that needs
them, not with this module.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from .iso_bricks import IsoError

if TYPE_CHECKING:
    from bf_core.model.spec_parts import Post

__all__ = ["IsoError", "PostResult", "mesh_failure", "postprocess"]

Tick = Callable[[float, str], None] | None

DEVIATION = 0.1  # ·h: the largest deviation after pyfqmr (8.9.3 п. 8)
SIMPLIFY_REPEATS = 3  # gentler attempts after the first (решение 13)
ATTEMPTS = ((0.25, 7.0), (0.45, 6.0), (0.65, 5.0), (0.85, 4.0))  # (the share of triangles to keep, aggressiveness)
SIMPLIFY_MIN = 200  # triangles: a smaller surface is not simplified
CHECK_HINT = "Увеличьте шаг h или сглаживание η, проверьте данные на противоречия и повторите: каркас не записан."


@dataclass(frozen=True)
class PostResult:
    vertices: np.ndarray  # float64 V×3, project coordinates
    triangles: np.ndarray  # int32 T×3
    removed_parts: int = 0  # parts removed under V_min
    simplified: bool = False  # pyfqmr took effect
    journal: list[str] = field(default_factory=list)  # Russian lines for the log of the model

    @property
    def empty(self) -> bool:
        return len(self.triangles) == 0


def _tick(tick: Tick, fraction: float, message: str) -> None:
    if tick is not None:
        tick(float(min(max(fraction, 0.0), 1.0)), message)


def _empty(journal: list[str], removed: int = 0) -> PostResult:
    return PostResult(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32), removed, False, journal)


def _fmt(x: float) -> str:
    """A number for the journal: «0,031», «1 250,4»."""
    return f"{x:,.3f}".replace(",", " ").replace(".", ",").rstrip("0").rstrip(",") if math.isfinite(x) else "∞"


def _arrays(v: Any, t: Any) -> tuple[np.ndarray, np.ndarray]:
    vv = np.ascontiguousarray(v, dtype=np.float64).reshape(-1, 3)
    tt = np.ascontiguousarray(t, dtype=np.int32).reshape(-1, 3)
    if len(tt) and (int(tt.min()) < 0 or int(tt.max()) >= len(vv)):
        raise IsoError("iso_check", "Треугольники поверхности ссылаются на несуществующие вершины.", CHECK_HINT)
    return vv, tt


def _local(v: np.ndarray) -> np.ndarray:
    """The vertices relative to the middle of the box: the volumes are taken in small numbers (UTM)."""
    return v - (v.min(axis=0) + v.max(axis=0)) / 2.0 if len(v) else v


# --- the checks ----------------------------------------------------------------------------------------------


def mesh_failure(v: Any, t: Any, *, h: float, tick: Tick = None, intersections: bool = True) -> str | None:
    """Why the surface is not a closed outward solid (8.9.3 п. 7; Russian, without the final full stop), or None.
    The checks are scale-free; `h` is the step of the surface (it stays in the contract: the triangles under
    1e-12·h² were dropped by the isosurface). `intersections=False` skips the self-intersections (the result of a
    boolean: see `postprocess`)."""
    from bf_core.geo import checks, solids
    from bf_core.geo.meshes import edge_summary

    vv, tt = _arrays(v, t)
    if len(tt) == 0:
        return "в поверхности нет треугольников"
    open_edges, nonmanifold = edge_summary(tt, len(vv))
    if open_edges:
        return f"поверхность не замкнута: открытых рёбер — {open_edges}"
    if nonmanifold:
        return f"поверхность не многообразна: рёбер, общих для трёх и более треугольников, — {nonmanifold}"
    conflicts = checks.orientation_conflicts(tt, len(vv))
    if conflicts:
        return f"нормали соседних треугольников не согласованы (рёбер — {conflicts})"
    if intersections:
        _tick(tick, 0.0, "Проверка: самопересечения")
        pairs = checks.self_intersections(
            vv,
            tt,
            limit=1,
            samples=1,
            progress=None if tick is None else (lambda f: _tick(tick, f, "Проверка: самопересечения")),
        ).pairs
        if pairs:
            return f"поверхность пересекает сама себя: пар треугольников — {pairs}"
    volume = solids.signed_volume(_local(vv), tt)
    if not volume > 0.0:
        return f"объём тела не положителен ({_fmt(volume)} м³): нормали направлены внутрь"
    return None


def _check(v: np.ndarray, t: np.ndarray, h: float, tick: Tick, intersections: bool = True) -> None:
    why = mesh_failure(v, t, h=h, tick=tick, intersections=intersections)
    if why is not None:
        raise IsoError("iso_check", f"Поверхность не прошла проверку: {why}.", CHECK_HINT)


# --- step 1: V_min --------------------------------------------------------------------------------------------


def _compact(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    from bf_core.geo.solids import compact

    cv, ct = compact(v, t)
    return cv, np.ascontiguousarray(ct, dtype=np.int32)


def _drop_small_parts(
    v: np.ndarray, t: np.ndarray, v_min: float, journal: list[str]
) -> tuple[np.ndarray, np.ndarray, int]:
    """Removes the connected parts whose |volume| is below `v_min`; → (vertices, triangles, how many parts)."""
    from bf_core.geo.solids import part_labels

    labels = part_labels(t, len(v))[t[:, 0]]  # the part of every triangle
    parts, inverse = np.unique(labels, return_inverse=True)
    p = _local(v)
    a, b, c = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
    six = np.einsum("ij,ij->i", a, np.cross(b, c))
    volume = np.bincount(inverse, weights=six, minlength=len(parts)) / 6.0
    small = np.abs(volume) < v_min
    if not small.any():
        return v, t, 0
    islands, cavities = int((small & (volume > 0)).sum()), int((small & (volume <= 0)).sum())
    lost = float(np.abs(volume[small]).sum())
    keep = ~small[inverse]
    cv, ct = _compact(v, t[keep])
    pieces = []
    if islands:
        pieces.append(f"островов — {islands}")
    if cavities:
        pieces.append(f"полостей — {cavities}")
    journal.append(f"Удалены части меньше V_min = {_fmt(v_min)} м³: {', '.join(pieces)} (суммарно {_fmt(lost)} м³).")
    return cv, ct, int(small.sum())


# --- step 2: pyfqmr -------------------------------------------------------------------------------------------


def _decimate(
    local: np.ndarray, t: np.ndarray, target: int, aggressiveness: float
) -> tuple[np.ndarray, np.ndarray] | None:
    """pyfqmr: quadric edge collapse to about `target` triangles; None when it did not make the mesh smaller. The
    arrays are copies: pyfqmr needs writable ones of its own (ТЗ 17.3.6)."""
    import pyfqmr

    s = pyfqmr.Simplify()
    s.setMesh(np.array(local, dtype=np.float64, order="C"), np.array(t, dtype=np.int32, order="C"))
    s.simplify_mesh(target_count=max(int(target), 4), aggressiveness=float(aggressiveness), preserve_border=True,
                    verbose=False)  # fmt: skip
    out_v, out_t, _ = s.getMesh()
    v = np.ascontiguousarray(out_v, dtype=np.float64).reshape(-1, 3)
    tt = np.ascontiguousarray(out_t, dtype=np.int32).reshape(-1, 3)
    if len(tt) == 0 or len(tt) >= len(t) or not np.isfinite(v).all() or int(tt.max()) >= len(v) or int(tt.min()) < 0:
        return None
    return v, tt


def _sound(v: np.ndarray, t: np.ndarray) -> bool:
    """The simplified surface is still a closed oriented manifold with a positive volume."""
    from bf_core.geo import checks, solids
    from bf_core.geo.meshes import edge_summary

    cv, ct = _compact(v, t)
    return (
        edge_summary(ct, len(cv)) == (0, 0)
        and checks.orientation_conflicts(ct, len(cv)) == 0
        and solids.signed_volume(_local(cv), ct) > 0.0
    )


def _simplify(
    v: np.ndarray, t: np.ndarray, h: float, threads: int, journal: list[str], tick: Tick
) -> tuple[np.ndarray, np.ndarray, bool]:
    from bf_core.geo.deviation import max_deviation

    if len(t) < SIMPLIFY_MIN:
        journal.append("Упрощение пропущено: в поверхности слишком мало треугольников.")
        return v, t, False
    origin = np.round((v.min(axis=0) + v.max(axis=0)) / 2.0)
    local = v - origin  # pyfqmr works in small numbers
    tolerance = DEVIATION * h
    worst = 0.0
    for k, (share, aggressiveness) in enumerate(ATTEMPTS[: SIMPLIFY_REPEATS + 1]):
        _tick(tick, 0.2 + 0.1 * k, f"Упрощение: попытка {k + 1}")
        made = _decimate(local, t, int(len(t) * share), aggressiveness)
        if made is None:
            continue
        sv, st = made
        if not _sound(sv, st):
            worst = math.inf
            continue
        deviation = max_deviation(local[np.unique(t)], sv, st, threads=threads)
        if deviation <= tolerance:
            cv, ct = _compact(sv + origin, st)
            journal.append(
                f"Упрощение: треугольников {len(t)} → {len(ct)}, наибольшее отклонение {_fmt(deviation)} м "
                f"(допуск 0,1·h = {_fmt(tolerance)} м)."
            )
            return cv, ct, True
        worst = max(worst, deviation)
    journal.append(
        f"Упрощение отменено: за {SIMPLIFY_REPEATS + 1} попытки отклонение не уложилось в 0,1·h = {_fmt(tolerance)} м "
        f"(худшее — {_fmt(worst)} м); поверхность оставлена без упрощения."
    )
    return v, t, False


# --- step 3: the cut ------------------------------------------------------------------------------------------


def _solid_of(clip: Any, what: str) -> tuple[np.ndarray, np.ndarray]:
    from bf_core.geo.solids import mesh_arrays

    return mesh_arrays(clip, what)


def _under_dtm(dtm: Any, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The body under a DTM: the sheet swept downwards below the lowest of the surface and of the sheet."""
    from bf_core.geo.clip_sheet import sweep_solid

    sv, st = _solid_of(dtm, "ЦТМ")
    if len(st) == 0:
        from bf_core.geo.solids import empty_error

        raise empty_error("ЦТМ")
    depth = float(sv[:, 2].max() - min(float(sv[:, 2].min()), float(v[:, 2].min())))
    return sweep_solid((sv, st), (0.0, 0.0, -1.0), depth * 1.1 + 1.0, name="ЦТМ")


def _clip(
    v: np.ndarray, t: np.ndarray, kind: str, clip: Any, boundary: Any, h: float, journal: list[str], tick: Tick
) -> PostResult | None:
    """The surface cut by the body of `kind`; None — there is nothing to cut with (the extent was not given)."""
    from bf_core.geo import solids

    if kind == "boundary" and clip is None:
        clip = boundary  # the extent may come under either name
        boundary = None
    if clip is None:
        if kind == "boundary":
            return None  # no solid of the extent: the field closed the surface
        what = "каркас" if kind == "wireframe" else "ЦТМ"
        raise IsoError(
            "iso_clip_missing",
            f"Для обрезки поверхности не передан {what}.",
            "Укажите каркас или ЦТМ обрезки в постобработке модели.",
        )
    _check(v, t, h, None)  # a boolean takes a sound solid and gives one: the result is not tested for crossings again
    names = {"boundary": "Охват модели", "wireframe": "Каркас обрезки", "dtm": "Тело под ЦТМ"}
    bodies = [(v, t), _under_dtm(clip, v) if kind == "dtm" else _solid_of(clip, names[kind])]
    labels = ["Поверхность модели", names[kind]]
    if kind == "dtm" and boundary is not None:
        bodies.append(_solid_of(boundary, names["boundary"]))
        labels.append(names["boundary"])
    before = abs(solids.signed_volume(_local(v), t))
    _tick(tick, 0.6, "Обрезка")
    rv, rt, report = solids.boolean_many(bodies, "intersection", names=labels)
    what = {"boundary": "охватом модели", "wireframe": "каркасом", "dtm": "ЦТМ"}[kind]
    if report["empty"] or report["flat"]:
        journal.append(f"Обрезка {what}: от поверхности ничего не осталось.")
        return _empty(journal)
    cv, ct = solids.canonical(rv, rt)
    journal.append(f"Обрезка {what}: объём {_fmt(before)} → {_fmt(report['volume'])} м³.")
    return PostResult(cv, ct, journal=journal)


# --- the whole -------------------------------------------------------------------------------------------------


def postprocess(
    v: Any,
    t: Any,
    *,
    h: float,
    post: Post,
    clip: tuple[Any, Any] | None = None,
    boundary: tuple[Any, Any] | None = None,
    threads: int = 1,
    tick: Tick = None,
) -> PostResult:
    """V_min → pyfqmr → the cut → the checks of п. 7 (see the module). `post` is a `model.spec_parts.Post` (`v_min`,
    `simplify`, `clip`); `clip` — the solid of the wireframe or of the extent, or the open sheet of the DTM;
    `boundary` — the solid of the extent that goes with a DTM. IsoError `iso_check` — the surface failed the checks
    (the wireframe is not written); `solids.SolidError` — the body of the cut is no solid (its message names it)."""
    journal: list[str] = []
    vv, tt = _arrays(v, t)
    if len(tt) == 0:
        return _empty(["Поверхность пуста: тело не построено."])
    _tick(tick, 0.0, "Постобработка")
    vv, tt = _compact(vv, tt)
    removed = 0
    if post.v_min > 0.0:
        vv, tt, removed = _drop_small_parts(vv, tt, float(post.v_min), journal)
        if len(tt) == 0:
            journal.append("После удаления малых частей поверхность пуста.")
            return _empty(journal, removed)
    _tick(tick, 0.15, "Постобработка")
    simplified = False
    if post.simplify:
        vv, tt, simplified = _simplify(vv, tt, float(h), threads, journal, tick)
    cut = _clip(vv, tt, post.clip, clip, boundary, float(h), journal, tick) if post.clip != "none" else None
    if cut is not None:
        if cut.empty:
            return PostResult(cut.vertices, cut.triangles, removed, simplified, journal)
        vv, tt = cut.vertices, cut.triangles
    _tick(tick, 0.7, "Проверка поверхности")
    _check(
        vv, tt, float(h), None if tick is None else (lambda f, m: _tick(tick, 0.7 + 0.3 * f, m)), cut is None
    )  # the crossings: of a surface that no boolean made (manifold3d keeps its own result clear of them)
    _tick(tick, 1.0, "Постобработка")
    return PostResult(vv, tt, removed, simplified, journal)
