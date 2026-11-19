"""Datamine wireframes (`*tr.dm` + `*pt.dm`) and strings (`.dm`) — ТЗ 7.35 Д-13, 9.12; ADR 0009; ADR 0013 §2.

**Preliminary** (`PRELIMINARY`): the only sample is a triangle table of Datamine Studio without its points table
(`tests/data/real/Datamine_block_model.zip`: `PID1…PID3`, `TRIANGLE` and attributes, as described here); no
points table in a `.dm` and no strings have been seen (question № 4 to the owner; the owner's `.dmx` wireframe
has the same fields in both tables, `formats/datamine_dmx.py`). The tables are read by
`formats/datamine.py` (checked on the owner's block model and on that table); what the fields mean is taken from
the help of Datamine Studio and checked on synthetic files only. Every reading says so in its first warning.

A wireframe is two tables. Triangles: `TRIANGLE, PID1, PID2, PID3` and any other fields (`GROUP`, `SURFACE`,
`LINK`, `COLOUR`, attributes) — a record per triangle, `PID1…PID3` are numbers of points. Points: `PID, XP, YP,
ZP`. The numbers of points are matched by value: they need not start from 1, be in order or be whole.

    find_pair(path)                  the other table of the pair (`formats/datamine_pair.py`): by the name
                                     (`oretr.dm` ↔ `orept.dm`, any case, `ore_tr` / `ore-tr` as well), else the
                                     only table of that kind in the folder (a warning says which one was taken)
    open_dm_wireframes(path, …)      `DmWireframes`: `entries` — the composition without geometry, `read(entry)`
    inspect_dm_wireframes(path, …)   (entries, warnings)
    read_dm_wireframes(path, …)      the wireframes one by one as `MeshSet`
    split_fields(header)             the fields a file can be divided by
    read_dm_strings(path, …)         `str_layers.StrRead` of `LineSet`s;  layer_fields(header)

`path` may be either table of the pair; `points` names the other one when it is not found by itself. By default
a file is one wireframe; `split_by` — a field of the triangle table (number or text, stored or implicit): the
triangles of every value are a wireframe, in the order of their first triangle (ADR 0013 §2 «деление — по
выбранному полю»). A wireframe keeps its triangles in the order of the file and its points in the order of the
points table; points that no triangle uses are left out; points at one place under different numbers are welded
exactly (`meshset.weld_exact`). A field with one value in the whole wireframe is its attribute (an absent value
is empty, never 0); the fields that change inside it are named in a warning (attributes of triangles are not
loaded, as in TRIDB). `COLOUR` is a number of the Datamine palette, not a colour: it stays an attribute.
A triangle that refers to a point that is not in the points table, or a used point without a coordinate, refuses
its wireframe (`bad_mesh`, `bad_coordinates`); the other wireframes of the file are still read.

Memory: the composition holds 4 bytes a triangle (the group of every record) and one chunk of pages; reading
holds the points (24 bytes each) and the triangles as int32 (12 bytes each) of the file, and one `MeshSet`.

Strings: `PTN, PVALUE, XP, YP, ZP` and attributes — a record per vertex; `PVALUE` is the number of the string,
`PTN` — of the point in it. A line begins where `PVALUE` changes or `PTN` does not grow. One layer named after the
file, or a layer per value of `layer_field`; a closed string repeats its first point (`lineset.detect_closed`);
a record without X or Y is left out and counted, an absent Z stays NaN; the other fields are attributes of lines
or of vertices (`str_layers.build_layers`), `seq` is the number of the record.

Error codes: those of `datamine.py` and pair_missing, pair_ambiguous, pair_mismatch, not_wireframe, not_strings,
unsupported (a block model), no_field, too_many_meshes, no_meshes, no_lines, bad_mesh, bad_coordinates.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.formats.datamine import STRINGS, DmField, DmHeader, iter_chunks, read_dm, read_header
from bf_core.formats.datamine_pair import HINT_PAIR, PAIR_NAME, find_pair, resolve_pair, wrong_kind
from bf_core.formats.errors import FormatError
from bf_core.formats.meshset import MeshEntry, MeshSet, clean_attrs, clean_name, n_text, source_of, weld_exact
from bf_core.formats.str_layers import NO_CODE, StrRead, build_layers, scaled, text_codes
from bf_core.formats.table import Progress, no_progress

FORMAT = "datamine"
STRINGS_FORMAT = "datamine_str"
PRELIMINARY = True  # shown by the import wizard next to the format (ADR 0013 §6)
NOTE_WIREFRAMES = (
    "Каркасы Datamine читаются предварительно: по описанию формата, без проверки на образцах. "
    "Сверьте число треугольников, положение и объём каркаса с Datamine"
)
NOTE_STRINGS = (
    "Стринги Datamine читаются предварительно: по описанию формата, без проверки на образцах. "
    "Сверьте число линий и их положение с Datamine"
)
MAX_MESHES = 50_000  # as `tridb.MAX_MESHES`
MAX_TEXTS = 100_000  # different texts of a field followed while looking for attributes
_TRIANGLE_FIELDS = ("PID1", "PID2", "PID3")
_POINT_FIELDS = ("PID", "XP", "YP", "ZP")
_STRING_FIELDS = ("XP", "YP", "ZP", "PTN", "PVALUE")
_NOT_ATTRIBUTES = frozenset({"TRIANGLE", *_TRIANGLE_FIELDS})
_GROUPING = ("GROUP", "SURFACE", "LINK")  # the grouping fields of Datamine: offered first
_NAN = np.int64(0x7FF8000000000000)  # the token of an absent number
_UNSEEN = np.int64(0x7FF8000000000001)  # no value seen yet: the bits of no number after `_number_tokens`


# ---------------------------------------------------------------- fields and their values


def split_fields(header: DmHeader) -> list[str]:
    """The fields of a triangle table that it can be divided into wireframes by: the grouping fields of Datamine
    first, then the others in the order of the file."""
    names = [f.name for f in header.fields if f.name.upper() not in _NOT_ATTRIBUTES]
    first = [n for g in _GROUPING for n in names if n.upper() == g]
    return first + [n for n in names if n not in first]


def layer_fields(header: DmHeader) -> list[str]:
    """The fields of a strings table whose values can be layers."""
    return [f.name for f in header.fields if f.name.upper() not in ("XP", "YP", "ZP", "PTN")]


def _named(header: DmHeader, name: str, allowed: Sequence[str], what: str) -> DmField:
    try:
        found: DmField | None = header.field(name)
    except KeyError:
        found = None
    if found is None or found.name not in allowed:
        listed = ", ".join(allowed[:20]) + (" …" if len(allowed) > 20 else "")
        raise FormatError(
            "no_field", f"В таблице Datamine нет поля «{name}», по которому можно {what}.", f"Поля файла: {listed}."
        )
    return found


def _number_field(header: DmHeader, name: str, path: Path) -> DmField:
    f = header.field(name)
    if f.kind != "N":
        raise FormatError(
            "broken", f"Поле «{f.name}» файла «{path.name}» должно быть числом, а записано текстом.", HINT_PAIR
        )
    return f


def _number_tokens(values: np.ndarray) -> np.ndarray:
    """int64 per number: equal numbers (−0.0 is 0.0) — equal tokens, an absent one — `_NAN`."""
    tokens = (values + 0.0).view(np.int64)
    tokens[np.isnan(values)] = _NAN
    return tokens


def _number_of(token: int) -> float:
    return float(np.array(token, dtype=np.int64).view(np.float64))


def _number_text(value: float) -> str:
    return str(int(value)) if value.is_integer() and abs(value) < 1e15 else repr(value)


def _first_seen(tokens: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(the different tokens in the order of their first row, the number of its token for every row)."""
    unique, first, inverse = np.unique(tokens, return_index=True, return_inverse=True)
    rank = np.empty(len(unique), dtype=np.int64)
    by_first = np.argsort(first, kind="stable")
    rank[by_first] = np.arange(len(unique))
    return unique[by_first], rank[inverse.reshape(-1)]


def _columns(path: Path, header: DmHeader, names: Sequence[str], tick: Progress, message: str) -> dict[str, np.ndarray]:
    """Number fields of a table as float64 arrays (an implicit field — its value in every record)."""
    rows = header.records
    fields = [_number_field(header, name, path) for name in names]
    out = {f.name: np.empty(rows, dtype=np.float64) for f in fields}
    for f in fields:
        if not f.stored:
            out[f.name].fill(float(f.default))  # type: ignore[arg-type]
    stored = [f.name for f in fields if f.stored]
    with closing(iter_chunks(path, header, fields=stored, max_rows=rows if stored else 0)) as chunks:
        for chunk in chunks:
            tick(chunk.start / rows, f"{message}: {n_text(chunk.start)} из {n_text(rows)}")
            for name, values in chunk.columns.items():
                out[name][chunk.start : chunk.start + chunk.rows] = values
            del chunk
    return out


# ---------------------------------------------------------------- wireframes


class _Constants:
    """Which attribute fields have one value in every group of records (a wireframe), and that value."""

    def __init__(self, fields: Sequence[DmField]) -> None:
        self.fields = list(fields)
        self.first = np.full((len(self.fields), 16), _UNSEEN, dtype=np.int64)  # field × group: the first token
        self.varies = np.zeros((len(self.fields), 16), dtype=bool)
        self.texts: list[dict[str, int]] = [{} for _ in self.fields]  # field: text → its token (0, 1, …)
        self.lost = [False] * len(self.fields)  # a text field with too many texts: taken as changing everywhere
        self._listed: dict[int, list[str]] = {}

    def room(self, groups: int) -> None:
        have = self.first.shape[1]
        if groups > have:
            grow = max(groups, 2 * have) - have
            self.first = np.concatenate([self.first, np.full((len(self.fields), grow), _UNSEEN)], axis=1)
            self.varies = np.concatenate([self.varies, np.zeros((len(self.fields), grow), dtype=bool)], axis=1)

    def tokens(self, j: int, values: Any, keep: bool = False) -> np.ndarray | None:
        """A token per record of a chunk; None — the field is not followed any more (`keep` — never give up)."""
        if self.fields[j].kind == "N":
            return _number_tokens(values)
        if self.lost[j]:
            return None
        codes, texts = text_codes(values)
        known = self.texts[j]
        ids = np.array([known.setdefault(text, len(known)) for text in texts] + [-1], dtype=np.int64)
        if len(known) > MAX_TEXTS and not keep:
            self.lost[j], self.texts[j] = True, {}
            return None
        return ids[codes]  # a blank text (code −1) is the token −1

    def add(self, j: int, tokens: np.ndarray | None, group: np.ndarray, groups: np.ndarray, at: np.ndarray) -> None:
        """`group` — the group of every record of the chunk; `groups`, `at` — the groups present and a record of
        each."""
        if tokens is None:
            return
        first = self.first[j]
        new = first[groups] == _UNSEEN
        first[groups[new]] = tokens[at[new]]
        self.varies[j, group[tokens != first[group]]] = True

    def value(self, j: int, group: int) -> tuple[bool, Any]:
        """(one value in the group?, the value: a number, a text or None)."""
        f = self.fields[j]
        if not f.stored:
            return True, f.default
        if self.lost[j] or self.varies[j, group]:
            return False, None
        token = int(self.first[j, group])
        return True, self.decode(j, token)

    def decode(self, j: int, token: int) -> Any:
        """The value a token of the field stands for: a number (NaN — absent), a text or None."""
        if self.fields[j].kind == "N":
            return _number_of(token)
        if token < 0:
            return None
        if j not in self._listed:
            self._listed[j] = list(self.texts[j])  # tokens are given in the order the texts were first seen
        return self._listed[j][token]


class DmWireframes:
    """The wireframes of a pair of Datamine tables: `entries` without geometry, `read(entry)` — a `MeshSet`.
    `warnings` — of the file (the reading of the geometry adds to them)."""

    def __init__(
        self,
        path: str | Path,
        points: str | Path | None = None,
        *,
        split_by: str | None = None,
        encoding: str | None = None,
        progress: Progress | None = None,
        max_meshes: int = MAX_MESHES,
    ) -> None:
        tick = progress or no_progress
        tick(0.0, "Чтение заголовков Datamine")
        resolved = resolve_pair(Path(path), None if points is None else Path(points), encoding)
        self.path, self.header, self.points_path, self.points_header, found, by_name = resolved
        self.warnings = [NOTE_WIREFRAMES, *found]
        for w in (*self.header.warnings, *self.points_header.warnings):
            if w not in self.warnings:
                self.warnings.append(w)
        self.split = None if split_by is None else _named(self.header, split_by, split_fields(self.header), "делить")
        for name in _TRIANGLE_FIELDS:
            _number_field(self.header, name, self.path)
        if self.header.records == 0:
            raise FormatError("no_meshes", f"В файле «{self.path.name}» нет треугольников.", HINT_PAIR)
        stem = self.path.stem
        cut = PAIR_NAME.match(stem) if by_name else None
        self.base = clean_name((cut.group(1).rstrip(" _-") if cut else "") or stem)
        self._max = max(int(max_meshes), 1)
        self._order: np.ndarray | None = None  # the records sorted by group (None — one group)
        self._bounds = np.array([0, self.header.records], dtype=np.int64)
        self._xyz: np.ndarray | None = None
        self._tri: np.ndarray | None = None
        self._keys: list[int | str] = [0]
        self._at: dict[int | str, int] = {}
        self._values: list[Any] = [None]
        self.entries: list[MeshEntry] = []
        self._scan(tick)

    def __enter__(self) -> DmWireframes:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._xyz = self._tri = self._order = None

    # --- the composition ---------------------------------------------------------------------------------

    def _scan(self, tick: Progress) -> None:
        """One pass over the attribute fields: the group of every record and the attributes of the groups."""
        h, split = self.header, self.split
        rows = h.records
        fields = [f for f in h.fields if f.name.upper() not in _NOT_ATTRIBUTES]
        constants = _Constants(fields)
        stored = [(j, f) for j, f in enumerate(fields) if f.stored]
        split_at = next((j for j, f in stored if f is split), None)
        codes = np.zeros(rows, dtype=np.int32) if split_at is not None else None
        group_of: dict[int, int] = {}
        names = [f.name for _, f in stored]
        with closing(iter_chunks(self.path, h, fields=names, max_rows=rows if names else 0)) as chunks:
            for chunk in chunks:
                tick(chunk.start / rows, f"Состав каркаса: {n_text(chunk.start)} из {n_text(rows)} треугольников")
                group = np.zeros(chunk.rows, dtype=np.int64)
                tokens = {j: constants.tokens(j, chunk.columns[f.name], keep=j == split_at) for j, f in stored}
                if codes is not None and split is not None and split_at is not None:
                    seen, number = _first_seen(tokens[split_at])  # type: ignore[arg-type]
                    for token in seen.tolist():
                        if group_of.setdefault(token, len(group_of)) >= self._max:
                            raise FormatError(
                                "too_many_meshes",
                                f"Поле «{split.name}» делит файл больше чем на {n_text(self._max)} каркасов.",
                                "Выберите другое поле деления или загрузите файл одним каркасом.",
                            )
                    group = np.array([group_of[token] for token in seen.tolist()], dtype=np.int64)[number]
                    codes[chunk.start : chunk.start + chunk.rows] = group
                    constants.room(len(group_of))
                groups, at = np.unique(group, return_index=True)
                for j, _f in stored:
                    constants.add(j, tokens[j], group, groups, at)
                del chunk, tokens
        if codes is not None and split_at is not None:
            sizes = np.bincount(codes, minlength=len(group_of))
            self._order = np.argsort(codes, kind="stable").astype(np.int32 if rows < 2**31 else np.int64)
            self._bounds = np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)
            decoded = [constants.decode(split_at, token) for token in group_of]  # in the order of the first triangle
            self._values = [None if isinstance(v, float) and v != v else v for v in decoded]
        elif split is not None:  # an implicit field: one value, one wireframe
            self._values = [split.default]
        if split is not None:
            self._keys = [self._key_of(v) for v in self._values]
        for k, key in enumerate(self._keys):
            attrs, changing = [], []
            for j, f in enumerate(fields):
                one, value = constants.value(j, k)
                if one:
                    attrs.append((f.name, value))
                else:
                    changing.append(f.name)
            cleaned, warnings = clean_attrs(attrs)
            if changing:
                listed = ", ".join(changing[:12]) + (" …" if len(changing) > 12 else "")
                warnings.append(f"Поля треугольников, которые меняются внутри каркаса, не загружены: {listed}")
            whole = split is None
            self.entries.append(
                MeshEntry(
                    key=key,
                    name=self._name_of(k),
                    points=self.points_header.records if whole else None,
                    triangles=int(self._bounds[k + 1] - self._bounds[k]),
                    attrs=cleaned,
                    warnings=warnings,
                )
            )
        tick(1.0, f"Каркасов в файле: {n_text(len(self.entries))}")

    @staticmethod
    def _key_of(value: Any) -> int | str:
        if value is None or (isinstance(value, float) and value != value):
            return ""
        if isinstance(value, float):
            return int(value) if value.is_integer() and abs(value) < 2**53 else repr(value)
        return str(value)

    def _name_of(self, k: int) -> str:
        if self.split is None:
            return self.base
        value = self._values[k]
        if value is None or (isinstance(value, float) and value != value):
            return clean_name(f"{self.split.name} — пусто")
        if isinstance(value, float):
            return clean_name(f"{self.split.name} {_number_text(value)}")
        return clean_name(value)

    # --- the geometry ------------------------------------------------------------------------------------

    def load(self, progress: Progress | None = None) -> None:
        """Read the points and the triangles of the file (once; `read` does it by itself)."""
        if self._tri is not None:
            return
        tick = progress or no_progress
        h, ph = self.header, self.points_header
        got = _columns(self.points_path, ph, _POINT_FIELDS, scaled(tick, 0.0, 0.4), "Чтение точек")
        pid, x, y, z = (got[ph.field(name).name] for name in _POINT_FIELDS)
        xyz = np.column_stack([x, y, z])
        del got, x, y, z
        n = len(pid)
        dense = n > 0 and bool(np.array_equal(pid, pid[0] + np.arange(n, dtype=np.float64)))
        perm = ordered = None
        if not dense and n:
            perm = np.argsort(pid, kind="stable")  # an absent number goes last and matches nothing
            ordered = pid[perm]
            same = ordered[1:] == ordered[:-1]
            if same.any():
                a, b = perm[1:][same], perm[:-1][same]
                differ = int(np.count_nonzero((xyz[a] != xyz[b]).any(axis=1)))
                if differ:
                    self.warnings.append(
                        f"Номера точек (PID) повторяются с разными координатами: {n_text(differ)} — "
                        "взята первая точка с таким номером"
                    )
        tri = np.empty((h.records, 3), dtype=np.int32)
        names = [h.field(name).name for name in _TRIANGLE_FIELDS]
        corners = _columns(self.path, h, names, scaled(tick, 0.4, 1.0), "Чтение треугольников")
        for c, name in enumerate(names):
            q = corners.pop(name)
            if dense:
                rel = q - pid[0]
                ok = (rel >= 0) & (rel < n) & (rel == np.floor(rel))
                tri[:, c] = np.where(ok, rel, -1)
            elif perm is not None and ordered is not None:
                at = np.minimum(np.searchsorted(ordered, q), n - 1)
                tri[:, c] = np.where(ordered[at] == q, perm[at], -1)
            else:
                tri[:, c] = -1
            del q
        used = np.zeros(n + 1, dtype=bool)
        used[tri.reshape(-1)] = True  # −1 marks the spare last element
        spare = n - int(np.count_nonzero(used[:n]))
        if spare:
            self.warnings.append(f"Точки, которые не входят в треугольники: {n_text(spare)} — не загружены")
        self._xyz, self._tri = xyz, tri
        tick(1.0, f"Прочитано треугольников: {n_text(len(tri))}")

    def _number(self, entry: MeshEntry | int | str) -> int:
        key = entry.key if isinstance(entry, MeshEntry) else entry
        if not self._at:
            self._at = {own: k for k, own in enumerate(self._keys)}
        return self._at[key]  # KeyError — no such wireframe

    def size_of(self, entry: MeshEntry | int | str) -> int:
        k = self._number(entry)
        return int(self._bounds[k + 1] - self._bounds[k])

    def read(self, entry: MeshEntry | int | str, progress: Progress | None = None) -> MeshSet:
        """A wireframe of the file. FormatError — this one cannot be read; the others still can."""
        tick = progress or no_progress
        k = self._number(entry)
        self.load(scaled(tick, 0.0, 0.6) if self._tri is None else None)
        assert self._tri is not None and self._xyz is not None
        name = self.entries[k].name
        tri = self._tri if self._order is None else self._tri[self._order[self._bounds[k] : self._bounds[k + 1]]]
        missing = int(np.count_nonzero((tri < 0).any(axis=1)))
        if missing:
            raise FormatError(
                "bad_mesh",
                f"Каркас «{name}»: треугольников, которые ссылаются на точки, которых нет в файле точек "
                f"«{self.points_path.name}»: {n_text(missing)} из {n_text(len(tri))}.",
                "Проверьте, что файл точек относится к этому каркасу (пара …tr.dm и …pt.dm) и скопирован полностью.",
            )
        tick(0.7, f"Каркас «{name}»: вершины")
        used, inverse = np.unique(tri, return_inverse=True)
        xyz, local, merged = weld_exact(
            self._xyz[used], np.ascontiguousarray(inverse.reshape(-1, 3), dtype=np.int32), scaled(tick, 0.7, 1.0)
        )
        warnings = list(self.entries[k].warnings)
        if merged:
            warnings.append(f"Совпадающие вершины с разными номерами объединены: {n_text(merged)}")
        more: dict[str, Any] = {"file": self.path.name, "points_file": self.points_path.name}
        if self.header.word == 4 or self.points_header.word == 4:
            more["precision"] = "float32"
        if self.split is not None:
            more["split"] = {"field": self.split.name, "value": self._values[k]}
        mesh = MeshSet(
            name, xyz, local, attrs=self.entries[k].attrs, source=source_of(FORMAT, None, name, **more),
            warnings=warnings,
        )  # fmt: skip
        tick(1.0, f"Каркас «{name}»: {n_text(mesh.triangle_count)} треугольников")
        return mesh


def open_dm_wireframes(path: str | Path, points: str | Path | None = None, **options: Any) -> DmWireframes:
    """`options` — `split_by`, `encoding`, `progress`, `max_meshes` of `DmWireframes`."""
    return DmWireframes(path, points, **options)


def inspect_dm_wireframes(
    path: str | Path, points: str | Path | None = None, **options: Any
) -> tuple[list[MeshEntry], list[str]]:
    """The composition of a Datamine wireframe file without its geometry: (entries, warnings of the file)."""
    with DmWireframes(path, points, **options) as f:
        return f.entries, f.warnings


def read_dm_wireframes(
    path: str | Path,
    points: str | Path | None = None,
    *,
    split_by: str | None = None,
    keys: Iterable[int | str] | None = None,
    encoding: str | None = None,
    progress: Progress | None = None,
    max_meshes: int = MAX_MESHES,
) -> Iterator[MeshSet]:
    """The wireframes of a pair of Datamine tables one at a time (`keys` — only these, in the order of the
    file). A wireframe that cannot be read raises its FormatError; to skip it and go on use `open_dm_wireframes`.
    The warnings of the file come with the first wireframe."""
    tick = progress or no_progress
    with DmWireframes(
        path, points, split_by=split_by, encoding=encoding, progress=scaled(tick, 0.0, 0.1), max_meshes=max_meshes
    ) as f:
        wanted = None if keys is None else set(keys)
        todo = [e for e in f.entries if wanted is None or e.key in wanted]
        if todo:
            f.load(scaled(tick, 0.1, 0.6))
        total = max(sum(f.size_of(e) for e in todo), 1)
        done = 0
        for entry in todo:
            size = f.size_of(entry)
            mesh = f.read(entry, scaled(tick, 0.6 + 0.4 * done / total, 0.6 + 0.4 * (done + size) / total))
            if entry is todo[0]:
                mesh.warnings[:0] = f.warnings
            done += size
            yield mesh
        tick(1.0, f"Прочитано каркасов: {n_text(len(todo))}")


# ---------------------------------------------------------------- strings


def read_dm_strings(
    path: str | Path,
    progress: Progress | None = None,
    *,
    layer_field: str | None = None,
    encoding: str | None = None,
) -> StrRead:
    """Read a Datamine strings table (see the module docstring). `layer_field` — a field whose values are the
    layers (one of `layer_fields`); None — one layer named after the file."""
    tick = progress or no_progress
    path = Path(path)
    tick(0.0, "Чтение заголовка Datamine")
    header = read_header(path, encoding)
    if header.kind != STRINGS:
        raise wrong_kind(path, header, "strings")
    layer = None if layer_field is None else _named(header, layer_field, layer_fields(header), "разделить стринги")
    geometry = [_number_field(header, name, path).name for name in _STRING_FIELDS]
    names = geometry + [f.name for f in header.fields if (f.stored or f is layer) and f.name not in geometry]
    table = read_dm(path, scaled(tick, 0.0, 0.6), encoding=encoding, fields=names)
    rows = table.rows
    column = {c.name: c for c in table.columns}
    warnings = [NOTE_STRINGS, *table.warnings]
    xyz = np.column_stack([column[name].values for name in geometry[:3]])
    endless = np.isinf(xyz[:, 2])
    if endless.any():
        xyz[endless, 2] = np.nan
        warnings.append(f"Записей с бесконечной отметкой Z: {n_text(int(endless.sum()))} — отметка считается пустой")
    nowhere = int(np.count_nonzero(~np.isfinite(xyz[:, :2]).all(axis=1)))
    if nowhere:
        warnings.append(f"Записей без X или Y: {n_text(nowhere)} — пропущены")
    if rows == nowhere:
        raise FormatError(
            "no_lines",
            f"В файле «{path.name}» нет стрингов: ни одной записи с координатами.",
            "Проверьте исходный файл.",
        )
    ptn, pvalue = column[geometry[3]].values, column[geometry[4]].values
    starts = np.ones(rows, dtype=bool)
    with np.errstate(invalid="ignore"):
        a, b = pvalue[1:], pvalue[:-1]
        starts[1:] = ((a != b) & ~(np.isnan(a) & np.isnan(b))) | (ptn[1:] <= ptn[:-1])
    code = np.zeros(rows, dtype=np.int64)
    layer_names = [clean_name(path.stem, "Стринги")]
    if layer is not None:
        values = column[layer.name]
        if layer.kind == "N":
            seen, code = _first_seen(_number_tokens(values.values))
            numbers = [_number_of(token) for token in seen.tolist()]
            layer_names = [NO_CODE if v != v else f"{layer.name} {_number_text(v)}" for v in numbers]
        else:
            code, layer_names = text_codes(values.values)
            if (code < 0).any():
                if NO_CODE not in layer_names:
                    layer_names = [*layer_names, NO_CODE]
                code[code < 0] = layer_names.index(NO_CODE)
        starts[1:] |= code[1:] != code[:-1]  # a line belongs to one layer
    skip = {*geometry[:4], *([layer.name] if layer is not None else [])}
    columns = [c for c in table.columns if c.name not in skip]
    tick(0.6, "Сборка линий")
    layers = build_layers(
        xyz, starts, code, layer_names, columns, line_only=(geometry[4],), progress=scaled(tick, 0.6, 1.0)
    )
    lines = sum(layer_.line_count for layer_ in layers)
    tick(1.0, f"Прочитано линий: {n_text(lines)}")
    return StrRead(STRINGS_FORMAT, layers, header, header.encoding, rows, warnings)


__all__ = [
    "FORMAT",
    "PRELIMINARY",
    "STRINGS_FORMAT",
    "DmWireframes",
    "find_pair",
    "inspect_dm_wireframes",
    "layer_fields",
    "open_dm_wireframes",
    "read_dm_strings",
    "read_dm_wireframes",
    "split_fields",
]
