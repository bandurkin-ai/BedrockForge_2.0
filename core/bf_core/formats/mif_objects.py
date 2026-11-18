"""The objects of the Data section of a MapInfo MIF file (ТЗ 7.35 Д-13; ADR 0009, ADR 0011 §2).

`MifObjects.feed(lines, first_line)` takes the section a block of lines at a time (the caller reads the file in
chunks), `finish()` checks that the last object is complete. What is collected:
  * lines — `Line`, `Pline` (sections of `Pline Multiple`), `Region` (polygons, flagged closed, with their number
    within the object), `Rect`: `lines_xy()`, `counts`, `line_obj`, `line_seq`, `line_ring`, `line_closed`;
  * points — `Point`, `Multipoint`: `px`, `py`, `p_obj`, `p_seq`;
  * text — `Text`: the lower left corner of its rectangle, `t_text` (`\\n` is a line break), `t_height` (the height
    of the rectangle per line of text), `t_rotation` (`Angle`, 0 without the clause), `t_obj`, `t_seq`.
`*_obj` is the number of the object from 0 — the row of the `.mid`; the parts of a `Collection` share its row;
`*_seq` is the number of the vertex in the order of the file. `None`, `Arc`, `Ellipse`, `Roundrect` take a row
and are counted (`nones`, `skipped`). Other lines are clauses of the object before them (`Pen`, `Brush`,
`Symbol`, `Smooth`, `Center`, `Font`, …) and are not read. Coordinate lists are parsed in blocks (`numpy.loadtxt`),
a count written in the file is never used to allocate: lines are read until the count is met or the file ends
(`truncated`). A line that is not what the object needs is `broken` with its number.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np

from bf_core.formats.errors import FormatError
from bf_core.formats.str_layers import n_text

LONG_BLOCK = 32  # coordinate lines parsed by numpy at once; shorter blocks are cheaper line by line
FLUSH = 1 << 17  # Python floats kept before they are packed into an array
_HINT = "Сохраните таблицу в MapInfo заново (Таблица → Экспорт → MapInfo Interchange) и загрузите оба файла."


def _fail(code: str, message: str, hint: str = _HINT) -> FormatError:
    return FormatError(code, message, hint)


class MifObjects:
    """The objects of the Data section, fed a block of lines at a time."""

    def __init__(self) -> None:
        self.obj = -1  # the object being read: a row of the .mid
        self.sub_left = 0  # parts of a Collection still to come: they share its row
        self.vertex = 0  # vertices so far: `seq`
        self.xy: list[np.ndarray] = []  # lines: blocks of vertices, then `flat` — the vertices not packed yet
        self.flat: list[float] = []
        self.counts: list[int] = []
        self.line_obj: list[int] = []
        self.line_seq: list[int] = []
        self.line_ring: list[float] = []
        self.line_closed: list[bool] = []
        self.px: list[float] = []  # points
        self.py: list[float] = []
        self.p_obj: list[int] = []
        self.p_seq: list[int] = []
        self.tx: list[float] = []  # text
        self.ty: list[float] = []
        self.t_obj: list[int] = []
        self.t_seq: list[int] = []
        self.t_text: list[str] = []
        self.t_height: list[float] = []
        self.t_rotation: list[float] = []
        self.skipped: dict[str, int] = {}
        self.nones = 0
        self.need = 0  # coordinate lines of the section still to read
        self.got = 0  # vertices of the section read so far
        self.sections = 0  # sections of the object still to finish
        self.ring = 0
        self.kind = ""
        self.expect: Callable[[list[str], str, int], None] | None = None
        self.in_text = False
        self.string = ""
        self.handlers: dict[str, Callable[[list[str], str, int], None]] = {
            "point": self._point,
            "line": self._line,
            "pline": self._pline,
            "region": self._region,
            "text": self._text,
            "rect": self._rect,
            "multipoint": self._multipoint,
            "collection": self._collection,
            "none": self._none,
            "arc": self._skip,
            "ellipse": self._skip,
            "roundrect": self._skip,
        }

    # --- reading ------------------------------------------------------------------------------------

    def feed(self, lines: list[str], first: int) -> None:
        i, n = 0, len(lines)
        handlers = self.handlers
        while i < n:
            if self.need:
                take = min(self.need, n - i)
                self._coords(lines[i : i + take], first + i)
                i += take
                continue
            line = lines[i]
            i += 1
            tokens = line.split()
            if not tokens:
                continue
            if self.expect is not None:
                expect, self.expect = self.expect, None
                expect(tokens, line, first + i - 1)
                continue
            handler = handlers.get(tokens[0].lower())
            if handler is not None:
                handler(tokens, line, first + i - 1)
            elif self.in_text and len(tokens) > 1 and tokens[0].lower() == "angle":
                self.t_rotation[-1] = self._numbers(tokens, 1, 1, first + i - 1, line)[0]

    def finish(self) -> None:
        if self.need or self.expect is not None or self.sections > 0:
            raise _fail(
                "truncated",
                f"Файл .mif обрезан: объект {n_text(self.obj + 1)} не дочитан.",
                "Скопируйте файл заново или сохраните таблицу в MapInfo ещё раз.",
            )

    @staticmethod
    def _bad(no: int, line: str, what: str) -> FormatError:
        return _fail("broken", f"Строка {n_text(no)} файла .mif: {what}, получено «{line.strip()[:40]}».")

    def _numbers(self, tokens: list[str], start: int, count: int, no: int, line: str) -> list[float]:
        try:
            values = [float(t) for t in tokens[start : start + count]]
        except ValueError:
            values = []
        if len(values) != count or not all(math.isfinite(v) for v in values):
            raise self._bad(no, line, f"ожидалось чисел: {count}")
        return values

    def _int(self, token: str | None, no: int, line: str) -> int:
        if token is None or not token.isdigit():
            raise self._bad(no, line, "ожидалось целое число")
        return int(token)

    def _begin(self) -> None:
        self.in_text = False
        if self.sub_left:
            self.sub_left -= 1
        else:
            self.obj += 1

    def _coords(self, block: list[str], no: int) -> None:
        """Coordinate lines of the section being read: a numpy block when long, Python floats when short."""
        rows: np.ndarray | None = None
        flat: list[float] | None = None
        if len(block) >= LONG_BLOCK:
            try:
                rows = np.loadtxt(block, dtype=np.float64, comments=None, ndmin=2)
            except ValueError:
                rows = None
            if rows is not None and (rows.shape != (len(block), 2) or not np.isfinite(rows).all()):
                rows = None
        else:
            flat = []
            try:
                for line in block:
                    x, y = line.split()
                    flat.append(float(x))
                    flat.append(float(y))
            except ValueError:
                flat = None
            if flat is not None and not math.isfinite(sum(flat)):
                flat = None  # NaN or infinity among them (or a huge sum): checked line by line below
        if rows is None and flat is None:  # line by line: blank lines are allowed, an error names its line
            flat = []
            for k, line in enumerate(block):
                tokens = line.split()
                if tokens:
                    flat += self._numbers(tokens, 0, 2, no + k, line)
        count = len(rows) if rows is not None else len(flat or ()) // 2
        if not count:
            return
        if self.kind == "multipoint":
            self.px.extend(rows[:, 0].tolist() if rows is not None else (flat or [])[0::2])
            self.py.extend(rows[:, 1].tolist() if rows is not None else (flat or [])[1::2])
            self.p_obj.extend([self.obj] * count)
            self.p_seq.extend(range(self.vertex, self.vertex + count))
            self.vertex += count
        elif rows is not None:
            self._flush()
            self.xy.append(rows)
            self.got += count
        else:
            self.flat.extend(flat or ())
            self.got += count
        self.need -= count
        if self.need == 0:
            self._section_done()

    def _flush(self) -> None:
        if self.flat:
            self.xy.append(np.asarray(self.flat, dtype=np.float64).reshape(-1, 2))
            self.flat = []

    def lines_xy(self) -> np.ndarray:
        """X, Y of the vertices of all lines, in the order of `counts`."""
        self._flush()
        return np.concatenate(self.xy) if self.xy else np.empty((0, 2))

    def _start(self, count: int) -> None:
        self.got = 0
        if count:
            self.need = count
        else:
            self._section_done()

    def _count(self, tokens: list[str], line: str, no: int) -> None:
        self._start(self._int(tokens[0], no, line))

    def _section_done(self) -> None:
        if self.got:  # vertices of a Pline section or of a polygon of a Region (a Multipoint is stored already)
            region = self.kind == "region"
            self._line_done(self.got, region, float(self.ring) if region else math.nan)
            self.got = 0
        self.sections -= 1
        if self.sections > 0:
            self.ring += 1
            self.expect = self._count

    def _line_done(self, count: int, closed: bool, ring: float) -> None:
        self.counts.append(count)
        self.line_obj.append(self.obj)
        self.line_seq.append(self.vertex)
        self.line_ring.append(ring)
        self.line_closed.append(closed)
        self.vertex += count
        if len(self.flat) >= FLUSH:
            self._flush()

    # --- objects ------------------------------------------------------------------------------------

    def _point(self, tokens: list[str], line: str, no: int) -> None:
        self._begin()
        try:
            x, y = float(tokens[1]), float(tokens[2])
        except (ValueError, IndexError):
            x = y = math.nan
        if not math.isfinite(x + y):
            x, y = self._numbers(tokens, 1, 2, no, line)  # names the line (a huge sum is not an error)
        self.px.append(x)
        self.py.append(y)
        self.p_obj.append(self.obj)
        self.p_seq.append(self.vertex)
        self.vertex += 1

    def _line(self, tokens: list[str], line: str, no: int) -> None:
        self._begin()
        x1, y1, x2, y2 = self._numbers(tokens, 1, 4, no, line)
        self.flat += [x1, y1, x2, y2]
        self._line_done(2, False, math.nan)

    def _rect(self, tokens: list[str], line: str, no: int) -> None:
        self._begin()
        x1, y1, x2, y2 = self._numbers(tokens, 1, 4, no, line)
        self.flat += [x1, y1, x2, y1, x2, y2, x1, y2, x1, y1]
        self._line_done(5, True, math.nan)

    def _pline(self, tokens: list[str], line: str, no: int) -> None:
        self._begin()
        self.kind, self.sections = "pline", 1
        if len(tokens) > 1 and tokens[1].lower() == "multiple":
            self.sections = self._int(tokens[2] if len(tokens) > 2 else None, no, line)
            if self.sections:
                self.expect = self._count
        elif len(tokens) > 1:
            self._start(self._int(tokens[1], no, line))
        else:
            self.expect = self._count

    def _region(self, tokens: list[str], line: str, no: int) -> None:
        self._begin()
        self.kind, self.ring = "region", 1
        self.sections = self._int(tokens[1] if len(tokens) > 1 else None, no, line)
        if self.sections:
            self.expect = self._count

    def _multipoint(self, tokens: list[str], line: str, no: int) -> None:
        self._begin()
        self.kind, self.sections = "multipoint", 1
        self._start(self._int(tokens[1] if len(tokens) > 1 else None, no, line))

    def _collection(self, tokens: list[str], line: str, no: int) -> None:
        self._begin()
        self.sub_left = self._int(tokens[1] if len(tokens) > 1 else None, no, line)

    def _none(self, tokens: list[str], line: str, no: int) -> None:
        self._begin()
        self.nones += 1

    def _skip(self, tokens: list[str], line: str, no: int) -> None:
        self._begin()
        name = tokens[0].capitalize()
        self.skipped[name] = self.skipped.get(name, 0) + 1

    def _text(self, tokens: list[str], line: str, no: int) -> None:
        self._begin()
        rest = line.strip()[4:].strip()
        if rest:
            self._text_string(rest.split(), rest, no)
        else:
            self.expect = self._text_string

    def _text_string(self, tokens: list[str], line: str, no: int) -> None:
        rest = line.strip()
        a, b = rest.find('"'), rest.rfind('"')
        if a != 0 or b <= a:
            raise self._bad(no, line, "ожидался текст в кавычках")
        self.string = rest[1:b]
        tail = rest[b + 1 :].split()
        if tail:
            self._text_box(tail, line, no)
        else:
            self.expect = self._text_box

    def _text_box(self, tokens: list[str], line: str, no: int) -> None:
        x1, y1, x2, y2 = self._numbers(tokens, 0, 4, no, line)
        text = self.string.replace("\\n", "\n")
        self.tx.append(min(x1, x2))
        self.ty.append(min(y1, y2))
        self.t_obj.append(self.obj)
        self.t_seq.append(self.vertex)
        self.t_text.append(text)
        self.t_height.append(abs(y2 - y1) / (text.count("\n") + 1))
        self.t_rotation.append(0.0)
        self.vertex += 1
        self.in_text = True


__all__ = ["MifObjects"]
