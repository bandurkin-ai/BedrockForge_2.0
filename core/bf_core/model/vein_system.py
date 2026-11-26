"""The system of veins (подшаг 19.35; ТЗ 7.35 Д-39; ADR 0026 решение 21; контракт docs/progress/e19-api.md §10).

    build_vein_system(veins, h, *, threads=1, tick=None, dense_max=DENSE_MAX) -> VeinSystemResult

`veins` is a sequence of `VeinInput(code, name, hanging, footwall, params)`, 2–20 of them; THE ORDER IS THE AGE: the
first vein is the youngest (as the groups of the stack, `stack.py`). The sides come from `data.load_vein_sides` (one
call per code), `params` are the `bound` / `polygon` / `offset` / `t_min` of 8.9.3 (`spec_parts._vein`).

1. Every vein is built whole by `veins.build_vein`: the raw body R_i (closed, outward normals). A vein pinched out
   everywhere (`None`) is left out and named in the journal; it is no error.
2. The younger vein cuts the older: the body of the vein j is R_j minus the raw bodies of all the veins younger than j
   (`solids.boolean_many(..., "difference")`; the younger are whole: nothing cuts them). A vein cut out entirely has no
   body (the journal says by which veins); the bodies are disjoint and in `solids.canonical` form.
3. The common body of the system is the union of the raw bodies (`boolean_many(..., "union")`, canonical), which
   equals the union of the cut ones: Σ volume(cut) = volume(common) within the tolerance of the boolean.
4. A pair of veins (a younger, b older) whose raw bodies meet gives a row of `pairs`: the codes `a`, `b`, `line_length`
   (the sum of the lengths of the polylines of `crossing.intersection_lines(R_a, R_b)`, a closed one with its closing
   segment) and `volume_common` (the volume of R_a ∩ R_b, BEFORE the cut). Pairs that do not meet are not listed. The
   lines come as float64 polylines in project coordinates, per pair (`lines`): the lines of different pairs are never
   merged. The common zone has no code of its own (вопрос № 94): it belongs to the younger vein.

Different veins have different codes (the key of the pair); the bytes of the result do not depend on `threads`
(1, 2, 4, 8: the work over pairs and over the cuts goes by the index, the results are collected by it) nor on the order
of the rows of the contacts (`build_vein` sorts them). Degenerate data are `{code, message, hint}` in Russian
(`spec.refuse`; the message names the vein). numba, manifold3d and pyfqmr are imported lazily (nothing at the top here).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError

from .spec_parts import VEINS_MAX, bad

F8 = np.ndarray
Tick = Callable[[float | None, str | None], None]

VEINS_MIN = 2
EMPTY_V = np.zeros((0, 3), dtype=np.float64)
EMPTY_T = np.zeros((0, 3), dtype=np.int32)


@dataclass(frozen=True)
class VeinInput:
    """One vein of the system: `hanging`, `footwall` [n, 3] float64 (project coordinates), `params` — 8.9.3."""

    code: str
    name: str
    hanging: Any
    footwall: Any
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VeinPart:
    """The result for one vein, in the input order. `vertices` / `triangles` — the cut body (empty when `empty`),
    `volume` of it, `raw_volume` before the cut (0.0 when the vein pinched out), `cut_by` — codes of the younger
    veins that cut it, `status`: `ok`, `pinched` (nothing is left by `t_min`), `cut_out` (the younger ones cover it)."""

    code: str
    name: str
    vertices: F8
    triangles: np.ndarray
    volume: float
    raw_volume: float
    cut_by: tuple[str, ...]
    status: str

    @property
    def empty(self) -> bool:
        return len(self.triangles) == 0


@dataclass(frozen=True)
class PairLines:
    """The lines where the raw bodies of a (younger a, older b) cross: [{"closed": bool, "points": (k, 3) float64}]."""

    a: str
    b: str
    lines: list[dict[str, Any]]


@dataclass(frozen=True)
class VeinSystemResult:
    """`parts` — by `veins` (the input order); `common` — (vertices, triangles, volume) of the union or None;
    `pairs` — [{a, b, line_length, volume_common}] (e19-api §10); `lines` — the polylines of the pairs that have any;
    `stats` — the numbers of the journal; `notes` — the journal in Russian."""

    parts: list[VeinPart]
    common: tuple[F8, np.ndarray, float] | None
    pairs: list[dict[str, Any]]
    lines: list[PairLines]
    stats: dict[str, Any]
    notes: list[str]


# --- helpers -------------------------------------------------------------------------------------------------------


def _beat(tick: Tick | None, share: float, text: str) -> None:
    if tick is not None:
        tick(share, text)


def _within(tick: Tick | None, start: float, stop: float) -> Tick | None:
    if tick is None:
        return None
    outer = tick
    return lambda share, text: outer(None if share is None else start + (stop - start) * share, text)


def _solids_error(err: Exception, what: str) -> ApiError:
    """`SolidError` of a boolean → ApiError that names the step."""
    code = str(getattr(err, "code", "solid_failed"))
    message = str(getattr(err, "message", str(err)))
    hint = str(getattr(err, "hint", "")) or "Проверьте данные жил или уменьшите шаг h."
    return ApiError(422, code, f"Система жил, {what}: {message}", hint)


def _named(err: ApiError, vein: VeinInput) -> ApiError:
    """An error of `build_vein` with the vein named; the machine fields are kept, `code` is added."""
    message = err.message if err.message.startswith("Жила") else f"Жила «{vein.name}»: {err.message}"
    return ApiError(err.status, err.code, message, err.hint, {**err.extra, "code": vein.code})


def _check(veins: Sequence[VeinInput]) -> None:
    if not VEINS_MIN <= len(veins) <= VEINS_MAX:
        raise bad("bad_param", f"В системе должно быть от {VEINS_MIN} до {VEINS_MAX} жил.",
                  "Добавьте или уберите жилы.", "veins")  # fmt: skip
    seen: set[str] = set()
    for i, v in enumerate(veins):
        if v.code in seen:
            raise bad("bad_param", f"Код жилы «{v.code}» повторяется.", "У каждой жилы свой код.", f"veins.{i}.code")
        seen.add(v.code)


def _boxes_meet(a: tuple[F8, np.ndarray], b: tuple[F8, np.ndarray]) -> bool:
    return bool(np.all(a[0].min(axis=0) <= b[0].max(axis=0)) and np.all(b[0].min(axis=0) <= a[0].max(axis=0)))


def line_length(lines: Sequence[dict[str, Any]]) -> float:
    """The total length of the polylines (a closed one — with its closing segment), accumulated by `math.fsum`."""
    parts: list[float] = []
    for line in lines:
        p = np.asarray(line["points"], dtype=np.float64)
        if len(p) < 2:
            continue
        q = np.concatenate([p, p[:1]]) if line.get("closed") else p
        parts.extend(np.linalg.norm(np.diff(q, axis=0), axis=1).tolist())
    return math.fsum(parts)


def _pair(raw_a: tuple[F8, np.ndarray], raw_b: tuple[F8, np.ndarray], codes: tuple[str, str]
          ) -> tuple[float, list[dict[str, Any]]]:  # fmt: skip
    """(volume of R_a ∩ R_b, the lines where they cross); (0, []) when the boxes do not meet."""
    from bf_core.geo import crossing, solids

    if not _boxes_meet(raw_a, raw_b):
        return 0.0, []
    try:
        _, _, report = solids.boolean_many([raw_a, raw_b], "intersection", names=[f"жила {c}" for c in codes])
    except solids.SolidError as err:
        raise _solids_error(err, f"пара «{codes[0]}» и «{codes[1]}»") from None
    volume = 0.0 if report["empty"] or report["flat"] else float(report["volume"])
    lines = crossing.intersection_lines(raw_a, raw_b)
    return volume, lines


def _cut(raw: tuple[F8, np.ndarray], cutters: list[tuple[F8, np.ndarray]], name: str, cut_names: list[str]
         ) -> tuple[F8, np.ndarray, float]:  # fmt: skip
    """R_j minus the younger bodies: (vertices, triangles, volume), canonical; empty and 0.0 when nothing is left."""
    from bf_core.geo import solids

    try:
        v, t, report = solids.boolean_many([raw, *cutters], "difference", names=[name, *cut_names])
    except solids.SolidError as err:
        raise _solids_error(err, f"вырез жилы «{name}»") from None
    if report["empty"] or report["flat"]:
        return EMPTY_V, EMPTY_T, 0.0
    cv, ct = solids.canonical(v, t)
    return cv, ct, float(report["volume"])


def _map(pool: ThreadPoolExecutor | None, fn: Callable[[Any], Any], items: list[Any]) -> list[Any]:
    """`fn` over `items`, the results in the order of the items (the same bits for any number of threads)."""
    if pool is None or len(items) < 2:
        return [fn(x) for x in items]
    return list(pool.map(fn, items))


# --- the system ----------------------------------------------------------------------------------------------------


def build_vein_system(veins: Sequence[VeinInput], h: float, *, threads: int = 1, tick: Tick | None = None,
                      dense_max: int | None = None) -> VeinSystemResult:  # fmt: skip
    """The bodies, the common body, the lines and the table of pairs of a system of veins (see the module text)."""
    from bf_core.geo import solids

    from . import veins as vm

    veins = list(veins)
    _check(veins)
    threads = max(1, int(threads))
    notes: list[str] = [f"Система жил: {len(veins)}; порядок в списке — возраст (первая жила — самая молодая)."]
    kw: dict[str, Any] = {} if dense_max is None else {"dense_max": dense_max}
    n = len(veins)

    raw: list[tuple[F8, np.ndarray] | None] = []
    raw_volume: list[float] = []
    for i, vein in enumerate(veins):
        _beat(tick, 0.5 * i / n, f"Жила «{vein.name}»: тело")
        try:
            body = vm.build_vein(vein.hanging, vein.footwall, vein.params, h, threads=threads,
                                 tick=_within(tick, 0.5 * i / n, 0.5 * (i + 1) / n), **kw)  # fmt: skip
        except ApiError as err:
            raise _named(err, vein) from None
        if body is None:
            raw.append(None)
            raw_volume.append(0.0)
            notes.append(f"Жила «{vein.name}» ({vein.code}): выклинилась целиком (мощность ≤ t_min) — тела нет.")
            continue
        raw.append((body.vertices, body.triangles))
        raw_volume.append(float(body.volume))
        notes += [f"Жила «{vein.name}» ({vein.code}): {note}" for note in body.notes]

    alive = [i for i in range(n) if raw[i] is not None]
    pairs_idx = [(a, b) for k, a in enumerate(alive) for b in alive[k + 1:]]  # a is younger than b  # fmt: skip
    _beat(tick, 0.5, "Система жил: пересечения пар")
    pool = ThreadPoolExecutor(max_workers=threads) if threads > 1 else None
    try:

        def pair(ab: tuple[int, int]) -> tuple[float, list[dict[str, Any]]]:
            a, b = ab
            ra, rb = raw[a], raw[b]
            assert ra is not None and rb is not None
            return _pair(ra, rb, (veins[a].code, veins[b].code))

        found = _map(pool, pair, pairs_idx)
        pairs: list[dict[str, Any]] = []
        lines: list[PairLines] = []
        cutters: dict[int, list[int]] = {i: [] for i in alive}
        for (a, b), (volume, ls) in zip(pairs_idx, found, strict=True):
            if volume <= 0.0 and not ls:
                continue
            length = line_length(ls)
            pairs.append({"a": veins[a].code, "b": veins[b].code, "line_length": length, "volume_common": volume})
            if ls:
                lines.append(PairLines(veins[a].code, veins[b].code, ls))
            if volume > 0.0:
                cutters[b].append(a)
            notes.append(f"Жилы «{veins[a].name}» и «{veins[b].name}»: общая зона {volume:.6g} м³, "
                         f"линии пересечения {length:.6g} м; режет молодая «{veins[a].name}».")  # fmt: skip
        _beat(tick, 0.7, "Система жил: вырезы")

        def cut(j: int) -> tuple[F8, np.ndarray, float]:
            rj = raw[j]
            assert rj is not None
            if not cutters[j]:
                cv, ct = solids.canonical(*rj)
                return cv, ct, raw_volume[j]
            by = [raw[i] for i in cutters[j]]
            assert all(x is not None for x in by)
            return _cut(rj, [x for x in by if x is not None], veins[j].code, [veins[i].code for i in cutters[j]])

        cuts = _map(pool, cut, alive)
    finally:
        if pool is not None:
            pool.shutdown()

    done = dict(zip(alive, cuts, strict=True))
    parts: list[VeinPart] = []
    for i, vein in enumerate(veins):
        by = tuple(veins[k].code for k in cutters.get(i, []))
        if i not in done:
            parts.append(VeinPart(vein.code, vein.name, EMPTY_V, EMPTY_T, 0.0, 0.0, (), "pinched"))
            continue
        v, t, volume = done[i]
        if len(t) == 0:
            notes.append(f"Жила «{vein.name}» ({vein.code}): целиком в молодых жилах ({', '.join(by)}) — тела нет.")
            parts.append(VeinPart(vein.code, vein.name, EMPTY_V, EMPTY_T, 0.0, raw_volume[i], by, "cut_out"))
        else:
            if by:
                notes.append(f"Жила «{vein.name}» ({vein.code}): вырезана молодыми жилами {', '.join(by)}; "
                             f"объём {raw_volume[i]:.6g} → {volume:.6g} м³.")  # fmt: skip
            parts.append(VeinPart(vein.code, vein.name, v, t, volume, raw_volume[i], by, "ok"))

    _beat(tick, 0.85, "Система жил: общее тело")
    whole = [r for r in raw if r is not None]
    common = _union(whole, tick)
    if common is None:
        notes.append("Общего тела нет: все жилы выклинились.")
    stats = {
        "veins": n,
        "built": len(alive),
        "pairs": len(pairs),
        "volume_cut": math.fsum(p.volume for p in parts),
        "volume_raw": math.fsum(raw_volume),
        "volume_common": 0.0 if common is None else common[2],
        "line_length": math.fsum(p["line_length"] for p in pairs),
    }
    _beat(tick, 1.0, "Система жил: готово")
    return VeinSystemResult(parts, common, pairs, lines, stats, notes)


def _union(bodies: list[tuple[F8, np.ndarray]], tick: Tick | None) -> tuple[F8, np.ndarray, float] | None:
    """The union of the raw bodies, canonical: (vertices, triangles, volume); None for no body at all."""
    from bf_core.geo import solids

    if not bodies:
        return None
    if len(bodies) == 1:
        v, t = solids.canonical(*bodies[0])
        return v, t, abs(solids.signed_volume(v - v.mean(axis=0), t.astype(np.int64)))
    try:
        v, t, report = solids.boolean_many(bodies, "union", tick=_within(tick, 0.85, 1.0))
    except solids.SolidError as err:
        raise _solids_error(err, "общее тело") from None
    cv, ct = solids.canonical(v, t)
    return cv, ct, float(report["volume"])


__all__ = ["PairLines", "VeinInput", "VeinPart", "VeinSystemResult", "build_vein_system", "line_length"]
