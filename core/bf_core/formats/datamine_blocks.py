"""Datamine block models (`.dm`, `.fbm`) as a source of blocks (ТЗ 7.31, 7.35 Д-13, 8.7.2; ADR 0023 decisions 5–6).

`open_blocks(path, encoding=None)` → `DatamineSource`, a `blocks_text.BlockSource` that `importing/object_blockfile.py`
reads like a DAT or a CSV: `names`, `kinds`, `rows` (exact), `hint`, `notes`, `chunks(want, rows, tick, limit)` →
`BlockChunk`s. The tables are read by `formats/datamine.py` (`read_header`, `iter_chunks`: the pages are mapped, a
portion is the columns of a few megabytes); nothing here parses a byte of the format, it only says which fields are the
blocks and which are the grid.

What the dictionary of the file becomes (the owner's analysis, `tests/data/real/blockmodels-dm-fbm-dat.md`):
* the centre `XC YC ZC` and the size `XINC YINC ZINC` of a block — columns of `names` when they are stored in the
  records (a sub-block model keeps its own size in every record); a size that is an implicit field (one value for the
  whole file) goes to `hint.size`;
* the grid — `XMORIG YMORIG ZMORIG` (the CORNER of the model; `X0 Y0 Z0` repeat them in a `.dm`), `NX NY NZ`, the turn
  `ANGLEn` about `ROTAXISn` — goes to `hint` (`blocks_roles.datamine_hint`), stored or implicit alike: a grid constant
  that the file stores in every record is taken from the first record and is checked on the way (a note says if it
  changes). The turn is provisional (№ 64): the check of three centres of `grid_infer` catches a wrong sign. `.fbm`
  carries the same fields; its `NX NY NZ` may be bigger than a `.dm`'s of the same model (445 × 560 × 104 against
  443 × 558 × 102) — the blocks are the same, the cells (i, j, k) are counted from the corner;
* `IJK` is never read: the importer recomputes the cell of a block from its centre (ADR 0009). It is not a data field;
* any other implicit field (a constant of the file, say `DENSITY` = 2,6) is a column of the same value in every row;
* a number field is float64, the absent data of Datamine (−1e30) is NaN, never 0; a trace («TR», 1e−30) is kept and
  counted in the notes. A value such as −99 is data: «пусто» is the user's choice in the wizard (`EMPTY_CODES` offered
  when it occurs), exactly as for a DAT. A text field is codes into the labels of a portion (blank — empty).

Sub-blocks: Datamine keeps a sub-cell in a record of its own size, the parent's own size is not named in the file —
the importer takes the biggest block for the parent (a warning of `grid_infer` says so) unless the wizard gives it.
Provisional, as the turn: there is no sample with sub-blocks, the tests build them (`tests/formats/dm_builder.py`).

Error codes: those of `datamine.read_header` and `not_block_model`, `empty_file`.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

import numpy as np

from bf_core.blocks.grid_infer import GridHint
from bf_core.formats import datamine as dm
from bf_core.formats.blocks_roles import classify, datamine_hint
from bf_core.formats.blocks_text import CHUNK_ROWS, NUMBER, TEXT, BlockChunk, BlockSource, TextColumn, Tick, _text_of
from bf_core.formats.errors import FormatError

__all__ = ["DatamineSource", "is_block_model", "open_blocks"]

SUFFIXES = (".dm", ".fbm")
#: Fields that describe the grid, not the blocks (compared in upper case).
GRID_FIELDS = frozenset({"XMORIG", "YMORIG", "ZMORIG", "X0", "Y0", "Z0", "NX", "NY", "NZ", "IJK",
                         "ANGLE1", "ANGLE2", "ANGLE3", "ROTAXIS1", "ROTAXIS2", "ROTAXIS3"})  # fmt: skip
SIZE_FIELDS = frozenset({"XINC", "YINC", "ZINC"})  # the grid when implicit, the size of a block when stored


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def _kind_error(path: Path, header: dm.DmHeader) -> FormatError:
    what = {
        dm.TRIANGLES: "таблица треугольников каркаса",
        dm.POINTS: "таблица точек каркаса",
        dm.STRINGS: "стринги",
    }.get(header.kind, "таблица без полей блока (XC, YC, ZC)")
    return FormatError(
        "not_block_model",
        f"Файл «{path.name}» — {what} Datamine, а не блочная модель.",
        "Блочная модель Datamine — таблица с центром блока XC, YC, ZC и размером XINC, YINC, ZINC; "
        "каркасы и стринги загружаются как каркасы и линии.",
    )


def is_block_model(path: str | Path, encoding: str | None = None) -> bool:
    """A `.dm` / `.fbm` whose dictionary says it is a block model (a header is read, no record). A `.fbm` is one when
    it has the centre of a block; False — not a Datamine table at all or a table of something else."""
    path = Path(path)
    if path.suffix.lower() not in SUFFIXES:
        return False
    try:
        header = dm.read_header(path, encoding)
    except (FormatError, OSError):
        return False
    return header.kind == dm.BLOCK_MODEL or (header.kind == dm.TABLE and classify(header.names).has_centre)


class DatamineSource(BlockSource):
    """A `.dm` or `.fbm` block model (see the module docstring)."""

    def __init__(self, path: str | Path, encoding: str | None = None) -> None:
        super().__init__()
        self.path = Path(path)
        self.format = "fbm" if self.path.suffix.lower() == ".fbm" else "dm"
        self.label = "Datamine FBM" if self.format == "fbm" else "Datamine DM"
        h = self.header = dm.read_header(self.path, encoding)
        self.encoding = h.encoding
        if h.kind in (dm.TRIANGLES, dm.POINTS, dm.STRINGS) or not classify(h.names).has_centre:
            raise _kind_error(self.path, h)
        if h.records == 0:
            raise FormatError("empty_file", "В таблице Datamine нет ни одного блока.",
                              "Проверьте, что модель сохранена полностью, и повторите.")  # fmt: skip
        self.rows = h.records
        self.notes = list(h.warnings)
        implicit = {f.name: f for f in h.fields if not f.stored}
        self._stored = {f.name for f in h.stored}
        # stored fields that are grid constants: IJK (dropped), the others (read once from the first record)
        self._grid_stored = [f.name for f in h.stored if f.name.upper() in GRID_FIELDS and f.name.upper() != "IJK"]
        self._ignored = {f.name for f in h.fields if f.name.upper() in GRID_FIELDS}
        self._ignored |= {n for n in implicit if n.upper() in SIZE_FIELDS}
        constants = dict(h.constants)
        self._first: dict[str, float] = {}
        if self._grid_stored:
            first = next(dm.iter_chunks(self.path, h, fields=self._grid_stored, max_rows=1))
            for name in self._grid_stored:
                value = float(first.columns[name][0])
                self._first[name] = value
                constants[name] = None if np.isnan(value) else value
        self.hint: GridHint | None = datamine_hint(constants)
        self.names, self.kinds = [], {}
        self._implicit: dict[str, float | str | None] = {}
        for f in h.fields:
            if f.name in self._ignored:
                continue
            self.names.append(f.name)
            self.kinds[f.name] = NUMBER if f.kind == "N" else TEXT
            if not f.stored:
                self._implicit[f.name] = f.default
        if self.hint is not None:
            self.notes.extend(self.hint.notes)

    # ------------------------------------------------------------------------------------------------ the portions

    def chunks(
        self, want: Sequence[str] | None = None, rows: int = CHUNK_ROWS, tick: Tick | None = None,
        limit: int | None = None,
    ) -> Iterator[BlockChunk]:  # fmt: skip
        h = self.header
        total = h.records if limit is None else min(h.records, max(0, limit))
        if not total:
            return
        wanted = set(self.names if want is None else want)
        read = [n for n in self.names if n in wanted and n in self._stored]
        constants = [n for n in self.names if n in wanted and n in self._implicit]
        watch = [n for n in self._grid_stored if n not in read]
        traces: dict[str, int] = {}
        damaged: dict[str, int] = {}
        moved: dict[str, int] = {}
        source = dm.iter_chunks(self.path, h, fields=[*read, *watch], chunk_rows=max(1, rows), max_rows=total)
        try:
            for part in source:
                if tick is not None:
                    tick(part.start / total, f"Чтение записей: {_n(part.start)} из {_n(total)}")
                chunk = BlockChunk(part.start, part.rows)
                for name in read:
                    column = part.columns[name]
                    if self.kinds[name] == NUMBER:
                        chunk.numbers[name] = column
                    else:
                        chunk.texts[name] = _text_of(column)
                for name in constants:
                    value = self._implicit[name]
                    if self.kinds[name] == NUMBER:
                        chunk.numbers[name] = np.full(part.rows, np.nan if value is None else float(value))
                    else:
                        chunk.texts[name] = (
                            TextColumn(np.full(part.rows, -1, dtype=np.int32), [])
                            if value in (None, "")
                            else TextColumn(np.zeros(part.rows, dtype=np.int32), [str(value)])
                        )
                for name in self._grid_stored:  # the constants of the grid must be the same in every record
                    column = part.columns[name]
                    same = (column == self._first[name]) | (np.isnan(column) & np.isnan(self._first[name]))
                    if not bool(same.all()):
                        moved[name] = moved.get(name, 0) + int((~same).sum())
                for name, count in part.traces.items():
                    traces[name] = traces.get(name, 0) + count
                for name, count in part.damaged.items():
                    damaged[name] = damaged.get(name, 0) + count
                yield chunk
        finally:
            source.close()
        for name, count in traces.items():
            self.notes.append(f"Поле «{name}»: {_n(count)} знач. «TR» (следы, 1e-30) оставлены как есть.")
        for name, count in damaged.items():
            self.notes.append(f"Поле «{name}»: {_n(count)} знач. содержат символы, которые не читаются в "
                              f"кодировке {h.encoding}.")  # fmt: skip
        for name, count in moved.items():
            self.notes.append(f"Поле «{name}» (константа сетки) меняется в {_n(count)} записях: взято значение "
                              "первой записи.")  # fmt: skip
        if tick is not None:
            tick(1.0, f"Прочитано записей: {_n(total)}")


def open_blocks(path: str | Path, encoding: str | None = None) -> DatamineSource:
    """The source of the blocks of a Datamine `.dm` / `.fbm` (`FormatError` — not a readable block model)."""
    return DatamineSource(path, encoding)
