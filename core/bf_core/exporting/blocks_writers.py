"""The writers of a block model (подшаг 17.16; ТЗ 8.7.7, 7.35 Д-17; ADR 0023 §12, decision 20): Micromine DAT, CSV and
Parquet in portions, and the wrappers of the registry (`exporting/registry.py`) for the type `block_model`.

    sink = DatSink(path, columns, encoding, title=…, note=…)   # or CsvSink(…), ParquetSink(…)
    for batch in …: sink.write(batch)                           # `Batch`: geometry, fields, (P, s, w)
    sink.commit()                                               # «.part» → the file; `sink.abort()` leaves nothing

The rows of a block model never sit in memory together: the core (`blocks/export.py`) gives a portion of a few hundred
thousand blocks, a sink writes it at once. The file is made as «‹имя›.part» and renamed when complete: a cancel or a
failure leaves nothing. The same model gives the same bytes (no clock, no random, a fixed order of the rows).

* DAT — the Extended header of Micromine (`formats/micromine_header.py`), the coordinates and the numbers as `R` (an
  IEEE double: a value comes back bit for bit, an empty one is NaN), a category as `C` as wide as its longest label;
  the code page `cp1251` (Windows-1251, the default), `bocu1` or `utf-8`; names of at most 10 characters, unique.
* CSV — a delimiter (`;` `,` tab `|`), a decimal comma or point, an encoding (`utf-8-sig` by default); a number is the
  shortest text that gives the same double (an empty one — nothing, or the code the user chose), a label is quoted when
  it needs it. CSV Micromine adds the lines of the types and of the grid (`x0, dx, y0, dy, z0, dz, CENTRE`).
* Parquet — `idx`, `sx…wz` (sub-blocks), X, Y, Z, DX, DY, DZ, the fields; an empty number is null; the file carries
  the metadata `bf2.block_model` = `{grid, fields}` for the reader.

Writers of the registry: `write_dat`, `write_csv`, `write_parquet` (`Writer.write(item, path, env, tick)`) —
`exporting/writers.py` registers them for «Блочные модели».
"""

from __future__ import annotations

import codecs
import json
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bf_core.api.errors import ApiError

if TYPE_CHECKING:
    import numpy as np

GEO = ("X", "Y", "Z", "DX", "DY", "DZ")
NUMBER, CATEGORY = "number", "category"
CSV_ENCODINGS = ("utf-8-sig", "utf-8", "cp1251", "cp866", "koi8-r", "utf-16")
DAT_ENCODINGS = ("cp1251", "bocu1", "utf-8")
DELIMITERS = (";", ",", "\t", "|")
EOL = "\r\n"
_CONTROL = re.compile(r"[\x00-\x1f]")
MB = 2**20


def encoding_error(what: str, text: str, encoding: str) -> ApiError:
    shown = text if len(text) <= 40 else text[:40] + "…"
    return ApiError(422, "export_encoding", f"{what}: в «{shown}» есть символы, которых нет в кодировке {encoding}.",
                    "Выберите кодировку UTF-8 или переименуйте поле или метку.", {"param": "encoding"})  # fmt: skip


@dataclass(frozen=True)
class Column:
    """A field of the model as the file has it: the output `name` (unique), `kind`, the `labels` of a category."""

    name: str
    kind: str
    labels: tuple[str, ...] = ()
    unit: str | None = None


@dataclass
class Batch:
    """A portion of rows that passed: `geo` float64 [n, 6] (X Y Z DX DY DZ: the centre in the axes of the project, the
    size along the axes of the model), `cols` — the fields in the order of the columns (float64, NaN — empty;
    a category — int32 codes, −1 — empty), `p` uint64, `s`, `w` uint8 [n, 3] or None (the rows of the model)."""

    n: int
    geo: np.ndarray
    cols: list[np.ndarray]
    p: np.ndarray
    s: np.ndarray | None
    w: np.ndarray | None


class Sink(ABC):
    """A file written in portions through «‹имя›.part»."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.part = self.path.with_name(self.path.name + ".part")
        self.warnings: list[str] = []
        self._fh: Any = open(self.part, "wb")  # noqa: SIM115 - closed by commit / abort

    @abstractmethod
    def write(self, batch: Batch) -> None: ...

    def _finish(self) -> None:  # noqa: B027 - most formats keep nothing back
        """Flushes what the format keeps back (the footer of Parquet)."""

    def commit(self) -> int:
        """The file under its name; returns its size in bytes."""
        try:
            self._finish()
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._fh.close()
            os.replace(self.part, self.path)
        except BaseException:
            self.abort()
            raise
        return self.path.stat().st_size

    def abort(self) -> None:
        """Closes and removes what was written (never raises)."""
        try:
            if not self._fh.closed:
                self._fh.close()
        except OSError:
            pass
        self.part.unlink(missing_ok=True)


# --- Micromine DAT ----------------------------------------------------------------------------------------------------


def dat_codepage(encoding: str) -> str:
    return {"cp1251": "ibm-5347_P100-1998", "bocu1": "BOCU-1", "utf-8": "utf-8"}[encoding]


class DatSink(Sink):
    def __init__(self, path: Path, columns: Sequence[Column], encoding: str = "cp1251", *, title: str = "",
                 note: str = "") -> None:  # fmt: skip
        import numpy as np

        from bf_core.formats.errors import FormatError
        from bf_core.formats.micromine_header import TEXT_LIMIT, Field, build_header, codec_of, default_xml
        from bf_core.formats.micromine_write import field_names

        codepage = dat_codepage(encoding)
        self.codec = codec_of(codepage) or "cp1251"
        self.columns = list(columns)
        try:
            names, notes = field_names([*GEO, *(c.name for c in self.columns)], self.codec)
        except FormatError as exc:
            raise ApiError(422, "export_encoding", exc.message, exc.hint, {"param": "encoding"}) from exc
        self.names, self.warnings = names, notes
        fields = [Field(n, "R", 8, 0) for n in names[: len(GEO)]]
        self.tables: list[np.ndarray | None] = [None] * len(GEO)
        for name, c in zip(names[len(GEO) :], self.columns, strict=True):
            if c.kind == NUMBER:
                fields.append(Field(name, "R", 8, 0))
                self.tables.append(None)
                continue
            cells = [self._cell(label, encoding) for label in c.labels]
            width = max([1, *(len(x) for x in cells)])
            if width > TEXT_LIMIT:
                raise ApiError(422, "export_label_too_long", f"Метка поля «{c.name}» длиннее {TEXT_LIMIT} символов.",
                               "Сократите метки категорий: в DAT поле текста не шире 255.")  # fmt: skip
            fields.append(Field(name, "C", width, 0))
            table = np.full(len(cells) + 1, b" " * width, dtype=f"S{width}")  # the last one is the blank
            for i, cell in enumerate(cells):
                table[i] = cell.ljust(width)
            self.tables.append(table)
        self.fields = fields
        xml = default_xml(names, codepage, title, note)
        try:
            head = build_header(fields, self.codec, xml)
        except UnicodeEncodeError as exc:
            raise encoding_error("Имя поля", str(exc.object), encoding) from exc
        kinds = ["<f8" if f.kind == "R" else f"S{f.width}" for f in fields]
        self.dtype = np.dtype({"names": [f"f{i}" for i in range(len(fields))] + ["eol"], "formats": [*kinds, "S2"]})
        self.rows = 0
        super().__init__(path)
        self._fh.write(head)

    def _cell(self, label: str, encoding: str) -> bytes:
        from bf_core.formats.micromine_header import encode_text

        text = _CONTROL.sub(" ", label).rstrip()
        try:
            return encode_text(text, self.codec)
        except UnicodeEncodeError as exc:
            raise encoding_error("Метка категории", label, encoding) from exc

    def write(self, batch: Batch) -> None:
        import numpy as np

        rec = np.empty(batch.n, dtype=self.dtype)
        rec["eol"] = b"\r\n"
        for a in range(len(GEO)):
            rec[f"f{a}"] = batch.geo[:, a]
        for k, (c, values) in enumerate(zip(self.columns, batch.cols, strict=True)):
            i = len(GEO) + k
            if c.kind == NUMBER:
                rec[f"f{i}"] = values
            else:
                table = self.tables[i]
                assert table is not None
                rec[f"f{i}"] = table[np.where(values < 0, len(table) - 1, values)]
        self._fh.write(rec.tobytes())
        self.rows += batch.n


# --- CSV --------------------------------------------------------------------------------------------------------------


def _quote(label: str, delimiter: str) -> str:
    if label == "":
        return ""
    if delimiter in label or '"' in label or "\n" in label or "\r" in label or label != label.strip():
        return '"' + label.replace('"', '""') + '"'
    return label


class CsvSink(Sink):
    """`header` — "plain" (the names) or "micromine" (the names, the types, the grid line `grid_line` — the numbers
    of x0, dx, y0, dy, z0, dz; None — no grid line)."""

    def __init__(self, path: Path, columns: Sequence[Column], *, delimiter: str = ";", decimal: str = ",",
                 encoding: str = "utf-8-sig", header: str = "plain",
                 grid_line: Sequence[float] | None = None) -> None:  # fmt: skip
        self.columns, self.delimiter, self.decimal = list(columns), delimiter, decimal
        self.encoding = encoding
        self.rows = 0
        self._labels = [pa_strings([_quote(x, delimiter) for x in c.labels]) if c.kind == CATEGORY else None
                        for c in self.columns]  # fmt: skip
        self._enc = None if encoding == "utf-8" else codecs.getincrementalencoder(encoding)()
        names = [*GEO, *(c.name for c in self.columns)]
        lines = [[_quote(n, delimiter) for n in names]]
        if header == "micromine":
            lines.append(["DOUBLE" if i < len(GEO) or self.columns[i - len(GEO)].kind == NUMBER else "CHARACTER"
                          for i in range(len(names))])  # fmt: skip
            if grid_line is not None:
                grid = [self._number(x) for x in grid_line] + ["CENTRE"]
                lines.append((grid + [""] * len(names))[: len(names)])
        head = self._encoded(("".join(delimiter.join(line) + EOL for line in lines)).encode("utf-8"))
        super().__init__(path)
        self._fh.write(head)

    def _number(self, x: float) -> str:
        text = repr(float(x))
        text = text[:-2] if text.endswith(".0") else text
        return text.replace(".", ",") if self.decimal == "," else text

    def _encoded(self, data: bytes) -> bytes:
        """The portion in the encoding of the file (UTF-8 is what the pipeline makes)."""
        if self._enc is None:
            return data
        try:
            return self._enc.encode(data.decode("utf-8"))
        except UnicodeEncodeError as exc:
            near = str(exc.object)[max(0, exc.start - 10) : exc.end + 10]
            raise encoding_error("Запись CSV", near, self.encoding) from exc

    def write(self, batch: Batch) -> None:
        import pyarrow as pa
        import pyarrow.compute as pc

        cols: list[Any] = []
        for a in range(len(GEO)):
            cols.append(self._numbers(batch.geo[:, a]))
        for c, labels, values in zip(self.columns, self._labels, batch.cols, strict=True):
            if c.kind == NUMBER:
                cols.append(self._numbers(values))
            else:
                codes = pa.array(values, mask=values < 0)
                cols.append(pa.DictionaryArray.from_arrays(codes, labels).cast(pa.string()))
        lines = pc.binary_join_element_wise(*cols, self.delimiter, null_handling="replace", null_replacement="")
        one = pa.ListArray.from_arrays(pa.array([0, len(lines)], pa.int32()), lines)  # all the lines as one list
        joined = pc.binary_join(one, EOL)[0]
        self._fh.write(self._encoded(bytes(joined.as_buffer()) + EOL.encode("ascii")))
        self.rows += batch.n

    def _numbers(self, values: np.ndarray) -> Any:
        import numpy as np
        import pyarrow as pa
        import pyarrow.compute as pc

        text = pa.array(values, mask=np.isnan(values)).cast(pa.string())
        return pc.replace_substring(text, ".", ",") if self.decimal == "," else text


def pa_strings(items: Sequence[str]) -> Any:
    import pyarrow as pa

    return pa.array(list(items), type=pa.string())


# --- Parquet ----------------------------------------------------------------------------------------------------------


class ParquetSink(Sink):
    def __init__(self, path: Path, columns: Sequence[Column], *, grid: dict[str, Any], sub: bool,
                 row_group: int = 1 << 20) -> None:  # fmt: skip
        import pyarrow as pa
        import pyarrow.parquet as pq

        super().__init__(path)
        self.columns, self.sub, self.rows = list(columns), sub, 0
        head = [("idx", pa.uint64())]
        if sub:
            head += [(n, pa.uint8()) for n in ("sx", "sy", "sz", "wx", "wy", "wz")]
        body = [(c.name, pa.float64() if c.kind == NUMBER else pa.string()) for c in self.columns]
        schema = pa.schema([*head, *((n, pa.float64()) for n in GEO), *body])
        meta = {"grid": grid, "fields": [{"name": c.name, "kind": c.kind, "unit": c.unit,
                                          "labels": list(c.labels)} for c in self.columns]}  # fmt: skip
        schema = schema.with_metadata({"bf2.block_model": json.dumps(meta, ensure_ascii=False)})
        self._schema = schema
        self._writer = pq.ParquetWriter(self._fh, schema, compression="zstd", compression_level=3)
        self._labels = [pa_strings(c.labels) if c.kind == CATEGORY else None for c in self.columns]
        self._row_group = row_group

    def write(self, batch: Batch) -> None:
        import numpy as np
        import pyarrow as pa

        arrays: list[Any] = [pa.array(batch.p)]
        if self.sub:
            assert batch.s is not None and batch.w is not None
            arrays += [pa.array(np.ascontiguousarray(batch.s[:, a])) for a in range(3)]
            arrays += [pa.array(np.ascontiguousarray(batch.w[:, a])) for a in range(3)]
        arrays += [pa.array(np.ascontiguousarray(batch.geo[:, a])) for a in range(len(GEO))]
        for c, labels, values in zip(self.columns, self._labels, batch.cols, strict=True):
            if c.kind == NUMBER:
                arrays.append(pa.array(values, mask=np.isnan(values)))
            else:
                codes = pa.array(values, mask=values < 0)
                arrays.append(pa.DictionaryArray.from_arrays(codes, labels).cast(pa.string()))
        table = pa.Table.from_arrays(arrays, schema=self._schema)
        self._writer.write_table(table, row_group_size=self._row_group)
        self.rows += batch.n

    def _finish(self) -> None:
        self._writer.close()

    def abort(self) -> None:
        try:
            self._writer.close()
        except Exception:  # noqa: BLE001 - a failed writer may not close
            pass
        super().abort()


# --- the registry (ТЗ 7.35 Д-17) -------------------------------------------------------------------------------------

SUFFIX = {"dat": ".dat", "csv": ".csv", "parquet": ".parquet", "omf": ".omf"}
REGISTRY_OPTIONS: dict[str, dict[str, Any]] = {
    "dat": {"encoding": "cp1251"},
    "csv": {"delimiter": ";", "decimal": ",", "encoding": "utf-8-sig", "header": "plain"},
    "parquet": {},
}


def suffixes(fmt: str) -> Callable[[Any, Any], tuple[str, ...]]:
    return lambda _item, _env: (SUFFIX[fmt],)


def memory(fmt: str) -> Callable[[Any], int]:
    """MB for the scheduler: a portion of rows (`blocks/export.ROWS`) and the buffers of the sink, plus the row group
    of the model files; OMF writes the whole grid densely."""

    def of(item: Any) -> int:
        if fmt == "omf":
            return max(512, 256 + int(24 * int(item.info.get("grid_cells") or 0) / MB))
        return 640

    return of


def write(fmt: str) -> Callable[[Any, Path, Any, Callable[..., None]], Any]:
    """`Writer.write` of the pair «block_model × fmt»: the model with every field and every block, the options of
    `REGISTRY_OPTIONS`; the window «Экспорт проекта» has chosen the name, so a file there is replaced."""

    def run(item: Any, path: Path, env: Any, tick: Callable[..., None]) -> Any:
        from bf_core.blocks import export
        from bf_core.exporting.registry import Written

        body = {"format": fmt, "path": str(path), "overwrite": True, **REGISTRY_OPTIONS.get(fmt, {})}

        def go(progress: float | None, message: str | None) -> None:
            tick(0.0 if progress is None else progress, message)

        done = export.run_export(env.engine, env.store.root.parent, {**body, "model_id": item.id}, tick=go)
        return Written([Path(done["path"])], int(done["bytes"]), list(done["warnings"]))

    return run
