"""Operations on wireframes (ТЗ 7.23; ADR 0015 §7): правка треугольников, исправить, упростить, сгладить, в один
объект, на части, булева, обрезать плоскостью, полигоном, поверхностью. Every run takes the sources loaded by the
job (`Sources`), the checked parameters (`registry.precheck`) and `tick(fraction, message)`; it returns an
`Output` and never changes its sources. The kernels are `geo/*` (their errors carry {code, message, hint}).
Ported from BF `ops/operations.py` (parts D and E).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .output import MeshOut, Output, Tick, failed, staged, within
from .sources import Sources, Wf

MAX_NAME = 200


def _name(text: str) -> str:
    return " ".join(text.split())[:MAX_NAME]


def _one(src: Sources, p: dict[str, Any]) -> tuple[Wf, np.ndarray, np.ndarray]:
    wf = src.wfs[int(p["wireframe_id"])]
    v, t = src.mesh(wf)
    return wf, v, t


def _same(wf: Wf, suffix: str, v: np.ndarray, t: np.ndarray, report: dict[str, Any]) -> Output:
    """A changed copy of a wireframe: its colour and attributes go with it."""
    name = _name(f"{wf.name} ({suffix})")
    return Output(name, [MeshOut(name, v, t, wf.color, wf.attrs)], report=report)


def edit_triangles(src: Sources, p: dict[str, Any], tick: Tick) -> Output:
    from bf_core.geo.extrude import edit_triangles as edit

    wf, v, t = _one(src, p)
    tick(0.1, "Правка треугольников")
    remove = np.asarray(p["remove"], dtype=np.int64)
    add = np.asarray(p["add"], dtype=np.float64).reshape(-1, 3, 3)
    nv, nt = edit(v, t, remove, add)
    if not len(nt):
        raise failed(
            "empty_result", f"В каркасе «{wf.name}» не осталось треугольников.", "Отметьте меньше треугольников."
        )
    return _same(wf, "правка", nv, nt, {"removed": int(len(remove)), "added": int(len(add)), "triangles": len(nt)})


def repair(src: Sources, p: dict[str, Any], tick: Tick) -> Output:
    from bf_core.geo.repair import repair as fix

    wf, v, t = _one(src, p)
    flags = {k: p[k] for k in ("drop_duplicate", "drop_degenerate", "fix_nonmanifold", "fill_holes", "orient")}
    nv, nt, report = fix(v, t, weld=p["weld"], weld_on=p["weld_on"], progress=within(tick, 0.0, 1.0), **flags)
    out = _same(wf, "исправлен", nv, nt, report)
    out.tolerances = {"weld": report.get("tolerance")}
    after, before = report.get("after") or {}, report.get("before") or {}
    if after.get("self_intersections"):
        out.warnings.append(
            f"Самопересечения остались ({after['self_intersections']}): они не исправляются автоматически — "
            "«Проверить» отметит их красным, правьте треугольники вручную."
        )
    if not after.get("closed") and before.get("open_edges"):
        notes = [str(s["note"]) for s in report.get("steps", []) if s.get("note")]
        out.warnings.append("Каркас остался незамкнутым" + (": " + "; ".join(notes) if notes else "."))
    return out


def simplify(src: Sources, p: dict[str, Any], tick: Tick) -> Output:
    from bf_core.geo.reshape import simplify_mesh

    wf, v, t = _one(src, p)
    nv, nt, report = simplify_mesh(
        v, t, target_triangles=p["target_triangles"], tolerance=p["tolerance"], progress=within(tick, 0.0, 1.0)
    )
    out = _same(wf, "упрощён", nv, nt, report)
    out.tolerances = {"tolerance": p["tolerance"], "max_deviation": report.get("max_deviation")}
    return out


def smooth(src: Sources, p: dict[str, Any], tick: Tick) -> Output:
    from bf_core.geo.reshape import smooth_mesh

    wf, v, t = _one(src, p)
    nv, nt, report = smooth_mesh(
        v, t, iterations=p["iterations"], strength=p["strength"], keep_boundary=p["keep_boundary"],
        progress=within(tick, 0.0, 1.0),
    )  # fmt: skip
    out = _same(wf, "сглажен", nv, nt, report)
    out.tolerances = {"max_deviation": report.get("max_deviation")}
    return out


def merge(src: Sources, p: dict[str, Any], tick: Tick) -> Output:
    from bf_core.geo.reshape import merge_meshes

    wfs = [src.wfs[int(i)] for i in p["wireframe_ids"]]
    tick(0.2, "Объединение в один объект")
    v, t = merge_meshes([src.mesh(w) for w in wfs])
    name = _name("В один объект: " + " + ".join(w.name for w in wfs))
    return Output(name, [MeshOut(name, v, t, wfs[0].color)], report={"parts": len(wfs), "triangles": len(t)})


def split(src: Sources, p: dict[str, Any], tick: Tick) -> Output:
    from bf_core.geo.reshape import split_components

    wf, v, t = _one(src, p)
    tick(0.2, "Связные части")
    parts = split_components(v, t)
    if len(parts) < 2:
        raise failed(
            "single_part", f"Каркас «{wf.name}» — одна связная часть: делить нечего.", "Выберите другой каркас."
        )
    limit = int(p["max_parts"])
    out = Output(_name(f"{wf.name} (части)"), report={"parts": len(parts), "kept": min(limit, len(parts))})
    for n, (pv, pt) in enumerate(parts[:limit], start=1):
        out.meshes.append(MeshOut(_name(f"{wf.name} — часть {n}"), pv, pt, wf.color))
    if len(parts) > limit:
        out.warnings.append(f"Частей {len(parts)}: созданы {limit} самых больших.")
    return out


BOOL_SIGN = {"union": "∪", "intersection": "∩", "difference": "−"}


def boolean(src: Sources, p: dict[str, Any], tick: Tick) -> Output:
    from bf_core.geo.solids import boolean_many

    ids = [p["a"], *(p["b"] if isinstance(p["b"], list) else [p["b"]])]
    wfs = [src.wfs[int(i)] for i in ids]
    v, t, report = boolean_many(
        [src.mesh(w) for w in wfs], p["op"], names=[w.name for w in wfs], tick=staged(tick, 0.0, 1.0, "Булева операция")
    )
    name = _name(f" {BOOL_SIGN[p['op']]} ".join(w.name for w in wfs))
    if report.get("empty") or not len(t):
        why = "тела не пересекаются" if p["op"] == "intersection" else "от первого тела ничего не осталось"
        raise failed("empty_result", f"Результат «{name}» пуст: {why}.", "Проверьте положение тел.")
    out = Output(name, [MeshOut(name, v, t, wfs[0].color)], report=report)
    out.tolerances = {"manifold": report.get("tolerance")}
    if report.get("flat"):
        out.warnings.append("У результата нет толщины: тела касаются по грани.")
    if report.get("flipped"):
        out.warnings.append(
            "Вывернутые тела развёрнуты наружу: " + ", ".join(f"«{n}»" for n in report["flipped"]) + "."
        )
    return out


def _parts(wf: Wf, title: str, result: dict[str, Any], titles: dict[str, str]) -> Output:
    out = Output(_name(f"{wf.name}: {title}"), report=dict(result.get("report") or {}))
    for key, part_title in titles.items():
        part = result.get(key)
        if part is not None and len(part[1]):
            out.meshes.append(MeshOut(_name(f"{wf.name}: {part_title}"), part[0], part[1], wf.color, wf.attrs))
    if out.report.get("warning"):
        out.warnings.append(str(out.report["warning"]))
    if not out.meshes:
        raise failed(
            "empty_result", "После обрезки ничего не осталось.",
            "Секущая не пересекает объект или выбрана пустая сторона — проверьте параметры.",
        )  # fmt: skip
    return out


def clip_plane(src: Sources, p: dict[str, Any], tick: Tick) -> Output:
    from bf_core.geo.clip import clip_by_plane

    wf, v, t = _one(src, p)
    normal = np.asarray(p["normal"], dtype=np.float64)
    result = clip_by_plane(
        (v, t), p["point"], normal, keep=p["keep"], name=wf.name, tick=staged(tick, 0.0, 1.0, "Обрезка плоскостью")
    )
    flat = abs(normal[2]) > 0.999 * float(np.linalg.norm(normal))
    titles = {
        "positive": ("выше уровня" if normal[2] > 0 else "ниже уровня") if flat else "по нормали",
        "negative": ("ниже уровня" if normal[2] > 0 else "выше уровня") if flat else "против нормали",
    }
    return _parts(wf, "обрезка плоскостью", result, titles)


def clip_polygon(src: Sources, p: dict[str, Any], tick: Tick) -> Output:
    from bf_core.geo.clip import clip_by_polygon

    wf, v, t = _one(src, p)
    (line,) = src.lines("polygon")
    result = clip_by_polygon(
        (v, t), line.points[:, :2], keep=p["keep"], name=wf.name, tick=staged(tick, 0.0, 1.0, "Обрезка полигоном")
    )
    return _parts(wf, "обрезка полигоном", result, {"inside": "внутри полигона", "outside": "вне полигона"})


def clip_surface(src: Sources, p: dict[str, Any], tick: Tick) -> Output:
    from bf_core.geo.clip import clip_by_surface

    wf, v, t = _one(src, p)
    surface = src.wfs[int(p["surface_id"])]
    result = clip_by_surface(
        (v, t), src.mesh(surface), keep=p["keep"], direction="normal" if p["direction"] == "normal" else None,
        names=(wf.name, surface.name), tick=staged(tick, 0.0, 1.0, "Обрезка поверхностью"),
    )  # fmt: skip
    titles = {"above": f"над «{surface.name}»", "below": f"под «{surface.name}»"}
    return _parts(wf, f"обрезка «{surface.name}»", result, titles)


def stitch_series(src: Sources, p: dict[str, Any], tick: Tick) -> Output:
    """«Каркас по серии» (`run_series.py`): contours of a series → wireframes (not in `job.LINE_OPS`)."""
    from .run_series import stitch_series as run

    return run(src, p, tick)


RUNS = {
    "edit_triangles": edit_triangles,
    "repair": repair,
    "simplify": simplify,
    "smooth": smooth,
    "merge": merge,
    "split": split,
    "boolean": boolean,
    "clip_plane": clip_plane,
    "clip_polygon": clip_polygon,
    "clip_surface": clip_surface,
    "stitch_series": stitch_series,
}
