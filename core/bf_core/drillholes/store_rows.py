"""Rows of an interval table as the values the database driver takes (ADR 0003 §4), a portion at a time and
without row-by-row work: the vectors of `dh_rows.num` (float64 little-endian, cut after the last value of the
row), the flags of `qual`, the text maps `{slot: text}` of `txt` as JSON, NULL for a missing depth. What
`store.write_table` and `composites.sink` store is what the row-by-row writer of Э4 stored, bit for bit
(`tests/test_drillholes_store.py`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any


def hole_codes(holes: Any, index: tuple[Sequence[str], Any] | None = None) -> tuple[Any, list[str]]:
    """(the index of the name of every row, the names): millions of rows name some thousand holes. `index` —
    (names, the index of every row), when the caller has it: nothing is looked up then."""
    import numpy as np
    import pandas as pd

    if index is not None and len(index[1]) == len(holes):
        return np.asarray(index[1]), list(index[0])
    codes, names = pd.factorize(np.asarray(holes, dtype=object), use_na_sentinel=False)
    return codes, list(names)


def encode_vectors(matrix: Any) -> list[bytes | None]:
    """Rows of a float64 matrix → the vectors of `dh_rows.num`: little-endian, cut after the last value of the
    row (None — no value at all). One `tobytes` of the matrix cut into rows; only the rows whose last column
    is empty are looked at again."""
    import numpy as np

    n, w = matrix.shape
    if not w or not n:
        return [None] * n
    step = 8 * w
    blob = np.ascontiguousarray(matrix, dtype="<f8").tobytes()
    out: list[bytes | None] = [blob[k : k + step] for k in range(0, len(blob), step)]
    short = np.flatnonzero(np.isnan(matrix[:, -1]))
    if len(short):
        present = ~np.isnan(matrix[short])
        widths = np.where(present.any(axis=1), w - np.argmax(present[:, ::-1], axis=1), 0)
        for r, width in zip(short.tolist(), widths.tolist(), strict=True):
            out[r] = blob[r * step : r * step + 8 * width] if width else None
    return out


def matrix(columns: Sequence[Any], start: int, stop: int) -> Any:
    """Rows [start, stop) of the number columns as one C-ordered float64 matrix."""
    import numpy as np

    out = np.empty((stop - start, len(columns)), dtype="<f8")
    for k, col in enumerate(columns):
        out[:, k] = col[start:stop]
    return out


def driver_floats(a: Any) -> list[float | None]:
    """float64 → driver values: NaN → None (SQL NULL)."""
    import numpy as np

    out: list[float | None] = a.tolist()
    for i in np.flatnonzero(np.isnan(a)).tolist():
        out[i] = None
    return out


def driver_quals(quals: Sequence[Any], start: int, stop: int) -> list[bytes | None]:
    """`dh_rows.qual` of the rows [start, stop): one byte per number slot, None — all plain."""
    import numpy as np

    k = len(quals)
    q = np.empty((stop - start, k), dtype=np.uint8)
    for j, col in enumerate(quals):
        q[:, j] = col[start:stop]
    marked = np.flatnonzero(q.any(axis=1)) if k else np.zeros(0, dtype=np.int64)
    out: list[bytes | None] = [None] * (stop - start)
    if len(marked):
        blob = q[marked].tobytes()
        for r, at in zip(marked.tolist(), range(0, len(blob), k), strict=True):
            out[r] = blob[at : at + k]
    return out


class Texts:
    """`dh_rows.txt` of a table — `{slot: text}` as JSON, None when every column is empty (None, NaN, "").

    The rows are grouped by the values of all the text columns once, and the JSON of a group is written once —
    a table of millions of rows has some dozen rocks and flags; a portion then takes the texts of its rows.
    With more groups than GROUPS (a column of sample numbers) the JSON is written portion by portion, for the
    groups met there. Values that are not text (numbers, flags of an attribute column: equal ones of different
    types — 1, 1.0, True — would fall into one group) are written row by row, as given."""

    GROUPS = 65_536

    def __init__(self, texts: Sequence[tuple[str, Any]], beat: Callable[[], None] | None = None) -> None:
        """`beat` — called after every column (a sign of life of the job: a column of millions of rows)."""
        import numpy as np
        import pandas as pd

        self.texts = list(texts)
        self.by_row = False
        n = len(self.texts[0][1]) if self.texts else 0
        group = np.zeros(n, dtype=np.int64)
        span = 1  # group < span
        self.parts: list[tuple[str, Any, list[str]]] = []
        for slot, col in self.texts:
            try:
                codes, uniques = pd.factorize(col, use_na_sentinel=True)
            except TypeError:  # values that cannot be grouped (lists)
                self.by_row = True
                return
            labels = uniques.tolist()
            if any(type(v) is not str for v in labels):
                self.by_row = True
                return
            if "" in labels:
                codes = np.where(codes == labels.index(""), -1, codes)
            if span > 2**40:  # many columns of many values: renumber the groups met so far
                group, met = pd.factorize(group)
                span = len(met)
            group = group * (len(labels) + 1) + (codes + 1)
            span *= len(labels) + 1
            self.parts.append((slot, codes, labels))
            if beat is not None:
                beat()
        self.of_row, met = pd.factorize(group)
        self.first = np.empty(len(met), dtype=np.int64)  # a row of every group
        self.first[self.of_row] = np.arange(n, dtype=np.int64)
        self.made = self._json(self.first) if len(met) <= self.GROUPS else None

    def _json(self, rows: Any) -> Any:
        """The JSON of the groups whose rows are given (an object array)."""
        import json

        import numpy as np

        made = np.empty(len(rows), dtype=object)
        for g, r in enumerate(rows.tolist()):
            txt = {slot: labels[codes[r]] for slot, codes, labels in self.parts if codes[r] >= 0}
            made[g] = json.dumps(txt, ensure_ascii=False) if txt else None
        return made

    def portion(self, start: int, stop: int) -> list[str | None]:
        import numpy as np

        if self.by_row:
            return _texts_by_row(self.texts, start, stop)
        groups = self.of_row[start:stop]
        out: list[str | None]
        if self.made is not None:
            out = self.made[groups].tolist()
        else:
            met, of_row = np.unique(groups, return_inverse=True)
            out = self._json(self.first[met])[of_row].tolist()
        return out


def _texts_by_row(texts: Sequence[tuple[str, Any]], start: int, stop: int) -> list[str | None]:
    import json

    cols = [(slot, col[start:stop].tolist()) for slot, col in texts]
    out: list[str | None] = []
    for r in range(stop - start):
        txt = {slot: vals[r] for slot, vals in cols if not (vals[r] is None or vals[r] == "" or vals[r] != vals[r])}
        out.append(json.dumps(txt, ensure_ascii=False) if txt else None)
    return out
