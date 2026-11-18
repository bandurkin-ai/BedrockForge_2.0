"""Polylines of a file → trenches (ТЗ 7.35 Д-16; ADR 0011 §5): the reading and the geometry of `import.trenches`.
No database here: the wizard uses `preview`, the job `read_polylines` and `build`.

A trench is one polyline: a line of a Micromine or Surpac STR or of a DXF (any line reader of `formats.registry`),
or the rows of a table of vertices «канава, X, Y, Z» (`formats.points`), gathered by the name of the trench in the
order of the file. The `lines` parameter of the job (`spec`):
  path, filename (the original name of a stored file), encoding;
  layers — names of the layers to take (default: all);
  name_from — "string" (default): the name of the string — STRING of Micromine, «Стринг N» of Surpac, the layer
    of a DXF; several lines of one string are numbered «<имя>-1», «<имя>-2» … in the order of the file;
    "field": the attribute `name_field` of the line (JOIN, D1, a field of the user) — lines with an empty one are
    left out. A table of vertices always names by a column: `name_field`, else found by its name (TRENCH, Канава,
    HOLEID …); a name that looks like a number is kept as it is written («007»);
  x, y, z, table {encoding, delimiter, header_row, sheet} — columns and read options of a table of vertices;
  reverse — «Обратить направление»: true for every trench, or a list of trench names.

`build` turns the polylines into trenches: vertices in the direction of the trench, a vertex that does not advance
the 3D length taken once, depth of a vertex = the 3D length from the first one (summed along the trench, so a
set of 10 000 trenches loses no digits). Not a trench, each kind with a line for the operation log: a line without
two different vertices, a line with a vertex without Z (an empty Z is never 0). Trench names must differ.
Errors are `JobError`s {code, message, hint}: not_lines, no_name_field, trench_name_repeats, name_too_long,
unknown_trench, file_unreadable and the codes of the readers.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from bf_core.jobs.context import JobError

NAME_ALIASES = ("TRENCH", "TRENCH_ID", "TRENCHID", "TRENCH_NAME", "КАНАВА", "НОМЕР_КАНАВЫ", "№_КАНАВЫ", "ТРАНШЕЯ")
MAX_NAME = 200  # dh_holes.name
SHOWN = 10  # names listed in one line of the log
Tick = Callable[[float | None, str | None], None]


def n_text(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def listed(names: Sequence[str]) -> str:
    shown = ", ".join(f"«{n}»" for n in names[:SHOWN])
    return shown + (f" и ещё {n_text(len(names) - SHOWN)}" if len(names) > SHOWN else "")


@dataclass
class Polylines:
    """The lines of a file as they are read: `xyz[offsets[i]:offsets[i + 1]]` — the vertices of `names[i]`."""

    format: str  # a key of `registry.OBJECT_FORMATS`
    names: list[str]
    offsets: Any  # int64 (n + 1)
    xyz: Any  # float64 (N, 3)
    layers: list[dict[str, Any]] = field(default_factory=list)  # {name, lines} of every layer of lines
    fields: list[str] = field(default_factory=list)  # attributes a name can be taken from
    warnings: list[str] = field(default_factory=list)
    skipped: int = 0  # lines that hold no trench


@dataclass
class Trenches:
    """Trenches ready to write: vertices in the direction of the trench, `depth` — 3D length from the first one."""

    names: list[str]
    offsets: Any
    xyz: Any
    depth: Any
    warnings: list[str] = field(default_factory=list)
    skipped: int = 0

    def lengths(self) -> Any:
        return self.depth[self.offsets[1:] - 1]


# --- reading ----------------------------------------------------------------------------------------


def _texts(column: Any) -> list[str]:
    """Values of an attribute as names: numbers as they are typed (12.0 → «12»), an empty one is «»."""
    import pyarrow as pa

    from bf_core.formats.numbers import number_text

    if pa.types.is_floating(column.type):
        return [number_text(v) for v in column.to_pylist()]
    return [" ".join(str(v).split()) if v is not None else "" for v in column.to_pylist()]


def _column(names: Sequence[str], wanted: str) -> str | None:
    key = wanted.strip().casefold()
    return next((n for n in names if n.casefold() == key), None)


def _from_table(layer: Any, spec: dict[str, Any], reread: Callable[[str], Any], out: Polylines) -> None:
    """A table of vertices: the rows of a trench are gathered by its name, in the order of the file."""
    import numpy as np
    import pyarrow as pa

    from .names import match_column
    from .schema import SCHEMAS

    names = [] if layer.attrs is None else list(layer.attrs.column_names)
    out.fields = names
    wanted = (spec.get("name_field") or "").strip()
    aliases = (*NAME_ALIASES, *next(f.aliases for f in SCHEMAS["collar"] if f.key == "hole_id"))
    column = _column(names, wanted) if wanted else match_column(names, aliases)
    if column is None:
        what = f"нет колонки «{wanted}»" if wanted else "не найдена колонка с именем канавы"
        raise JobError(
            "no_name_field",
            f"В таблице вершин {what}.",
            f"Колонки файла: {', '.join(names) or 'только координаты'}. Выберите колонку с именем канавы.",
        )
    if pa.types.is_floating(layer.attrs.column(column).type):
        layer = reread(column)  # names that look like numbers are read as text: «007» is not «7»
    index: dict[str, int] = {}
    code = np.array(
        [index.setdefault(t, len(index)) if t else -1 for t in _texts(layer.attrs.column(column))], dtype=np.int64
    )
    rows = np.flatnonzero(code >= 0)
    if len(rows) < len(code):
        out.warnings.append(f"Строк без имени канавы: {n_text(len(code) - len(rows))} — они пропущены")
    out.names = list(index)
    out.offsets = np.concatenate([[0], np.cumsum(np.bincount(code[rows], minlength=len(index)))]).astype(np.int64)
    out.xyz = np.ascontiguousarray(layer.xyz[rows[np.argsort(code[rows], kind="stable")]])


def _from_layers(layers: Sequence[Any], spec: dict[str, Any], out: Polylines) -> None:
    """Layers of lines: every line of two vertices or more is a trench, named by its string or by a field."""
    import numpy as np

    from bf_core.formats.str_layers import ranges

    by_field = spec.get("name_from") == "field"
    wanted = (spec.get("name_field") or "").strip()
    if by_field and not wanted:
        raise JobError("no_name_field", "Не выбрано поле с именем канавы.", "Выберите поле или «имя стринга».")
    only = None if spec.get("layers") is None else {" ".join(str(n).split()) for n in spec["layers"]}
    parts: list[Any] = []
    counts: list[Any] = []
    no_field: list[str] = []
    numbered: list[str] = []
    unnamed = closed = 0
    for layer in layers:
        if layer.points_only:
            out.warnings.append(f"Слой «{layer.name}» — точки ({n_text(layer.point_count)}): канав в нём нет")
            continue
        n = np.diff(layer.offsets)
        take = np.flatnonzero(n >= 2)
        out.layers.append({"name": layer.name, "lines": len(take)})
        attrs = [] if layer.attrs is None else list(layer.attrs.column_names)
        out.fields += [a for a in attrs if a not in out.fields]
        if only is not None and layer.name not in only:
            continue
        out.skipped += len(n) - len(take)
        if by_field:
            column = _column(attrs, wanted)
            if column is None:
                no_field.append(layer.name)
                out.skipped += len(take)
                continue
            texts = _texts(layer.attrs.column(column))
            named = np.array([bool(texts[i]) for i in take.tolist()], dtype=bool)
            unnamed += len(take) - int(named.sum())
            take = take[named]
            names = [texts[i] for i in take.tolist()]
        elif len(take) == 1:
            names = [layer.name]
        else:
            names = [f"{layer.name}-{k}" for k in range(1, len(take) + 1)]
            numbered.append(layer.name)
        if not len(take):
            continue
        out.names += names
        closed += int(np.count_nonzero(layer.closed[take]))
        counts.append(n[take])
        parts.append(layer.xyz if len(take) == len(n) else layer.xyz[ranges(layer.offsets[take], n[take])])
    if by_field and not _column(out.fields, wanted):
        raise JobError(
            "no_name_field",
            f"У линий файла нет поля «{wanted}».",
            f"Поля линий: {', '.join(out.fields) or 'нет'}. Выберите другое поле или «имя стринга».",
        )
    if no_field:
        out.warnings.append(f"В слоях нет поля «{wanted}», их линии пропущены: {listed(no_field)}")
    if unnamed:
        out.skipped += unnamed
        out.warnings.append(f"Линий с пустым полем «{wanted}»: {n_text(unnamed)} — они пропущены")
    if numbered:
        text = "В стринге несколько линий: канавы названы «имя-1», «имя-2» … по порядку в файле"
        out.warnings.append(f"{text} — {listed(numbered)}")
    if closed:
        out.warnings.append(f"Замкнутых линий: {n_text(closed)} — такая канава возвращается к своему началу")
    total = np.concatenate(counts) if counts else np.zeros(0, dtype=np.int64)
    out.offsets = np.concatenate([[0], np.cumsum(total)]).astype(np.int64)
    out.xyz = np.ascontiguousarray(np.concatenate(parts) if parts else np.zeros((0, 3)))


def read_polylines(spec: dict[str, Any], tick: Tick | None = None) -> Polylines:
    """The polylines of the file `spec` (module docstring) with the names of their trenches."""
    from bf_core.formats import registry
    from bf_core.formats.errors import FormatError
    from bf_core.formats.table import ReadOptions

    path = Path(spec["path"])
    label = Path(spec.get("filename") or path.name).name

    def progress(fraction: float, message: str | None = None) -> None:
        if tick is not None:
            tick(fraction, f"{label}: {message}" if message else label)

    def read(key: str, text_column: str | None = None) -> Any:
        options: dict[str, Any] = {}
        if key == registry.POINTS:
            raw = spec.get("table") or {}
            keep = tuple(c for c in (text_column, spec.get("name_field")) if c)
            options["options"] = ReadOptions(
                raw.get("encoding") or None, raw.get("delimiter") or None, int(raw.get("header_row", 1)),
                raw.get("sheet") or None, keep,
            )  # fmt: skip
            options.update({k: spec[k] for k in ("x", "y", "z") if spec.get(k) is not None})
        return registry.read_objects(
            path, progress, filename=spec.get("filename"), encoding=spec.get("encoding") or None, options=options
        )

    try:
        fmt = registry.detect_object(path, spec.get("filename"))
        if fmt.kind == registry.RASTER_KIND:
            raise JobError(
                "not_lines", f"«{label}» — растр, а не линии.", "Канавы загружаются из STR, DXF или таблицы вершин."
            )
        got = read(fmt.key)
        out = Polylines(fmt.key, [], None, None, warnings=list(got.warnings))
        if fmt.key == registry.POINTS:
            _from_table(got.layers[0], spec, lambda column: read(fmt.key, column).layers[0], out)
        else:
            _from_layers(got.layers, spec, out)
    except FormatError as exc:
        raise JobError(exc.code, f"«{label}»: {exc.message}", exc.hint) from exc
    except OSError as exc:
        raise JobError(
            "file_unreadable", f"«{label}»: файл не читается ({exc.strerror or exc}).", "Проверьте путь и доступ."
        ) from exc
    seen: set[str] = set()
    twice = list(dict.fromkeys(n for n in out.names if n in seen or seen.add(n)))  # type: ignore[func-returns-value]
    if twice:
        raise JobError(
            "trench_name_repeats",
            f"Имена канав повторяются: {listed(twice)}.",
            "Канава — одна линия. Возьмите имя из другого поля (например, JOIN) или исправьте имена в файле.",
        )
    long = [n for n in out.names if len(n) > MAX_NAME]
    if long:
        raise JobError("name_too_long", f"Имя канавы длиннее {MAX_NAME} символов: «{long[0][:40]}…».", "Сократите имя.")
    return out


# --- geometry -----------------------------------------------------------------------------------------


def _chain(xyz, offsets, reverse, out, depth, starts, state):  # type: ignore[no-untyped-def]
    """Vertices of every line in the direction of its trench, without a vertex that does not advance the 3D
    length, and that length at every vertex. state: 0 — a trench, 1 — a coordinate is missing, 2 — fewer than
    two different vertices (such a line takes no rows of `out`)."""
    w = 0
    for i in range(offsets.shape[0] - 1):
        a = offsets[i]
        b = offsets[i + 1]
        first = w
        starts[i] = w
        for j in range(b - a):
            k = b - 1 - j if reverse[i] else a + j
            x = xyz[k, 0]
            y = xyz[k, 1]
            z = xyz[k, 2]
            if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
                state[i] = 1
                break
            if w > first:
                dx = x - out[w - 1, 0]
                dy = y - out[w - 1, 1]
                dz = z - out[w - 1, 2]
                d = depth[w - 1] + math.sqrt(dx * dx + dy * dy + dz * dz)
                if not d > depth[w - 1]:
                    continue
                depth[w] = d
            else:
                depth[w] = 0.0
            out[w, 0] = x
            out[w, 1] = y
            out[w, 2] = z
            w += 1
        if state[i] == 0 and w - first < 2:
            state[i] = 2
        if state[i] != 0:
            w = first
    starts[offsets.shape[0] - 1] = w


@cache
def _kernel() -> Callable[..., Any]:
    """`_chain` compiled by numba (cached on disk); numba is loaded on first use, never with this module."""
    from bf_core.jit import njit

    return njit(cache=True, nogil=True)(_chain)  # type: ignore[no-any-return]


def build(lines: Polylines, reverse: bool | Sequence[str] = False) -> Trenches:
    """Trenches of the polylines: direction, vertices without repeats, depth by 3D length."""
    import numpy as np

    n = len(lines.names)
    flags = np.zeros(n, dtype=np.bool_)
    if isinstance(reverse, (list, tuple)):
        where = {name: i for i, name in enumerate(lines.names)}
        unknown = [str(name) for name in reverse if str(name) not in where]
        if unknown:
            raise JobError(
                "unknown_trench", f"Канав для обращения нет в файле: {listed(unknown)}.", "Проверьте имена канав."
            )
        flags[[where[str(name)] for name in reverse]] = True
    elif reverse:
        flags[:] = True
    xyz = np.empty_like(lines.xyz)
    depth = np.empty(len(xyz))
    starts = np.zeros(n + 1, dtype=np.int64)
    state = np.zeros(n, dtype=np.uint8)
    _kernel()(lines.xyz, lines.offsets, flags, xyz, depth, starts, state)
    keep = np.flatnonzero(state == 0)
    total = int(starts[n])
    out = Trenches([lines.names[i] for i in keep.tolist()], np.append(starts[keep], total), xyz[:total], depth[:total])
    out.skipped = lines.skipped + n - len(keep)
    for code, text in ((1, "Не загружены линии, у вершин которых нет координаты Z"),
                       (2, "Не загружены линии, в которых меньше двух разных вершин")):  # fmt: skip
        bad = [lines.names[i] for i in np.flatnonzero(state == code).tolist()]
        if bad:
            out.warnings.append(f"{text} ({n_text(len(bad))}): {listed(bad)}")
    return out


def preview(spec: dict[str, Any], tick: Tick | None = None, limit: int = 30) -> dict[str, Any]:
    """What the wizard shows before the import: layers, fields for the name, counts and the first trenches
    (name, vertices, length and the first vertex in the chosen direction)."""
    lines = read_polylines(spec, tick)
    t = build(lines, spec.get("reverse") or False)
    first, length = t.xyz[t.offsets[:-1]], t.lengths()
    rows = [
        {"name": t.names[i], "vertices": int(t.offsets[i + 1] - t.offsets[i]), "length": float(length[i]),
         "x": float(first[i, 0]), "y": float(first[i, 1]), "z": float(first[i, 2])}
        for i in range(min(limit, len(t.names)))
    ]  # fmt: skip
    return {
        "format": lines.format, "layers": lines.layers, "fields": lines.fields, "trenches": len(t.names),
        "vertices": len(t.xyz), "length": float(length.sum()), "skipped": t.skipped,
        "warnings": [*lines.warnings, *t.warnings], "preview": rows,
    }  # fmt: skip
